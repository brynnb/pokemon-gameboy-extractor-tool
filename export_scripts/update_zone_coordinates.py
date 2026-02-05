#!/usr/bin/env python3
"""
Update Map Coordinates

This script updates the coordinates of overworld maps to be relative to Pallet Town.
It starts with Pallet Town (map_id 1) at coordinates (0,0) and recursively updates
connected maps based on their map connections.

Coordinate System:
- Pallet Town's top-left tile is at (0,0)
- For maps to the north, y coordinates are negative with no overlap
  (e.g., Route 1's bottom row is at y=-1, its top row is at y=-36)
- For maps further north (like Viridian City), coordinates continue to decrease
  (e.g., Viridian City's bottom row is at y=-37, just above Route 1's top row)
- For maps to the south, y coordinates are positive and start at Pallet Town's height
- For maps to the west, x coordinates are negative with no overlap
- For maps to the east, x coordinates are positive and start at Pallet Town's width

This ensures there's no overlap between adjacent maps.

The script processes all overworld maps, starting with Pallet Town and branching out
to adjacent maps based on the map_connections table. The maps are processed in
breadth-first order, ensuring that each map's coordinates are updated relative to
its connected maps that have already been processed.

Usage:
    python update_zone_coordinates.py
"""

import sqlite3
import time
from pathlib import Path

# Constants
# Get the project root directory (parent of the script's directory)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "pokemon.db"
PALLET_TOWN_MAP_ID = 0
BLOCK_SIZE = 2  # Each block is 2x2 tiles


def get_map_dimensions(cursor, map_id):
    """Get the width and height of a map in tiles (blocks * 2)"""
    cursor.execute(
        """
        SELECT width * 2, height * 2 
        FROM maps 
        WHERE id = ?
        """,
        (map_id,),
    )
    return cursor.fetchone()


def update_map_coordinates(conn, map_id, x_offset, y_offset):
    """Update the coordinates of a map in the tiles table and save the position metadata"""
    cursor = conn.cursor()

    # 1. Update the actual tiles if they exist
    cursor.execute(
        """
        UPDATE tiles
        SET x = x + ?, y = y + ?
        WHERE map_id = ?
        """,
        (x_offset, y_offset, map_id),
    )

    # 2. Save the offset to the metadata table so other scripts (like tile generation) can use it
    cursor.execute("SELECT name FROM maps WHERE id = ?", (map_id,))
    map_name = cursor.fetchone()[0]
    
    cursor.execute(
        """
        INSERT OR REPLACE INTO overworld_map_positions (map_id, map_name, x_offset, y_offset)
        VALUES (?, ?, ?, ?)
        """,
        (map_id, map_name, x_offset, y_offset),
    )

    conn.commit()
    return cursor.rowcount


def get_map_name(cursor, map_id):
    """Get the name of a map by its ID"""
    cursor.execute("SELECT name FROM maps WHERE id = ?", (map_id,))
    result = cursor.fetchone()
    return result[0] if result else None


def get_map_id_by_name(cursor, map_name):
    """Get the map ID for a given map name"""
    cursor.execute("SELECT id FROM maps WHERE name = ?", (map_name,))
    result = cursor.fetchone()
    return result[0] if result else None


def get_connection_details(cursor, from_map_id, to_map_id):
    """Get connection details between two maps"""
    # Get map names
    from_map_name = get_map_name(cursor, from_map_id)
    to_map_name = get_map_name(cursor, to_map_id)

    if not from_map_name or not to_map_name:
        return None, None

    # Check for direct connection
    cursor.execute(
        """
        SELECT direction, offset
        FROM map_connections
        WHERE from_map_id = ? AND to_map_id = ?
        """,
        (from_map_name, to_map_name),
    )
    result = cursor.fetchone()

    if result:
        return result[0], result[1]

    # Check for reverse connection
    cursor.execute(
        """
        SELECT direction, offset
        FROM map_connections
        WHERE from_map_id = ? AND to_map_id = ?
        """,
        (to_map_name, from_map_name),
    )
    result = cursor.fetchone()

    if result:
        # Reverse the direction
        direction = result[0]
        if direction == "north":
            return "south", result[1]
        elif direction == "south":
            return "north", result[1]
        elif direction == "east":
            return "west", result[1]
        elif direction == "west":
            return "east", result[1]

    return None, None


