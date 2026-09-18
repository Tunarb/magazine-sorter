from pathlib import Path
from collections import Counter

try:
    from .metadata_parser import parse_metadata
except ImportError:
    from metadata_parser import parse_metadata


INVENTORY_FILE = Path("magazine_inventory.txt")


def classify_filename(filename):
    name = Path(filename).stem

    if "Nr." in name or "No." in name:
        return "NR/NO"

    if "Uge." in name:
        return "UGE"

    if any(month in name.lower() for month in [
        "januar",
        "februar",
        "marts",
        "april",
        "maj",
        "juni",
        "juli",
        "august",
        "september",
        "oktober",
        "november",
        "december",
    ]):
        return "MONTH NAME"

    if any(char.isdigit() for char in name):
        parts = name.split(".")

        if len(parts) >= 2:
            return "NUMBERED"

    return "OTHER"


def main():
    files = INVENTORY_FILE.read_text(encoding="utf-8").splitlines()

    groups = Counter()
    examples = {}

    for path in files:
        filename = Path(path).name
        metadata = parse_metadata(filename)

        if all(value is None for value in metadata.values()):
            category = classify_filename(filename)

            groups[category] += 1

            if category not in examples:
                examples[category] = []

            if len(examples[category]) < 10:
                examples[category].append(filename)

    print("UNKNOWN METADATA PATTERNS")
    print("=========================")
    print()

    for category, count in groups.most_common():
        print(f"{category}: {count}")
        print("-" * 40)

        for filename in examples[category]:
            print(filename)

        print()


if __name__ == "__main__":
    main()