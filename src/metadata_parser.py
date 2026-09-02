import re


MONTHS = {
    "januar": 1,
    "februar": 2,
    "marts": 3,
    "april": 4,
    "maj": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "december": 12,

    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


MONTH_PATTERN = (
    "januar|februar|marts|april|maj|juni|juli|august|"
    "september|oktober|november|december|"
    "january|february|march|may|june|july|august|"
    "september|october|november|december"
)


def normalize_text(text):
    """
    Normaliserer kendte tekstvarianter uden at ændre
    selve metadata-strukturen.
    """
    replacements = {
        "Soendag": "Søndag",
        "soendag": "søndag",
        "SONDAG": "SØNDAG",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return text


def parse_metadata(filename):
    """
    Udtrækker metadata fra et magasinfilnavn.

    Returnerer:
        {
            "year": int | None,
            "month": int | None,
            "day": int | None,
            "issue": int | None,
            "week": int | None,
        }

    Reglerne er bevidst konservative.
    Hvis noget ikke kan tolkes sikkert, returneres kun
    den metadata, der faktisk kan udledes.
    """

    text = normalize_text(filename)

    result = {
        "year": None,
        "month": None,
        "day": None,
        "issue": None,
        "week": None,
    }


    # ------------------------------------------------------------
    # EXPLICIT ISSUE NUMBER (Nr/No) HAS HIGHEST PRIORITY
    #
    # Hvis filnavnet eksplicit siger Nr./No., er tallet et issue-
    # nummer, også selv om filnavnet senere indeholder en udgivelsesdato.
    #
    # Eksempel:
    #   RUM Nr. 01 2023 - 19-01-2023.pdf
    #   Wendy Nr. 07 2023 - 24-07-2023.pdf
    #
    # Datoen i filnavnet bruges IKKE, når et eksplicit issue-nummer
    # allerede er fundet.
    # ------------------------------------------------------------

    # ÅR + NR/NO
    match = re.search(
        r"\b(20\d{2})[\s._-]*(?:Nr|No)[\s._-]*(\d{1,3})(?!\d)",
        text,
        re.IGNORECASE,
    )

    if match:
        result["year"] = int(match.group(1))
        result["issue"] = int(match.group(2))
        return result

    # NR/NO + firecifret år
    match = re.search(
        r"\b(?:Nr|No)[\s._-]*(\d{1,3})[\s._-]+(20\d{2})\b",
        text,
        re.IGNORECASE,
    )

    if match:
        result["issue"] = int(match.group(1))
        result["year"] = int(match.group(2))
        return result

    # NR/NO + tocifret år
    match = re.search(
        r"\b(?:Nr|No)[\s._-]*(\d{1,3})[\s._-]+(\d{2})(?!\d)",
        text,
        re.IGNORECASE,
    )

    if match:
        issue = int(match.group(1))
        short_year = int(match.group(2))

        if 0 <= short_year <= 30:
            year = 2000 + short_year
        else:
            year = 1900 + short_year

        result["issue"] = issue
        result["year"] = year
        return result

    # Standalone NR/NO
    match = re.search(
        r"\b(?:Nr|No)[\s._-]*(\d{1,3})(?!\d)",
        text,
        re.IGNORECASE,
    )

    if match:
        result["issue"] = int(match.group(1))
        return result

    # ------------------------------------------------------------
    # 1. Uge + år
    # ------------------------------------------------------------

    match = re.search(
        r"\bUge[\s._-]*(\d{1,2})[\s._-]*(20\d{2})\b",
        text,
        re.IGNORECASE,
    )

    if match:
        result["week"] = int(match.group(1))
        result["year"] = int(match.group(2))
        return result

    # ------------------------------------------------------------
    # 2. Dato DD-MM-YYYY
    # ------------------------------------------------------------

    match = re.search(
        r"(?<!\d)(\d{1,2})[-./](\d{1,2})[-./](20\d{2})(?!\d)",
        text,
    )

    if match:
        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3))

        if 1 <= day <= 31 and 1 <= month <= 12:
            result["day"] = day
            result["month"] = month
            result["year"] = year
            return result

    # ------------------------------------------------------------
    # 3. Dato YYYY-MM-DD
    # ------------------------------------------------------------

    match = re.search(
        r"(?<!\d)(20\d{2})[-./](\d{1,2})[-./](\d{1,2})(?!\d)",
        text,
    )

    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))

        if 1 <= month <= 12 and 1 <= day <= 31:
            result["year"] = year
            result["month"] = month
            result["day"] = day
            return result

    # ------------------------------------------------------------
    # 4. Dag + tekstmåned + år
    #
    # Eksempler:
    #   03.Februar.2025
    #   10.Marts.2025
    #   7.April.2025
    #
    # VIGTIGT:
    # Dette skal komme FØR ISSUE + MONTH.
    # Ellers ville f.eks. 03.Februar.2025 blive tolket
    # som issue 3 + måned februar.
    # ------------------------------------------------------------

    match = re.search(
        rf"\b(\d{{1,2}})[\s._-]+({MONTH_PATTERN})[\s._-]+(20\d{{2}})\b",
        text,
        re.IGNORECASE,
    )

    if match:
        day = int(match.group(1))
        month = MONTHS[match.group(2).lower()]
        year = int(match.group(3))

        if 1 <= day <= 31:
            result["day"] = day
            result["month"] = month
            result["year"] = year
            return result

    # ------------------------------------------------------------
    # 5. Tekstmåned + år
    # ------------------------------------------------------------

    match = re.search(
        rf"\b({MONTH_PATTERN})[\s._-]+(20\d{{2}})\b",
        text,
        re.IGNORECASE,
    )

    if match:
        month_name = match.group(1).lower()
        result["month"] = MONTHS[month_name]
        result["year"] = int(match.group(2))
        return result

    # ------------------------------------------------------------
    # 6. År + tekstmåned
    # ------------------------------------------------------------

    match = re.search(
        rf"\b(20\d{{2}})[\s._-]+({MONTH_PATTERN})\b",
        text,
        re.IGNORECASE,
    )

    if match:
        result["year"] = int(match.group(1))
        result["month"] = MONTHS[match.group(2).lower()]
        return result

    # ------------------------------------------------------------
    # 7. ISSUE + MONTH
    #
    # Gammel regel - BEVARES.
    #
    # Eksempler:
    #   12.Maj
    #   7.April
    #   01.September
    #
    # Her er første tal et nummer/issue og ikke en dato.
    # ------------------------------------------------------------

    match = re.search(
        rf"\b(\d{{1,3}})[\s._-]+({MONTH_PATTERN})\b",
        text,
        re.IGNORECASE,
    )

    if match:
        issue = int(match.group(1))
        month = MONTHS[match.group(2).lower()]

        result["issue"] = issue
        result["month"] = month
        return result

    # ------------------------------------------------------------
    # 8. ISSUE + YEAR
    #
    # Almindelig release-gruppe-form for magasiner uden eksplicit
    # "Nr./No."-tekst, fx:
    #   Euroman.378.2025
    #   Gastro.218.2025
    #   ALT.for.damerne.13.2025
    #
    # Denne regel kræver separator mellem issue og år, så kompakte
    # dato-/månedskoder som 092022 ikke bliver tolket som issue.
    # ------------------------------------------------------------

    match = re.search(
        r"(?<!\d)(\d{1,3})[\s._-]+(20\d{2})(?!\d)",
        text,
    )

    if match:
        result["issue"] = int(match.group(1))
        result["year"] = int(match.group(2))
        return result

    # ------------------------------------------------------------
    # 9. Kompakt DDMMYYYY
    # ------------------------------------------------------------

    match = re.search(
        r"(?<!\d)(\d{2})(\d{2})(20\d{2})(?!\d)",
        text,
    )

    if match:
        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3))

        if 1 <= day <= 31 and 1 <= month <= 12:
            result["day"] = day
            result["month"] = month
            result["year"] = year
            return result

    # ------------------------------------------------------------
    # 10. Kompakt YYYYMM
    # ------------------------------------------------------------

    match = re.search(
        r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(?!\d)",
        text,
    )

    if match:
        result["year"] = int(match.group(1))
        result["month"] = int(match.group(2))
        return result

    # ------------------------------------------------------------
    # 11. Kompakt MMYYYY
    # ------------------------------------------------------------

    match = re.search(
        r"(?<!\d)(0[1-9]|1[0-2])(20\d{2})(?!\d)",
        text,
    )

    if match:
        result["month"] = int(match.group(1))
        result["year"] = int(match.group(2))
        return result

    # ------------------------------------------------------------
    # 15. YYYY + issue
    # ------------------------------------------------------------

    match = re.search(
        r"\b(20\d{2})[\s._-]+(\d{2,3})(?!\d)",
        text,
    )

    if match:
        year = int(match.group(1))
        number = int(match.group(2))

        if number > 12:
            result["year"] = year
            result["issue"] = number
            return result

    # ------------------------------------------------------------
    # 16. MM YYYY
    # ------------------------------------------------------------

    match = re.search(
        r"(?<!\d)(0[1-9]|1[0-2])[\s._-]+(20\d{2})(?!\d)",
        text,
    )

    if match:
        result["month"] = int(match.group(1))
        result["year"] = int(match.group(2))
        return result

    # ------------------------------------------------------------
    # 17. YYYY MM
    # ------------------------------------------------------------

    match = re.search(
        r"(?<!\d)(20\d{2})[\s._-]+(0[1-9]|1[0-2])(?!\d)",
        text,
    )

    if match:
        result["year"] = int(match.group(1))
        result["month"] = int(match.group(2))
        return result

    return result