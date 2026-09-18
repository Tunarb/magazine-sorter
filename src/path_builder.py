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

def build_special_destination(publication, title, year=None, extension=".pdf"):
    """Build a Komga-friendly destination for a special issue.

    Specials stay in the publication's existing Series folder instead of
    creating a Specials subfolder, because Komga treats subfolders as Series.
    """
    publication = str(publication or "").strip()
    title = str(title or "").strip()
    extension = str(extension or ".pdf").strip() or ".pdf"

    if not publication or not title:
        return "_REVIEW"

    if not extension.startswith("."):
        extension = "." + extension

    filename = f"{publication} - {title}{extension}"
    if year is not None:
        filename = f"{publication} - {year} - {title}{extension}"

    return f"{publication}/{filename}"


def build_standalone_destination(filename, oneshots_dir="_oneshots"):
    """Build a Komga One-Shot destination without inventing a Series folder.

    The directory name follows Komga's configurable One-Shots directory
    convention. The file name is deliberately preserved because this is a
    manual classification and the sorter should not guess a new title.
    """
    filename = str(filename or "").strip()
    oneshots_dir = str(oneshots_dir or "_oneshots").strip().strip("/\\")

    if not filename or not oneshots_dir:
        return "_REVIEW"

    return f"{oneshots_dir}/{filename}"
