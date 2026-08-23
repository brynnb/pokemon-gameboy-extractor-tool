#!/usr/bin/env python3
"""
Export static data and assets for the offline Phaser map viewer.

The viewer intentionally does not talk to a backend. This script materializes
the small query surface it needs from pokemon.db into JSON files under
pokemon-phaser/public/viewer-data and copies tile/sprite PNGs under
pokemon-phaser/public/viewer-assets.
"""
from __future__ import annotations

from contextlib import closing
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from config import (
    DB_PATH,
    PROJECT_ROOT,
    SPRITES_DIR,
    TILE_IMAGE_OUTPUT_DIR,
    VIEWER_ASSET_DIR,
    VIEWER_DATA_DIR,
    VIEWER_SPRITE_ASSET_DIR,
    VIEWER_TILE_ASSET_DIR,
)

TILE_ASSET_DIR = VIEWER_TILE_ASSET_DIR
SPRITE_ASSET_DIR = VIEWER_SPRITE_ASSET_DIR


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def deterministic_generation_time(conn: sqlite3.Connection) -> tuple[str, int]:
    rows = conn.execute(
        "SELECT source_date_epoch FROM extraction_runs"
    ).fetchall()
    if len(rows) != 1:
        raise ValueError(
            f"Expected one deterministic extraction run, found {len(rows)}"
        )
    epoch = int(rows[0][0])
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(), epoch


def clean_output_dirs() -> None:
    for path in (VIEWER_DATA_DIR, VIEWER_ASSET_DIR):
        if path.exists():
            shutil.rmtree(path)
    VIEWER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    TILE_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    SPRITE_ASSET_DIR.mkdir(parents=True, exist_ok=True)


def source_tile_path(image_path: str) -> Path | None:
    basename = Path(image_path).name
    candidates = [
        PROJECT_ROOT / image_path,
        PROJECT_ROOT / "export_scripts" / image_path,
        TILE_IMAGE_OUTPUT_DIR / basename,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def copy_tile_assets(tile_images: list[dict[str, Any]]) -> int:
    copied = 0
    for row in tile_images:
        source = source_tile_path(row["original_image_path"])
        if not source:
            raise FileNotFoundError(
                f"Missing tile image for tile_images.id={row['id']}: "
                f"{row['original_image_path']}"
            )
        shutil.copy2(source, TILE_ASSET_DIR / source.name)
        copied += 1
    return copied


def copy_sprite_assets() -> int:
    source_dir = SPRITES_DIR
    if not source_dir.exists():
        raise FileNotFoundError(
            f"Sprite source directory not found: {source_dir}. "
            "Run git submodule update --init --recursive first."
        )

    copied = 0
    for source in sorted(source_dir.glob("*.png")):
        shutil.copy2(source, SPRITE_ASSET_DIR / source.name)
        copied += 1
    return copied


def export_static_json(conn: sqlite3.Connection) -> dict[str, int]:
    conn.row_factory = sqlite3.Row

    tile_images = rows_to_dicts(
        conn.execute(
            """
            SELECT id, tileset_id, block_index, position, image_path, image_hash
            FROM tile_images
            ORDER BY id
            """
        )
    )
    for row in tile_images:
        row["original_image_path"] = row["image_path"]
        row["image_path"] = f"viewer-assets/tile_images/{Path(row['image_path']).name}"
    write_json(VIEWER_DATA_DIR / "tile-images.json", tile_images)

    maps = rows_to_dicts(
        conn.execute(
            """
            SELECT id, name, width, height, tileset_id, is_overworld
            FROM maps
            ORDER BY id
            """
        )
    )
    map_info_dir = VIEWER_DATA_DIR / "map-info"
    for map_row in maps:
        write_json(map_info_dir / f"{map_row['id']}.json", map_row)

    overworld_maps = [
        {"id": row["id"], "name": row["name"]}
        for row in maps
        if row["is_overworld"] == 1
    ]
    write_json(VIEWER_DATA_DIR / "overworld-maps.json", overworld_maps)

    tiles_dir = VIEWER_DATA_DIR / "tiles"
    tile_count = 0
    for map_row in maps:
        tiles = rows_to_dicts(
            conn.execute(
                """
                SELECT
                    t.id,
                    t.x,
                    t.y,
                    t.tile_image_id,
                    t.local_x,
                    t.local_y,
                    t.map_id,
                    t.collision_type,
                    m.name AS map_name
                FROM tiles t
                JOIN maps m ON t.map_id = m.id
                WHERE t.map_id = ?
                ORDER BY t.y, t.x, t.id
                """,
                (map_row["id"],),
            )
        )
        tile_count += len(tiles)
        write_json(tiles_dir / f"{map_row['id']}.json", tiles)

    items = rows_to_dicts(
        conn.execute(
            """
            SELECT
                o.id,
                o.x,
                o.y,
                o.map_id,
                o.item_id,
                i.name,
                i.short_name AS description
            FROM objects o
            LEFT JOIN items i ON o.item_id = i.id
            WHERE o.object_type = 'item'
              AND o.x IS NOT NULL
              AND o.y IS NOT NULL
            ORDER BY o.map_id, o.id
            """
        )
    )
    write_json(VIEWER_DATA_DIR / "items.json", items)

    npcs = rows_to_dicts(
        conn.execute(
            """
            SELECT
                id,
                x,
                y,
                map_id,
                sprite_name,
                name,
                text,
                action_type,
                action_direction,
                movement_type,
                trainer_class,
                trainer_party_index
            FROM objects
            WHERE object_type = 'npc'
              AND x IS NOT NULL
              AND y IS NOT NULL
            ORDER BY map_id, id
            """
        )
    )
    write_json(VIEWER_DATA_DIR / "npcs.json", npcs)

    warps = rows_to_dicts(
        conn.execute(
            """
            SELECT
                id,
                source_map,
                source_map_id AS map_id,
                x,
                y,
                destination_map,
                destination_kind,
                destination_map_id,
                destination_x,
                destination_y,
                destination_warp_id
            FROM warps
            WHERE x IS NOT NULL
              AND y IS NOT NULL
            ORDER BY source_map_id, id
            """
        )
    )
    write_json(VIEWER_DATA_DIR / "warps.json", warps)

    return {
        "tileImages": len(tile_images),
        "maps": len(maps),
        "overworldMaps": len(overworld_maps),
        "tiles": tile_count,
        "items": len(items),
        "npcs": len(npcs),
        "warps": len(warps),
    }


def main() -> None:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Missing database: {DB_PATH}")

    clean_output_dirs()
    with closing(sqlite3.connect(DB_PATH)) as conn:
        counts = export_static_json(conn)

    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        tile_images = rows_to_dicts(
            conn.execute(
                """
                SELECT id, image_path AS original_image_path
                FROM tile_images
                ORDER BY id
                """
            )
        )
        generated_at, source_date_epoch = deterministic_generation_time(conn)

    counts["copiedTileImages"] = copy_tile_assets(tile_images)
    counts["copiedSprites"] = copy_sprite_assets()
    manifest = {
        "generatedAt": generated_at,
        "sourceDateEpoch": source_date_epoch,
        "counts": counts,
    }
    write_json(VIEWER_DATA_DIR / "manifest.json", manifest)

    print("Exported offline viewer data:")
    for key, value in counts.items():
        print(f"  {key}: {value}")
    print(f"Output: {VIEWER_DATA_DIR}")


if __name__ == "__main__":
    main()
