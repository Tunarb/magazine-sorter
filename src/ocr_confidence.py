def compare_metadata(
    filename_metadata,
    ocr_metadata,
    filename_publication=None,
    ocr_publication=None,
):
    """
    Sammenligner metadata fra filnavnet med metadata fra OCR.

    Filename er den primære kilde.
    OCR bruges som supplement.

    Publication, issue og år vurderes separat.

    Konflikter giver altid REVIEW.
    """

    filename_issue = filename_metadata.get("issue")
    filename_year = filename_metadata.get("year")
    filename_month = filename_metadata.get("month")
    filename_day = filename_metadata.get("day")
    filename_week = filename_metadata.get("week")

    ocr_issue = ocr_metadata.get("issue")
    ocr_year = ocr_metadata.get("year")
    ocr_month = ocr_metadata.get("month")
    ocr_day = ocr_metadata.get("day")
    ocr_week = ocr_metadata.get("week")

    score = 0
    reasons = []
    conflicts = []

    # ---------------------------------------------------------
    # Publication
    # ---------------------------------------------------------

    if filename_publication is not None and ocr_publication is not None:
        if filename_publication.lower() == ocr_publication.lower():
            score += 40
            reasons.append("Publication matcher")
        else:
            conflicts.append("Publication KONFLIKT")

    elif filename_publication is not None:
        score += 40
        reasons.append("Publication fundet i filnavn")

    elif ocr_publication is not None:
        score += 20
        reasons.append("Publication fundet via OCR")

    # ---------------------------------------------------------
    # Hjælpefunktion til metadatafelter
    # ---------------------------------------------------------

    def compare_field(
        name,
        filename_value,
        ocr_value,
        strong_points,
        single_points,
    ):
        nonlocal score

        if filename_value is not None and ocr_value is not None:
            if filename_value == ocr_value:
                score += strong_points
                reasons.append(f"{name} matcher")
            else:
                conflicts.append(f"{name} KONFLIKT")

        elif filename_value is not None:
            score += single_points
            reasons.append(f"{name} fundet i filnavn")

        elif ocr_value is not None:
            score += single_points
            reasons.append(f"{name} fundet via OCR")

    # ---------------------------------------------------------
    # Issue
    # ---------------------------------------------------------

    compare_field(
        "Issue",
        filename_issue,
        ocr_issue,
        strong_points=30,
        single_points=20,
    )

    # ---------------------------------------------------------
    # Year
    # ---------------------------------------------------------

    compare_field(
        "År",
        filename_year,
        ocr_year,
        strong_points=30,
        single_points=20,
    )

    # ---------------------------------------------------------
    # Måned
    # ---------------------------------------------------------

    compare_field(
        "Måned",
        filename_month,
        ocr_month,
        strong_points=10,
        single_points=5,
    )

    # ---------------------------------------------------------
    # Dag
    # ---------------------------------------------------------

    compare_field(
        "Dag",
        filename_day,
        ocr_day,
        strong_points=10,
        single_points=5,
    )

    # ---------------------------------------------------------
    # Uge
    # ---------------------------------------------------------

    compare_field(
        "Uge",
        filename_week,
        ocr_week,
        strong_points=20,
        single_points=10,
    )

    # ---------------------------------------------------------
    # Konflikter vinder altid
    # ---------------------------------------------------------

    if conflicts:
        reasons.extend(conflicts)

        return {
            "score": 0,
            "action": "REVIEW",
            "reasons": reasons,
        }

    # ---------------------------------------------------------
    # Metadata-type
    # ---------------------------------------------------------

    has_issue = (
        filename_issue is not None
        or ocr_issue is not None
    )

    has_year = (
        filename_year is not None
        or ocr_year is not None
    )

    has_week = (
        filename_week is not None
        or ocr_week is not None
    )

    has_date = (
        (
            filename_year is not None
            or ocr_year is not None
        )
        and
        (
            filename_month is not None
            or ocr_month is not None
        )
    )

    # ---------------------------------------------------------
    # Beslutning
    # ---------------------------------------------------------

    if (
        filename_publication is not None
        and has_issue
        and has_year
        and score >= 80
    ):
        action = "AUTO"

    elif (
        filename_publication is not None
        and has_week
        and has_year
        and score >= 70
    ):
        action = "AUTO"

    elif (
        filename_publication is not None
        and has_date
        and score >= 70
    ):
        action = "AUTO"

    elif score >= 50:
        action = "REVIEW_SUGGESTION"

    else:
        action = "REVIEW"

    return {
        "score": score,
        "action": action,
        "reasons": reasons,
    }


