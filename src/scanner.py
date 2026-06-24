from pathlib import Path

from models import MagazineFile


SUPPORTED_EXTENSIONS = [
    ".pdf",
    ".cbz",
]


def find_magazines(folder):
    folder = Path(folder)

    files = []

    for ext in SUPPORTED_EXTENSIONS:
        for file in folder.glob(f"*{ext}"):

            files.append(
                MagazineFile(
                    path=file,
                    filename=file.name,
                    extension=file.suffix.lower(),
                    parent_folder=file.parent.name,
                )
            )

    return sorted(files, key=lambda x: x.filename.lower())