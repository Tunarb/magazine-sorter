from pathlib import Path
import os
import shutil
import hashlib
from threading import Event, Lock, Thread
from datetime import datetime, timezone
import asyncio
import json
import re
import uuid
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Set

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.concurrency import run_in_threadpool

from src.classifier import classify_file
from src.ocr_classifier import analyze_file
from src.path_builder import (
    build_destination,
    build_special_destination,
    build_standalone_destination,
)
from src.scanner import find_magazines
from src.web.profile_manager import (
    add_publication,
    list_publications,
    suggest_profile,
    update_publication,
)
from src.web.settings_manager import (
    DEFAULT_SETTINGS,
    OCR_LANGUAGES,
    env_managed_paths,
    get_effective_settings,
    get_input_folder,
    get_output_folder,
    load_settings,
    save_settings,
    test_folder_access,
    validate_settings_payload,
    page_stages_to_text,
)
from src.web.auto_run import (
    AUTO_RUN_MODES,
    AUTO_RUN_SCHEDULES,
    DEFAULT_AUTO_RUN,
    WEEKDAYS,
    next_run_at,
    schedule_summary,
    validate_auto_run,
)
from src.web.run_store import (
    checkpoint_items,
    checkpoint_matches_input,
    clear_checkpoint,
    file_fingerprint,
    load_checkpoint,
    save_checkpoint,
    checkpoint_review_decisions,
    checkpoint_events,
    ensure_checkpoint_identity,
    new_run_id,
    save_history_run,
    load_history_run,
    list_history_runs,
)


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent.parent
DATA_DIR = Path(os.getenv("MAGAZINE_SORTER_DATA_DIR", str(PROJECT_ROOT / "data")))

TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# Settings are persisted separately from source code. Environment variables
# remain available as deployment overrides for Docker/Unraid.

def _current_input_folder():
    return get_input_folder()


def _current_output_folder():
    return get_output_folder()


# No fixed folder constants: paths are read from Settings when needed.


app = FastAPI(
    title="Magazine Sorter",
    version="0.9.0",
)


app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR)),
    name="static",
)


templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR)
)


# ---------------------------------------------------------------------------
# GUI pipeline state
# ---------------------------------------------------------------------------

_last_run = None
_review_decisions = {}

_run_lock = Lock()
_cancel_event = Event()

_apply_operation_lock = Lock()
_apply_state_lock = Lock()

_run_state = {
    "running": False,
    "phase": "Idle",
    "current": 0,
    "total": 0,
    "filename": "",
    "percent": 0,
    "error": None,
    "cancelled": False,
}

_apply_state = {
    "running": False,
    "phase": "Idle",
    "current": 0,
    "total": 0,
    "filename": "",
    "percent": 0,
    "error": None,
}


def _set_apply_state(current=None, total=None, phase=None, filename=None, percent=None, error=None, running=None):
    with _apply_state_lock:
        if current is not None:
            _apply_state["current"] = int(current)
        if total is not None:
            _apply_state["total"] = int(total)
        if phase is not None:
            _apply_state["phase"] = str(phase)
        if filename is not None:
            _apply_state["filename"] = str(filename)
        if percent is not None:
            _apply_state["percent"] = max(0, min(100, int(percent)))
        if error is not None:
            _apply_state["error"] = error
        elif phase not in ("Error",):
            _apply_state["error"] = None
        if running is not None:
            _apply_state["running"] = bool(running)


def _get_apply_state():
    with _apply_state_lock:
        return dict(_apply_state)


_AUTO_STATE_FILE = DATA_DIR / "auto_run_state.json"
_auto_stop_event = Event()
_auto_thread = None
_auto_runtime_lock = Lock()
_auto_runtime = {
    "running": False,
    "phase": "Disabled",
    "last_started_at": None,
    "last_finished_at": None,
    "last_result": None,
    "next_run_at": None,
    "last_error": None,
    "startup_consumed": False,
    "seen_fingerprints": [],
}


def _load_auto_runtime():
    if not _AUTO_STATE_FILE.exists():
        return
    try:
        with _AUTO_STATE_FILE.open("r", encoding="utf-8") as handle:
            saved = json.load(handle)
        if isinstance(saved, dict):
            with _auto_runtime_lock:
                for key in _auto_runtime:
                    if key in saved:
                        _auto_runtime[key] = saved[key]
    except (OSError, ValueError, TypeError):
        pass


def _save_auto_runtime():
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with _auto_runtime_lock:
            payload = dict(_auto_runtime)
        temporary = _AUTO_STATE_FILE.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        temporary.replace(_AUTO_STATE_FILE)
    except OSError:
        pass


def _set_auto_runtime(**updates):
    with _auto_runtime_lock:
        _auto_runtime.update(updates)
    _save_auto_runtime()


def _auto_run_files(settings):
    input_folder = _current_input_folder()
    files = list(find_magazines(input_folder))
    cutoff = datetime.now().timestamp() - (settings["min_file_age_minutes"] * 60)
    selected = []
    seen = set()
    if settings.get("skip_unchanged"):
        with _auto_runtime_lock:
            seen = set((_auto_runtime.get("seen_fingerprints") or []))

    for file in files:
        try:
            fingerprint = file_fingerprint(file)
            if settings.get("skip_unchanged") and fingerprint in seen:
                continue
            if Path(file.path).stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        selected.append(file)
        if settings["max_files_per_run"] and len(selected) >= settings["max_files_per_run"]:
            break
    return selected


def _remember_auto_files(files, result=None, safe_apply=False):
    if not files:
        return

    # Only remember files that are genuinely resolved. REVIEW, ERROR, IGNORE
    # and collision-blocked files must remain eligible for a later Auto Run,
    # because classifier/profile changes or external fixes may make them
    # actionable without changing the file itself.
    eligible = []
    if result:
        collisions = set((result.get("collisions") or {}).keys())
        for item in result.get("results", {}).get("AUTO", []):
            if item.get("destination") in collisions:
                continue
            eligible.append(item.get("filename"))
    else:
        eligible = [file.filename for file in files]

    eligible = set(eligible)
    if not eligible:
        return

    with _auto_runtime_lock:
        seen = list(_auto_runtime.get("seen_fingerprints") or [])
        existing = set(seen)
        for file in files:
            if file.filename not in eligible:
                continue
            try:
                fingerprint = file_fingerprint(file)
            except OSError:
                # Safe Apply may already have moved the source. In that case
                # there is no need to remember it; the source is no longer in
                # the input tree and cannot be selected again.
                continue
            if fingerprint not in existing:
                seen.append(fingerprint)
                existing.add(fingerprint)
        _auto_runtime["seen_fingerprints"] = seen[-50000:]
    _save_auto_runtime()


def _execute_auto_run(settings, trigger="scheduled"):
    """Execute one Auto Run using the normal Dry Run/Apply pipeline."""
    with _run_lock:
        if _run_state["running"]:
            return {"started": False, "reason": "active_run"}

    selected = _auto_run_files(settings)

    # If skip_unchanged (or the other processing safeguards) leaves nothing
    # to process, do not create an empty Dry Run. An empty Auto Run would
    # otherwise replace the current run and make pending Review items
    # disappear from the Review queue. The existing run/history remains the
    # source of truth until there is actually something new to process.
    if not selected:
        _set_auto_runtime(
            running=False,
            phase="No new files",
            last_error=None,
            last_result={
                "files": 0,
                "message": "No new or changed files to process.",
            },
        )
        return {"started": False, "files": 0, "reason": "no_new_files"}

    started = datetime.now().astimezone()
    _set_auto_runtime(
        running=True,
        phase="Starting" if trigger == "scheduled" else "Starting manual run",
        last_started_at=started.isoformat(),
        last_error=None,
    )

    try:
        asyncio.run(dry_run(new_run=True, run_type="auto_run", files_override=selected))
        while not _auto_stop_event.is_set():
            with _run_lock:
                active = bool(_run_state["running"])
            if not active:
                break
            _auto_stop_event.wait(1)

        if settings["mode"] == "safe_apply" and not _auto_stop_event.is_set():
            try:
                result = asyncio.run(apply_changes({"confirm": True}))
                _set_auto_runtime(phase="Finished", last_result={
                    "moved": result.get("count", 0),
                    "message": result.get("message"),
                })
            except HTTPException as exc:
                _set_auto_runtime(phase="Finished with safety block", last_result={
                    "moved": 0,
                    "message": str(exc.detail),
                })
        else:
            result = get_last_run()
            stats = (result or {}).get("statistics") or {}
            _set_auto_runtime(phase="Finished", last_result={
                "files": stats.get("files", 0),
                "auto": stats.get("auto", 0),
                "review": stats.get("review", 0),
                "collisions": stats.get("collisions", 0),
            })

        _remember_auto_files(selected, result=get_last_run(), safe_apply=(settings["mode"] == "safe_apply"))
        _set_auto_runtime(
            running=False,
            last_finished_at=datetime.now().astimezone().isoformat(),
        )
        return {"started": True, "files": len(selected)}
    except Exception as exc:
        _set_auto_runtime(
            running=False,
            phase="Error",
            last_finished_at=datetime.now().astimezone().isoformat(),
            last_error=str(exc),
        )
        return {"started": True, "files": len(selected), "error": str(exc)}


def _auto_run_worker():
    while not _auto_stop_event.wait(20):
        settings = get_effective_settings().get("auto_run", DEFAULT_AUTO_RUN)
        try:
            settings = validate_auto_run(settings)
        except ValueError as exc:
            _set_auto_runtime(phase="Configuration error", last_error=str(exc), next_run_at=None)
            continue

        if not settings["enabled"]:
            _set_auto_runtime(running=False, phase="Disabled", next_run_at=None, last_error=None)
            continue

        with _auto_runtime_lock:
            running = bool(_auto_runtime.get("running"))
            next_at_raw = _auto_runtime.get("next_run_at")

        if running:
            continue

        now = datetime.now().astimezone()
        startup_due = False
        if not next_at_raw:
            next_at = next_run_at(settings, now)
            with _auto_runtime_lock:
                startup_consumed = bool(_auto_runtime.get("startup_consumed"))
            startup_due = bool(settings.get("run_on_startup")) and not startup_consumed
            _set_auto_runtime(next_run_at=next_at.isoformat(), phase="Scheduled")
            if startup_due:
                _set_auto_runtime(startup_consumed=True)

        with _auto_runtime_lock:
            next_at_raw = _auto_runtime.get("next_run_at")
        try:
            due_at = datetime.fromisoformat(next_at_raw) if next_at_raw else now
        except ValueError:
            due_at = now
        if now < due_at and not startup_due:
            continue

        with _run_lock:
            if _run_state["running"]:
                _set_auto_runtime(phase="Waiting for active run")
                continue

        # A startup run is one-shot. Clear the flag in runtime flow by
        # scheduling the next normal run immediately before executing.
        following = next_run_at(settings, now)
        _set_auto_runtime(next_run_at=following.isoformat())
        settings_for_run = dict(settings)
        settings_for_run["run_on_startup"] = False
        _execute_auto_run(settings_for_run, trigger="scheduled")


def _start_auto_run_now():
    settings = validate_auto_run(get_effective_settings().get("auto_run", DEFAULT_AUTO_RUN))
    with _auto_runtime_lock:
        if _auto_runtime.get("running"):
            return False, "Auto Run is already running."
    with _run_lock:
        if _run_state["running"]:
            return False, "Another run is already active."
    Thread(
        target=_execute_auto_run,
        args=(settings, "manual"),
        daemon=True,
        name="magazine-sorter-auto-run-manual",
    ).start()
    return True, "Auto Run started."

def _start_auto_thread():
    global _auto_thread
    if _auto_thread and _auto_thread.is_alive():
        return
    _load_auto_runtime()
    _auto_stop_event.clear()
    _auto_thread = Thread(target=_auto_run_worker, daemon=True, name="magazine-sorter-auto-run")
    _auto_thread.start()


def _stop_auto_thread():
    _auto_stop_event.set()


@app.on_event("startup")
async def _startup_auto_run():
    _start_auto_thread()


@app.on_event("shutdown")
async def _shutdown_auto_run():
    _stop_auto_thread()


def get_destination(publication, metadata):
    if not publication:
        return None

    return build_destination(
        {
            "magazine": publication,
            **metadata,
        }
    )


