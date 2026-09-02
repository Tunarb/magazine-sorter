def build_destination(parsed):
    magazine = parsed.get("magazine")

    if not magazine:
        return "_REVIEW"

    year = parsed.get("year")
    month = parsed.get("month")
    day = parsed.get("day")
    issue = parsed.get("issue")
    week = parsed.get("week")

    # Dato-baserede blade
    if year and month and day:
        return f"{magazine}/{magazine} - {year}-{month:02d}-{day:02d}.pdf"

    # Månedsblade
    if year and month:
        return f"{magazine}/{magazine} - {year}-{month:02d}.pdf"

    # Ugeblade
    if year and week:
        return f"{magazine}/{magazine} - {year} - Uge {week:02d}.pdf"

    # Nummerbaserede blade
    if issue:
        if year:
            if isinstance(issue, int):
                return f"{magazine}/{magazine} - {year} - Nr {issue:02d}.pdf"

            return f"{magazine}/{magazine} - {year} - Nr {issue}.pdf"

        if isinstance(issue, int):
            return f"{magazine}/{magazine} - Nr {issue:02d}.pdf"

        return f"{magazine}/{magazine} - Nr {issue}.pdf"

    return "_REVIEW"


def get_status(destination):
    if destination == "_REVIEW":
        return "REVIEW"

    return "OK"