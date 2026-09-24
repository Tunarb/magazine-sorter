from pathlib import Path

try:
    from .models import MagazineFile
except ImportError:
    from models import MagazineFile


SUPPORTED_EXTENSIONS = [
    ".pdf",
    ".cbz",
]


def find_magazines(folder):
    folder = Path(folder)

    files = []

    for file in folder.rglob("*"):
        if not file.is_file():
            continue

        if file.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        # Quarantined duplicate files are deliberately kept under the input
        # tree, but must never re-enter a Dry Run.
        try:
            relative = file.relative_to(folder)
        except ValueError:
            continue
        if "_duplicates" in relative.parts:
            continue

        files.append(
            MagazineFile(
                path=file,
                filename=file.name,
                extension=file.suffix.lower(),
                parent_folder=file.parent.name,
            )
        )

    return sorted(files, key=lambda x: x.filename.lower())