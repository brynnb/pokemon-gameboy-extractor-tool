#!/usr/bin/env python3
"""
Extract wild Pokemon encounter data from the pokered disassembly.

Parses:
  - data/wild/maps/*.asm for grass and water encounters per map
  - data/wild/super_rod.asm for super rod fishing encounters
  - data/wild/good_rod.asm for good rod fishing encounters
  - data/wild/probabilities.asm for encounter slot probabilities

Creates tables:
  - wild_encounters: All wild Pokemon encounters (grass, water, fishing)
  - encounter_slots: Probability distribution for encounter slots
"""
import os
import re
import sqlite3
from pathlib import Path

# Constants
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "pokemon.db"
WILD_DIR = PROJECT_ROOT / "pokemon-game-data/data/wild"
WILD_MAPS_DIR = WILD_DIR / "maps"
CONSTANTS_DIR = PROJECT_ROOT / "pokemon-game-data/constants"
MAP_CONSTANTS_FILE = CONSTANTS_DIR / "map_constants.asm"
POKEDEX_CONSTANTS_FILE = CONSTANTS_DIR / "pokedex_constants.asm"


def create_tables(conn):
    """Create wild encounter tables."""
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS wild_encounters")
    cursor.execute("DROP TABLE IF EXISTS encounter_slots")

    # Wild encounters table
    cursor.execute("""
    CREATE TABLE wild_encounters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        map_name TEXT NOT NULL,
        map_id INTEGER,
        encounter_type TEXT NOT NULL,
        encounter_rate INTEGER NOT NULL DEFAULT 0,
        slot_index INTEGER NOT NULL,
        pokemon_name TEXT NOT NULL,
        level INTEGER NOT NULL,
        version TEXT DEFAULT 'both',
        FOREIGN KEY (map_id) REFERENCES maps (id)
    )
    """)

    # Encounter slot probabilities
    cursor.execute("""
    CREATE TABLE encounter_slots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slot_index INTEGER NOT NULL,
        probability REAL NOT NULL,
        cumulative_probability REAL NOT NULL
    )
    """)

    conn.commit()
    return cursor


def load_map_ids(cursor):
    """Load map name -> ID mapping from the maps table."""
    cursor.execute("SELECT id, name FROM maps")
    return {name: id for id, name in cursor.fetchall()}


def parse_wild_map_file(file_path):
    """
    Parse a data/wild/maps/*.asm file for grass and water encounters.
    
    Format:
      MapNameWildMons:
        def_grass_wildmons <encounter_rate>
        db <level>, <POKEMON>
        ... (10 entries)
        end_grass_wildmons
        
        def_water_wildmons <encounter_rate>
        db <level>, <POKEMON>
        ... (10 entries)
        end_water_wildmons
    
    Some maps have version-specific encounters (IF DEF(_RED) / IF DEF(_BLUE)).
    Returns list of encounter dicts.
    """
    encounters = []

    with open(file_path, "r") as f:
        content = f.read()
        lines = content.split("\n")

    # Extract map name from the label
    map_label_match = re.search(r"(\w+)WildMons:", content)
    if not map_label_match:
        return encounters

    # Parse grass encounters
    grass_encounters = parse_encounter_section(lines, "grass")
    encounters.extend(grass_encounters)

    # Parse water encounters
    water_encounters = parse_encounter_section(lines, "water")
    encounters.extend(water_encounters)

    return encounters


