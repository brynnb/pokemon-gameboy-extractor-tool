#!/usr/bin/env python3
"""Classify warps as 'door' (immediate) or 'carpet' (directional) based on
the original Game Boy tile data.

Outputs a SQL migration for the capture-quest MySQL database.

Classification logic (from home/overworld.asm CheckWarpsNoCollision):
1. Player steps onto a warp tile position
2. Game checks IsPlayerStandingOnDoorTileOrWarpTile:
   a. Is the 8x8 tile at player's feet a "door tile"? -> immediate warp
   b. Is it a "warp tile" (per tileset)? -> immediate warp
3. If neither -> carpet warp (requires directional input to activate)

For carpet warps, the required direction is inferred from the warp's position
on the map:
  - y == 0 (top edge) -> UP
  - y == max_y (bottom edge) -> DOWN
  - x == 0 (left edge) -> LEFT
  - x == max_x (right edge) -> RIGHT
  - Interior position -> infer from destination (e.g., stairs going up/down)
"""

import sqlite3
import sys

DB_PATH = (
    "/Users/brynnbateman/Documents/GitHub/pokemon-gameboy-extractor-tool/pokemon.db"
)

# From data/tilesets/door_tile_ids.asm
# 8x8 tile IDs at player's feet that count as "door" tiles per tileset
DOOR_TILE_IDS = {
    0: [0x1B, 0x58],  # OVERWORLD
    3: [0x3A],  # FOREST
    2: [0x5E],  # MART
    8: [0x54],  # HOUSE
    9: [0x3B],  # FOREST_GATE
    10: [0x3B],  # MUSEUM
    12: [0x3B],  # GATE
    13: [0x1E],  # SHIP
    18: [0x1C, 0x38, 0x1A],  # LOBBY
    19: [0x1A, 0x1C, 0x53],  # MANSION
    20: [0x34],  # LAB
    22: [0x43, 0x58, 0x1B],  # FACILITY
    23: [0x3B, 0x1B],  # PLATEAU
}

# From data/tilesets/warp_tile_ids.asm
# Additional "warp tiles" per tileset that also trigger immediate warps
WARP_TILE_IDS = {
    0: [0x1B, 0x58],  # OVERWORLD
    1: [0x3B, 0x1A, 0x1C],  # REDS_HOUSE_1
    2: [0x5E],  # MART
    3: [0x5A, 0x5C, 0x3A],  # FOREST
    4: [0x3B, 0x1A, 0x1C],  # REDS_HOUSE_2
    5: [0x4A],  # DOJO
    6: [0x5E],  # POKECENTER
    7: [0x4A],  # GYM
    8: [0x54, 0x5C, 0x32],  # HOUSE
    9: [0x3B, 0x1A, 0x1C],  # FOREST_GATE
    10: [0x3B, 0x1A, 0x1C],  # MUSEUM
    11: [0x13],  # UNDERGROUND
    12: [0x3B, 0x1A, 0x1C],  # GATE
    13: [0x37, 0x39, 0x1E, 0x4A],  # SHIP
    14: [],  # SHIP_PORT
    15: [0x1B, 0x13],  # CEMETERY
    16: [0x15, 0x55, 0x04],  # INTERIOR
    17: [0x18, 0x1A, 0x22],  # CAVERN
    18: [0x1A, 0x1C, 0x38],  # LOBBY
    19: [0x1A, 0x1C, 0x53],  # MANSION
    20: [0x34],  # LAB
    21: [],  # CLUB
    22: [0x43, 0x58, 0x20, 0x1B, 0x13],  # FACILITY
    23: [0x1B, 0x3B],  # PLATEAU
}

# Tileset remapping for blockset graphics lookup
TILESET_REMAP = {
    5: 7,  # DOJO -> GYM
    2: 6,  # MART -> POKECENTER
    10: 12,  # MUSEUM -> GATE
    9: 12,  # FOREST_GATE -> GATE
    4: 1,  # REDS_HOUSE_2 -> REDS_HOUSE
}


def get_feet_tile_id(block_data, position):
    """Get the 8x8 tile ID at the player's feet for a given quadrant position.

    Block layout (4x4 of 8x8 tiles):
      [0]  [1]  [2]  [3]
      [4]  [5]  [6]  [7]
      [8]  [9]  [10] [11]
      [12] [13] [14] [15]

    Feet sub-tile (bottom-left of each quadrant):
      Pos 0 (TL): index 4
      Pos 1 (TR): index 6
      Pos 2 (BL): index 12
      Pos 3 (BR): index 14
    """
    feet_indices = {0: 4, 1: 6, 2: 12, 3: 14}
    idx = feet_indices[position]
    if idx < len(block_data):
        return block_data[idx]
    return None


def is_door_or_warp_tile(feet_tile_id, tileset_id):
    """Check if the feet tile is a door tile or warp tile (immediate warp)."""
    door_tiles = DOOR_TILE_IDS.get(tileset_id, [])
    if feet_tile_id in door_tiles:
        return True
    warp_tiles = WARP_TILE_IDS.get(tileset_id, [])
    if feet_tile_id in warp_tiles:
        return True
    return False


