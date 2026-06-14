#!/usr/bin/env python3
"""
Extract hidden objects, hidden items, hidden coins, and map music from the pokered disassembly.

Parses:
  - data/events/hidden_objects.asm for hidden interactable objects
  - data/events/hidden_item_coords.asm for hidden item locations
  - data/events/hidden_coins.asm for hidden coin locations
  - data/maps/hide_show_data.asm + constants/hide_show_constants.asm for missable objects
  - data/maps/songs.asm for map music assignments

Creates tables:
  - hidden_items: Hidden item pickup locations
  - hidden_coins: Hidden coin pickup locations  
  - hidden_objects: Hidden interactable objects (PCs, bookcases, gym statues, etc.)
  - missable_objects: Source HS_* show/hide object state rows
  - map_music: Music assignment per map
"""
import os
import re
import sqlite3

from config import CONSTANTS_DIR, DB_PATH, EVENTS_DIR, MAP_DATA_DIR, MAP_OBJECTS_DIR

MAPS_DIR = MAP_DATA_DIR
OBJECTS_DIR = MAP_OBJECTS_DIR


def create_tables(conn):
    """Create hidden object and music tables."""
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS hidden_items")
    cursor.execute("DROP TABLE IF EXISTS hidden_coins")
    cursor.execute("DROP TABLE IF EXISTS hidden_objects")
    cursor.execute("DROP TABLE IF EXISTS missable_objects")
    cursor.execute("DROP TABLE IF EXISTS map_music")

    cursor.execute("""
    CREATE TABLE hidden_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        map_constant TEXT NOT NULL,
        map_id INTEGER,
        x INTEGER NOT NULL,
        y INTEGER NOT NULL,
        FOREIGN KEY (map_id) REFERENCES maps (id)
    )
    """)

    cursor.execute("""
    CREATE TABLE hidden_coins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        map_constant TEXT NOT NULL,
        map_id INTEGER,
        x INTEGER NOT NULL,
        y INTEGER NOT NULL,
        FOREIGN KEY (map_id) REFERENCES maps (id)
    )
    """)

    cursor.execute("""
    CREATE TABLE hidden_objects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        map_constant TEXT NOT NULL,
        map_id INTEGER,
        x INTEGER NOT NULL,
        y INTEGER NOT NULL,
        item_or_direction TEXT,
        routine TEXT,
        object_type TEXT DEFAULT 'hidden',
        FOREIGN KEY (map_id) REFERENCES maps (id)
    )
    """)

    cursor.execute("""
    CREATE TABLE missable_objects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hs_index INTEGER NOT NULL,
        hs_constant TEXT NOT NULL UNIQUE,
        map_constant TEXT NOT NULL,
        map_id INTEGER,
        object_constant TEXT NOT NULL,
        object_index INTEGER,
        object_name TEXT,
        object_type TEXT,
        initial_state TEXT NOT NULL,
        initial_visible INTEGER NOT NULL,
        label TEXT NOT NULL,
        FOREIGN KEY (map_id) REFERENCES maps (id)
    )
    """)

    cursor.execute("""
    CREATE TABLE map_music (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        map_constant TEXT NOT NULL,
        map_id INTEGER,
        music_constant TEXT NOT NULL,
        FOREIGN KEY (map_id) REFERENCES maps (id)
    )
    """)

    conn.commit()
    return cursor


def load_map_ids(cursor):
    """Load map name -> ID mapping from the maps table."""
    cursor.execute("SELECT id, name FROM maps")
    return {name: id for id, name in cursor.fetchall()}


def upper_snake_to_camel(value):
    special = {
        "SS": "SS",
        "TM": "TM",
        "HM": "HM",
        "PC": "PC",
    }
    parts = value.split("_")
    converted = []
    for part in parts:
        if part in special:
            converted.append(special[part])
        elif re.fullmatch(r"B?\d+F", part):
            converted.append(part)
        else:
            converted.append(part[:1] + part[1:].lower())
    return "".join(converted)


def parse_map_object_constants():
    constants = {}
    for object_path in OBJECTS_DIR.glob("*.asm"):
        map_name = object_path.stem
        object_constants = {}
        index = 1
        in_constants = False
        for raw_line in object_path.read_text().splitlines():
            stripped = raw_line.strip()
            if stripped == "object_const_def":
                in_constants = True
                index = 1
                continue
            if not in_constants:
                continue
            match = re.match(r"const_export\s+([A-Z0-9_]+)", stripped)
            if match:
                object_constants[match.group(1)] = index
                index += 1
                continue
            if stripped and not stripped.startswith(";"):
                break
        constants[map_name] = object_constants
    return constants


def object_info_for_index(cursor, map_id, map_name, object_index):
    if map_id is None or object_index is None:
        return None, None
    expected_name = f"{map_name}_NPC_{object_index}"
    cursor.execute(
        "SELECT name, object_type FROM objects WHERE map_id = ? AND name = ?",
        (map_id, expected_name),
    )
    row = cursor.fetchone()
    if row:
        return row[0], row[1]
    cursor.execute(
        "SELECT name, object_type FROM objects WHERE map_id = ? ORDER BY id LIMIT 1 OFFSET ?",
        (map_id, object_index - 1),
    )
    row = cursor.fetchone()
    if row:
        return row[0], row[1]
    return expected_name, None


