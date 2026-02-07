#!/usr/bin/env python3
"""
Extract trainer data from the pokered disassembly.

Parses:
  - constants/trainer_constants.asm for trainer class IDs
  - data/trainers/names.asm for trainer class display names
  - data/trainers/parties.asm for trainer party compositions
  - data/trainers/pic_pointers_money.asm for base prize money
  - data/trainers/ai_pointers.asm for AI behavior
  - data/maps/objects/*.asm for NPC-to-trainer links (OPP_ references)

Creates tables:
  - trainer_classes: Trainer class definitions (Youngster, Bug Catcher, etc.)
  - trainer_parties: Individual trainer party compositions
  - trainer_party_pokemon: Pokemon in each trainer party
"""
import os
import re
import sqlite3
from pathlib import Path

# Constants
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "pokemon.db"
TRAINERS_DIR = PROJECT_ROOT / "pokemon-game-data/data/trainers"
CONSTANTS_DIR = PROJECT_ROOT / "pokemon-game-data/constants"
OBJECTS_DIR = PROJECT_ROOT / "pokemon-game-data/data/maps/objects"
MOVE_CONSTANTS_FILE = CONSTANTS_DIR / "move_constants.asm"


def create_tables(conn):
    """Create trainer-related tables."""
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS trainer_classes")
    cursor.execute("DROP TABLE IF EXISTS trainer_parties")
    cursor.execute("DROP TABLE IF EXISTS trainer_party_pokemon")

    # Trainer class definitions
    cursor.execute("""
    CREATE TABLE trainer_classes (
        id INTEGER PRIMARY KEY,
        constant_name TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        base_money INTEGER NOT NULL DEFAULT 0,
        is_gym_leader INTEGER DEFAULT 0,
        is_elite_four INTEGER DEFAULT 0,
        is_rival INTEGER DEFAULT 0
    )
    """)

    # Individual trainer parties (each entry in the parties.asm data block)
    cursor.execute("""
    CREATE TABLE trainer_parties (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trainer_class_id INTEGER NOT NULL,
        party_index INTEGER NOT NULL,
        location_comment TEXT,
        is_variable_level INTEGER DEFAULT 0,
        FOREIGN KEY (trainer_class_id) REFERENCES trainer_classes (id)
    )
    """)

    # Pokemon in each trainer party
    cursor.execute("""
    CREATE TABLE trainer_party_pokemon (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trainer_party_id INTEGER NOT NULL,
        slot_index INTEGER NOT NULL,
        pokemon_name TEXT NOT NULL,
        level INTEGER NOT NULL,
        FOREIGN KEY (trainer_party_id) REFERENCES trainer_parties (id)
    )
    """)

    conn.commit()
    return cursor


def parse_trainer_constants():
    """
    Parse trainer_constants.asm for class IDs.
    Returns list of (id, constant_name) in order.
    """
    constants = []
    constants_file = CONSTANTS_DIR / "trainer_constants.asm"

    with open(constants_file, "r") as f:
        lines = f.readlines()

    trainer_id = 0
    for line in lines:
        stripped = line.strip()
        match = re.match(r"trainer_const\s+(\w+)\s*;\s*\$([0-9A-Fa-f]+)", stripped)
        if match:
            name = match.group(1)
            hex_id = int(match.group(2), 16)
            constants.append((hex_id, name))

    return constants


def parse_trainer_names():
    """Parse trainer display names from names.asm. Returns list in order."""
    names = []
    names_file = TRAINERS_DIR / "names.asm"

    with open(names_file, "r") as f:
        for line in f:
            match = re.match(r'\s*li\s+"([^"]+)"', line.strip())
            if match:
                names.append(match.group(1))

    return names


def parse_prize_money():
    """Parse base prize money from pic_pointers_money.asm. Returns list in order."""
    money = []
    money_file = TRAINERS_DIR / "pic_pointers_money.asm"

    with open(money_file, "r") as f:
        for line in f:
            match = re.match(r"\s*pic_money\s+\w+,\s+(\d+)", line.strip())
            if match:
                money.append(int(match.group(1)))

    return money


