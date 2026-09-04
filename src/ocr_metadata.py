import re


def parse_ocr_metadata(text):
    result = {
        "issue": None,
        "year": None,
    }

    if not text:
        return result

    normalized = re.sub(r"\s+", " ", text).strip()

    # Nr./No. + issue + optional text + year
    # Examples:
    # "Nr. 5 2025"
    # "Nr. 5 24. apr. - 26. maj. 2025"
    match = re.search(
        r"\b(?:nr|no)\.?\s*(\d{1,3})(?:\s+.*?)*?\s+(20\d{2})\b",
        normalized,
        flags=re.IGNORECASE,
    )

    if match:
        result["issue"] = int(match.group(1))
        result["year"] = int(match.group(2))
        return result

    # Issue/year written as 17/2025 or 17-2025
    match = re.search(
        r"(?<!\d)(\d{1,3})\s*[/\-]\s*(20\d{2})(?!\d)",
        normalized,
    )

    if match:
        result["issue"] = int(match.group(1))
        result["year"] = int(match.group(2))
        return result

    return result