import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, "src")

from classifier import classify_file
from path_builder import build_destination

INVENTORY_FILE = Path("magazine_inventory.txt")


def main():
    files = INVENTORY_FILE.read_text(encoding="utf-8").splitlines()

    destinations = defaultdict(list)

    for path in files:
        filename = Path(path).name

        result = classify_file(filename)
        metadata = result["metadata"]
        publication = result.get("publication")

        issue = metadata.get("issue")

        if issue is not None:
            try:
                issue = int(issue)
            except (TypeError, ValueError):
                pass

        parsed = {
            "magazine": publication,
            "year": metadata.get("year"),
            "month": metadata.get("month"),
            "day": metadata.get("day"),
            "issue": issue,
            "week": metadata.get("week"),
        }

        destination = build_destination(parsed)

        if destination != "_REVIEW":
            destinations[destination].append(filename)

    collisions = {
        destination: filenames
        for destination, filenames in destinations.items()
        if len(filenames) > 1
    }

    print("DESTINATION COLLISION TEST")
    print("==========================")
    print()

    print(f"Unique destinations: {len(destinations)}")
    print(f"Collisions:          {len(collisions)}")
    print()

    for destination, filenames in sorted(collisions.items()):
        print("COLLISION")
        print(f"destination: {destination}")

        for filename in filenames:
            print(f"  - {filename}")

        print()


if __name__ == "__main__":
    main()