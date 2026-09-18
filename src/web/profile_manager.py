from __future__ import annotations

import ast
import shutil
from pathlib import Path
from pprint import pformat

from src.magazine_profiles import PUBLICATIONS
from src.metadata_parser import parse_metadata


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROFILE_FILE = PROJECT_ROOT / "src" / "magazine_profiles.py"


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
    """Analyse one example filename using the existing metadata parser.

    This function deliberately does not add or modify a publication. It only
    turns parser output into a human-readable suggestion for the GUI.
    """
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
        detected.append(
            f"Date {metadata['day']:02d}-{metadata['month']:02d}"
        )
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


def add_publication(
    name: str,
    aliases: list[str],
    profile_type: str,
    include_year: bool,
):
    name = (name or "").strip()
    aliases = [alias.strip() for alias in aliases if alias.strip()]

    if not name:
        raise ValueError("Publication name is required")

    if not aliases:
        aliases = [name]

    if profile_type not in {"issue", "month", "date", "week"}:
        raise ValueError("Invalid publication type")

    if name in PUBLICATIONS:
        raise ValueError(f"Publication already exists: {name}")

    lower_names = {key.lower() for key in PUBLICATIONS}
    if name.lower() in lower_names:
        raise ValueError(f"Publication already exists: {name}")

    profile = {
        "aliases": aliases,
        "type": profile_type,
        "include_year": bool(include_year),
    }

    _append_profile_to_source(name, profile)

    return {
        "name": name,
        **profile,
    }


def update_publication(
    old_name: str,
    name: str,
    aliases: list[str],
    profile_type: str,
    include_year: bool,
):
    """Safely update an existing publication profile in the source file."""
    old_name = (old_name or "").strip()
    name = (name or "").strip()
    aliases = [str(alias).strip() for alias in aliases if str(alias).strip()]

    if not old_name:
        raise ValueError("Existing publication name is required")
    if not name:
        raise ValueError("Publication name is required")
    if not aliases:
        aliases = [name]
    if profile_type not in {"issue", "month", "date", "week"}:
        raise ValueError("Invalid publication type")
    if old_name not in PUBLICATIONS:
        raise ValueError("Publication not found: " + old_name)

    for existing in PUBLICATIONS:
        if existing != old_name and existing.lower() == name.lower():
            raise ValueError("Publication already exists: " + name)

    profile = {
        "aliases": aliases,
        "type": profile_type,
        "include_year": bool(include_year),
    }
    _replace_profile_in_source(old_name, name, profile)

    if old_name != name:
        del PUBLICATIONS[old_name]
    PUBLICATIONS[name] = profile

    return {
        "name": name,
        **profile,
    }


def _replace_profile_in_source(old_name: str, new_name: str, profile: dict):
    source = PROFILE_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(PROFILE_FILE))

    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "PUBLICATIONS"
            for target in node.targets
        )
    ]
    if len(assignments) != 1 or not isinstance(assignments[0].value, ast.Dict):
        raise RuntimeError("Could not safely locate PUBLICATIONS in magazine_profiles.py")

    publications = assignments[0].value
    target_key = None
    target_value = None
    for key_node, value_node in zip(publications.keys, publications.values):
        try:
            key_value = ast.literal_eval(key_node)
        except Exception:
            continue
        if key_value == old_name:
            target_key = key_node
            target_value = value_node
            break

    if target_key is None or target_value is None:
        raise RuntimeError("Could not safely locate publication: " + old_name)

    def offset(line_number, column):
        lines = source.splitlines(True)
        return sum(len(line) for line in lines[:line_number - 1]) + column

    start = offset(target_key.lineno, target_key.col_offset)
    end = offset(target_value.end_lineno, target_value.end_col_offset)

    alias_lines = ",\n".join(
        "        " + repr(alias) for alias in profile["aliases"]
    )
    replacement = (
        "    " + repr(new_name) + ": {\n"
        "        \"aliases\": [\n"
        + alias_lines + "\n"
        + "        ],\n"
        + "        \"type\": " + repr(profile["type"]) + ",\n"
        + "        \"include_year\": " + repr(bool(profile["include_year"])) + ",\n"
        + "    }"
    )

    new_source = source[:start] + replacement + source[end:]
    ast.parse(new_source, filename=str(PROFILE_FILE))

    backup = PROFILE_FILE.with_suffix(PROFILE_FILE.suffix + ".bak")
    shutil.copy2(PROFILE_FILE, backup)

    temp_file = PROFILE_FILE.with_suffix(PROFILE_FILE.suffix + ".tmp")
    temp_file.write_text(new_source, encoding="utf-8")
    temp_file.replace(PROFILE_FILE)


def _append_profile_to_source(name: str, profile: dict):
    source = PROFILE_FILE.read_text(encoding="utf-8")

    tree = ast.parse(source, filename=str(PROFILE_FILE))

    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "PUBLICATIONS"
            for target in node.targets
        )
    ]

    if len(assignments) != 1:
        raise RuntimeError(
            "Could not safely locate PUBLICATIONS in magazine_profiles.py"
        )

    assignment = assignments[0]
    if not isinstance(assignment.value, ast.Dict):
        raise RuntimeError("PUBLICATIONS is not a dictionary")

    close_index = source.rfind("}")
    if close_index < 0:
        raise RuntimeError("Could not find the end of PUBLICATIONS")

    entry = (
        "\n    "
        + repr(name)
        + ": "
        + pformat(profile, width=88, sort_dicts=False)
        + ",\n"
    )

    new_source = source[:close_index] + entry + source[close_index:]

    ast.parse(new_source, filename=str(PROFILE_FILE))

    backup = PROFILE_FILE.with_suffix(PROFILE_FILE.suffix + ".bak")
    shutil.copy2(PROFILE_FILE, backup)

    temp_file = PROFILE_FILE.with_suffix(PROFILE_FILE.suffix + ".tmp")
    temp_file.write_text(new_source, encoding="utf-8")
    temp_file.replace(PROFILE_FILE)

    # Keep the in-memory profile registry in sync so the new publication
    # is available immediately without requiring a server restart.
    PUBLICATIONS[name] = profile
