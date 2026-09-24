let dryRunActive = false;


async function runDryRun(event) {
    if (dryRunActive) {
        await stopDryRun();
        return;
    }

    const clickedButton = event && event.currentTarget
        ? event.currentTarget
        : null;
    const forceNew = !!(clickedButton && clickedButton.dataset.newRun === "true");

    if (forceNew) {
        const confirmed = window.confirm(
            "Start a new Dry Run? The files will be re-analyzed and a new History entry will be created. The previous run will remain in History."
        );
        if (!confirmed) {
            return;
        }
    }

    dryRunActive = true;
    setDryRunButtons(true);

    setRunStatus(
        "Starting",
        0,
        0,
        "",
        0
    );

    try {
        const url = forceNew
            ? "/api/dry-run?new_run=true"
            : "/api/dry-run";
        const response = await fetch(
            url,
            { method: "POST" }
        );

        if (!response.ok) {
            let detail = `HTTP ${response.status}`;
            try {
                const data = await response.json();
                detail = data.detail || detail;
            } catch (error) {
                // Keep the HTTP status if the response is not JSON.
            }
            throw new Error(detail);
        }

        await pollDryRunStatus();

    } catch (error) {
        console.error("Dry Run failed:", error);

        setRunStatus(
            "Error",
            0,
            0,
            error.message,
            0,
            error.message
        );
    } finally {
        dryRunActive = false;
        await refreshRunStatus();
    }
}


async function stopDryRun() {
    setRunStatus(
        "Stopping",
        0,
        0,
        "Finishing current file...",
        0
    );

    try {
        const response = await fetch(
            "/api/dry-run/stop",
            { method: "POST" }
        );

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
    } catch (error) {
        console.error("Stop request failed:", error);
        setRunStatus(
            "Error stopping",
            0,
            0,
            error.message,
            0,
            error.message
        );
    }
}


function setDryRunButtons(running, phase) {
    const hasResult = !running && (
        phase === "Finished (cached)" ||
        phase === "Finished"
    );

    document.querySelectorAll(".dry-run-button").forEach((button) => {
        button.disabled = false;
        button.textContent = running
            ? "Stop"
            : (hasResult ? "New Dry Run" : "Dry Run");
        button.classList.toggle("danger", running);
        button.dataset.newRun = hasResult ? "true" : "false";
    });
}


async function pollDryRunStatus() {
    while (true) {
        const response = await fetch(
            "/api/dry-run/status",
            { cache: "no-store" }
        );

        if (!response.ok) {
            await sleep(750);
            continue;
        }

        const data = await response.json();

        setRunStatus(
            data.phase,
            data.current,
            data.total,
            data.filename,
            data.percent,
            data.error
        );

        setDryRunButtons(!!data.running, data.phase);

        if (!data.running) {
            if (data.statistics) {
                updateStatistics(data.statistics);
                updateResults(data.results || [], data.collisions || {}, data.collision_details || {});
                setReportAvailable(true);
            }

            return;
        }

        await sleep(500);
    }
}


function sleep(milliseconds) {
    return new Promise(
        (resolve) =>
            setTimeout(
                resolve,
                milliseconds
            )
    );
}


function setRunStatus(
    phase,
    current,
    total,
    filename,
    percent,
    error
) {
    const statusBar =
        document.querySelector(
            "#run-status-bar"
        );

    const statusText =
        document.querySelector(
            "#run-status-text"
        );

    const statusFile =
        document.querySelector(
            "#run-status-file"
        );

    const statusFill =
        document.querySelector(
            "#run-status-fill"
        );

    const statusCount =
        document.querySelector(
            "#run-status-count"
        );

    if (!statusBar) {
        return;
    }

    statusBar.classList.toggle(
        "error",
        Boolean(error) ||
        phase === "Error"
    );

    statusText.textContent =
        error ||
        phase ||
        "Idle";

    statusFile.textContent =
        filename ||
        "";

    const calculatedPercent =
        percent !== undefined
            ? percent
            : total
                ? Math.round(
                    (current / total) * 100
                )
                : 0;

    statusFill.style.width =
        `${Math.max(
            0,
            Math.min(
                100,
                calculatedPercent
            )
        )}%`;

    statusCount.textContent =
        total
            ? `${current} / ${total}`
            : "";
}


let runStatusPollTimer = null;

function startGlobalRunStatusPolling() {
    if (runStatusPollTimer) return;
    refreshRunStatus();
    runStatusPollTimer = window.setInterval(refreshRunStatus, 1000);
}

function initializeRunStatus() {
    setRunStatus(
        "Idle",
        0,
        0,
        "",
        0
    );
}


let runStatusRefreshInFlight = false;
let lastApplyPlanKey = null;

async function refreshRunStatus() {
    if (runStatusRefreshInFlight) return;
    runStatusRefreshInFlight = true;

    try {
        // Apply runs in a worker thread, so this status endpoint can remain
        // live while files are being hashed/moved.
        const applyResponse = await fetch(
            "/api/apply/status",
            { cache: "no-store" }
        );

        if (applyResponse.ok) {
            const applyData = await applyResponse.json();
            if (applyData.running) {
                setRunStatus(
                    applyData.phase,
                    applyData.current,
                    applyData.total,
                    applyData.filename,
                    applyData.percent,
                    applyData.error
                );
                document.querySelectorAll(".dry-run-button").forEach((button) => {
                    button.disabled = true;
                    button.textContent = "Apply in progress...";
                });
                return;
            }
        }

        const response = await fetch(
            "/api/dry-run/status",
            { cache: "no-store" }
        );

        if (!response.ok) {
            return;
        }

        const data = await response.json();

        setRunStatus(
            data.phase,
            data.current,
            data.total,
            data.filename,
            data.percent,
            data.error
        );

        setDryRunButtons(!!data.running, data.phase);

        if (!data.running && data.statistics) {
            // The Dry Run result describes classification, while the Apply plan
            // describes the current physical state. Fetch the latter before
            // rendering either dashboard statistics or result rows so a poll
            // can never briefly paint stale READY/APPLY values and then replace
            // them with the post-Apply state.
            let displayStatistics = { ...data.statistics };
            let displayResults = data.results || [];
            try {
                const planResponse = await fetch(
                    "/api/apply/plan",
                    { cache: "no-store" }
                );
                if (planResponse.ok) {
                    const plan = await planResponse.json();
                    const planStats = plan.statistics || {};
                    displayStatistics = {
                        ...data.statistics,
                        apply_ready_count: planStats.ready ?? 0,
                        apply_blocked_count: planStats.blocked ?? 0,
                        apply_already_applied_count: planStats.already_applied ?? 0,
                    };
                    const planByFilename = new Map(
                        (plan.items || []).map((item) => [String(item.filename || ""), item])
                    );
                    displayResults = displayResults.map((result) => {
                        if (result.status !== "AUTO") return result;
                        const planItem = planByFilename.get(String(result.source || ""));
                        if (!planItem) return result;
                        if (planItem.status === "APPLIED") {
                            return { ...result, status: "APPLIED", reason: planItem.reason };
                        }
                        if (planItem.status === "BLOCKED") {
                            return { ...result, status: "BLOCKED", reason: planItem.reason };
                        }
                        return result;
                    });
                }
            } catch (error) {
                console.debug("Could not refresh Apply plan:", error);
            }

            updateStatistics(displayStatistics);
            updateResults(displayResults, data.collisions || {}, data.collision_details || {});
            updateDashboardRunBadge({
                ...displayStatistics,
                collisions: displayStatistics.collisions ?? Object.keys(data.collisions || {}).length,
            });
            setReportAvailable(true);

            const applyPlanKey = `${displayStatistics.files ?? 0}|${displayStatistics.apply_ready_count ?? 0}|${displayStatistics.apply_already_applied_count ?? 0}|${displayStatistics.apply_blocked_count ?? 0}|${displayStatistics.apply_completed ? 1 : 0}`;
            lastApplyPlanKey = applyPlanKey;
        } else if (!data.running) {
            updateStatistics({ auto: 0, review: 0, ignore: 0, errors: 0, collisions: 0, blocked_files: 0, files: 0 });
            updateResults([]);
            setReportAvailable(false);
        }

    } catch (error) {
        console.error(
            "Status refresh failed:",
            error
        );
    } finally {
        runStatusRefreshInFlight = false;
    }
}


function updateStatistics(stats) {
    const elements = {
        auto: document.querySelector('[data-stat="auto"]'),
        review: document.querySelector('[data-stat="review"]'),
        ignore: document.querySelector('[data-stat="ignore"]'),
        collision: document.querySelector('[data-stat="collision"]'),
    };

    const blockedFiles = Number(stats.apply_blocked_count ?? stats.blocked_files ?? 0);
    const appliedCount = Number(stats.apply_already_applied_count ?? stats.applied_count ?? 0);
    const ready = Number.isFinite(Number(stats.apply_ready_count))
        ? Math.max(0, Number(stats.apply_ready_count))
        : Math.max(0, Number(stats.auto || 0) - blockedFiles - appliedCount);
    if (elements.auto) elements.auto.textContent = ready;
    if (elements.review) elements.review.textContent = stats.review ?? 0;
    if (elements.ignore) elements.ignore.textContent = stats.ignore ?? 0;
    if (elements.collision) elements.collision.textContent = stats.blocked_files ?? stats.collisions ?? 0;

    const errorCount = Number(stats.errors || 0);
    const errorAlert = document.querySelector("#dashboard-error-alert");
    const errorCountEl = document.querySelector("#dashboard-error-count");
    if (errorCountEl) errorCountEl.textContent = errorCount;
    if (errorAlert) errorAlert.hidden = errorCount === 0;

    updateDashboardRunBadge(stats);

    const applyButton = document.querySelector("#apply-header-button, .apply-changes-link");
    if (applyButton) {
        const appliedCount = Number(stats.apply_already_applied_count ?? stats.applied_count ?? 0);
        const applyCompleted = Boolean(stats.apply_completed);
        const blockedFiles = Number(stats.apply_blocked_count ?? stats.blocked_files ?? 0);
        const ready = Number.isFinite(Number(stats.apply_ready_count))
            ? Math.max(0, Number(stats.apply_ready_count))
            : Math.max(0, Number(stats.auto || 0) - blockedFiles - appliedCount);

        if (applyCompleted && ready === 0) {
            applyButton.textContent = appliedCount
                ? `Applied ${appliedCount} file${appliedCount === 1 ? "" : "s"}`
                : "Apply complete";
            applyButton.classList.add("muted-action");
            applyButton.setAttribute("aria-disabled", "true");
        } else {
            applyButton.textContent = ready
                ? `Apply ${ready} file${ready === 1 ? "" : "s"}`
                : "Apply Changes";
            applyButton.classList.toggle("muted-action", ready === 0);
            applyButton.removeAttribute("aria-disabled");
        }
    }
}

function updateDashboardRunBadge(stats = {}) {
    const runBadge = document.querySelector("#dashboard-run-badge-text");
    const runBadgeWrap = document.querySelector("#dashboard-run-badge");
    if (!runBadge || !runBadgeWrap) return;

    const total = Number.isFinite(Number(stats.files))
        ? Number(stats.files)
        : Number(stats.auto || 0)
            + Number(stats.review || 0)
            + Number(stats.ignore || 0)
            + Number(stats.errors || 0);

    runBadge.textContent = total
        ? `${total} files in current run`
        : "No active run";
    runBadgeWrap.classList.toggle("has-results", total > 0);
}

let dashboardResults = [];
let dashboardFilter = "ALL";
let dashboardSearch = "";
let dashboardPage = 1;
const DASHBOARD_PAGE_SIZE = 50;

function normalizeDashboardResults(results, collisions, collisionDetails = {}) {
    const collisionMap = new Map();
    Object.entries(collisions || {}).forEach(([destination, filenames]) => {
        const files = collisionDetails[destination] || (filenames || []).map((filename) => ({ filename }));
        (filenames || []).forEach((filename) => {
            collisionMap.set(filename, {
                destination,
                filenames: filenames || [],
                files,
                identical: files.length > 1
                    && files.every((file) => file.sha256 && file.sha256 === files[0].sha256),
            });
        });
    });

    return (results || []).map((result) => {
        const collision = collisionMap.get(result.source);
        if (!collision || result.status !== "AUTO") return result;
        return {
            ...result,
            status: "BLOCKED",
            reason: "Destination collision with another file in this run",
            collision,
        };
    });
}

function resultStatusLabel(status) {
    return {
        AUTO: "READY",
        REVIEW: "REVIEW",
        IGNORE: "IGNORE",
        BLOCKED: "BLOCKED",
        ERROR: "ERROR",
        APPLIED: "APPLIED",
    }[status] || status;
}

function filteredDashboardResults() {
    const query = dashboardSearch.trim().toLowerCase();
    return dashboardResults.filter((result) => {
        if (dashboardFilter !== "ALL" && result.status !== dashboardFilter) return false;
        if (!query) return true;
        return [
            result.source,
            result.destination,
            result.publication,
            result.reason,
            result.source_type,
        ].some((value) => String(value || "").toLowerCase().includes(query));
    });
}

function dashboardHasActiveFilter() {
    return dashboardFilter !== "ALL" || Boolean(dashboardSearch.trim());
}

function bindDashboardDryRunButtons() {
    document.querySelectorAll(".dry-run-button").forEach((button) => {
        if (button.dataset.bound === "true") return;
        button.dataset.bound = "true";
        button.addEventListener("click", runDryRun);
    });
}

function updateResults(results, collisions = {}, collisionDetails = {}) {
    const container = document.querySelector("#results-container");
    if (!container) return;
    dashboardResults = normalizeDashboardResults(results, collisions, collisionDetails);
    renderDashboardResults();
}

