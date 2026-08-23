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
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from config import (
    DB_PATH,
    MAP_CONSTANTS_FILE,
    WILD_DIR,
    WILD_MAPS_DIR,
)


RELEASES = ("red", "blue")
WILD_DATA_POINTERS_FILE = WILD_DIR / "grass_water.asm"


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
        source_label TEXT,
        encounter_type TEXT NOT NULL,
        encounter_rate INTEGER NOT NULL DEFAULT 0,
        slot_index INTEGER NOT NULL,
        pokemon_name TEXT NOT NULL,
        level INTEGER NOT NULL,
        version TEXT DEFAULT 'both',
        CHECK (encounter_type IN ('grass', 'water', 'super_rod', 'good_rod')),
        CHECK (slot_index > 0),
        CHECK (version IN ('red', 'blue', 'both')),
        UNIQUE (map_name, encounter_type, version, slot_index),
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


def load_map_constant_order(file_path=MAP_CONSTANTS_FILE):
    """Return map constants in their source-defined numeric order.

    ``WildDataPointers`` is an index-aligned table.  Its comments are helpful to
    humans but are incomplete, so deriving map identity from comments or wild
    data filenames is not reliable.
    """
    constants = []
    pattern = re.compile(r"^\s*map_const\s+([A-Z][A-Z0-9_]*)\s*,")

    with open(file_path, "r", encoding="utf-8") as source:
        for line in source:
            match = pattern.match(line)
            if match:
                constants.append(match.group(1))

    if not constants:
        raise ValueError(f"No map constants found in {file_path}")
    if len(constants) != len(set(constants)):
        raise ValueError(f"Duplicate map constants found in {file_path}")
    return constants


def load_wild_data_pointer_map(
    pointer_file=WILD_DATA_POINTERS_FILE,
    map_constants_file=MAP_CONSTANTS_FILE,
):
    """Return ``WildMons`` label -> canonical map constants.

    The pointer and map-constant tables are both indexed by map ID.  Repeated
    pointers are retained, which is required for shared data such as
    ``SeaRoutesWildMons`` (Route 19 and Route 20).
    """
    map_constants = load_map_constant_order(map_constants_file)
    pointers = []
    in_table = False

    with open(pointer_file, "r", encoding="utf-8") as source:
        for line in source:
            code = line.split(";", 1)[0].strip()
            if code == "WildDataPointers:":
                in_table = True
                continue
            if not in_table:
                continue
            if code.startswith("assert_table_length"):
                break

            match = re.match(r"^dw\s+([A-Za-z_][A-Za-z0-9_]*)\b", code)
            if match:
                pointers.append(match.group(1))

    if not in_table:
        raise ValueError(f"WildDataPointers table not found in {pointer_file}")
    if len(pointers) != len(map_constants):
        raise ValueError(
            "WildDataPointers/map constant length mismatch: "
            f"{len(pointers)} pointers for {len(map_constants)} maps"
        )

    result = defaultdict(list)
    for map_name, source_label in zip(map_constants, pointers):
        result[source_label].append(map_name)
    return dict(result)


def parse_wild_map_definition(file_path):
    """Parse one wild-data source and retain its assembly label."""
    with open(file_path, "r", encoding="utf-8") as source:
        content = source.read()

    label_match = re.search(
        r"^([A-Za-z_][A-Za-z0-9_]*WildMons):",
        content,
        flags=re.MULTILINE,
    )
    if not label_match:
        raise ValueError(f"No WildMons label found in {file_path}")

    lines = content.splitlines()
    encounters = []
    encounters.extend(parse_encounter_section(lines, "grass", source=file_path))
    encounters.extend(parse_encounter_section(lines, "water", source=file_path))
    return label_match.group(1), encounters


def parse_wild_map_file(file_path):
    """Compatibility wrapper returning encounters without the source label."""
    _source_label, encounters = parse_wild_map_definition(file_path)
    return encounters


def _parse_rgbds_int(value):
    """Parse the integer formats used by RGBDS assembly source."""
    if value.startswith("$"):
        return int(value[1:], 16)
    if value.startswith("%"):
        return int(value[1:], 2)
    return int(value, 10)


def _release_condition_matches(expression, release, source):
    expression = re.sub(r"\s+", "", expression).upper()
    expected = f"DEF(_{release.upper()})"
    if expression == expected:
        return True
    if expression in {f"DEF(_{other.upper()})" for other in RELEASES}:
        return False
    if expression == f"!{expected}":
        return False
    if expression in {f"!DEF(_{other.upper()})" for other in RELEASES}:
        return True
    raise ValueError(f"Unsupported wild encounter condition {expression!r} in {source}")


