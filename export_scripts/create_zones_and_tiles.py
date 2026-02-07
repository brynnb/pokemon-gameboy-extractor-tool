#!/usr/bin/env python3
"""
Pokemon Map Data Restructuring

This script creates a more structured database schema with tiles table,
and extracts 16x16 pixel tile images from the existing tilesets.

The script:
1. Creates a "tiles" table with x, y coordinates, map_id, and tile_image_id
2. Extracts 16x16 pixel tile images from the existing tilesets

Usage:
    python create_zones_and_tiles.py
"""

import os
import sqlite3
import binascii
from PIL import Image
from pathlib import Path
import sys
import time
import hashlib
import io
import re

# Constants
# Get the project root directory (parent of the script's directory)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "pokemon.db"
TILE_IMAGES_DIR = "tile_images"
BATCH_SIZE = 1000  # Number of tiles to insert in a single batch


def create_new_tables():
    """Create new tiles and tile_images tables in the database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Drop existing tables if they exist
    cursor.execute("DROP TABLE IF EXISTS tiles")
    cursor.execute("DROP TABLE IF EXISTS tile_images")

    # Create tiles table
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS tiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        x INTEGER NOT NULL,
        y INTEGER NOT NULL,
        local_x INTEGER NOT NULL,
        local_y INTEGER NOT NULL,
        map_id INTEGER NOT NULL,
        tile_image_id INTEGER NOT NULL,
        is_overworld INTEGER NOT NULL DEFAULT 0,
        collision_type INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (map_id) REFERENCES maps (id),
        FOREIGN KEY (tile_image_id) REFERENCES tile_images (id)
    )
    """
    )

    # Create tile_images table with image_hash for deduplication
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS tile_images (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tileset_id INTEGER NOT NULL,
        block_index INTEGER NOT NULL,
        position INTEGER NOT NULL,  -- 0: top-left, 1: top-right, 2: bottom-left, 3: bottom-right
        image_path TEXT NOT NULL,
        image_hash TEXT NOT NULL,
        FOREIGN KEY (tileset_id) REFERENCES tilesets (id)
    )
    """
    )

    # Create indexes for better performance
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tiles_map_id ON tiles (map_id)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_tiles_tile_image_id ON tiles (tile_image_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_tile_images_tileset_id ON tile_images (tileset_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_tile_images_hash ON tile_images (image_hash)"
    )

    conn.commit()
    return conn


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


def get_image_hash(img):
    """Generate a hash for an image to identify duplicates"""
    img_bytes = io.BytesIO()
    img.save(img_bytes, format="PNG")
    return hashlib.md5(img_bytes.getvalue()).hexdigest()


def extract_tile_images(conn):
    """Extract 16x16 pixel tile images from the blocksets and tilesets"""
    cursor = conn.cursor()

    # Create directory for tile images if it doesn't exist
    os.makedirs(TILE_IMAGES_DIR, exist_ok=True)

    # Clean up old files
    print("Cleaning up old tile images...")
    old_files = list(Path(TILE_IMAGES_DIR).glob("*.png"))
    for old_file in old_files:
        try:
            os.remove(old_file)
        except Exception as e:
            print(f"Error removing {old_file}: {e}")
    print(f"Removed {len(old_files)} old tile images")

    # Define GameBoy color palette (white, light gray, dark gray, black)
    palette = [(255, 255, 255), (192, 192, 192), (96, 96, 96), (0, 0, 0)]

    # Get all tilesets
    cursor.execute("SELECT id, name FROM tilesets")
    tilesets = cursor.fetchall()

    tile_image_count = 0
    unique_image_count = 0
    duplicate_count = 0
    total_tilesets = len(tilesets)

    print(f"Processing {total_tilesets} tilesets...")
    start_time = time.time()

    # Dictionary to track image hashes and their corresponding IDs
    image_hash_to_id = {}

    # Dictionary to map (tileset_id, block_index, position) to tile_image_id
    block_pos_to_image_id = {}

    for i, (tileset_id, tileset_name) in enumerate(tilesets, 1):
        # Update progress
        sys.stdout.write(f"\rProcessing tileset {i}/{total_tilesets}: {tileset_name}")
        sys.stdout.flush()

        # Special case: Map DOJO (tileset ID 5) to GYM (tileset ID 7)
        # This is because in the original game, DOJO uses the same graphics as GYM
        query_tileset_id = 7 if tileset_id == 5 else tileset_id
        # Special case: Map MART (tileset ID 2) to POKECENTER (tileset ID 6)
        # This is because marts and pokecenters share similar interior graphics
        if tileset_id == 2:
            query_tileset_id = 6

        # Get blockset data for this tileset
        cursor.execute(
            """
        SELECT block_index, block_data 
        FROM blocksets 
        WHERE tileset_id = ? 
        ORDER BY block_index
        """,
            (query_tileset_id,),
        )

        blockset_rows = cursor.fetchall()

        if not blockset_rows:
            continue

        # Get tileset tiles
        cursor.execute(
            """
        SELECT tile_index, tile_data 
        FROM tileset_tiles 
        WHERE tileset_id = ? 
        ORDER BY tile_index
        """,
            (query_tileset_id,),
        )

        tile_rows = cursor.fetchall()

        if not tile_rows:
            continue

        # Create dictionaries for easy lookup
        blocks = {row[0]: row[1] for row in blockset_rows}
        tiles = {row[0]: row[1] for row in tile_rows}

        # Process each block to create 16x16 pixel images (4 per block)
        for block_index, block_data in blocks.items():
            # Define the 4 positions in the block (each position is 2x2 tiles)
            positions = [
                # Top-left: tiles at positions 0,1,4,5
                [(0, 0), (0, 1), (1, 0), (1, 1)],
                # Top-right: tiles at positions 2,3,6,7
                [(0, 2), (0, 3), (1, 2), (1, 3)],
                # Bottom-left: tiles at positions 8,9,12,13
                [(2, 0), (2, 1), (3, 0), (3, 1)],
                # Bottom-right: tiles at positions 10,11,14,15
                [(2, 2), (2, 3), (3, 2), (3, 3)],
            ]

            for pos_index, position in enumerate(positions):
                # Create a new 16x16 image
                img = Image.new("RGB", (16, 16), color=(255, 255, 255))

                # Process each of the 4 tiles in this position
                for i, (y, x) in enumerate(position):
                    # Calculate the position in the block data
                    tile_pos = y * 4 + x

                    # Get the tile index from the block data
                    if tile_pos < len(block_data):
                        tile_index = block_data[tile_pos]
                    else:
                        continue

                    # Get the tile data
                    tile_data = tiles.get(tile_index)
                    if not tile_data:
                        continue

                    # Decode the tile data
                    tile_pixels = decode_2bpp_tile(tile_data)

                    # Calculate where to place this tile in the 16x16 image
                    offset_x = (i % 2) * 8  # 0 for left tiles, 8 for right tiles
                    offset_y = (i // 2) * 8  # 0 for top tiles, 8 for bottom tiles

                    # Draw the tile
                    for py in range(8):
                        for px in range(8):
                            pixel_value = tile_pixels[py][px]
                            pixel_color = palette[pixel_value]
                            img.putpixel((offset_x + px, offset_y + py), pixel_color)

                # Generate hash for the image
                img_hash = get_image_hash(img)

                # Check if we've already seen this image
                if img_hash in image_hash_to_id:
                    # Use the existing image ID
                    existing_image_id = image_hash_to_id[img_hash]
                    block_pos_to_image_id[(tileset_id, block_index, pos_index)] = (
                        existing_image_id
                    )
                    # Special case: If this is the GYM tileset (ID 7), also store the mapping for DOJO (ID 5)
                    if tileset_id == 7:
                        block_pos_to_image_id[(5, block_index, pos_index)] = (
                            existing_image_id
                        )
                    # Special case: If this is the POKECENTER tileset (ID 6), also store the mapping for MART (ID 2)
                    if tileset_id == 6:
                        block_pos_to_image_id[(2, block_index, pos_index)] = (
                            existing_image_id
                        )
                    duplicate_count += 1
                else:
                    # Save the image with a sequential number
                    image_path = f"{TILE_IMAGES_DIR}/tile_{unique_image_count}.png"
                    img.save(image_path)

                    # Insert the new image record
                    cursor.execute(
                        """
                    INSERT INTO tile_images (tileset_id, block_index, position, image_path, image_hash)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                        (tileset_id, block_index, pos_index, image_path, img_hash),
                    )

                    # Get the new image ID
                    image_id = cursor.lastrowid
                    image_hash_to_id[img_hash] = image_id
                    block_pos_to_image_id[(tileset_id, block_index, pos_index)] = (
                        image_id
                    )
                    # Special case: If this is the GYM tileset (ID 7), also store the mapping for DOJO (ID 5)
                    if tileset_id == 7:
                        block_pos_to_image_id[(5, block_index, pos_index)] = image_id
                    # Special case: If this is the POKECENTER tileset (ID 6), also store the mapping for MART (ID 2)
                    if tileset_id == 6:
                        block_pos_to_image_id[(2, block_index, pos_index)] = image_id
                    unique_image_count += 1

                tile_image_count += 1

                # Commit periodically
                if unique_image_count % BATCH_SIZE == 0:
                    conn.commit()

    conn.commit()
    elapsed_time = time.time() - start_time
    print(f"\nProcessed {tile_image_count} tile images")
    print(f"- Unique images: {unique_image_count}")
    print(f"- Duplicate images: {duplicate_count}")
    print(f"- Total time: {elapsed_time:.2f} seconds")

    return block_pos_to_image_id


