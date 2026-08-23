#!/usr/bin/env python3
import re
import sqlite3

from config import CONSTANTS_DIR, DB_PATH, MOVES_DATA_DIR

POKEMON_DATA_DIR = MOVES_DATA_DIR

EXPECTED_MOVE_COUNT = 165
EXPECTED_MOVE_IDS = frozenset(range(1, EXPECTED_MOVE_COUNT + 1))

# These moves exposed the old parser's dependence on exactly one space before
# the PP value. Keep them named so future source-format changes cannot silently
# drop the same high-impact records again.
AUDITED_MOVE_CONSTANTS = frozenset(
    {
        "GUILLOTINE",
        "MEGA_KICK",
        "HORN_DRILL",
        "HYDRO_PUMP",
        "BLIZZARD",
        "HYPER_BEAM",
        "FISSURE",
        "SELFDESTRUCT",
        "FIRE_BLAST",
        "SKY_ATTACK",
        "EXPLOSION",
    }
)

MOVE_RE = re.compile(
    r"^\s*move\s+"
    r"(?P<animation>[A-Za-z_][A-Za-z0-9_]*)\s*,\s*"
    r"(?P<effect>[A-Za-z_][A-Za-z0-9_]*)\s*,\s*"
    r"(?P<power>\d+)\s*,\s*"
    r"(?P<type>[A-Za-z_][A-Za-z0-9_]*)\s*,\s*"
    r"(?P<accuracy>\d+)\s*,\s*"
    r"(?P<pp>\d+)\s*$"
)

# Hardcoded HM moves based on hm_moves.asm
HM_MOVES = {"CUT", "FLY", "SURF", "STRENGTH", "FLASH"}

# Hardcoded field moves based on field_moves.asm
FIELD_MOVES = {
    "CUT",
    "FLY",
    "SURF",
    "STRENGTH",
    "FLASH",
    "DIG",
    "TELEPORT",
    "SOFTBOILED",
}

# Type mapping to ensure consistent type names
TYPE_MAPPING = {
    "PSYCHIC_TYPE": "PSYCHIC",
}