function renderDashboardResults() {
    const container = document.querySelector("#results-container");
    if (!container) return;

    const visible = filteredDashboardResults();
    const totalPages = Math.max(1, Math.ceil(visible.length / DASHBOARD_PAGE_SIZE));
    dashboardPage = Math.min(Math.max(dashboardPage, 1), totalPages);
    const startIndex = (dashboardPage - 1) * DASHBOARD_PAGE_SIZE;
    const pageItems = visible.slice(startIndex, startIndex + DASHBOARD_PAGE_SIZE);

    const count = document.querySelector("#visible-result-count");
    if (count) count.textContent = visible.length;

    const summary = document.querySelector("#current-run-summary-text");
    if (summary) {
        if (!dashboardResults.length) {
            summary.textContent = "Run a Dry Run to see the proposed changes.";
        } else if (!dashboardHasActiveFilter()) {
            const ready = dashboardResults.filter((item) => item.status === "AUTO").length;
            const review = dashboardResults.filter((item) => item.status === "REVIEW").length;
            const blocked = dashboardResults.filter((item) => item.status === "BLOCKED").length;
            const ignore = dashboardResults.filter((item) => item.status === "IGNORE").length;
            const errors = dashboardResults.filter((item) => item.status === "ERROR").length;
            summary.textContent = `${ready} ready · ${review} review · ${blocked} blocked · ${ignore} ignored · ${errors} errors`;
        } else {
            summary.textContent = `Filtered from ${dashboardResults.length} files`;
        }
    }

    const clearButton = document.querySelector("#clear-dashboard-filter");
    if (clearButton) clearButton.hidden = !dashboardHasActiveFilter();

    if (!dashboardResults.length) {
        container.innerHTML = `
            <div class="empty-state dashboard-empty-state">
                <div class="empty-icon">▤</div>
                <h3>No dry-run results yet</h3>
                <p>Run a dry run to see what Magazine Sorter would do with your files.</p>
                <button class="button primary dry-run-button">Start Dry Run</button>
            </div>
        `;
        bindDashboardDryRunButtons();
        return;
    }

    if (!visible.length) {
        container.innerHTML = `
            <div class="empty-state dashboard-empty-state compact-empty">
                <div class="empty-icon">⌕</div>
                <h3>No matching files</h3>
                <p>Try another search or clear the active filter.</p>
                <button class="button secondary" type="button" id="clear-result-filter">Clear filters</button>
            </div>
        `;
        document.querySelector("#clear-result-filter")?.addEventListener("click", clearDashboardFilters);
        return;
    }

    const rows = pageItems.map((result) => {
        const status = String(result.status || "").toLowerCase();
        const destination = result.destination || "—";
        const globalIndex = dashboardResults.indexOf(result);
        const destinationParts = splitDestination(destination);
        const blockedNote = status === "blocked"
            ? `<span class="result-destination-note">Safety block · no files will be overwritten</span>`
            : status === "error"
                ? `<span class="result-destination-note error-note">Processing error · no files will be changed</span>`
                : "";
        return `
            <button class="result-table-row" type="button" data-result-index="${globalIndex}" aria-label="Inspect ${escapeHtml(result.source || "file")}">
                <span class="result-table-status ${status}">${escapeHtml(resultStatusLabel(result.status))}</span>
                <span class="result-table-source" title="${escapeHtml(result.source || "")}">
                    <span class="result-file-name">${escapeHtml(result.source || "—")}</span>
                    <span class="result-file-meta">${escapeHtml(result.publication || result.source_type || "—")}</span>
                </span>
                <span class="result-table-arrow" aria-hidden="true">→</span>
                <span class="result-table-destination" title="${escapeHtml(destination)}">
                    <span class="result-destination-folder">${escapeHtml(destinationParts.folder)}</span>
                    <span class="result-destination-file">${escapeHtml(destinationParts.file)}</span>
                    ${blockedNote}
                </span>
                <span class="result-table-action">Inspect&nbsp;›</span>
            </button>
        `;
    }).join("");

    const pageStart = startIndex + 1;
    const pageEnd = startIndex + pageItems.length;
    const pager = totalPages > 1 ? `
        <div class="result-pagination">
            <span>Showing ${pageStart}–${pageEnd} of ${visible.length}</span>
            <div class="result-pagination-actions">
                <button class="button secondary button-small" type="button" id="dashboard-prev" ${dashboardPage <= 1 ? "disabled" : ""}>Previous</button>
                <span class="result-page-number">Page ${dashboardPage} of ${totalPages}</span>
                <button class="button secondary button-small" type="button" id="dashboard-next" ${dashboardPage >= totalPages ? "disabled" : ""}>Next</button>
            </div>
        </div>
    ` : "";

    container.innerHTML = `
        <div class="result-table">
            <div class="result-table-head">
                <span>Status</span>
                <span>Original filename</span>
                <span></span>
                <span>Proposed destination</span>
                <span></span>
            </div>
            ${rows}
        </div>
        ${pager}
    `;

    container.querySelectorAll(".result-table-row").forEach((row) => {
        row.addEventListener("click", () => {
            const result = dashboardResults[Number(row.dataset.resultIndex)];
            if (result) openInspect(result);
        });
    });

    container.querySelector("#dashboard-prev")?.addEventListener("click", () => {
        dashboardPage -= 1;
        renderDashboardResults();
    });
    container.querySelector("#dashboard-next")?.addEventListener("click", () => {
        dashboardPage += 1;
        renderDashboardResults();
    });
}

function splitDestination(destination) {
    const value = String(destination || "—");
    if (value === "—") return { folder: "", file: value };
    const separator = Math.max(value.lastIndexOf("/"), value.lastIndexOf("\\"));
    if (separator < 0) return { folder: "", file: value };
    return { folder: value.slice(0, separator + 1), file: value.slice(separator + 1) };
}

function clearDashboardFilters() {
    dashboardFilter = "ALL";
    dashboardSearch = "";
    dashboardPage = 1;
    syncDashboardFilters();
    renderDashboardResults();
}

function syncDashboardFilters() {
    const select = document.querySelector("#result-filter");
    if (select) select.value = dashboardFilter;
    const search = document.querySelector("#result-search");
    if (search && search.value !== dashboardSearch) search.value = dashboardSearch;
    document.querySelectorAll(".stat-card-clickable").forEach((card) => {
        card.classList.toggle("active-filter", card.dataset.statusFilter === dashboardFilter);
        card.setAttribute("aria-pressed", card.dataset.statusFilter === dashboardFilter ? "true" : "false");
    });
}

