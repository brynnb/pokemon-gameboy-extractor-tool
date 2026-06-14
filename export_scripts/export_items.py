#!/usr/bin/env python3
import os
import re
import sqlite3

from config import CONSTANTS_DIR, DB_PATH, ITEMS_DATA_DIR, MOVES_DATA_DIR

POKEMON_DATA_DIR = ITEMS_DATA_DIR


def create_database():
    """Create SQLite database and tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Drop existing items table if it exists
    cursor.execute("DROP TABLE IF EXISTS items")

    # Create items table
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        short_name TEXT NOT NULL,
        price INTEGER,
        is_usable INTEGER NOT NULL DEFAULT 0,
        uses_party_menu INTEGER NOT NULL DEFAULT 0,
        vending_price INTEGER,
        move_id INTEGER,
        is_guard_drink INTEGER NOT NULL DEFAULT 0,
        is_key_item INTEGER NOT NULL DEFAULT 0
    )
    """
    )

    conn.commit()
    return conn, cursor


def parse_item_constants():
    """Parse item constants to get item IDs and short names"""
    item_constants_path = CONSTANTS_DIR / "item_constants.asm"

    with open(item_constants_path, "r") as f:
        content = f.read()

    # Extract item constants
    item_constants = {}
    pattern = r"const\s+(\w+)\s*;\s*\$([0-9A-F]+)"
    matches = re.finditer(pattern, content)

    for match in matches:
        short_name = match.group(1)
        item_id = int(match.group(2), 16)
        item_constants[short_name] = item_id

    return item_constants


def parse_item_names():
    """Parse item names from names.asm"""
    names_path = POKEMON_DATA_DIR / "names.asm"

    with open(names_path, "r") as f:
        content = f.read()

    # Extract item names
    item_names = []

    # Find the position of the first assert_list_length NUM_ITEMS
    assert_pos = content.find("assert_list_length NUM_ITEMS")
    if assert_pos != -1:
        # Only parse content up to the assert statement
        content_to_parse = content[:assert_pos]
    else:
        content_to_parse = content

    pattern = r'li\s+"([^"]+)"'
    matches = re.finditer(pattern, content_to_parse)

    for match in matches:
        item_name = match.group(1)
        item_names.append(item_name)

    return item_names


def parse_item_prices():
    """Parse item prices from prices.asm"""
    prices_path = POKEMON_DATA_DIR / "prices.asm"

    with open(prices_path, "r") as f:
        content = f.read()

    # Extract item prices
    item_prices = []
    pattern = r"bcd3\s+(\d+)"
    matches = re.finditer(pattern, content)

    for match in matches:
        price = int(match.group(1))
        item_prices.append(price)

    return item_prices


def parse_key_items():
    """Parse key items from key_items.asm"""
    key_items_path = POKEMON_DATA_DIR / "key_items.asm"

    with open(key_items_path, "r") as f:
        content = f.read()

    # Extract key items
    key_items = []
    pattern = r"dbit\s+(TRUE|FALSE)\s*;\s*(\w+)"
    matches = re.finditer(pattern, content)

    for match in matches:
        is_key = match.group(1) == "TRUE"
        item_name = match.group(2)
        key_items.append((item_name, is_key))

    return key_items


def parse_party_menu_items():
    """Parse items that use party menu from use_party.asm"""
    party_menu_path = POKEMON_DATA_DIR / "use_party.asm"

    with open(party_menu_path, "r") as f:
        content = f.read()

    # Extract party menu items
    party_menu_items = []
    pattern = r"db\s+(\w+)"
    matches = re.finditer(pattern, content)

    for match in matches:
        item_name = match.group(1)
        if item_name != "-1":  # Skip the end marker
            party_menu_items.append(item_name)

    return party_menu_items


def parse_overworld_items():
    """Parse items usable in overworld from use_overworld.asm"""
    overworld_path = POKEMON_DATA_DIR / "use_overworld.asm"

    with open(overworld_path, "r") as f:
        content = f.read()

    # Extract overworld items
    overworld_items = []
    pattern = r"db\s+(\w+)"
    matches = re.finditer(pattern, content)

    for match in matches:
        item_name = match.group(1)
        if item_name != "-1":  # Skip the end marker
            overworld_items.append(item_name)

    return overworld_items