def create_database(db_path=None):
    """Create SQLite database and tables"""
    db_path = db_path or DB_PATH
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Drop existing moves table if it exists
    cursor.execute("DROP TABLE IF EXISTS moves")

    # Create moves table
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS moves (
        id INTEGER PRIMARY KEY,
        constant_name TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        short_name TEXT NOT NULL,
        effect TEXT,
        power INTEGER,
        type TEXT,
        accuracy INTEGER,
        pp INTEGER,
        battle_animation TEXT,
        battle_sound TEXT,
        battle_sound_pitch INTEGER,
        battle_sound_tempo INTEGER,
        battle_subanimation TEXT,
        battle_tileset INTEGER,
        battle_delay INTEGER,
        field_move_effect INTEGER DEFAULT 0,
        grammar_type INTEGER DEFAULT 0,
        is_hm INTEGER DEFAULT 0
    )
    """
    )

    return conn


def parse_move_constants(path=None):
    """Parse move constants from move_constants.asm"""
    move_constants = {}

    path = path or CONSTANTS_DIR / "move_constants.asm"
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    for line in lines:
        match = re.match(
            r"\s*const\s+([A-Za-z_][A-Za-z0-9_]*)\s*;\s*([0-9a-fA-F]+)\b",
            line,
        )
        if match:
            move_name = match.group(1)
            move_id_str = match.group(2)
            try:
                move_id = int(move_id_str, 16)
                move_constants[move_name] = move_id
            except ValueError:
                # Skip constants that don't have a valid hex ID
                continue

    return move_constants


def parse_move_line(line):
    """Parse one move macro without depending on source column alignment."""
    source = line.partition(";")[0]
    match = MOVE_RE.fullmatch(source)
    if not match:
        raise ValueError(f"Could not parse move macro: {line.rstrip()}")

    groups = match.groupdict()
    animation = groups["animation"]
    type_name = TYPE_MAPPING.get(groups["type"], groups["type"])
    return animation, {
        "animation": animation,
        "effect": groups["effect"],
        "power": int(groups["power"]),
        "type": type_name,
        "accuracy": int(groups["accuracy"]),
        "pp": int(groups["pp"]),
    }


def parse_move_data(path=None):
    """Parse move data from moves.asm"""
    moves_data = {}
    move_name_to_type = {}  # New mapping of move names to types

    path = path or POKEMON_DATA_DIR / "moves.asm"
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Skip header lines until we reach the moves table
    start_index = 0
    for i, line in enumerate(lines):
        if line.strip() == "Moves:":
            start_index = i + 2  # Skip the table_width line
            break

    # Parse each move entry
    move_id = 1
    for i in range(start_index, len(lines)):
        line = lines[i].strip()
        if line.startswith("assert_table_length"):
            break

        if line.startswith("move "):
            try:
                animation, move_data = parse_move_line(lines[i])
            except ValueError as exc:
                raise ValueError(f"{path}:{i + 1}: {exc}") from exc
            if animation in moves_data:
                raise ValueError(f"{path}:{i + 1}: duplicate move {animation}")
            moves_data[animation] = move_data
            move_name_to_type[animation] = move_data["type"]

    return moves_data, move_name_to_type


def parse_move_names(path=None):
    """Parse move names from names.asm"""
    move_names = {}

    path = path or POKEMON_DATA_DIR / "names.asm"
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Skip header lines until we reach the move names
    start_index = 0
    for i, line in enumerate(lines):
        if line.strip() == "MoveNames::":
            start_index = i + 2  # Skip the list_start line
            break

    # Parse each move name
    move_id = 1
    for i in range(start_index, len(lines)):
        line = lines[i].strip()
        if line.startswith("assert_list_length"):
            break

        if line.startswith('li "'):
            # Extract move name
            match = re.match(r'li "([^"]+)"', line)
            if match:
                name = match.group(1)
                move_names[move_id] = name
                move_id += 1

    return move_names


def parse_move_sounds(path=None):
    """Parse move sound effects from sfx.asm"""
    move_sounds = {}

    path = path or POKEMON_DATA_DIR / "sfx.asm"
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Skip header lines until we reach the sound table
    start_index = 0
    for i, line in enumerate(lines):
        if line.strip() == "MoveSoundTable:":
            start_index = i + 2  # Skip the table_width line
            break

    # Parse each sound entry
    move_id = 1
    for i in range(start_index, len(lines)):
        line = lines[i].strip()
        if line.startswith("assert_table_length"):
            break

        if line.startswith("db "):
            # Extract sound data
            match = re.fullmatch(
                r"db\s+([A-Za-z_][A-Za-z0-9_]*)\s*,\s*"
                r"\$([0-9a-fA-F]+)\s*,\s*\$([0-9a-fA-F]+)"
                r"(?:\s*;.*)?",
                line,
            )
            if not match:
                raise ValueError(f"{path}:{i + 1}: could not parse move sound: {line}")
            sound, pitch, tempo = match.groups()
            move_sounds[move_id] = {
                "sound": sound,
                "pitch": int(pitch, 16),
                "tempo": int(tempo, 16),
            }
            move_id += 1

    return move_sounds


def validate_exact_move_ids(label, ids):
    """Require the complete Gen 1 move ID domain, with an actionable error."""
    actual = set(ids)
    missing = sorted(EXPECTED_MOVE_IDS - actual)
    unexpected = sorted(actual - EXPECTED_MOVE_IDS)
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing IDs {missing}")
        if unexpected:
            details.append(f"unexpected IDs {unexpected}")
        raise ValueError(
            f"{label} must contain exactly {EXPECTED_MOVE_COUNT} move IDs: "
            + "; ".join(details)
        )


def validate_move_sources(move_constants, moves_data, move_names, move_sounds):
    """Validate the four parallel source tables before replacing database data."""
    if move_constants.get("NO_MOVE") != 0:
        raise ValueError("move constants must define NO_MOVE as ID 0")

    constants = {
        name: move_id
        for name, move_id in move_constants.items()
        if name != "NO_MOVE"
    }
    ids_to_names = {}
    for name, move_id in constants.items():
        ids_to_names.setdefault(move_id, []).append(name)
    duplicate_ids = {
        move_id: names for move_id, names in ids_to_names.items() if len(names) > 1
    }
    if duplicate_ids:
        raise ValueError(f"move constants contain duplicate IDs: {duplicate_ids}")

    validate_exact_move_ids("move constants", constants.values())
    validate_exact_move_ids("move names", move_names)
    validate_exact_move_ids("move sounds", move_sounds)

    unknown_data = sorted(set(moves_data) - set(constants))
    missing_data = sorted(set(constants) - set(moves_data))
    if unknown_data or missing_data:
        details = []
        if missing_data:
            details.append(f"missing constants {missing_data}")
        if unknown_data:
            details.append(f"unknown constants {unknown_data}")
        raise ValueError("move data does not match move constants: " + "; ".join(details))

    validate_exact_move_ids(
        "move data", (constants[move_name] for move_name in moves_data)
    )

    missing_audited = sorted(AUDITED_MOVE_CONSTANTS - set(moves_data))
    if missing_audited:
        raise ValueError(f"move data is missing audited moves: {missing_audited}")


def validate_moves_table(conn):
    """Validate a generated moves table and return its row count."""
    rows = conn.execute("SELECT id, constant_name FROM moves ORDER BY id").fetchall()
    validate_exact_move_ids("moves table", (row[0] for row in rows))
    expected_constants = {
        name for name, move_id in parse_move_constants().items() if move_id != 0
    }
    actual_constants = {row[1] for row in rows}
    if actual_constants != expected_constants:
        raise ValueError(
            "moves table constant-name coverage mismatch: "
            f"missing={sorted(expected_constants - actual_constants)}, "
            f"extra={sorted(actual_constants - expected_constants)}"
        )
    missing_constants = conn.execute(
        """
        SELECT id
        FROM moves
        WHERE effect IS NULL OR battle_animation IS NULL
           OR battle_sound IS NULL OR battle_sound = 'NO_SOUND'
        ORDER BY id
        """
    ).fetchall()
    if missing_constants:
        raise ValueError(
            "moves table has missing effect/animation/sound constants for IDs "
            f"{[row[0] for row in missing_constants]}"
        )
    return len(rows)


DEPENDENT_MOVE_REFERENCES = (
    # Third value is an SQL condition identifying a disallowed NULL. Items
    # legitimately have no move, and pokered has one source-level UNUSED TM/HM
    # compatibility bit that is not a move relationship.
    ("items", "move_id", None),
    ("pokemon_learnset", "move_id", "1"),
    ("pokemon_tmhm", "move_id", "move_name != 'UNUSED'"),
)


def validate_dependent_move_references(conn, require_tables=False):
    """Reject dangling or unexpectedly-null references to the moves table.

    This is intended for the final pipeline validator, after all dependent
    exporters have run. During the standalone move export, those tables may not
    exist yet or may still contain data from the previous extraction.
    """
    validated_counts = {}
    for table, column, unexpected_null_condition in DEPENDENT_MOVE_REFERENCES:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if not exists:
            if require_tables:
                raise ValueError(f"missing move-dependent table: {table}")
            continue

        total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        dangling = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM {table} AS dependent
            LEFT JOIN moves ON moves.id = dependent.{column}
            WHERE dependent.{column} IS NOT NULL AND moves.id IS NULL
            """
        ).fetchone()[0]
        null_count = 0
        if unexpected_null_condition:
            null_count = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM {table}
                WHERE {column} IS NULL AND ({unexpected_null_condition})
                """
            ).fetchone()[0]
        if dangling or null_count:
            raise ValueError(
                f"{table}.{column} has {dangling} dangling and "
                f"{null_count} unexpectedly-null move references"
            )
        validated_counts[table] = total
    return validated_counts


def parse_move_grammar():
    """Parse move grammar from grammar.asm"""
    move_grammar = {}

    with open(POKEMON_DATA_DIR / "grammar.asm", "r") as f:
        lines = f.readlines()

    # Parse each grammar set
    current_set = 0
    for i, line in enumerate(lines):
        line = line.strip()

        if line.startswith("; set "):
            # Extract set number
            match = re.match(r"; set (\d+)", line)
            if match:
                current_set = int(match.group(1))

        elif (
            line.startswith("db ")
            and not line.startswith("db 0")
            and not line.startswith("db -1")
        ):
            # Extract move name
            move_name = line.replace("db ", "").strip()
            move_grammar[move_name] = current_set

    return move_grammar


def parse_battle_animations():
    """Parse battle animations from animations.asm"""
    battle_animations = {}

    with open(POKEMON_DATA_DIR / "animations.asm", "r") as f:
        lines = f.readlines()

    # Find all animation definitions
    current_move = None
    for i, line in enumerate(lines):
        # Check for animation label (e.g., "PoundAnim:")
        anim_match = re.match(r"(\w+)Anim:", line)
        if anim_match:
            current_move = anim_match.group(1).upper()
            battle_animations[current_move] = []

        # Check for battle_anim macro
        if "battle_anim" in line and current_move:
            match = re.search(
                r"battle_anim (\w+),\s+(\w+)(?:,\s+(\d+),\s+(\d+))?", line
            )
            if match:
                groups = match.groups()
                move_sound = groups[0]
                subanimation = groups[1]

                if groups[2] is not None and groups[3] is not None:
                    tileset = int(groups[2])
                    delay = int(groups[3])
                    battle_animations[current_move].append(
                        {
                            "sound": move_sound,
                            "subanimation": subanimation,
                            "tileset": tileset,
                            "delay": delay,
                        }
                    )
                else:
                    battle_animations[current_move].append(
                        {
                            "sound": move_sound,
                            "subanimation": subanimation,
                            "tileset": None,
                            "delay": None,
                        }
                    )

    return battle_animations


def main(db_path=None):
    # Parse and validate every parallel source table before replacing database
    # data. A formatting change must fail loudly instead of publishing a
    # plausible-looking partial move table.
    move_constants = parse_move_constants()
    moves_data, move_name_to_type = parse_move_data()
    move_names = parse_move_names()
    move_sounds = parse_move_sounds()
    validate_move_sources(move_constants, moves_data, move_names, move_sounds)

    # Create database
    conn = create_database(db_path)
    cursor = conn.cursor()

    move_grammar = parse_move_grammar()
    battle_animations = parse_battle_animations()

    # Insert data into database
    for move_name, move_data in moves_data.items():
        # Get the move ID from the constants
        move_id = move_constants[move_name]

        # Get sound data
        sound_data = move_sounds[move_id]

        # Get the proper name from move_names
        name = move_names[move_id]
        # Generate short_name by converting the name to uppercase and replacing spaces with underscores
        short_name = name.replace(" ", "_").upper()

        # Check if it's a field move
        field_move_effect = 1 if short_name in FIELD_MOVES else 0

        # Check if it's an HM move
        is_hm = 1 if short_name in HM_MOVES else 0

        # Get grammar type
        grammar_type = move_grammar.get(move_data["animation"], 0)

        # Get battle animation data
        battle_anim_data = battle_animations.get(
            move_data["animation"],
            [{"subanimation": "NO_SUBANIMATION", "tileset": 0, "delay": 0}],
        )

        # Use the first animation entry if available
        battle_subanimation = "NO_SUBANIMATION"
        battle_tileset = 0
        battle_delay = 0

        if battle_anim_data:
            first_anim = battle_anim_data[0]
            battle_subanimation = first_anim.get("subanimation", "NO_SUBANIMATION")
            battle_tileset = first_anim.get("tileset", 0) or 0
            battle_delay = first_anim.get("delay", 0) or 0

        # Get the type for this move
        type_name = move_data["type"]

        cursor.execute(
            """
            INSERT INTO moves (
                id, constant_name, name, short_name, effect, power, type, accuracy, pp,
                battle_animation, battle_sound, battle_sound_pitch, battle_sound_tempo,
                battle_subanimation, battle_tileset, battle_delay,
                field_move_effect, grammar_type, is_hm
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                move_id,
                move_name,
                name,
                short_name,
                move_data["effect"],
                move_data["power"],
                type_name,
                move_data["accuracy"],
                move_data["pp"],
                move_data["animation"],
                sound_data["sound"],
                sound_data["pitch"],
                sound_data["tempo"],
                battle_subanimation,
                battle_tileset,
                battle_delay,
                field_move_effect,
                grammar_type,
                is_hm,
            ),
        )

    exported_count = validate_moves_table(conn)

    # Commit changes and close connection
    conn.commit()
    conn.close()

    # Log number of moves exported
    print(f"Successfully exported {exported_count} moves to pokemon.db")


if __name__ == "__main__":
    main()
