from pathlib import Path
import sys

# Allow imports from src/
SRC_DIR = Path(__file__).resolve().parent

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    from .ocr_classifier import analyze_file
except ImportError:
    from ocr_classifier import analyze_file
try:
    from .path_builder import build_destination
except ImportError:
    from path_builder import build_destination


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = SRC_DIR.parent
TEST_FOLDER = PROJECT_ROOT / "ocr_test_data"
REPORT_FILE = PROJECT_ROOT / "ocr_dry_run_report.txt"


# Real magazine PDFs selected for the OCR test.
#
# Specials and random publications are deliberately excluded for now.
TEST_FILES = [
    "ALT.for.damerne.20.DANiSH.WEB-DL.PDF-MAGGi.pdf",
    "ALT.Interioer.03.DANiSH.WEB-DL.PDF-MAGGi.pdf",
    "BoligLiv.05.DANiSH.WEB-DL.PDF-MAGGi.pdf",
    "Disney.Prinsesser.04.DANiSH.WEB-DL.PDF-MAGGi.pdf",

    "Hendes.Verden.17.DANiSH.WEB-DL.PDF-MAGGi.pdf",
    "Hendes Verden - Nr. 17 2022.pdf",
    "Hendes.Verden.Nr.18.2025.DANiSH.WEB-DL.PDF-MAGGi.pdf",
    "Hendes.Verden.20.DANiSH.WEB-DL.PDF-MAGGi.pdf",

    "Hjemmet.17.DANiSH.WEB-DL.PDF-MAGGi.pdf",
    "Hjemmet.20.DANiSH.WEB-DL.PDF-MAGGi.pdf",

    "HER&NU.20.DANiSH.WEB-DL.PDF-MAGGi.pdf",

    "RUM.04.DANiSH.WEB-DL.PDF-MAGGi.pdf",

    "Sallys.04.DANiSH.WEB-DL.PDF-MAGGi.pdf",

    "Vores.Boern.02.DANiSH.WEB-DL.PDF-MAGGi.pdf",

    "Wendy.04.DANiSH.WEB-DL.PDF-MAGGi.pdf",

    "BoligLiv - Nr. 05 2022.pdf",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def format_metadata(metadata):
    if not metadata:
        return "-"

    parts = []

    for key in ["year", "month", "day", "issue", "week"]:
        value = metadata.get(key)

        if value is not None:
            parts.append(f"{key}={value}")

    return ", ".join(parts) if parts else "-"


def get_destination(result):
    """
    Calculate the expected destination for AUTO results.

    This function does NOT move or rename anything.
    """

    if result["action"] != "AUTO":
        return None

    publication = result.get("publication")
    metadata = result.get("merged_metadata", {})

    if not publication:
        return None

    parsed = {
        "magazine": publication,
        **metadata,
    }

    return build_destination(parsed)


def classify_auto_type(result):
    """
    Distinguish between AUTO caused by filename
    and AUTO caused by OCR.
    """

    if result["action"] != "AUTO":
        return None

    if not result["ocr_pages"]:
        return "FILENAME"

    return "OCR"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("OCR DRY RUN - REAL MAGAZINE TEST SET")
    print("=" * 70)
    print()
    print(f"Test folder:  {TEST_FOLDER}")
    print(f"Files tested: {len(TEST_FILES)}")
    print()
    print("NO FILES WILL BE MOVED OR RENAMED.")
    print()

    results = []

    # -----------------------------------------------------------------------
    # Process selected files
    # -----------------------------------------------------------------------

    for index, filename in enumerate(TEST_FILES, start=1):
        pdf_path = TEST_FOLDER / filename

        print()
        print("#" * 70)
        print(f"[{index}/{len(TEST_FILES)}] {filename}")
        print("#" * 70)

        if not pdf_path.exists():
            print()
            print("ERROR: File not found.")

            results.append(
                {
                    "filename": filename,
                    "publication": None,
                    "filename_metadata": {},
                    "ocr_metadata": {},
                    "merged_metadata": {},
                    "ocr_pages": [],
                    "score": 0,
                    "action": "ERROR",
                    "reasons": ["File not found"],
                    "auto_type": None,
                    "destination": None,
                }
            )

            continue

        try:
            result = analyze_file(pdf_path)

            result["auto_type"] = classify_auto_type(result)
            result["destination"] = get_destination(result)

            results.append(result)

        except Exception as exc:
            print()
            print("ERROR:")
            print(exc)

            results.append(
                {
                    "filename": filename,
                    "publication": None,
                    "filename_metadata": {},
                    "ocr_metadata": {},
                    "merged_metadata": {},
                    "ocr_pages": [],
                    "score": 0,
                    "action": "ERROR",
                    "reasons": [str(exc)],
                    "auto_type": None,
                    "destination": None,
                }
            )

    # -----------------------------------------------------------------------
    # Summary groups
    # -----------------------------------------------------------------------

    auto_results = [
        result
        for result in results
        if result["action"] == "AUTO"
    ]

    filename_auto_results = [
        result
        for result in auto_results
        if result["auto_type"] == "FILENAME"
    ]

    ocr_auto_results = [
        result
        for result in auto_results
        if result["auto_type"] == "OCR"
    ]

    review_results = [
        result
        for result in results
        if result["action"] == "REVIEW"
    ]

    suggestion_results = [
        result
        for result in results
        if result["action"] == "REVIEW_SUGGESTION"
    ]

    error_results = [
        result
        for result in results
        if result["action"] == "ERROR"
    ]

    ocr_used_results = [
        result
        for result in results
        if result["ocr_pages"]
    ]

    ocr_skipped_results = [
        result
        for result in results
        if not result["ocr_pages"]
        and result["action"] != "ERROR"
    ]

    # -----------------------------------------------------------------------
    # Console summary
    # -----------------------------------------------------------------------

    print()
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print()
    print(f"Files tested:          {len(results)}")
    print(f"OCR used:              {len(ocr_used_results)}")
    print(f"OCR skipped:           {len(ocr_skipped_results)}")
    print()
    print(f"AUTO total:            {len(auto_results)}")
    print(f"  AUTO via filename:   {len(filename_auto_results)}")
    print(f"  AUTO via OCR:        {len(ocr_auto_results)}")
    print()
    print(f"REVIEW:                {len(review_results)}")
    print(f"REVIEW_SUGGESTION:     {len(suggestion_results)}")
    print(f"ERROR:                 {len(error_results)}")
    print()

    # -----------------------------------------------------------------------
    # Build report
    # -----------------------------------------------------------------------

    lines = []

    lines.append("=" * 70)
    lines.append("OCR DRY RUN REPORT")
    lines.append("=" * 70)
    lines.append("")
    lines.append("Real magazine PDFs from ocr_test_data.")
    lines.append("Special/random publications are excluded from this test.")
    lines.append("NO FILES WERE MOVED OR RENAMED.")
    lines.append("")

    lines.append("=" * 70)
    lines.append("SUMMARY")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"Files tested:        {len(results)}")
    lines.append(f"OCR used:            {len(ocr_used_results)}")
    lines.append(f"OCR skipped:         {len(ocr_skipped_results)}")
    lines.append("")
    lines.append(f"AUTO total:          {len(auto_results)}")
    lines.append(f"AUTO via filename:   {len(filename_auto_results)}")
    lines.append(f"AUTO via OCR:        {len(ocr_auto_results)}")
    lines.append("")
    lines.append(f"REVIEW:              {len(review_results)}")
    lines.append(f"REVIEW_SUGGESTION:   {len(suggestion_results)}")
    lines.append(f"ERROR:               {len(error_results)}")
    lines.append("")

    # -----------------------------------------------------------------------
    # Detailed results
    # -----------------------------------------------------------------------

    lines.append("=" * 70)
    lines.append("DETAILED RESULTS")
    lines.append("=" * 70)

    for result in results:
        lines.append("")
        lines.append("-" * 70)
        lines.append(f"FILE: {result['filename']}")
        lines.append(f"ACTION: {result['action']}")
        lines.append(f"SCORE: {result['score']}")
        lines.append(
            f"PUBLICATION: {result['publication'] or '-'}"
        )

        lines.append(
            f"FILENAME METADATA: "
            f"{format_metadata(result['filename_metadata'])}"
        )

        lines.append(
            f"OCR METADATA: "
            f"{format_metadata(result['ocr_metadata'])}"
        )

        lines.append(
            f"MERGED METADATA: "
            f"{format_metadata(result['merged_metadata'])}"
        )

        if result["ocr_pages"]:
            page_numbers = [
                str(page["page"])
                for page in result["ocr_pages"]
            ]

            lines.append(
                f"OCR PAGES USED: {', '.join(page_numbers)}"
            )
        else:
            lines.append("OCR PAGES USED: NONE")

        if result["auto_type"]:
            lines.append(
                f"AUTO SOURCE: {result['auto_type']}"
            )

        if result["destination"]:
            lines.append(
                f"DESTINATION: {result['destination']}"
            )

        if result["reasons"]:
            lines.append("REASONS:")

            for reason in result["reasons"]:
                lines.append(f"  - {reason}")

    # -----------------------------------------------------------------------
    # AUTO via OCR
    # -----------------------------------------------------------------------

    lines.append("")
    lines.append("=" * 70)
    lines.append("AUTO VIA OCR")
    lines.append("=" * 70)

    if not ocr_auto_results:
        lines.append("None")
    else:
        for result in ocr_auto_results:
            lines.append("")
            lines.append(f"FILE: {result['filename']}")
            lines.append(
                f"METADATA: "
                f"{format_metadata(result['merged_metadata'])}"
            )
            lines.append(f"SCORE: {result['score']}")

            page_numbers = [
                str(page["page"])
                for page in result["ocr_pages"]
            ]

            lines.append(
                f"OCR PAGES: {', '.join(page_numbers)}"
            )

            lines.append(
                f"DESTINATION: {result['destination'] or '-'}"
            )

    # -----------------------------------------------------------------------
    # REVIEW
    # -----------------------------------------------------------------------

    lines.append("")
    lines.append("=" * 70)
    lines.append("REVIEW")
    lines.append("=" * 70)

    if not review_results:
        lines.append("None")
    else:
        for result in review_results:
            lines.append("")
            lines.append(f"FILE: {result['filename']}")
            lines.append(
                f"PUBLICATION: "
                f"{result['publication'] or '-'}"
            )
            lines.append(
                f"METADATA: "
                f"{format_metadata(result['merged_metadata'])}"
            )
            lines.append(f"SCORE: {result['score']}")

            if result["ocr_pages"]:
                page_numbers = [
                    str(page["page"])
                    for page in result["ocr_pages"]
                ]

                lines.append(
                    f"OCR PAGES: {', '.join(page_numbers)}"
                )
            else:
                lines.append("OCR PAGES: NONE")

            if result["reasons"]:
                lines.append("REASONS:")

                for reason in result["reasons"]:
                    lines.append(f"  - {reason}")

    # -----------------------------------------------------------------------
    # Errors
    # -----------------------------------------------------------------------

    lines.append("")
    lines.append("=" * 70)
    lines.append("ERROR")
    lines.append("=" * 70)

    if not error_results:
        lines.append("None")
    else:
        for result in error_results:
            lines.append("")
            lines.append(f"FILE: {result['filename']}")
            lines.append(
                f"ERROR: "
                f"{' | '.join(result['reasons'])}"
            )

    # -----------------------------------------------------------------------
    # Write report
    # -----------------------------------------------------------------------

    REPORT_FILE.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )

    print(f"Report written to: {REPORT_FILE}")


if __name__ == "__main__":
    main()