def parse_hide_show_constants():
    constants = []
    file_path = CONSTANTS_DIR / "hide_show_constants.asm"
    in_constants = False
    for raw_line in file_path.read_text().splitlines():
        stripped = raw_line.strip()
        if stripped == "const_def":
            in_constants = True
            continue
        if not in_constants:
            continue
        match = re.match(r"const\s+(HS_[A-Z0-9_]+)", stripped)
        if match:
            constants.append(match.group(1))
            continue
        if stripped and not stripped.startswith(";") and not stripped.startswith("const"):
            break
    return constants


def parse_hide_show_rows():
    rows = []
    file_path = MAPS_DIR / "hide_show_data.asm"
    for raw_line in file_path.read_text().splitlines():
        stripped = raw_line.strip()
        match = re.match(r"db\s+([A-Z0-9_]+),\s+([A-Z0-9_]+),\s+(SHOW|HIDE)", stripped)
        if match:
            rows.append(
                {
                    "map_constant": match.group(1),
                    "object_constant": match.group(2),
                    "initial_state": match.group(3),
                }
            )
    return rows


def parse_missable_objects(cursor, map_ids):
    hs_constants = parse_hide_show_constants()
    hide_show_rows = parse_hide_show_rows()
    object_constants_by_map = parse_map_object_constants()
    missables = []
    for hs_index, (hs_constant, row) in enumerate(zip(hs_constants, hide_show_rows)):
        map_constant = row["map_constant"]
        map_id = map_ids.get(map_constant)
        map_name = upper_snake_to_camel(map_constant)
        object_constant = row["object_constant"]
        object_index = object_constants_by_map.get(map_name, {}).get(object_constant)
        object_name, object_type = object_info_for_index(cursor, map_id, map_name, object_index)
        initial_state = row["initial_state"]
        missables.append(
            {
                "hs_index": hs_index,
                "hs_constant": hs_constant,
                "map_constant": map_constant,
                "map_id": map_id,
                "object_constant": object_constant,
                "object_index": object_index,
                "object_name": object_name,
                "object_type": object_type,
                "initial_state": initial_state,
                "initial_visible": 1 if initial_state == "SHOW" else 0,
                "label": f"SourceMissableInitial:{hs_constant}",
            }
        )
    return missables


def parse_hidden_items(map_ids):
    """Parse hidden_item_coords.asm for hidden item locations."""
    items = []
    file_path = EVENTS_DIR / "hidden_item_coords.asm"

    with open(file_path, "r") as f:
        for line in f:
            stripped = line.strip()
            match = re.match(r"hidden_item\s+(\w+),\s+(\d+),\s+(\d+)", stripped)
            if match:
                map_const = match.group(1)
                x = int(match.group(2))
                y = int(match.group(3))
                map_id = map_ids.get(map_const)
                items.append((map_const, map_id, x, y))

    return items


def parse_hidden_coins(map_ids):
    """Parse hidden_coins.asm for hidden coin locations."""
    coins = []
    file_path = EVENTS_DIR / "hidden_coins.asm"

    with open(file_path, "r") as f:
        for line in f:
            stripped = line.strip()
            match = re.match(r"hidden_coin\s+(\w+),\s+(\d+),\s+(\d+)", stripped)
            if match:
                map_const = match.group(1)
                x = int(match.group(2))
                y = int(match.group(3))
                map_id = map_ids.get(map_const)
                coins.append((map_const, map_id, x, y))

    return coins


def parse_hidden_objects(map_ids):
    """
    Parse hidden_objects.asm for hidden interactable objects.
    These include PCs, bookcases, gym statues, posters, etc.
    """
    objects = []
    file_path = EVENTS_DIR / "hidden_objects.asm"

    with open(file_path, "r") as f:
        content = f.read()

    # Find all hidden object blocks per map
    # Format:
    # MapNameHiddenObjects:
    #     hidden_object  x, y, ITEM_OR_DIRECTION, RoutineName
    #     hidden_text_predef  x, y, TextPredefName, RoutineName
    #     db -1 ; end

    current_map = None

    for line in content.split("\n"):
        stripped = line.strip()

        # Match map label (e.g., "RedsHouse2FHiddenObjects:")
        label_match = re.match(r"^(\w+)HiddenObjects:", stripped)
        if label_match:
            label = label_match.group(1)
            # Try to derive map constant from the label
            # These labels don't always match map constants exactly
            current_map = label
            continue

        # Match hidden_object macro
        obj_match = re.match(
            r"hidden_object\s+(\d+),\s+(\d+),\s+(\w+),\s+(\w+)",
            stripped
        )
        if obj_match and current_map:
            x = int(obj_match.group(1))
            y = int(obj_match.group(2))
            item_dir = obj_match.group(3)
            routine = obj_match.group(4)

            # Determine object type from routine name
            obj_type = "hidden"
            if "PC" in routine or "Pc" in routine:
                obj_type = "pc"
            elif "Bookcase" in routine or "bookcase" in routine:
                obj_type = "bookcase"
            elif "GymStatue" in routine or "Statue" in routine:
                obj_type = "gym_statue"
            elif "Poster" in routine:
                obj_type = "poster"
            elif "BenchGuy" in routine:
                obj_type = "bench_guy"
            elif "Fossil" in routine:
                obj_type = "fossil"
            elif "Gameboy" in routine:
                obj_type = "cable_club"

            objects.append({
                "map_label": current_map,
                "x": x,
                "y": y,
                "item_or_direction": item_dir,
                "routine": routine,
                "object_type": obj_type,
            })
            continue

        # Match hidden_text_predef macro
        text_match = re.match(
            r"hidden_text_predef\s+(\d+),\s+(\d+),\s+(\w+),\s+(\w+)",
            stripped
        )
        if text_match and current_map:
            x = int(text_match.group(1))
            y = int(text_match.group(2))
            text_predef = text_match.group(3)
            routine = text_match.group(4)

            objects.append({
                "map_label": current_map,
                "x": x,
                "y": y,
                "item_or_direction": text_predef,
                "routine": routine,
                "object_type": "text_predef",
            })
            continue

        # End of block
        if stripped == "db -1 ; end":
            current_map = None

    return objects