function formatFileSize(bytes) {
    const value = Number(bytes);
    if (!Number.isFinite(value)) return "Unknown size";
    if (value < 1024) return `${value} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let size = value / 1024;
    let unit = 0;
    while (size >= 1024 && unit < units.length - 1) {
        size /= 1024;
        unit += 1;
    }
    return `${size.toFixed(size >= 10 ? 1 : 2)} ${units[unit]}`;
}

function renderCollisionComparison(collision) {
    if (!collision || !Array.isArray(collision.files) || !collision.files.length) return "";

    const files = collision.files;
    const hashesAvailable = files.every((file) => file.hash_status === "ok" && file.sha256);
    const identical = hashesAvailable && files.length > 1
        && files.every((file) => file.sha256 === files[0].sha256);
    const verdict = identical
        ? `<span class="collision-verdict identical">IDENTICAL FILE CONTENTS</span>`
        : hashesAvailable
            ? `<span class="collision-verdict different">DIFFERENT FILE CONTENTS</span>`
            : `<span class="collision-verdict unavailable">FILE CONTENT COULD NOT BE VERIFIED</span>`;

    const cards = files.map((file, index) => {
        const hash = file.sha256 || "Not available";
        return `
            <div class="collision-file-card">
                <div class="collision-file-header">
                    <span>FILE ${index + 1}</span>
                    <span>${escapeHtml(formatFileSize(file.size))}</span>
                </div>
                <div class="collision-file-name" title="${escapeHtml(file.filename || "")}">${escapeHtml(file.filename || "—")}</div>
                <div class="collision-file-meta">SHA-256</div>
                <code class="collision-file-hash" title="${escapeHtml(hash)}">${escapeHtml(hash)}</code>
                ${file.file_url ? `
                    <button
                        class="button secondary button-small collision-file-open${index === 0 ? " active" : ""}"
                        type="button"
                        data-file-url="${escapeHtml(file.file_url)}"
                        data-filename="${escapeHtml(file.filename || "")}"
                    >View this PDF</button>
                ` : `<span class="collision-file-unavailable">Preview unavailable</span>`}
                <button
                    class="button secondary button-small collision-file-quarantine"
                    type="button"
                    data-filename="${escapeHtml(file.filename || "")}"
                >Move to _duplicates</button>
            </div>
        `;
    }).join("");

    return `
        <section class="collision-comparison">
            <div class="collision-comparison-header">
                <div>
                    <div class="inspect-side-label">COLLISION FILES</div>
                    <p>${escapeHtml(String(files.length))} files resolve to the same destination.</p>
                </div>
                ${verdict}
            </div>
            <div class="collision-file-grid">${cards}</div>
            <div class="collision-destination">
                <span>DESTINATION</span>
                <code>${escapeHtml(collision.destination || "—")}</code>
            </div>
        </section>
    `;
}


function PathExtension(filename) {
    const value = String(filename || "");
    const match = value.match(/(\.[^.\/]+)$/);
    return match ? match[1] : "";
}

function suggestSpecialTitle(filename, publication) {
    let title = String(filename || "").replace(/\.[^.]+$/, "").trim();
    const pub = String(publication || "").trim();

    title = title.replace(/^\d{4}[._-]\d{1,2}\s*/i, "").trim();

    if (pub) {
        const variants = [pub, pub.replace(/\bog\b/gi, "and"), pub.replace(/\band\b/gi, "og")];
        for (const variant of [...new Set(variants)]) {
            const escaped = variant.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
            const pubPattern = new RegExp(`^${escaped}\\s*[-–—:._]?\\s*`, "i");
            const candidate = title.replace(pubPattern, "").trim();
            if (candidate !== title) {
                title = candidate;
                break;
            }
        }
    }

    title = title
        .replace(/\s*[-–—]\s*\d{1,2}[.-]\d{1,2}[.-]\d{4}.*$/i, "")
        .replace(/\s+20\d{2}\s*$/i, "")
        .replace(/[_]+/g, " ")
        .replace(/\s{2,}/g, " ")
        .replace(/^[-–—:]+|[-–—:]+$/g, "")
        .trim();

    return title || String(filename || "").replace(/\.[^.]+$/, "").trim();
}

function openInspect(result) {
    const backdrop = document.querySelector("#inspect-backdrop");
    const content = document.querySelector("#inspect-content");
    const title = document.querySelector("#inspect-title");
    const label = document.querySelector("#inspect-status-label");
    if (!backdrop || !content || !title || !label) return;

    const status = result.status;
    const destination = result.destination || "No destination proposed";
    const sourceType = result.source_type || "Unknown";
    const reason = result.reason || "No additional reason recorded.";
    const collision = result.collision;
    const canReclassify = status !== "ERROR" && status !== "APPLIED";
    const publication = String(result.publication || "").trim();
    const filename = String(result.source || "").trim();
    const fileUrl = result.file_url || "";
    const metadata = result.metadata || {};
    const currentClassification = String(result.classification || "").toUpperCase();
    const isPdf = PathExtension(filename).toLowerCase() === ".pdf";
    const destinationFilename = destination.includes("/")
        ? destination.slice(destination.lastIndexOf("/") + 1)
        : destination;
    const hasFilenameUpgrade = destinationFilename
        && destinationFilename !== "No destination proposed"
        && destinationFilename !== filename;

    const specialCandidate = String(reason || "").toUpperCase().startsWith("SPECIAL_CANDIDATE");
    const inferredClassification = currentClassification === "SPECIAL"
        ? "SPECIAL"
        : currentClassification === "STANDALONE"
            ? "STANDALONE"
            : specialCandidate
                ? "SPECIAL"
                : "ISSUE";
    const suggestedSpecialTitle = String(result.special_title || suggestSpecialTitle(filename, publication)).trim();
    const suggestedYear = metadata.year ?? "";
    const reviewLink = status === "REVIEW"
        ? `<a class="button primary inspect-review-link" href="/review">Open Review</a>`
        : "";

    label.textContent = resultStatusLabel(status);
    title.textContent = result.source || "File details";

    const reclassifyBlock = canReclassify ? `
        <section class="inspect-section inspect-reclassify">
            <div class="inspect-section-heading">
                <div>
                    <div class="inspect-side-label">CLASSIFICATION</div>
                    <h3>Correct what Magazine Sorter thinks this file is</h3>
                    <p>The current parser/OCR suggestion is pre-filled. You can change it here without moving or renaming the file until Apply Changes.</p>
                </div>
            </div>

            <div class="form-field">
                <label for="inspect-reclassify-type">Classification</label>
                <select id="inspect-reclassify-type">
                    <option value="ISSUE" ${inferredClassification === "ISSUE" ? "selected" : ""}>Normal issue</option>
                    <option value="SPECIAL" ${inferredClassification === "SPECIAL" ? "selected" : ""}>Special</option>
                    <option value="STANDALONE" ${inferredClassification === "STANDALONE" ? "selected" : ""}>Standalone</option>
                </select>
                <div class="publication-type-hint">Specials stay in the publication folder. Standalone files use Komga's <code>_oneshots</code> directory convention.</div>
            </div>

            <div id="inspect-publication-fields">
                <div class="form-field">
                    <label for="inspect-publication">Publication</label>
                    <select id="inspect-publication">
                        <option value="${escapeHtml(publication)}" selected>${escapeHtml(publication || "Select publication...")}</option>
                    </select>
                </div>
                <div id="inspect-custom-publication" class="form-field hidden">
                    <label for="inspect-custom-publication-input">Publication name</label>
                    <input id="inspect-custom-publication-input" type="text" value="" placeholder="e.g. My Magazine">
                </div>
                <div id="inspect-normal-fields">
                    <div class="inspect-metadata-grid">
                        <div class="form-field">
                            <label for="inspect-year">Year</label>
                            <input id="inspect-year" type="number" min="1900" max="2100" value="${escapeHtml(String(metadata.year ?? ""))}" placeholder="e.g. 2022">
                        </div>
                        <div class="form-field">
                            <label for="inspect-issue">Issue</label>
                            <input id="inspect-issue" type="number" min="1" max="9999" value="${escapeHtml(String(metadata.issue ?? ""))}" placeholder="e.g. 03">
                        </div>
                        <div class="form-field">
                            <label for="inspect-month">Month</label>
                            <input id="inspect-month" type="number" min="1" max="12" value="${escapeHtml(String(metadata.month ?? ""))}" placeholder="e.g. 2">
                        </div>
                        <div class="form-field">
                            <label for="inspect-day">Day</label>
                            <input id="inspect-day" type="number" min="1" max="31" value="${escapeHtml(String(metadata.day ?? ""))}" placeholder="e.g. 15">
                        </div>
                        <div class="form-field">
                            <label for="inspect-week">Week</label>
                            <input id="inspect-week" type="number" min="1" max="53" value="${escapeHtml(String(metadata.week ?? ""))}" placeholder="e.g. 08">
                        </div>
                    </div>
                    <div class="publication-type-hint" id="inspect-profile-hint">The existing metadata suggestion is pre-filled. The publication profile decides which values are used for the destination.</div>
                </div>
            </div>

            <div id="inspect-special-fields" hidden>
                <div class="form-field">
                    <label for="inspect-special-title">Special title</label>
                    <input id="inspect-special-title" type="text" value="${escapeHtml(suggestedSpecialTitle)}" placeholder="e.g. Efterårs- og vinterhaven">
                    <div class="publication-type-hint">Suggested from the filename. Edit it if the cover shows a different title.</div>
                </div>
                <div class="form-field">
                    <label for="inspect-special-year">Year <span class="optional-label">Optional</span></label>
                    <input id="inspect-special-year" type="number" min="1900" max="2100" value="${escapeHtml(String(suggestedYear))}" placeholder="e.g. 2022">
                </div>
            </div>

            <div id="inspect-standalone-fields" hidden>
                <div class="form-field">
                    <label for="inspect-standalone-filename">Filename</label>
                    <input id="inspect-standalone-filename" type="text" value="${escapeHtml(filename)}">
                </div>
            </div>

            <div class="inspect-reclassify-preview">
                <span>PROPOSED DESTINATION</span>
                <code id="inspect-reclassify-destination">Complete the classification</code>
            </div>
            <div class="inspect-reclassify-actions">
                <button class="button primary" type="button" id="inspect-reclassify-button">Save classification</button>
                <span class="form-message" id="inspect-reclassify-message"></span>
            </div>
        </section>
    ` : "";

    const collisionBlock = collision ? renderCollisionComparison(collision) : "";

    const filenameUpgradeBlock = `
        <section class="inspect-section inspect-filename-section">
            <div class="inspect-section-heading">
                <div>
                    <div class="inspect-side-label">FILENAME</div>
                    <h3>Current vs. proposed name</h3>
                </div>
                <span class="inspect-filename-badge">${hasFilenameUpgrade ? "NAME CHANGE" : "UNCHANGED"}</span>
            </div>
            <div class="inspect-filename-grid">
                <div>
                    <span class="inspect-mini-label">CURRENT</span>
                    <code>${escapeHtml(filename || "—")}</code>
                </div>
                <div>
                    <span class="inspect-mini-label">PROPOSED</span>
                    <code>${escapeHtml(hasFilenameUpgrade ? destinationFilename : (destinationFilename || filename || "—"))}</code>
                </div>
            </div>
            <p class="inspect-help-text">Filename cleanup stays a separate, deliberate step. This view only shows the name Magazine Sorter currently proposes.</p>
        </section>
    `;

    content.innerHTML = `
        <div class="inspect-workspace">
            <section class="inspect-preview-pane">
                <div class="inspect-preview-toolbar">
                    <div>
                        <div class="inspect-side-label">FILE PREVIEW</div>
                        <strong id="inspect-preview-name">${escapeHtml(filename || "Preview")}</strong>
                    </div>
                    <div class="inspect-preview-actions">
                        ${fileUrl ? `<a class="button secondary button-small" href="${escapeHtml(fileUrl)}" target="_blank" rel="noopener">Open in tab</a>` : ""}
                    </div>
                </div>
                <div class="inspect-preview-frame">
                    ${fileUrl && isPdf ? `
                        <iframe id="inspect-pdf-frame" src="${escapeHtml(fileUrl)}#zoom=page-width" title="PDF preview"></iframe>
                    ` : fileUrl ? `
                        <div class="inspect-preview-empty">
                            <div class="empty-icon">◫</div>
                            <h3>Preview not available</h3>
                            <p>Browser preview is currently available for PDF files.</p>
                            <a class="button secondary" href="${escapeHtml(fileUrl)}" target="_blank" rel="noopener">Open file</a>
                        </div>
                    ` : `
                        <div class="inspect-preview-empty">
                            <div class="empty-icon">◫</div>
                            <h3>Preview unavailable</h3>
                            <p>The source file is no longer available at its original location.</p>
                        </div>
                    `}
                </div>
            </section>

            <aside class="inspect-info-pane">
                <div class="inspect-status-line">
                    <span class="result-table-status ${String(status).toLowerCase()}">${escapeHtml(resultStatusLabel(status))}</span>
                    <span>${escapeHtml(sourceType)}</span>
                </div>

                <section class="inspect-section">
                    <div class="inspect-comparison">
                        <div class="inspect-side original">
                            <div class="inspect-side-label">ORIGINAL FILE</div>
                            <div class="inspect-side-value">${escapeHtml(result.source || "—")}</div>
                        </div>
                        <div class="inspect-side proposed">
                            <div class="inspect-side-label">PROPOSED DESTINATION</div>
                            <div class="inspect-side-value">${escapeHtml(destination)}</div>
                        </div>
                    </div>
                </section>

                ${collisionBlock}

                <section class="inspect-section inspect-details">
                    <div class="inspect-detail-row"><span>Publication</span><strong>${escapeHtml(result.publication || "—")}</strong></div>
                    <div class="inspect-detail-row"><span>Detected by</span><strong>${escapeHtml(sourceType)}</strong></div>
                    <div class="inspect-detail-row"><span>Reason</span><strong>${escapeHtml(reason)}</strong></div>
                </section>

                ${filenameUpgradeBlock}
                ${reclassifyBlock}
            </aside>
        </div>
    `;

    const footer = document.querySelector("#inspect-footer");
    if (footer) {
        footer.innerHTML = `
            ${reviewLink}
            <button class="button secondary" type="button" id="inspect-close-bottom">Close</button>
        `;
    }

    backdrop.hidden = false;
    document.body.classList.add("inspect-open");

    document.querySelector("#inspect-close-bottom")?.addEventListener("click", closeInspect);

    // Collision cards can switch the large preview without closing the inspector.
    document.querySelectorAll(".collision-file-open").forEach((button) => {
        button.addEventListener("click", () => {
            const url = button.dataset.fileUrl || "";
            const name = button.dataset.filename || "Preview";
            const frame = document.querySelector("#inspect-pdf-frame");
            const previewName = document.querySelector("#inspect-preview-name");
            if (!frame || !url) return;
            frame.src = `${url}#zoom=page-width`;
            if (previewName) previewName.textContent = name;
            document.querySelectorAll(".collision-file-open").forEach((item) => item.classList.remove("active"));
            button.classList.add("active");
        });
    });

    document.querySelectorAll(".collision-file-quarantine").forEach((button) => {
        button.addEventListener("click", async () => {
            const filename = button.dataset.filename || "";
            if (!filename) return;

            const confirmed = window.confirm(
                `Move "${filename}" to _duplicates?\n\nThe file will not be deleted. It will be kept in the duplicate quarantine and excluded from future scans.`
            );
            if (!confirmed) return;

            const buttons = document.querySelectorAll(".collision-file-quarantine");
            buttons.forEach((item) => { item.disabled = true; });
            button.textContent = "Moving...";

            try {
                const response = await fetch("/api/result/quarantine-duplicate", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ filename }),
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);

                closeInspect();
                refreshRunStatus();
            } catch (error) {
                console.error("Could not quarantine duplicate:", error);
                alert(`Could not move the file to _duplicates.\n\n${error.message}`);
                buttons.forEach((item) => { item.disabled = false; });
                button.textContent = "Move to _duplicates";
            }
        });
    });

    if (canReclassify) {
        const typeSelect = document.querySelector("#inspect-reclassify-type");
        const publicationSelect = document.querySelector("#inspect-publication");
        const customPublicationField = document.querySelector("#inspect-custom-publication");
        const customPublicationInput = document.querySelector("#inspect-custom-publication-input");
        const publicationFields = document.querySelector("#inspect-publication-fields");
        const normalFields = document.querySelector("#inspect-normal-fields");
        const specialFields = document.querySelector("#inspect-special-fields");
        const standaloneFields = document.querySelector("#inspect-standalone-fields");
        const destinationEl = document.querySelector("#inspect-reclassify-destination");
        const button = document.querySelector("#inspect-reclassify-button");
        const message = document.querySelector("#inspect-reclassify-message");

        const getPublicationName = () => {
            if (publicationSelect?.value === "__custom__") {
                return customPublicationInput?.value.trim() || "";
            }
            return publicationSelect?.value.trim() || "";
        };

        const getMetadata = () => {
            const values = {};
            ["year", "month", "day", "issue", "week"].forEach((name) => {
                const value = document.querySelector(`#inspect-${name}`)?.value.trim() || "";
                values[name] = value === "" ? null : Number(value);
            });
            return values;
        };

        const profileForPublication = () => {
            const selected = publicationSelect?.selectedOptions?.[0];
            return selected?.dataset.profileType || "issue";
        };

        const updateReclassifyPreview = () => {
            const mode = typeSelect?.value || "ISSUE";
            const isSpecial = mode === "SPECIAL";
            const isStandalone = mode === "STANDALONE";
            if (publicationFields) publicationFields.hidden = isStandalone;
            if (normalFields) normalFields.hidden = isSpecial || isStandalone;
            if (specialFields) specialFields.hidden = !isSpecial;
            if (standaloneFields) standaloneFields.hidden = !isStandalone;
            if (!destinationEl) return;

            if (isStandalone) {
                const standaloneFilename = document.querySelector("#inspect-standalone-filename")?.value.trim() || "";
                destinationEl.textContent = standaloneFilename ? `_oneshots/${standaloneFilename}` : "Enter a standalone filename";
                return;
            }

            const publicationValue = getPublicationName();
            if (!publicationValue) {
                destinationEl.textContent = "Select a publication";
                return;
            }

            if (isSpecial) {
                const titleValue = document.querySelector("#inspect-special-title")?.value.trim() || "";
                const yearValue = document.querySelector("#inspect-special-year")?.value.trim() || "";
                if (!titleValue) {
                    destinationEl.textContent = "Enter a Special title";
                    return;
                }
                const extension = PathExtension(filename);
                const specialFilename = `${publicationValue} - ${yearValue ? yearValue + " - " : ""}${titleValue}${extension}`;
                destinationEl.textContent = `${publicationValue}/${specialFilename}`;
                return;
            }

            const values = getMetadata();
            const profileType = profileForPublication();
            let proposed = "";
            if (profileType === "date" && values.year != null && values.month != null && values.day != null) {
                proposed = `${publicationValue} - ${values.year}-${String(values.month).padStart(2, "0")}-${String(values.day).padStart(2, "0")}${PathExtension(filename)}`;
            } else if (profileType === "month" && values.year != null && values.month != null) {
                proposed = `${publicationValue} - ${values.year}-${String(values.month).padStart(2, "0")}${PathExtension(filename)}`;
            } else if (profileType === "week" && values.year != null && values.week != null) {
                proposed = `${publicationValue} - ${values.year} - Uge ${String(values.week).padStart(2, "0")}${PathExtension(filename)}`;
            } else if (values.issue != null) {
                proposed = `${publicationValue} - ${values.year != null ? values.year + " - " : ""}Nr ${String(values.issue).padStart(2, "0")}${PathExtension(filename)}`;
            }
            destinationEl.textContent = proposed ? `${publicationValue}/${proposed}` : "Complete the required metadata";
        };

        const populatePublications = async () => {
            try {
                const response = await fetch("/api/publications", { cache: "no-store" });
                if (!response.ok) return;
                const data = await response.json();
                const items = data.items || [];
                const current = publication;
                publicationSelect.innerHTML = `
                    <option value="">Select known publication...</option>
                    ${items.map((item) => `
                        <option value="${escapeHtml(item.name)}" data-profile-type="${escapeHtml(item.type || "issue")}" ${item.name === current ? "selected" : ""}>${escapeHtml(item.name)}</option>
                    `).join("")}
                    <option value="__custom__" ${current && !items.some((item) => item.name === current) ? "selected" : ""}>Enter publication manually...</option>
                `;
                if (current && !items.some((item) => item.name === current)) {
                    customPublicationField?.classList.remove("hidden");
                    if (customPublicationInput) customPublicationInput.value = current;
                }
                updateReclassifyPreview();
            } catch (error) {
                console.debug("Could not load inspector publications:", error);
            }
        };

        const syncCustomPublication = () => {
            const custom = publicationSelect?.value === "__custom__";
            customPublicationField?.classList.toggle("hidden", !custom);
            updateReclassifyPreview();
        };

        typeSelect?.addEventListener("change", updateReclassifyPreview);
        publicationSelect?.addEventListener("change", syncCustomPublication);
        customPublicationInput?.addEventListener("input", updateReclassifyPreview);
        ["year", "month", "day", "issue", "week", "special-title", "special-year", "standalone-filename"].forEach((name) => {
            document.querySelector(`#inspect-${name}`)?.addEventListener("input", updateReclassifyPreview);
        });

        button?.addEventListener("click", async () => {
            const action = typeSelect?.value || "ISSUE";
            const payload = { filename, action: action === "ISSUE" ? "NORMAL" : action };
            if (action !== "STANDALONE") {
                payload.publication = getPublicationName();
            }
            if (action === "NORMAL") {
                payload.metadata = getMetadata();
            } else if (action === "SPECIAL") {
                payload.title = document.querySelector("#inspect-special-title")?.value.trim() || "";
                payload.year = document.querySelector("#inspect-special-year")?.value.trim() || null;
            } else {
                payload.standalone_filename = document.querySelector("#inspect-standalone-filename")?.value.trim() || "";
            }

            button.disabled = true;
            if (message) {
                message.className = "form-message";
                message.textContent = "Saving...";
            }

            try {
                const response = await fetch("/api/result/reclassify", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
                if (message) {
                    message.className = "form-message success";
                    message.textContent = "Saved. No file was moved or renamed.";
                }
                await refreshRunStatus();
                closeInspect();
            } catch (error) {
                if (message) {
                    message.className = "form-message error";
                    message.textContent = error.message;
                }
            } finally {
                button.disabled = false;
            }
        });

        syncCustomPublication();
        updateReclassifyPreview();
        populatePublications();
    }
}

function closeInspect() {
    const backdrop = document.querySelector("#inspect-backdrop");
    if (!backdrop) return;
    backdrop.hidden = true;
    document.body.classList.remove("inspect-open");
}