def parse_trainer_parties():
    """
    Parse parties.asm for all trainer party compositions.
    
    Format 1 (uniform level): db <level>, <POKEMON1>, <POKEMON2>, ..., 0
    Format 2 (variable level): db $FF, <level1>, <POKEMON1>, <level2>, <POKEMON2>, ..., 0
    
    Returns dict of {class_data_label: [(location_comment, is_variable, [(level, pokemon), ...]), ...]}
    """
    parties_file = TRAINERS_DIR / "parties.asm"

    with open(parties_file, "r") as f:
        content = f.read()
        lines = content.split("\n")

    all_parties = {}
    current_class = None
    current_comment = None

    for line in lines:
        stripped = line.strip()

        # Match class data label (e.g., "YoungsterData:")
        class_match = re.match(r"^(\w+Data):$", stripped)
        if class_match:
            current_class = class_match.group(1)
            all_parties[current_class] = []
            current_comment = None
            continue

        # Match location comments (e.g., "; Route 3")
        comment_match = re.match(r"^;\s*(.+)$", stripped)
        if comment_match and current_class:
            current_comment = comment_match.group(1).strip()
            continue

        # Match party data lines
        if current_class and stripped.startswith("db "):
            party_data = stripped[3:].strip()

            # Remove inline comments
            if ";" in party_data:
                in_quote = False
                for ci, ch in enumerate(party_data):
                    if ch == '"':
                        in_quote = not in_quote
                    elif ch == ';' and not in_quote:
                        party_data = party_data[:ci].strip()
                        break

            # Remove trailing comma if present
            party_data = party_data.rstrip(",").strip()

            # Split by comma
            tokens = [t.strip() for t in party_data.split(",") if t.strip()]
            if not tokens:
                continue

            # Check if variable level format ($FF prefix)
            is_variable = False
            if tokens[0] in ("$FF", "255"):
                is_variable = True
                tokens = tokens[1:]

            pokemon_list = []
            if is_variable:
                # Variable level: pairs of (level, pokemon), terminated by 0
                i = 0
                while i < len(tokens) - 1:
                    if tokens[i] == "0":
                        break
                    try:
                        level = int(tokens[i])
                        pokemon = tokens[i + 1]
                        if pokemon != "0":
                            pokemon_list.append((level, pokemon))
                        i += 2
                    except (ValueError, IndexError):
                        break
            else:
                # Uniform level: first token is level, rest are pokemon, terminated by 0
                if not tokens:
                    continue
                try:
                    level = int(tokens[0])
                except ValueError:
                    continue
                for t in tokens[1:]:
                    if t == "0":
                        break
                    pokemon_list.append((level, t))

            if pokemon_list:
                all_parties[current_class].append({
                    "location": current_comment,
                    "is_variable": is_variable,
                    "pokemon": pokemon_list,
                })

    return all_parties


def get_class_label_to_constant_map():
    """
    Map party data labels (e.g., "YoungsterData") to trainer constant names.
    Based on the order in TrainerDataPointers.
    """
    parties_file = TRAINERS_DIR / "parties.asm"

    with open(parties_file, "r") as f:
        content = f.read()

    # Extract pointer table order
    pointer_order = []
    in_table = False
    for line in content.split("\n"):
        stripped = line.strip()
        if "TrainerDataPointers:" in stripped:
            in_table = True
            continue
        if in_table:
            match = re.match(r"dw\s+(\w+)", stripped)
            if match:
                pointer_order.append(match.group(1))
            elif "assert_table_length" in stripped:
                break

    return pointer_order


# Gym leaders, Elite Four, and Rival constants for tagging
GYM_LEADERS = {"BROCK", "MISTY", "LT_SURGE", "ERIKA", "KOGA", "BLAINE", "SABRINA", "GIOVANNI"}
ELITE_FOUR = {"LORELEI", "BRUNO", "AGATHA", "LANCE"}
RIVALS = {"RIVAL1", "RIVAL2", "RIVAL3"}


