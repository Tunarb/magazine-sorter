ALIASES = {
    "Soendag": "Søndag",
    "Se Og Hoer": "SE og HØR",
    "Se Og Hør": "SE og HØR",
}


def normalize_magazine_name(name):
    return ALIASES.get(name, name)