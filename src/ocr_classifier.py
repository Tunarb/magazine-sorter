from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from .classifier import classify_file
except ImportError:
    from classifier import classify_file
try:
    from .ocr import ocr_selected_pages
except ImportError:
    from ocr import ocr_selected_pages
try:
    from .ocr_confidence import compare_metadata
except ImportError:
    from ocr_confidence import compare_metadata
try:
    from .ocr_metadata import parse_ocr_metadata
except ImportError:
    from ocr_metadata import parse_ocr_metadata


OCR_LANGUAGE = "dan"
OCR_DPI = 300

# Sider der typisk er interessante for magasinmetadata.
# Vi starter konservativt og udvider kun hvis nødvendigt.
OCR_PAGE_STAGES = [
    [1],
    [4],
    [5],
]


def has_complete_filename_metadata(filename_metadata, filename_publication):
    """
    Returnerer True hvis filnavnet allerede indeholder nok metadata
    til sikker automatisk behandling.

    Vi betragter følgende som komplet:

    - issue + year
    - week + year
    - month + year
    """
    if not filename_publication:
        return False

    if (
        filename_metadata["issue"] is not None
        and filename_metadata["year"] is not None
    ):
        return True

    if (
        filename_metadata["week"] is not None
        and filename_metadata["year"] is not None
    ):
        return True

    if (
        filename_metadata["month"] is not None
        and filename_metadata["year"] is not None
    ):
        return True

    return False


def metadata_is_complete(metadata):
    """
    Returnerer True når OCR har fundet issue + year.
    """
    return (
        metadata.get("issue") is not None
        and metadata.get("year") is not None
    )


def merge_metadata(filename_metadata, ocr_metadata):
    """
    Filnavnet er primær kilde.

    Hvis filnavnet allerede har en metadata-værdi, beholdes den.
    OCR bruges kun til felter der mangler i filnavnet.
    """
    merged = dict(filename_metadata)

    for key in (
        "year",
        "month",
        "day",
        "issue",
        "week",
    ):
        if merged.get(key) is None and ocr_metadata.get(key) is not None:
            merged[key] = ocr_metadata[key]

    return merged