def parse_guard_drink_items():
    """Parse guard drink items from guard_drink_items.asm"""
    guard_drink_path = POKEMON_DATA_DIR / "guard_drink_items.asm"

    with open(guard_drink_path, "r") as f:
        content = f.read()

    # Extract guard drink items
    guard_drink_items = []
    pattern = r"db\s+(\w+)"
    matches = re.finditer(pattern, content)

    for match in matches:
        item_name = match.group(1)
        if item_name != "0":  # Skip the end marker
            guard_drink_items.append(item_name)

    return guard_drink_items


def parse_vending_prices():
    """Parse vending prices from vending_prices.asm"""
    vending_path = POKEMON_DATA_DIR / "vending_prices.asm"

    with open(vending_path, "r") as f:
        content = f.read()

    # Extract vending prices
    vending_prices = {}
    pattern = r"vend_item\s+(\w+),\s+(\d+)"
    matches = re.finditer(pattern, content)

    for match in matches:
        item_name = match.group(1)
        price = int(match.group(2))
        vending_prices[item_name] = price

    return vending_prices


def parse_tm_hm_moves():
    """Parse TM/HM move IDs from item_constants.asm and move_constants.asm"""
    tm_hm_moves = {}

    # Read item_constants.asm to get TM/HM move mappings
    item_constants_path = CONSTANTS_DIR / "item_constants.asm"

    with open(item_constants_path, "r") as f:
        content = f.read()

    # Extract TM move mappings
    # Format: add_tm MOVE_NAME (creates TM_MOVE_NAME constant and TM##_MOVE = MOVE_NAME)
    tm_pattern = r"add_tm\s+(\w+)"
    tm_matches = re.finditer(tm_pattern, content)

    tm_moves = []
    for match in tm_matches:
        move_name = match.group(1)
        tm_moves.append(move_name)

    print(f"Found {len(tm_moves)} TM moves")

    # TMs start at item ID 0xC9 (201)
    tm_count = 0
    for i, move_name in enumerate(tm_moves):
        item_id = 0xC9 + i
        move_id = get_move_id_by_name(move_name)
        if move_id:
            tm_hm_moves[item_id] = move_id
            tm_count += 1

    # Extract HM move mappings
    # Format: add_hm MOVE_NAME (creates HM_MOVE_NAME constant and HM##_MOVE = MOVE_NAME)
    hm_pattern = r"add_hm\s+(\w+)"
    hm_matches = re.finditer(hm_pattern, content)

    hm_moves = []
    for match in hm_matches:
        move_name = match.group(1)
        hm_moves.append(move_name)

    print(f"Found {len(hm_moves)} HM moves")

    # HMs start at item ID 0xC4 (196)
    hm_count = 0
    for i, move_name in enumerate(hm_moves):
        item_id = 0xC4 + i
        move_id = get_move_id_by_name(move_name)
        if move_id:
            tm_hm_moves[item_id] = move_id
            hm_count += 1

    return tm_hm_moves


def get_move_id_by_name(move_name):
    """Get move ID by name from move_constants.asm"""
    move_constants_path = CONSTANTS_DIR / "move_constants.asm"

    with open(move_constants_path, "r") as f:
        content = f.read()

    # Find the move constant and get its ID
    # In move_constants.asm, moves are defined as:
    # const MOVE_NAME ; XX (where XX is the hex ID)
    pattern = r"const\s+(" + re.escape(move_name) + r")\s*;\s*([0-9a-fA-F]+)"
    match = re.search(pattern, content)

    if match:
        # Convert hex value to decimal
        move_id = int(match.group(2), 16)
        return move_id

    # If not found directly, try with different formats
    # Some moves have special names like PSYCHIC_M instead of PSYCHIC
    special_cases = {
        "PSYCHIC": "PSYCHIC_M",
    }

    if move_name in special_cases:
        alt_name = special_cases[move_name]
        pattern = r"const\s+(" + re.escape(alt_name) + r")\s*;\s*([0-9a-fA-F]+)"
        match = re.search(pattern, content)
        if match:
            move_id = int(match.group(2), 16)
            return move_id

    # Return None if move not found (no warning)
    return None


def is_item_usable(item_name, overworld_items, party_menu_items):
    """Determine if an item is usable based on overworld and party menu lists"""
    return item_name in overworld_items or item_name in party_menu_items