function bindDashboardControls() {
    const filter = document.querySelector("#result-filter");
    const search = document.querySelector("#result-search");

    filter?.addEventListener("change", () => {
        dashboardFilter = filter.value;
        dashboardPage = 1;
        syncDashboardFilters();
        renderDashboardResults();
    });

    search?.addEventListener("input", () => {
        dashboardSearch = search.value;
        dashboardPage = 1;
        renderDashboardResults();
    });

    document.querySelectorAll(".stat-card-clickable").forEach((card) => {
        card.addEventListener("click", () => {
            const next = card.dataset.statusFilter || "ALL";
            dashboardFilter = dashboardFilter === next ? "ALL" : next;
            dashboardPage = 1;
            syncDashboardFilters();
            renderDashboardResults();
            document.querySelector(".current-run-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
        });
    });

    document.querySelector("#inspect-close")?.addEventListener("click", closeInspect);
    document.querySelector("#inspect-backdrop")?.addEventListener("click", (event) => {
        if (event.target.id === "inspect-backdrop") closeInspect();
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") closeInspect();
    });

    document.querySelector("#clear-dashboard-filter")?.addEventListener("click", clearDashboardFilters);
    document.querySelector("#dashboard-error-filter")?.addEventListener("click", () => {
        dashboardFilter = "ERROR";
        dashboardPage = 1;
        syncDashboardFilters();
        renderDashboardResults();
        document.querySelector(".current-run-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });

    syncDashboardFilters();
}


function setReportAvailable(available) {
    const button = document.querySelector(
        "#download-report-button"
    );

    if (!button) {
        return;
    }

    button.disabled = !available;
}


async function downloadReport() {
    const button = document.querySelector(
        "#download-report-button"
    );

    if (!button) {
        return;
    }

    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "Preparing...";

    try {
        const response = await fetch(
            "/api/dry-run/report",
            {
                cache: "no-store",
            }
        );

        if (!response.ok) {
            throw new Error(
                `HTTP ${response.status}`
            );
        }

        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");

        link.href = url;
        link.download =
            "magazine_sorter_dry_run_report.txt";

        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);

    } catch (error) {
        console.error(
            "Report download failed:",
            error
        );

        alert(
            "Could not download the dry-run report."
        );

    } finally {
        button.disabled = false;
        button.textContent = originalText;
    }
}


async function loadReviewQueue() {
    const container =
        document.querySelector(
            "#review-list"
        );

    if (!container) {
        return;
    }

    try {
        const response = await fetch(
            "/api/review"
        );

        if (!response.ok) {
            throw new Error(
                `HTTP ${response.status}`
            );
        }

        const data = await response.json();

        document.querySelector(
            "#review-count"
        ).textContent = data.count;

        if (!data.items.length) {
            container.innerHTML = `
                <div class="review-no-items">
                    No files need review.
                </div>
            `;

            return;
        }

        container.innerHTML = `
            <div class="review-bulk-toolbar">
                <div>
                    <strong id="review-selection-info">0 selected</strong>
                    <span>of ${data.items.length}</span>
                </div>
                <div class="review-bulk-actions">
                    <button class="button secondary button-small" id="review-select-all">Select all</button>
                    <button class="button secondary button-small" id="review-clear-selection">Clear</button>
                    <button class="button secondary button-small" id="review-ocr-selected" disabled>Run OCR selected</button>
                    <button class="button primary button-small" id="review-approve-selected" disabled>Approve selected</button>
                    <button class="button secondary button-small" id="review-ignore-selected" disabled>Ignore selected</button>
                </div>
            </div>
            <div class="review-list">
                ${data.items.map((item) => {
                    return `
                        <div class="review-item-row">
                            <input class="review-file-checkbox" type="checkbox" data-review-filename="${escapeHtml(item.filename)}" data-review-extension="${escapeHtml(item.extension || "")}">
                            <button
                                class="review-item"
                                data-review-index="${item.index}"
                            >
                                <div class="review-item-status">REVIEW</div>
                                <div class="review-item-name">${escapeHtml(item.filename)}</div>
                                <div class="review-item-meta">${escapeHtml(item.publication || "Publication unknown")}</div>
                            </button>
                        </div>
                    `;
                }).join("")}
            </div>
        `;

        const checkboxes = Array.from(container.querySelectorAll(".review-file-checkbox"));
        const selectionInfo = container.querySelector("#review-selection-info");
        const approveButton = container.querySelector("#review-approve-selected");
        const ignoreButton = container.querySelector("#review-ignore-selected");
        const ocrButton = container.querySelector("#review-ocr-selected");
        const updateBulkState = () => {
            const selected = checkboxes.filter((box) => box.checked);
            const pdfSelected = selected.some((box) => (box.dataset.reviewExtension || "").toLowerCase() === ".pdf");
            if (selectionInfo) selectionInfo.textContent = `${selected.length} selected`;
            if (approveButton) approveButton.disabled = selected.length === 0;
            if (ignoreButton) ignoreButton.disabled = selected.length === 0;
            if (ocrButton) ocrButton.disabled = !pdfSelected;
        };

        checkboxes.forEach((box) => {
            box.addEventListener("click", (event) => event.stopPropagation());
            box.addEventListener("change", updateBulkState);
        });

        container.querySelector("#review-select-all")?.addEventListener("click", () => {
            checkboxes.forEach((box) => { box.checked = true; });
            updateBulkState();
        });

        container.querySelector("#review-clear-selection")?.addEventListener("click", () => {
            checkboxes.forEach((box) => { box.checked = false; });
            updateBulkState();
        });

        ocrButton?.addEventListener("click", async () => {
            const selected = checkboxes.filter((box) => box.checked);
            const pdfSelected = selected.filter((box) => (box.dataset.reviewExtension || "").toLowerCase() === ".pdf");
            if (!pdfSelected.length) return;
            if (!window.confirm(`Run OCR on ${pdfSelected.length} selected PDF${pdfSelected.length === 1 ? "" : "s"}?`)) return;

            ocrButton.disabled = true;
            ignoreButton.disabled = true;
            const originalText = ocrButton.textContent;
            let completed = 0;
            let failed = 0;
            try {
                for (const box of pdfSelected) {
                    ocrButton.textContent = `OCR ${completed + 1}/${pdfSelected.length}...`;
                    const index = checkboxes.indexOf(box);
                    const response = await fetch(`/api/review/${index}/ocr`, { method: "POST" });
                    if (!response.ok) {
                        failed += 1;
                    } else {
                        completed += 1;
                    }
                }
                window.alert(failed ? `OCR completed for ${completed} file${completed === 1 ? "" : "s"}; ${failed} failed.` : `OCR completed for ${completed} file${completed === 1 ? "" : "s"}.`);
            } catch (error) {
                console.error("Bulk OCR failed:", error);
                window.alert(`Bulk OCR stopped: ${error.message}`);
            } finally {
                ocrButton.textContent = originalText;
                updateBulkState();
            }
        });

        approveButton?.addEventListener("click", async () => {
            const selected = checkboxes.filter((box) => box.checked).map((box) => box.dataset.reviewFilename);
            if (!selected.length) return;
            if (!window.confirm(`Approve ${selected.length} selected file${selected.length === 1 ? "" : "s"}? Files will remain in place until Apply Changes.`)) return;

            approveButton.disabled = true;
            approveButton.textContent = "Approving...";
            try {
                const response = await fetch("/api/review/bulk-approve", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ filenames: selected }),
                });
                const result = await response.json();
                if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);

                let message = result.message || "Selected files processed.";
                if (result.skipped?.length) {
                    const skipped = result.skipped.map((item) => `${item.filename}: ${item.reason}`).join("\n");
                    message += `\n\nSkipped:\n${skipped}`;
                }
                window.alert(message);
                await loadReviewQueue();
                const detail = document.querySelector("#review-detail");
                if (detail) {
                    detail.innerHTML = `
                        <div class="review-empty">
                            <div class="empty-icon">◉</div>
                            <h2>Select a file</h2>
                            <p>Choose a file from the review queue to inspect it.</p>
                        </div>
                    `;
                }
            } catch (error) {
                window.alert(`Could not approve selected files: ${error.message}`);
                approveButton.disabled = false;
            } finally {
                approveButton.textContent = "Approve selected";
                updateBulkState();
            }
        });

        ignoreButton?.addEventListener("click", async () => {
            const selected = checkboxes.filter((box) => box.checked).map((box) => box.dataset.reviewFilename);
            if (!selected.length) return;
            if (!window.confirm(`Ignore ${selected.length} selected file${selected.length === 1 ? "" : "s"}? They will remain untouched and will not be moved.`)) return;

            ignoreButton.disabled = true;
            ignoreButton.textContent = "Ignoring...";
            try {
                const response = await fetch("/api/review/bulk-ignore", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ filenames: selected }),
                });
                const result = await response.json();
                if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
                window.alert(result.message || "Selected files ignored.");
                await loadReviewQueue();
                const detail = document.querySelector("#review-detail");
                if (detail) {
                    detail.innerHTML = `
                        <div class="review-empty">
                            <div class="empty-icon">◉</div>
                            <h2>Select a file</h2>
                            <p>Choose a file from the review queue to inspect it.</p>
                        </div>
                    `;
                }
            } catch (error) {
                window.alert(`Could not ignore selected files: ${error.message}`);
                ignoreButton.disabled = false;
                ignoreButton.textContent = "Ignore selected";
            }
        });

        container.querySelectorAll(".review-item").forEach((button) => {
            button.addEventListener("click", () => {
                const index = Number(button.dataset.reviewIndex);
                selectReviewItem(index);
            });
        });

    } catch (error) {
        console.error(
            "Review queue failed:",
            error
        );

        container.innerHTML = `
            <div class="review-error">
                Could not load review queue.
            </div>
        `;
    }
}


async function selectReviewItem(index) {
    document
        .querySelectorAll(".review-item")
        .forEach((item) => {
            item.classList.remove("selected");
        });

    const selected =
        document.querySelector(
            `[data-review-index="${index}"]`
        );

    if (selected) {
        selected.classList.add("selected");
    }

    const detail =
        document.querySelector(
            "#review-detail"
        );

    if (!detail) {
        return;
    }

    detail.innerHTML = `
        <div class="review-loading">
            <div class="spinner"></div>
            Loading file...
        </div>
    `;

    try {
        const response = await fetch(
            `/api/review/${index}`
        );

        if (!response.ok) {
            throw new Error(
                `HTTP ${response.status}`
            );
        }

        const item =
            await response.json();

        renderReviewDetail(
            index,
            item
        );

    } catch (error) {
        console.error(
            "Review item failed:",
            error
        );

        detail.innerHTML = `
            <div class="review-error">
                Could not load this review item.
            </div>
        `;
    }
}