def _compile_section_for_release(section_lines, release, source):
    """Evaluate RGBDS conditionals and return one release's encounter rows."""
    entries = []
    active = True
    conditional_stack = []

    for line_number, line in section_lines:
        code = line.split(";", 1)[0].strip()
        if not code:
            continue

        if_match = re.match(r"^IF\s+(.+)$", code, flags=re.IGNORECASE)
        if if_match:
            condition = _release_condition_matches(if_match.group(1), release, source)
            frame = {
                "parent_active": active,
                "branch_taken": condition,
            }
            conditional_stack.append(frame)
            active = active and condition
            continue

        elif_match = re.match(r"^ELIF\s+(.+)$", code, flags=re.IGNORECASE)
        if elif_match:
            if not conditional_stack:
                raise ValueError(f"ELIF without IF at {source}:{line_number}")
            frame = conditional_stack[-1]
            condition = _release_condition_matches(elif_match.group(1), release, source)
            active = frame["parent_active"] and not frame["branch_taken"] and condition
            frame["branch_taken"] = frame["branch_taken"] or condition
            continue

        if code.upper() == "ELSE":
            if not conditional_stack:
                raise ValueError(f"ELSE without IF at {source}:{line_number}")
            frame = conditional_stack[-1]
            active = frame["parent_active"] and not frame["branch_taken"]
            frame["branch_taken"] = True
            continue

        if code.upper() == "ENDC":
            if not conditional_stack:
                raise ValueError(f"ENDC without IF at {source}:{line_number}")
            frame = conditional_stack.pop()
            active = frame["parent_active"]
            continue

        if not active:
            continue

        entry_match = re.match(
            r"^db\s+([$%]?[0-9A-Fa-f]+)\s*,\s*([A-Z][A-Z0-9_]*)\b",
            code,
        )
        if entry_match:
            entries.append(
                (_parse_rgbds_int(entry_match.group(1)), entry_match.group(2))
            )

    if conditional_stack:
        raise ValueError(f"Unclosed IF block in {source}")
    return entries


def parse_encounter_section(lines, encounter_type, source="<memory>"):
    """Compile a grass/water section into explicit Red and Blue slot tables.

    Common rows are emitted once for each release.  This makes each non-empty
    ``(encounter_type, version)`` group a self-contained table with slots 1-10,
    including when common rows occur on both sides of conditional blocks.
    """
    if encounter_type not in {"grass", "water"}:
        raise ValueError(f"Unsupported encounter type: {encounter_type}")

    macro_start = f"def_{encounter_type}_wildmons"
    macro_end = f"end_{encounter_type}_wildmons"
    section_start = None
    encounter_rate = None
    section_lines = []

    for line_number, line in enumerate(lines, 1):
        code = line.split(";", 1)[0].strip()
        start_match = re.match(
            rf"^{macro_start}\s+([$%]?[0-9A-Fa-f]+)\b",
            code,
        )
        if start_match:
            if section_start is not None:
                raise ValueError(f"Duplicate {encounter_type} section in {source}")
            section_start = line_number
            encounter_rate = _parse_rgbds_int(start_match.group(1))
            continue

        if section_start is None:
            continue
        if code == macro_end:
            break
        section_lines.append((line_number, line))
    else:
        if section_start is not None:
            raise ValueError(f"Unclosed {encounter_type} section in {source}")

    if section_start is None:
        raise ValueError(f"Missing {encounter_type} section in {source}")

    encounters = []
    for release in RELEASES:
        release_entries = _compile_section_for_release(section_lines, release, source)
        expected_count = 0 if encounter_rate == 0 else 10
        if len(release_entries) != expected_count:
            raise ValueError(
                f"{source}: {encounter_type} table for {release} has "
                f"{len(release_entries)} slots; expected {expected_count}"
            )

        for slot_index, (level, pokemon_name) in enumerate(release_entries, 1):
            encounters.append(
                {
                    "encounter_type": encounter_type,
                    "encounter_rate": encounter_rate,
                    "slot_index": slot_index,
                    "pokemon_name": pokemon_name,
                    "level": level,
                    "version": release,
                }
            )

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


def collect_wild_map_definitions(wild_maps_dir=WILD_MAPS_DIR):
    """Load wild sources keyed by their assembly label, never their filename."""
    definitions = {}
    for wild_file in sorted(Path(wild_maps_dir).glob("*.asm")):
        source_label, encounters = parse_wild_map_definition(wild_file)
        if source_label in definitions:
            raise ValueError(
                f"Duplicate wild data label {source_label}: "
                f"{definitions[source_label]['path']} and {wild_file}"
            )
        definitions[source_label] = {
            "path": wild_file,
            "encounters": encounters,
        }
    return definitions


