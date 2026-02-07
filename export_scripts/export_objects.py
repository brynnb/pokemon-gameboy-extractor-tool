#!/usr/bin/env python3
import os
import re
import sqlite3
from pathlib import Path

# Constants
# Get the project root directory (parent of the script's directory)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "pokemon.db"
POKEMON_DATA_DIR = PROJECT_ROOT / "pokemon-game-data/data/maps/objects"
CONSTANTS_DIR = PROJECT_ROOT / "pokemon-game-data/constants"
MAP_HEADERS_DIR = PROJECT_ROOT / "pokemon-game-data/data/maps/headers"

# Object types
OBJECT_TYPE_BG = "sign"
OBJECT_TYPE_OBJECT = "npc"
OBJECT_TYPE_ITEM = "item"


def create_database():
    """Create SQLite database and objects table"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Drop existing objects table if it exists
    cursor.execute("DROP TABLE IF EXISTS objects")

    # Create objects table
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS objects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        map_id INTEGER,
        object_type TEXT NOT NULL,
        x INTEGER,
        y INTEGER,
        local_x INTEGER,
        local_y INTEGER,
        spriteset_id INTEGER,
        sprite_name TEXT,
        text TEXT,
        action_type TEXT,
        action_direction TEXT,
        item_id INTEGER,
        movement_type TEXT DEFAULT 'LAND',
        trainer_class TEXT,
        trainer_party_index INTEGER,
        FOREIGN KEY (map_id) REFERENCES maps (id),
        FOREIGN KEY (item_id) REFERENCES items (id)
    )
    """
    )

    conn.commit()
    return conn, cursor


def get_all_maps(cursor):
    """Get all maps from the database"""
    cursor.execute("SELECT id, name FROM maps")
    return {name: id for id, name in cursor.fetchall()}


def convert_camel_to_upper_underscore(name):
    """Convert CamelCase to UPPER_CASE_WITH_UNDERSCORES"""
    # Insert underscore between lowercase and uppercase (e.g. CamelCase -> Camel_Case)
    s1 = re.sub("([a-z])([A-Z])", r"\1_\2", name)
    # Insert underscore between upper and upper-lower (e.g. MapHeader -> Map_Header)
    s2 = re.sub("([A-Z])([A-Z][a-z])", r"\1_\2", s1)
    # Insert underscore between letters and digits (e.g. Route16 -> Route_16)
    s3 = re.sub("([a-zA-Z])([0-9])", r"\1_\2", s2)
    return s3.upper()


def get_map_id_for_map(map_name, cursor):
    """Get map ID for a map from the maps table"""
    # Try exact match first
    cursor.execute("SELECT id FROM maps WHERE name = ?", (map_name,))
    result = cursor.fetchone()
    if result:
        return result[0]

    # Try case-insensitive match
    cursor.execute("SELECT id FROM maps WHERE LOWER(name) = LOWER(?)", (map_name,))
    result = cursor.fetchone()
    if result:
        return result[0]

    # Convert CamelCase to UPPER_CASE_WITH_UNDERSCORES
    upper_with_underscores = convert_camel_to_upper_underscore(map_name)
    cursor.execute("SELECT id FROM maps WHERE name = ?", (upper_with_underscores,))
    result = cursor.fetchone()
    if result:
        return result[0]

    return None


def parse_map_name_from_file(file_path):
    """Extract map name from file path"""
    file_name = os.path.basename(file_path)
    map_name = os.path.splitext(file_name)[0]
    return map_name