function renderReviewDetail(index, item) {
    const detail = document.querySelector("#review-detail");
    if (!detail) return;

    const isPdf = item.extension.toLowerCase() === ".pdf";
    const publications = item.publications || [];
    const currentPublication = item.publication || "";
    const currentProfile = item.publication_profile || null;
    const initialMetadata = item.metadata || {};
    const knownPublication = publications.some(
        (publication) => publication.name === currentPublication
    );
    const CUSTOM_PUBLICATION = "__custom__";
    const specialCandidate = String(item.reason || "").toUpperCase().startsWith("SPECIAL_CANDIDATE");

    detail.innerHTML = `
        <div class="review-detail-header">
            <div>
                <div class="review-detail-label">REVIEW</div>
                <h2 title="${escapeHtml(item.filename)}">${escapeHtml(item.filename)}</h2>
            </div>
            <div class="review-header-actions">
                <button class="button secondary" id="learn-review-button">Learn publication</button>
                <button class="button primary" id="run-ocr-button" ${isPdf ? "" : "disabled"}>Run OCR</button>
            </div>
        </div>

        <div class="review-workspace">
            <div class="pdf-viewer">
                ${isPdf ? `
                    <iframe src="${item.pdf_url}#zoom=page-width" title="PDF preview"></iframe>
                ` : `
                    <div class="pdf-not-supported">
                        <div class="empty-icon">◫</div>
                        <h3>Preview not available</h3>
                        <p>Browser preview is currently available for PDF files.</p>
                    </div>
                `}
            </div>

            <div class="review-info">
                <div class="info-section">
                    <div class="info-label">WHY IT NEEDS REVIEW</div>
                    <div class="reason-box">${escapeHtml(item.reason || "No reason available")}</div>
                </div>

                <div class="manual-decision-card">
                    <div class="manual-decision-heading">
                        <div>
                            <div class="info-label">MANUAL DECISION</div>
                            <h3>Tell Magazine Sorter what this file is</h3>
                        </div>
                    </div>

                    <div class="form-field">
                        <label for="review-decision-type">Classification</label>
                        <select id="review-decision-type">
                            <option value="ISSUE" ${specialCandidate ? "" : "selected"}>Normal issue</option>
                            <option value="SPECIAL" ${specialCandidate ? "selected" : ""}>Special</option>
                            <option value="STANDALONE">Standalone</option>
                        </select>
                        <div class="publication-type-hint">Specials stay in the publication folder. Standalone files use Komga's <code>_oneshots</code> directory convention.</div>
                    </div>

                    <div id="review-publication-fields">
                        <div class="form-field">
                            <label for="review-publication">Publication</label>
                            <select id="review-publication">
                                <option value="">Select known publication...</option>
                                ${publications.map((publication) => `
                                    <option value="${escapeHtml(publication.name)}" ${publication.name === currentPublication && knownPublication ? "selected" : ""}>
                                        ${escapeHtml(publication.name)}
                                    </option>
                                `).join("")}
                                <option value="${CUSTOM_PUBLICATION}" ${!knownPublication && currentPublication ? "selected" : ""}>Enter publication manually...</option>
                            </select>
                        </div>

                        <div id="review-custom-publication" class="form-field review-custom-publication ${knownPublication || !currentPublication ? "hidden" : ""}">
                            <label for="review-custom-publication-input">Publication name</label>
                            <input id="review-custom-publication-input" type="text" value="${!knownPublication ? escapeHtml(currentPublication) : ""}" placeholder="e.g. My Magazine">
                        </div>

                        <div id="review-publication-type" class="publication-type-hint"></div>
                        <div id="review-metadata-fields" class="review-metadata-fields"></div>
                    </div>

                    <div id="review-special-fields" class="review-special-fields hidden">
                        <div class="form-field">
                            <label for="review-special-title">Special title</label>
                            <input id="review-special-title" type="text" placeholder="e.g. Christmas Magazine">
                        </div>
                        <div class="form-field">
                            <label for="review-special-year">Year <span class="field-optional">optional</span></label>
                            <input id="review-special-year" type="number" min="1900" max="2100" value="${initialMetadata.year ?? ""}" placeholder="e.g. 2025">
                        </div>
                    </div>

                    <div id="review-standalone-fields" class="review-standalone-fields hidden">
                        <div class="form-field">
                            <label for="review-standalone-filename">Filename</label>
                            <input id="review-standalone-filename" type="text" value="${escapeHtml(item.filename)}" placeholder="Standalone filename">
                            <div class="publication-type-hint">The filename is preserved by default. You can clean it up manually before applying.</div>
                        </div>
                    </div>

                    <div id="review-destination" class="proposed-destination review-destination-preview">
                        <div class="info-label">Destination preview</div>
                        <code>Complete the classification</code>
                    </div>

                    <div class="review-decision-actions">
                        <button class="button secondary" id="review-ignore-button">Ignore</button>
                        <button class="button primary" id="review-approve-button">Approve</button>
                    </div>

                    <div id="review-decision-message" class="form-message"></div>
                </div>

                <div class="info-section compact-review-info">
                    <div class="info-label">FILENAME METADATA</div>
                    <div class="metadata-box">${formatMetadata(initialMetadata)}</div>
                </div>

                <div id="ocr-result" class="ocr-result"></div>
            </div>
        </div>
    `;

    const publicationSelect = document.querySelector("#review-publication");
    const customPublicationInput = document.querySelector("#review-custom-publication-input");
    const customPublicationField = document.querySelector("#review-custom-publication");
    const decisionType = document.querySelector("#review-decision-type");
    const publicationFields = document.querySelector("#review-publication-fields");
    const specialFields = document.querySelector("#review-special-fields");
    const standaloneFields = document.querySelector("#review-standalone-fields");
    const approveButton = document.querySelector("#review-approve-button");
    const ocrButton = document.querySelector("#run-ocr-button");

    const getProfile = () => publications.find(
        (publication) => publication.name === publicationSelect?.value
    ) || null;

    const getPublicationName = () => {
        if (publicationSelect?.value === CUSTOM_PUBLICATION) {
            return customPublicationInput?.value.trim() || "";
        }
        return publicationSelect?.value || "";
    };

    const readVisibleMetadata = () => {
        const metadata = {};
        ["issue", "year", "month", "day", "week"].forEach((name) => {
            const input = document.querySelector(`#review-${name}`);
            if (input && input.value.trim() !== "") {
                const value = Number(input.value);
                if (Number.isFinite(value)) metadata[name] = value;
            }
        });
        return metadata;
    };

    const updateDecisionMode = () => {
        const mode = decisionType?.value || "ISSUE";
        const isSpecial = mode === "SPECIAL";
        const isStandalone = mode === "STANDALONE";

        publicationFields?.classList.toggle("hidden", isStandalone);
        specialFields?.classList.toggle("hidden", !isSpecial);
        standaloneFields?.classList.toggle("hidden", !isStandalone);

        // Special files only need the publication selector/name and Special metadata.
        // Normal-issue metadata (issue/month/date/week/year fields) is not relevant here.
        const metadataFields = document.querySelector("#review-metadata-fields");
        const publicationTypeHint = document.querySelector("#review-publication-type");
        metadataFields?.classList.toggle("hidden", isSpecial || isStandalone);
        publicationTypeHint?.classList.toggle("hidden", isSpecial || isStandalone);

        if (approveButton) {
            approveButton.textContent = isSpecial ? "Classify as Special" : isStandalone ? "Keep as Standalone" : "Approve";
        }

        updateReviewDestination();
    };

    const renderFields = () => {
        const profile = getProfile();
        const fields = document.querySelector("#review-metadata-fields");
        const typeHint = document.querySelector("#review-publication-type");
        if (!fields || !typeHint) return;

        const previous = readVisibleMetadata();
        const existing = { ...initialMetadata, ...previous };
        const isCustom = publicationSelect?.value === CUSTOM_PUBLICATION;

        if (customPublicationField) {
            customPublicationField.classList.toggle("hidden", !isCustom);
        }

        if (isCustom) {
            typeHint.innerHTML = "Publication type: <strong>Manual</strong> — choose the metadata fields that apply.";
        } else if (!profile) {
            typeHint.textContent = "Select a known publication to see its metadata type.";
            fields.innerHTML = `
                <div class="review-metadata-empty">
                    Choose a known publication, or enter one manually.
                </div>
            `;
            updateReviewDestination();
            return;
        } else {
            typeHint.innerHTML = `Publication type: <strong>${escapeHtml(profile.type_label)}</strong>`;
        }

        const input = (name, label, value, min, max, placeholder = "") => `
            <div class="form-field review-metadata-field">
                <label for="review-${name}">${label}</label>
                <input id="review-${name}" type="number" min="${min}" max="${max}" value="${value ?? ""}" placeholder="${placeholder}">
            </div>
        `;

        let html = `<div class="review-metadata-grid">`;
        if (isCustom) {
            html += input("issue", "Issue", existing.issue, 1, 999, "Issue number");
            html += input("year", "Year", existing.year, 1900, 2100, "e.g. 2025");
            html += input("month", "Month", existing.month, 1, 12, "1–12");
            html += input("day", "Day", existing.day, 1, 31, "1–31");
            html += input("week", "Week", existing.week, 1, 53, "Week number");
        } else if (profile.type === "issue") {
            html += input("issue", "Issue", existing.issue, 1, 999, "Issue number");
        } else if (profile.type === "month") {
            html += input("month", "Month", existing.month, 1, 12, "1–12");
        } else if (profile.type === "date") {
            html += input("day", "Day", existing.day, 1, 31, "1–31");
            html += input("month", "Month", existing.month, 1, 12, "1–12");
        } else if (profile.type === "week") {
            html += input("week", "Week", existing.week, 1, 53, "Week number");
        }
        if (isCustom || profile?.include_year) {
            if (!isCustom && profile?.include_year) {
                html += input("year", "Year", existing.year, 1900, 2100, "e.g. 2025");
            }
        }
        html += `</div>`;
        fields.innerHTML = html;

        fields.querySelectorAll("input").forEach((inputElement) => {
            inputElement.addEventListener("input", updateReviewDestination);
        });
        customPublicationInput?.addEventListener("input", updateReviewDestination);
        updateReviewDestination();
    };

    const collectMetadata = () => readVisibleMetadata();

    window.__reviewCollectMetadata = collectMetadata;

    function updateReviewDestination() {
        const mode = decisionType?.value || "ISSUE";
        const profile = getProfile();
        const publication = getPublicationName();
        const destination = document.querySelector("#review-destination code");
        if (!destination) return;

        if (mode === "STANDALONE") {
            const filename = document.querySelector("#review-standalone-filename")?.value.trim() || "";
            destination.textContent = filename ? `_oneshots/${filename}` : "Enter a standalone filename";
            return;
        }

        if (!publication) {
            destination.textContent = "Enter a publication name";
            return;
        }

        if (mode === "SPECIAL") {
            const title = document.querySelector("#review-special-title")?.value.trim() || "";
            const year = document.querySelector("#review-special-year")?.value.trim() || "";
            if (!title) {
                destination.textContent = "Enter a Special title";
                return;
            }
            const extension = item.extension || ".pdf";
            const filename = `${publication} - ${year ? year + " - " : ""}${title}${extension}`;
            destination.textContent = `${publication}/${filename}`;
            return;
        }

        const metadata = collectMetadata();
        let filename = publication;
        let hasCore = false;

        if (profile?.type === "issue") {
            hasCore = metadata.issue != null;
            if (hasCore) filename += ` - ${metadata.year ? metadata.year + " - " : ""}Nr ${String(metadata.issue).padStart(2, "0")}.pdf`;
        } else if (profile?.type === "month") {
            hasCore = metadata.month != null;
            if (hasCore) filename += ` - ${metadata.year ? metadata.year + "-" : ""}${String(metadata.month).padStart(2, "0")}.pdf`;
        } else if (profile?.type === "date") {
            hasCore = metadata.day != null && metadata.month != null;
            if (hasCore) filename += ` - ${metadata.year ? metadata.year + "-" : ""}${String(metadata.month).padStart(2, "0")}-${String(metadata.day).padStart(2, "0")}.pdf`;
        } else if (profile?.type === "week") {
            hasCore = metadata.week != null;
            if (hasCore) filename += ` - ${metadata.year ? metadata.year + " - " : ""}Uge ${String(metadata.week).padStart(2, "0")}.pdf`;
        } else {
            if (metadata.year != null && metadata.month != null && metadata.day != null) {
                hasCore = true;
                filename += ` - ${metadata.year}-${String(metadata.month).padStart(2, "0")}-${String(metadata.day).padStart(2, "0")}.pdf`;
            } else if (metadata.year != null && metadata.month != null && metadata.issue == null) {
                hasCore = true;
                filename += ` - ${metadata.year}-${String(metadata.month).padStart(2, "0")}.pdf`;
            } else if (metadata.year != null && metadata.week != null) {
                hasCore = true;
                filename += ` - ${metadata.year} - Uge ${String(metadata.week).padStart(2, "0")}.pdf`;
            } else if (metadata.issue != null) {
                hasCore = true;
                filename += ` - ${metadata.year ? metadata.year + " - " : ""}Nr ${String(metadata.issue).padStart(2, "0")}.pdf`;
            }
        }

        if (!hasCore || (profile?.include_year && metadata.year == null)) {
            destination.textContent = "Complete the required metadata";
            return;
        }

        destination.textContent = `${publication}/${filename}`;
    }

    publicationSelect?.addEventListener("change", renderFields);
    customPublicationInput?.addEventListener("input", updateReviewDestination);
    decisionType?.addEventListener("change", updateDecisionMode);
    document.querySelector("#review-special-title")?.addEventListener("input", updateReviewDestination);
    document.querySelector("#review-special-year")?.addEventListener("input", updateReviewDestination);
    document.querySelector("#review-standalone-filename")?.addEventListener("input", updateReviewDestination);
    renderFields();
    updateDecisionMode();

    if (ocrButton && isPdf) {
        ocrButton.addEventListener("click", () => runReviewOCR(index));
    }

    document.querySelector("#review-approve-button")?.addEventListener(
        "click", () => approveReview(index, collectMetadata, getProfile, getPublicationName)
    );
    document.querySelector("#review-ignore-button")?.addEventListener(
        "click", () => ignoreReview(index)
    );

    document.querySelector("#learn-review-button")?.addEventListener("click", () => {
        const name = getPublicationName();
        const params = new URLSearchParams({ name, filename: item.filename });
        window.location.href = `/publications?${params.toString()}`;
    });
}


async function approveReview(index, collectMetadata, getProfile, getPublicationName) {
    const publicationSelect = document.querySelector("#review-publication");
    const message = document.querySelector("#review-decision-message");
    const button = document.querySelector("#review-approve-button");
    const decisionType = document.querySelector("#review-decision-type")?.value || "ISSUE";
    const publication = getPublicationName ? getPublicationName() : (publicationSelect?.value || "");
    const metadata = collectMetadata();

    button.disabled = true;
    message.className = "form-message";
    message.textContent = decisionType === "SPECIAL" ? "Classifying as Special..." : decisionType === "STANDALONE" ? "Keeping as Standalone..." : "Approving...";

    try {
        let response;
        if (decisionType === "SPECIAL") {
            const title = document.querySelector("#review-special-title")?.value.trim() || "";
            const yearValue = document.querySelector("#review-special-year")?.value.trim() || "";
            response = await fetch(`/api/review/${index}/classify`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    action: "SPECIAL",
                    publication,
                    title,
                    year: yearValue || null,
                }),
            });
        } else if (decisionType === "STANDALONE") {
            const filename = document.querySelector("#review-standalone-filename")?.value.trim() || "";
            response = await fetch(`/api/review/${index}/classify`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    action: "STANDALONE",
                    filename,
                }),
            });
        } else {
            if (!publication) {
                throw new Error("Enter or select a publication first.");
            }
            response = await fetch(`/api/review/${index}/approve`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ publication, metadata }),
            });
        }

        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);

        message.className = "form-message success";
        message.textContent = data.message || "Decision saved.";
        await loadReviewQueue();
    } catch (error) {
        message.className = "form-message error";
        message.textContent = error.message;
        button.disabled = false;
    }
}



async function ignoreReview(index) {
    const button = document.querySelector("#review-ignore-button");
    const message = document.querySelector("#review-decision-message");
    if (!button || !message) return;

    button.disabled = true;
    message.className = "form-message";
    message.textContent = "Ignoring...";

    try {
        const response = await fetch(`/api/review/${index}/ignore`, { method: "POST" });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        message.className = "form-message success";
        message.textContent = "Ignored and saved to run history.";
        await loadReviewQueue();
    } catch (error) {
        message.className = "form-message error";
        message.textContent = error.message;
        button.disabled = false;
    }
}


async function runReviewOCR(index) {
    const button =
        document.querySelector(
            "#run-ocr-button"
        );

    const resultContainer =
        document.querySelector(
            "#ocr-result"
        );

    if (!button || !resultContainer) {
        return;
    }

    button.disabled = true;
    button.textContent = "Running OCR...";

    resultContainer.innerHTML = `
        <div class="ocr-running">
            <div class="spinner"></div>

            <strong>
                OCR is running
            </strong>

            <span>
                The OCR engine is scanning the selected pages.
            </span>
        </div>
    `;

    try {
        const response = await fetch(
            `/api/review/${index}/ocr`,
            {
                method: "POST",
            }
        );

        if (!response.ok) {
            const error =
                await response.json();

            throw new Error(
                error.detail ||
                `HTTP ${response.status}`
            );
        }

        const data =
            await response.json();

        renderOCRResult(data);

        const useOCRButton = document.querySelector("#use-ocr-result-button");
        useOCRButton?.addEventListener("click", () => {
            applyOCRResultToReviewForm(data);
        });

    } catch (error) {
        console.error(
            "OCR failed:",
            error
        );

        resultContainer.innerHTML = `
            <div class="ocr-error">

                <strong>
                    OCR failed
                </strong>

                <p>
                    ${escapeHtml(
                        error.message
                    )}
                </p>

            </div>
        `;

    } finally {
        button.disabled = false;
        button.textContent = "Run OCR";
    }
}


function applyOCRResultToReviewForm(data) {
    const publicationSelect = document.querySelector("#review-publication");
    const customPublicationInput = document.querySelector("#review-custom-publication-input");

    if (!publicationSelect) return;

    const decisionType = document.querySelector("#review-decision-type");
    if (decisionType) {
        decisionType.value = "ISSUE";
        decisionType.dispatchEvent(new Event("change"));
    }

    const publication = String(data.publication || "").trim();
    const publications = Array.from(publicationSelect.options).map((option) => option.value);

    if (publication && publications.includes(publication)) {
        publicationSelect.value = publication;
    } else if (publication) {
        publicationSelect.value = "__custom__";
        if (customPublicationInput) customPublicationInput.value = publication;
    }

    publicationSelect.dispatchEvent(new Event("change"));

    const metadata = data.merged_metadata || {};
    ["issue", "year", "month", "day", "week"].forEach((name) => {
        const input = document.querySelector(`#review-${name}`);
        if (!input || metadata[name] == null) return;
        input.value = metadata[name];
        input.dispatchEvent(new Event("input", { bubbles: true }));
    });

    const destination = document.querySelector("#review-destination code");
    if (destination) {
        destination.scrollIntoView({ behavior: "smooth", block: "center" });
    }

    const message = document.querySelector("#review-decision-message");
    if (message) {
        message.className = "form-message success";
        message.textContent = "OCR result copied into the manual decision. Review it before approving.";
    }
}


function renderOCRResult(data) {
    const container =
        document.querySelector(
            "#ocr-result"
        );

    if (!container) {
        return;
    }

    const metadata =
        data.merged_metadata || {};

    const reasons =
        data.reasons || [];

    const pages =
        data.ocr_pages || [];

    const actionClass =
        String(
            data.action || "REVIEW"
        ).toLowerCase();

    container.innerHTML = `
        <div class="ocr-section">

            <div class="ocr-title-row">

                <div>
                    <div class="info-label">
                        OCR RESULT
                    </div>

                    <div class="ocr-action ${actionClass}">
                        ${escapeHtml(
                            data.action
                        )}
                    </div>
                </div>

                <div class="confidence">

                    <span>
                        Confidence
                    </span>

                    <strong>
                        ${escapeHtml(
                            data.score
                        )}%
                    </strong>

                </div>

            </div>


            <div class="info-section">

                <div class="info-label">
                    OCR metadata
                </div>

                <div class="metadata-grid">

                    <div>
                        <span>Issue</span>
                        <strong>
                            ${displayValue(
                                data.ocr_metadata.issue
                            )}
                        </strong>
                    </div>

                    <div>
                        <span>Year</span>
                        <strong>
                            ${displayValue(
                                data.ocr_metadata.year
                            )}
                        </strong>
                    </div>

                </div>

            </div>


            <div class="info-section">

                <div class="info-label">
                    Merged metadata
                </div>

                <div class="metadata-grid">

                    <div>
                        <span>Issue</span>
                        <strong>
                            ${displayValue(
                                metadata.issue
                            )}
                        </strong>
                    </div>

                    <div>
                        <span>Year</span>
                        <strong>
                            ${displayValue(
                                metadata.year
                            )}
                        </strong>
                    </div>

                    <div>
                        <span>Month</span>
                        <strong>
                            ${displayValue(
                                metadata.month
                            )}
                        </strong>
                    </div>

                </div>

            </div>


            <div class="info-section">

                <div class="info-label">
                    OCR pages
                </div>

                <div class="page-tags">

                    ${
                        pages.length
                            ? pages
                                .map(
                                    (page) =>
                                        `<span>
                                            Page ${page.page}
                                        </span>`
                                )
                                .join("")
                            : "<span>None</span>"
                    }

                </div>

            </div>


            ${
                data.destination
                    ? `
                        <div class="proposed-destination">

                            <div class="info-label">
                                Proposed destination
                            </div>

                            <code>
                                ${escapeHtml(
                                    data.destination
                                )}
                            </code>

                            <button class="button secondary button-small" id="use-ocr-result-button" type="button">Use OCR result</button>

                        </div>
                    `
                    : ""
            }


            ${
                reasons.length
                    ? `
                        <div class="info-section">

                            <div class="info-label">
                                Why
                            </div>

                            <ul class="reason-list">

                                ${reasons
                                    .map(
                                        (reason) =>
                                            `<li>
                                                ${escapeHtml(
                                                    reason
                                                )}
                                            </li>`
                                    )
                                    .join("")}

                            </ul>

                        </div>
                    `
                    : ""
            }

        </div>
    `;
}