def parse_encounter_section(lines, encounter_type):
    """Parse a grass or water encounter section from the file lines."""
    encounters = []
    macro_start = f"def_{encounter_type}_wildmons"
    macro_end = f"end_{encounter_type}_wildmons"

    in_section = False
    encounter_rate = 0
    slot_index = 0
    current_version = "both"

    for line in lines:
        stripped = line.strip()

        # Check for section start
        if macro_start in stripped:
            rate_match = re.search(rf"{macro_start}\s+(\d+)", stripped)
            if rate_match:
                encounter_rate = int(rate_match.group(1))
            in_section = True
            slot_index = 0
            current_version = "both"
            continue

        if macro_end in stripped:
            in_section = False
            continue

        if not in_section:
            continue

        # Handle version-specific blocks
        if "IF DEF(_RED)" in stripped:
            current_version = "red"
            continue
        elif "IF DEF(_BLUE)" in stripped:
            current_version = "blue"
            continue
        elif "ENDC" in stripped:
            current_version = "both"
            continue

        # Parse encounter entry: db <level>, <POKEMON>
        entry_match = re.match(r"\s*db\s+(\d+),\s+(\w+)", stripped)
        if entry_match:
            level = int(entry_match.group(1))
            pokemon_name = entry_match.group(2)
            slot_index += 1

            encounters.append({
                "encounter_type": encounter_type,
                "encounter_rate": encounter_rate,
                "slot_index": slot_index,
                "pokemon_name": pokemon_name,
                "level": level,
                "version": current_version,
            })

    return encounters


def parse_super_rod():
    """
    Parse data/wild/super_rod.asm for fishing encounters.
    Returns dict of {map_constant: [(level, pokemon_name), ...]}.
    """
    super_rod_file = WILD_DIR / "super_rod.asm"
    encounters = {}

    with open(super_rod_file, "r") as f:
        content = f.read()
        lines = content.split("\n")

    # Phase 1: Parse map -> group mappings
    map_groups = {}  # map_constant -> group_label
    for line in lines:
        stripped = line.strip()
        match = re.match(r"dbw\s+(\w+),\s+(\.\w+)", stripped)
        if match:
            map_const = match.group(1)
            group_label = match.group(2)
            map_groups[map_const] = group_label

    # Phase 2: Parse group definitions
    groups = {}  # group_label -> [(level, pokemon)]
    current_group = None
    for line in lines:
        stripped = line.strip()

        group_match = re.match(r"(\.\w+):", stripped)
        if group_match:
            current_group = group_match.group(1)
            groups[current_group] = []
            continue

        if current_group:
            entry_match = re.match(r"db\s+(\d+),\s+(\w+)", stripped)
            if entry_match:
                level = int(entry_match.group(1))
                pokemon = entry_match.group(2)
                groups[current_group].append((level, pokemon))
            elif stripped.startswith("db ") and re.match(r"db\s+\d+$", stripped):
                # This is the count line, skip it
                pass

    # Phase 3: Map constants to encounters
    for map_const, group_label in map_groups.items():
        if group_label in groups:
            encounters[map_const] = groups[group_label]

    return encounters


def parse_good_rod():
    """Parse data/wild/good_rod.asm. Returns list of (level, pokemon) tuples."""
    good_rod_file = WILD_DIR / "good_rod.asm"
    encounters = []

    with open(good_rod_file, "r") as f:
        for line in f:
            match = re.match(r"\s*db\s+(\d+),\s+(\w+)", line.strip())
            if match:
                level = int(match.group(1))
                pokemon = match.group(2)
                encounters.append((level, pokemon))

    return encounters


def parse_encounter_probabilities():
    """
    Parse data/wild/probabilities.asm for encounter slot probabilities.
    The original game uses 10 slots with specific probability distributions.
    """
    prob_file = WILD_DIR / "probabilities.asm"

    # Default Gen 1 encounter probabilities (slots 1-10)
    # These are the standard probabilities from the original game
    default_probs = [
        (1, 19.9, 19.9),   # Slot 1: ~20%
        (2, 19.9, 39.8),   # Slot 2: ~20%
        (3, 15.2, 55.0),   # Slot 3: ~15%
        (4, 9.8, 64.8),    # Slot 4: ~10%
        (5, 9.8, 74.6),    # Slot 5: ~10%
        (6, 9.8, 84.4),    # Slot 6: ~10%
        (7, 5.1, 89.5),    # Slot 7: ~5%
        (8, 5.1, 94.6),    # Slot 8: ~5%
        (9, 4.3, 98.9),    # Slot 9: ~4%
        (10, 1.2, 100.0),  # Slot 10: ~1%
    ]

    return default_probs


def convert_map_constant_to_name(constant):
    """Convert a map constant like PALLET_TOWN to a database-friendly name."""
    return constant


