from pathlib import Path

from magazine_profiles import PUBLICATIONS
from metadata_parser import parse_metadata

INVENTORY_FILE = Path("magazine_inventory.txt")


def normalize(text):
    return text.lower().replace(".", " ").strip()


def find_publication(filename):
    name = normalize(Path(filename).stem)

    matches = []

    for publication, profile in PUBLICATIONS.items():
        for alias in profile.get("aliases", []):
            alias_normalized = normalize(alias)

            if alias_normalized in name:
                matches.append((publication, len(alias_normalized)))
                break

    if not matches:
        return None

    matches.sort(key=lambda item: item[1], reverse=True)

    longest_length = matches[0][1]

    best_matches = [
        publication
        for publication, length in matches
        if length == longest_length
    ]

    if len(best_matches) == 1:
        return best_matches[0]

    return "AMBIGUOUS"


def classify_file(filename):
    metadata = parse_metadata(filename)
    publication = find_publication(filename)

    # Tydelige bøger ignoreres.
    lower_name = filename.lower()

    if ".book." in lower_name or "-book." in lower_name:
        return {
            "status": "IGNORE",
            "publication": None,
            "metadata": metadata,
            "reason": "BOOK",
        }

    # Bekræftede specials/tillæg.
    # Disse må IKKE automatisk navngives som almindelige magasiner.
    # Senere kan OCR/AI eller manuel behandling bruges til identifikation.
    if (
        "mad and bolig gourmet" in lower_name
        or (
            "isabellas" in lower_name
            and "efterars- og vinterhaven" in lower_name
        )
        or (
            "isabellas" in lower_name
            and "efterårs- og vinterhaven" in lower_name
        )
    ):
        return {
            "status": "IGNORE",
            "publication": None,
            "metadata": metadata,
            "reason": "SPECIAL",
        }

    # Specials og tillæg ignoreres.
    if "tillaeg" in lower_name or "tillæg" in lower_name:
        return {
            "status": "IGNORE",
            "publication": None,
            "metadata": metadata,
            "reason": "SPECIAL",
        }

    # Ukendt publication skal til review.
    if publication is None:
        return {
            "status": "REVIEW",
            "publication": None,
            "metadata": metadata,
            "reason": "PUBLICATION_UNKNOWN",
        }

    # Ambiguous skal altid til review.
    if publication == "AMBIGUOUS":
        return {
            "status": "REVIEW",
            "publication": None,
            "metadata": metadata,
            "reason": "PUBLICATION_AMBIGUOUS",
        }

    # Publication er kendt, men vi mangler brugbar metadata.
    if not any(
        metadata[key] is not None
        for key in ("year", "month", "issue", "week")
    ):
        return {
            "status": "REVIEW",
            "publication": publication,
            "metadata": metadata,
            "reason": "NO_METADATA",
        }

    return {
        "status": "AUTO",
        "publication": publication,
        "metadata": metadata,
        "reason": None,
    }


def main():
    files = INVENTORY_FILE.read_text(encoding="utf-8").splitlines()

    results = {
        "AUTO": [],
        "REVIEW": [],
        "IGNORE": [],
    }

    for path in files:
        filename = Path(path).name
        result = classify_file(filename)

        results[result["status"]].append(
            (filename, result)
        )

    print("CLASSIFICATION")
    print("==============")
    print()

    for status in ("AUTO", "REVIEW", "IGNORE"):
        print(f"{status}: {len(results[status])}")

    print()
    print("REVIEW")
    print("======")

    for filename, result in results["REVIEW"]:
        metadata = result["metadata"]

        print()
        print(filename)
        print(f"  publication: {result['publication']}")
        print(f"  year:        {metadata['year']}")
        print(f"  month:       {metadata['month']}")
        print(f"  issue:       {metadata['issue']}")
        print(f"  week:        {metadata['week']}")
        print(f"  reason:      {result['reason']}")

    print()
    print("IGNORE")
    print("======")

    for filename, result in results["IGNORE"]:
        print()
        print(filename)
        print(f"  reason: {result['reason']}")


if __name__ == "__main__":
    main()