def populate_tiles(conn, block_pos_to_image_id):
    """Populate the tiles table based on the tiles_raw and maps tables"""
    cursor = conn.cursor()

    # Clear the tiles table before repopulating
    print("Clearing existing tiles...")
    cursor.execute("DELETE FROM tiles")
    conn.commit()

    # Check if the tiles_raw table exists
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='tiles_raw'"
    )
    has_tiles_raw = cursor.fetchone() is not None

    if not has_tiles_raw:
        print("Error: tiles_raw table does not exist. Please run export_map.py first.")
        return

    # Check if the collision_tiles table exists
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='collision_tiles'"
    )
    has_collision_tiles = cursor.fetchone() is not None

    if not has_collision_tiles:
        print(
            "Warning: collision_tiles table does not exist. Walkability data may be inaccurate."
        )

    # Get all maps with their is_overworld flag
    cursor.execute(
        """
    SELECT id, name, width, height, tileset_id, is_overworld
    FROM maps
    """
    )

    maps = cursor.fetchall()
    total_maps = len(maps)

    print(f"Processing {total_maps} maps...")
    tile_count = 0
    processed_maps = 0
    skipped_maps = 0
    skipped_map_names = []
    start_time = time.time()

    # Prepare for batch insert
    tiles_data = []

    # Get the overworld map positions if available
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='overworld_map_positions'"
    )
    has_positions_table = cursor.fetchone() is not None

    map_positions = {}
    if has_positions_table:
        cursor.execute(
            "SELECT map_name, x_offset, y_offset FROM overworld_map_positions"
        )
        for map_name, x_offset, y_offset in cursor.fetchall():
            map_positions[map_name] = (x_offset, y_offset)

    # Process each map
    for i, (
        map_id,
        map_name,
        width,
        height,
        tileset_id,
        is_overworld,
    ) in enumerate(maps, 1):
        # Update progress every 5 maps
        if i % 5 == 0 or i == total_maps:
            sys.stdout.write(
                f"\rProcessed {i}/{total_maps} maps, created {tile_count} tiles"
            )
            sys.stdout.flush()

        processed_maps += 1

        # Get position offsets for this map if it's an overworld map
        x_offset, y_offset = 0, 0
        if is_overworld and map_name in map_positions:
            x_offset, y_offset = map_positions[map_name]

        # Get raw tile data for this map from tiles_raw table
        cursor.execute(
            """
        SELECT x, y, block_index, tileset_id, is_overworld
        FROM tiles_raw
        WHERE map_id = ?
        """,
            (map_id,),
        )

        raw_tiles = cursor.fetchall()

        if not raw_tiles:
            continue

        # Collect tiles for this map
        map_tiles = []

        # Process each raw tile
        for (
            raw_x,
            raw_y,
            block_index,
            raw_tileset_id,
            raw_is_overworld,
        ) in raw_tiles:
            # Shared tileset mappings (from gfx/tilesets.asm)
            # Some maps use different tileset IDs but share the same blockset/graphics
            lookup_tileset_id = raw_tileset_id

            if raw_tileset_id == 5:  # DOJO uses GYM (7)
                lookup_tileset_id = 7
            elif raw_tileset_id == 2:  # MART uses POKECENTER (6)
                lookup_tileset_id = 6
            elif raw_tileset_id == 10:  # MUSEUM uses GATE (12)
                lookup_tileset_id = 12
            elif raw_tileset_id == 9:  # FOREST_GATE uses GATE (12)
                lookup_tileset_id = 12
            elif raw_tileset_id == 4:  # REDS_HOUSE_2 uses REDS_HOUSE (1)
                lookup_tileset_id = 1

            # Get the block data to check individual tiles
            cursor.execute(
                """
                SELECT block_data FROM blocksets 
                WHERE tileset_id = ? AND block_index = ?
                """,
                (lookup_tileset_id, block_index),
            )
            block_data_row = cursor.fetchone()

            # If we can't find the block data, skip this block
            if not block_data_row:
                continue

            block_data = block_data_row[0]

            # Each block corresponds to 4 tiles (2x2 grid)
            # We need to create 4 entries in the tiles table
            for position in range(4):
                # Calculate the global x, y coordinates
                tile_x = raw_x * 2 + (position % 2) + x_offset
                tile_y = raw_y * 2 + (position // 2) + y_offset

                # Calculate local x, y coordinates
                local_x = raw_x * 2 + (position % 2)
                local_y = raw_y * 2 + (position // 2)

                # Get the tile_image_id from our dictionary
                tile_image_id = block_pos_to_image_id.get(
                    (lookup_tileset_id, block_index, position)
                )
                if not tile_image_id:
                    # Try with a default position if specific position not found
                    tile_image_id = block_pos_to_image_id.get(
                        (lookup_tileset_id, block_index, 0)
                    )
                    if not tile_image_id:
                        continue

                # Determine collision type for THIS 16x16 tile (2x2 8x8 tiles)
                # 0: Solid, 1: Land, 2: Water
                #
                # In the original Game Boy Pokémon engine, collision is checked per
                # 16x16 "step" position by looking at the 8x8 tile at the player's
                # feet — the bottom-left tile of each 2x2 step area. The coll_tiles
                # list (stored in collision_tiles table) contains the 8x8 tile IDs
                # that ARE walkable/passable.
                #
                # Block layout (4x4 of 8x8 tiles, indices 0-15):
                #   [0]  [1]  [2]  [3]
                #   [4]  [5]  [6]  [7]
                #   [8]  [9]  [10] [11]
                #   [12] [13] [14] [15]
                #
                # Each 16x16 step position's bottom-left 8x8 tile index:
                #   Pos 0 (TL): index 4
                #   Pos 1 (TR): index 6
                #   Pos 2 (BL): index 12
                #   Pos 3 (BR): index 14
                collision_type = 0

                # Get the bottom-left 8x8 sub-tile for this position
                bl_sub_indices = {0: 4, 1: 6, 2: 12, 3: 14}
                bl_idx = bl_sub_indices[position]

                # Also gather all 4 sub-tiles for water detection
                tile_sub_indices = []
                if position == 0:
                    tile_sub_indices = [0, 1, 4, 5]
                elif position == 1:
                    tile_sub_indices = [2, 3, 6, 7]
                elif position == 2:
                    tile_sub_indices = [8, 9, 12, 13]
                elif position == 3:
                    tile_sub_indices = [10, 11, 14, 15]

                raw_tile_ids = []
                for sub_idx in tile_sub_indices:
                    if sub_idx < len(block_data):
                        raw_tile_ids.append(block_data[sub_idx])

                # Check for Water ($14, $48)
                if any(tid == 0x14 or tid == 0x48 for tid in raw_tile_ids):
                    collision_type = 2
                elif has_collision_tiles and bl_idx < len(block_data):
                    # Check if the bottom-left 8x8 tile is in the walkable list
                    bl_tile_id = block_data[bl_idx]
                    cursor.execute(
                        "SELECT COUNT(*) FROM collision_tiles WHERE tileset_id = ? AND tile_id = ?",
                        (raw_tileset_id, bl_tile_id),
                    )
                    if cursor.fetchone()[0] > 0:
                        collision_type = 1

                # Add to map tiles
                map_tiles.append(
                    (
                        tile_x,
                        tile_y,
                        local_x,
                        local_y,
                        map_id,
                        tile_image_id,
                        is_overworld,
                        collision_type,
                    )
                )

        # Sort map tiles by y-coordinate in descending order (top to bottom becomes bottom to top)
        map_tiles.sort(key=lambda t: (-t[1], t[0]))

        # Add to batch insert data
        tiles_data.extend(map_tiles)
        tile_count += len(map_tiles)

        # Execute batch insert if we've reached the batch size
        if len(tiles_data) >= BATCH_SIZE:
            cursor.executemany(
                """
            INSERT INTO tiles (x, y, local_x, local_y, map_id, tile_image_id, is_overworld, collision_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                tiles_data,
            )
            conn.commit()
            tiles_data = []

    # Insert any remaining tiles
    if tiles_data:
        cursor.executemany(
            """
        INSERT INTO tiles (x, y, local_x, local_y, map_id, tile_image_id, is_overworld, collision_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            tiles_data,
        )
        conn.commit()

    elapsed_time = time.time() - start_time
    print(
        f"\nCreated {tile_count} tiles from {processed_maps} maps in {elapsed_time:.2f} seconds"
    )


def main():
    """Main function"""
    total_start_time = time.time()

    print("Creating new tables...")
    conn = create_new_tables()

    print("\nExtracting tile images...")
    block_pos_to_image_id = extract_tile_images(conn)

    print("\nPopulating tiles table...")
    populate_tiles(conn, block_pos_to_image_id)

    # Print summary
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM tile_images")
    tile_image_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM tiles")
    tile_count = cursor.fetchone()[0]

    total_elapsed_time = time.time() - total_start_time

    print("\nSummary:")
    print(f"- Extracted {tile_image_count} unique tile images")
    print(f"- Created {tile_count} tiles")
    print(f"- Total time: {total_elapsed_time:.2f} seconds")

    print("\nDone!")
    conn.close()


if __name__ == "__main__":
    main()
