from pathlib import Path
from collections import defaultdict

import sys

sys.path.insert(0, "src")

try:
    from .classifier import classify_file
except ImportError:
    from classifier import classify_file
try:
    from .path_builder import build_destination
except ImportError:
    from path_builder import build_destination


INVENTORY_FILE = Path("magazine_inventory.txt")


def main():
    files = [
        line.strip()
        for line in INVENTORY_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    results = {
        "AUTO": [],
        "REVIEW": [],
        "IGNORE": [],
    }

    destinations = defaultdict(list)

    for path in files:
        filename = Path(path).name
        classification = classify_file(filename)

        destination = "_REVIEW"

        if classification["status"] == "AUTO":
            parsed = {
                "magazine": classification["publication"],
                **classification["metadata"],
            }

            destination = build_destination(parsed)
            destinations[destination].append(filename)

        results[classification["status"]].append(
            {
                "filename": filename,
                "classification": classification,
                "destination": destination,
            }
        )

    collisions = {
        destination: filenames
        for destination, filenames in destinations.items()
        if len(filenames) > 1
    }

    print("INVENTORY DRY RUN")
    print("=================")
    print()

    print(f"Files in inventory: {len(files)}")
    print(f"AUTO:               {len(results['AUTO'])}")
    print(f"REVIEW:             {len(results['REVIEW'])}")
    print(f"IGNORE:             {len(results['IGNORE'])}")
    print(f"Unique destinations: {len(destinations)}")
    print(f"Collisions:          {len(collisions)}")

    print()
    print("REVIEW")
    print("======")

    for result in results["REVIEW"]:
        classification = result["classification"]
        metadata = classification["metadata"]

        print()
        print(result["filename"])
        print(f"  publication: {classification['publication']}")
        print(f"  year:        {metadata['year']}")
        print(f"  month:       {metadata['month']}")
        print(f"  issue:       {metadata['issue']}")
        print(f"  week:        {metadata['week']}")
        print(f"  reason:      {classification['reason']}")

    print()
    print("IGNORE")
    print("======")

    for result in results["IGNORE"]:
        print()
        print(result["filename"])
        print(f"  reason: {result['classification']['reason']}")

    print()
    print("COLLISIONS")
    print("==========")

    if not collisions:
        print("None")
    else:
        for destination, filenames in sorted(collisions.items()):
            print()
            print(f"destination: {destination}")

            for filename in filenames:
                print(f"  - {filename}")


if __name__ == "__main__":
    main()