from pathlib import Path
import os
import shutil
from threading import Event, Lock, Thread
from datetime import datetime, timezone
import re
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Set

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from src.classifier import classify_file
from src.ocr_classifier import analyze_file
from src.path_builder import build_destination
from src.scanner import find_magazines
from src.web.profile_manager import (
    add_publication,
    list_publications,
    suggest_profile,
    update_publication,
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

TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# Temporary test input.
# Later this becomes a GUI-configurable setting.
DEFAULT_INPUT_FOLDER = Path(
    os.getenv(
        "MAGAZINE_SORTER_INPUT_FOLDER",
        str(PROJECT_ROOT / "ocr_test_data"),
    )
)
DEFAULT_OUTPUT_FOLDER = Path(
    os.getenv(
        "MAGAZINE_SORTER_OUTPUT_FOLDER",
        str(PROJECT_ROOT / "library"),
    )
)


app = FastAPI(
    title="Magazine Sorter",
    version="0.7.0",
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


def get_destination(publication, metadata):
    if not publication:
        return None

    return build_destination(
        {
            "magazine": publication,
            **metadata,
        }
    )


def run_gui_dry_run(input_folder, progress_callback=None, cancel_check=None, resume_items=None, completed_callback=None):
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
    files = find_magazines(input_folder)

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
                    "result": filename_result,
                    "destination": destination,
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

                analysis = analyze_file(file.path)

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
    }

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
        "statistics": statistics,
    }