def infer_carpet_direction_from_edge(x, y, map_width_tiles, map_height_tiles):
    """Fallback: infer direction from the warp's position relative to map edges.

    Used when the destination map can't be resolved (e.g., LAST_MAP warps).
    These are almost always on the edge of their map.
    """
    at_top = y <= 0
    at_bottom = y >= map_height_tiles - 1
    at_left = x <= 0
    at_right = x >= map_width_tiles - 1

    if at_top:
        return "UP"
    if at_bottom:
        return "DOWN"
    if at_left:
        return "LEFT"
    if at_right:
        return "RIGHT"

    # Not on an edge - use closest edge
    dist_top = y
    dist_bottom = (map_height_tiles - 1) - y
    dist_left = x
    dist_right = (map_width_tiles - 1) - x
    min_dist = min(dist_top, dist_bottom, dist_left, dist_right)
    if min_dist == dist_top:
        return "UP"
    if min_dist == dist_bottom:
        return "DOWN"
    if min_dist == dist_left:
        return "LEFT"
    return "RIGHT"


def infer_carpet_direction_from_dest(
    dest_warp_x, dest_warp_y, dest_map_width, dest_map_height
):
    """Primary method: infer direction from the destination warp's position
    on the destination map.

    The destination warp is where the player appears on the other map.
    Its position relative to the destination map's edges tells us which
    side the player entered from:
      - dest warp at top of dest map -> player walked DOWN into it
      - dest warp at bottom -> player walked UP
      - dest warp at left -> player walked RIGHT
      - dest warp at right -> player walked LEFT
    """
    dist_top = dest_warp_y
    dist_bottom = (dest_map_height - 1) - dest_warp_y
    dist_left = dest_warp_x
    dist_right = (dest_map_width - 1) - dest_warp_x
    min_dist = min(dist_top, dist_bottom, dist_left, dist_right)

    # Direction is OPPOSITE of the edge the dest warp is closest to
    if min_dist == dist_top:
        return "DOWN"  # dest at top -> walked down into it
    if min_dist == dist_bottom:
        return "UP"  # dest at bottom -> walked up into it
    if min_dist == dist_left:
        return "RIGHT"  # dest at left -> walked right into it
    return "LEFT"  # dest at right -> walked left into it


