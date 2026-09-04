from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scanner import find_magazines
from classifier import classify_file


PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_FOLDER = PROJECT_ROOT / "dummy_data"
REPORT_FILE = PROJECT_ROOT / "review_report.txt"


def main():
    print("REVIEW")
    print("======")
    print()
    print(f"Input:  {INPUT_FOLDER}")
    print(f"Report: {REPORT_FILE}")
    print()

    files = find_magazines(INPUT_FOLDER)

    review_files = []

    for file in files:
        result = classify_file(file.filename)

        if result["status"] != "REVIEW":
            continue

        review_files.append(
            {
                "file": file,
                "result": result,
            }
        )

    lines = []

    lines.append("MAGAZINE SORTER - REVIEW")
    lines.append("========================")
    lines.append("")
    lines.append(f"Files scanned: {len(files)}")
    lines.append(f"REVIEW files:  {len(review_files)}")
    lines.append("")

    lines.append("# REVIEW FILES")
    lines.append("==============")
    lines.append("")

    for item in review_files:
        file = item["file"]
        result = item["result"]
        metadata = result["metadata"]

        lines.append("-" * 60)
        lines.append(f"File:        {file.filename}")
        lines.append(f"Path:        {file.path}")
        lines.append(f"Folder:      {file.parent_folder}")
        lines.append(f"Publication: {result['publication']}")
        lines.append(f"Year:        {metadata['year']}")
        lines.append(f"Month:       {metadata['month']}")
        lines.append(f"Day:         {metadata['day']}")
        lines.append(f"Issue:       {metadata['issue']}")
        lines.append(f"Week:        {metadata['week']}")
        lines.append(f"Reason:      {result['reason']}")
        lines.append("")

    if not review_files:
        lines.append("None")
        lines.append("")

    REPORT_FILE.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )

    print(f"Files scanned: {len(files)}")
    print(f"REVIEW files:  {len(review_files)}")
    print()
    print("Report written to:")
    print(REPORT_FILE)


if __name__ == "__main__":
    main()