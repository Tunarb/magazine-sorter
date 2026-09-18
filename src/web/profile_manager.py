from __future__ import annotations

import json
import os
from pathlib import Path

from src.magazine_profiles import PUBLICATIONS
from src.metadata_parser import parse_metadata


SRC_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = SRC_ROOT.parent
DATA_DIR = Path(os.getenv("MAGAZINE_SORTER_DATA_DIR", str(SRC_ROOT / "data")))
PROFILE_FILE = DATA_DIR / "publication_profiles.json"


TYPE_LABELS = {
    "issue": "Issue number + year",
    "month": "Month + year",
    "date": "Date + year",
    "week": "Week number + year",
}


def list_publications():
    return [
        {
            "name": name,
            "aliases": list(profile.get("aliases", [])),
            "type": profile.get("type", "issue"),
            "type_label": TYPE_LABELS.get(
                profile.get("type", "issue"),
                profile.get("type", "issue"),
            ),
            "include_year": bool(profile.get("include_year", True)),
        }
        for name, profile in sorted(
            PUBLICATIONS.items(),
            key=lambda item: item[0].lower(),
        )
    ]


def suggest_profile(filename: str, publication_name: str = ""):
    """Analyse one example filename using the existing metadata parser."""
    metadata = parse_metadata(filename or "")

    if metadata.get("issue") is not None:
        profile_type = "issue"
    elif metadata.get("day") is not None:
        profile_type = "date"
    elif metadata.get("week") is not None:
        profile_type = "week"
    elif metadata.get("month") is not None:
        profile_type = "month"
    else:
        profile_type = "issue"

    detected = []

    if metadata.get("issue") is not None:
        detected.append(f"Issue {metadata['issue']}")
    if metadata.get("day") is not None and metadata.get("month") is not None:
        detected.append(f"Date {metadata['day']:02d}-{metadata['month']:02d}")
    elif metadata.get("month") is not None:
        detected.append(f"Month {metadata['month']:02d}")
    if metadata.get("week") is not None:
        detected.append(f"Week {metadata['week']:02d}")
    if metadata.get("year") is not None:
        detected.append(f"Year {metadata['year']}")

    complete = (
        metadata.get("year") is not None
        and (
            metadata.get("issue") is not None
            or metadata.get("month") is not None
            or metadata.get("day") is not None
            or metadata.get("week") is not None
        )
    )

    if complete:
        confidence = "good"
        message = "The existing parser found a clear release pattern."
    elif detected:
        confidence = "partial"
        message = "The parser found some metadata, but the pattern is incomplete."
    else:
        confidence = "unknown"
        message = "The parser could not find a release pattern in this filename."

    return {
        "type": profile_type,
        "type_label": TYPE_LABELS[profile_type],
        "metadata": metadata,
        "detected": detected,
        "include_year": metadata.get("year") is not None,
        "confidence": confidence,
        "message": message,
        "publication_name": publication_name.strip(),
    }


def _normalize_profile(aliases, profile_type, include_year):
    aliases = [str(alias).strip() for alias in aliases if str(alias).strip()]
    if not aliases:
        raise ValueError("At least one alias is required")
    if profile_type not in {"issue", "month", "date", "week"}:
        raise ValueError("Invalid publication type")
    return {
        "aliases": aliases,
        "type": profile_type,
        "include_year": bool(include_year),
    }


def _load_persistent_profile_state():
    if not PROFILE_FILE.exists():
        return {"overrides": {}, "removed": []}
    try:
        saved = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"overrides": {}, "removed": []}
    if not isinstance(saved, dict):
        return {"overrides": {}, "removed": []}
    overrides = saved.get("overrides")
    removed = saved.get("removed")
    return {
        "overrides": overrides if isinstance(overrides, dict) else {},
        "removed": removed if isinstance(removed, list) else [],
    }


def _save_persistent_profiles(overrides, removed):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "overrides": overrides,
        "removed": sorted(set(removed)),
    }
    temporary = PROFILE_FILE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(PROFILE_FILE)


def add_publication(
    name: str,
    aliases: list[str],
    profile_type: str,
    include_year: bool,
):
    name = (name or "").strip()
    if not name:
        raise ValueError("Publication name is required")

    if name in PUBLICATIONS:
        raise ValueError(f"Publication already exists: {name}")

    lower_names = {key.lower() for key in PUBLICATIONS}
    if name.lower() in lower_names:
        raise ValueError(f"Publication already exists: {name}")

    profile = _normalize_profile(aliases, profile_type, include_year)
    state = _load_persistent_profile_state()
    overrides = dict(state["overrides"])
    removed = list(state["removed"])
    overrides[name] = profile
    removed = [item for item in removed if item != name]

    PUBLICATIONS[name] = profile
    try:
        _save_persistent_profiles(overrides, removed)
    except Exception:
        del PUBLICATIONS[name]
        raise

    return {"name": name, **profile}


def update_publication(
    old_name: str,
    name: str,
    aliases: list[str],
    profile_type: str,
    include_year: bool,
):
    old_name = (old_name or "").strip()
    name = (name or "").strip()

    if not old_name:
        raise ValueError("Existing publication name is required")
    if not name:
        raise ValueError("Publication name is required")
    if old_name not in PUBLICATIONS:
        raise ValueError("Publication not found: " + old_name)

    for existing in PUBLICATIONS:
        if existing != old_name and existing.lower() == name.lower():
            raise ValueError("Publication already exists: " + name)

    profile = _normalize_profile(aliases, profile_type, include_year)
    old_profile = PUBLICATIONS[old_name]
    state = _load_persistent_profile_state()
    overrides = dict(state["overrides"])
    removed = list(state["removed"])

    if old_name != name:
        del PUBLICATIONS[old_name]
        overrides.pop(old_name, None)
        if old_name not in removed:
            removed.append(old_name)
    PUBLICATIONS[name] = profile
    overrides[name] = profile
    removed = [item for item in removed if item != name]

    try:
        _save_persistent_profiles(overrides, removed)
    except Exception:
        del PUBLICATIONS[name]
        PUBLICATIONS[old_name] = old_profile
        raise

    return {"name": name, **profile}