function formatMetadata(metadata) {
    if (!metadata) {
        return "None";
    }

    const values = [];

    if (metadata.issue !== null) {
        values.push(
            `Issue: ${metadata.issue}`
        );
    }

    if (metadata.year !== null) {
        values.push(
            `Year: ${metadata.year}`
        );
    }

    if (metadata.month !== null) {
        values.push(
            `Month: ${metadata.month}`
        );
    }

    if (metadata.day !== null) {
        values.push(
            `Day: ${metadata.day}`
        );
    }

    if (metadata.week !== null) {
        values.push(
            `Week: ${metadata.week}`
        );
    }

    return values.length
        ? values.join("<br>")
        : "None";
}


function displayValue(value) {
    if (
        value === null ||
        value === undefined ||
        value === ""
    ) {
        return "—";
    }

    return escapeHtml(value);
}


function escapeHtml(value) {
    if (
        value === null ||
        value === undefined
    ) {
        return "";
    }

    return String(value)
        .replaceAll(
            "&",
            "&amp;"
        )
        .replaceAll(
            "<",
            "&lt;"
        )
        .replaceAll(
            ">",
            "&gt;"
        )
        .replaceAll(
            '"',
            "&quot;"
        )
        .replaceAll(
            "'",
            "&#039;"
        );
}


document.addEventListener(
    "DOMContentLoaded",
    () => {
        const reportButton = document.querySelector(
            "#download-report-button"
        );

        if (reportButton) {
            reportButton.addEventListener(
                "click",
                downloadReport
            );
        }

        document
            .querySelectorAll(
                ".dry-run-button"
            )
            .forEach((button) => {
                button.addEventListener(
                    "click",
                    runDryRun
                );
            });

        initializeRunStatus();
        bindDashboardControls();
        startGlobalRunStatusPolling();
        loadReviewQueue();
        initializeHistoryPage();
        initializePublicationsPage();
        initializeApplyPage();
        initializeApplyNavigation();
    }
);

/* -------------------------------------------------------------------------
   Run history
   ------------------------------------------------------------------------- */

let historyItems = [];
let selectedHistoryRunId = null;
let currentHistoryRecord = null;

function formatHistoryDate(value) {
    if (!value) return "—";
    try {
        return new Intl.DateTimeFormat("en-GB", {
            dateStyle: "medium",
            timeStyle: "short",
        }).format(new Date(value));
    } catch (error) {
        return value;
    }
}

function historyStatusClass(status) {
    const value = String(status || "").toLowerCase();
    if (value === "finished") return "finished";
    if (value === "stopped") return "stopped";
    if (value === "running") return "running";
    return "other";
}

async function loadHistory() {
    const container = document.querySelector("#history-list");
    if (!container) return;

    try {
        const response = await fetch("/api/history", { cache: "no-store" });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);

        historyItems = data.items || [];
        const count = document.querySelector("#history-count");
        if (count) count.textContent = historyItems.length;

        if (!historyItems.length) {
            container.innerHTML = `<div class="empty-state"><div class="empty-icon">◷</div><strong>No runs yet</strong><span>Completed and stopped runs will appear here.</span></div>`;
            return;
        }

        container.innerHTML = historyItems.map((run) => {
            const stats = run.statistics || {};
            return `
                <button class="history-row" data-run-id="${escapeHtml(run.run_id)}">
                    <div class="history-row-main">
                        <div class="history-row-title">
                            <strong>${escapeHtml(formatHistoryDate(run.created_at))}</strong>
                            <span class="history-status ${historyStatusClass(run.status)}">${escapeHtml(run.status || "unknown")}</span>
                            ${run.run_type === "history_rerun" ? `<span class="history-rerun-badge">RE-RUN</span>` : ""}
                        </div>
                        <div class="history-row-path" title="${escapeHtml(run.input_folder || "")}">${escapeHtml(run.input_folder || "—")}</div>
                    </div>
                    <div class="history-row-stats">
                        <span><strong>${stats.files ?? 0}</strong> files</span>
                        <span><strong>${stats.auto ?? 0}</strong> auto</span>
                        <span><strong>${stats.review ?? 0}</strong> review</span>
                        <span><strong>${stats.ignore ?? 0}</strong> ignore</span>
                        <span><strong>${stats.collisions ?? 0}</strong> collisions</span>
                    </div>
                </button>`;
        }).join("");

        container.querySelectorAll(".history-row").forEach((row) => {
            row.addEventListener("click", () => loadHistoryRun(row.dataset.runId));
        });
    } catch (error) {
        console.error("Could not load history:", error);
        container.innerHTML = `<div class="empty-state error-suggestion"><strong>Could not load history.</strong><span>${escapeHtml(error.message)}</span></div>`;
    }
}

function historyRecordItems(run) {
    const source = run.items || {};
    const uiStates = run._ui_states || {};
    return Object.entries(source).map(([fingerprint, entry]) => {
        const item = entry && entry.item;
        if (!item) return null;
        const result = item.result || {};
        const path = fingerprint.includes("|") ? fingerprint.split("|")[0] : "";
        const state = uiStates[fingerprint] || {};
        const operationStatus = state.operation_status || "";
        const operationLabel = operationStatus === "UNDONE" ? "Undone"
            : operationStatus === "UNDO_BLOCKED" ? "Undo blocked"
            : operationStatus === "UNDO_ERROR" ? "Undo error"
            : state.undoable ? "Undo available"
            : state.rerunnable ? "Available"
            : "Not available";
        const operationClass = operationStatus === "UNDONE" ? "unavailable"
            : operationStatus === "UNDO_BLOCKED" || operationStatus === "UNDO_ERROR" ? "available undo-warning"
            : state.undoable ? "available undo-safe"
            : state.rerunnable ? "available"
            : "unavailable";
        return {
            fingerprint,
            filename: item.filename || path || fingerprint,
            status: result.status || "UNKNOWN",
            publication: result.publication || "",
            destination: item.destination || result.reason || "—",
            source: item.source || "—",
            available: Boolean(state.rerunnable),
            undoable: Boolean(state.undoable),
            operationStatus,
            operationLabel,
            operationClass,
            operationReason: state.operation_reason || "",
            sourcePath: path,
        };
    }).filter(Boolean).sort((a, b) => a.filename.localeCompare(b.filename, "en"));
}

function renderHistoryDetail(run) {
    const detail = document.querySelector("#history-detail");
    if (!detail) return;
    currentHistoryRecord = run;

    const stats = run.statistics || {};
    const items = historyRecordItems(run);
    const availableCount = items.filter((item) => item.available).length;

    const eventHtml = (run.events || []).slice().reverse().map((event) => `
        <div class="history-event">
            <span class="history-event-time">${escapeHtml(formatHistoryDate(event.timestamp))}</span>
            <strong>${escapeHtml(event.message || event.type || "Event")}</strong>
            <span>${escapeHtml(event.filename || event.reason || (event.restored != null ? `${event.restored} restored · ${event.blocked || 0} blocked · ${event.errors || 0} errors` : ""))}</span>
        </div>`).join("");

    detail.innerHTML = `
        <div class="history-summary-grid">
            <div><span>Files</span><strong>${stats.files ?? 0}</strong></div>
            <div><span>Processed</span><strong>${stats.processed ?? 0}</strong></div>
            <div><span>AUTO</span><strong>${stats.auto ?? 0}</strong></div>
            <div><span>REVIEW</span><strong>${stats.review ?? 0}</strong></div>
            <div><span>IGNORE</span><strong>${stats.ignore ?? 0}</strong></div>
            <div><span>ERROR</span><strong>${stats.errors ?? 0}</strong></div>
            <div><span>Collisions</span><strong>${stats.collisions ?? 0}</strong></div>
        </div>

        <div class="history-section">
            <div class="history-toolbar">
                <div>
                    <div class="info-label">Files in this run</div>
                    <div class="history-selection-info" id="history-selection-info">0 selected</div>
                </div>
                <div class="history-toolbar-controls">
                    <input id="history-file-search" class="history-search" type="search" placeholder="Search files...">
                    <select id="history-status-filter" class="history-filter">
                        <option value="ALL">All statuses</option>
                        <option value="AUTO">AUTO</option>
                        <option value="REVIEW">REVIEW</option>
                        <option value="IGNORE">IGNORE</option>
                        <option value="ERROR">ERROR</option>
                    </select>
                    <button class="button secondary button-small" id="history-select-visible">Select visible</button>
                    <button class="button secondary button-small" id="history-select-status">Select status</button>
                    <button class="button secondary button-small" id="history-clear-selection">Clear</button>
                    <button class="button primary button-small" id="history-rerun-selected" disabled>Re-run selected</button>
                    <button class="button secondary button-small" id="history-undo-selected" disabled>Undo selected</button>
                </div>
            </div>
            <div class="history-help">Re-run is available for files that still exist at their original source path. Undo is available only for files moved by Apply Changes with a rollback-safe integrity record. Both actions preserve this original History entry.</div>
            <div class="history-results" id="history-results-list">
                ${items.length ? items.map((item) => `
                    <label class="history-result-row history-selectable-row" data-fingerprint="${escapeHtml(item.fingerprint)}" data-status="${escapeHtml(item.status)}" data-filename="${escapeHtml(item.filename.toLowerCase())}">
                        <input class="history-file-checkbox" type="checkbox" data-fingerprint="${escapeHtml(item.fingerprint)}" ${item.available || item.undoable ? "" : "disabled"}>
                        <span class="history-result-status ${escapeHtml(String(item.status).toLowerCase())}">${escapeHtml(item.status)}</span>
                        <span class="history-result-file" title="${escapeHtml(item.filename)}">${escapeHtml(item.filename)}</span>
                        <span class="history-result-destination" title="${escapeHtml(item.destination)}">${escapeHtml(item.destination)}</span>
                        <span class="history-availability ${item.operationClass}" title="${escapeHtml(item.operationReason || "")}">${escapeHtml(item.operationLabel)}</span>
                    </label>`).join("") : `<div class="empty-state">No stored file results.</div>`}
            </div>
        </div>

        <div class="history-section">
            <div class="info-label">Activity</div>
            <div class="history-events">${eventHtml || `<div class="empty-state">No recorded events.</div>`}</div>
        </div>`;

    initializeHistorySelection();
}

