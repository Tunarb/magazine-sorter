import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.web.auto_run import DEFAULT_AUTO_RUN, validate_auto_run


SRC_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = SRC_ROOT.parent
DATA_DIR = Path(os.getenv("MAGAZINE_SORTER_DATA_DIR", str(SRC_ROOT / "data")))
SETTINGS_FILE = DATA_DIR / "settings.json"

DEFAULT_SETTINGS = {
    "schema_version": 1,
    "interface": {
        "language": "en",
    },
    "folders": {
        "input_folder": str(PROJECT_ROOT / "ocr_test_data"),
        "output_folder": str(PROJECT_ROOT / "library"),
    },
    "ocr": {
        "enabled": True,
        "language": "dan",
        "dpi": 300,
        "page_stages": [[1], [4], [5]],
    },
    "processing": {
        "dry_run_default": True,
    },
    "safety": {
        "never_overwrite": True,
        "never_move_review": True,
        "stop_on_collision": True,
    },
    "auto_run": DEFAULT_AUTO_RUN.copy(),
}

OCR_LANGUAGES = {
    "dan": "Danish",
}


def _copy_defaults() -> dict:
    return json.loads(json.dumps(DEFAULT_SETTINGS))


def _deep_merge(defaults: dict, saved: dict) -> dict:
    result = _copy_defaults()
    for section, values in saved.items():
        if isinstance(values, dict) and isinstance(result.get(section), dict):
            for key, value in values.items():
                result[section][key] = value
        else:
            result[section] = values
    return result


def load_settings() -> dict:
    if not SETTINGS_FILE.exists():
        return _copy_defaults()

    try:
        with SETTINGS_FILE.open("r", encoding="utf-8") as handle:
            saved = json.load(handle)
    except (OSError, ValueError, TypeError):
        return _copy_defaults()

    if not isinstance(saved, dict):
        return _copy_defaults()

    return _deep_merge(DEFAULT_SETTINGS, saved)


def save_settings(settings: dict) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = _deep_merge(DEFAULT_SETTINGS, settings)
    payload["schema_version"] = 1

    temporary = SETTINGS_FILE.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    temporary.replace(SETTINGS_FILE)
    return payload


def _env_override(name: str) -> Optional[str]:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None


def get_effective_settings() -> dict:
    settings = load_settings()

    input_override = _env_override("MAGAZINE_SORTER_INPUT_FOLDER")
    output_override = _env_override("MAGAZINE_SORTER_OUTPUT_FOLDER")

    if input_override:
        settings["folders"]["input_folder"] = input_override
    if output_override:
        settings["folders"]["output_folder"] = output_override

    return settings


def env_managed_paths() -> Dict[str, bool]:
    return {
        "input_folder": bool(_env_override("MAGAZINE_SORTER_INPUT_FOLDER")),
        "output_folder": bool(_env_override("MAGAZINE_SORTER_OUTPUT_FOLDER")),
    }


def get_input_folder() -> Path:
    return Path(get_effective_settings()["folders"]["input_folder"]).expanduser()


def get_output_folder() -> Path:
    return Path(get_effective_settings()["folders"]["output_folder"]).expanduser()


def normalize_page_stages(value) -> List[List[int]]:
    if not isinstance(value, list):
        raise ValueError("OCR stages must be a list")

    stages = []
    for stage in value:
        if not isinstance(stage, list) or not stage:
            raise ValueError("Each OCR stage must contain at least one page")
        pages = []
        for page in stage:
            if isinstance(page, bool):
                raise ValueError("OCR page numbers must be positive integers")
            try:
                number = int(page)
            except (TypeError, ValueError):
                raise ValueError("OCR page numbers must be positive integers")
            if number < 1 or number > 9999:
                raise ValueError("OCR page numbers must be between 1 and 9999")
            if number not in pages:
                pages.append(number)
        stages.append(pages)

    if not stages:
        raise ValueError("At least one OCR stage is required")

    return stages


def parse_page_stages(text: str) -> List[List[int]]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Enter at least one OCR page stage")

    stages = []
    for raw_stage in text.split("|"):
        raw_stage = raw_stage.strip()
        if not raw_stage:
            raise ValueError("OCR stages cannot be empty")
        pages = []
        for raw_page in raw_stage.split(","):
            raw_page = raw_page.strip()
            if not raw_page:
                raise ValueError("OCR page numbers cannot be empty")
            if not raw_page.isdigit():
                raise ValueError("OCR page numbers must be positive integers")
            pages.append(int(raw_page))
        stages.append(pages)

    return normalize_page_stages(stages)


def page_stages_to_text(stages: List[List[int]]) -> str:
    return " | ".join(", ".join(str(page) for page in stage) for stage in stages)