def format_result(item):
    result = item["result"]

    return {
        "status": result["status"],
        "source": item["filename"],
        "destination": item["destination"],
        "publication": result.get("publication"),
        "reason": result.get("reason"),
        "source_type": item.get("source"),
    }


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
            if status == "REVIEW":
                decision = decisions.get(item.get("filename"))
                if decision and decision.get("action") == "IGNORE":
                    item = dict(item)
                    item["result"] = dict(item.get("result") or {})
                    item["result"]["status"] = "IGNORE"
                    item["result"]["reason"] = "Ignored manually in Review"
                    status = "IGNORE"
                elif decision and decision.get("action") == "AUTO":
                    item = dict(item)
                    item["result"] = dict(item.get("result") or {})
                    item["result"]["status"] = "AUTO"
                    item["result"]["publication"] = decision.get("publication")
                    item["result"]["metadata"] = decision.get("metadata") or {}
                    item["result"]["reason"] = "Approved manually in Review"
                    item["destination"] = get_destination(
                        decision.get("publication"),
                        decision.get("metadata") or {},
                    )
                    status = "AUTO"
            results[status].append(item)

        stored_stats = checkpoint.get("statistics") or {}
        result = {
            "input_folder": checkpoint.get("input_folder") or str(DEFAULT_INPUT_FOLDER),
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


def _load_persistent_current_run():
    checkpoint = load_checkpoint()
    if not checkpoint or not checkpoint_matches_input(checkpoint, DEFAULT_INPUT_FOLDER):
        return None, None

    had_identity = bool(checkpoint.get("run_id"))
    checkpoint = ensure_checkpoint_identity(checkpoint)
    if not had_identity:
        save_checkpoint(
            DEFAULT_INPUT_FOLDER,
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
        DEFAULT_INPUT_FOLDER,
        items,
        checkpoint_review_decisions(checkpoint),
    )
    return checkpoint, result


def _persist_review_state():
    checkpoint = load_checkpoint()
    if not checkpoint or not checkpoint_matches_input(checkpoint, DEFAULT_INPUT_FOLDER):
        return

    checkpoint = ensure_checkpoint_identity(checkpoint)
    decisions = dict(_review_decisions)
    events = list(checkpoint_events(checkpoint))

    save_checkpoint(
        DEFAULT_INPUT_FOLDER,
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
            DEFAULT_INPUT_FOLDER,
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
            "input_folder": str(DEFAULT_INPUT_FOLDER),
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


def _dry_run_worker(resume_items=None):
    global _last_run

    checkpoint_items_map = dict(resume_items or {})

    def completed_callback(fingerprint, item):
        checkpoint_items_map[fingerprint] = {
            "item": item,
        }
        checkpoint = load_checkpoint() or {}
        checkpoint = ensure_checkpoint_identity(checkpoint)
        save_checkpoint(
            DEFAULT_INPUT_FOLDER,
            _run_state["total"],
            checkpoint_items_map,
            status="running",
            run_id=checkpoint.get("run_id"),
            review_decisions=_review_decisions,
            events=checkpoint_events(checkpoint),
            created_at=checkpoint.get("created_at"),
        )

    try:
        result = run_gui_dry_run(
            DEFAULT_INPUT_FOLDER,
            progress_callback=_update_run_state,
            cancel_check=_cancel_event.is_set,
            resume_items=checkpoint_items_map,
            completed_callback=completed_callback,
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
            DEFAULT_INPUT_FOLDER,
            result["statistics"]["files"],
            checkpoint_items_map,
            status=status,
            run_id=previous.get("run_id"),
            review_decisions=_review_decisions,
            events=events,
            created_at=previous.get("created_at"),
        )
        _archive_checkpoint(load_checkpoint(), result=result)

        with _run_lock:
            _last_run = result
            _run_state["running"] = False
            _run_state["phase"] = "Stopped" if cancelled else "Finished"
            _run_state["current"] = result["statistics"]["processed"]
            _run_state["total"] = result["statistics"]["files"]
            _run_state["percent"] = (
                round(
                    (result["statistics"]["processed"] / result["statistics"]["files"]) * 100
                )
                if result["statistics"]["files"]
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

        # Manual Review decisions are persisted separately from the original
        # classifier result so the raw dry-run evidence is never overwritten.
        decision = review_decisions.get(item.get("filename"))
        if status == "REVIEW" and decision:
            item = dict(item)
            item["result"] = dict(item.get("result") or {})
            if decision.get("action") == "IGNORE":
                item["result"]["status"] = "IGNORE"
                item["result"]["reason"] = "Ignored manually in Review"
                status = "IGNORE"
            elif decision.get("action") == "AUTO":
                item["result"]["status"] = "AUTO"
                item["result"]["publication"] = decision.get("publication")
                item["result"]["metadata"] = decision.get("metadata") or {}
                item["result"]["reason"] = "Approved manually in Review"
                item["destination"] = get_destination(
                    decision.get("publication"),
                    decision.get("metadata") or {},
                )
                status = "AUTO"

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
    }

    return {
        "input_folder": str(Path(input_folder)),
        "files": files,
        "results": results,
        "collisions": collisions,
        "statistics": statistics,
    }


@app.post("/api/dry-run")
async def dry_run():
    global _last_run

    with _run_lock:
        if _run_state["running"]:
            return {
                "started": False,
                "running": True,
            }

        checkpoint = load_checkpoint()
        if checkpoint_matches_input(checkpoint, DEFAULT_INPUT_FOLDER):
            checkpoint = ensure_checkpoint_identity(checkpoint)
        resume_items = (
            checkpoint_items(checkpoint)
            if checkpoint_matches_input(checkpoint, DEFAULT_INPUT_FOLDER)
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
                DEFAULT_INPUT_FOLDER,
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
                DEFAULT_INPUT_FOLDER,
                len(find_magazines(DEFAULT_INPUT_FOLDER)),
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
        args=(resume_items,),
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
        if checkpoint_matches_input(checkpoint, DEFAULT_INPUT_FOLDER):
            items = checkpoint_items(checkpoint)
            if items:
                checkpoint = ensure_checkpoint_identity(checkpoint)
                _review_decisions.clear()
                _review_decisions.update(checkpoint_review_decisions(checkpoint))
                result = rebuild_result_from_checkpoint(
                    DEFAULT_INPUT_FOLDER,
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
        response["statistics"] = result["statistics"]

        results = []

        for status in ["AUTO", "REVIEW", "IGNORE"]:
            for item in result["results"][status]:
                results.append(
                    format_result(item)
                )

        response["results"] = results
        response["collisions"] = result["collisions"]
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

    statistics = record.get("statistics") or {}
    statistics = {
        "files": statistics.get("files", record.get("total", 0)),
        "processed": statistics.get("processed", sum(len(v) for v in results.values())),
        "auto": len(results["AUTO"]),
        "review": len(results["REVIEW"]),
        "ignore": len(results["IGNORE"]),
        "errors": len(results["ERROR"]),
        "unique_destinations": statistics.get("unique_destinations", len(destinations)),
        "collisions": len(collisions),
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


@app.get("/api/history/{run_id}")
async def history_item(run_id: str):
    record = load_history_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return record


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
    """Apply GUI-only review decisions to the in-memory dry-run result.

    This does not touch files and does not alter parser/classifier/OCR logic.
    """
    run = get_last_run()
    if run is None:
        return

    for item in run["results"].get("REVIEW", []):
        decision = _review_decisions.get(item["filename"])
        if not decision:
            continue

        if decision["action"] == "IGNORE":
            item["result"]["status"] = "IGNORE"
            item["result"]["reason"] = "Ignored manually in Review"
            run["results"]["IGNORE"].append(item)

        elif decision["action"] == "AUTO":
            item["result"]["status"] = "AUTO"
            item["result"]["publication"] = decision["publication"]
            item["result"]["metadata"] = decision["metadata"]
            item["result"]["reason"] = "Approved manually in Review"
            item["destination"] = get_destination(
                decision["publication"],
                decision["metadata"],
            )
            run["results"]["AUTO"].append(item)

    run["results"]["REVIEW"] = [
        item
        for item in run["results"]["REVIEW"]
        if item["result"].get("status") == "REVIEW"
    ]

    destinations = {}
    for status in ["AUTO"]:
        for item in run["results"][status]:
            destination = item.get("destination")
            if destination:
                destinations.setdefault(destination, []).append(item["filename"])

    run["collisions"] = {
        destination: filenames
        for destination, filenames in destinations.items()
        if len(filenames) > 1
    }

    run["statistics"]["auto"] = len(run["results"]["AUTO"])
    run["statistics"]["review"] = len(run["results"]["REVIEW"])
    run["statistics"]["ignore"] = len(run["results"]["IGNORE"])
    run["statistics"]["errors"] = len(run["results"]["ERROR"])
    run["statistics"]["collisions"] = len(run["collisions"])


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

    input_root = DEFAULT_INPUT_FOLDER.resolve()

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
        analysis = analyze_file(path)
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
            DEFAULT_INPUT_FOLDER,
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
            DEFAULT_INPUT_FOLDER,
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



# ---------------------------------------------------------------------------
# Apply Changes
# ---------------------------------------------------------------------------


def _apply_output_folder() -> Path:
    return DEFAULT_OUTPUT_FOLDER.resolve()


def _item_source_path(item) -> Optional[Path]:
    run = get_last_run()
    if run is None:
        return None

    for file in run.get("files", []):
        if file.filename == item.get("filename"):
            return Path(file.path).resolve()

    # Persistent history/checkpoint runs may not carry the live file objects.
    # Reconstruct the source path from the configured input folder.
    candidate = DEFAULT_INPUT_FOLDER.resolve() / item.get("filename", "")
    try:
        candidate.relative_to(DEFAULT_INPUT_FOLDER.resolve())
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


def _applied_filenames(run) -> Set[str]:
    applied = set()
    for event in _apply_events(run):
        if event.get("type") == "apply_moved" and event.get("filename"):
            applied.add(event["filename"])
    return applied


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

    blockers = []
    if status != "finished":
        blockers.append(f"Run status is '{status}', not finished")
    # REVIEW files are intentionally not a global blocker. Apply may move
    # safely classified AUTO files while REVIEW files remain untouched for
    # later human processing. Errors and collisions remain hard blockers.
    if error_count:
        blockers.append(f"{error_count} file(s) have errors")
    if collision_count:
        blockers.append(f"{collision_count} collision(s) must be resolved")

    applied = _applied_filenames(run)
    items = []
    plan_blockers = list(blockers)

    for item in run.get("results", {}).get("AUTO", []):
        filename = item.get("filename", "")
        source = _item_source_path(item)
        target = _destination_path(output_root, item)
        item_blockers = []

        if filename in applied:
            items.append({
                "filename": filename,
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
            "source": str(source) if source else "",
            "destination": str(target) if target else "",
            "relative_destination": item.get("destination"),
            "status": item_status,
            "reason": reason,
        })

    ready_items = [item for item in items if item["status"] == "READY"]
    blocked_items = [item for item in items if item["status"] == "BLOCKED"]

    if blocked_items:
        plan_blockers.append(f"{len(blocked_items)} destination/source check(s) failed")

    already_applied = len([item for item in items if item["status"] == "APPLIED"])

    return {
        "ready": not plan_blockers and bool(ready_items),
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
    checkpoint = load_checkpoint()
    if not checkpoint:
        return

    checkpoint = ensure_checkpoint_identity(checkpoint)
    events = list(checkpoint_events(checkpoint))
    events.append(event)
    save_checkpoint(
        DEFAULT_INPUT_FOLDER,
        checkpoint.get("total", len(checkpoint_items(checkpoint))),
        checkpoint_items(checkpoint),
        status=checkpoint.get("status", "finished"),
        run_id=checkpoint.get("run_id"),
        review_decisions=checkpoint_review_decisions(checkpoint),
        events=events,
        created_at=checkpoint.get("created_at"),
    )
    refreshed = load_checkpoint()
    if refreshed:
        _archive_checkpoint(refreshed, result=get_last_run())


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


@app.post("/api/apply")
async def apply_changes(payload: dict):
    if payload.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="Explicit confirmation is required")

    with _run_lock:
        if _run_state["running"]:
            raise HTTPException(status_code=409, detail="A Dry Run is currently running")

    plan = _apply_plan()
    if not plan.get("ready"):
        raise HTTPException(status_code=409, detail=plan.get("reason") or "Apply is not safe")

    output_root = _apply_output_folder()
    output_root.mkdir(parents=True, exist_ok=True)

    moved = []

    # Re-check every source and destination immediately before moving. The
    # preview is advisory; these final checks are the actual safety barrier.
    for entry in plan["items"]:
        if entry["status"] != "READY":
            continue

        source = Path(entry["source"]).resolve()
        target = Path(entry["destination"]).resolve()

        try:
            source.relative_to(DEFAULT_INPUT_FOLDER.resolve())
            target.relative_to(output_root)
        except ValueError:
            raise HTTPException(status_code=409, detail=f"Safety check failed for {entry['filename']}")

        if not source.is_file():
            raise HTTPException(status_code=409, detail=f"Source disappeared: {entry['filename']}")
        if target.exists():
            raise HTTPException(status_code=409, detail=f"Destination appeared during apply: {target}")

    for entry in plan["items"]:
        if entry["status"] != "READY":
            continue

        source = Path(entry["source"]).resolve()
        target = Path(entry["destination"]).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            # shutil.move is appropriate for Docker/Unraid where source and
            # library may be different mounted filesystems. We still verify
            # the destination exists before recording success.
            shutil.move(str(source), str(target))
            if not target.is_file():
                raise OSError("Destination was not created")
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Apply stopped after {len(moved)} file(s). {entry['filename']}: {exc}",
            )

        moved.append(entry["filename"])
        _save_apply_event(
            _event(
                "apply_moved",
                "File moved by Apply Changes",
                filename=entry["filename"],
                source=str(source),
                destination=str(target),
            )
        )

    _save_apply_event(
        _event(
            "apply_completed",
            "Apply Changes completed",
            count=len(moved),
            output_folder=str(output_root),
        )
    )

    return {
        "success": True,
        "moved": moved,
        "count": len(moved),
        "output_folder": str(output_root),
        "message": f"{len(moved)} file(s) moved successfully.",
    }

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "application": "magazine-sorter",
        "version": "0.7.0",
    }
