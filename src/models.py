from dataclasses import dataclass
from pathlib import Path


@dataclass
class MagazineFile:
    path: Path
    filename: str
    extension: str
    parent_folder: str = ""