"""
Pokemon Map Exporter

This script exports map data from the Pokemon Red/Blue codebase to a SQLite database.
It follows the map data structure described in MAPLOGIC.md:

1. Maps are stored as .blk files, where each byte is an index into a blockset
2. Blocksets (.bst files) contain blocks, where each block is 16 bytes representing a 4x4 grid of tile indices
3. Tilesets (.png/.2bpp files) contain tiles, where each tile is 8x8 pixels with 2 bits per pixel

The export process:
1. Load map constants (dimensions) from constants/map_constants.asm
2. Load tileset constants from constants/tileset_constants.asm
3. Extract map headers from data/maps/headers/*.asm to determine which tileset each map uses
4. Extract map data from maps/*.blk
5. Extract tileset data from gfx/tilesets/*.png and gfx/blocksets/*.bst
6. Generate 2bpp files from PNG files if they don't exist
7. Parse blockset files to extract block data
8. Parse 2bpp files to extract tile data
9. Store all data in a SQLite database

Usage:
    python export_map.py                # Export map data to pokemon.db
"""

import os
import re
import sqlite3
import glob
import subprocess
from pathlib import Path
import binascii
import argparse
from PIL import Image, ImageDraw

# Constants
# Get the project root directory (parent of the script's directory)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "pokemon.db"
MAPS_DIR = PROJECT_ROOT / "pokemon-game-data/maps"
MAP_HEADERS_DIR = PROJECT_ROOT / "pokemon-game-data/data/maps/headers"
MAP_CONSTANTS_FILE = PROJECT_ROOT / "pokemon-game-data/constants/map_constants.asm"
BLOCKSETS_DIR = PROJECT_ROOT / "pokemon-game-data/gfx/blocksets"
TILESETS_DIR = PROJECT_ROOT / "pokemon-game-data/gfx/tilesets"
TILESET_CONSTANTS_FILE = (
    PROJECT_ROOT / "pokemon-game-data/constants/tileset_constants.asm"
)

# Regular expressions
MAP_CONST_PATTERN = re.compile(
    r"\s*map_const\s+(\w+),\s*(\d+),\s*(\d+)\s*;?\s*\$([0-9A-F]+)"
)
MAP_HEADER_PATTERN = re.compile(r"\s*map_header\s+(\w+),\s+(\w+),\s+(\w+),\s+(.+)")
TILESET_CONST_PATTERN = re.compile(r"\s*const\s+(\w+)(?:\s*;.*)?$")
CONNECTION_PATTERN = re.compile(r"\s*connection\s+(\w+),\s+(\w+),\s+(\w+),\s+(-?\d+)")


def create_database():
    """Create SQLite database and tables for map data"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Drop existing tables if they exist
    cursor.execute("DROP TABLE IF EXISTS maps")
    cursor.execute("DROP TABLE IF EXISTS tilesets")
    cursor.execute("DROP TABLE IF EXISTS map_connections")
    cursor.execute("DROP TABLE IF EXISTS blocksets")
    cursor.execute("DROP TABLE IF EXISTS tileset_tiles")
    cursor.execute("DROP TABLE IF EXISTS tiles_raw")
    cursor.execute("DROP TABLE IF EXISTS collision_tiles")

    # Create maps table
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS maps (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        width INTEGER NOT NULL,
        height INTEGER NOT NULL,
        tileset_id INTEGER,
        blk_data BLOB,
        north_connection INTEGER,
        south_connection INTEGER,
        west_connection INTEGER,
        east_connection INTEGER,
        is_overworld INTEGER NOT NULL DEFAULT 0
    )
    """
    )

    # Create tilesets table
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS tilesets (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        blockset_path TEXT,
        tileset_path TEXT
    )
    """
    )

    # Create blocksets table
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS blocksets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tileset_id INTEGER NOT NULL,
        block_index INTEGER NOT NULL,
        block_data BLOB NOT NULL,
        FOREIGN KEY (tileset_id) REFERENCES tilesets (id)
    )
    """
    )

    # Create map_connections table
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS map_connections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_map_id INTEGER NOT NULL,
        to_map_id INTEGER NOT NULL,
        direction TEXT NOT NULL,
        offset INTEGER,
        FOREIGN KEY (from_map_id) REFERENCES maps (id),
        FOREIGN KEY (to_map_id) REFERENCES maps (id)
    )
    """
    )

    # Create tileset_tiles table
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS tileset_tiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tileset_id INTEGER NOT NULL,
        tile_index INTEGER NOT NULL,
        tile_data BLOB NOT NULL,
        FOREIGN KEY (tileset_id) REFERENCES tilesets (id)
    )
    """
    )

    # Create collision_tiles table to store collision data from the original game
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS collision_tiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tileset_id INTEGER NOT NULL,
        tileset_name TEXT NOT NULL,
        tile_id INTEGER NOT NULL,
        FOREIGN KEY (tileset_id) REFERENCES tilesets (id)
    )
    """
    )

    # Create tiles_raw table to store raw tile data before processing
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS tiles_raw (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        map_id INTEGER NOT NULL,
        x INTEGER NOT NULL,
        y INTEGER NOT NULL,
        block_index INTEGER NOT NULL,
        tileset_id INTEGER NOT NULL,
        is_overworld INTEGER NOT NULL DEFAULT 0,
        is_walkable INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY (map_id) REFERENCES maps (id),
        FOREIGN KEY (tileset_id) REFERENCES tilesets (id)
    )
    """
    )

    conn.commit()
    return conn