def main():
    print("MAGAZINE SORTER - OCR CONFIDENCE TEST")
    print("====================================")
    print()

    tests = [
        {
            "name": "ALT: publication filename + issue/year OCR",
            "filename_publication": "ALT for damerne",
            "ocr_publication": None,
            "filename": {
                "issue": None,
                "year": None,
                "month": None,
                "day": None,
                "week": None,
            },
            "ocr": {
                "issue": 20,
                "year": 2025,
                "month": None,
                "day": None,
                "week": None,
            },
        },
        {
            "name": "ALT: publication + issue filename + år OCR",
            "filename_publication": "ALT for damerne",
            "ocr_publication": None,
            "filename": {
                "issue": 20,
                "year": None,
                "month": None,
                "day": None,
                "week": None,
            },
            "ocr": {
                "issue": 20,
                "year": 2025,
                "month": None,
                "day": None,
                "week": None,
            },
        },
        {
            "name": "Publication + issue/year matcher",
            "filename_publication": "ALT for damerne",
            "ocr_publication": "ALT for damerne",
            "filename": {
                "issue": 20,
                "year": 2025,
                "month": None,
                "day": None,
                "week": None,
            },
            "ocr": {
                "issue": 20,
                "year": 2025,
                "month": None,
                "day": None,
                "week": None,
            },
        },
        {
            "name": "Publication konflikt",
            "filename_publication": "ALT for damerne",
            "ocr_publication": "Femina Danmark",
            "filename": {
                "issue": 20,
                "year": 2025,
                "month": None,
                "day": None,
                "week": None,
            },
            "ocr": {
                "issue": 20,
                "year": 2025,
                "month": None,
                "day": None,
                "week": None,
            },
        },
        {
            "name": "Issue konflikt",
            "filename_publication": "ALT for damerne",
            "ocr_publication": None,
            "filename": {
                "issue": 20,
                "year": 2025,
                "month": None,
                "day": None,
                "week": None,
            },
            "ocr": {
                "issue": 21,
                "year": 2025,
                "month": None,
                "day": None,
                "week": None,
            },
        },
        {
            "name": "År konflikt",
            "filename_publication": "ALT for damerne",
            "ocr_publication": None,
            "filename": {
                "issue": 20,
                "year": 2024,
                "month": None,
                "day": None,
                "week": None,
            },
            "ocr": {
                "issue": 20,
                "year": 2025,
                "month": None,
                "day": None,
                "week": None,
            },
        },
        {
            "name": "Kun OCR publication + issue/year",
            "filename_publication": None,
            "ocr_publication": "ALT for damerne",
            "filename": {
                "issue": None,
                "year": None,
                "month": None,
                "day": None,
                "week": None,
            },
            "ocr": {
                "issue": 20,
                "year": 2025,
                "month": None,
                "day": None,
                "week": None,
            },
        },
    ]

    for test in tests:
        result = compare_metadata(
            test["filename"],
            test["ocr"],
            filename_publication=test["filename_publication"],
            ocr_publication=test["ocr_publication"],
        )

        print(test["name"])
        print("-" * len(test["name"]))
        print(f"Score:  {result['score']}")
        print(f"Action: {result['action']}")

        for reason in result["reasons"]:
            print(f"  - {reason}")

        print()


if __name__ == "__main__":
    main()