def analyze_file(pdf_path, language=OCR_LANGUAGE, dpi=OCR_DPI, page_stages=None, ocr_enabled=True):
    """
    Analyserer én PDF.

    Først bruges filename/classifier-logikken.

    Hvis filnavnet allerede indeholder komplet metadata,
    springes OCR over.

    Ellers køres progressiv OCR:

        side 1
        side 4
        side 5

    OCR stopper så snart issue + year er fundet.
    """
    pdf_path = Path(pdf_path).resolve()

    if page_stages is None:
        page_stages = OCR_PAGE_STAGES

    print()
    print("=" * 70)
    print(f"FILE: {pdf_path.name}")
    print("=" * 70)

    # ------------------------------------------------------------
    # 1. Klassifikation ud fra filnavn
    # ------------------------------------------------------------

    filename_result = classify_file(pdf_path.name)

    filename_publication = filename_result["publication"]
    filename_metadata = filename_result["metadata"]

    print()
    print("FILENAME CLASSIFICATION")
    print("-----------------------")
    print(f"status:      {filename_result['status']}")
    print(f"publication: {filename_publication}")
    print(f"metadata:    {filename_metadata}")
    print(f"reason:      {filename_result['reason']}")

    # ------------------------------------------------------------
    # 2. Komplet filnavn -> ingen OCR
    # ------------------------------------------------------------

    if has_complete_filename_metadata(
        filename_metadata,
        filename_publication,
    ):
        print()
        print("Springes over - filnavnet indeholder komplet metadata.")

        print()
        print("FINAL SUGGESTION")
        print("----------------")
        print(f"publication: {filename_publication}")
        print(f"metadata:    {filename_metadata}")
        print("score:       100")
        print("action:      AUTO")
        print("reason:      Komplet metadata fundet i filnavn")

        return {
            "filename": pdf_path.name,
            "publication": filename_publication,
            "filename_metadata": filename_metadata,
            "ocr_metadata": {
                "issue": None,
                "year": None,
            },
            "merged_metadata": filename_metadata,
            "ocr_pages": [],
            "ocr_text": [],
            "score": 100,
            "action": "AUTO",
            "reasons": [
                "Komplet metadata fundet i filnavn"
            ],
        }

    # ------------------------------------------------------------
    # 3. Progressiv OCR
    # ------------------------------------------------------------

    ocr_metadata = {
        "issue": None,
        "year": None,
    }

    if not ocr_enabled:
        merged_metadata = merge_metadata(filename_metadata, ocr_metadata)
        return {
            "filename": pdf_path.name,
            "publication": filename_publication,
            "filename_metadata": filename_metadata,
            "ocr_metadata": ocr_metadata,
            "merged_metadata": merged_metadata,
            "ocr_pages": [],
            "ocr_text": [],
            "score": 0,
            "action": "REVIEW",
            "reasons": ["Automatic OCR is disabled in Settings"],
        }

    print()
    print("PROGRESSIVE OCR")
    print("----------------")

    ocr_pages = []
    combined_text = ""

    ocr_metadata = {
        "issue": None,
        "year": None,
    }

    pages_used = []

    for stage_pages in page_stages:
        # Undgå at OCR'e en side igen hvis den allerede er behandlet.
        pages_to_process = [
            page
            for page in stage_pages
            if page not in pages_used
        ]

        if not pages_to_process:
            continue

        print()
        print(
            f"OCR stage: pages "
            f"{', '.join(str(page) for page in pages_to_process)}"
        )

        stage_results = ocr_selected_pages(
            pdf_path,
            pages_to_process,
            language=language,
            dpi=dpi,
        )

        for result in stage_results:
            page_number = result["page"]
            text = result["text"]

            pages_used.append(page_number)
            ocr_pages.append(result)

            if text:
                combined_text += "\n" + text

        # Parse den samlede OCR-tekst efter hvert trin.
        ocr_metadata = parse_ocr_metadata(combined_text)

        print()
        print(f"OCR metadata after pages {pages_used}:")
        print(ocr_metadata)

        # --------------------------------------------------------
        # Stop så snart OCR har fundet issue + year.
        # --------------------------------------------------------

        if metadata_is_complete(ocr_metadata):
            print()
            print(
                "OCR har fundet komplet issue/year-metadata - "
                "stopper yderligere OCR."
            )
            break

    # ------------------------------------------------------------
    # 4. Sammenlign filename og OCR
    # ------------------------------------------------------------

    confidence = compare_metadata(
        filename_metadata,
        ocr_metadata,
        filename_publication=filename_publication,
        ocr_publication=None,
    )

    score = confidence["score"]
    action = confidence["action"]
    reasons = confidence["reasons"]

    # ------------------------------------------------------------
    # 5. Kombiner metadata
    # ------------------------------------------------------------

    merged_metadata = merge_metadata(
        filename_metadata,
        ocr_metadata,
    )

    # ------------------------------------------------------------
    # 6. Vis resultat
    # ------------------------------------------------------------

    print()
    print("CONFIDENCE")
    print("----------")
    print(f"score:  {score}")
    print(f"action: {action}")

    if reasons:
        print("reasons:")
        for reason in reasons:
            print(f"  - {reason}")

    print()
    print("OCR PAGES USED")
    print("--------------")

    if pages_used:
        print(", ".join(str(page) for page in pages_used))
    else:
        print("Ingen")

    print()
    print("OCR METADATA")
    print("------------")
    print(ocr_metadata)

    print()
    print("MERGED METADATA")
    print("---------------")
    print(merged_metadata)

    print()
    print("FINAL SUGGESTION")
    print("----------------")
    print(f"publication: {filename_publication}")
    print(f"metadata:    {merged_metadata}")
    print(f"score:       {score}")
    print(f"action:      {action}")

    if reasons:
        print("reason:")
        for reason in reasons:
            print(f"  - {reason}")

    return {
        "filename": pdf_path.name,
        "publication": filename_publication,
        "filename_metadata": filename_metadata,
        "ocr_metadata": ocr_metadata,
        "merged_metadata": merged_metadata,
        "ocr_pages": ocr_pages,
        "ocr_text": ocr_pages,
        "score": score,
        "action": action,
        "reasons": reasons,
    }


def main():
    project_root = Path(__file__).resolve().parent.parent

    test_pdf = (
        project_root
        / "ocr_test_data"
        / "ALT.for.damerne.20.DANiSH.WEB-DL.PDF-MAGGi.pdf"
    )

    analyze_file(test_pdf)


if __name__ == "__main__":
    main()