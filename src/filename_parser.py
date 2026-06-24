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
    }

    match = re.search(r"(\d{4})\.(\d{2})", filename)

    if match:
        result["year"] = int(match.group(1))
        result["month"] = int(match.group(2))

        magazine = filename.split(match.group(0))[0]
        magazine = magazine.replace(".", " ").strip()

        result["magazine"] = magazine

        return result

    lower_filename = filename.lower()

    for month_name, month_number in MONTHS.items():
        if month_name in lower_filename:
            result["month"] = month_number

            issue_match = re.search(r"(\d+)\." + month_name, lower_filename)

            if issue_match:
                result["issue"] = int(issue_match.group(1))

                magazine = lower_filename.split(issue_match.group(0))[0]
                magazine = magazine.replace(".", " ").strip().title()

                result["magazine"] = magazine

            break

    return result