def parse_bg_events(content, map_name):
    """Parse background events (signs) from the map object file"""
    signs = []

    # Find the bg events section
    bg_section_match = re.search(
        r"def_bg_events(.*?)(?:def_object_events|\Z)", content, re.DOTALL
    )
    if not bg_section_match:
        return signs

    bg_section = bg_section_match.group(1)

    # Extract individual bg events
    bg_pattern = r"bg_event\s+(\d+),\s+(\d+),\s+(\w+)"
    bg_matches = re.finditer(bg_pattern, bg_section)

    for i, match in enumerate(bg_matches):
        x = int(match.group(1))
        y = int(match.group(2))
        text_id = match.group(3)

        # Determine if we should show a sprite for this sign
        # Building signs and informational signposts are already visible in the background tiles
        sprite_name = "SPRITE_SIGN"
        if any(
            keyword in text_id
            for keyword in [
                "MART_SIGN",
                "POKECENTER_SIGN",
                "GYM_SIGN",
                "TRAINER_TIPS",
                "CITY_SIGN",
                "TOWN_SIGN",
                "ACADEMY_SIGN",
                "POKEDEX_FAN_CLUB_SIGN",
            ]
        ):
            sprite_name = None
        # Also generally hide things just named "_SIGN" since they are tiles
        elif text_id.endswith("_SIGN"):
            sprite_name = None

        signs.append(
            {
                "name": f"{map_name}_SIGN_{i+1}",
                "object_type": OBJECT_TYPE_BG,
                "x": None,  # Global x will be populated later
                "y": None,  # Global y will be populated later
                "local_x": x,
                "local_y": y,
                "text": text_id,
                "sprite_name": sprite_name,
            }
        )

    return signs


def get_all_items(cursor):
    """Get all items from the database and create a mapping between item constants and item IDs"""
    cursor.execute("SELECT id, name, short_name FROM items")
    items_by_short_name = {}

    for id, name, short_name in cursor.fetchall():
        if short_name:
            items_by_short_name[short_name] = id

    # Add TM and HM mappings
    cursor.execute(
        "SELECT id, short_name FROM items WHERE short_name LIKE 'TM%' OR short_name LIKE 'HM%'"
    )
    for id, short_name in cursor.fetchall():
        # Extract the move name from TM_MOVE_NAME format
        if "_" in short_name:
            parts = short_name.split("_", 1)
            if len(parts) > 1:
                tm_type, move_name = parts
                # Map both formats: TM_MOVE_NAME and TM_MOVE
                items_by_short_name[short_name] = id

                # Also add the TM/HM prefix without the move name
                # This handles cases where the map file just references "TM_SUBMISSION" etc.
                if tm_type in ("TM", "HM"):
                    items_by_short_name[tm_type + "_" + move_name] = id

    return items_by_short_name


def parse_object_events(content, map_name, cursor):
    """Parse object events (NPCs, items) from the map object file"""
    objects = []

    # Get all items from the database
    items = get_all_items(cursor)

    # Find the object events section
    object_section_match = re.search(
        r"def_object_events(.*?)(?:def_warps_to|\Z)", content, re.DOTALL
    )
    if not object_section_match:
        return objects

    object_section = object_section_match.group(1)

    # Extract individual object events
    object_pattern = r"object_event\s+(\d+),\s+(\d+),\s+(\w+),\s+(\w+),\s+(\w+),\s+(\w+)(?:,\s+(\w+)(?:,\s+(\w+))?)?"
    object_matches = re.finditer(object_pattern, object_section)

    for i, match in enumerate(object_matches):
        x = int(match.group(1))
        y = int(match.group(2))
        sprite = match.group(3)
        action_type = match.group(4)
        action_direction = match.group(5)
        text_id = match.group(6)

        # Check for additional parameters (item or trainer info)
        item_or_trainer = (
            match.group(7) if len(match.groups()) >= 7 and match.group(7) else None
        )
        trainer_level = (
            match.group(8) if len(match.groups()) >= 8 and match.group(8) else None
        )

        # Determine if this is an item, trainer, or regular NPC
        object_type = OBJECT_TYPE_OBJECT
        item_id = None
        trainer_class = None
        trainer_party_index = None

        # Check if this is a trainer NPC (OPP_ prefix in the 7th parameter)
        if item_or_trainer and item_or_trainer.startswith("OPP_"):
            trainer_class = item_or_trainer[4:]  # Strip OPP_ prefix
            if trainer_level and trainer_level.isdigit():
                trainer_party_index = int(trainer_level)
        # If it's a Poké Ball sprite, it's likely an item
        elif sprite == "SPRITE_POKE_BALL" and item_or_trainer:
            object_type = OBJECT_TYPE_ITEM
            # Look up the item ID from the items table using the constant name
            item_id = items.get(item_or_trainer)
            # If no match, leave item_id as null
        # Check for other item sprites
        elif (
            "ITEM" in sprite
            or "BALL" in sprite
            or "POTION" in sprite
            or "FOSSIL" in sprite
        ):
            object_type = OBJECT_TYPE_ITEM
            # Try to extract item ID from sprite name if possible
            item_match = re.search(r"ITEM_(\d+)", sprite)
            if item_match:
                item_id = int(item_match.group(1))

        # Determine movement type based on sprite
        movement_type = "LAND"
        if sprite in [
            "SPRITE_SEEL",
            "SPRITE_LAPRAS",
            "SPRITE_SWIMMER",
            "SPRITE_WATER_NPC",
        ]:
            movement_type = "WATER"
        elif sprite == "SPRITE_PLAYER":  # Players can do both (eventually)
            movement_type = "BOTH"

        # Normalize direction
        direction = action_direction
        if direction == "NONE" or "MOVEMENT_BYTE" in direction:
            direction = "DOWN"

        objects.append(
            {
                "name": f"{map_name}_{'ITEM' if object_type == OBJECT_TYPE_ITEM else 'NPC'}_{i+1}",
                "object_type": object_type,
                "x": None,  # Global x will be populated later
                "y": None,  # Global y will be populated later
                "local_x": x,
                "local_y": y,
                "spriteset_id": None,  # Not implemented yet
                "sprite_name": sprite,
                "text": text_id,
                "action_type": action_type,
                "action_direction": direction,
                "item_id": item_id,
                "movement_type": movement_type,
                "trainer_class": trainer_class,
                "trainer_party_index": trainer_party_index,
            }
        )

    return objects


