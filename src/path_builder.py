def build_destination(parsed):
    magazine = parsed.get("magazine")

    if not magazine:
        return "_REVIEW"

    year = parsed.get("year")
    month = parsed.get("month")
    issue = parsed.get("issue")

    # Månedsblade
    if year and month:
        return f"{magazine}/{magazine} - {year}-{month:02d}.pdf"

    # Nummerbaserede blade
    if issue:
        if year:
            return f"{magazine}/{magazine} - {year} - Nr {issue:02d}.pdf"

        return f"{magazine}/{magazine} - Nr {issue:02d}.pdf"

    return "_REVIEW"


def get_status(destination):
    if destination == "_REVIEW":
        return "REVIEW"

    return "OK"