from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from .scanner import find_magazines
except ImportError:
    from scanner import find_magazines
try:
    from .classifier import classify_file
except ImportError:
    from classifier import classify_file
try:
    from .path_builder import build_destination
except ImportError:
    from path_builder import build_destination


PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_FOLDER = PROJECT_ROOT / "dummy_data"
REPORT_FILE = PROJECT_ROOT / "dry_run_report.txt"


def get_destination(result):
    metadata = result["metadata"]

    parsed = {
        "magazine": result["publication"],
        **metadata,
    }

    return build_destination(parsed)


def run_dry_run(input_folder):
    """
    Run a complete dry-run without changing any files.

    Returns a dictionary containing:
    - files
    - results
    - collisions
    - statistics
    """

    input_folder = Path(input_folder)

    files = find_magazines(input_folder)

    results = {
        "AUTO": [],
        "REVIEW": [],
        "IGNORE": [],
    }

    destinations = {}

    for file in files:
        result = classify_file(file.filename)

        destination = None

        if result["status"] == "AUTO":
            destination = get_destination(result)

            destinations.setdefault(destination, []).append(
                file.filename
            )

        results[result["status"]].append(
            {
                "filename": file.filename,
                "result": result,
                "destination": destination,
            }
        )

    collisions = {
        destination: filenames
        for destination, filenames in destinations.items()
        if len(filenames) > 1
    }

    return {
        "input_folder": str(input_folder),
        "files": files,
        "results": results,
        "collisions": collisions,
        "statistics": {
            "files": len(files),
            "auto": len(results["AUTO"]),
            "review": len(results["REVIEW"]),
            "ignore": len(results["IGNORE"]),
            "unique_destinations": len(destinations),
            "collisions": len(collisions),
        },
    }


def write_report(run):
    lines = []

    lines.append("MAGAZINE SORTER - DRY RUN")
    lines.append("========================")
    lines.append("")
    lines.append(f"Input:              {run['input_folder']}")
    lines.append(f"Files found:        {run['statistics']['files']}")
    lines.append(f"AUTO:               {run['statistics']['auto']}")
    lines.append(f"REVIEW:             {run['statistics']['review']}")
    lines.append(f"IGNORE:             {run['statistics']['ignore']}")
    lines.append(
        f"Unique destinations: "
        f"{run['statistics']['unique_destinations']}"
    )
    lines.append(
        f"Collisions:         "
        f"{run['statistics']['collisions']}"
    )
    lines.append("")

    lines.append("# AUTO FILES")
    lines.append("============")
    lines.append("")

    for item in run["results"]["AUTO"]:
        lines.append(item["filename"])
        lines.append(f"-> {item['destination']}")
        lines.append("")

    lines.append("# REVIEW FILES")
    lines.append("==============")
    lines.append("")

    for item in run["results"]["REVIEW"]:
        result = item["result"]
        metadata = result["metadata"]

        lines.append(item["filename"])
        lines.append(
            f"publication: {result['publication']}"
        )
        lines.append(
            f"year:        {metadata['year']}"
        )
        lines.append(
            f"month:       {metadata['month']}"
        )
        lines.append(
            f"issue:       {metadata['issue']}"
        )
        lines.append(
            f"week:        {metadata['week']}"
        )
        lines.append(
            f"reason:      {result['reason']}"
        )
        lines.append("")

    lines.append("# IGNORE FILES")
    lines.append("==============")
    lines.append("")

    for item in run["results"]["IGNORE"]:
        result = item["result"]

        lines.append(item["filename"])
        lines.append(
            f"reason:      {result['reason']}"
        )
        lines.append("")

    lines.append("# COLLISIONS")
    lines.append("============")
    lines.append("")

    if run["collisions"]:
        for destination, filenames in run["collisions"].items():
            lines.append(destination)

            for filename in filenames:
                lines.append(
                    f"  <- {filename}"
                )

            lines.append("")
    else:
        lines.append("None")
        lines.append("")

    REPORT_FILE.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def main():
    print("DRY RUN")
    print("=======")
    print()

    print(f"Input:  {INPUT_FOLDER}")
    print(f"Report: {REPORT_FILE}")
    print()

    run = run_dry_run(INPUT_FOLDER)

    write_report(run)

    stats = run["statistics"]

    print(f"Files found:        {stats['files']}")
    print(f"AUTO:               {stats['auto']}")
    print(f"REVIEW:             {stats['review']}")
    print(f"IGNORE:             {stats['ignore']}")
    print(
        f"Unique destinations: "
        f"{stats['unique_destinations']}"
    )
    print(
        f"Collisions:         "
        f"{stats['collisions']}"
    )
    print()

    print("Report written to:")
    print(REPORT_FILE)


if __name__ == "__main__":
    main()