function initializeHistorySelection() {
    const detail = document.querySelector("#history-detail");
    if (!detail) return;
    const rows = Array.from(detail.querySelectorAll(".history-selectable-row"));
    const checkboxes = Array.from(detail.querySelectorAll(".history-file-checkbox"));
    const itemsByFingerprint = new Map(historyRecordItems(currentHistoryRecord).map((item) => [item.fingerprint, item]));
    const search = detail.querySelector("#history-file-search");
    const filter = detail.querySelector("#history-status-filter");
    const info = detail.querySelector("#history-selection-info");
    const rerun = detail.querySelector("#history-rerun-selected");
    const undo = detail.querySelector("#history-undo-selected");

    const visibleRows = () => rows.filter((row) => !row.hidden);

    const applyFilter = () => {
        const term = (search?.value || "").trim().toLowerCase();
        const status = filter?.value || "ALL";
        rows.forEach((row) => {
            row.hidden = Boolean((term && !(row.dataset.filename || "").includes(term)) || (status !== "ALL" && row.dataset.status !== status));
        });
    };

    const update = () => {
        const selectedBoxes = checkboxes.filter((box) => box.checked && !box.disabled);
        const selected = selectedBoxes.length;
        const selectedItems = selectedBoxes.map((box) => itemsByFingerprint.get(box.dataset.fingerprint)).filter(Boolean);
        const rerunnable = selectedItems.filter((item) => item.available).length;
        const undoable = selectedItems.filter((item) => item.undoable).length;
        const visible = visibleRows();
        if (info) info.textContent = `${selected} selected · ${visible.length} visible`;
        if (rerun) rerun.disabled = rerunnable === 0;
        if (undo) undo.disabled = undoable === 0;
    };

    checkboxes.forEach((box) => box.addEventListener("change", update));
    search?.addEventListener("input", () => { applyFilter(); update(); });
    filter?.addEventListener("change", () => { applyFilter(); update(); });

    detail.querySelector("#history-select-visible")?.addEventListener("click", () => {
        visibleRows().forEach((row) => {
            const box = row.querySelector(".history-file-checkbox");
            if (box && !box.disabled) box.checked = true;
        });
        update();
    });

    detail.querySelector("#history-select-status")?.addEventListener("click", () => {
        const status = filter?.value || "ALL";
        if (status === "ALL") {
            window.alert("Choose a status in the filter first, then use Select status.");
            return;
        }
        rows.forEach((row) => {
            if (row.hidden) return;
            const box = row.querySelector(".history-file-checkbox");
            if (box && !box.disabled && row.dataset.status === status) box.checked = true;
        });
        update();
    });

    detail.querySelector("#history-clear-selection")?.addEventListener("click", () => {
        checkboxes.forEach((box) => { box.checked = false; });
        update();
    });

    rerun?.addEventListener("click", async () => {
        const selected = checkboxes.filter((box) => box.checked && !box.disabled).map((box) => box.dataset.fingerprint);
        if (!selected.length || !currentHistoryRecord) return;
        if (!window.confirm(`Re-run ${selected.length} selected file${selected.length === 1 ? "" : "s"}? The original History entry will be kept.`)) return;

        rerun.disabled = true;
        rerun.textContent = "Starting...";
        try {
            const response = await fetch(`/api/history/${encodeURIComponent(currentHistoryRecord.run_id)}/rerun`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ fingerprints: selected }),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
            const unavailable = data.unavailable || [];
            const message = unavailable.length
                ? `Re-run started for ${data.selected} file(s). ${unavailable.length} selected file(s) were unavailable and were skipped.`
                : `Re-run started for ${data.selected} file(s).`;
            window.alert(message);
            window.location.href = "/";
        } catch (error) {
            window.alert(`Could not start re-run: ${error.message}`);
            rerun.disabled = false;
            rerun.textContent = "Re-run selected";
        }
    });

    undo?.addEventListener("click", async () => {
        const selected = checkboxes
            .filter((box) => box.checked && !box.disabled)
            .map((box) => itemsByFingerprint.get(box.dataset.fingerprint))
            .filter((item) => item && item.undoable)
            .map((item) => item.fingerprint);

        if (!selected.length || !currentHistoryRecord) return;

        undo.disabled = true;
        undo.textContent = "Checking...";
        try {
            const planResponse = await fetch(
                `/api/history/${encodeURIComponent(currentHistoryRecord.run_id)}/undo-plan?fingerprints=${encodeURIComponent(selected.join(","))}`,
                { cache: "no-store" }
            );
            const plan = await planResponse.json();
            if (!planResponse.ok) throw new Error(plan.detail || `HTTP ${planResponse.status}`);

            const stats = plan.statistics || {};
            const readyItems = (plan.items || []).filter((item) => item.status === "READY");
            const blockedItems = (plan.items || []).filter((item) => item.status === "BLOCKED");
            if (!readyItems.length) {
                const reasons = blockedItems.map((item) => `• ${item.filename}: ${item.reason}`).join("\n");
                throw new Error(`No selected files are safe to undo.\n\n${reasons}`);
            }

            const readyCount = stats.ready || readyItems.length;
            const modal = document.createElement("div");
            modal.className = "history-undo-modal";
            modal.innerHTML = `
                <div class="history-undo-dialog" role="dialog" aria-modal="true" aria-labelledby="history-undo-title">
                    <div class="history-undo-header">
                        <div>
                            <div class="eyebrow">ROLLBACK PREFLIGHT</div>
                            <h3 id="history-undo-title">Undo ${readyCount} file${readyCount === 1 ? "" : "s"}?</h3>
                        </div>
                        <button type="button" class="history-undo-close" aria-label="Close">×</button>
                    </div>
                    <div class="history-undo-summary">
                        <div class="history-undo-count ready"><strong>${readyCount}</strong><span>Ready</span></div>
                        <div class="history-undo-count ${blockedItems.length ? "blocked" : "neutral"}"><strong>${blockedItems.length}</strong><span>Blocked</span></div>
                    </div>
                    <div class="history-undo-list">
                        ${readyItems.map((item) => `
                            <div class="history-undo-item ready">
                                <span class="history-undo-item-status">READY</span>
                                <span class="history-undo-item-name" title="${escapeHtml(item.filename)}">${escapeHtml(item.filename)}</span>
                                <span class="history-undo-item-path">${escapeHtml(item.destination)} → ${escapeHtml(item.source)}</span>
                            </div>`).join("")}
                        ${blockedItems.map((item) => `
                            <div class="history-undo-item blocked">
                                <span class="history-undo-item-status">BLOCKED</span>
                                <span class="history-undo-item-name" title="${escapeHtml(item.filename)}">${escapeHtml(item.filename)}</span>
                                <span class="history-undo-item-path">${escapeHtml(item.reason)}</span>
                            </div>`).join("")}
                    </div>
                    <div class="history-undo-warning">Only READY files will be restored. Blocked files remain untouched. Nothing will be overwritten.</div>
                    <div class="history-undo-actions">
                        <button type="button" class="button secondary" data-undo-cancel>Cancel</button>
                        <button type="button" class="button primary" data-undo-confirm>Undo ${readyCount} file${readyCount === 1 ? "" : "s"}</button>
                    </div>
                </div>`;
            document.body.appendChild(modal);

            const closeModal = () => {
                modal.remove();
                undo.disabled = false;
                undo.textContent = "Undo selected";
            };
            modal.querySelector(".history-undo-close")?.addEventListener("click", closeModal);
            modal.querySelector("[data-undo-cancel]")?.addEventListener("click", closeModal);
            modal.addEventListener("click", (event) => {
                if (event.target === modal) closeModal();
            });

            const confirmButton = modal.querySelector("[data-undo-confirm]");
            confirmButton?.addEventListener("click", async () => {
                confirmButton.disabled = true;
                modal.querySelector("[data-undo-cancel]")?.setAttribute("disabled", "disabled");
                modal.querySelector(".history-undo-close")?.setAttribute("disabled", "disabled");
                confirmButton.textContent = "Undoing...";
                undo.textContent = "Undoing...";
                try {
                    const response = await fetch(`/api/history/${encodeURIComponent(currentHistoryRecord.run_id)}/undo`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ fingerprints: selected }),
                    });
                    const data = await response.json();
                    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);

                    const restored = data.count || 0;
                    const blocked = (data.blocked || []).length;
                    const errors = (data.errors || []).length;
                    let message = `${restored} file${restored === 1 ? "" : "s"} restored.`;
                    if (blocked) message += ` ${blocked} blocked and left untouched.`;
                    if (errors) message += ` ${errors} failed.`;
                    modal.remove();
                    window.alert(message);
                    await loadHistoryRun(currentHistoryRecord.run_id);
                } catch (error) {
                    modal.remove();
                    window.alert(`Could not undo selected files: ${error.message}`);
                    undo.disabled = false;
                    undo.textContent = "Undo selected";
                }
            });
            confirmButton?.focus();
            const response = await fetch(`/api/history/${encodeURIComponent(currentHistoryRecord.run_id)}/undo`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ fingerprints: selected }),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);

            const restored = data.count || 0;
            const blocked = (data.blocked || []).length;
            const errors = (data.errors || []).length;
            let message = `${restored} file${restored === 1 ? "" : "s"} restored.`;
            if (blocked) message += ` ${blocked} blocked and left untouched.`;
            if (errors) message += ` ${errors} failed.`;
            window.alert(message);
            await loadHistoryRun(currentHistoryRecord.run_id);
        } catch (error) {
            window.alert(`Could not undo selected files: ${error.message}`);
            if (undo) {
                undo.disabled = false;
                undo.textContent = "Undo selected";
            }
        }
    });

    applyFilter();
    update();
}

async function loadHistoryRun(runId) {
    const detailPanel = document.querySelector("#history-detail-panel");
    const detail = document.querySelector("#history-detail");
    if (!detailPanel || !detail) return;

    selectedHistoryRunId = runId;
    document.querySelectorAll(".history-row").forEach((row) => row.classList.toggle("selected", row.dataset.runId === runId));
    detailPanel.hidden = false;
    detail.innerHTML = `<div class="empty-state">Loading run details...</div>`;

    try {
        const response = await fetch(`/api/history/${encodeURIComponent(runId)}`, { cache: "no-store" });
        const run = await response.json();
        if (!response.ok) throw new Error(run.detail || `HTTP ${response.status}`);

        document.querySelector("#history-detail-title").textContent = `${formatHistoryDate(run.created_at)} · ${run.status || "unknown"}`;
        document.querySelector("#history-detail-subtitle").textContent = run.input_folder || "";

        // Keep the summary row consistent with the immutable run snapshot.
        const selectedRow = document.querySelector(`.history-row[data-run-id="${CSS.escape(runId)}"]`);
        if (selectedRow) {
            const stats = run.statistics || {};
            const summary = selectedRow.querySelector(".history-row-stats");
            if (summary) {
                summary.innerHTML = `
                    <span><strong>${stats.files ?? 0}</strong> files</span>
                    <span><strong>${stats.auto ?? 0}</strong> auto</span>
                    <span><strong>${stats.review ?? 0}</strong> review</span>
                    <span><strong>${stats.ignore ?? 0}</strong> ignore</span>
                    <span><strong>${stats.collisions ?? 0}</strong> collisions</span>`;
            }
        }
        const reportButton = document.querySelector("#history-report-button");
        if (reportButton) reportButton.onclick = () => window.location.href = `/api/history/${encodeURIComponent(runId)}/report`;
        renderHistoryDetail(run);
        detailPanel.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
        detail.innerHTML = `<div class="empty-state error-suggestion"><strong>Could not load this run.</strong><span>${escapeHtml(error.message)}</span></div>`;
    }
}

function initializeHistoryPage() {
    if (!document.querySelector("#history-list")) return;
    loadHistory();
}


/* -------------------------------------------------------------------------
   Publications manager
   ------------------------------------------------------------------------- */

let publicationItems = [];
let publicationSuggestion = null;


async function loadPublications() {
    const container = document.querySelector("#publications-list");
    if (!container) {
        return;
    }

    try {
        const response = await fetch("/api/publications", { cache: "no-store" });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();
        publicationItems = data.items || [];

        const count = document.querySelector("#publication-count");
        if (count) {
            count.textContent = publicationItems.length;
        }

        renderPublications();
    } catch (error) {
        console.error("Could not load publications:", error);
        container.innerHTML = `
            <div class="empty-publications">
                Could not load publications: ${escapeHtml(error.message)}
            </div>
        `;
    }
}


function renderPublications() {
    const container = document.querySelector("#publications-list");
    if (!container) {
        return;
    }

    const search = (document.querySelector("#publication-search")?.value || "")
        .trim()
        .toLowerCase();

    const filtered = publicationItems.filter((item) => {
        if (!search) {
            return true;
        }

        return item.name.toLowerCase().includes(search) ||
            item.aliases.some((alias) => alias.toLowerCase().includes(search));
    });

    if (!filtered.length) {
        container.innerHTML = `
            <div class="empty-publications">
                No publications found.
            </div>
        `;
        return;
    }

    container.innerHTML = filtered.map((item) => `
        <div class="publication-row">
            <div class="publication-main">
                <div>
                    <div class="publication-name">
                        ${escapeHtml(item.name)}
                    </div>
                    <div class="publication-meta">
                        ${escapeHtml(item.type_label || publicationTypeLabel(item.type))}
                        · ${item.include_year ? "Year included" : "Year omitted"}
                    </div>
                    <div class="publication-aliases">
                        ${item.aliases.map((alias) => `
                            <span class="alias-tag">${escapeHtml(alias)}</span>
                        `).join("")}
                    </div>
                </div>
                <button class="button secondary publication-edit-button" type="button" data-publication-name="${escapeHtml(item.name)}">Edit</button>
            </div>
        </div>
    `).join("");
}


function openPublicationEditor(name) {
    const item = publicationItems.find((publication) => publication.name === name);
    const panel = document.querySelector("#edit-publication-panel");
    const nameInput = document.querySelector("#edit-publication-name");
    const aliasesInput = document.querySelector("#edit-publication-aliases");
    const typeSelect = document.querySelector("#edit-publication-type");
    const includeYear = document.querySelector("#edit-publication-include-year");
    const summary = document.querySelector("#edit-publication-summary");
    const message = document.querySelector("#edit-publication-message");

    if (!item || !panel || !nameInput || !aliasesInput || !typeSelect || !includeYear) {
        return;
    }

    document.querySelector("#add-publication-panel")?.setAttribute("hidden", "true");
    panel.hidden = false;
    panel.dataset.oldName = item.name;
    nameInput.value = item.name;
    aliasesInput.value = (item.aliases || []).join(", ");
    typeSelect.value = item.type || "issue";
    includeYear.checked = Boolean(item.include_year);
    if (summary) {
        summary.innerHTML = `<strong>${escapeHtml(item.name)}</strong> · ${escapeHtml(item.type_label || publicationTypeLabel(item.type))}`;
    }
    if (message) {
        message.textContent = "";
        message.className = "form-message";
    }
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
    nameInput.focus();
}


async function saveEditedPublication() {
    const panel = document.querySelector("#edit-publication-panel");
    const nameInput = document.querySelector("#edit-publication-name");
    const aliasesInput = document.querySelector("#edit-publication-aliases");
    const typeSelect = document.querySelector("#edit-publication-type");
    const includeYear = document.querySelector("#edit-publication-include-year");
    const message = document.querySelector("#edit-publication-message");
    const button = document.querySelector("#save-edited-publication");

    if (!panel || !nameInput || !aliasesInput || !typeSelect || !includeYear || !message || !button) {
        return;
    }

    const oldName = panel.dataset.oldName || "";
    const name = nameInput.value.trim();
    const aliases = aliasesInput.value.split(",").map((alias) => alias.trim()).filter(Boolean);

    if (!name) {
        message.className = "form-message error";
        message.textContent = "Publication name is required.";
        return;
    }

    button.disabled = true;
    message.className = "form-message";
    message.textContent = "Saving changes...";

    try {
        const response = await fetch("/api/publications/update", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                old_name: oldName,
                name,
                aliases,
                type: typeSelect.value,
                include_year: includeYear.checked,
            }),
        });

        const contentType = response.headers.get("content-type") || "";
        const data = contentType.includes("application/json")
            ? await response.json()
            : { detail: await response.text() };

        if (!response.ok) {
            throw new Error(data.detail || `HTTP ${response.status}`);
        }

        message.className = "form-message success";
        message.textContent = "Publication updated.";
        panel.hidden = true;
        await loadPublications();
    } catch (error) {
        console.error("Could not update publication:", error);
        message.className = "form-message error";
        message.textContent = error.message;
    } finally {
        button.disabled = false;
    }
}


function publicationTypeLabel(type) {
    const labels = {
        issue: "Issue number + year",
        month: "Month + year",
        date: "Date + year",
        week: "Week number + year",
    };

    return labels[type] || type || "Unknown";
}


async function analyzePublication() {
    const filenameInput = document.querySelector("#publication-filename");
    const nameInput = document.querySelector("#publication-name");
    const suggestion = document.querySelector("#profile-suggestion");
    const confirm = document.querySelector("#learn-confirm");
    const analyzeButton = document.querySelector("#analyze-publication");

    if (!filenameInput || !suggestion || !confirm || !analyzeButton) {
        return;
    }

    const filename = filenameInput.value.trim();
    const publicationName = nameInput?.value.trim() || "";

    if (!publicationName) {
        suggestion.className = "profile-suggestion error-suggestion";
        suggestion.textContent = "Enter the publication name first.";
        confirm.hidden = true;
        nameInput?.focus();
        return;
    }

    if (!filename) {
        suggestion.className = "profile-suggestion error-suggestion";
        suggestion.textContent = "Enter one representative filename first.";
        confirm.hidden = true;
        filenameInput.focus();
        return;
    }

    analyzeButton.disabled = true;
    analyzeButton.textContent = "Analyzing...";
    suggestion.className = "profile-suggestion";
    suggestion.textContent = "Running the existing metadata parser...";
    confirm.hidden = true;

    try {
        const response = await fetch("/api/publications/suggest", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                filename,
                publication_name: publicationName,
            }),
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || `HTTP ${response.status}`);
        }

        publicationSuggestion = data;
        renderPublicationSuggestion(data, publicationName, filename);
    } catch (error) {
        console.error("Publication suggestion failed:", error);
        suggestion.className = "profile-suggestion error-suggestion";
        suggestion.textContent = `Could not analyse filename: ${error.message}`;
    } finally {
        analyzeButton.disabled = false;
        analyzeButton.textContent = "Analyze filename";
    }
}


function renderPublicationSuggestion(data, publicationName, filename) {
    const suggestion = document.querySelector("#profile-suggestion");
    const confirm = document.querySelector("#learn-confirm");
    const confidence = document.querySelector("#suggestion-confidence");
    const previewPublication = document.querySelector("#preview-publication");
    const previewAlias = document.querySelector("#preview-alias");
    const previewFormat = document.querySelector("#preview-format");
    const previewYear = document.querySelector("#preview-year");
    const typeSelect = document.querySelector("#publication-type");
    const includeYear = document.querySelector("#publication-include-year");
    const aliasInput = document.querySelector("#publication-alias");

    if (!suggestion || !confirm) {
        return;
    }

    const metadata = data.metadata || {};
    const detected = data.detected || [];

    suggestion.className = "profile-suggestion";
    suggestion.innerHTML = detected.length
        ? `<strong>Detected from filename:</strong> ${escapeHtml(detected.join(" · "))}`
        : `<strong>No release metadata detected.</strong> The parser cannot safely infer a release pattern from this filename.`;

    if (confidence) {
        confidence.className = `suggestion-confidence ${data.confidence || "unknown"}`;
        confidence.textContent =
            data.confidence === "good" ? "Good match" :
            data.confidence === "partial" ? "Partial match" :
            "Needs manual choice";
    }

    if (previewPublication) {
        previewPublication.textContent = publicationName;
    }

    if (previewAlias) {
        const suggestedAlias = aliasInput?.value.trim() || publicationName;
        previewAlias.textContent = suggestedAlias;
    }

    if (previewFormat) {
        previewFormat.textContent = data.type_label || publicationTypeLabel(data.type);
    }

    if (previewYear) {
        previewYear.textContent = data.include_year ? "Included" : "Not detected";
    }

    if (typeSelect) {
        typeSelect.value = data.type || "issue";
    }

    if (includeYear) {
        includeYear.checked = Boolean(data.include_year);
    }

    confirm.hidden = false;

    // Keep the filename visible in the DOM for future profile tooling without
    // changing the current profile data model.
    confirm.dataset.exampleFilename = filename;

    // Avoid an unused-variable warning in browsers/devtools while keeping the
    // parsed metadata available for later UI improvements.
    void metadata;
}