def export_grass_water_encounters(
    cursor,
    map_ids,
    wild_maps_dir=WILD_MAPS_DIR,
    pointer_file=WILD_DATA_POINTERS_FILE,
    map_constants_file=MAP_CONSTANTS_FILE,
):
    """Insert canonical per-map, per-release grass/water slot tables."""
    definitions = collect_wild_map_definitions(wild_maps_dir)
    pointer_map = load_wild_data_pointer_map(pointer_file, map_constants_file)

    missing_definitions = sorted(set(pointer_map) - set(definitions))
    if missing_definitions:
        raise ValueError(
            "WildDataPointers references undefined labels: "
            + ", ".join(missing_definitions)
        )

    unreferenced_definitions = sorted(set(definitions) - set(pointer_map))
    if unreferenced_definitions:
        raise ValueError(
            "Wild map definitions are absent from WildDataPointers: "
            + ", ".join(unreferenced_definitions)
        )

    inserted_count = 0
    maps_with_encounters = set()
    for source_label, map_names in pointer_map.items():
        encounters = definitions[source_label]["encounters"]
        if not encounters:
            continue

        for map_name in map_names:
            map_id = map_ids.get(map_name)
            if map_id is None:
                raise ValueError(
                    f"Wild encounter map {map_name} is missing from the maps table "
                    f"(source {source_label})"
                )
            maps_with_encounters.add(map_name)

            for encounter in encounters:
                cursor.execute(
                    """INSERT INTO wild_encounters
                       (map_name, map_id, source_label, encounter_type,
                        encounter_rate, slot_index, pokemon_name, level, version)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        map_name,
                        map_id,
                        source_label,
                        encounter["encounter_type"],
                        encounter["encounter_rate"],
                        encounter["slot_index"],
                        encounter["pokemon_name"],
                        encounter["level"],
                        encounter["version"],
                    ),
                )
                inserted_count += 1

    return inserted_count, len(maps_with_encounters)


def validate_wild_encounters(cursor):
    """Reject incomplete slots and broken canonical map relationships."""
    invalid_groups = cursor.execute(
        """
        SELECT map_name, encounter_type, version,
               COUNT(*) AS row_count,
               COUNT(DISTINCT slot_index) AS distinct_slots,
               MIN(slot_index) AS first_slot,
               MAX(slot_index) AS last_slot
        FROM wild_encounters
        WHERE encounter_type IN ('grass', 'water')
        GROUP BY map_name, encounter_type, version
        HAVING row_count != 10
            OR distinct_slots != 10
            OR first_slot != 1
            OR last_slot != 10
        """
    ).fetchall()
    if invalid_groups:
        raise ValueError(f"Incomplete grass/water encounter groups: {invalid_groups[:5]}")

    invalid_releases = cursor.execute(
        """
        SELECT COUNT(*)
        FROM wild_encounters
        WHERE encounter_type IN ('grass', 'water')
          AND version NOT IN ('red', 'blue')
        """
    ).fetchone()[0]
    if invalid_releases:
        raise ValueError(
            f"Found {invalid_releases} grass/water rows without an explicit release"
        )

    broken_maps = cursor.execute(
        """
        SELECT e.map_name, e.map_id, e.source_label
        FROM wild_encounters AS e
        LEFT JOIN maps AS m ON m.id = e.map_id
        WHERE e.map_name != 'GLOBAL'
          AND (e.map_id IS NULL OR m.id IS NULL OR m.name != e.map_name)
        LIMIT 5
        """
    ).fetchall()
    if broken_maps:
        raise ValueError(f"Broken wild encounter map relationships: {broken_maps}")


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
    grass_water_count, maps_with_encounters = export_grass_water_encounters(
        cursor,
        map_ids,
    )

    print(f"  Extracted {grass_water_count} grass/water encounters from {maps_with_encounters} maps")

    # =========================================================================
    # Phase 3: Super Rod fishing encounters
    # =========================================================================
    print("\nPhase 3: Extracting Super Rod encounters...")
    super_rod_data = parse_super_rod()
    super_rod_count = 0

    for map_const, encounters in super_rod_data.items():
        map_id = map_ids.get(map_const)
        if map_id is None:
            raise ValueError(f"Super Rod map {map_const} is missing from the maps table")

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

    validate_wild_encounters(cursor)
    print("  Validated complete release slot tables and canonical map relationships")

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