def parse_map_music(map_ids):
    """Parse songs.asm for map music assignments."""
    music_data = []
    file_path = MAPS_DIR / "songs.asm"

    with open(file_path, "r") as f:
        for line in f:
            stripped = line.strip()
            # Format: db MUSIC_CONSTANT, BANK(Music_Name) ; MAP_CONSTANT
            match = re.match(
                r"db\s+(\w+),\s+BANK\(\w+\)\s*;\s*(\w+)",
                stripped
            )
            if match:
                music_const = match.group(1)
                map_const = match.group(2)
                map_id = map_ids.get(map_const)
                music_data.append((map_const, map_id, music_const))

    return music_data


def main():
    conn = sqlite3.connect(DB_PATH)
    cursor = create_tables(conn)
    map_ids = load_map_ids(cursor)

    # =========================================================================
    # Phase 1: Hidden items
    # =========================================================================
    print("Phase 1: Extracting hidden items...")
    hidden_items = parse_hidden_items(map_ids)
    for map_const, map_id, x, y in hidden_items:
        cursor.execute(
            "INSERT INTO hidden_items (map_constant, map_id, x, y) VALUES (?, ?, ?, ?)",
            (map_const, map_id, x, y),
        )
    print(f"  Extracted {len(hidden_items)} hidden items")

    # =========================================================================
    # Phase 2: Hidden coins
    # =========================================================================
    print("\nPhase 2: Extracting hidden coins...")
    hidden_coins = parse_hidden_coins(map_ids)
    for map_const, map_id, x, y in hidden_coins:
        cursor.execute(
            "INSERT INTO hidden_coins (map_constant, map_id, x, y) VALUES (?, ?, ?, ?)",
            (map_const, map_id, x, y),
        )
    print(f"  Extracted {len(hidden_coins)} hidden coins")

    # =========================================================================
    # Phase 3: Hidden objects
    # =========================================================================
    print("\nPhase 3: Extracting hidden objects...")
    hidden_objects = parse_hidden_objects(map_ids)
    for obj in hidden_objects:
        cursor.execute(
            """INSERT INTO hidden_objects 
               (map_constant, map_id, x, y, item_or_direction, routine, object_type)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (obj["map_label"], None, obj["x"], obj["y"],
             obj["item_or_direction"], obj["routine"], obj["object_type"]),
        )
    print(f"  Extracted {len(hidden_objects)} hidden objects")

    # =========================================================================
    # Phase 4: Missable objects
    # =========================================================================
    print("\nPhase 4: Extracting missable objects...")
    missable_objects = parse_missable_objects(cursor, map_ids)
    for obj in missable_objects:
        cursor.execute(
            """INSERT INTO missable_objects
               (hs_index, hs_constant, map_constant, map_id, object_constant,
                object_index, object_name, object_type, initial_state, initial_visible, label)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                obj["hs_index"],
                obj["hs_constant"],
                obj["map_constant"],
                obj["map_id"],
                obj["object_constant"],
                obj["object_index"],
                obj["object_name"],
                obj["object_type"],
                obj["initial_state"],
                obj["initial_visible"],
                obj["label"],
            ),
        )
    print(f"  Extracted {len(missable_objects)} missable objects")

    # =========================================================================
    # Phase 5: Map music
    # =========================================================================
    print("\nPhase 5: Extracting map music...")
    music_data = parse_map_music(map_ids)
    for map_const, map_id, music_const in music_data:
        cursor.execute(
            "INSERT INTO map_music (map_constant, map_id, music_constant) VALUES (?, ?, ?)",
            (map_const, map_id, music_const),
        )
    print(f"  Extracted {len(music_data)} map music assignments")

    conn.commit()

    # Summary
    print(f"\nResults:")
    for table in ["hidden_items", "hidden_coins", "hidden_objects", "missable_objects", "map_music"]:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"  {table}: {count}")

    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    main()
