from __future__ import annotations

import json
import re
import os
import uuid
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("MAGAZINE_SORTER_DATA_DIR", str(PROJECT_ROOT / "data")))
RUN_DIR = DATA_DIR / "dry_run"
HISTORY_DIR = DATA_DIR / "runs"
CHECKPOINT_FILE = RUN_DIR / "current.json"


def _now():
    return datetime.now(timezone.utc).isoformat()


def new_run_id():
    """Create a sortable, human-readable run identifier."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{stamp}_{uuid.uuid4().hex[:8]}"


def file_fingerprint(file) -> str:
    """Stable-enough fingerprint for deciding whether a cached result is reusable."""
    path = Path(file.path).resolve()
    stat = path.stat()
    return f"{path}|{stat.st_size}|{stat.st_mtime_ns}"


def load_checkpoint() -> Optional[dict]:
    if not CHECKPOINT_FILE.exists():
        return None

    try:
        with CHECKPOINT_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, TypeError):
        return None

    if not isinstance(data, dict):
        return None

    return data


def save_checkpoint(
    input_folder: Path,
    total: int,
    items_by_fingerprint: Dict[str, dict],
    status: str = "running",
    run_id: Optional[str] = None,
    review_decisions: Optional[dict] = None,
    events: Optional[list] = None,
    created_at: Optional[str] = None,
    run_type: str = "dry_run",
) -> dict:
    """Persist the resumable current run and return the saved payload."""
    RUN_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "schema_version": 2,
        "run_id": run_id or new_run_id(),
        "input_folder": str(Path(input_folder).resolve()),
        "total": total,
        "status": status,
        "run_type": run_type,
        "created_at": created_at or _now(),
        "updated_at": _now(),
        "items": items_by_fingerprint,
        "review_decisions": review_decisions or {},
        "events": events or [],
    }

    temporary = RUN_DIR / f".{CHECKPOINT_FILE.stem}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

        # Windows can briefly deny the replace when an antivirus/indexer has
        # the destination open. Retry the atomic replace instead of letting a
        # transient persistence race become a file-classification ERROR.
        last_error = None
        for attempt in range(10):
            try:
                temporary.replace(CHECKPOINT_FILE)
                return payload
            except PermissionError as exc:
                last_error = exc
                if attempt == 9:
                    raise
                time.sleep(0.1)
        if last_error:
            raise last_error
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

    return payload


def clear_checkpoint() -> None:
    try:
        CHECKPOINT_FILE.unlink()
    except FileNotFoundError:
        pass


def checkpoint_matches_input(checkpoint: Optional[dict], input_folder: Path) -> bool:
    if not checkpoint:
        return False

    return checkpoint.get("input_folder") == str(Path(input_folder).resolve())


def checkpoint_items(checkpoint: Optional[dict]) -> Dict[str, dict]:
    if not checkpoint:
        return {}

    items = checkpoint.get("items")
    if not isinstance(items, dict):
        return {}

    return items


def checkpoint_review_decisions(checkpoint: Optional[dict]) -> dict:
    if not checkpoint:
        return {}

    decisions = checkpoint.get("review_decisions")
    return decisions if isinstance(decisions, dict) else {}


def checkpoint_events(checkpoint: Optional[dict]) -> list:
    if not checkpoint:
        return []

    events = checkpoint.get("events")
    return events if isinstance(events, list) else []


def ensure_checkpoint_identity(checkpoint: dict) -> dict:
    """Upgrade an older v1 checkpoint in memory without losing its data."""
    if not checkpoint:
        return checkpoint

    if not checkpoint.get("run_id"):
        updated_at = checkpoint.get("updated_at") or _now()
        compact = re.sub(r"[^0-9]", "", str(updated_at))[:14] or datetime.now().strftime("%Y%m%d%H%M%S")
        checkpoint["run_id"] = f"legacy_{compact}_{uuid.uuid4().hex[:8]}"

    checkpoint.setdefault("schema_version", 2)
    checkpoint.setdefault("created_at", checkpoint.get("updated_at") or _now())
    checkpoint.setdefault("review_decisions", {})
    checkpoint.setdefault("events", [])
    return checkpoint


def _history_path(run_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(run_id))
    return HISTORY_DIR / safe_id


def save_history_run(run_record: dict) -> Path:
    """Save a complete immutable-ish snapshot of a run, updated when Review changes."""
    run_id = run_record.get("run_id")
    if not run_id:
        raise ValueError("run_record must contain run_id")

    target_dir = _history_path(run_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "run.json"
    temporary = target.with_suffix(".tmp")

    payload = dict(run_record)
    payload["schema_version"] = max(2, int(payload.get("schema_version", 2)))
    payload["updated_at"] = _now()

    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    temporary.replace(target)
    return target


def load_history_run(run_id: str) -> Optional[dict]:
    target = _history_path(run_id) / "run.json"
    if not target.exists():
        return None

    try:
        with target.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, TypeError):
        return None

    return data if isinstance(data, dict) else None


def list_history_runs() -> list[dict]:
    """Return history summaries, newest first."""
    if not HISTORY_DIR.exists():
        return []

    runs = []
    for path in HISTORY_DIR.iterdir():
        if not path.is_dir():
            continue

        run_file = path / "run.json"
        if not run_file.exists():
            continue

        try:
            with run_file.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError, TypeError):
            continue

        if not isinstance(data, dict) or not data.get("run_id"):
            continue

        stats = data.get("statistics") or {}
        runs.append(
            {
                "run_id": data["run_id"],
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "status": data.get("status"),
                "run_type": data.get("run_type", "dry_run"),
                "input_folder": data.get("input_folder"),
                "statistics": {
                    # History is an immutable snapshot. Never derive the file
                    # count from the current input folder because files may
                    # have been moved after the run completed.
                    "files": max(
                        int(stats.get("files", 0) or 0),
                        int(data.get("total", 0) or 0),
                    ),
                    "processed": stats.get("processed", 0),
                    "auto": stats.get("auto", 0),
                    "review": stats.get("review", 0),
                    "ignore": stats.get("ignore", 0),
                    "errors": stats.get("errors", 0),
                    "collisions": stats.get("collisions", 0),
                },
            }
        )

    runs.sort(
        key=lambda item: item.get("created_at") or item.get("updated_at") or "",
        reverse=True,
    )
    return runs
