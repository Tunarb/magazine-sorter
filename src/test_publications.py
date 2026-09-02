from pathlib import Path
from collections import Counter

from magazine_profiles import PUBLICATIONS


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

    # Hvis flere publikationer matcher, vælg det mest specifikke
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


def main():
    files = INVENTORY_FILE.read_text(encoding="utf-8").splitlines()

    counts = Counter()
    unknown = []
    ambiguous = []

    for path in files:
        filename = Path(path).name
        publication = find_publication(filename)

        if publication == "AMBIGUOUS":
            ambiguous.append(filename)
        elif publication is None:
            unknown.append(filename)
        else:
            counts[publication] += 1

    print("PUBLICATION MATCHING")
    print("====================")
    print()

    for publication, count in counts.most_common():
        print(f"{publication}: {count}")

    print()
    print("UNKNOWN")
    print("=======")
    print(f"Total: {len(unknown)}")
    print()

    for filename in unknown[:50]:
        print(filename)

    if len(unknown) > 50:
        print()
        print(f"... and {len(unknown) - 50} more")

    print()
    print("AMBIGUOUS")
    print("=========")
    print(f"Total: {len(ambiguous)}")

    for filename in ambiguous:
        print(filename)


if __name__ == "__main__":
    main()