SPECIAL_TEXT_TOKENS = {
    "<PLAYER>": "{PLAYER}",
    "<RIVAL>": "{RIVAL}",
    "#MON": "POKÉMON",
    "# BALL": "POKÉ BALL",
    "POKéMON": "POKÉMON",
    "POKé": "POKÉ",
    "#": "POKÉ",
}


def normalize_game_text_tokens(text):
    for token in sorted(SPECIAL_TEXT_TOKENS, key=len, reverse=True):
        text = text.replace(token, SPECIAL_TEXT_TOKENS[token])
    return text