def run_gui_dry_run(input_folder, progress_callback=None, cancel_check=None, resume_items=None, completed_callback=None, ocr_settings=None, files_override=None):
    """
    GUI shell around the existing classifier/OCR engine.

    Important:
    - No parser/classifier/OCR logic is implemented here.
    - Filename classification remains first priority.
    - OCR is only invoked for files that are REVIEW after filename
      classification.
    - A sufficiently confident OCR result becomes AUTO.
    - Nothing is moved or renamed.
    """

    input_folder = Path(input_folder)
    ocr_settings = ocr_settings or get_effective_settings().get("ocr", {})
    files = list(files_override) if files_override is not None else find_magazines(input_folder)

    if progress_callback:
        progress_callback(
            0,
            len(files),
            "Scanning files",
            "",
        )

    results = {
        "AUTO": [],
        "REVIEW": [],
        "IGNORE": [],
        "ERROR": [],
    }

    destinations = {}
    resume_items = resume_items or {}

    def append_item(item):
        status = item["result"]["status"]
        results[status].append(item)

        destination = item.get("destination")
        if status == "AUTO" and destination:
            destinations.setdefault(destination, []).append(item["filename"])

    # Restore results already completed in an earlier run. The fingerprint
    # check below ensures changed files are processed again.
    for file in files:
        fingerprint = None
        try:
            fingerprint = file_fingerprint(file)
        except OSError:
            pass

        cached = resume_items.get(fingerprint) if fingerprint else None
        if cached and isinstance(cached.get("item"), dict):
            append_item(cached["item"])

    for index, file in enumerate(files, start=1):
        if cancel_check and cancel_check():
            break

        fingerprint = None
        try:
            fingerprint = file_fingerprint(file)
        except OSError:
            fingerprint = None

        cached = resume_items.get(fingerprint) if fingerprint else None
        if cached and isinstance(cached.get("item"), dict):
            if progress_callback:
                progress_callback(index, len(files), "Resuming", file.filename)
            continue

        if progress_callback:
            progress_callback(
                index - 1,
                len(files),
                "Classifying filename",
                file.filename,
            )

        try:
            filename_result = classify_file(file.filename)

            # Existing classifier says IGNORE.
            if filename_result["status"] == "IGNORE":
                item = {
                    "filename": file.filename,
                    "extension": file.extension,
                    "path": str(Path(file.path).resolve()),
                    "result": filename_result,
                    "destination": None,
                    "source": "FILENAME",
                    "ocr": None,
                }
                append_item(item)
                if completed_callback and fingerprint:
                    completed_callback(fingerprint, item)
                continue

            # Existing classifier already says AUTO.
            if filename_result["status"] == "AUTO":
                destination = get_destination(
                    filename_result["publication"],
                    filename_result["metadata"],
                )

                item = {
                    "filename": file.filename,
                    "extension": file.extension,
                    "path": str(Path(file.path).resolve()),
                    "result": filename_result,
                    "destination": destination,
                    "source": "FILENAME",
                    "ocr": None,
                }

                append_item(item)
                if completed_callback and fingerprint:
                    completed_callback(fingerprint, item)

                continue

            # Some REVIEW results are explicit Special candidates. They
            # must remain REVIEW so OCR cannot silently promote them to a
            # normal issue. The user can classify them as Special or
            # Standalone from the Review UI.
            if filename_result.get("reason") == "SPECIAL_CANDIDATE":
                item = {
                    "filename": file.filename,
                    "extension": file.extension,
                    "path": str(Path(file.path).resolve()),
                    "result": filename_result,
                    "destination": None,
                    "source": "FILENAME",
                    "ocr": None,
                }
                append_item(item)
                if completed_callback and fingerprint:
                    completed_callback(fingerprint, item)
                continue

            # Existing classifier says REVIEW.
            # Let the existing OCR classifier decide whether it can
            # safely promote the file to AUTO.
            if file.extension.lower() == ".pdf":
                if progress_callback:
                    progress_callback(
                        index - 1,
                        len(files),
                        "OCR",
                        file.filename,
                    )

                analysis = analyze_file(
                    file.path,
                    language=ocr_settings.get("language", "dan"),
                    dpi=int(ocr_settings.get("dpi", 300)),
                    page_stages=ocr_settings.get("page_stages") or [[1], [4], [5]],
                    ocr_enabled=bool(ocr_settings.get("enabled", True)),
                )

                action = analysis["action"]

                if action == "AUTO":
                    publication = analysis["publication"]
                    metadata = analysis["merged_metadata"]
                    destination = get_destination(
                        publication,
                        metadata,
                    )

                    item = {
                        "filename": file.filename,
                        "extension": file.extension,
                        "path": str(Path(file.path).resolve()),
                        "result": {
                            "status": "AUTO",
                            "publication": publication,
                            "metadata": metadata,
                            "reason": None,
                        },
                        "destination": destination,
                        "source": "OCR",
                        "ocr": analysis,
                    }

                    append_item(item)
                    if completed_callback and fingerprint:
                        completed_callback(fingerprint, item)

                else:
                    item = {
                        "filename": file.filename,
                        "extension": file.extension,
                        "path": str(Path(file.path).resolve()),
                        "result": {
                            "status": "REVIEW",
                            "publication": analysis["publication"],
                            "metadata": analysis["merged_metadata"],
                            "reason": (
                                "; ".join(analysis["reasons"])
                                if analysis["reasons"]
                                else "OCR could not identify the file safely"
                            ),
                        },
                        "destination": (
                            get_destination(
                                analysis["publication"],
                                analysis["merged_metadata"],
                            )
                            if analysis["publication"]
                            else None
                        ),
                        "source": "OCR",
                        "ocr": analysis,
                    }

                    append_item(item)
                    if completed_callback and fingerprint:
                        completed_callback(fingerprint, item)

            else:
                # OCR engine currently supports PDFs only.
                item = {
                    "filename": file.filename,
                    "extension": file.extension,
                    "path": str(Path(file.path).resolve()),
                    "result": filename_result,
                    "destination": None,
                    "source": "FILENAME",
                    "ocr": None,
                }
                append_item(item)
                if completed_callback and fingerprint:
                    completed_callback(fingerprint, item)

        except Exception as exc:
            item = {
                "filename": file.filename,
                "extension": file.extension,
                "path": str(Path(file.path).resolve()),
                "result": {
                    "status": "ERROR",
                    "publication": None,
                    "metadata": {},
                    "reason": str(exc),
                },
                "destination": None,
                "source": None,
                "ocr": None,
            }
            append_item(item)
            if completed_callback and fingerprint:
                completed_callback(fingerprint, item)

    collisions = {
        destination: filenames
        for destination, filenames in destinations.items()
        if len(filenames) > 1
    }

    statistics = {
        "files": len(files),
        "processed": (
            len(results["AUTO"])
            + len(results["REVIEW"])
            + len(results["IGNORE"])
            + len(results["ERROR"])
        ),
        "auto": len(results["AUTO"]),
        "review": len(results["REVIEW"]),
        "ignore": len(results["IGNORE"]),
        "errors": len(results["ERROR"]),
        "unique_destinations": len(destinations),
        "collisions": len(collisions),
        "blocked_files": sum(len(filenames) for filenames in collisions.values()),
    }
    collision_details = build_collision_details(files, collisions)

    if progress_callback and not (cancel_check and cancel_check()):
        progress_callback(
            len(files),
            len(files),
            "Finished",
            "",
        )

    return {
        "input_folder": str(input_folder),
        "files": files,
        "results": results,
        "collisions": collisions,
        "collision_details": collision_details,
        "statistics": statistics,
    }


def _sha256_file(path, chunk_size=1024 * 1024):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def build_collision_details(files, collisions):
    """Build lightweight side-by-side evidence for blocked collision groups.

    Hashing is intentionally limited to files that are part of a collision,
    so normal dry runs do not pay the cost for every magazine.
    """
    if not collisions:
        return {}

    by_filename = {file.filename: file for file in files}
    details = {}

    for destination, filenames in collisions.items():
        group = []
        for filename in filenames:
            file = by_filename.get(filename)
            detail = {
                "filename": filename,
                "size": None,
                "sha256": None,
                "hash_status": "unavailable",
            }
            if file is not None:
                try:
                    path = Path(file.path)
                    detail["size"] = path.stat().st_size
                    detail["sha256"] = _sha256_file(path)
                    detail["hash_status"] = "ok"
                except OSError:
                    pass
            group.append(detail)
        details[destination] = group

    return details


def format_result(item, collision_details=None):
    result = item["result"]

    payload = {
        "status": result["status"],
        "source": item["filename"],
        "destination": item["destination"],
        "publication": result.get("publication"),
        "reason": result.get("reason"),
        "source_type": item.get("source"),
    }

    if collision_details and item.get("destination") in collision_details:
        group = collision_details[item["destination"]]
        payload["collision"] = {
            "destination": item["destination"],
            "files": group,
            "identical": (
                len(group) > 1
                and all(file.get("hash_status") == "ok" and file.get("sha256") == group[0].get("sha256") for file in group)
            ),
        }

    return payload


def get_last_run():
    global _last_run
    if _last_run is not None:
        return _last_run

    checkpoint, result = _load_persistent_current_run()
    if checkpoint is not None and result is not None:
        _review_decisions.clear()
        _review_decisions.update(checkpoint_review_decisions(checkpoint))
        _last_run = result
    return _last_run


def get_review_items():
    run = get_last_run()
    if run is None:
        return []
    return run["results"]["REVIEW"]


def _event(event_type, message, **details):
    event = {
        "type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": message,
    }
    event.update(details)
    return event


def _apply_persisted_decision(item, decision):
    """Return a copy of an item with a persisted manual decision applied.

    Manual reclassification is allowed for Review, Blocked (raw AUTO), and
    Ignore items. The raw classifier result in the checkpoint remains intact.
    """
    if not decision:
        return item, item.get("result", {}).get("status")

    action = str(decision.get("action") or "").upper()
    if action not in {"IGNORE", "AUTO", "SPECIAL", "STANDALONE"}:
        return item, item.get("result", {}).get("status")

    resolved = dict(item)
    resolved["result"] = dict(item.get("result") or {})

    if action == "IGNORE":
        resolved["result"]["status"] = "IGNORE"
        resolved["result"]["reason"] = "Ignored manually in Review"
        return resolved, "IGNORE"

    resolved["result"]["status"] = "AUTO"
    resolved["result"]["classification"] = action
    resolved["result"]["publication"] = decision.get("publication")
    resolved["result"]["metadata"] = decision.get("metadata") or {}

    if action == "SPECIAL":
        resolved["result"]["reason"] = "Classified manually as Special"
        resolved["result"]["special_title"] = decision.get("title")
        resolved["destination"] = build_special_destination(
            decision.get("publication"),
            decision.get("title"),
            decision.get("year"),
            extension=Path(resolved.get("filename", "")).suffix or ".pdf",
        )
    elif action == "STANDALONE":
        resolved["result"]["reason"] = "Classified manually as Standalone"
        resolved["destination"] = build_standalone_destination(
            decision.get("filename") or resolved.get("filename", ""),
            decision.get("oneshots_dir", "_oneshots"),
        )
    else:
        resolved["result"]["reason"] = "Approved manually in Review"
        resolved["destination"] = get_destination(
            decision.get("publication"),
            decision.get("metadata") or {},
        )

    return resolved, "AUTO"


def _run_record_from_checkpoint(checkpoint, result=None):
    checkpoint = ensure_checkpoint_identity(dict(checkpoint))
    if result is None:
        stored_items = checkpoint_items(checkpoint)
        decisions = checkpoint_review_decisions(checkpoint)
        results = {"AUTO": [], "REVIEW": [], "IGNORE": [], "ERROR": []}
        for entry in stored_items.values():
            item = entry.get("item") if isinstance(entry, dict) else None
            if not isinstance(item, dict):
                continue
            status = item.get("result", {}).get("status")
            if status not in results:
                continue
            decision = decisions.get(item.get("filename"))
            item, status = _apply_persisted_decision(item, decision)
            if status not in results:
                continue
            results[status].append(item)

        stored_stats = checkpoint.get("statistics") or {}
        result = {
            "input_folder": checkpoint.get("input_folder") or str(_current_input_folder()),
            "files": [],
            "results": results,
            "collisions": {},
            "statistics": {
                "files": stored_stats.get("files", checkpoint.get("total", 0)),
                "processed": stored_stats.get("processed", sum(len(v) for v in results.values())),
                "auto": len(results["AUTO"]),
                "review": len(results["REVIEW"]),
                "ignore": len(results["IGNORE"]),
                "errors": len(results["ERROR"]),
                "unique_destinations": stored_stats.get("unique_destinations", 0),
                "collisions": stored_stats.get("collisions", 0),
            },
        }

        destinations = {}
        for item in results["AUTO"]:
            destination = item.get("destination")
            if destination:
                destinations.setdefault(destination, []).append(item.get("filename"))
        result["collisions"] = {
            destination: filenames
            for destination, filenames in destinations.items()
            if len(filenames) > 1
        }
        result["statistics"]["unique_destinations"] = len(destinations)
        result["statistics"]["collisions"] = len(result["collisions"])

    resolved_items = {}
    for status in ["AUTO", "REVIEW", "IGNORE", "ERROR"]:
        for item in result["results"].get(status, []):
            resolved_items[item["filename"]] = item

    return {
        "schema_version": 2,
        "run_id": checkpoint["run_id"],
        "run_type": checkpoint.get("run_type", "dry_run"),
        "input_folder": result["input_folder"],
        "created_at": checkpoint.get("created_at"),
        "updated_at": checkpoint.get("updated_at"),
        "status": checkpoint.get("status", "finished"),
        "total": checkpoint.get("total", result["statistics"]["files"]),
        "statistics": result["statistics"],
        "items": checkpoint_items(checkpoint),
        "resolved_items": resolved_items,
        "review_decisions": checkpoint_review_decisions(checkpoint),
        "events": checkpoint_events(checkpoint),
    }


def _archive_checkpoint(checkpoint, result=None):
    if not checkpoint:
        return None

    checkpoint = ensure_checkpoint_identity(dict(checkpoint))
    record = _run_record_from_checkpoint(checkpoint, result=result)
    save_history_run(record)
    return record


def _repair_apply_event_fingerprints(checkpoint):
    """Backfill rollback fingerprints for older Apply events that lack them."""
    if not checkpoint:
        return checkpoint, False

    fingerprint_by_filename = {}
    fingerprint_by_path = {}
    for fingerprint, entry in checkpoint_items(checkpoint).items():
        item = entry.get("item") if isinstance(entry, dict) else None
        if not isinstance(item, dict):
            continue
        filename = item.get("filename")
        if filename:
            fingerprint_by_filename.setdefault(filename, str(fingerprint))
        item_path = item.get("path")
        if item_path:
            fingerprint_by_path[str(Path(item_path).resolve())] = str(fingerprint)

    events = list(checkpoint_events(checkpoint))
    changed = False
    for event in events:
        if event.get("type") != "apply_moved" or event.get("fingerprint"):
            continue
        event_source = event.get("source")
        fingerprint = None
        if event_source:
            fingerprint = fingerprint_by_path.get(str(Path(event_source).resolve()))
        if not fingerprint:
            fingerprint = fingerprint_by_filename.get(event.get("filename"))
        if fingerprint:
            event["fingerprint"] = fingerprint
            changed = True

    if changed:
        checkpoint["events"] = events
    return checkpoint, changed


def _load_persistent_current_run():
    checkpoint = load_checkpoint()
    if not checkpoint or not checkpoint_matches_input(checkpoint, _current_input_folder()):
        return None, None

    had_identity = bool(checkpoint.get("run_id"))
    checkpoint = ensure_checkpoint_identity(checkpoint)
    checkpoint, repaired_events = _repair_apply_event_fingerprints(checkpoint)
    if repaired_events:
        save_checkpoint(
            _current_input_folder(),
            checkpoint.get("total", len(checkpoint_items(checkpoint))),
            checkpoint_items(checkpoint),
            status=checkpoint.get("status", "finished"),
            run_id=checkpoint.get("run_id"),
            review_decisions=checkpoint_review_decisions(checkpoint),
            events=checkpoint_events(checkpoint),
            created_at=checkpoint.get("created_at"),
            run_type=checkpoint.get("run_type", "dry_run"),
        )
        checkpoint = load_checkpoint() or checkpoint
    if not had_identity:
        save_checkpoint(
            _current_input_folder(),
            checkpoint.get("total", len(checkpoint_items(checkpoint))),
            checkpoint_items(checkpoint),
            status=checkpoint.get("status", "finished"),
            run_id=checkpoint.get("run_id"),
            review_decisions=checkpoint_review_decisions(checkpoint),
            events=checkpoint_events(checkpoint),
            created_at=checkpoint.get("created_at"),
        )
        checkpoint = load_checkpoint() or checkpoint

    items = checkpoint_items(checkpoint)
    if not items:
        return checkpoint, None

    result = rebuild_result_from_checkpoint(
        _current_input_folder(),
        items,
        checkpoint_review_decisions(checkpoint),
    )
    # The input folder is smaller after Apply, but the run still represents
    # the original Dry Run population. Preserve that run-level total and make
    # the persisted Apply/Undo events available to the current-run state.
    if checkpoint.get("status") == "finished":
        result["statistics"]["files"] = checkpoint.get("total", result["statistics"].get("files", 0))
        result["statistics"]["processed"] = sum(
            len(result["results"].get(status, []))
            for status in ("AUTO", "REVIEW", "IGNORE", "ERROR")
        )
    result["events"] = checkpoint_events(checkpoint)
    if checkpoint.get("status") == "finished":
        _archive_checkpoint(checkpoint, result=result)
    return checkpoint, result


