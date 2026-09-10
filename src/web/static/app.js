let dryRunActive = false;


async function runDryRun() {
    if (dryRunActive) {
        await stopDryRun();
        return;
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
        const response = await fetch(
            "/api/dry-run",
            { method: "POST" }
        );

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
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

        // A status/network hiccup should not wipe an already completed run.
        // Keep the error visible in the status bar instead of using a popup.

    } finally {
        dryRunActive = false;
        setDryRunButtons(false);
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


function setDryRunButtons(running) {
    document.querySelectorAll(".dry-run-button").forEach((button) => {
        button.disabled = false;
        button.textContent = running ? "Stop" : "Dry Run";
        button.classList.toggle("danger", running);
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

        if (!data.running) {
            if (data.statistics) {
                updateStatistics(data.statistics);
                updateResults(data.results || []);
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


function initializeRunStatus() {
    setRunStatus(
        "Idle",
        0,
        0,
        "",
        0
    );
}


async function refreshRunStatus() {
    try {
        const response = await fetch(
            "/api/dry-run/status",
            {
                cache: "no-store",
            }
        );

        if (!response.ok) {
            return;
        }

        const data =
            await response.json();

        setRunStatus(
            data.phase,
            data.current,
            data.total,
            data.filename,
            data.percent,
            data.error
        );

        if (!data.running &&
            data.statistics) {
            updateStatistics(
                data.statistics
            );

            updateResults(
                data.results || []
            );

            setReportAvailable(true);
        }

    } catch (error) {
        console.error(
            "Status refresh failed:",
            error
        );
    }
}


function updateStatistics(stats) {
    const elements = {
        auto: document.querySelector(
            '[data-stat="auto"]'
        ),
        review: document.querySelector(
            '[data-stat="review"]'
        ),
        ignore: document.querySelector(
            '[data-stat="ignore"]'
        ),
        collision: document.querySelector(
            '[data-stat="collision"]'
        ),
    };

    if (elements.auto) {
        elements.auto.textContent = stats.auto;
    }

    if (elements.review) {
        elements.review.textContent = stats.review;
    }

    if (elements.ignore) {
        elements.ignore.textContent = stats.ignore;
    }

    if (elements.collision) {
        elements.collision.textContent =
            stats.collisions;
    }
}


function updateResults(results) {
    const container =
        document.querySelector(
            "#results-container"
        );

    if (!container) {
        return;
    }

    if (!results.length) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">▤</div>

                <h3>No results</h3>

                <p>
                    The dry run did not find any
                    supported magazine files.
                </p>
            </div>
        `;

        return;
    }

    container.innerHTML = results
        .map((result) => {
            const statusClass =
                result.status.toLowerCase();

            const destination =
                result.destination ||
                result.reason ||
                "—";

            return `
                <div class="result-row">

                    <div class="result-status ${statusClass}">
                        ${escapeHtml(result.status)}
                    </div>

                    <div
                        class="result-source"
                        title="${escapeHtml(result.source)}"
                    >
                        ${escapeHtml(result.source)}
                    </div>

                    <div class="result-arrow">
                        →
                    </div>

                    <div
                        class="result-destination"
                        title="${escapeHtml(destination)}"
                    >
                        ${escapeHtml(destination)}
                    </div>

                </div>
            `;
        })
        .join("");
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

        container.innerHTML = data.items
            .map((item) => {
                return `
                    <button
                        class="review-item"
                        data-review-index="${item.index}"
                    >

                        <div class="review-item-status">
                            REVIEW
                        </div>

                        <div class="review-item-name">
                            ${escapeHtml(item.filename)}
                        </div>

                        <div class="review-item-meta">
                            ${escapeHtml(
                                item.publication ||
                                "Publication unknown"
                            )}
                        </div>

                    </button>
                `;
            })
            .join("");

        container
            .querySelectorAll(
                ".review-item"
            )
            .forEach((button) => {
                button.addEventListener(
                    "click",
                    () => {
                        const index =
                            Number(
                                button.dataset
                                    .reviewIndex
                            );

                        selectReviewItem(
                            index
                        );
                    }
                );
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

                    <div id="review-destination" class="proposed-destination review-destination-preview">
                        <div class="info-label">Destination preview</div>
                        <code>Complete publication and metadata</code>
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
        const profile = getProfile();
        const publication = getPublicationName();
        const destination = document.querySelector("#review-destination code");
        if (!destination) return;

        if (!publication) {
            destination.textContent = "Enter a publication name";
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
    renderFields();

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
    const profile = getProfile();
    const publication = getPublicationName ? getPublicationName() : (publicationSelect?.value || "");
    const metadata = collectMetadata();

    if (!publication) {
        message.className = "form-message error";
        message.textContent = "Enter or select a publication first.";
        return;
    }

    button.disabled = true;
    message.className = "form-message";
    message.textContent = "Approving...";

    try {
        const response = await fetch(`/api/review/${index}/approve`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ publication, metadata }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);

        message.className = "form-message success";
        message.textContent = "Approved and saved to run history.";
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
        refreshRunStatus();
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


function formatHistoryDate(value) {
    if (!value) {
        return "—";
    }

    try {
        return new Intl.DateTimeFormat("da-DK", {
            dateStyle: "medium",
            timeStyle: "short",
        }).format(new Date(value));
    } catch (error) {
        return value;
    }
}


function historyStatusClass(status) {
    const value = String(status || "").toLowerCase();
    if (value === "finished") {
        return "finished";
    }
    if (value === "stopped") {
        return "stopped";
    }
    if (value === "running") {
        return "running";
    }
    return "other";
}


async function loadHistory() {
    const container = document.querySelector("#history-list");
    if (!container) {
        return;
    }

    try {
        const response = await fetch("/api/history", { cache: "no-store" });
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || `HTTP ${response.status}`);
        }

        historyItems = data.items || [];
        const count = document.querySelector("#history-count");
        if (count) {
            count.textContent = historyItems.length;
        }

        if (!historyItems.length) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">◷</div>
                    <strong>No runs yet</strong>
                    <span>Completed and stopped runs will appear here.</span>
                </div>
            `;
            return;
        }

        container.innerHTML = historyItems.map((run) => {
            const stats = run.statistics || {};
            return `
                <button class="history-row" data-run-id="${escapeHtml(run.run_id)}">
                    <div class="history-row-main">
                        <div class="history-row-title">
                            <strong>${escapeHtml(formatHistoryDate(run.created_at))}</strong>
                            <span class="history-status ${historyStatusClass(run.status)}">
                                ${escapeHtml(run.status || "unknown")}
                            </span>
                        </div>
                        <div class="history-row-path" title="${escapeHtml(run.input_folder || "")}">
                            ${escapeHtml(run.input_folder || "—")}
                        </div>
                    </div>
                    <div class="history-row-stats">
                        <span><strong>${stats.files ?? 0}</strong> files</span>
                        <span><strong>${stats.auto ?? 0}</strong> auto</span>
                        <span><strong>${stats.review ?? 0}</strong> review</span>
                        <span><strong>${stats.ignore ?? 0}</strong> ignore</span>
                        <span><strong>${stats.collisions ?? 0}</strong> collisions</span>
                    </div>
                </button>
            `;
        }).join("");

        container.querySelectorAll(".history-row").forEach((row) => {
            row.addEventListener("click", () => loadHistoryRun(row.dataset.runId));
        });

        if (selectedHistoryRunId && historyItems.some((run) => run.run_id === selectedHistoryRunId)) {
            loadHistoryRun(selectedHistoryRunId);
        }
    } catch (error) {
        console.error("Could not load history:", error);
        container.innerHTML = `
            <div class="empty-state error-suggestion">
                <strong>Could not load history.</strong>
                <span>${escapeHtml(error.message)}</span>
            </div>
        `;
    }
}


async function loadHistoryRun(runId) {
    const detailPanel = document.querySelector("#history-detail-panel");
    const detail = document.querySelector("#history-detail");
    if (!detailPanel || !detail) {
        return;
    }

    selectedHistoryRunId = runId;
    document.querySelectorAll(".history-row").forEach((row) => {
        row.classList.toggle("selected", row.dataset.runId === runId);
    });

    detailPanel.hidden = false;
    detail.innerHTML = `<div class="empty-state">Loading run details...</div>`;

    try {
        const response = await fetch(`/api/history/${encodeURIComponent(runId)}`, { cache: "no-store" });
        const run = await response.json();
        if (!response.ok) {
            throw new Error(run.detail || `HTTP ${response.status}`);
        }

        const stats = run.statistics || {};
        const title = document.querySelector("#history-detail-title");
        const subtitle = document.querySelector("#history-detail-subtitle");
        if (title) {
            title.textContent = `${formatHistoryDate(run.created_at)} · ${run.status || "unknown"}`;
        }
        if (subtitle) {
            subtitle.textContent = run.input_folder || "";
        }

        const reportButton = document.querySelector("#history-report-button");
        if (reportButton) {
            reportButton.onclick = () => window.location.href = `/api/history/${encodeURIComponent(runId)}/report`;
        }

        const items = [];
        if (run.resolved_items && Object.keys(run.resolved_items).length) {
            Object.entries(run.resolved_items).forEach(([filename, item]) => {
                const result = item.result || {};
                items.push({
                    filename: item.filename || filename,
                    status: result.status || "UNKNOWN",
                    publication: result.publication || "",
                    destination: item.destination || result.reason || "—",
                    source: item.source || "—",
                });
            });
        } else {
            Object.entries(run.items || {}).forEach(([fingerprint, entry]) => {
                const item = entry && entry.item;
                if (!item) {
                    return;
                }
                const result = item.result || {};
                items.push({
                    filename: item.filename || fingerprint,
                    status: result.status || "UNKNOWN",
                    publication: result.publication || "",
                    destination: item.destination || result.reason || "—",
                    source: item.source || "—",
                });
            });
        }

        items.sort((a, b) => a.filename.localeCompare(b.filename, "da"));

        const eventHtml = (run.events || []).slice().reverse().map((event) => `
            <div class="history-event">
                <span class="history-event-time">${escapeHtml(formatHistoryDate(event.timestamp))}</span>
                <strong>${escapeHtml(event.message || event.type || "Event")}</strong>
                ${event.filename ? `<span>${escapeHtml(event.filename)}</span>` : ""}
            </div>
        `).join("");

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
                <div class="info-label">Files in this run</div>
                <div class="history-results">
                    ${items.length ? items.map((item) => `
                        <div class="history-result-row">
                            <span class="history-result-status ${escapeHtml(String(item.status).toLowerCase())}">${escapeHtml(item.status)}</span>
                            <span class="history-result-file" title="${escapeHtml(item.filename)}">${escapeHtml(item.filename)}</span>
                            <span class="history-result-destination" title="${escapeHtml(item.destination)}">${escapeHtml(item.destination)}</span>
                        </div>
                    `).join("") : `<div class="empty-state">No stored file results.</div>`}
                </div>
            </div>

            <div class="history-section">
                <div class="info-label">Activity</div>
                <div class="history-events">
                    ${eventHtml || `<div class="empty-state">No recorded events.</div>`}
                </div>
            </div>
        `;

        detailPanel.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
        detail.innerHTML = `
            <div class="empty-state error-suggestion">
                <strong>Could not load this run.</strong>
                <span>${escapeHtml(error.message)}</span>
            </div>
        `;
    }
}


function initializeHistoryPage() {
    if (!document.querySelector("#history-list")) {
        return;
    }
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

        if (data.reason && (!data.ready || stats.blocked)) {
            blocker.textContent = data.reason;
            blocker.className = "form-message error";
        } else if (data.already_applied) {
            blocker.textContent = "This run has already been applied.";
            blocker.className = "form-message";
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
        await loadApplyPlan();
    } catch (error) {
        message.textContent = `Apply stopped: ${error.message}`;
        message.className = "apply-message error";
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
