try:
    from .classifier import classify_file
except ImportError:
    from classifier import classify_file
try:
    from .path_builder import build_destination
except ImportError:
    from path_builder import build_destination
try:
    from .config_loader import load_config
except ImportError:
    from config_loader import load_config
try:
    from .scanner import find_magazines
except ImportError:
    from scanner import find_magazines


def main():
    config = load_config()
    files = find_magazines(config["input_folder"])

    results = {
        "AUTO": [],
        "REVIEW": [],
        "IGNORE": [],
    }

    for file in files:
        classification = classify_file(file.filename)

        destination = "_REVIEW"

        if classification["status"] == "AUTO":
            parsed = {
                "magazine": classification["publication"],
                **classification["metadata"],
            }

            destination = build_destination(parsed)

        results[classification["status"]].append(
            {
                "filename": file.filename,
                "parent_folder": file.parent_folder,
                "destination": destination,
                "classification": classification,
            }
        )

    print("AUTO FILES")
    print("==========")
    print()

    for result in results["AUTO"]:
        print(result["filename"])
        print(f"-> {result['destination']}")
        print()

    print()
    print("REVIEW FILES")
    print("============")
    print()

    for result in results["REVIEW"]:
        classification = result["classification"]
        metadata = classification["metadata"]

        print(result["filename"])
        print(f"publication: {classification['publication']}")
        print(f"year:        {metadata['year']}")
        print(f"month:       {metadata['month']}")
        print(f"issue:       {metadata['issue']}")
        print(f"week:        {metadata['week']}")
        print(f"reason:      {classification['reason']}")
        print()

    print()
    print("IGNORE FILES")
    print("============")
    print()

    for result in results["IGNORE"]:
        classification = result["classification"]

        print(result["filename"])
        print(f"reason: {classification['reason']}")
        print()

    print()
    print("SUMMARY")
    print("=======")
    print()

    print(f"Files:  {len(files)}")
    print(f"AUTO:   {len(results['AUTO'])}")
    print(f"REVIEW: {len(results['REVIEW'])}")
    print(f"IGNORE: {len(results['IGNORE'])}")


if __name__ == "__main__":
    main()