def _persist_review_state():
    checkpoint = load_checkpoint()
    if not checkpoint or not checkpoint_matches_input(checkpoint, _current_input_folder()):
        return

    checkpoint = ensure_checkpoint_identity(checkpoint)
    decisions = dict(_review_decisions)
    events = list(checkpoint_events(checkpoint))

    save_checkpoint(
        _current_input_folder(),
        checkpoint.get("total", len(checkpoint_items(checkpoint))),
        checkpoint_items(checkpoint),
        status=checkpoint.get("status", "finished"),
        run_id=checkpoint["run_id"],
        review_decisions=decisions,
        events=events,
        created_at=checkpoint.get("created_at"),
    )

    refreshed = load_checkpoint()
    if refreshed:
        result = rebuild_result_from_checkpoint(
            _current_input_folder(),
            checkpoint_items(refreshed),
            checkpoint_review_decisions(refreshed),
        )
        _archive_checkpoint(refreshed, result=result)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    stats = {
        "auto": 0,
        "review": 0,
        "ignore": 0,
        "collision": 0,
    }

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "stats": stats,
            "results": [],
            "input_folder": str(_current_input_folder()),
        },
    )


def _update_run_state(current, total, phase, filename):
    with _run_lock:
        _run_state["current"] = current
        _run_state["total"] = total
        _run_state["phase"] = phase
        _run_state["filename"] = filename
        _run_state["percent"] = (
            round((current / total) * 100)
            if total
            else 100
        )


def _dry_run_worker(resume_items=None, run_type="dry_run", files_override=None):
    global _last_run

    input_folder = _current_input_folder()
    settings_snapshot = get_effective_settings()
    checkpoint_items_map = dict(resume_items or {})

    def completed_callback(fingerprint, item):
        checkpoint_items_map[fingerprint] = {
            "item": item,
        }
        checkpoint = load_checkpoint() or {}
        checkpoint = ensure_checkpoint_identity(checkpoint)
        try:
            save_checkpoint(
                _current_input_folder(),
                _run_state["total"],
                checkpoint_items_map,
                status="running",
                run_id=checkpoint.get("run_id"),
                review_decisions=_review_decisions,
                events=checkpoint_events(checkpoint),
                created_at=checkpoint.get("created_at"),
                run_type=run_type,
            )
        except OSError as exc:
            # Checkpoint persistence is auxiliary to classification. A transient
            # Windows file-lock/AV race must never turn an otherwise valid file
            # result into an ERROR. The final checkpoint save below will retry.
            print(f"Checkpoint update deferred: {exc}")

    try:
        result = run_gui_dry_run(
            input_folder,
            progress_callback=_update_run_state,
            cancel_check=_cancel_event.is_set,
            resume_items=checkpoint_items_map,
            completed_callback=completed_callback,
            ocr_settings=settings_snapshot.get("ocr", {}),
            files_override=files_override,
        )

        cancelled = _cancel_event.is_set()

        status = "stopped" if cancelled else "finished"
        previous = load_checkpoint() or {}
        previous = ensure_checkpoint_identity(previous)
        events = list(checkpoint_events(previous))
        events.append(
            _event(
                "run_stopped" if cancelled else "run_finished",
                "Dry Run stopped" if cancelled else "Dry Run finished",
            )
        )
        save_checkpoint(
            _current_input_folder(),
            result["statistics"]["files"],
            checkpoint_items_map,
            status=status,
            run_id=previous.get("run_id"),
            review_decisions=_review_decisions,
            events=events,
            created_at=previous.get("created_at"),
            run_type=run_type,
        )
        _archive_checkpoint(load_checkpoint(), result=result)

        with _run_lock:
            _last_run = result
            _run_state["running"] = False
            _run_state["phase"] = "Stopped" if cancelled else "Finished"
            final_total = max(0, int(result["statistics"].get("files", 0) or 0))
            final_processed = max(0, int(result["statistics"].get("processed", 0) or 0))
            _run_state["total"] = final_total
            _run_state["current"] = (
                min(final_processed, final_total)
                if cancelled
                else final_total
            )
            _run_state["percent"] = (
                round((_run_state["current"] / final_total) * 100)
                if final_total
                else 100
            )
            _run_state["filename"] = ""
            _run_state["error"] = None
            _run_state["cancelled"] = cancelled

    except Exception as exc:
        with _run_lock:
            _run_state["running"] = False
            _run_state["phase"] = "Error"
            _run_state["error"] = str(exc)
            _run_state["cancelled"] = False


def rebuild_result_from_checkpoint(input_folder, checkpoint_items_map, review_decisions=None):
    results = {
        "AUTO": [],
        "REVIEW": [],
        "IGNORE": [],
        "ERROR": [],
    }
    destinations = {}
    review_decisions = review_decisions or {}

    for entry in checkpoint_items_map.values():
        item = entry.get("item") if isinstance(entry, dict) else None
        if not isinstance(item, dict):
            continue

        status = item.get("result", {}).get("status")
        if status not in results:
            continue

        # Manual decisions are persisted separately from the original
        # classifier result so the raw dry-run evidence is never overwritten.
        decision = review_decisions.get(item.get("filename"))
        item, status = _apply_persisted_decision(item, decision)
        if status not in results:
            continue

        results[status].append(item)

        if status == "AUTO" and item.get("destination"):
            destinations.setdefault(item["destination"], []).append(item["filename"])

    files = find_magazines(input_folder)
    collisions = {
        destination: filenames
        for destination, filenames in destinations.items()
        if len(filenames) > 1
    }

    statistics = {
        "files": len(files),
        "processed": sum(len(values) for values in results.values()),
        "auto": len(results["AUTO"]),
        "review": len(results["REVIEW"]),
        "ignore": len(results["IGNORE"]),
        "errors": len(results["ERROR"]),
        "unique_destinations": len(destinations),
        "collisions": len(collisions),
        "blocked_files": sum(len(filenames) for filenames in collisions.values()),
    }
    collision_details = build_collision_details(files, collisions)

    return {
        "input_folder": str(Path(input_folder)),
        "files": files,
        "results": results,
        "collisions": collisions,
        "collision_details": collision_details,
        "statistics": statistics,
    }


@app.post("/api/dry-run")
async def dry_run(new_run: bool = False, run_type: str = "dry_run", max_files: int = 0, files_override=None):
    global _last_run

    if run_type not in ("dry_run", "auto_run"):
        raise HTTPException(status_code=400, detail="Invalid run type")
    if max_files < 0 or max_files > 10000:
        raise HTTPException(status_code=400, detail="Invalid max_files value")

    selected_override = files_override
    if selected_override is None and max_files:
        selected_override = list(find_magazines(_current_input_folder()))[:max_files]

    with _run_lock:
        if _run_state["running"]:
            return {
                "started": False,
                "running": True,
            }

    if _get_apply_state()["running"]:
        raise HTTPException(status_code=409, detail="Apply Changes is currently running")

    with _run_lock:
        checkpoint = load_checkpoint()

        # "New Dry Run" deliberately starts a fresh run. The previous run
        # remains permanently available in History.
        if new_run:
            checkpoint = None
            clear_checkpoint()

        if checkpoint_matches_input(checkpoint, _current_input_folder()):
            checkpoint = ensure_checkpoint_identity(checkpoint)
        resume_items = (
            checkpoint_items(checkpoint)
            if checkpoint_matches_input(checkpoint, _current_input_folder())
            else {}
        )

        # A finished checkpoint is already a valid Dry Run result. Do not
        # make the user OCR the same files again just because the web app
        # restarted.
        if checkpoint and checkpoint.get("status") == "finished" and resume_items:
            _run_state["running"] = False
            _run_state["phase"] = "Finished (cached)"
            _run_state["current"] = checkpoint.get("total", len(resume_items))
            _run_state["total"] = checkpoint.get("total", len(resume_items))
            _run_state["percent"] = 100
            _run_state["filename"] = ""
            _run_state["error"] = None
            _run_state["cancelled"] = False

            _review_decisions.clear()
            _review_decisions.update(checkpoint_review_decisions(checkpoint))
            _last_run = rebuild_result_from_checkpoint(
                _current_input_folder(),
                resume_items,
                _review_decisions,
            )
            _archive_checkpoint(checkpoint, result=_last_run)

            return {
                "started": False,
                "running": False,
                "cached": True,
            }

        _last_run = None
        _review_decisions.clear()
        _cancel_event.clear()

        # A new run gets its own permanent identity. Resumed runs retain the
        # existing identity so their history remains one continuous run.
        if not resume_items:
            run_id = new_run_id()
            created_at = datetime.now(timezone.utc).isoformat()
            save_checkpoint(
                _current_input_folder(),
                len(selected_override) if selected_override is not None else len(find_magazines(_current_input_folder())),
                {},
                status="running",
                run_id=run_id,
                review_decisions={},
                events=[_event("run_started", "Dry Run started")],
                created_at=created_at,
            )
        _run_state["running"] = True
        _run_state["phase"] = "Starting / resuming" if resume_items else "Starting"
        _run_state["current"] = 0
        _run_state["total"] = checkpoint.get("total", 0) if checkpoint else 0
        _run_state["filename"] = ""
        _run_state["percent"] = 0
        _run_state["error"] = None
        _run_state["cancelled"] = False

    thread = Thread(
        target=_dry_run_worker,
        args=(resume_items, run_type, selected_override),
        daemon=True,
    )
    thread.start()

    return {
        "started": True,
        "running": True,
        "resuming": bool(resume_items),
    }


@app.post("/api/dry-run/stop")
async def stop_dry_run():
    with _run_lock:
        if not _run_state["running"]:
            return {
                "stopped": False,
                "running": False,
            }

        _cancel_event.set()
        _run_state["phase"] = "Stopping"
        _run_state["filename"] = "Finishing current file..."

    return {
        "stopped": True,
        "running": True,
    }


@app.get("/api/dry-run/status")
async def dry_run_status():
    with _run_lock:
        state = dict(_run_state)
        result = _last_run

    if result is None and not state["running"]:
        checkpoint = load_checkpoint()
        if checkpoint_matches_input(checkpoint, _current_input_folder()):
            items = checkpoint_items(checkpoint)
            if items:
                checkpoint = ensure_checkpoint_identity(checkpoint)
                _review_decisions.clear()
                _review_decisions.update(checkpoint_review_decisions(checkpoint))
                result = rebuild_result_from_checkpoint(
                    _current_input_folder(),
                    items,
                    _review_decisions,
                )
                _archive_checkpoint(checkpoint, result=result)
                state["phase"] = (
                    "Finished (cached)"
                    if checkpoint.get("status") == "finished"
                    else "Interrupted / resumable"
                )
                state["current"] = len(items)
                state["total"] = checkpoint.get("total", len(items))
                state["percent"] = round(
                    (len(items) / state["total"]) * 100
                ) if state["total"] else 0

    response = {
        "running": state["running"],
        "phase": state["phase"],
        "current": state["current"],
        "total": state["total"],
        "filename": state["filename"],
        "percent": state["percent"],
        "error": state["error"],
        "cancelled": state["cancelled"],
    }

    if not state["running"] and result is not None:
        statistics = dict(result["statistics"])
        applied_count = len(_applied_operations(result))
        apply_completed = any(
            event.get("type") == "apply_completed"
            for event in _apply_events(result)
        )
        statistics["applied_count"] = applied_count
        statistics["apply_completed"] = apply_completed
        response["statistics"] = statistics

        results = []

        collision_details = result.get("collision_details") or build_collision_details(
            result.get("files") or find_magazines(_current_input_folder()),
            result.get("collisions") or {},
        )
        for status in ["AUTO", "REVIEW", "IGNORE", "ERROR"]:
            for item in result["results"][status]:
                results.append(
                    format_result(item, collision_details)
                )

        response["results"] = results
        response["collisions"] = result["collisions"]
        response["collision_details"] = collision_details
        response["input_folder"] = result["input_folder"]

    return response


def _format_metadata_for_report(metadata):
    if not metadata:
        return "-"

    values = []

    for key in ["year", "month", "day", "issue", "week"]:
        value = metadata.get(key)
        if value is not None:
            values.append(f"{key}={value}")

    return ", ".join(values) if values else "-"


def build_dry_run_report(result):
    statistics = result["statistics"]

    lines = [
        "=" * 72,
        "MAGAZINE SORTER - DRY RUN REPORT",
        "=" * 72,
        "",
        "NO FILES WERE MOVED OR RENAMED.",
        "",
        f"Input folder: {result['input_folder']}",
        "",
        "SUMMARY",
        "-------",
        f"Files discovered:    {statistics['files']}",
        f"Files processed:     {statistics['processed']}",
        f"AUTO:                {statistics['auto']}",
        f"REVIEW:              {statistics['review']}",
        f"IGNORE:              {statistics['ignore']}",
        f"ERROR:               {statistics['errors']}",
        f"Unique destinations: {statistics['unique_destinations']}",
        f"Collisions:          {statistics['collisions']}",
        "",
    ]

    for status in ["AUTO", "REVIEW", "IGNORE", "ERROR"]:
        items = result["results"][status]

        lines.extend([
            "=" * 72,
            status,
            "=" * 72,
            "",
        ])

        if not items:
            lines.append("None")
            lines.append("")
            continue

        for item in items:
            r = item["result"]
            ocr = item.get("ocr")

            lines.extend([
                f"FILE: {item['filename']}",
                f"STATUS: {r.get('status')}",
                f"SOURCE: {item.get('source') or '-'}",
                f"PUBLICATION: {r.get('publication') or '-'}",
                f"FILENAME METADATA: {_format_metadata_for_report(r.get('metadata'))}",
                f"DESTINATION: {item.get('destination') or '-'}",
            ])

            if ocr:
                lines.extend([
                    f"OCR ACTION: {ocr.get('action') or '-'}",
                    f"OCR SCORE: {ocr.get('score') if ocr.get('score') is not None else '-'}",
                    f"OCR METADATA: {_format_metadata_for_report(ocr.get('ocr_metadata'))}",
                    f"MERGED METADATA: {_format_metadata_for_report(ocr.get('merged_metadata'))}",
                    f"OCR PAGES: {', '.join(str(p.get('page')) for p in ocr.get('ocr_pages', [])) or '-'}",
                ])

                reasons = ocr.get("reasons") or []
                if reasons:
                    lines.append("OCR REASONS:")
                    lines.extend(f"  - {reason}" for reason in reasons)

            if r.get("reason"):
                lines.append(f"REASON: {r['reason']}")

            lines.extend(["", "-" * 72, ""])

    if result["collisions"]:
        lines.extend([
            "=" * 72,
            "COLLISIONS",
            "=" * 72,
            "",
        ])

        for destination, filenames in result["collisions"].items():
            lines.append(f"DESTINATION: {destination}")
            for filename in filenames:
                lines.append(f"  - {filename}")
            lines.append("")

    return "\n".join(lines)


