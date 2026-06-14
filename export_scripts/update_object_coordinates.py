#!/usr/bin/env python3
"""
Update Object Coordinates

This script updates the global coordinates (x, y) of objects in the database
based on their local coordinates (local_x, local_y) and the position of their map.

Coordinate System:
- For overworld maps (is_overworld = 1), global coordinates are calculated by adding
  the map's offset to the local coordinates. The map's offset is determined by the
  global coordinates of the (0,0) local coordinates in that map.
- For non-overworld maps (is_overworld = 0), global coordinates are the same as
  local coordinates.

This ensures that objects in the overworld have consistent global coordinates
that match the global coordinate system of the tiles.
"""

import sqlite3
import time

from config import DB_PATH


def get_map_positions(cursor):
    """Get the positions of all maps"""
    # For overworld maps, get the global coordinates of the (0,0) local coordinates
    cursor.execute(
        """
        SELECT m.id, t.x, t.y
        FROM maps m
        JOIN tiles t ON m.id = t.map_id
        WHERE m.is_overworld = 1 AND t.local_x = 0 AND t.local_y = 0
        GROUP BY m.id
        """
    )
    return {map_id: (x, y) for map_id, x, y in cursor.fetchall()}


def update_object_coordinates(conn):
    """Update the global coordinates of objects based on their map's position"""
    cursor = conn.cursor()

    # Get map positions for overworld maps
    map_positions = get_map_positions(cursor)

    # Update object coordinates for overworld maps
    total_updated = 0
    for map_id, (offset_x, offset_y) in map_positions.items():
        cursor.execute(
            """
            UPDATE objects
            SET x = local_x + ?,
                y = local_y + ?
            WHERE map_id = ?
            """,
            (offset_x, offset_y, map_id),
        )
        total_updated += cursor.rowcount

    # For non-overworld maps, set global coordinates equal to local coordinates
    cursor.execute(
        """
        UPDATE objects
        SET x = local_x,
            y = local_y
        WHERE map_id IN (
            SELECT id FROM maps WHERE is_overworld = 0
        )
        """
    )
    total_updated += cursor.rowcount

    conn.commit()
    return total_updated


def main():
    """Main function"""
    # Connect to the database
    conn = sqlite3.connect(DB_PATH)

    # Update object coordinates
    updated_count = update_object_coordinates(conn)

    # Close the connection
    conn.close()

    print(f"Successfully updated coordinates for {updated_count} objects")


if __name__ == "__main__":
    main()