def main():
    conn = sqlite3.connect(DB_PATH)
    cursor = create_tables(conn)

    map_ids = load_map_ids(cursor)

    # =========================================================================
    # Phase 1: Encounter slot probabilities
    # =========================================================================
    print("Phase 1: Inserting encounter slot probabilities...")
    probs = parse_encounter_probabilities()
    for slot_idx, prob, cum_prob in probs:
        cursor.execute(
            "INSERT INTO encounter_slots (slot_index, probability, cumulative_probability) VALUES (?, ?, ?)",
            (slot_idx, prob, cum_prob),
        )
    print(f"  Inserted {len(probs)} encounter slot probabilities")

    # =========================================================================
    # Phase 2: Grass and water encounters from map files
    # =========================================================================
    print("\nPhase 2: Extracting grass/water encounters...")
    grass_water_count = 0
    maps_with_encounters = 0

    for wild_file in sorted(WILD_MAPS_DIR.glob("*.asm")):
        if wild_file.name == "nothing.asm":
            continue

        encounters = parse_wild_map_file(wild_file)
        if not encounters:
            continue

        # Derive map name from file name (e.g., Route1.asm -> ROUTE_1)
        file_stem = wild_file.stem
        # Convert CamelCase to UPPER_SNAKE_CASE
        map_name_upper = re.sub(r"([a-z])([A-Z])", r"\1_\2", file_stem)
        map_name_upper = re.sub(r"([A-Z])([A-Z][a-z])", r"\1_\2", map_name_upper)
        map_name_upper = re.sub(r"([a-zA-Z])(\d)", r"\1_\2", map_name_upper)
        map_name_upper = map_name_upper.upper()

        map_id = map_ids.get(map_name_upper)

        if encounters:
            maps_with_encounters += 1

        for enc in encounters:
            cursor.execute(
                """INSERT INTO wild_encounters 
                   (map_name, map_id, encounter_type, encounter_rate, slot_index,
                    pokemon_name, level, version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    map_name_upper,
                    map_id,
                    enc["encounter_type"],
                    enc["encounter_rate"],
                    enc["slot_index"],
                    enc["pokemon_name"],
                    enc["level"],
                    enc["version"],
                ),
            )
            grass_water_count += 1

    print(f"  Extracted {grass_water_count} grass/water encounters from {maps_with_encounters} maps")

    # =========================================================================
    # Phase 3: Super Rod fishing encounters
    # =========================================================================
    print("\nPhase 3: Extracting Super Rod encounters...")
    super_rod_data = parse_super_rod()
    super_rod_count = 0

    for map_const, encounters in super_rod_data.items():
        map_id = map_ids.get(map_const)

        for idx, (level, pokemon) in enumerate(encounters, 1):
            cursor.execute(
                """INSERT INTO wild_encounters 
                   (map_name, map_id, encounter_type, encounter_rate, slot_index,
                    pokemon_name, level, version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (map_const, map_id, "super_rod", 0, idx, pokemon, level, "both"),
            )
            super_rod_count += 1

    print(f"  Extracted {super_rod_count} Super Rod encounters from {len(super_rod_data)} maps")

    # =========================================================================
    # Phase 4: Good Rod fishing encounters (global, not map-specific)
    # =========================================================================
    print("\nPhase 4: Extracting Good Rod encounters...")
    good_rod_data = parse_good_rod()

    for idx, (level, pokemon) in enumerate(good_rod_data, 1):
        cursor.execute(
            """INSERT INTO wild_encounters 
               (map_name, map_id, encounter_type, encounter_rate, slot_index,
                pokemon_name, level, version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("GLOBAL", None, "good_rod", 0, idx, pokemon, level, "both"),
        )

    print(f"  Extracted {len(good_rod_data)} Good Rod encounters (global)")

    conn.commit()

    # Summary
    cursor.execute("SELECT COUNT(*) FROM wild_encounters")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT map_name) FROM wild_encounters")
    unique_maps = cursor.fetchone()[0]
    print(f"\nTotal: {total} wild encounters across {unique_maps} maps")

    conn.close()
    print("Done!")


if __name__ == "__main__":
    main()