@app.get("/api/dry-run/report")
async def dry_run_report():
    result = get_last_run()

    if result is None:
        _, result = _load_persistent_current_run()

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="No completed dry run available",
        )

    report = build_dry_run_report(result)

    return PlainTextResponse(
        report,
        headers={
            "Content-Disposition": 'attachment; filename="magazine_sorter_dry_run_report.txt"'
        },
    )


# ---------------------------------------------------------------------------
# Run History
# ---------------------------------------------------------------------------

def _result_from_history_record(record):
    results = {
        "AUTO": [],
        "REVIEW": [],
        "IGNORE": [],
        "ERROR": [],
    }

    for entry in (record.get("items") or {}).values():
        item = entry.get("item") if isinstance(entry, dict) else None
        if not isinstance(item, dict):
            continue
        status = item.get("result", {}).get("status")
        if status in results:
            results[status].append(item)

    destinations = {}
    for item in results["AUTO"]:
        destination = item.get("destination")
        if destination:
            destinations.setdefault(destination, []).append(item["filename"])

    collisions = {
        destination: filenames
        for destination, filenames in destinations.items()
        if len(filenames) > 1
    }

    # Use the stored snapshot. The original input folder may now contain
    # fewer files because Apply Changes or another run moved them.
    stored_stats = record.get("statistics") or {}
    statistics = {
        "files": stored_stats.get("files", record.get("total", sum(len(v) for v in results.values()))),
        "processed": stored_stats.get("processed", sum(len(v) for v in results.values())),
        "auto": stored_stats.get("auto", len(results["AUTO"])),
        "review": stored_stats.get("review", len(results["REVIEW"])),
        "ignore": stored_stats.get("ignore", len(results["IGNORE"])),
        "errors": stored_stats.get("errors", len(results["ERROR"])),
        "unique_destinations": stored_stats.get("unique_destinations", len(destinations)),
        "collisions": stored_stats.get("collisions", len(collisions)),
        "blocked_files": stored_stats.get("blocked_files", sum(len(filenames) for filenames in collisions.values())),
    }

    return {
        "input_folder": record.get("input_folder") or "",
        "files": [],
        "results": results,
        "collisions": collisions,
        "statistics": statistics,
    }


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="history.html",
        context={
            "history_count": len(list_history_runs()),
        },
    )


@app.get("/api/history")
async def history_api():
    # History is a read-only view. Runs are archived when they are created or
    # when Review changes them; do not archive/rewrite the current checkpoint
    # just because the History page is opened.
    items = list_history_runs()
    return {
        "count": len(items),
        "items": items,
    }


def _history_ui_states(record):
    states = {}
    operations = _applied_operations(record)
    outcomes = _operation_outcomes(record)
    for fingerprint, entry in (record.get("items") or {}).items():
        item = entry.get("item") if isinstance(entry, dict) else None
        if not isinstance(item, dict):
            continue
        raw_path = fingerprint.rsplit("|", 2)[0] if "|" in fingerprint else ""
        path = Path(raw_path) if raw_path else None
        outcome = outcomes.get(("fingerprint", str(fingerprint))) or outcomes.get(("filename", str(item.get("filename") or "")))
        operation_status = ""
        operation_reason = ""
        if outcome:
            event_type = outcome.get("type")
            if event_type == "apply_moved":
                operation_status = "APPLIED"
            elif event_type == "undo_moved":
                operation_status = "UNDONE"
            elif event_type == "undo_blocked":
                operation_status = "UNDO_BLOCKED"
                operation_reason = str(outcome.get("reason") or "")
            elif event_type == "undo_error":
                operation_status = "UNDO_ERROR"
                operation_reason = str(outcome.get("reason") or "")
        states[fingerprint] = {
            "rerunnable": bool(path and path.is_file()),
            "undoable": fingerprint in operations,
            "operation_status": operation_status,
            "operation_reason": operation_reason,
        }
    return states


@app.get("/api/history/{run_id}")
async def history_item(run_id: str):
    record = load_history_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")
    response = dict(record)
    response["_ui_states"] = _history_ui_states(record)
    return response


def _history_item_source_path(fingerprint, item):
    # Fingerprints are stored as <absolute path>|<size>|<mtime_ns>.
    if fingerprint and "|" in fingerprint:
        return Path(fingerprint.rsplit("|", 2)[0])
    source = item.get("path") if isinstance(item, dict) else None
    return Path(source) if source else None


def _history_items_for_rerun(record, fingerprints):
    stored = record.get("items") or {}
    requested = set(fingerprints or [])
    if not requested:
        raise HTTPException(status_code=400, detail="Select at least one file to re-run.")

    selected = []
    unavailable = []
    for fingerprint in requested:
        entry = stored.get(fingerprint)
        if not isinstance(entry, dict) or not isinstance(entry.get("item"), dict):
            unavailable.append(fingerprint)
            continue
        item = entry["item"]
        path = _history_item_source_path(fingerprint, item)
        if path is None or not path.exists() or not path.is_file():
            unavailable.append(item.get("filename") or fingerprint)
            continue
        try:
            from src.models import MagazineFile
            selected.append(
                MagazineFile(
                    path=path,
                    filename=path.name,
                    extension=path.suffix.lower(),
                    parent_folder=path.parent.name,
                )
            )
        except OSError:
            unavailable.append(item.get("filename") or fingerprint)

    return selected, unavailable


@app.post("/api/history/{run_id}/rerun")
async def rerun_history(run_id: str, payload: dict):
    record = load_history_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")

    with _run_lock:
        if _run_state["running"]:
            raise HTTPException(status_code=409, detail="A Dry Run is already running.")

        fingerprints = payload.get("fingerprints") if isinstance(payload, dict) else None
        selected_files, unavailable = _history_items_for_rerun(record, fingerprints)
        if not selected_files:
            detail = "None of the selected files are currently available at their original locations."
            if unavailable:
                detail += ""
            raise HTTPException(status_code=409, detail=detail)

        _last_run = None
        _review_decisions.clear()
        _cancel_event.clear()

        run_id_new = new_run_id()
        created_at = datetime.now(timezone.utc).isoformat()
        input_folder = Path(record.get("input_folder") or _current_input_folder())
        save_checkpoint(
            input_folder,
            len(selected_files),
            {},
            status="running",
            run_id=run_id_new,
            review_decisions={},
            run_type="history_rerun",
            events=[_event(
                "run_started",
                "Re-run started from History",
                parent_run_id=run_id,
                selected_count=len(selected_files),
            )],
            created_at=created_at,
        )

        _run_state["running"] = True
        _run_state["phase"] = "Starting re-run"
        _run_state["current"] = 0
        _run_state["total"] = len(selected_files)
        _run_state["filename"] = ""
        _run_state["percent"] = 0
        _run_state["error"] = None
        _run_state["cancelled"] = False

    def rerun_worker():
        global _last_run
        input_folder_local = input_folder
        settings_snapshot = get_effective_settings()
        checkpoint_items_map = {}

        def completed_callback(fingerprint, item):
            checkpoint_items_map[fingerprint] = {"item": item}
            checkpoint = load_checkpoint() or {}
            checkpoint = ensure_checkpoint_identity(checkpoint)
            save_checkpoint(
                input_folder_local,
                _run_state["total"],
                checkpoint_items_map,
                status="running",
                run_id=checkpoint.get("run_id"),
                review_decisions={},
                events=checkpoint_events(checkpoint),
                created_at=checkpoint.get("created_at"),
            )

        try:
            result = run_gui_dry_run(
                input_folder_local,
                progress_callback=_update_run_state,
                cancel_check=_cancel_event.is_set,
                resume_items={},
                completed_callback=completed_callback,
                ocr_settings=settings_snapshot.get("ocr", {}),
                files_override=selected_files,
            )
            cancelled = _cancel_event.is_set()
            checkpoint = load_checkpoint() or {}
            checkpoint = ensure_checkpoint_identity(checkpoint)
            events = list(checkpoint_events(checkpoint))
            events.append(_event(
                "run_stopped" if cancelled else "run_finished",
                "Re-run stopped" if cancelled else "Re-run finished",
                parent_run_id=run_id,
            ))
            save_checkpoint(
                input_folder_local,
                len(selected_files),
                checkpoint_items_map,
                status="stopped" if cancelled else "finished",
                run_id=checkpoint.get("run_id"),
                review_decisions={},
                events=events,
                created_at=checkpoint.get("created_at"),
                run_type="history_rerun",
            )
            _last_run = result
            refreshed = load_checkpoint() or checkpoint
            _archive_checkpoint(refreshed, result=result)
            with _run_lock:
                _run_state["running"] = False
                _run_state["phase"] = "Stopped" if cancelled else "Finished"
                _run_state["current"] = len(selected_files)
                _run_state["total"] = len(selected_files)
                _run_state["percent"] = 100 if selected_files else 0
                _run_state["filename"] = ""
                _run_state["cancelled"] = cancelled
        except Exception as exc:
            with _run_lock:
                _run_state["running"] = False
                _run_state["phase"] = "Error"
                _run_state["error"] = str(exc)
                _run_state["cancelled"] = False

    Thread(target=rerun_worker, daemon=True).start()

    return {
        "started": True,
        "run_id": run_id_new,
        "selected": len(selected_files),
        "unavailable": unavailable,
        "parent_run_id": run_id,
    }


@app.get("/api/history/{run_id}/report")
async def history_report(run_id: str):
    record = load_history_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")

    result = _result_from_history_record(record)
    report = build_dry_run_report(result)
    return PlainTextResponse(
        report,
        headers={
            "Content-Disposition": f'attachment; filename="magazine_sorter_{run_id}.txt"'
        },
    )


# ---------------------------------------------------------------------------
# Publications
# ---------------------------------------------------------------------------