def main():
    # Create database
    conn, cursor = create_database()

    # Parse data
    item_constants = parse_item_constants()
    item_names = parse_item_names()
    item_prices = parse_item_prices()
    key_items_data = parse_key_items()
    party_menu_items = parse_party_menu_items()
    overworld_items = parse_overworld_items()
    guard_drink_items = parse_guard_drink_items()
    vending_prices = parse_vending_prices()
    tm_hm_moves = parse_tm_hm_moves()

    # Create reverse mapping for item constants
    item_id_to_name = {v: k for k, v in item_constants.items()}

    # Create mapping for key items
    key_item_map = {}
    for item_name, is_key in key_items_data:
        key_item_map[item_name] = is_key

    # Insert items into database
    item_count = 0
    for i, name in enumerate(item_names):
        item_id = i + 1  # Item IDs start at 1
        short_name = item_id_to_name.get(item_id, f"UNKNOWN_{item_id}")
        price = item_prices[i]

        # Convert 0 price to NULL
        price_value = None if price == 0 else price

        # Check if item is usable
        is_usable = is_item_usable(short_name, overworld_items, party_menu_items)

        # Check if item uses party menu
        uses_party_menu = short_name in party_menu_items

        # Check if item is a guard drink
        is_guard_drink = short_name in guard_drink_items

        # Check if item is a key item
        is_key_item = key_item_map.get(short_name, False)

        # Get vending price if available
        vending_price = vending_prices.get(short_name)

        # Get move ID if it's a TM/HM
        move_id = tm_hm_moves.get(item_id)

        # Insert into database
        cursor.execute(
            """
        INSERT INTO items (
            id, name, short_name, price, is_usable, uses_party_menu, 
            vending_price, move_id, is_guard_drink, is_key_item
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                item_id,
                name,
                short_name,
                price_value,
                1 if is_usable else 0,
                1 if uses_party_menu else 0,
                vending_price,
                move_id,
                1 if is_guard_drink else 0,
                1 if is_key_item else 0,
            ),
        )
        item_count += 1

    # Read move names for TM/HM items
    move_names = {}
    move_constants_path = CONSTANTS_DIR / "move_constants.asm"
    with open(move_constants_path, "r") as f:
        content = f.read()

    pattern = r"const\s+(\w+)\s*;\s*([0-9a-fA-F]+)"
    matches = re.finditer(pattern, content)
    for match in matches:
        move_name = match.group(1)
        move_id = int(match.group(2), 16)
        move_names[move_id] = move_name

    # Get the next available item ID
    cursor.execute("SELECT MAX(id) FROM items")
    max_id = cursor.fetchone()[0]
    next_id = max_id + 1

    # Add HM items (HM01-HM05)
    hm_count = 0
    for i in range(5):
        original_item_id = 0xC4 + i  # HMs start at 0xC4
        hm_number = i + 1
        move_id = tm_hm_moves.get(original_item_id)

        if move_id and move_id in move_names:
            move_name = move_names[move_id]
            item_name = f"HM{hm_number:02d}"
            short_name = f"HM_{move_name}"

            try:
                # Insert HM item into database with sequential ID
                cursor.execute(
                    """
                INSERT INTO items (
                    id, name, short_name, price, is_usable, uses_party_menu, 
                    move_id, is_guard_drink, is_key_item
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        next_id,
                        item_name,
                        short_name,
                        None,  # HMs don't have a price
                        1,  # HMs are usable
                        1,  # HMs use party menu
                        move_id,
                        0,  # Not a guard drink
                        1,  # HMs are key items
                    ),
                )
                hm_count += 1
                next_id += 1
            except sqlite3.Error as e:
                print(f"Error adding HM item {item_name}: {e}")

    # Add TM items (TM01-TM50)
    tm_count = 0
    for i in range(50):
        original_item_id = 0xC9 + i  # TMs start at 0xC9
        tm_number = i + 1
        move_id = tm_hm_moves.get(original_item_id)

        if move_id and move_id in move_names:
            move_name = move_names[move_id]
            item_name = f"TM{tm_number:02d}"
            short_name = f"TM_{move_name}"

            # TMs have a price (placeholder for now)
            price = 3000

            try:
                # Insert TM item into database with sequential ID
                cursor.execute(
                    """
                INSERT INTO items (
                    id, name, short_name, price, is_usable, uses_party_menu, 
                    move_id, is_guard_drink, is_key_item
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        next_id,
                        item_name,
                        short_name,
                        price,
                        1,  # TMs are usable
                        1,  # TMs use party menu
                        move_id,
                        0,  # Not a guard drink
                        0,  # TMs are not key items
                    ),
                )
                tm_count += 1
                next_id += 1
            except sqlite3.Error as e:
                print(f"Error adding TM item {item_name}: {e}")

    print(f"Added {hm_count} HM items and {tm_count} TM items to the database")

    # Commit changes and close connection
    conn.commit()
    conn.close()

    print(f"Successfully exported {item_count} items to pokemon.db")


if __name__ == "__main__":
    main()
