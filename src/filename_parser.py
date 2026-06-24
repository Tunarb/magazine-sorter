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
        "year": None,
        "month": None,
        "issue": None,
        "week": None,
    }

    # Regel 1: Bil.Magasinet.2020.08.pdf
    match = re.search(r"(\d{4})\.(\d{2})", filename)

    if match:
        result["year"] = int(match.group(1))
        result["month"] = int(match.group(2))

        magazine = filename.split(match.group(0))[0]
        magazine = magazine.replace(".", " ").strip()

        result["magazine"] = magazine

        return result

    lower_filename = filename.lower()

    # Regel 2: Månedsnavne
    for month_name, month_number in MONTHS.items():
        if month_name in lower_filename:

            result["month"] = month_number

            # Familie.Journal.12.Maj...
            issue_match = re.search(r"(\d+)\." + month_name, lower_filename)

            if issue_match:
                result["issue"] = int(issue_match.group(1))

                magazine = lower_filename.split(issue_match.group(0))[0]
                magazine = magazine.replace(".", " ").strip().title()

                result["magazine"] = magazine

            # Euroman.Januar.2026.pdf
            year_match = re.search(r"(20\d{2})", lower_filename)

            if year_match:
                result["year"] = int(year_match.group(1))

            if not result["magazine"]:
                magazine = lower_filename.split(month_name)[0]
                magazine = magazine.replace(".", " ").strip().title()

                result["magazine"] = magazine

            return result

    # Regel 3: Soendag.Uge.34.2024.pdf
    week_match = re.search(r"uge\.(\d+)\.(20\d{2})", lower_filename)

    if week_match:
        result["week"] = int(week_match.group(1))
        result["year"] = int(week_match.group(2))

        magazine = lower_filename.split(week_match.group(0))[0]
        magazine = magazine.replace(".", " ").strip().title()

        result["magazine"] = magazine

        return result

    # Regel 4: No.390.2024 eller Nr.06.2026
    issue_match = re.search(r"(?:no|nr)\.(\d+)", lower_filename)

    if issue_match:
        result["issue"] = int(issue_match.group(1))

        year_match = re.search(r"\b(20\d{2})\b", lower_filename)

        if year_match:
            result["year"] = int(year_match.group(1))

        magazine = lower_filename.split(issue_match.group(0))[0]
        magazine = magazine.replace(".", " ").strip().title()

        result["magazine"] = magazine

        return result

    return result