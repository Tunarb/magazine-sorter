import sys
from pathlib import Path

sys.path.insert(0, "src")

from classifier import classify_file
from path_builder import build_destination


INVENTORY_FILE = Path("magazine_inventory.txt")


def main():
    files = INVENTORY_FILE.read_text(encoding="utf-8").splitlines()

    print("DESTINATION DRY-RUN")
    print("===================")
    print()

    for path in files:
        filename = Path(path).name

        result = classify_file(filename)
        metadata = result["metadata"]
        publication = result.get("publication")

        issue = metadata.get("issue")

        if issue is not None and issue.isdigit():
            issue = int(issue)

        parsed = {
            "magazine": publication,
            "year": metadata.get("year"),
            "month": metadata.get("month"),
            "issue": issue,
            "week": metadata.get("week"),
        }

        destination = build_destination(parsed)

        print(filename)
        print(f"  publication: {publication}")
        print(f"  year:        {metadata.get('year')}")
        print(f"  month:       {metadata.get('month')}")
        print(f"  issue:       {metadata.get('issue')}")
        print(f"  week:        {metadata.get('week')}")
        print(f"  status:      {result['status']}")
        print(f"  destination: {destination}")
        print()


if __name__ == "__main__":
    main()