from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scanner import find_magazines
from classifier import classify_file
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


def main():
    print("DRY RUN")
    print("=======")
    print()
    print(f"Input:  {INPUT_FOLDER}")
    print(f"Report: {REPORT_FILE}")
    print()

    files = find_magazines(INPUT_FOLDER)

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

    lines = []

    lines.append("MAGAZINE SORTER - DRY RUN")
    lines.append("========================")
    lines.append("")
    lines.append(f"Files found:        {len(files)}")
    lines.append(f"AUTO:               {len(results['AUTO'])}")
    lines.append(f"REVIEW:             {len(results['REVIEW'])}")
    lines.append(f"IGNORE:             {len(results['IGNORE'])}")
    lines.append(f"Unique destinations: {len(destinations)}")
    lines.append(f"Collisions:         {len(collisions)}")
    lines.append("")

    lines.append("# AUTO FILES")
    lines.append("============")
    lines.append("")

    for item in results["AUTO"]:
        lines.append(
            f"{item['filename']}"
        )
        lines.append(
            f"-> {item['destination']}"
        )
        lines.append("")

    lines.append("# REVIEW FILES")
    lines.append("==============")
    lines.append("")

    for item in results["REVIEW"]:
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

    for item in results["IGNORE"]:
        result = item["result"]

        lines.append(item["filename"])
        lines.append(
            f"reason:      {result['reason']}"
        )
        lines.append("")

    lines.append("# COLLISIONS")
    lines.append("============")
    lines.append("")

    if collisions:
        for destination, filenames in collisions.items():
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

    print(f"Files found:        {len(files)}")
    print(f"AUTO:               {len(results['AUTO'])}")
    print(f"REVIEW:             {len(results['REVIEW'])}")
    print(f"IGNORE:             {len(results['IGNORE'])}")
    print(f"Unique destinations: {len(destinations)}")
    print(f"Collisions:         {len(collisions)}")
    print()
    print(f"Report written to:")
    print(REPORT_FILE)


if __name__ == "__main__":
    main()