def load_map_constants():
    """Load map constants from the constants file"""
    map_constants = {}

    with open(MAP_CONSTANTS_FILE, "r") as f:
        content = f.read()

    matches = MAP_CONST_PATTERN.finditer(content)
    for match in matches:
        name = match.group(1)
        width = int(match.group(2))
        height = int(match.group(3))
        map_id = int(match.group(4), 16) if match.group(4) else None
        if map_id is None:
            # If no hex ID is provided, use the order in the file
            map_id = len(map_constants)

        map_constants[name] = {
            "id": map_id,
            "name": name,
            "width": width,
            "height": height,
        }

    print(f"Loaded {len(map_constants)} map constants")
    return map_constants


def load_tileset_constants():
    """Load tileset constants from the constants file"""
    tileset_constants = {}

    with open(TILESET_CONSTANTS_FILE, "r") as f:
        content = f.read()

    # Extract tileset constants manually
    lines = content.split("\n")
    start_id = 0
    in_tileset_section = False

    for i, line in enumerate(lines):
        if "; tileset ids" in line:
            in_tileset_section = True
            continue

        if in_tileset_section:
            if "const_def" in line:
                # Extract starting ID if specified
                const_def_match = re.search(r"const_def\s+(\d+)", line)
                if const_def_match:
                    start_id = int(const_def_match.group(1))
                continue

            # Match const TILESET_NAME
            const_match = re.search(r"\s*const\s+(\w+)", line)
            if const_match:
                name = const_match.group(1)
                tileset_constants[name] = {"id": start_id, "name": name}
                start_id += 1

            # Stop when we reach a blank line or a new section
            if line.strip() == "" or (
                line.strip()
                and line.strip()[0] == ";"
                and "tileset" not in line.lower()
            ):
                if (
                    len(tileset_constants) > 0
                ):  # Only break if we've found some constants
                    break

    print(f"Loaded {len(tileset_constants)} tileset constants")

    # Debug: Print all tileset constants
    print("Tileset constants:")
    for name, info in tileset_constants.items():
        print(f"  {name}: {info['id']}")

    return tileset_constants


def extract_map_headers():
    """Extract map headers from the header files"""
    map_headers = {}
    map_to_constant = {}
    map_connections = []

    header_files = glob.glob(f"{MAP_HEADERS_DIR}/*.asm")
    for header_file in header_files:
        with open(header_file, "r") as f:
            content = f.read()

        # Extract the map_header directive
        lines = content.strip().split("\n")
        map_name = None
        map_id = None

        for line in lines:
            line = line.strip()

            # Extract map header
            match = MAP_HEADER_PATTERN.search(line)
            if match:
                map_name = match.group(1)
                map_id = match.group(2)
                tileset = match.group(3)
                connections = match.group(4)

                # Parse connections
                north_conn = "NORTH" in connections
                south_conn = "SOUTH" in connections
                west_conn = "WEST" in connections
                east_conn = "EAST" in connections

                map_headers[map_name] = {
                    "map_id": map_id,
                    "tileset": tileset,
                    "north_connection": north_conn,
                    "south_connection": south_conn,
                    "west_connection": west_conn,
                    "east_connection": east_conn,
                }

                # Map the map name to its constant
                map_to_constant[map_name] = map_id
                continue

            # Extract connections
            conn_match = CONNECTION_PATTERN.search(line)
            if conn_match and map_name and map_id:
                direction = conn_match.group(1)
                connected_map_name = conn_match.group(2)
                connected_map_id = conn_match.group(3)
                offset = int(conn_match.group(4))

                map_connections.append(
                    {
                        "from_map_name": map_name,
                        "from_map_id": map_id,
                        "to_map_name": connected_map_name,
                        "to_map_id": connected_map_id,
                        "direction": direction,
                        "offset": offset,
                    }
                )

    print(f"Loaded {len(map_headers)} map headers")
    print(f"Loaded {len(map_connections)} map connections")

    # Debug: Print some map headers with their tileset names
    print("Sample map headers with tileset names:")
    count = 0
    for name, info in map_headers.items():
        if count < 5:
            print(f"  {name}: {info['tileset']}")
            count += 1

    # Debug: Print some map connections
    print("Sample map connections:")
    for i, conn in enumerate(map_connections[:5]):
        print(
            f"  {conn['from_map_name']} ({conn['from_map_id']}) -> {conn['to_map_name']} ({conn['to_map_id']}) [{conn['direction']}]"
        )

    return map_headers, map_to_constant, map_connections


