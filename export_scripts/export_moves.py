#!/usr/bin/env python3
import os
import re
import sqlite3

from config import CONSTANTS_DIR, DB_PATH, MOVES_DATA_DIR

POKEMON_DATA_DIR = MOVES_DATA_DIR

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


def create_database():
    """Create SQLite database and tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Drop existing moves table if it exists
    cursor.execute("DROP TABLE IF EXISTS moves")

    # Create moves table
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS moves (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        short_name TEXT NOT NULL,
        effect INTEGER,
        power INTEGER,
        type TEXT,
        accuracy INTEGER,
        pp INTEGER,
        battle_animation INTEGER,
        battle_sound INTEGER,
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


def parse_move_constants():
    """Parse move constants from move_constants.asm"""
    move_constants = {}

    with open(CONSTANTS_DIR / "move_constants.asm", "r") as f:
        lines = f.readlines()

    for line in lines:
        match = re.search(r"const (\w+)\s*; (\w+)", line)
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


def parse_move_data():
    """Parse move data from moves.asm"""
    moves_data = {}
    move_name_to_type = {}  # New mapping of move names to types

    with open(POKEMON_DATA_DIR / "moves.asm", "r") as f:
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
            # Extract move data using regex
            match = re.match(
                r"move (\w+),\s+(\w+),\s+(\d+), (\w+),\s+(\d+), (\d+)", line
            )
            if match:
                animation, effect, power, type_name, accuracy, pp = match.groups()
                # Map the type name to its proper value
                type_name = TYPE_MAPPING.get(type_name, type_name)
                moves_data[animation] = {
                    "animation": animation,
                    "effect": effect,
                    "power": int(power),
                    "type": type_name,
                    "accuracy": int(accuracy),
                    "pp": int(pp),
                }
                # Store the mapping of move name to type
                move_name_to_type[animation] = type_name

    return moves_data, move_name_to_type


def parse_move_names():
    """Parse move names from names.asm"""
    move_names = {}

    with open(POKEMON_DATA_DIR / "names.asm", "r") as f:
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


def parse_move_sounds():
    """Parse move sound effects from sfx.asm"""
    move_sounds = {}

    with open(POKEMON_DATA_DIR / "sfx.asm", "r") as f:
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
            match = re.match(r"db (\w+),\s+\$([0-9a-f]+), \$([0-9a-f]+)", line)
            if match:
                sound, pitch, tempo = match.groups()
                move_sounds[move_id] = {
                    "sound": sound,
                    "pitch": int(pitch, 16),
                    "tempo": int(tempo, 16),
                }
                move_id += 1

    return move_sounds


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


def main():
    # Create database
    conn = create_database()
    cursor = conn.cursor()

    # Parse data
    move_constants = parse_move_constants()
    moves_data, move_name_to_type = parse_move_data()
    move_names = parse_move_names()
    move_sounds = parse_move_sounds()
    move_grammar = parse_move_grammar()
    battle_animations = parse_battle_animations()

    # Insert data into database
    for move_name, move_data in moves_data.items():
        # Get the move ID from the constants
        move_id = move_constants.get(move_name, 0)
        if move_id == 0:
            continue  # Skip moves without a valid ID

        # Get sound data
        sound_data = move_sounds.get(
            move_id, {"sound": "NO_SOUND", "pitch": 0, "tempo": 0}
        )

        # Get the proper name from move_names
        name = move_names.get(move_id, f"MOVE_{move_id}")
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
                id, name, short_name, effect, power, type, accuracy, pp,
                battle_animation, battle_sound, battle_sound_pitch, battle_sound_tempo,
                battle_subanimation, battle_tileset, battle_delay,
                field_move_effect, grammar_type, is_hm
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                move_id,
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

    # Commit changes and close connection
    conn.commit()
    conn.close()

    # Log number of moves exported
    print(f"Successfully exported {len(moves_data)} moves to pokemon.db")


if __name__ == "__main__":
    main()