def main():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Cache destination map info for dest_warp_index resolution
    map_info_cache = {}

    def get_map_info(map_name):
        if map_name not in map_info_cache:
            cursor.execute(
                "SELECT id, width, height FROM maps WHERE name = ?", (map_name,)
            )
            r = cursor.fetchone()
            map_info_cache[map_name] = (r[0], r[1] * 2, r[2] * 2) if r else None
        return map_info_cache[map_name]

    warp_events_cache = {}

    def get_warp_events(map_id):
        if map_id not in warp_events_cache:
            cursor.execute(
                "SELECT x, y FROM warp_events WHERE map_id = ? ORDER BY id", (map_id,)
            )
            warp_events_cache[map_id] = cursor.fetchall()
        return warp_events_cache[map_id]

    # Get all warp events with their block data and map dimensions
    cursor.execute(
        """
        SELECT 
            w.map_id, m.name as map_name, w.x, w.y, 
            w.dest_map, w.dest_warp_index,
            tr.tileset_id, tr.block_index,
            m.width, m.height, m.is_overworld
        FROM warp_events w
        JOIN maps m ON m.id = w.map_id
        JOIN tiles_raw tr ON tr.map_id = w.map_id 
            AND tr.x = w.x / 2 AND tr.y = w.y / 2
        ORDER BY w.map_id, w.x, w.y
    """
    )

    warps = cursor.fetchall()

    results = []

    for (
        map_id,
        map_name,
        x,
        y,
        dest_map,
        dest_warp_index,
        tileset_id,
        block_index,
        map_width,
        map_height,
        is_overworld,
    ) in warps:
        # Calculate quadrant position within the 2x2 block
        position = (x % 2) + 2 * (y % 2)

        # Get block data (with tileset remapping for graphics lookup)
        lookup_tileset = TILESET_REMAP.get(tileset_id, tileset_id)
        cursor.execute(
            "SELECT block_data FROM blocksets WHERE tileset_id = ? AND block_index = ?",
            (lookup_tileset, block_index),
        )
        row = cursor.fetchone()
        if not row:
            print(
                f"WARNING: No block data for tileset {lookup_tileset}, block {block_index} "
                f"(map {map_name} at {x},{y})",
                file=sys.stderr,
            )
            continue

        block_data = row[0]
        feet_tile_id = get_feet_tile_id(block_data, position)

        if feet_tile_id is None:
            print(
                f"WARNING: Could not get feet tile for map {map_name} at {x},{y}",
                file=sys.stderr,
            )
            continue

        # Classify: door/warp tile = immediate, everything else = carpet
        if is_door_or_warp_tile(feet_tile_id, tileset_id):
            warp_type = "door"
            direction = None
            dir_method = None
        else:
            warp_type = "carpet"
            map_width_tiles = map_width * 2
            map_height_tiles = map_height * 2
            direction = None
            dir_method = None

            # Primary method: resolve dest_warp_index to find the destination
            # warp's position on the destination map. The side of the dest map
            # the dest warp is on tells us the activation direction.
            dest_info = get_map_info(dest_map)
            if dest_info:
                dest_map_id, dest_mw, dest_mh = dest_info
                dest_warps = get_warp_events(dest_map_id)
                if 1 <= dest_warp_index <= len(dest_warps):
                    dwx, dwy = dest_warps[dest_warp_index - 1]
                    direction = infer_carpet_direction_from_dest(
                        dwx, dwy, dest_mw, dest_mh
                    )
                    dir_method = "dest_warp"

            # Fallback: edge detection on source map (for LAST_MAP, etc.)
            if direction is None:
                direction = infer_carpet_direction_from_edge(
                    x, y, map_width_tiles, map_height_tiles
                )
                dir_method = "edge"

        results.append(
            {
                "map_id": map_id,
                "map_name": map_name,
                "x": x,
                "y": y,
                "dest_map": dest_map,
                "tileset_id": tileset_id,
                "is_overworld": is_overworld,
                "feet_tile_id": feet_tile_id,
                "warp_type": warp_type,
                "direction": direction,
                "dir_method": dir_method,
                "map_width_tiles": map_width * 2,
                "map_height_tiles": map_height * 2,
            }
        )

    # Print summary
    door_count = sum(1 for r in results if r["warp_type"] == "door")
    carpet_count = sum(1 for r in results if r["warp_type"] == "carpet")

    print(f"-- Warp classification results:", file=sys.stderr)
    print(f"-- Total warps: {len(results)}", file=sys.stderr)
    print(f"-- Door (immediate): {door_count}", file=sys.stderr)
    print(f"-- Carpet (directional): {carpet_count}", file=sys.stderr)

    # Print carpet warps grouped by direction
    dest_count = sum(1 for r in results if r.get("dir_method") == "dest_warp")
    edge_count = sum(1 for r in results if r.get("dir_method") == "edge")
    print(
        f"-- Direction method: dest_warp={dest_count}, edge={edge_count}",
        file=sys.stderr,
    )

    for dir_name in ["UP", "DOWN", "LEFT", "RIGHT"]:
        dir_warps = [r for r in results if r["direction"] == dir_name]
        print(
            f"\n-- Carpet warps requiring {dir_name} ({len(dir_warps)}):",
            file=sys.stderr,
        )
        for r in dir_warps:
            method = f"[{r.get('dir_method', '?')}]"
            print(
                f"--   {r['map_name']:35s} ({r['x']:2d},{r['y']:2d}) -> "
                f"{r['dest_map']:30s} feet=0x{r['feet_tile_id']:02X} {method}",
                file=sys.stderr,
            )

    # Output SQL migration
    print("-- Migration 056: Add warp_type and warp_direction to phaser_warps")
    print("-- Generated by classify_warps.py from original Game Boy tile data")
    print("--")
    print("-- warp_type: 'door' (immediate warp on step) or 'carpet' (directional)")
    print("-- warp_direction: required direction for carpet warps (UP/DOWN/LEFT/RIGHT)")
    print("--                 NULL for door warps")
    print("")
    print(
        "ALTER TABLE phaser_warps ADD COLUMN warp_type VARCHAR(10) NOT NULL DEFAULT 'door';"
    )
    print(
        "ALTER TABLE phaser_warps ADD COLUMN warp_direction VARCHAR(10) DEFAULT NULL;"
    )
    print("")

    # Generate UPDATE statements for carpet warps
    # Interior maps: coords match directly between warp_events and phaser_warps
    # Overworld maps: phaser_warps uses global coords, need tile table lookup

    interior_carpets = [
        r for r in results if r["warp_type"] == "carpet" and not r["is_overworld"]
    ]
    overworld_carpets = [
        r for r in results if r["warp_type"] == "carpet" and r["is_overworld"]
    ]

    if interior_carpets:
        print("-- Interior carpet warps (local coords match phaser_warps directly)")
        for r in interior_carpets:
            print(
                f"UPDATE phaser_warps SET warp_type = 'carpet', "
                f"warp_direction = '{r['direction']}' "
                f"WHERE source_map_id = {r['map_id']} "
                f"AND x = {r['x']} AND y = {r['y']};"
            )

    if overworld_carpets:
        print("")
        print("-- Overworld carpet warps (need global coord lookup via phaser_tiles)")
        for r in overworld_carpets:
            print(
                f"UPDATE phaser_warps pw "
                f"JOIN phaser_tiles t ON t.x = pw.x AND t.y = pw.y "
                f"AND t.map_id = {r['map_id']} "
                f"AND t.local_x = {r['x']} AND t.local_y = {r['y']} "
                f"SET pw.warp_type = 'carpet', pw.warp_direction = '{r['direction']}' "
                f"WHERE pw.source_map_id = 9999;"
            )

    conn.close()


if __name__ == "__main__":
    main()
