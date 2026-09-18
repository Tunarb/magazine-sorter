from __future__ import annotations

from datetime import datetime, timedelta

DEFAULT_AUTO_RUN = {
    "enabled": False,
    "mode": "dry_run",
    "schedule": "daily",
    "time": "03:00",
    "weekday": 0,
    "interval_hours": 24,
    "min_file_age_minutes": 5,
    "run_on_startup": False,
    "max_files_per_run": 0,
    "skip_unchanged": True,
}

AUTO_RUN_MODES = {
    "dry_run": "Scheduled Dry Run",
    "safe_apply": "Scheduled Safe Apply",
}

AUTO_RUN_SCHEDULES = {
    "daily": "Daily",
    "weekly": "Weekly",
    "interval": "Every X hours",
}

WEEKDAYS = [
    (0, "Monday"),
    (1, "Tuesday"),
    (2, "Wednesday"),
    (3, "Thursday"),
    (4, "Friday"),
    (5, "Saturday"),
    (6, "Sunday"),
]


def validate_auto_run(value: dict) -> dict:
    if not isinstance(value, dict):
        value = {}

    result = dict(DEFAULT_AUTO_RUN)
    result.update({key: value[key] for key in DEFAULT_AUTO_RUN if key in value})

    result["enabled"] = bool(result["enabled"])
    if result["mode"] not in AUTO_RUN_MODES:
        raise ValueError("Auto Run mode is invalid")
    if result["schedule"] not in AUTO_RUN_SCHEDULES:
        raise ValueError("Auto Run schedule is invalid")

    try:
        hour, minute = str(result["time"]).split(":", 1)
        hour = int(hour)
        minute = int(minute)
    except (ValueError, TypeError):
        raise ValueError("Auto Run time must use HH:MM format")
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("Auto Run time must use HH:MM format")
    result["time"] = f"{hour:02d}:{minute:02d}"

    try:
        result["weekday"] = int(result["weekday"])
    except (TypeError, ValueError):
        raise ValueError("Auto Run weekday is invalid")
    if result["weekday"] not in range(7):
        raise ValueError("Auto Run weekday is invalid")

    try:
        result["interval_hours"] = int(result["interval_hours"])
    except (TypeError, ValueError):
        raise ValueError("Auto Run interval must be an integer number of hours")
    if result["interval_hours"] < 6 or result["interval_hours"] > 168:
        raise ValueError("Auto Run interval must be between 6 and 168 hours")

    try:
        result["min_file_age_minutes"] = int(result["min_file_age_minutes"])
    except (TypeError, ValueError):
        raise ValueError("Minimum file age must be an integer number of minutes")
    if result["min_file_age_minutes"] < 0 or result["min_file_age_minutes"] > 1440:
        raise ValueError("Minimum file age must be between 0 and 1440 minutes")

    try:
        result["max_files_per_run"] = int(result["max_files_per_run"])
    except (TypeError, ValueError):
        raise ValueError("Maximum files per run must be an integer")
    if result["max_files_per_run"] < 0 or result["max_files_per_run"] > 10000:
        raise ValueError("Maximum files per run must be between 0 and 10000")

    result["run_on_startup"] = bool(result["run_on_startup"])
    result["skip_unchanged"] = bool(result["skip_unchanged"])
    return result


def next_run_at(settings: dict, now: datetime | None = None) -> datetime:
    settings = validate_auto_run(settings)
    now = now or datetime.now().astimezone()

    if settings["schedule"] == "interval":
        return now + timedelta(hours=settings["interval_hours"])

    hour, minute = [int(part) for part in settings["time"].split(":")]
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if settings["schedule"] == "daily":
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    days_ahead = (settings["weekday"] - now.weekday()) % 7
    candidate += timedelta(days=days_ahead)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def schedule_summary(settings: dict) -> str:
    settings = validate_auto_run(settings)
    if settings["schedule"] == "daily":
        return f"Daily at {settings['time']}"
    if settings["schedule"] == "weekly":
        weekday = dict(WEEKDAYS).get(settings["weekday"], "Monday")
        return f"Weekly on {weekday} at {settings['time']}"
    return f"Every {settings['interval_hours']} hours"
