def build_destination(parsed):
    magazine = parsed.get("magazine")

    if not magazine:
        return "_REVIEW"

    year = parsed.get("year")
    month = parsed.get("month")
    issue = parsed.get("issue")

    if year and month:
        return f"{magazine}/{magazine} - {year}-{month:02d}.pdf"

    if issue:
        return f"{magazine}/{magazine} - Nr {issue}.pdf"

    return "_REVIEW"


def get_status(destination):
    if destination == "_REVIEW":
        return "REVIEW"

    return "OK"