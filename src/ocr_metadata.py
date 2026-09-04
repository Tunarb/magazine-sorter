import re


def parse_ocr_metadata(text):
    """
    Forsøger at finde magasin-nummer og årstal i OCR-tekst.

    Eksempel:
        ALT FOR DAMERNE 20/2025

    giver:
        {
            "issue": 20,
            "year": 2025,
        }

    Returnerer None for felter, der ikke kan findes.
    """

    result = {
        "issue": None,
        "year": None,
    }

    if not text:
        return result

    # OCR-tekst kan have mange linjeskift og mærkelige mellemrum.
    # Gør den derfor mere ensartet først.
    normalized = re.sub(r"\s+", " ", text).strip()

    # Typisk magasinformat:
    #
    #   20/2025
    #   20 / 2025
    #   20-2025
    #   20 - 2025
    #
    # Nummeret begrænses til 1-3 cifre, mens årstal skal være 20xx.
    match = re.search(
        r"(?<!\d)(\d{1,3})\s*[/\-]\s*(20\d{2})(?!\d)",
        normalized,
    )

    if match:
        result["issue"] = int(match.group(1))
        result["year"] = int(match.group(2))
        return result

    # Alternativt:
    #
    #   NR. 20 2025
    #   NR 20 2025
    #   NO. 20 2025
    #   NO 20 2025
    #
    match = re.search(
        r"\b(?:nr|no)\.?\s*(\d{1,3})\s+(20\d{2})\b",
        normalized,
        flags=re.IGNORECASE,
    )

    if match:
        result["issue"] = int(match.group(1))
        result["year"] = int(match.group(2))
        return result

    return result


def main():
    test_text = """
    28

    31

    70

    98

    6

    Artikler

    Sofie Lassen-Kahlke
    Forfatter, mor og færdig med kun
    at være likeable

    2 ALT FOR DAMERNE 20/2025

    80

    88
    """

    print("MAGAZINE SORTER - OCR METADATA TEST")
    print("===================================")
    print()

    print("OCR text:")
    print(test_text.strip())
    print()

    result = parse_ocr_metadata(test_text)

    print("PARSED METADATA")
    print("================")
    print(f"Issue: {result['issue']}")
    print(f"Year:  {result['year']}")


if __name__ == "__main__":
    main()