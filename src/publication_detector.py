from magazine_profiles import PUBLICATIONS


def find_publication(filename):
    matches = []

    for publication, profile in PUBLICATIONS.items():
        for alias in profile["aliases"]:
            if alias.lower() in filename.lower():
                matches.append((publication, alias))
                break

    if not matches:
        return None

    matches.sort(key=lambda item: len(item[1]), reverse=True)

    if len(matches) == 1:
        return matches[0][0]

    if len(matches[0][1]) > len(matches[1][1]):
        return matches[0][0]

    return "AMBIGUOUS"