def main():
    conn = sqlite3.connect(DB_PATH)
    cursor = create_tables(conn)

    # Load reference data
    trainer_constants = parse_trainer_constants()
    trainer_names = parse_trainer_names()
    prize_money = parse_prize_money()
    all_parties = parse_trainer_parties()
    pointer_order = get_class_label_to_constant_map()

    print(f"Loaded {len(trainer_constants)} trainer classes, {len(trainer_names)} names, {len(prize_money)} money values")
    print(f"Parsed {sum(len(v) for v in all_parties.values())} total parties across {len(all_parties)} classes")

    # =========================================================================
    # Phase 1: Insert trainer classes
    # =========================================================================
    print("\nPhase 1: Inserting trainer classes...")

    # Build mapping from data label to constant
    label_to_constant = {}
    for i, label in enumerate(pointer_order):
        if i < len(trainer_constants):
            _, const_name = trainer_constants[i + 1] if i + 1 < len(trainer_constants) else (0, "UNKNOWN")
            # The pointer order matches trainer_constants order (starting from index 1, skipping NOBODY)
            if i + 1 < len(trainer_constants):
                label_to_constant[label] = trainer_constants[i + 1]

    for class_id, const_name in trainer_constants:
        if const_name == "NOBODY":
            continue

        display_name = trainer_names[class_id - 1] if class_id - 1 < len(trainer_names) else const_name
        money = prize_money[class_id - 1] if class_id - 1 < len(prize_money) else 0

        is_gym = 1 if const_name in GYM_LEADERS else 0
        is_e4 = 1 if const_name in ELITE_FOUR else 0
        is_rival = 1 if const_name in RIVALS else 0

        cursor.execute(
            """INSERT INTO trainer_classes 
               (id, constant_name, display_name, base_money, is_gym_leader, is_elite_four, is_rival)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (class_id, const_name, display_name, money, is_gym, is_e4, is_rival),
        )

    print(f"  Inserted {len(trainer_constants) - 1} trainer classes")

    # =========================================================================
    # Phase 2: Insert trainer parties
    # =========================================================================
    print("\nPhase 2: Inserting trainer parties...")

    total_parties = 0
    total_pokemon = 0

    for i, data_label in enumerate(pointer_order):
        if data_label not in all_parties:
            continue

        # Map data label to class ID
        class_id = i + 1  # trainer_constants are 1-indexed (NOBODY=0 is skipped)

        for party_idx, party in enumerate(all_parties[data_label]):
            cursor.execute(
                """INSERT INTO trainer_parties 
                   (trainer_class_id, party_index, location_comment, is_variable_level)
                   VALUES (?, ?, ?, ?)""",
                (class_id, party_idx + 1, party["location"], 1 if party["is_variable"] else 0),
            )
            party_id = cursor.lastrowid
            total_parties += 1

            for slot_idx, (level, pokemon) in enumerate(party["pokemon"]):
                cursor.execute(
                    """INSERT INTO trainer_party_pokemon 
                       (trainer_party_id, slot_index, pokemon_name, level)
                       VALUES (?, ?, ?, ?)""",
                    (party_id, slot_idx + 1, pokemon, level),
                )
                total_pokemon += 1

    print(f"  Inserted {total_parties} parties with {total_pokemon} total Pokemon")

    conn.commit()

    # =========================================================================
    # Summary
    # =========================================================================
    cursor.execute("SELECT COUNT(*) FROM trainer_classes")
    class_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM trainer_parties")
    party_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM trainer_party_pokemon")
    pokemon_count = cursor.fetchone()[0]

    print(f"\nResults:")
    print(f"  trainer_classes:       {class_count}")
    print(f"  trainer_parties:       {party_count}")
    print(f"  trainer_party_pokemon: {pokemon_count}")

    # Show some examples
    print("\nSample - Brock's party:")
    cursor.execute("""
        SELECT tc.display_name, tp.location_comment, tpp.pokemon_name, tpp.level
        FROM trainer_classes tc
        JOIN trainer_parties tp ON tp.trainer_class_id = tc.id
        JOIN trainer_party_pokemon tpp ON tpp.trainer_party_id = tp.id
        WHERE tc.constant_name = 'BROCK'
    """)
    for row in cursor.fetchall():
        print(f"  {row[0]} ({row[1]}): Lv.{row[3]} {row[2]}")

    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    main()