def process_map_file(file_path, cursor):
    """Process a single map object file and extract all objects"""
    map_name = parse_map_name_from_file(file_path)

    # Get map ID for this map
    map_id = get_map_id_for_map(map_name, cursor)
    if not map_id:
        print(f"Warning: Could not find map ID for map {map_name}")
        return []

    with open(file_path, "r") as f:
        content = f.read()

    # Parse different types of objects
    signs = parse_bg_events(content, map_name)
    objects = parse_object_events(content, map_name, cursor)

    # Combine all objects and add map_id
    all_objects = signs + objects
    for obj in all_objects:
        obj["map_id"] = map_id

    return all_objects


def main():
    # Create database
    conn, cursor = create_database()

    # Get all map object files
    map_files = list(POKEMON_DATA_DIR.glob("*.asm"))
    print(f"Found {len(map_files)} map files")

    # Process each map file
    all_objects = []
    processed_count = 0

    for file_path in map_files:
        objects = process_map_file(file_path, cursor)
        all_objects.extend(objects)
        processed_count += 1

    print(f"Processed {processed_count} map files, found {len(all_objects)} objects")

    # Insert objects into database
    signs_count = 0
    sprites_count = 0

    for obj in all_objects:
        cursor.execute(
            """
        INSERT INTO objects (
            name, map_id, object_type, x, y, local_x, local_y,
            spriteset_id, sprite_name, text, action_type, action_direction, item_id, movement_type,
            trainer_class, trainer_party_index
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                obj.get("name"),
                obj.get("map_id"),
                obj.get("object_type"),
                obj.get("x"),
                obj.get("y"),
                obj.get("local_x"),
                obj.get("local_y"),
                obj.get("spriteset_id"),
                obj.get("sprite_name"),
                obj.get("text"),
                obj.get("action_type"),
                obj.get("action_direction"),
                obj.get("item_id"),
                obj.get("movement_type"),
                obj.get("trainer_class"),
                obj.get("trainer_party_index"),
            ),
        )

        if obj.get("object_type") == "sign":
            signs_count += 1
        else:
            sprites_count += 1

    # Commit changes and close connection
    conn.commit()
    conn.close()

    print(
        f"Successfully exported {len(all_objects)} objects to pokemon.db ({signs_count} signs, {sprites_count} sprites)"
    )
    print(
        "Note: Run update_object_coordinates.py to update global coordinates (x, y) based on local coordinates (local_x, local_y)"
    )


if __name__ == "__main__":
    main()
