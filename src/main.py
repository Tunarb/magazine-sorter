from filename_parser import parse_filename
from magazine_aliases import normalize_magazine_name
from path_builder import build_destination, get_status
from config_loader import load_config
from scanner import find_magazines


def main():
    config = load_config()

    files = find_magazines(config["input_folder"])

    ok_results = []
    review_results = []

    for file in files:
        parsed = parse_filename(file.filename)

        if parsed["magazine"]:
            parsed["magazine"] = normalize_magazine_name(
                parsed["magazine"]
            )

        destination = build_destination(parsed)
        status = get_status(destination)

        result = {
            "filename": file.filename,
            "parent_folder": file.parent_folder,
            "destination": destination,
            "status": status,
        }

        if status == "OK":
            ok_results.append(result)
        else:
            review_results.append(result)

    print("OK FILES")
    print("--------")
    print()

    for result in ok_results:
        print(result["filename"])
        print(f"→ {result['destination']}")
        print()

    print()
    print("REVIEW FILES")
    print("------------")
    print()

    for result in review_results:
        print(result["filename"])
        print(f"Folder: {result['parent_folder']}")
        print(f"→ {result['destination']}")
        print()

    total_files = len(files)
    ok_count = len(ok_results)
    review_count = len(review_results)

    success_rate = 0

    if total_files > 0:
        success_rate = (ok_count / total_files) * 100

    print()
    print("SUMMARY")
    print("-------")
    print()

    print(f"Files: {total_files}")
    print(f"OK: {ok_count}")
    print(f"REVIEW: {review_count}")
    print(f"Success rate: {success_rate:.1f}%")


if __name__ == "__main__":
    main()