from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

INVENTORY_FILE = PROJECT_ROOT / "magazines_unraid_inventory.txt"
DUMMY_FOLDER = PROJECT_ROOT / "dummy_data"


def main():
    if not INVENTORY_FILE.exists():
        print(f"ERROR: Inventory not found: {INVENTORY_FILE}")
        return

    DUMMY_FOLDER.mkdir(exist_ok=True)

    # Fjern alle eksisterende dummy-filer
    removed = 0

    for file in DUMMY_FOLDER.iterdir():
        if file.is_file():
            file.unlink()
            removed += 1

    lines = INVENTORY_FILE.read_text(
        encoding="utf-8"
    ).splitlines()

    created = 0
    skipped = 0

    for line in lines:
        line = line.strip()

        if not line:
            continue

        filename = Path(line).name

        if not filename:
            skipped += 1
            continue

        destination = DUMMY_FOLDER / filename

        if destination.exists():
            skipped += 1
            continue

        destination.touch()
        created += 1

    inventory_entries = len(
        [x for x in lines if x.strip()]
    )

    print("DUMMY DATA CREATED")
    print("==================")
    print()
    print(f"Inventory:          {INVENTORY_FILE}")
    print(f"Old files removed:  {removed}")
    print(f"Inventory entries:  {inventory_entries}")
    print(f"Dummy files created:{created}")
    print(f"Skipped:            {skipped}")
    print()
    print(f"Folder: {DUMMY_FOLDER}")


if __name__ == "__main__":
    main()