def extract_map_data():
    """Extract map data from .blk files"""
    map_data = {}

    blk_files = glob.glob(f"{MAPS_DIR}/*.blk")
    for blk_file in blk_files:
        map_name = os.path.basename(blk_file).replace(".blk", "")

        with open(blk_file, "rb") as f:
            blk_data = f.read()

        # Store the original blk_data
        map_data[map_name] = {"blk_data": blk_data}

    print(f"Loaded {len(map_data)} map data files")
    return map_data


def is_overworld_map(map_name, map_headers):
    """Determine if a map is an overworld map based on its tileset"""
    # Overworld maps use tileset 0 (OVERWORLD)
    for header_name, header_info in map_headers.items():
        if (
            header_name.lower() == map_name.lower()
            or header_name.lower().replace("_", "") == map_name.lower()
        ):
            return header_info.get("tileset_id") == 0
    return False


def ensure_2bpp_files_exist():
    """Check for and generate 2bpp files from PNG files if they don't exist"""
    png_files = glob.glob(f"{TILESETS_DIR}/*.png")
    generated_count = 0

    for png_file in png_files:
        base_name = os.path.basename(png_file).replace(".png", "")
        bpp_file = f"{TILESETS_DIR}/{base_name}.2bpp"

        # Check if 2bpp file exists and is newer than the PNG file
        if not os.path.exists(bpp_file) or os.path.getmtime(
            png_file
        ) > os.path.getmtime(bpp_file):
            try:
                print(f"Generating 2bpp file for {base_name}...")
                result = subprocess.run(
                    ["rgbgfx", "-o", bpp_file, png_file],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                generated_count += 1
            except subprocess.CalledProcessError as e:
                print(f"Error generating 2bpp file for {base_name}: {e}")
                print(f"stdout: {e.stdout}")
                print(f"stderr: {e.stderr}")
            except FileNotFoundError:
                print("rgbgfx tool not found. Please install RGBDS tools.")
                break

    print(f"Generated {generated_count} 2bpp files")
    return generated_count


def extract_tileset_data():
    """Extract tileset data"""
    tileset_data = {}

    # Get all blockset files
    blockset_files = glob.glob(f"{BLOCKSETS_DIR}/*.bst")
    for blockset_file in blockset_files:
        tileset_name = os.path.basename(blockset_file).replace(".bst", "")

        # Find corresponding tileset files
        tileset_png = f"{TILESETS_DIR}/{tileset_name}.png"
        tileset_2bpp = f"{TILESETS_DIR}/{tileset_name}.2bpp"

        has_png = os.path.exists(tileset_png)
        has_2bpp = os.path.exists(tileset_2bpp)

        tileset_data[tileset_name.upper()] = {
            "blockset_path": blockset_file,
            "tileset_png_path": tileset_png if has_png else None,
            "tileset_2bpp_path": tileset_2bpp if has_2bpp else None,
        }

    print(f"Loaded {len(tileset_data)} tileset data files")
    return tileset_data


def find_matching_blk_file(map_name, map_data):
    """Find a matching .blk file for a map name using various transformations"""
    # Try direct match
    if map_name in map_data:
        return map_name

    # Try without underscores
    no_underscores = map_name.replace("_", "")
    if no_underscores in map_data:
        return no_underscores

    # Try case-insensitive match
    for blk_name in map_data.keys():
        if (
            blk_name.lower() == map_name.lower()
            or blk_name.lower() == no_underscores.lower()
        ):
            return blk_name

    # Try partial match (map name is part of the blk file name)
    for blk_name in map_data.keys():
        if (
            map_name.lower() in blk_name.lower()
            or no_underscores.lower() in blk_name.lower()
        ):
            return blk_name

    return None


def find_tileset_id(tileset_name, tileset_constants):
    """Find a tileset ID by name using various matching strategies"""
    if not tileset_name:
        return None

    # Direct match
    if tileset_name in tileset_constants:
        return tileset_constants[tileset_name]["id"]

    # Case-insensitive match
    for const_name, const_info in tileset_constants.items():
        if const_name.lower() == tileset_name.lower():
            return const_info["id"]

    # Partial match
    for const_name, const_info in tileset_constants.items():
        if (
            const_name.lower() in tileset_name.lower()
            or tileset_name.lower() in const_name.lower()
        ):
            return const_info["id"]

    return None


def parse_blockset_file(blockset_path):
    """Parse a blockset (.bst) file and return the block data

    Each block is 16 bytes, representing a 4x4 grid of tile indices.
    These are stored contiguously in the file.
    """
    blocks = []

    try:
        with open(blockset_path, "rb") as f:
            blockset_data = f.read()

        # Each block is 16 bytes (4x4 tile indices)
        block_size = 16
        num_blocks = len(blockset_data) // block_size

        for i in range(num_blocks):
            start_pos = i * block_size
            end_pos = start_pos + block_size
            block_data = blockset_data[start_pos:end_pos]
            blocks.append(block_data)

        return blocks
    except Exception as e:
        print(f"Error parsing blockset file {blockset_path}: {e}")
        return []


def parse_2bpp_file(file_path):
    """Parse a 2bpp file and return the tile data

    Each tile is 16 bytes (8x8 pixels, 2 bits per pixel).
    Pixels are spread across neighboring bytes.
    """
    tiles = []

    try:
        with open(file_path, "rb") as f:
            file_data = f.read()

        # Each tile is 16 bytes (8x8 pixels, 2 bits per pixel)
        tile_size = 16
        num_tiles = len(file_data) // tile_size

        for i in range(num_tiles):
            start_pos = i * tile_size
            end_pos = start_pos + tile_size
            tile_data = file_data[start_pos:end_pos]
            tiles.append(tile_data)

        return tiles
    except Exception as e:
        print(f"Error parsing 2bpp file {file_path}: {e}")
        return []


def decode_2bpp_tile(tile_data):
    """Decode a 2bpp tile into a 2D array of pixel values (0-3)

    Each tile is 8x8 pixels, with 2 bits per pixel.
    Pixels are spread across neighboring bytes.
    """
    pixels = []

    # Process 16 bytes (8 rows of 2 bytes each)
    for row in range(8):
        row_pixels = []
        # Each row is represented by 2 bytes
        byte1 = tile_data[row * 2]
        byte2 = tile_data[row * 2 + 1]

        # Process each bit in the bytes
        for bit in range(8):
            # Extract the bit from each byte (from MSB to LSB)
            bit_pos = 7 - bit
            bit1 = (byte1 >> bit_pos) & 1
            bit2 = (byte2 >> bit_pos) & 1

            # Combine the bits to get the pixel value (0-3)
            pixel_value = (bit2 << 1) | bit1
            row_pixels.append(pixel_value)

        pixels.append(row_pixels)

    return pixels


def render_map(map_name):
    """Render a map based on the data in the database

    Args:
        map_name: The name of the map to render

    Returns:
        PIL.Image: The rendered map image
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get map data
    cursor.execute(
        "SELECT id, width, height, tileset_id, blk_data FROM maps WHERE name = ?",
        (map_name,),
    )
    map_data = cursor.fetchone()

    if not map_data:
        print(f"Map {map_name} not found in database")
        return None

    map_id, width, height, tileset_id, blk_data = map_data

    if not blk_data:
        print(f"Map {map_name} has no block data")
        return None

    # Convert hex string to bytes
    blk_bytes = bytes.fromhex(blk_data)

    # Get blockset data for this tileset
    cursor.execute(
        "SELECT block_index, block_data FROM blocksets WHERE tileset_id = ? ORDER BY block_index",
        (tileset_id,),
    )
    blockset_rows = cursor.fetchall()

    if not blockset_rows:
        print(f"No blockset data found for tileset {tileset_id}")
        return None

    # Create a dictionary of block_index -> block_data
    blocks = {row[0]: row[1] for row in blockset_rows}

    # Get tileset tiles
    cursor.execute(
        "SELECT tile_index, tile_data FROM tileset_tiles WHERE tileset_id = ? ORDER BY tile_index",
        (tileset_id,),
    )
    tile_rows = cursor.fetchall()

    if not tile_rows:
        print(f"No tile data found for tileset {tileset_id}")
        return None

    # Create a dictionary of tile_index -> tile_data
    tiles = {row[0]: row[1] for row in tile_rows}

    # Define GameBoy color palette (white, light gray, dark gray, black)
    palette = [(255, 255, 255), (192, 192, 192), (96, 96, 96), (0, 0, 0)]

    # Calculate image dimensions
    # Each block is 2x2 squares, each square is 16x16 pixels
    # Use a scale factor to make the image larger and sharper
    scale = 2  # Scale factor for better visibility
    img_width = width * 32 * scale  # 16 pixels per tile * 2 tiles per block * scale
    img_height = height * 32 * scale  # 16 pixels per tile * 2 tiles per block * scale

    # Create a new image
    img = Image.new("RGB", (img_width, img_height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    # Render each block in the map
    for y in range(height):
        for x in range(width):
            # Get the block index from the map data
            block_index = blk_bytes[y * width + x]

            # Get the block data
            block_data = blocks.get(block_index)
            if not block_data:
                print(f"Block {block_index} not found in blockset")
                continue

            # Each block is 4x4 tiles (2x2 squares, each square is 2x2 tiles)
            for block_y in range(4):
                for block_x in range(4):
                    # Get the tile index from the block data
                    tile_index = block_data[block_y * 4 + block_x]

                    # Get the tile data
                    tile_data = tiles.get(tile_index)
                    if not tile_data:
                        print(f"Tile {tile_index} not found in tileset")
                        continue

                    # Decode the tile data
                    tile_pixels = decode_2bpp_tile(tile_data)

                    # Calculate the position of this tile in the image
                    # Each tile is 8x8 pixels
                    # Each block is 4x4 tiles (32x32 pixels)
                    tile_x = (x * 32 + block_x * 8) * scale
                    tile_y = (y * 32 + block_y * 8) * scale

                    # Draw the tile with scaling
                    for py in range(8):
                        for px in range(8):
                            pixel_value = tile_pixels[py][px]
                            pixel_color = palette[pixel_value]

                            # Draw a scaled pixel (as a rectangle)
                            for sy in range(scale):
                                for sx in range(scale):
                                    draw.point(
                                        (
                                            tile_x + px * scale + sx,
                                            tile_y + py * scale + sy,
                                        ),
                                        fill=pixel_color,
                                    )

    return img


def load_collision_data(conn):
    """Load collision data from the original game's collision_tile_ids.asm file"""
    cursor = conn.cursor()

    # Clear existing collision data
    cursor.execute("DELETE FROM collision_tiles")

    # Define the mapping between collision data names and tileset IDs
    collision_to_tileset = {
        "Underground_Coll": {"id": 11, "name": "UNDERGROUND"},
        "Overworld_Coll": {"id": 0, "name": "OVERWORLD"},
        "RedsHouse1_Coll": {"id": 1, "name": "REDS_HOUSE_1"},
        "RedsHouse2_Coll": {"id": 4, "name": "REDS_HOUSE_2"},
        "Mart_Coll": {"id": 2, "name": "MART"},
        "Pokecenter_Coll": {"id": 6, "name": "POKECENTER"},
        "Dojo_Coll": {"id": 5, "name": "DOJO"},
        "Gym_Coll": {"id": 7, "name": "GYM"},
        "Forest_Coll": {"id": 3, "name": "FOREST"},
        "House_Coll": {"id": 8, "name": "HOUSE"},
        "ForestGate_Coll": {"id": 9, "name": "FOREST_GATE"},
        "Museum_Coll": {"id": 10, "name": "MUSEUM"},
        "Gate_Coll": {"id": 12, "name": "GATE"},
        "Ship_Coll": {"id": 13, "name": "SHIP"},
        "ShipPort_Coll": {"id": 14, "name": "SHIP_PORT"},
        "Cemetery_Coll": {"id": 15, "name": "CEMETERY"},
        "Interior_Coll": {"id": 16, "name": "INTERIOR"},
        "Cavern_Coll": {"id": 17, "name": "CAVERN"},
        "Lobby_Coll": {"id": 18, "name": "LOBBY"},
        "Mansion_Coll": {"id": 19, "name": "MANSION"},
        "Lab_Coll": {"id": 20, "name": "LAB"},
        "Club_Coll": {"id": 21, "name": "CLUB"},
        "Facility_Coll": {"id": 22, "name": "FACILITY"},
        "Plateau_Coll": {"id": 23, "name": "PLATEAU"},
    }

    # Parse the collision data from the original game's collision_tile_ids.asm file
    collision_file_path = (
        PROJECT_ROOT / "pokemon-game-data/data/tilesets/collision_tile_ids.asm"
    )

    if not os.path.exists(collision_file_path):
        print(f"Warning: Collision data file not found at {collision_file_path}")
        return conn

    try:
        with open(collision_file_path, "r") as f:
            content = f.read()

        # Parse the collision data
        # Note: Some labels share the same coll_tiles line, e.g.:
        #   Mart_Coll::
        #   Pokecenter_Coll::
        #       coll_tiles $11, $1a, ...
        # We track ALL pending labels and assign the coll_tiles data to each.
        collision_data = {}
        pending_labels = []

        # Split the file into lines
        lines = content.split("\n")
        for line in lines:
            line = line.strip()

            # Check if this is a collision data label
            if line.endswith("_Coll::"):
                label = line.replace("::", "")
                collision_data[label] = []
                pending_labels.append(label)

            # Check if this is a coll_tiles line
            elif line.startswith("coll_tiles") and pending_labels:
                # Extract the tile IDs
                tile_ids_part = line.replace("coll_tiles", "").strip()

                # Skip empty lines or lines with just a comment
                if not tile_ids_part or tile_ids_part == ";":
                    pending_labels = []
                    continue

                # Extract the hex values
                hex_values = re.findall(r"\$([0-9A-Fa-f]+)", tile_ids_part)

                # Convert hex values to integers
                tile_ids = [int(hex_val, 16) for hex_val in hex_values]

                # Assign to ALL pending labels (handles shared labels)
                for label in pending_labels:
                    collision_data[label] = tile_ids

                pending_labels = []

        # Insert the parsed collision data into the database
        for coll_name, tile_ids in collision_data.items():
            if coll_name in collision_to_tileset:
                tileset_info = collision_to_tileset[coll_name]
                tileset_id = tileset_info["id"]
                tileset_name = tileset_info["name"]

                for tile_id in tile_ids:
                    cursor.execute(
                        """
                        INSERT INTO collision_tiles (tileset_id, tileset_name, tile_id)
                        VALUES (?, ?, ?)
                        """,
                        (tileset_id, tileset_name, tile_id),
                    )

        conn.commit()
        print(f"Loaded collision data for {len(collision_data)} tilesets")

    except Exception as e:
        print(f"Error parsing collision data: {e}")

    return conn


def is_block_walkable(block_index, tileset_id, conn):
    """Determine if a block is walkable based on the original Pokémon game's collision data.

    In the original game, each tileset has a list of block indices (coll_tiles) that the
    player CAN walk on. These are stored in the collision_tiles table.

    Args:
        block_index: The block index to check
        tileset_id: The tileset ID
        conn: Database connection

    Returns:
        bool: True if the block is walkable, False otherwise
    """
    cursor = conn.cursor()

    # Check if the block_index is in the collision_tiles table for this tileset
    cursor.execute(
        """
        SELECT COUNT(*) FROM collision_tiles 
        WHERE tileset_id = ? AND tile_id = ?
        """,
        (tileset_id, block_index),
    )

    count = cursor.fetchone()[0]

    # If the block is in the collision_tiles table, it IS walkable
    if count > 0:
        return True

    # Not in the walkable list — not walkable
    return False


def main():
    # Ensure 2bpp files exist
    ensure_2bpp_files_exist()

    # Create database
    db_conn = create_database()
    cursor = db_conn.cursor()

    # Load collision data
    load_collision_data(db_conn)

    # Load constants and data
    map_constants = load_map_constants()
    tileset_constants = load_tileset_constants()
    map_headers, map_to_constant, map_connections = extract_map_headers()
    map_data = extract_map_data()
    tileset_data = extract_tileset_data()

    # Insert tileset data
    tileset_count = 0
    for tileset_name, tileset_info in tileset_data.items():
        tileset_id = find_tileset_id(tileset_name, tileset_constants)

        if tileset_id is not None:
            cursor.execute(
                "INSERT INTO tilesets (id, name, blockset_path, tileset_path) VALUES (?, ?, ?, ?)",
                (
                    tileset_id,
                    tileset_name,
                    tileset_info["blockset_path"],
                    tileset_info["tileset_png_path"],
                ),
            )
            tileset_count += 1

            # Parse and insert blockset data
            blockset_path = tileset_info["blockset_path"]
            if os.path.exists(blockset_path):
                blocks = parse_blockset_file(blockset_path)
                for block_index, block_data in enumerate(blocks):
                    cursor.execute(
                        "INSERT INTO blocksets (tileset_id, block_index, block_data) VALUES (?, ?, ?)",
                        (tileset_id, block_index, block_data),
                    )
                print(f"Inserted {len(blocks)} blocks for tileset {tileset_name}")
            else:
                print(f"Warning: Blockset file not found: {blockset_path}")

            # Parse and insert tileset tile data
            tileset_2bpp_path = tileset_info["tileset_2bpp_path"]
            if tileset_2bpp_path and os.path.exists(tileset_2bpp_path):
                tiles = parse_2bpp_file(tileset_2bpp_path)
                for tile_index, tile_data in enumerate(tiles):
                    cursor.execute(
                        "INSERT INTO tileset_tiles (tileset_id, tile_index, tile_data) VALUES (?, ?, ?)",
                        (tileset_id, tile_index, tile_data),
                    )
                print(f"Inserted {len(tiles)} tiles for tileset {tileset_name}")
            else:
                print(f"Warning: 2bpp file not found: {tileset_2bpp_path}")
        else:
            print(f"Warning: No tileset ID found for tileset {tileset_name}")

    print(f"Inserted {tileset_count} tilesets into database")

    # First pass: Process all maps to identify overworld maps and their dimensions
    overworld_maps = {}
    for map_name, map_info in map_constants.items():
        # Find the corresponding map header
        header_info = None
        for header_name, header_data in map_headers.items():
            if header_data["map_id"] == map_name:
                header_info = header_data
                break

        if not header_info:
            header_info = {}

        tileset_name = header_info.get("tileset")
        tileset_id = None
        if tileset_name:
            tileset_id = find_tileset_id(tileset_name, tileset_constants)

            # Check if this is an overworld map (tileset_id = 0)
            is_overworld = tileset_id == 0

            if is_overworld:
                # Store information about this overworld map
                overworld_maps[map_name] = {
                    "width": map_info["width"],
                    "height": map_info["height"],
                    "id": map_info["id"],
                }

    # Insert map data
    map_count = 0
    tileset_match_count = 0
    for map_name, map_info in map_constants.items():
        map_id = map_info["id"]

        # Find the corresponding map header
        header_info = None
        for header_name, header_data in map_headers.items():
            if header_data["map_id"] == map_name:
                header_info = header_data
                break

        if not header_info:
            header_info = {}

        tileset_name = header_info.get("tileset")
        tileset_id = None
        if tileset_name:
            tileset_id = find_tileset_id(tileset_name, tileset_constants)
            if tileset_id is not None:
                tileset_match_count += 1

        # Find the corresponding .blk file
        blk_name = find_matching_blk_file(map_name, map_data)
        blk_data = None
        if blk_name:
            blk_data = map_data[blk_name]["blk_data"]

        # Check if this is an overworld map (tileset_id = 0)
        is_overworld = tileset_id == 0

        # For overworld maps, invert the y-coordinates by reversing the rows in the blk_data
        if is_overworld and blk_data:
            width = map_info["width"]
            height = map_info["height"]

            # Convert blk_data to a list of bytes
            blk_bytes = list(blk_data)

            # Reshape into a 2D grid (rows x columns)
            grid = []
            for y in range(height):
                row = []
                for x in range(width):
                    idx = y * width + x
                    if idx < len(blk_bytes):
                        row.append(blk_bytes[idx])
                    else:
                        row.append(0)  # Pad with zeros if needed
                grid.append(row)

            # Reverse the rows to invert y-coordinates
            grid.reverse()

            # Flatten back to 1D
            inverted_blk_bytes = []
            for row in grid:
                inverted_blk_bytes.extend(row)

            # Convert back to bytes
            blk_data = bytes(inverted_blk_bytes)

        # Insert map data even if some fields are missing
        cursor.execute(
            """
            INSERT INTO maps (id, name, width, height, tileset_id, blk_data, is_overworld)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                map_id,
                map_name,
                map_info["width"],
                map_info["height"],
                tileset_id,
                blk_data,
                1 if tileset_id == 0 else 0,
            ),
        )
        map_count += 1

        if not blk_name:
            print(f"Warning: No matching .blk file found for map {map_name}")
        if tileset_id is None and tileset_name:
            print(f"Warning: No tileset ID found for tileset {tileset_name}")

    print(f"Inserted {map_count} maps into database")
    print(f"Maps with matching tileset IDs: {tileset_match_count}")

    # Populate tiles_raw table with raw tile data
    print("Populating tiles_raw table...")
    tiles_raw_count = 0

    # Clear existing data
    cursor.execute("DELETE FROM tiles_raw")

    # Process each map to extract raw tile data
    for map_name, map_info in map_constants.items():
        map_id = map_info["id"]
        width = map_info["width"]
        height = map_info["height"]

        # Find the corresponding map header to get tileset
        header_info = None
        for header_name, header_data in map_headers.items():
            if header_data["map_id"] == map_name:
                header_info = header_data
                break

        if not header_info:
            continue

        tileset_name = header_info.get("tileset")
        if not tileset_name:
            continue

        tileset_id = find_tileset_id(tileset_name, tileset_constants)
        if tileset_id is None:
            continue

        # Check if this is an overworld map
        is_overworld = tileset_id == 0

        # Find the corresponding .blk file
        blk_name = find_matching_blk_file(map_name, map_data)
        if not blk_name or not map_data[blk_name]["blk_data"]:
            continue

        blk_data = map_data[blk_name]["blk_data"]

        # Convert blk_data to a list of integers
        try:
            # If blk_data is already a bytes object
            if isinstance(blk_data, bytes):
                blk_bytes = list(blk_data)
            # If blk_data is a string representation of hex
            elif isinstance(blk_data, str) and all(
                c in "0123456789ABCDEFabcdef" for c in blk_data
            ):
                blk_bytes = [
                    int(blk_data[i : i + 2], 16) for i in range(0, len(blk_data), 2)
                ]
            else:
                # Try to convert from binary string
                blk_bytes = list(map(ord, blk_data))
        except Exception as e:
            print(f"Error processing blk_data for map {map_name}: {e}")
            continue

        # Verify we have the expected number of blocks
        expected_blocks = width * height
        if len(blk_bytes) < expected_blocks:
            # Pad with zeros if needed
            blk_bytes.extend([0] * (expected_blocks - len(blk_bytes)))
        elif len(blk_bytes) > expected_blocks:
            # Truncate if needed
            blk_bytes = blk_bytes[:expected_blocks]

        # Process each block in the map
        for y in range(height):
            for x in range(width):
                # Get the block index from the map data
                block_pos = y * width + x
                if block_pos < len(blk_bytes):
                    block_index = blk_bytes[block_pos]

                    # Determine if the block is walkable using the database
                    is_walkable = is_block_walkable(block_index, tileset_id, db_conn)

                    # Insert into tiles_raw table
                    cursor.execute(
                        """
                        INSERT INTO tiles_raw (map_id, x, y, block_index, tileset_id, is_overworld, is_walkable)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            map_id,
                            x,
                            y,
                            block_index,
                            tileset_id,
                            1 if is_overworld else 0,
                            1 if is_walkable else 0,
                        ),
                    )
                    tiles_raw_count += 1

                    # Commit every 1000 inserts to avoid transaction getting too large
                    if tiles_raw_count % 1000 == 0:
                        db_conn.commit()
                        print(f"Inserted {tiles_raw_count} raw tiles so far...")

    db_conn.commit()
    print(f"Inserted {tiles_raw_count} raw tiles into tiles_raw table")

    # Insert map connections
    connection_count = 0
    for connection in map_connections:
        cursor.execute(
            """
            INSERT INTO map_connections (from_map_id, to_map_id, direction, offset)
            VALUES (?, ?, ?, ?)
            """,
            (
                connection["from_map_id"],
                connection["to_map_id"],
                connection["direction"],
                connection["offset"],
            ),
        )
        connection_count += 1

    print(f"Inserted {connection_count} map connections into database")

    # Add a special table to store overworld map positioning information
    cursor.execute("DROP TABLE IF EXISTS overworld_map_positions")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS overworld_map_positions (
            map_id INTEGER PRIMARY KEY,
            map_name TEXT NOT NULL,
            x_offset INTEGER NOT NULL,
            y_offset INTEGER NOT NULL
        )
        """
    )

    db_conn.commit()
    db_conn.close()

    print("Map data exported to pokemon.db successfully!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Export Pokémon map data to a database"
    )
    args = parser.parse_args()
    main()