def validate_settings_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Settings payload must be an object")

    current = load_settings()
    folders = payload.get("folders") or {}
    ocr = payload.get("ocr") or {}

    input_folder = str(folders.get("input_folder", current["folders"]["input_folder"])).strip()
    output_folder = str(folders.get("output_folder", current["folders"]["output_folder"])).strip()
    if not input_folder:
        raise ValueError("Input folder is required")
    if not output_folder:
        raise ValueError("Library/output folder is required")

    try:
        input_path = Path(input_folder).expanduser().resolve()
        output_path = Path(output_folder).expanduser().resolve()
        if input_path == output_path:
            raise ValueError("Input and library/output folders must be different")
        if input_path in output_path.parents or output_path in input_path.parents:
            raise ValueError("Input and library/output folders must not contain one another")
    except OSError as exc:
        raise ValueError("Could not validate the configured folder paths: %s" % exc)

    language = str(ocr.get("language", current["ocr"]["language"])).strip().lower()
    if language not in OCR_LANGUAGES:
        raise ValueError("The selected OCR language is not available")

    try:
        dpi = int(ocr.get("dpi", current["ocr"]["dpi"]))
    except (TypeError, ValueError):
        raise ValueError("OCR DPI must be an integer")
    if dpi < 100 or dpi > 600:
        raise ValueError("OCR DPI must be between 100 and 600")

    stages_value = ocr.get("page_stages", current["ocr"]["page_stages"])
    if isinstance(stages_value, str):
        stages = parse_page_stages(stages_value)
    else:
        stages = normalize_page_stages(stages_value)

    enabled = bool(ocr.get("enabled", current["ocr"].get("enabled", True)))

    result = _copy_defaults()
    result["folders"] = {
        "input_folder": input_folder,
        "output_folder": output_folder,
    }
    result["ocr"] = {
        "enabled": enabled,
        "language": language,
        "dpi": dpi,
        "page_stages": stages,
    }
    # Safety is intentionally enforced by the application. The GUI displays
    # these values but does not allow them to be weakened.
    result["processing"]["dry_run_default"] = True
    result["safety"] = _copy_defaults()["safety"]
    result["interface"]["language"] = "en"
    result["auto_run"] = validate_auto_run(payload.get("auto_run", current.get("auto_run", DEFAULT_AUTO_RUN)))
    return result


def test_folder_access(input_folder: Path, output_folder: Path) -> dict:
    input_folder = Path(input_folder).expanduser()
    output_folder = Path(output_folder).expanduser()

    result = {
        "input": {"path": str(input_folder), "exists": input_folder.exists(), "readable": False},
        "output": {"path": str(output_folder), "exists": output_folder.exists(), "writable": False},
        "checks": [],
        "ok": True,
    }

    if input_folder.exists() and input_folder.is_dir():
        try:
            next(input_folder.iterdir(), None)
            result["input"]["readable"] = True
        except OSError:
            result["input"]["readable"] = False
        if result["input"]["readable"]:
            result["checks"].append({"ok": True, "message": "Input folder is accessible."})
        else:
            result["checks"].append({"ok": False, "message": "Input folder exists but cannot be read."})
    else:
        result["checks"].append({"ok": False, "message": "Input folder does not exist or is not a folder."})

    if output_folder.exists():
        if output_folder.is_dir():
            try:
                probe = output_folder / ".magazine_sorter_write_test"
                with probe.open("w", encoding="utf-8") as handle:
                    handle.write("test")
                probe.unlink()
                result["output"]["writable"] = True
            except OSError:
                result["output"]["writable"] = False
        if result["output"]["writable"]:
            result["checks"].append({"ok": True, "message": "Library/output folder is writable."})
        else:
            result["checks"].append({"ok": False, "message": "Library/output folder is not writable."})
    else:
        parent = output_folder.parent
        try:
            parent_ok = parent.exists() and parent.is_dir() and os.access(str(parent), os.W_OK)
        except OSError:
            parent_ok = False
        if parent_ok:
            result["checks"].append({"ok": True, "message": "Library/output folder does not exist yet, but its parent is writable and it can be created."})
            result["output"]["writable"] = True
        else:
            result["checks"].append({"ok": False, "message": "Library/output folder does not exist and its parent cannot be used to create it."})

    try:
        input_resolved = input_folder.resolve()
        output_resolved = output_folder.resolve()
        if input_resolved == output_resolved:
            result["checks"].append({"ok": False, "message": "Input and library/output folders must be different."})
        elif input_resolved in output_resolved.parents or output_resolved in input_resolved.parents:
            result["checks"].append({"ok": False, "message": "Input and library/output folders must not contain one another."})
        else:
            result["checks"].append({"ok": True, "message": "Input and library/output folders are separate."})
    except OSError:
        result["checks"].append({"ok": False, "message": "Could not resolve the configured folder paths."})

    result["ok"] = all(check["ok"] for check in result["checks"])
    return result