def _unknown_publication_key(filename):
    """Create a conservative comparison key for unknown publication names.

    This is GUI discovery only. It does not change the parser/classifier and
    it never creates a profile automatically.
    """
    stem = Path(filename).stem
    text = stem.replace("_", " ").replace(".", " ").replace("-", " ")
    text = re.sub(r"\b(?:danish|web|dl|pdf|maggi|ebook|iversen\d*)\b", " ", text, flags=re.I)
    text = re.sub(r"\b(?:nr|no)\.?\s*\d{1,3}\b", " ", text, flags=re.I)
    text = re.sub(r"(?<!\d)\d{1,3}[./_-]\d{1,2}[./_-]20\d{2}(?!\d)", " ", text)
    text = re.sub(r"(?<!\d)20\d{2}[./_-]\d{1,2}(?!\d)", " ", text)
    text = re.sub(r"(?<!\d)\d{1,2}[./_-]20\d{2}(?!\d)", " ", text)
    text = re.sub(r"(?<!\d)\d{6,8}(?!\d)", " ", text)
    text = re.sub(r"\b20\d{2}\b", " ", text)
    text = re.sub(r"\b\d{1,3}\b", " ", text)
    text = re.sub(r"[^\wæøåÆØÅ]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip().lower()


def _suggested_publication_name(filename):
    stem = Path(filename).stem
    text = stem.replace("_", " ").replace(".", " ").replace("-", " ")
    text = re.sub(r"\b(?:danish|web|dl|pdf|maggi|ebook|iversen\d*)\b", " ", text, flags=re.I)
    text = re.sub(r"\b(?:nr|no)\.?\s*\d{1,3}\b", " ", text, flags=re.I)
    text = re.sub(r"(?<!\d)\d{1,3}[./_-]\d{1,2}[./_-]20\d{2}(?!\d)", " ", text)
    text = re.sub(r"(?<!\d)20\d{2}[./_-]\d{1,2}(?!\d)", " ", text)
    text = re.sub(r"(?<!\d)\d{1,2}[./_-]20\d{2}(?!\d)", " ", text)
    text = re.sub(r"(?<!\d)\d{6,8}(?!\d)", " ", text)
    text = re.sub(r"\b20\d{2}\b", " ", text)
    text = re.sub(r"\b\d{1,3}\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" ._-\t")
    return text


def discover_unknown_publications():
    """Find conservative groups among PUBLICATION_UNKNOWN review files.

    Groups are suggestions only. A group is shown as a possible new
    publication when at least two unknown files have the same normalized
    publication-name key. Similar-but-not-identical names are intentionally
    not merged automatically.
    """
    run = get_last_run()
    if run is None:
        return {"groups": [], "singles": [], "has_run": False}

    unknown = []
    for item in run["results"].get("REVIEW", []):
        result = item.get("result", {})
        if result.get("reason") != "PUBLICATION_UNKNOWN":
            continue
        unknown.append(item)

    buckets = {}
    for item in unknown:
        key = _unknown_publication_key(item["filename"])
        if not key:
            key = f"__single__:{item['filename']}"
        buckets.setdefault(key, []).append(item)

    groups = []
    singles = []
    for key, items in buckets.items():
        if key.startswith("__single__") or len(items) < 2:
            singles.extend(items)
            continue
        groups.append(
            {
                "suggested_name": _suggested_publication_name(items[0]["filename"]),
                "count": len(items),
                "files": [item["filename"] for item in items],
                "example_filename": items[0]["filename"],
            }
        )

    # Keep discovery conservative: only exact normalized-name groups are
    # promoted to "possible new publication". Similar singleton names remain
    # individual unknown files rather than being guessed together.
    groups.sort(key=lambda item: item["suggested_name"].lower())
    singles.sort(key=lambda item: item["filename"].lower())

    return {
        "groups": groups,
        "singles": [
            {
                "filename": item["filename"],
                "suggested_name": _suggested_publication_name(item["filename"]),
            }
            for item in singles
        ],
        "has_run": True,
    }


@app.get("/publications", response_class=HTMLResponse)
async def publications_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="publications.html",
        context={
            "publication_count": len(list_publications()),
        },
    )


@app.get("/api/publications/discover")
async def publications_discover_api():
    return discover_unknown_publications()


@app.get("/api/publications")
async def publications_api():
    publications = list_publications()
    return {
        "count": len(publications),
        "items": publications,
    }


@app.post("/api/publications/learn-context")
async def publication_learn_context(payload: dict):
    """Prepare the Publications form from a Review file.

    This is deliberately suggestion-only: it derives a human-friendly
    publication name from the filename and reuses the existing metadata
    parser. It never creates or changes a publication profile by itself.
    """
    filename = str(payload.get("filename") or "").strip()
    supplied_name = str(payload.get("publication_name") or "").strip()

    if not filename:
        raise HTTPException(
            status_code=400,
            detail="Filename is required",
        )

    publication_name = supplied_name or _suggested_publication_name(filename)
    suggestion = suggest_profile(
        filename,
        publication_name=publication_name,
    )

    return {
        "filename": filename,
        "publication_name": publication_name,
        "metadata": suggestion.get("metadata") or {},
        "detected": suggestion.get("detected") or [],
        "type": suggestion.get("type"),
        "type_label": suggestion.get("type_label"),
        "include_year": suggestion.get("include_year", True),
        "confidence": suggestion.get("confidence"),
    }


@app.post("/api/publications/suggest")
async def publication_suggest(payload: dict):
    filename = str(payload.get("filename") or "")
    publication_name = str(payload.get("publication_name") or "")

    if not filename.strip():
        raise HTTPException(
            status_code=400,
            detail="Example filename is required",
        )

    return suggest_profile(
        filename,
        publication_name=publication_name,
    )


@app.post("/api/publications/update")
async def publication_update(payload: dict):
    old_name = str(payload.get("old_name") or "").strip()
    name = str(payload.get("name") or "").strip()
    aliases = payload.get("aliases") or []
    profile_type = str(payload.get("type") or "issue")
    include_year = bool(payload.get("include_year", True))

    if not isinstance(aliases, list):
        aliases = [str(aliases)]

    try:
        publication = update_publication(
            old_name=old_name,
            name=name,
            aliases=[str(alias) for alias in aliases],
            profile_type=profile_type,
            include_year=include_year,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not update publication: {exc}",
        )

    return {
        "success": True,
        "publication": publication,
        "message": "Publication updated.",
    }


@app.post("/api/publications")
async def publication_create(payload: dict):
    name = str(payload.get("name") or "").strip()
    aliases = payload.get("aliases") or []
    profile_type = str(payload.get("type") or "issue")
    include_year = bool(payload.get("include_year", True))

    if not isinstance(aliases, list):
        aliases = [str(aliases)]

    try:
        publication = add_publication(
            name=name,
            aliases=[str(alias) for alias in aliases],
            profile_type=profile_type,
            include_year=include_year,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        # Always return JSON so the GUI can show the real validation/storage
        # error instead of failing while trying to parse an HTML 500 page.
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )
    except Exception as exc:
        # Unexpected errors are still surfaced as JSON.  Do not expose a
        # traceback to the browser, but make the actual exception useful for
        # diagnosing the local installation.
        raise HTTPException(
            status_code=500,
            detail=f"Could not save publication: {exc}",
        )

    return {
        "success": True,
        "publication": publication,
        "message": "Publication learned. The server may reload automatically.",
    }


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------

def _review_path_for_item(item):
    run = get_last_run()
    if run is None:
        return None

    for file in run.get("files", []):
        if file.filename == item["filename"]:
            return Path(file.path).resolve()

    return None


def _find_review_item(index):
    review_items = get_review_items()

    if index < 0 or index >= len(review_items):
        raise HTTPException(
            status_code=404,
            detail="Review item not found",
        )

    return review_items[index]


def _rebuild_result_after_review_decisions():
    """Rebuild the current in-memory run from the raw checkpoint plus decisions."""
    global _last_run

    run = get_last_run()
    if run is None:
        return

    checkpoint = load_checkpoint()
    if checkpoint and checkpoint_matches_input(checkpoint, _current_input_folder()):
        _last_run = rebuild_result_from_checkpoint(
            _current_input_folder(),
            checkpoint_items(checkpoint),
            _review_decisions,
        )
        return

    # Fallback for legacy in-memory runs without a checkpoint.
    original_items = []
    for status in ["AUTO", "REVIEW", "IGNORE", "ERROR"]:
        original_items.extend(run["results"].get(status, []))

    results = {"AUTO": [], "REVIEW": [], "IGNORE": [], "ERROR": []}
    destinations = {}
    for item in original_items:
        decision = _review_decisions.get(item.get("filename"))
        resolved, status = _apply_persisted_decision(item, decision)
        if status not in results:
            continue
        results[status].append(resolved)
        if status == "AUTO" and resolved.get("destination"):
            destinations.setdefault(resolved["destination"], []).append(resolved["filename"])

    run["results"] = results
    run["collisions"] = {
        destination: filenames
        for destination, filenames in destinations.items()
        if len(filenames) > 1
    }
    run["statistics"]["auto"] = len(results["AUTO"])
    run["statistics"]["review"] = len(results["REVIEW"])
    run["statistics"]["ignore"] = len(results["IGNORE"])
    run["statistics"]["errors"] = len(results["ERROR"])
    run["statistics"]["collisions"] = len(run["collisions"])
    run["statistics"]["blocked_files"] = sum(len(v) for v in run["collisions"].values())


@app.get("/review", response_class=HTMLResponse)
async def review_page(request: Request):
    review_items = get_review_items()

    return templates.TemplateResponse(
        request=request,
        name="review.html",
        context={
            "review_count": len(review_items),
        },
    )


@app.get("/api/review")
async def review_queue():
    review_items = get_review_items()

    results = []

    for index, item in enumerate(review_items):
        result = item["result"]

        results.append(
            {
                "index": index,
                "filename": item["filename"],
                "extension": item["extension"],
                "publication": result.get("publication"),
                "metadata": result.get("metadata", {}),
                "reason": result.get("reason"),
                "source": item.get("source"),
            }
        )

    return {
        "count": len(results),
        "items": results,
    }


@app.get("/api/review/{index}")
async def review_item(index: int):
    item = _find_review_item(index)
    result = item["result"]

    publication = result.get("publication")
    publications = list_publications()
    selected_profile = next(
        (profile for profile in publications if profile["name"] == publication),
        None,
    )

    return {
        "index": index,
        "filename": item["filename"],
        "extension": item["extension"],
        "publication": publication,
        "metadata": result.get("metadata", {}),
        "reason": result.get("reason"),
        "source": item.get("source"),
        "pdf_url": f"/api/review/{index}/file",
        "publication_profile": selected_profile,
        "publications": publications,
    }


@app.get("/api/review/{index}/file")
async def review_file(index: int):
    item = _find_review_item(index)
    path = _review_path_for_item(item)

    if path is None:
        raise HTTPException(status_code=404, detail="File not found")

    input_root = _current_input_folder().resolve()

    try:
        path.relative_to(input_root)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail="File is outside the configured input folder",
        )

    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    media_type = "application/pdf"
    if path.suffix.lower() == ".cbz":
        media_type = "application/zip"

    return FileResponse(
        path=str(path),
        media_type=media_type,
        headers={
            "Content-Disposition": f'inline; filename="{path.name}"',
        },
    )


@app.post("/api/review/{index}/ocr")
async def review_ocr(index: int):
    item = _find_review_item(index)
    path = _review_path_for_item(item)

    if path is None:
        raise HTTPException(status_code=404, detail="File not found")

    if path.suffix.lower() != ".pdf":
        raise HTTPException(
            status_code=400,
            detail="OCR is currently supported for PDF files only",
        )

    try:
        settings = get_effective_settings()
        analysis = analyze_file(
            path,
            language=settings["ocr"]["language"],
            dpi=int(settings["ocr"]["dpi"]),
            page_stages=settings["ocr"]["page_stages"],
            ocr_enabled=True,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"OCR failed: {exc}",
        )

    publication = analysis["publication"]
    metadata = analysis["merged_metadata"]
    destination = get_destination(publication, metadata)

    return {
        "filename": analysis["filename"],
        "publication": publication,
        "filename_metadata": analysis["filename_metadata"],
        "ocr_metadata": analysis["ocr_metadata"],
        "merged_metadata": analysis["merged_metadata"],
        "ocr_pages": analysis["ocr_pages"],
        "score": analysis["score"],
        "action": analysis["action"],
        "reasons": analysis["reasons"],
        "destination": destination,
    }


@app.post("/api/review/{index}/approve")
async def review_approve(index: int, payload: dict):
    item = _find_review_item(index)

    publication = str(
        payload.get("publication")
        or item["result"].get("publication")
        or ""
    ).strip()

    raw_metadata = payload.get("metadata") or item["result"].get("metadata") or {}
    metadata = {
        "year": raw_metadata.get("year"),
        "month": raw_metadata.get("month"),
        "day": raw_metadata.get("day"),
        "issue": raw_metadata.get("issue"),
        "week": raw_metadata.get("week"),
    }

    if not publication:
        raise HTTPException(
            status_code=400,
            detail="Publication is required before approving",
        )

    if not any(value is not None for value in metadata.values()):
        raise HTTPException(
            status_code=400,
            detail="At least one metadata value is required",
        )

    destination = get_destination(publication, metadata)
    if destination in {None, "_REVIEW"}:
        raise HTTPException(
            status_code=400,
            detail="The supplied metadata is not enough to build a safe destination",
        )

    _review_decisions[item["filename"]] = {
        "action": "AUTO",
        "publication": publication,
        "metadata": metadata,
    }

    _rebuild_result_after_review_decisions()
    checkpoint = load_checkpoint()
    if checkpoint:
        checkpoint = ensure_checkpoint_identity(checkpoint)
        events = list(checkpoint_events(checkpoint))
        events.append(_event(
            "review_approved",
            "File approved manually in Review",
            filename=item["filename"],
            publication=publication,
            metadata=metadata,
        ))
        save_checkpoint(
            _current_input_folder(),
            checkpoint.get("total", 0),
            checkpoint_items(checkpoint),
            status=checkpoint.get("status", "finished"),
            run_id=checkpoint.get("run_id"),
            review_decisions=_review_decisions,
            events=events,
            created_at=checkpoint.get("created_at"),
        )
        _archive_checkpoint(load_checkpoint(), result=get_last_run())

    return {
        "success": True,
        "message": "Approved in dry-run. No file was moved or renamed.",
    }


@app.post("/api/review/{index}/classify")
async def review_classify(index: int, payload: dict):
    """Apply an explicit Special or Standalone decision in Review.

    The decision only changes the current dry-run plan. Nothing is moved or
    renamed until the normal Apply Changes step is confirmed.
    """
    item = _find_review_item(index)
    action = str(payload.get("action") or "").strip().upper()

    if action not in {"SPECIAL", "STANDALONE"}:
        raise HTTPException(status_code=400, detail="Unsupported Review classification.")

    extension = Path(item["filename"]).suffix or ".pdf"

    if action == "SPECIAL":
        publication = str(payload.get("publication") or item["result"].get("publication") or "").strip()
        title = str(payload.get("title") or "").strip()
        raw_year = payload.get("year")
        year = None
        if raw_year not in (None, ""):
            try:
                year = int(raw_year)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Year must be a number.")
            if year < 1900 or year > 2100:
                raise HTTPException(status_code=400, detail="Year must be between 1900 and 2100.")

        if not publication:
            raise HTTPException(status_code=400, detail="Publication is required for a Special.")
        if not title:
            raise HTTPException(status_code=400, detail="Special title is required.")

        destination = build_special_destination(
            publication, title, year, extension=extension
        )
        if destination == "_REVIEW":
            raise HTTPException(status_code=400, detail="The Special destination could not be built safely.")

        decision = {
            "action": "SPECIAL",
            "publication": publication,
            "title": title,
            "year": year,
            "metadata": {"year": year},
        }
        message = "Classified as Special. No file was moved or renamed."
    else:
        filename = str(payload.get("filename") or item["filename"]).strip()
        if not filename:
            raise HTTPException(status_code=400, detail="Standalone filename is required.")
        if Path(filename).name != filename:
            raise HTTPException(status_code=400, detail="Standalone filename must not contain folders.")

        destination = build_standalone_destination(filename)
        if destination == "_REVIEW":
            raise HTTPException(status_code=400, detail="The Standalone destination could not be built safely.")

        decision = {
            "action": "STANDALONE",
            "publication": None,
            "filename": filename,
            "oneshots_dir": "_oneshots",
            "metadata": {},
        }
        message = "Classified as Standalone. No file was moved or renamed."

    _review_decisions[item["filename"]] = decision
    _rebuild_result_after_review_decisions()

    checkpoint = load_checkpoint()
    if checkpoint:
        checkpoint = ensure_checkpoint_identity(checkpoint)
        events = list(checkpoint_events(checkpoint))
        events.append(_event(
            "review_classified",
            message,
            filename=item["filename"],
            action=action,
            destination=destination,
            publication=decision.get("publication"),
            title=decision.get("title"),
        ))
        save_checkpoint(
            _current_input_folder(),
            checkpoint.get("total", 0),
            checkpoint_items(checkpoint),
            status=checkpoint.get("status", "finished"),
            run_id=checkpoint.get("run_id"),
            review_decisions=_review_decisions,
            events=events,
            created_at=checkpoint.get("created_at"),
        )
        _archive_checkpoint(load_checkpoint(), result=get_last_run())

    return {
        "success": True,
        "message": message,
        "destination": destination,
    }


