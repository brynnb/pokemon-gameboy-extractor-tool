"""Shared Pokemon name normalization for exporter scripts."""

SPECIAL_NAME_MAPPINGS = {
    "NidoranM": "NIDORAN_M",
    "NidoranF": "NIDORAN_F",
    "Farfetchd": "FARFETCHD",
    "MrMime": "MR_MIME",
    "Nidoran♂": "NIDORAN_M",
    "Nidoran♀": "NIDORAN_F",
    "Mr.Mime": "MR_MIME",
    "Farfetch'd": "FARFETCHD",
}


def normalize_pokemon_name(name):
    """Convert names with special characters to their constant representation."""
    return SPECIAL_NAME_MAPPINGS.get(name, name.upper())
