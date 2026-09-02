import re

MONTHS = {
    "januar": 1,
    "februar": 2,
    "marts": 3,
    "april": 4,
    "maj": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "december": 12,
}


def parse_filename(filename):
    result = {
        "filename": filename,
        "magazine": None,
        "series": None,
        "year": None,
        "month": None,
        "day": None,
        "issue": None,
        "week": None,
    }

    lower_filename = filename.lower()

    # Regel 1: År + måned som 2020.08
    match = re.search(r"\b(20\d{2})\.(\d{1,2})\b", lower_filename)

    if match:
        result["year"] = int(match.group(1))
        result["month"] = int(match.group(2))

        magazine = filename[:match.start()]
        magazine = magazine.replace(".", " ").strip()

        result["magazine"] = magazine
        return result

    # Regel 2: Uge + år
    # Eksempel: Uge.34.2024
    week_match = re.search(
        r"\buge\.(\d{1,2})\.(20\d{2})\b",
        lower_filename,
    )

    if week_match:
        result["week"] = int(week_match.group(1))
        result["year"] = int(week_match.group(2))

        magazine = filename[:week_match.start()]
        magazine = magazine.replace(".", " ").strip()

        result["magazine"] = magazine
        return result

    # Regel 3: Nr/No + nummer + dato
    # Eksempel:
    # Hjemmet.Nr.11.09.Marts.2026
    # Gastro.Nr.231.05.Marts.2026
    issue_date_match = re.search(
        r"\b(?:nr|no)\.(\d+)\.(\d{1,2})\.("
        + "|".join(MONTHS.keys())
        + r")\.(20\d{2})\b",
        lower_filename,
    )

    if issue_date_match:
        result["issue"] = int(issue_date_match.group(1))
        result["day"] = int(issue_date_match.group(2))
        result["month"] = MONTHS[issue_date_match.group(3)]
        result["year"] = int(issue_date_match.group(4))

        magazine = filename[:issue_date_match.start()]
        magazine = magazine.replace(".", " ").strip()

        result["magazine"] = magazine
        return result

    # Regel 4: Dag + månedsnavn + år
    # Eksempel:
    # 7.TV-Dage.7.Juni.2025
    # Billed-Bladet.12.Juni.2025
    date_match = re.search(
        r"\b(\d{1,2})\.("
        + "|".join(MONTHS.keys())
        + r")\.(20\d{2})\b",
        lower_filename,
    )

    if date_match:
        result["day"] = int(date_match.group(1))
        result["month"] = MONTHS[date_match.group(2)]
        result["year"] = int(date_match.group(3))

        magazine = filename[:date_match.start()]
        magazine = magazine.replace(".", " ").strip()

        result["magazine"] = magazine
        return result

    # Regel 5: Månedsnavn + år
    # Eksempel: Euroman.Januar.2026
    month_date_match = re.search(
        r"\b("
        + "|".join(MONTHS.keys())
        + r")\.(20\d{2})\b",
        lower_filename,
    )

    if month_date_match:
        result["month"] = MONTHS[month_date_match.group(1)]
        result["year"] = int(month_date_match.group(2))

        magazine = filename[:month_date_match.start()]
        magazine = magazine.replace(".", " ").strip()

        result["magazine"] = magazine
        return result

    # Regel 6: Nr/No + nummer + år
    # Eksempel:
    # Basserne.Nr.1244
    # Hjemmet.Nr.11.2026
    issue_match = re.search(
        r"\b(?:nr|no)\.(\d+)",
        lower_filename,
    )

    if issue_match:
        result["issue"] = int(issue_match.group(1))

        year_match = re.search(
            r"\b(20\d{2})\b",
            lower_filename[issue_match.end():],
        )

        if year_match:
            result["year"] = int(year_match.group(1))

        magazine = filename[:issue_match.start()]
        magazine = magazine.replace(".", " ").strip()

        result["magazine"] = magazine
        return result

    return result