@app.post("/api/result/reclassify")
async def result_reclassify(payload: dict):
    """Reclassify a current BLOCKED or IGNORE result without moving files."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid request payload.")

    filename = str(payload.get("filename") or "").strip()
    action = str(payload.get("action") or "").strip().upper()
    if not filename:
        raise HTTPException(status_code=400, detail="Filename is required.")
    if action not in {"SPECIAL", "STANDALONE"}:
        raise HTTPException(status_code=400, detail="Unsupported reclassification.")

    run = get_last_run()
    if run is None:
        raise HTTPException(status_code=404, detail="No current run available.")

    item = None
    for status in ["AUTO", "REVIEW", "IGNORE", "ERROR"]:
        for candidate in run["results"].get(status, []):
            if candidate.get("filename") == filename:
                item = candidate
                break
        if item is not None:
            break

    if item is None:
        raise HTTPException(status_code=404, detail="File not found in current run.")

    extension = Path(filename).suffix or ".pdf"
    if action == "SPECIAL":
        publication = str(payload.get("publication") or item.get("result", {}).get("publication") or "").strip()
        title = str(payload.get("title") or "").strip()
        raw_year = payload.get("year")
        year = None
        if raw_year not in (None, ""):
            try:
                year = int(raw_year)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Year must be a number.")
            if year < 1900 or year > 2100:
                raise HTTPException(status_code=400, detail="Year must be between 1900 and 2100.")
        if not publication:
            raise HTTPException(status_code=400, detail="Publication is required for a Special.")
        if not title:
            raise HTTPException(status_code=400, detail="Special title is required.")
        destination = build_special_destination(publication, title, year, extension=extension)
        if destination == "_REVIEW":
            raise HTTPException(status_code=400, detail="The Special destination could not be built safely.")
        decision = {
            "action": "SPECIAL",
            "publication": publication,
            "title": title,
            "year": year,
            "metadata": {"year": year},
        }
        message = "Reclassified as Special. No file was moved or renamed."
    else:
        standalone_filename = str(payload.get("standalone_filename") or filename).strip()
        if not standalone_filename or Path(standalone_filename).name != standalone_filename:
            raise HTTPException(status_code=400, detail="Standalone filename must be a filename only.")
        destination = build_standalone_destination(standalone_filename)
        if destination == "_REVIEW":
            raise HTTPException(status_code=400, detail="The Standalone destination could not be built safely.")
        decision = {
            "action": "STANDALONE",
            "publication": None,
            "filename": standalone_filename,
            "oneshots_dir": "_oneshots",
            "metadata": {},
        }
        message = "Reclassified as Standalone. No file was moved or renamed."

    _review_decisions[filename] = decision
    _rebuild_result_after_review_decisions()

    checkpoint = load_checkpoint()
    if checkpoint:
        checkpoint = ensure_checkpoint_identity(checkpoint)
        events = list(checkpoint_events(checkpoint))
        events.append(_event(
            "result_reclassified",
            message,
            filename=filename,
            action=action,
            destination=destination,
            publication=decision.get("publication"),
            title=decision.get("title"),
        ))
        save_checkpoint(
            _current_input_folder(),
            checkpoint.get("total", 0),
            checkpoint_items(checkpoint),
            status=checkpoint.get("status", "finished"),
            run_id=checkpoint.get("run_id"),
            review_decisions=_review_decisions,
            events=events,
            created_at=checkpoint.get("created_at"),
        )
        _archive_checkpoint(load_checkpoint(), result=get_last_run())

    return {
        "success": True,
        "message": message,
        "destination": destination,
    }


@app.post("/api/review/{index}/ignore")
async def review_ignore(index: int):
    item = _find_review_item(index)

    _review_decisions[item["filename"]] = {
        "action": "IGNORE",
    }

    _rebuild_result_after_review_decisions()
    checkpoint = load_checkpoint()
    if checkpoint:
        checkpoint = ensure_checkpoint_identity(checkpoint)
        events = list(checkpoint_events(checkpoint))
        events.append(_event(
            "review_ignored",
            "File ignored manually in Review",
            filename=item["filename"],
        ))
        save_checkpoint(
            _current_input_folder(),
            checkpoint.get("total", 0),
            checkpoint_items(checkpoint),
            status=checkpoint.get("status", "finished"),
            run_id=checkpoint.get("run_id"),
            review_decisions=_review_decisions,
            events=events,
            created_at=checkpoint.get("created_at"),
        )
        _archive_checkpoint(load_checkpoint(), result=get_last_run())

    return {
        "success": True,
        "message": "Ignored in dry-run. No file was moved or renamed.",
    }


@app.post("/api/review/bulk-ignore")
async def review_bulk_ignore(payload: dict):
    """Ignore multiple currently queued Review files in one explicit action.

    This only changes Review decisions in the current dry-run state. It never
    moves or renames files. Filenames are used instead of mutable queue indexes
    so the operation remains stable while the queue changes.
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid request payload.")

    requested = payload.get("filenames") or []
    if not isinstance(requested, list):
        raise HTTPException(status_code=400, detail="Filenames must be a list.")

    requested = [str(value).strip() for value in requested if str(value).strip()]
    requested = list(dict.fromkeys(requested))
    if not requested:
        raise HTTPException(status_code=400, detail="Select at least one Review file.")

    review_items = get_review_items()
    available_by_name = {item.get("filename"): item for item in review_items}
    selected = [available_by_name[name] for name in requested if name in available_by_name]

    if not selected:
        raise HTTPException(status_code=409, detail="None of the selected files are still in the Review queue.")

    for item in selected:
        _review_decisions[item["filename"]] = {
            "action": "IGNORE",
        }

    _rebuild_result_after_review_decisions()
    checkpoint = load_checkpoint()
    if checkpoint:
        checkpoint = ensure_checkpoint_identity(checkpoint)
        events = list(checkpoint_events(checkpoint))
        events.append(_event(
            "review_bulk_ignored",
            "Multiple files ignored in Review",
            filenames=[item["filename"] for item in selected],
            selected_count=len(selected),
        ))
        save_checkpoint(
            _current_input_folder(),
            checkpoint.get("total", 0),
            checkpoint_items(checkpoint),
            status=checkpoint.get("status", "finished"),
            run_id=checkpoint.get("run_id"),
            review_decisions=_review_decisions,
            events=events,
            created_at=checkpoint.get("created_at"),
        )
        _archive_checkpoint(load_checkpoint(), result=get_last_run())

    return {
        "success": True,
        "ignored": len(selected),
        "skipped": len(requested) - len(selected),
        "message": "Ignored %d file(s) in dry-run. No file was moved or renamed." % len(selected),
    }