def calculate_map_offset(cursor, from_map_id, to_map_id, direction, connection_offset):
    """Calculate the x and y offsets for a map based on its connection"""
    # Get dimensions of maps
    from_width, from_height = get_map_dimensions(cursor, from_map_id)
    to_width, to_height = get_map_dimensions(cursor, to_map_id)

    # Calculate new offsets based on direction and connection offset
    x_offset, y_offset = 0, 0

    if direction == "north":
        # Connected map is north of current map
        # For north connections, offset is an x-axis offset (horizontal shift)
        x_offset = connection_offset * BLOCK_SIZE
        # Adjust to make y coordinates end at -1 (no overlap with y=0)
        y_offset = -to_height

    elif direction == "south":
        # Connected map is south of current map
        # For south connections, offset is an x-axis offset (horizontal shift)
        x_offset = connection_offset * BLOCK_SIZE
        y_offset = from_height

    elif direction == "east":
        # Connected map is east of current map
        # For east connections, offset is a y-axis offset (vertical shift)
        x_offset = from_width
        y_offset = connection_offset * BLOCK_SIZE

    elif direction == "west":
        # Connected map is west of current map
        # For west connections, offset is a y-axis offset (vertical shift)
        # Adjust to make x coordinates end at -1 (no overlap with x=0)
        x_offset = -to_width
        y_offset = connection_offset * BLOCK_SIZE

    return x_offset, y_offset


def get_all_map_connections(cursor):
    """Get all map connections from the database"""
    cursor.execute(
        "SELECT from_map_id, to_map_id, direction, offset FROM map_connections"
    )
    return cursor.fetchall()


def get_all_map_names(cursor):
    """Get all map names from the database"""
    cursor.execute("SELECT id, name FROM maps WHERE is_overworld = 1")
    return {row[0]: row[1] for row in cursor.fetchall()}


def process_map_connections(conn):
    """Process all map connections"""
    cursor = conn.cursor()

    # Get all map names
    map_names = get_all_map_names(cursor)

    processed_maps = set()
    map_queue = [(PALLET_TOWN_MAP_ID, 0, 0)]  # (map_id, x_offset, y_offset)

    while map_queue:
        current_map_id, current_x_offset, current_y_offset = map_queue.pop(0)

        if current_map_id in processed_maps:
            continue

        # First, reset the map to its original position (0,0)
        cursor.execute(
            "SELECT MIN(x), MIN(y) FROM tiles WHERE map_id = ?", (current_map_id,)
        )
        min_x, min_y = cursor.fetchone()

        # Reset to (0,0)
        update_map_coordinates(conn, current_map_id, -min_x, -min_y)

        # Then apply the calculated offsets
        updated_tiles = update_map_coordinates(
            conn, current_map_id, current_x_offset, current_y_offset
        )
        map_name = get_map_name(cursor, current_map_id)
        print(
            f"Updated {updated_tiles} tiles for {map_name} (map_id {current_map_id}) with offsets ({current_x_offset}, {current_y_offset})"
        )

        # Mark this map as processed
        processed_maps.add(current_map_id)

        # Find all connections from this map
        cursor.execute(
            """
            SELECT to_map_id, direction, offset
            FROM map_connections
            WHERE from_map_id = ?
            """,
            (map_name,),
        )
        outgoing_connections = cursor.fetchall()

        # Find all connections to this map
        cursor.execute(
            """
            SELECT from_map_id, direction, offset
            FROM map_connections
            WHERE to_map_id = ?
            """,
            (map_name,),
        )
        incoming_connections = cursor.fetchall()

        # Process all connections
        for connections in [outgoing_connections, incoming_connections]:
            for connected_map_name, direction, offset in connections:
                # Get the map ID for the connected map
                connected_map_id = get_map_id_by_name(cursor, connected_map_name)

                if not connected_map_id or connected_map_id in processed_maps:
                    continue

                # Calculate new offsets
                if connected_map_name == map_name:
                    # This is a reverse connection
                    if direction == "north":
                        direction = "south"
                    elif direction == "south":
                        direction = "north"
                    elif direction == "east":
                        direction = "west"
                    elif direction == "west":
                        direction = "east"

                x_offset, y_offset = calculate_map_offset(
                    cursor, current_map_id, connected_map_id, direction, offset
                )

                # Add the connected map to the queue with the calculated offsets
                new_x_offset = current_x_offset + x_offset
                new_y_offset = current_y_offset + y_offset
                map_queue.append((connected_map_id, new_x_offset, new_y_offset))

    print(f"\nProcessed {len(processed_maps)} maps")
    return processed_maps


def main():
    """Main function"""
    start_time = time.time()

    # Connect to the database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # Process map connections
        processed_maps = process_map_connections(conn)

        # Verify the results
        print("\nFinal coordinates:")
        for map_id in processed_maps:
            cursor.execute(
                "SELECT MIN(x), MAX(x), MIN(y), MAX(y) FROM tiles WHERE map_id = ?",
                (map_id,),
            )
            coords = cursor.fetchone()
            map_name = get_map_name(cursor, map_id)
            print(
                f"{map_name} (map_id {map_id}): x={coords[0]} to {coords[1]}, y={coords[2]} to {coords[3]}"
            )

        elapsed_time = time.time() - start_time
        print(f"\nTotal time: {elapsed_time:.2f} seconds")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
