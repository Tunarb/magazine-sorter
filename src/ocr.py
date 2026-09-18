from pathlib import Path
import os
import shutil
import subprocess
import tempfile

import fitz


PROJECT_ROOT = Path(__file__).resolve().parent.parent

# OCR is local inside the production Docker image. The legacy Docker backend
# remains available for the current development setup, where Tesseract may
# still run in the separate OCR container.
TESSDATA_DIR = Path(
    os.getenv(
        "MAGAZINE_SORTER_TESSDATA_DIR",
        str(PROJECT_ROOT / "ocr_test_data" / "tessdata"),
    )
)
OCR_IMAGE = os.getenv("MAGAZINE_SORTER_OCR_IMAGE", "jbarlow83/ocrmypdf-alpine")
OCR_BACKEND = os.getenv("MAGAZINE_SORTER_OCR_BACKEND", "auto").strip().lower()

DEFAULT_LANGUAGE = "dan"
DEFAULT_PAGES = 5
DEFAULT_DPI = 200


def render_page(pdf_path, page_number, output_path, dpi=DEFAULT_DPI):
    """
    Renderer én PDF-side til PNG med PyMuPDF.

    page_number er 1-baseret.
    """
    pdf_path = Path(pdf_path)

    with fitz.open(pdf_path) as document:
        if page_number < 1 or page_number > len(document):
            raise ValueError(
                f"Page {page_number} findes ikke i PDF'en. "
                f"PDF'en har {len(document)} sider."
            )

        page = document[page_number - 1]

        scale = dpi / 72
        matrix = fitz.Matrix(scale, scale)

        pixmap = page.get_pixmap(
            matrix=matrix,
            alpha=False,
        )

        pixmap.save(output_path)


def _ocr_local(image_path, language):
    tesseract = shutil.which("tesseract")
    if not tesseract:
        raise FileNotFoundError("Tesseract is not installed or not available on PATH")

    command = [tesseract, str(image_path), "stdout", "-l", language]
    if TESSDATA_DIR.exists():
        command.extend(["--tessdata-dir", str(TESSDATA_DIR)])

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Tesseract fejlede.\n\n"
            f"Return code: {result.returncode}\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )
    return result.stdout


def _ocr_docker(image_path, language):
    image_path = Path(image_path).resolve()
    tessdata_dir = TESSDATA_DIR.resolve()
    if not image_path.exists():
        raise FileNotFoundError(f"Billedet findes ikke: {image_path}")
    if not tessdata_dir.exists():
        raise FileNotFoundError(f"Tessdata-mappen findes ikke: {tessdata_dir}")
    if not shutil.which("docker"):
        raise FileNotFoundError("Docker CLI is not available for the OCR fallback backend")

    command = [
        "docker", "run", "--rm",
        "-v", f"{image_path.parent}:/data",
        "-v", f"{tessdata_dir}:/usr/share/tessdata",
        "--entrypoint", "tesseract", OCR_IMAGE,
        f"/data/{image_path.name}", "stdout", "-l", language,
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Tesseract fejlede.\n\n"
            f"Return code: {result.returncode}\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )
    return result.stdout


def ocr_image(image_path, language=DEFAULT_LANGUAGE):
    """Run Tesseract locally in production, with a legacy Docker fallback."""
    image_path = Path(image_path).resolve()
    if not image_path.exists():
        raise FileNotFoundError(f"Billedet findes ikke: {image_path}")

    if OCR_BACKEND == "docker":
        return _ocr_docker(image_path, language)
    if OCR_BACKEND == "local":
        return _ocr_local(image_path, language)

    # auto: prefer a local Tesseract installation. This is what the bundled
    # Docker image uses; existing development environments transparently keep
    # working with the old separate OCR container if Tesseract is absent.
    if shutil.which("tesseract"):
        return _ocr_local(image_path, language)
    return _ocr_docker(image_path, language)


def ocr_pdf(
    pdf_path,
    pages=DEFAULT_PAGES,
    language=DEFAULT_LANGUAGE,
    dpi=DEFAULT_DPI,
):
    """
    OCR'er de første sider af en PDF.

    Der oprettes kun midlertidige PNG-filer.
    De slettes automatisk igen.

    Returnerer en liste:
        [
            {
                "page": 1,
                "text": "...",
            },
            ...
        ]
    """
    pdf_path = Path(pdf_path).resolve()

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF findes ikke: {pdf_path}")

    with fitz.open(pdf_path) as document:
        total_pages = len(document)

    pages_to_process = min(pages, total_pages)

    results = []

    with tempfile.TemporaryDirectory(
        prefix="magazine_sorter_ocr_"
    ) as temp_dir:
        temp_dir = Path(temp_dir)

        for page_number in range(1, pages_to_process + 1):
            image_path = temp_dir / f"page_{page_number:03d}.png"

            print(f"OCR page {page_number}/{pages_to_process}...")

            render_page(
                pdf_path,
                page_number,
                image_path,
                dpi=dpi,
            )

            text = ocr_image(
                image_path,
                language=language,
            )

            results.append(
                {
                    "page": page_number,
                    "text": text,
                }
            )

    return results


def ocr_selected_pages(
    pdf_path,
    page_numbers,
    language=DEFAULT_LANGUAGE,
    dpi=DEFAULT_DPI,
):
    """
    OCR'er specifikke sider af en PDF.

    page_numbers er 1-baserede sidetal.

    Returnerer en liste:
        [
            {
                "page": 1,
                "text": "...",
            },
            ...
        ]
    """
    pdf_path = Path(pdf_path).resolve()

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF findes ikke: {pdf_path}")

    with fitz.open(pdf_path) as document:
        total_pages = len(document)

    valid_pages = sorted(
        {
            page_number
            for page_number in page_numbers
            if 1 <= page_number <= total_pages
        }
    )

    results = []

    with tempfile.TemporaryDirectory(
        prefix="magazine_sorter_ocr_"
    ) as temp_dir:
        temp_dir = Path(temp_dir)

        for index, page_number in enumerate(valid_pages, start=1):
            image_path = temp_dir / f"page_{page_number:03d}.png"

            print(
                f"OCR selected page {page_number} "
                f"({index}/{len(valid_pages)})..."
            )

            render_page(
                pdf_path,
                page_number,
                image_path,
                dpi=dpi,
            )

            text = ocr_image(
                image_path,
                language=language,
            )

            results.append(
                {
                    "page": page_number,
                    "text": text,
                }
            )

    return results


def main():
    test_pdf = (
        PROJECT_ROOT
        / "ocr_test_data"
        / "ALT.for.damerne.20.DANiSH.WEB-DL.PDF-MAGGi.pdf"
    )

    print("MAGAZINE SORTER - OCR TEST")
    print("==========================")
    print()
    print(f"PDF:       {test_pdf}")
    print(f"OCR image: {OCR_IMAGE}")
    print(f"Language:  {DEFAULT_LANGUAGE}")
    print(f"Pages:     {DEFAULT_PAGES}")
    print(f"DPI:       {DEFAULT_DPI}")
    print()

    results = ocr_pdf(
        test_pdf,
        pages=DEFAULT_PAGES,
        language=DEFAULT_LANGUAGE,
        dpi=DEFAULT_DPI,
    )

    print()
    print("=" * 70)
    print("OCR RESULT")
    print("=" * 70)

    for result in results:
        print()
        print(f"--- PAGE {result['page']} ---")
        print(result["text"].strip())


if __name__ == "__main__":
    main()