@app.post("/api/review/bulk-approve")
async def review_bulk_approve(payload: dict):
    """Approve selected Review files only when their current result already
    contains enough information to build a safe destination.

    This intentionally mirrors the validation used by the single-file
    approval endpoint. It never moves or renames files.
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid request payload.")

    requested = payload.get("filenames") or []
    if not isinstance(requested, list):
        raise HTTPException(status_code=400, detail="Filenames must be a list.")

    requested = [str(value).strip() for value in requested if str(value).strip()]
    requested = list(dict.fromkeys(requested))
    if not requested:
        raise HTTPException(status_code=400, detail="Select at least one Review file.")

    review_items = get_review_items()
    available_by_name = {item.get("filename"): item for item in review_items}
    selected = [available_by_name[name] for name in requested if name in available_by_name]

    if not selected:
        raise HTTPException(
            status_code=409,
            detail="None of the selected files are still in the Review queue.",
        )

    approved = []
    skipped = []

    for item in selected:
        result = item.get("result") or {}
        publication = str(result.get("publication") or "").strip()
        metadata = {
            "year": (result.get("metadata") or {}).get("year"),
            "month": (result.get("metadata") or {}).get("month"),
            "day": (result.get("metadata") or {}).get("day"),
            "issue": (result.get("metadata") or {}).get("issue"),
            "week": (result.get("metadata") or {}).get("week"),
        }

        if not publication:
            skipped.append({"filename": item["filename"], "reason": "Publication is required"})
            continue

        if not any(value is not None for value in metadata.values()):
            skipped.append({"filename": item["filename"], "reason": "Missing metadata"})
            continue

        destination = get_destination(publication, metadata)
        if destination in {None, "_REVIEW"}:
            skipped.append({"filename": item["filename"], "reason": "Metadata is not enough for a safe destination"})
            continue

        _review_decisions[item["filename"]] = {
            "action": "AUTO",
            "publication": publication,
            "metadata": metadata,
        }
        approved.append(item["filename"])

    if approved:
        _rebuild_result_after_review_decisions()
        checkpoint = load_checkpoint()
        if checkpoint:
            checkpoint = ensure_checkpoint_identity(checkpoint)
            events = list(checkpoint_events(checkpoint))
            for filename in approved:
                item = available_by_name[filename]
                result = item.get("result") or {}
                events.append(_event(
                    "review_approved",
                    "File approved in Review bulk action",
                    filename=filename,
                    publication=result.get("publication"),
                    metadata=result.get("metadata") or {},
                ))
            events.append(_event(
                "review_bulk_approved",
                "Multiple files approved in Review",
                filenames=approved,
                approved_count=len(approved),
                skipped_count=len(skipped),
            ))
            save_checkpoint(
                _current_input_folder(),
                checkpoint.get("total", 0),
                checkpoint_items(checkpoint),
                status=checkpoint.get("status", "finished"),
                run_id=checkpoint.get("run_id"),
                review_decisions=_review_decisions,
                events=events,
                created_at=checkpoint.get("created_at"),
            )
            _archive_checkpoint(load_checkpoint(), result=get_last_run())

    return {
        "success": True,
        "approved": len(approved),
        "skipped": skipped,
        "message": (
            f"Approved {len(approved)} file(s)."
            if not skipped
            else f"Approved {len(approved)} file(s); skipped {len(skipped)} file(s) because they were not ready for safe approval."
        ),
    }


# ---------------------------------------------------------------------------
# Apply Changes
# ---------------------------------------------------------------------------


def _apply_output_folder() -> Path:
    return _current_output_folder().resolve()


def _item_source_path(item) -> Optional[Path]:
    # Persisted items now carry their exact source path. This is important
    # for nested input folders and after Apply has removed the source file.
    stored_path = item.get("path") if isinstance(item, dict) else None
    if stored_path:
        return Path(stored_path).resolve()

    run = get_last_run()
    if run is None:
        return None

    for file in run.get("files", []):
        if file.filename == item.get("filename"):
            return Path(file.path).resolve()

    # Persistent history/checkpoint runs may not carry the live file objects.
    # Reconstruct the source path from the configured input folder.
    candidate = _current_input_folder().resolve() / item.get("filename", "")
    try:
        candidate.relative_to(_current_input_folder().resolve())
    except ValueError:
        return None
    return candidate


def _destination_path(output_root: Path, item) -> Optional[Path]:
    destination = item.get("destination")
    if not destination or destination == "_REVIEW":
        return None

    target = (output_root / destination).resolve()

    try:
        target.relative_to(output_root)
    except ValueError:
        return None

    # The existing path builder currently emits .pdf for all supported files.
    # Preserve the real extension at the final filesystem boundary so a CBZ
    # can never be renamed to a misleading .pdf filename.
    extension = str(item.get("extension") or "").lower()
    if extension == ".cbz" and target.suffix.lower() == ".pdf":
        target = target.with_suffix(".cbz")

    return target


def _apply_events(run) -> List[Dict]:
    return list(run.get("events") or []) if run else []


def _checkpoint_fingerprint_for_event(run, event):
    """Resolve a legacy Apply/Undo event to its stored file fingerprint."""
    if event.get("fingerprint"):
        return str(event["fingerprint"])

    filename = event.get("filename")
    source = event.get("source")
    candidates = []

    stored_items = run.get("items") if isinstance(run, dict) else None
    if not isinstance(stored_items, dict):
        checkpoint = load_checkpoint()
        stored_items = checkpoint_items(checkpoint) if checkpoint else {}

    for fingerprint, entry in stored_items.items():
        item = entry.get("item") if isinstance(entry, dict) else None
        if not isinstance(item, dict):
            continue
        if filename and item.get("filename") != filename:
            continue
        item_path = item.get("path")
        if source and item_path:
            try:
                if Path(item_path).resolve() != Path(source).resolve():
                    continue
            except OSError:
                continue
        candidates.append(str(fingerprint))

    if len(candidates) == 1:
        return candidates[0]
    return candidates[0] if candidates else None


def _operation_key(run, event):
    fingerprint = _checkpoint_fingerprint_for_event(run, event)
    if fingerprint:
        return ("fingerprint", fingerprint)
    filename = event.get("filename")
    if filename:
        return ("filename", str(filename))
    return None


def _operation_states(run) -> Dict[tuple, str]:
    """Return the latest successful apply/undo state for each recorded file.

    Undo preflight failures are deliberately non-terminal: they do not change
    the file's current APPLIED state, so the file remains eligible for a later
    safe Undo attempt.
    """
    states = {}
    for event in _apply_events(run):
        key = _operation_key(run, event)
        if not key:
            continue
        event_type = event.get("type")
        if event_type == "apply_moved":
            states[key] = "APPLIED"
        elif event_type == "undo_moved":
            states[key] = "UNDONE"
    return states


def _operation_outcomes(run) -> Dict[tuple, dict]:
    """Return the latest operation outcome for each recorded file."""
    outcomes = {}
    for event in _apply_events(run):
        key = _operation_key(run, event)
        if not key:
            continue
        event_type = event.get("type")
        if event_type in {"apply_moved", "undo_moved", "undo_blocked", "undo_error"}:
            outcomes[key] = event
    return outcomes


def _applied_filenames(run) -> Set[str]:
    """Return filenames whose latest persisted operation is Apply.

    Apply-plan presentation must not depend on rollback fingerprint resolution.
    The fingerprint is still required by the integrity-sensitive Undo path, but
    an already-applied file should remain visible as APPLIED even when an older
    or repaired event cannot be matched back to a checkpoint fingerprint.
    """
    states = {}
    for event in _apply_events(run):
        filename = event.get("filename")
        if not filename:
            continue
        event_type = event.get("type")
        if event_type == "apply_moved":
            states[str(filename)] = "APPLIED"
        elif event_type == "undo_moved":
            states[str(filename)] = "UNDONE"
    return {filename for filename, state in states.items() if state == "APPLIED"}


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _applied_operations(run):
    """Return currently applied, undo-capable operations keyed by fingerprint."""
    operations = {}
    states = _operation_states(run)
    for event in _apply_events(run):
        if event.get("type") != "apply_moved":
            continue
        fingerprint = _checkpoint_fingerprint_for_event(run, event)
        if not fingerprint:
            # If a legacy event cannot be matched to a stored fingerprint,
            # it remains visible as applied by filename but is not offered
            # for integrity-sensitive Undo.
            continue
        key = _operation_key(run, event)
        if not key or states.get(key) != "APPLIED":
            continue
        operations[str(fingerprint)] = dict(event, fingerprint=str(fingerprint))
    return operations


def _history_apply_roots(run):
    """Return the input/output roots recorded for a historical Apply."""
    input_root = Path(run.get("input_folder") or _current_input_folder()).resolve()
    output_root = None
    for event in _apply_events(run):
        if event.get("type") == "apply_completed" and event.get("output_folder"):
            output_root = Path(str(event["output_folder"])).resolve()
    if output_root is None:
        output_root = _apply_output_folder()
    return input_root, output_root


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _undo_plan(run, fingerprints):
    if run is None:
        return {
            "ready": False,
            "reason": "Run not found.",
            "items": [],
            "statistics": {"selected": 0, "ready": 0, "blocked": 0},
        }

    requested = list(dict.fromkeys(str(value) for value in (fingerprints or []) if value))
    if not requested:
        return {
            "ready": False,
            "reason": "Select at least one applied file to undo.",
            "items": [],
            "statistics": {"selected": 0, "ready": 0, "blocked": 0},
        }

    operations = _applied_operations(run)
    input_root, output_root = _history_apply_roots(run)
    items = []
    blocked = []

    for fingerprint in requested:
        event = operations.get(fingerprint)
        if not event:
            items.append({
                "fingerprint": fingerprint,
                "filename": fingerprint,
                "status": "BLOCKED",
                "reason": "This file does not have a rollback-safe Apply record.",
            })
            blocked.append(fingerprint)
            continue

        raw_source = str(event.get("source") or "").strip()
        raw_destination = str(event.get("destination") or "").strip()
        reasons = []

        if not raw_source:
            reasons.append("Original source path is missing")
        if not raw_destination:
            reasons.append("Applied destination path is missing")

        source = Path(raw_source).resolve() if raw_source else None
        destination = Path(raw_destination).resolve() if raw_destination else None

        if source is not None and not _path_is_within(source, input_root):
            reasons.append("Original source path is outside the recorded input folder")
        if destination is not None and not _path_is_within(destination, output_root):
            reasons.append("Applied destination path is outside the recorded output folder")
        if source is not None and source.exists():
            reasons.append("Original source already exists")
        if destination is None or not destination.is_file():
            reasons.append("Applied destination file does not exist")
        if destination is not None and destination.is_symlink():
            reasons.append("Applied destination is a symbolic link")

        recorded_size = event.get("size")
        if recorded_size is not None and destination is not None and destination.is_file():
            try:
                if destination.stat().st_size != int(recorded_size):
                    reasons.append("Destination size no longer matches the Apply record")
            except OSError:
                reasons.append("Destination could not be inspected")

        recorded_hash = event.get("sha256")
        if recorded_hash and destination is not None and destination.is_file() and not destination.is_symlink():
            try:
                if _sha256_file(destination) != recorded_hash:
                    reasons.append("Destination contents no longer match the Apply record")
            except OSError:
                reasons.append("Destination could not be verified")

        if source is not None and destination is not None and source == destination:
            reasons.append("Original source and applied destination are identical")

        if reasons:
            items.append({
                "fingerprint": fingerprint,
                "filename": event.get("filename") or fingerprint,
                "source": str(source) if source else "",
                "destination": str(destination) if destination else "",
                "status": "BLOCKED",
                "reason": "; ".join(reasons),
            })
            blocked.append(fingerprint)
        else:
            items.append({
                "fingerprint": fingerprint,
                "filename": event.get("filename") or fingerprint,
                "source": str(source),
                "destination": str(destination),
                "status": "READY",
                "reason": "Safe to move back to the original location",
            })

    ready = [item for item in items if item["status"] == "READY"]
    return {
        # Partial execution is intentional: one blocked file must not prevent
        # unrelated safe files from being restored.
        "ready": bool(ready),
        "reason": "Some selected files are blocked; only READY files will be undone." if ready and blocked else (
            "All rollback safety checks passed." if ready else "No selected files passed the rollback safety checks."
        ),
        "items": items,
        "statistics": {
            "selected": len(items),
            "ready": len(ready),
            "blocked": len(blocked),
        },
    }

def _fingerprint_for_run_item(run, item):
    for fingerprint, entry in (run.get("items") or {}).items():
        stored = entry.get("item") if isinstance(entry, dict) else None
        if stored is item or stored == item:
            return str(fingerprint)
        if isinstance(stored, dict) and stored.get("filename") == item.get("filename"):
            return str(fingerprint)
    return None


def _apply_plan():
    run = get_last_run()
    output_root = _apply_output_folder()

    if run is None:
        return {
            "ready": False,
            "reason": "No completed Dry Run is available.",
            "output_folder": str(output_root),
            "items": [],
            "statistics": {},
        }

    stats = run.get("statistics") or {}
    review_count = stats.get("review", 0)
    error_count = stats.get("errors", 0)
    collision_count = stats.get("collisions", 0)
    status = run.get("status", "finished")

    # Apply is evaluated per AUTO file. REVIEW, ERROR and collision results
    # must not prevent unrelated READY files from being applied. A file with
    # a collision is marked BLOCKED below; other files can still proceed.
    blockers = []
    if status != "finished":
        blockers.append(f"Run status is '{status}', not finished")

    applied = _applied_filenames(run)
    items = []
    plan_blockers = list(blockers)

    collision_destinations = {
        destination
        for destination, filenames in (run.get("collisions") or {}).items()
        if len(filenames) > 1
    }

    for item in run.get("results", {}).get("AUTO", []):
        filename = item.get("filename", "")
        source = _item_source_path(item)
        target = _destination_path(output_root, item)
        item_blockers = []

        if filename in applied:
            items.append({
                "filename": filename,
                "fingerprint": _fingerprint_for_run_item(run, item),
                "source": str(source) if source else "",
                "destination": str(target) if target else "",
                "relative_destination": item.get("destination"),
                "status": "APPLIED",
                "reason": "Already applied in this run",
            })
            continue

        if source is None:
            item_blockers.append("Source path could not be resolved")
        elif not source.is_file():
            item_blockers.append("Source file does not exist")

        if target is None:
            item_blockers.append("Destination could not be built safely")
        elif target.exists():
            item_blockers.append("Destination already exists")

        if item.get("destination") in collision_destinations:
            item_blockers.append("Destination collision with another AUTO file")

        if source and target and source == target:
            item_blockers.append("Source and destination are identical")

        if item_blockers:
            plan_blockers.extend(
                f"{filename}: {reason}" for reason in item_blockers
            )
            item_status = "BLOCKED"
            reason = "; ".join(item_blockers)
        else:
            item_status = "READY"
            reason = "Ready to move"

        items.append({
            "filename": filename,
            "fingerprint": _fingerprint_for_run_item(run, item),
            "source": str(source) if source else "",
            "destination": str(target) if target else "",
            "relative_destination": item.get("destination"),
            "status": item_status,
            "reason": reason,
        })

    ready_items = [item for item in items if item["status"] == "READY"]
    blocked_items = [item for item in items if item["status"] == "BLOCKED"]

    already_applied = len([item for item in items if item["status"] == "APPLIED"])

    # Item-level blockers are intentionally NOT global blockers. The Apply
    # action is enabled whenever at least one AUTO file is READY, so safe
    # files can proceed while unsafe files remain untouched. Only a run-level
    # blocker (for example, a run that is not finished) prevents the entire
    # Apply operation.
    return {
        "ready": status == "finished" and bool(ready_items),
        "already_applied": already_applied == len(items) and bool(items),
        "reason": "; ".join(plan_blockers) if plan_blockers else "All checks passed",
        "output_folder": str(output_root),
        "run_id": run.get("run_id"),
        "status": status,
        "items": items,
        "statistics": {
            "total_auto": len(run.get("results", {}).get("AUTO", [])),
            "ready": len(ready_items),
            "blocked": len(blocked_items),
            "already_applied": already_applied,
            "review": review_count,
            "errors": error_count,
            "collisions": collision_count,
        },
    }


def _save_apply_event(event):
    """Persist an Apply/Undo event to the current checkpoint.

    History is archived once per Apply operation rather than once per file.
    This keeps large Apply runs fast while the checkpoint still records each
    successfully moved file for crash-safe rollback.
    """
    checkpoint = load_checkpoint()
    if not checkpoint:
        return

    checkpoint = ensure_checkpoint_identity(checkpoint)
    checkpoint, repaired_events = _repair_apply_event_fingerprints(checkpoint)
    events = list(checkpoint_events(checkpoint))
    events.append(event)
    save_checkpoint(
        _current_input_folder(),
        checkpoint.get("total", len(checkpoint_items(checkpoint))),
        checkpoint_items(checkpoint),
        status=checkpoint.get("status", "finished"),
        run_id=checkpoint.get("run_id"),
        review_decisions=checkpoint_review_decisions(checkpoint),
        events=events,
        created_at=checkpoint.get("created_at"),
        run_type=checkpoint.get("run_type", "dry_run"),
    )


def _finalize_apply_history_state():
    """Rebuild current run state from the checkpoint and archive it once."""
    global _last_run

    checkpoint = load_checkpoint()
    if not checkpoint:
        return None

    checkpoint = ensure_checkpoint_identity(checkpoint)
    events = list(checkpoint_events(checkpoint))
    result = _last_run
    if result is None:
        result = rebuild_result_from_checkpoint(
            _current_input_folder(),
            checkpoint_items(checkpoint),
            checkpoint_review_decisions(checkpoint),
        )

    # Keep the original Dry Run population even though applied source files
    # no longer exist in the input folder.
    result["statistics"]["files"] = checkpoint.get(
        "total", result["statistics"].get("files", 0)
    )
    result["statistics"]["processed"] = sum(
        len(result["results"].get(status, []))
        for status in ("AUTO", "REVIEW", "IGNORE", "ERROR")
    )
    result["events"] = events
    _last_run = result
    _archive_checkpoint(checkpoint, result=result)
    return result


@app.get("/apply", response_class=HTMLResponse)
async def apply_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="apply.html",
        context={
            "output_folder": str(_apply_output_folder()),
        },
    )


@app.get("/api/apply/plan")
async def apply_plan_api():
    return _apply_plan()


@app.post("/apply/execute", response_class=HTMLResponse)
async def apply_execute_form(request: Request):
    # Plain HTML form fallback for Apply Changes. This endpoint deliberately
    # parses application/x-www-form-urlencoded without requiring multipart
    # form support, then delegates to the same safety-checked apply function.
    body = (await request.body()).decode("utf-8", errors="replace")
    from urllib.parse import parse_qs

    values = parse_qs(body, keep_blank_values=True)
    confirmed = values.get("confirm", [""])[0] == "1"

    result = await apply_changes({"confirm": confirmed})
    return templates.TemplateResponse(
        request=request,
        name="apply.html",
        context={
            "output_folder": str(_apply_output_folder()),
            "apply_result": result,
        },
    )


@app.get("/api/apply/status")
async def apply_status_api():
    return _get_apply_state()


def _apply_changes_sync(payload: dict):
    if payload.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="Explicit confirmation is required")

    if not _apply_operation_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Apply Changes is already running")

    try:
        _set_apply_state(
            current=0,
            total=0,
            phase="Preparing Apply",
            filename="Checking safety...",
            percent=0,
            error=None,
            running=True,
        )

        with _run_lock:
            if _run_state["running"]:
                _set_apply_state(
                    phase="Error",
                    filename="",
                    error="A Dry Run is currently running",
                    running=False,
                )
                raise HTTPException(status_code=409, detail="A Dry Run is currently running")

        plan = _apply_plan()
        if not plan.get("ready"):
            raise HTTPException(status_code=409, detail=plan.get("reason") or "Apply is not safe")

        ready_items = [entry for entry in plan["items"] if entry["status"] == "READY"]
        total = len(ready_items)
        _set_apply_state(
            current=0,
            total=total,
            phase="Applying",
            filename=ready_items[0]["filename"] if ready_items else "",
            percent=0,
            error=None,
            running=True,
        )

        output_root = _apply_output_folder()
        output_root.mkdir(parents=True, exist_ok=True)

        moved = []
        apply_batch_id = uuid.uuid4().hex

        # Re-check every source and destination immediately before moving. The
        # preview is advisory; these final checks are the actual safety barrier.
        for entry in ready_items:
            source = Path(entry["source"]).resolve()
            target = Path(entry["destination"]).resolve()

            try:
                source.relative_to(_current_input_folder().resolve())
                target.relative_to(output_root)
            except ValueError:
                _set_apply_state(phase="Error", filename=entry["filename"], error=f"Safety check failed for {entry['filename']}", running=False)
                raise HTTPException(status_code=409, detail=f"Safety check failed for {entry['filename']}")

            if not source.is_file():
                _set_apply_state(phase="Error", filename=entry["filename"], error=f"Source disappeared: {entry['filename']}", running=False)
                raise HTTPException(status_code=409, detail=f"Source disappeared: {entry['filename']}")
            if target.exists():
                _set_apply_state(phase="Error", filename=entry["filename"], error=f"Destination appeared during apply: {target}", running=False)
                raise HTTPException(status_code=409, detail=f"Destination appeared during apply: {target}")

        for index, entry in enumerate(ready_items, start=1):
            source = Path(entry["source"]).resolve()
            target = Path(entry["destination"]).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            _set_apply_state(
                current=index - 1,
                total=total,
                phase="Applying",
                filename=entry["filename"],
                percent=round(((index - 1) / total) * 100) if total else 0,
                running=True,
            )

            try:
                # Record an integrity fingerprint before the move. This is what
                # makes a future Undo safe: we can prove that the destination is
                # still the same file we originally moved.
                source_stat = source.stat()
                source_size = source_stat.st_size
                source_mtime_ns = source_stat.st_mtime_ns
                source_hash = _sha256_file(source)

                # shutil.move is appropriate for Docker/Unraid where source and
                # library may be different mounted filesystems. We still verify
                # the destination exists and is unchanged before recording success.
                shutil.move(str(source), str(target))
                if not target.is_file() or target.is_symlink():
                    raise OSError("Destination was not created safely")
                if target.stat().st_size != source_size:
                    raise OSError("Destination size does not match the source")
                if _sha256_file(target) != source_hash:
                    raise OSError("Destination contents do not match the source")
            except Exception as exc:
                detail = f"Apply stopped after {len(moved)} file(s). {entry['filename']}: {exc}"
                _finalize_apply_history_state()
                _set_apply_state(
                    current=len(moved),
                    total=total,
                    phase="Error",
                    filename=entry["filename"],
                    percent=round((len(moved) / total) * 100) if total else 0,
                    error=detail,
                    running=False,
                )
                raise HTTPException(status_code=500, detail=detail)

            moved.append(entry["filename"])
            fingerprint = entry.get("fingerprint")
            if not fingerprint:
                fingerprint = f"{source}|{source_size}|{source_mtime_ns}"

            _save_apply_event(
                _event(
                    "apply_moved",
                    "File moved by Apply Changes",
                    filename=entry["filename"],
                    fingerprint=fingerprint,
                    source=str(source),
                    destination=str(target),
                    size=source_size,
                    sha256=source_hash,
                    apply_batch_id=apply_batch_id,
                )
            )

            _set_apply_state(
                current=index,
                total=total,
                phase="Applying",
                filename=entry["filename"],
                percent=round((index / total) * 100) if total else 100,
                running=True,
            )

        _save_apply_event(
            _event(
                "apply_completed",
                "Apply Changes completed",
                count=len(moved),
                output_folder=str(output_root),
                apply_batch_id=apply_batch_id,
            )
        )
        _finalize_apply_history_state()

        _set_apply_state(
            current=len(moved),
            total=total,
            phase="Apply complete",
            filename="",
            percent=100 if total else 0,
            error=None,
            running=False,
        )

        return {
            "success": True,
            "moved": moved,
            "count": len(moved),
            "output_folder": str(output_root),
            "message": f"{len(moved)} file(s) moved successfully.",
        }
    except HTTPException:
        if _get_apply_state()["running"]:
            _set_apply_state(phase="Error", error="Apply Changes stopped", running=False)
        raise
    except Exception as exc:
        detail = str(exc)
        _set_apply_state(phase="Error", error=detail, running=False)
        raise
    finally:
        _apply_operation_lock.release()


@app.post("/api/apply")
async def apply_changes(payload: dict):
    # Run the blocking filesystem work in a worker thread so the async web
    # server remains responsive to live Apply progress/status requests.
    return await run_in_threadpool(_apply_changes_sync, payload)


# ---------------------------------------------------------------------------
# Apply rollback / Undo
# ---------------------------------------------------------------------------

def _history_has_event(record, event_type, fingerprint):
    for event in _apply_events(record):
        if event.get("type") != event_type:
            continue
        if fingerprint and event.get("fingerprint") == fingerprint:
            return True
    return False


def _save_history_event(run_id, event):
    """Append an event to a historical run without depending on the current run."""
    record = load_history_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")

    events = list(record.get("events") or [])
    events.append(event)
    record["events"] = events
    save_history_run(record)

    checkpoint = load_checkpoint()
    if checkpoint and checkpoint.get("run_id") == run_id:
        checkpoint = ensure_checkpoint_identity(checkpoint)
        checkpoint_events_list = list(checkpoint_events(checkpoint))
        checkpoint_events_list.append(event)
        save_checkpoint(
            Path(record.get("input_folder") or _current_input_folder()),
            checkpoint.get("total", len(checkpoint_items(checkpoint))),
            checkpoint_items(checkpoint),
            status=checkpoint.get("status", "finished"),
            run_id=run_id,
            review_decisions=checkpoint_review_decisions(checkpoint),
            events=checkpoint_events_list,
            created_at=checkpoint.get("created_at"),
            run_type=checkpoint.get("run_type", "dry_run"),
        )


@app.get("/api/history/{run_id}/undo-plan")
async def history_undo_plan(run_id: str, fingerprints: str = ""):
    record = load_history_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")

    selected = [value for value in fingerprints.split(",") if value]
    return _undo_plan(record, selected)


@app.post("/api/history/{run_id}/undo")
async def undo_history(run_id: str, payload: dict):
    record = load_history_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")

    with _run_lock:
        if _run_state["running"]:
            raise HTTPException(status_code=409, detail="A Dry Run is currently running.")

        fingerprints = payload.get("fingerprints") if isinstance(payload, dict) else None
        plan = _undo_plan(record, fingerprints)
        if not plan.get("ready"):
            raise HTTPException(status_code=409, detail=plan.get("reason") or "Undo is not safe.")

        undo_batch_id = uuid.uuid4().hex
        moved = []
        blocked = []
        errors = []

        # Record plan-time blockers as outcomes, but leave the underlying
        # APPLIED state intact so they remain eligible for a later retry.
        for entry in plan.get("items", []):
            if entry.get("status") != "BLOCKED":
                continue
            blocked.append(entry.get("filename") or entry.get("fingerprint"))
            _save_history_event(
                run_id,
                _event(
                    "undo_blocked",
                    "Undo blocked for file",
                    filename=entry.get("filename"),
                    fingerprint=entry.get("fingerprint"),
                    reason=entry.get("reason"),
                    undo_batch_id=undo_batch_id,
                ),
            )
            record = load_history_run(run_id) or record

        # Re-check each READY file immediately before changing it. If one has
        # become unsafe since the preview, record the block and continue with
        # the other safe files.
        for entry in plan.get("items", []):
            if entry.get("status") != "READY":
                continue

            fingerprint = entry["fingerprint"]
            current_ops = _applied_operations(record)
            operation = current_ops.get(fingerprint)
            if not operation:
                reason = "Rollback-safe Apply record is no longer available"
                blocked.append(entry["filename"])
                _save_history_event(
                    run_id,
                    _event(
                        "undo_blocked",
                        "Undo blocked for file",
                        filename=entry["filename"],
                        fingerprint=fingerprint,
                        reason=reason,
                        undo_batch_id=undo_batch_id,
                    ),
                )
                record = load_history_run(run_id) or record
                continue

            current_path = Path(str(operation.get("destination") or entry["destination"])).resolve()
            original_path = Path(str(operation.get("source") or entry["source"])).resolve()
            input_root, output_root = _history_apply_roots(record)
            reasons = []
            if not _path_is_within(original_path, input_root):
                reasons.append("Original source path is outside the recorded input folder")
            if not _path_is_within(current_path, output_root):
                reasons.append("Applied destination path is outside the recorded output folder")
            if current_path.is_symlink() or not current_path.is_file():
                reasons.append("Applied destination is no longer a safe regular file")
            if original_path.exists():
                reasons.append("Original source location is no longer empty")

            recorded_hash = operation.get("sha256")
            recorded_size = operation.get("size")
            try:
                if recorded_hash and not reasons and _sha256_file(current_path) != recorded_hash:
                    reasons.append("File contents changed since Apply")
                if recorded_size is not None and not reasons and current_path.stat().st_size != int(recorded_size):
                    reasons.append("File size changed since Apply")
            except OSError as exc:
                reasons.append(f"File could not be verified: {exc}")

            if reasons:
                reason = "; ".join(reasons)
                blocked.append(entry["filename"])
                _save_history_event(
                    run_id,
                    _event(
                        "undo_blocked",
                        "Undo blocked for file",
                        filename=entry["filename"],
                        fingerprint=fingerprint,
                        reason=reason,
                        undo_batch_id=undo_batch_id,
                    ),
                )
                record = load_history_run(run_id) or record
                continue

            original_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(current_path), str(original_path))
                if not original_path.is_file() or original_path.is_symlink():
                    raise OSError("Original file was not restored safely")
                if recorded_hash and _sha256_file(original_path) != recorded_hash:
                    raise OSError("Restored file contents do not match the Apply record")
                if recorded_size is not None and original_path.stat().st_size != int(recorded_size):
                    raise OSError("Restored file size does not match the Apply record")
            except Exception as exc:
                # If the move itself succeeded but verification failed, try to
                # put the file back where it came from before continuing. This
                # avoids leaving a file in an ambiguous location.
                recovered = False
                try:
                    if original_path.is_file() and not original_path.is_symlink() and not current_path.exists():
                        shutil.move(str(original_path), str(current_path))
                        recovered = current_path.is_file() and not current_path.is_symlink()
                except Exception:
                    recovered = False

                reason = str(exc)
                if not recovered:
                    reason += "; automatic recovery to the applied destination failed"
                errors.append(entry["filename"])
                _save_history_event(
                    run_id,
                    _event(
                        "undo_error",
                        "Undo failed for file",
                        filename=entry["filename"],
                        fingerprint=fingerprint,
                        reason=reason,
                        recovered=recovered,
                        undo_batch_id=undo_batch_id,
                    ),
                )
                record = load_history_run(run_id) or record
                if not recovered:
                    # This is the exceptional case where the file may no
                    # longer have a known safe location. Stop rather than risk
                    # touching anything else.
                    break
                continue

            moved.append(entry["filename"])
            _save_history_event(
                run_id,
                _event(
                    "undo_moved",
                    "File restored by Undo",
                    filename=entry["filename"],
                    fingerprint=fingerprint,
                    source=str(current_path),
                    destination=str(original_path),
                    size=recorded_size,
                    sha256=recorded_hash,
                    undo_batch_id=undo_batch_id,
                ),
            )
            record = load_history_run(run_id) or record

        _save_history_event(
            run_id,
            _event(
                "undo_completed",
                "Undo completed",
                selected=plan["statistics"].get("selected", 0),
                restored=len(moved),
                blocked=len(blocked),
                errors=len(errors),
                undo_batch_id=undo_batch_id,
            ),
        )

    # A successful Undo changes the filesystem under the current input folder.
    # Any finished/cached Dry Run covering that folder is therefore stale: a
    # subsequent Dry Run must scan the restored files again instead of returning
    # the old cached result. History remains intact because only the current
    # checkpoint is invalidated.
    if moved:
        try:
            record_input = Path(record.get("input_folder") or "").resolve()
            current_input = Path(_current_input_folder()).resolve()
            if record_input == current_input:
                _invalidate_current_run_cache()
        except OSError:
            # Failing to compare paths must never turn a completed Undo into an
            # error; the restore itself has already succeeded.
            pass

    if errors:
        message = f"Undo restored {len(moved)} file(s); {len(blocked)} blocked and {len(errors)} failed."
    elif blocked:
        message = f"Undo restored {len(moved)} file(s); {len(blocked)} blocked and left untouched."
    else:
        message = f"{len(moved)} file(s) restored to their original locations."

    return {
        "success": True,
        "restored": moved,
        "blocked": blocked,
        "errors": errors,
        "count": len(moved),
        "message": message,
    }

# ---------------------------------------------------------------------------
# Current-run invalidation
# ---------------------------------------------------------------------------

def _invalidate_current_run_cache():
    global _last_run

    clear_checkpoint()
    _last_run = None
    _review_decisions.clear()
    _cancel_event.clear()

    with _run_lock:
        _run_state["running"] = False
        _run_state["phase"] = "Idle"
        _run_state["current"] = 0
        _run_state["total"] = 0
        _run_state["filename"] = ""
        _run_state["percent"] = 0
        _run_state["error"] = None
        _run_state["cancelled"] = False


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    settings = get_effective_settings()
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "settings": settings,
            "ocr_languages": OCR_LANGUAGES,
            "page_stages_text": page_stages_to_text(settings["ocr"]["page_stages"]),
            "env_managed": env_managed_paths(),
            "auto_run_modes": AUTO_RUN_MODES,
            "auto_run_schedules": AUTO_RUN_SCHEDULES,
            "weekdays": WEEKDAYS,
            "auto_run_summary": schedule_summary(settings.get("auto_run", DEFAULT_AUTO_RUN)),
        },
    )


@app.get("/api/settings")
async def settings_api():
    settings = get_effective_settings()
    return {
        "settings": settings,
        "ocr_languages": OCR_LANGUAGES,
        "page_stages_text": page_stages_to_text(settings["ocr"]["page_stages"]),
        "env_managed": env_managed_paths(),
        "auto_run_modes": AUTO_RUN_MODES,
        "auto_run_schedules": AUTO_RUN_SCHEDULES,
        "weekdays": WEEKDAYS,
        "auto_run_summary": schedule_summary(settings.get("auto_run", DEFAULT_AUTO_RUN)),
    }


@app.get("/api/auto-run/status")
async def auto_run_status():
    settings = validate_auto_run(get_effective_settings().get("auto_run", DEFAULT_AUTO_RUN))
    with _auto_runtime_lock:
        runtime = dict(_auto_runtime)
    runtime.pop("seen_fingerprints", None)
    return {
        "settings": settings,
        "runtime": runtime,
        "summary": schedule_summary(settings),
        "mode_label": AUTO_RUN_MODES.get(settings["mode"], settings["mode"]),
    }


@app.post("/api/auto-run/run-now")
async def auto_run_run_now():
    try:
        started, message = _start_auto_run_now()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not started:
        raise HTTPException(status_code=409, detail=message)
    return {"started": True, "message": message}


@app.post("/api/settings")
async def settings_update(payload: dict):
    with _run_lock:
        if _run_state["running"]:
            raise HTTPException(status_code=409, detail="Settings cannot be changed while a Dry Run is running.")

    try:
        validated = validate_settings_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    before = get_effective_settings()
    managed = env_managed_paths()
    stored = load_settings()
    if managed["input_folder"]:
        validated["folders"]["input_folder"] = stored["folders"]["input_folder"]
    if managed["output_folder"]:
        validated["folders"]["output_folder"] = stored["folders"]["output_folder"]

    save_settings(validated)
    effective = get_effective_settings()

    if before.get("auto_run") != effective.get("auto_run"):
        auto = effective.get("auto_run", DEFAULT_AUTO_RUN)
        _set_auto_runtime(
            next_run_at=None,
            phase="Scheduled" if auto.get("enabled") else "Disabled",
            last_error=None,
            startup_consumed=False,
        )

    # A cached/resumable Dry Run is only valid for the folder/OCR settings
    # that produced it. History is retained; only the current checkpoint is
    # invalidated when those settings actually change.
    if (before.get("folders") != effective.get("folders") or
            before.get("ocr") != effective.get("ocr")):
        _invalidate_current_run_cache()
    return {
        "success": True,
        "message": "Settings saved successfully.",
        "settings": effective,
        "page_stages_text": page_stages_to_text(effective["ocr"]["page_stages"]),
        "env_managed": env_managed_paths(),
    }


@app.post("/api/settings/test-folders")
async def settings_test_folders(payload: dict):
    folders = payload.get("folders") or {}
    input_folder = str(folders.get("input_folder") or "").strip()
    output_folder = str(folders.get("output_folder") or "").strip()
    if not input_folder or not output_folder:
        raise HTTPException(status_code=400, detail="Both folders are required.")
    return test_folder_access(Path(input_folder), Path(output_folder))


@app.post("/api/settings/reset")
async def settings_reset():
    with _run_lock:
        if _run_state["running"]:
            raise HTTPException(status_code=409, detail="Settings cannot be reset while a Dry Run is running.")

    before = get_effective_settings()
    save_settings(DEFAULT_SETTINGS)
    settings = get_effective_settings()
    _set_auto_runtime(
        running=False,
        phase="Disabled",
        next_run_at=None,
        last_error=None,
        startup_consumed=False,
        seen_fingerprints=[],
    )
    if (before.get("folders") != settings.get("folders") or
            before.get("ocr") != settings.get("ocr")):
        _invalidate_current_run_cache()
    return {
        "success": True,
        "message": "Recommended defaults restored.",
        "settings": settings,
        "page_stages_text": page_stages_to_text(settings["ocr"]["page_stages"]),
        "env_managed": env_managed_paths(),
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "application": "magazine-sorter",
        "version": "0.9.0",
    }