async function savePublication() {
    const nameInput = document.querySelector("#publication-name");
    const aliasInput = document.querySelector("#publication-alias");
    const typeSelect = document.querySelector("#publication-type");
    const includeYear = document.querySelector("#publication-include-year");
    const message = document.querySelector("#publication-form-message");
    const button = document.querySelector("#save-publication");

    if (!nameInput || !typeSelect || !includeYear || !message || !button) {
        return;
    }

    const name = nameInput.value.trim();
    const alias = aliasInput?.value.trim() || name;

    if (!publicationSuggestion) {
        message.className = "form-message error";
        message.textContent = "Analyze the example filename before saving.";
        return;
    }

    if (!name) {
        message.className = "form-message error";
        message.textContent = "Enter a publication name.";
        return;
    }

    button.disabled = true;
    message.className = "form-message";
    message.textContent = "Saving profile...";

    try {
        const response = await fetch("/api/publications", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                name,
                aliases: [alias],
                type: typeSelect.value,
                include_year: includeYear.checked,
            }),
        });

        const contentType = response.headers.get("content-type") || "";
        let data;
        if (contentType.includes("application/json")) {
            data = await response.json();
        } else {
            const text = await response.text();
            data = { detail: text || `HTTP ${response.status}` };
        }

        if (!response.ok) {
            throw new Error(data.detail || `HTTP ${response.status}`);
        }

        message.className = "form-message success";
        message.textContent = "Publication learned. Reloading profiles...";

        setTimeout(() => {
            window.location.reload();
        }, 900);
    } catch (error) {
        console.error("Could not save publication:", error);
        message.className = "form-message error";
        message.textContent = error.message;
        button.disabled = false;
    }
}


async function loadPublicationDiscovery() {
    const list = document.querySelector("#publication-discovery-list");
    const count = document.querySelector("#discovery-count");

    if (!list) {
        return;
    }

    try {
        const response = await fetch("/api/publications/discover", { cache: "no-store" });
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || `HTTP ${response.status}`);
        }

        const groups = data.groups || [];
        const singles = data.singles || [];

        if (count) {
            count.textContent = groups.length
                ? `${groups.length} possible`
                : "None found";
            count.classList.toggle("has-discoveries", groups.length > 0);
        }

        if (!data.has_run) {
            list.innerHTML = `
                <div class="empty-state">
                    <strong>Run a Dry Run first.</strong>
                    <span>Unknown files from the latest run will appear here.</span>
                </div>`;
            return;
        }

        if (!groups.length && !singles.length) {
            list.innerHTML = `
                <div class="empty-state">
                    <strong>No unknown publications found.</strong>
                    <span>Everything in the latest run either matched a known publication or had another review reason.</span>
                </div>`;
            return;
        }

        const groupHtml = groups.map((group) => `
            <div class="discovery-card">
                <div class="discovery-card-main">
                    <div class="discovery-title-row">
                        <h3>${escapeHtml(group.suggested_name || "Possible new publication")}</h3>
                        <span class="discovery-count">${group.count} files</span>
                    </div>
                    <p class="discovery-note">These unknown files share the same publication-name pattern.</p>
                    <div class="discovery-files">
                        ${group.files.map((filename) => `<div>${escapeHtml(filename)}</div>`).join("")}
                    </div>
                </div>
                <button
                    class="button primary discovery-learn-button"
                    data-name="${escapeHtml(group.suggested_name || "")}" 
                    data-filename="${escapeHtml(group.example_filename || "")}">
                    Review &amp; Learn
                </button>
            </div>
        `).join("");

        const singlesHtml = singles.length ? `
            <div class="discovery-singles">
                <h3>Other unknown files</h3>
                <p>These did not have enough matching examples to safely group them.</p>
                ${singles.map((item) => `
                    <div class="discovery-single-row">
                        <span>${escapeHtml(item.filename)}</span>
                        <button
                            class="button secondary discovery-learn-button"
                            data-name="${escapeHtml(item.suggested_name || "")}" 
                            data-filename="${escapeHtml(item.filename || "")}">
                            Use as example
                        </button>
                    </div>
                `).join("")}
            </div>` : "";

        list.innerHTML = groupHtml + singlesHtml;

        list.querySelectorAll(".discovery-learn-button").forEach((button) => {
            button.addEventListener("click", () => {
                const panel = document.querySelector("#add-publication-panel");
                const nameInput = document.querySelector("#publication-name");
                const filenameInput = document.querySelector("#publication-filename");

                if (panel) {
                    panel.hidden = false;
                }
                if (nameInput) {
                    nameInput.value = button.dataset.name || "";
                }
                if (filenameInput) {
                    filenameInput.value = button.dataset.filename || "";
                }
                document.querySelector("#publication-suggestion")?.scrollIntoView({ behavior: "smooth", block: "center" });
                nameInput?.focus();
            });
        });
    } catch (error) {
        console.error("Could not load publication discovery:", error);
        if (count) {
            count.textContent = "Unavailable";
        }
        list.innerHTML = `
            <div class="empty-state error-suggestion">
                <strong>Could not load discovery.</strong>
                <span>${escapeHtml(error.message)}</span>
            </div>`;
    }
}


function initializePublicationsPage() {
    if (!document.querySelector("#publications-list")) {
        return;
    }

    loadPublications();
    loadPublicationDiscovery();

    const search = document.querySelector("#publication-search");
    if (search) {
        search.addEventListener("input", renderPublications);
    }

    document.querySelector("#publications-list")?.addEventListener("click", (event) => {
        const button = event.target.closest(".publication-edit-button");
        if (!button) {
            return;
        }
        openPublicationEditor(button.dataset.publicationName || "");
    });

    document.querySelector("#cancel-edit-publication")?.addEventListener("click", () => {
        const panel = document.querySelector("#edit-publication-panel");
        if (panel) panel.hidden = true;
    });

    document.querySelector("#save-edited-publication")?.addEventListener(
        "click",
        saveEditedPublication
    );

    const showAdd = document.querySelector("#show-add-publication");
    const cancelAdd = document.querySelector("#cancel-add-publication");
    const panel = document.querySelector("#add-publication-panel");

    if (showAdd && panel) {
        showAdd.addEventListener("click", () => {
            panel.hidden = false;
            document.querySelector("#publication-name")?.focus();
        });
    }

    if (cancelAdd && panel) {
        cancelAdd.addEventListener("click", () => {
            panel.hidden = true;
        });
    }

    document.querySelector("#analyze-publication")?.addEventListener(
        "click",
        analyzePublication
    );

    document.querySelector("#publication-filename")?.addEventListener(
        "keydown",
        (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                analyzePublication();
            }
        }
    );

    document.querySelector("#publication-name")?.addEventListener("input", () => {
        const preview = document.querySelector("#preview-publication");
        if (preview) {
            preview.textContent = document.querySelector("#publication-name")?.value.trim() || "—";
        }
    });

    document.querySelector("#advanced-profile-toggle")?.addEventListener("click", () => {
        const fields = document.querySelector("#advanced-profile-fields");
        const toggle = document.querySelector("#advanced-profile-toggle");
        if (!fields || !toggle) {
            return;
        }

        fields.hidden = !fields.hidden;
        toggle.textContent = fields.hidden
            ? "Advanced settings ▸"
            : "Advanced settings ▾";
    });

    document.querySelector("#publication-type")?.addEventListener("change", () => {
        const preview = document.querySelector("#preview-format");
        const type = document.querySelector("#publication-type")?.value;
        if (preview) {
            preview.textContent = publicationTypeLabel(type);
        }
    });

    document.querySelector("#publication-include-year")?.addEventListener("change", () => {
        const preview = document.querySelector("#preview-year");
        const includeYear = document.querySelector("#publication-include-year")?.checked;
        if (preview) {
            preview.textContent = includeYear ? "Included" : "Not included";
        }
    });

    document.querySelector("#save-publication")?.addEventListener(
        "click",
        savePublication
    );

    // Review -> Learn publication can arrive here with a filename (and,
    // when already known, a publication name). Open the Add Publication
    // form automatically, fill it, and run the same existing filename
    // analysis the user would otherwise have to start manually.
    const learnParams = new URLSearchParams(window.location.search);
    const learnFilename = learnParams.get("filename") || "";
    const learnName = learnParams.get("name") || "";

    if (learnFilename) {
        const panel = document.querySelector("#add-publication-panel");
        const nameInput = document.querySelector("#publication-name");
        const filenameInput = document.querySelector("#publication-filename");
        const suggestion = document.querySelector("#profile-suggestion");

        if (panel) panel.hidden = false;
        if (filenameInput) filenameInput.value = learnFilename;

        const prepareLearnForm = async () => {
            try {
                const response = await fetch("/api/publications/learn-context", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        filename: learnFilename,
                        publication_name: learnName,
                    }),
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);

                if (nameInput) nameInput.value = data.publication_name || learnName;
                if (filenameInput) filenameInput.value = data.filename || learnFilename;

                await analyzePublication();
                document.querySelector("#add-publication-panel")?.scrollIntoView({
                    behavior: "smooth",
                    block: "start",
                });
            } catch (error) {
                console.error("Could not prepare Learn form:", error);
                if (suggestion) {
                    suggestion.className = "profile-suggestion error-suggestion";
                    suggestion.textContent = `Could not prepare publication: ${error.message}`;
                }
                nameInput?.focus();
            }
        };

        prepareLearnForm();
    }
}



/* -------------------------------------------------------------------------
   Apply Changes
   ------------------------------------------------------------------------- */

function initializeApplyNavigation() {
    if (window.location.pathname === "/apply") {
        return;
    }

    const actions = document.querySelector(".topbar-actions");
    if (!actions || actions.querySelector(".apply-changes-link")) {
        return;
    }

    const link = document.createElement("a");
    link.className = "button secondary apply-changes-link";
    link.href = "/apply";
    link.textContent = "Apply Changes";
    actions.appendChild(link);
}


async function loadApplyPlan() {
    const summary = document.querySelector("#apply-summary");
    const blocker = document.querySelector("#apply-blocker");
    const items = document.querySelector("#apply-items");
    const confirmRow = document.querySelector("#apply-confirm-row");
    const confirm = document.querySelector("#apply-confirm");
    const button = document.querySelector("#apply-button");
    const message = document.querySelector("#apply-message");

    if (!summary || !items) {
        return;
    }

    summary.innerHTML = "";
    items.innerHTML = `<tr><td colspan="4">Loading apply plan...</td></tr>`;
    blocker.textContent = "";
    message.textContent = "";
    message.className = "apply-message";
    confirmRow.hidden = true;
    confirm.checked = false;
    button.disabled = true;

    try {
        const response = await fetch("/api/apply/plan", { cache: "no-store" });
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || `HTTP ${response.status}`);
        }

        const stats = data.statistics || {};
        const cards = [
            ["AUTO files", stats.total_auto || 0],
            ["Ready", stats.ready || 0],
            ["Blocked", stats.blocked || 0],
            ["Already applied", stats.already_applied || 0],
        ];

        summary.innerHTML = cards.map(([label, value]) => `
            <div class="apply-summary-card">
                <div class="stat-label">${escapeHtml(label)}</div>
                <div class="apply-summary-value">${value}</div>
            </div>
        `).join("");

        if (data.already_applied) {
            blocker.textContent = "This run has already been applied.";
            blocker.className = "form-message";
        } else if (data.reason && (!data.ready || stats.blocked)) {
            blocker.textContent = data.reason;
            blocker.className = "form-message error";
        } else {
            blocker.textContent = "All safety checks passed.";
            blocker.className = "form-message success";
        }

        items.innerHTML = (data.items || []).map((item) => {
            const statusClass = item.status === "READY"
                ? "apply-status-ready"
                : item.status === "BLOCKED"
                    ? "apply-status-blocked"
                    : "apply-status-applied";
            return `
                <tr>
                    <td><span class="${statusClass}">${escapeHtml(item.status)}</span></td>
                    <td><code>${escapeHtml(item.source || "—")}</code></td>
                    <td><code>${escapeHtml(item.destination || "—")}</code></td>
                    <td>${escapeHtml(item.reason || "")}</td>
                </tr>
            `;
        }).join("") || `<tr><td colspan="4">No AUTO files are ready to apply.</td></tr>`;

        if (data.ready) {
            confirmRow.hidden = false;
        }

        const updateButton = () => {
            button.disabled = !data.ready || !confirm.checked;
            button.textContent = data.ready
                ? `Apply ${stats.ready || 0} file${stats.ready === 1 ? "" : "s"}`
                : "Apply Changes";
        };

        confirm.onchange = updateButton;
        updateButton();

    } catch (error) {
        blocker.textContent = `Could not load apply plan: ${error.message}`;
        blocker.className = "form-message error";
        items.innerHTML = `<tr><td colspan="4">Could not load apply plan.</td></tr>`;
    }
}


window.executeApply = executeApply;

async function executeApply() {
    const button = document.querySelector("#apply-button");
    const message = document.querySelector("#apply-message");
    if (!button || button.disabled) {
        return;
    }

    const confirmed = window.confirm(
        "Apply Changes will move the listed files and will not overwrite existing files. Continue?"
    );
    if (!confirmed) {
        return;
    }

    button.disabled = true;
    button.textContent = "Applying...";
    message.textContent = "Moving files...";
    message.className = "apply-message";

    try {
        const response = await fetch("/api/apply", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ confirm: true }),
        });
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || `HTTP ${response.status}`);
        }

        message.textContent = data.message || "Apply completed.";
        message.className = "apply-message success";
        setRunStatus("Apply complete", data.count || 0, data.count || 0, "", 100);
        await loadApplyPlan();
    } catch (error) {
        message.textContent = `Apply stopped: ${error.message}`;
        message.className = "apply-message error";
        setRunStatus("Apply error", 0, 0, error.message, 0, error.message);
        await loadApplyPlan();
    }
}


function initializeApplyPage() {
    if (!document.querySelector("#apply-items")) {
        return;
    }

    const refreshButton = document.querySelector("#apply-refresh");
    if (refreshButton) {
        refreshButton.addEventListener("click", loadApplyPlan);
    }

    loadApplyPlan();
}
