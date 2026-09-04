import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

import create_zones_and_tiles
import export_map

from create_zones_and_tiles import native_step_tile_ids


class NativeStepTileMetadataTest(unittest.TestCase):
    def test_collision_and_encounter_samples_follow_original_half_block_layout(self):
        block = bytes(range(16))
        self.assertEqual(
            [native_step_tile_ids(block, position) for position in range(4)],
            [(4, 5), (6, 7), (12, 13), (14, 15)],
        )

    def test_rejects_incomplete_blocks_and_invalid_positions(self):
        with self.assertRaises(ValueError):
            native_step_tile_ids(bytes(range(15)), 0)
        with self.assertRaises(ValueError):
            native_step_tile_ids(bytes(range(16)), 4)

    def test_real_route_1_exports_native_grass_samples(self):
        original_map_db = export_map.DB_PATH
        original_zone_db = create_zones_and_tiles.DB_PATH
        original_tile_output = create_zones_and_tiles.TILE_IMAGE_OUTPUT_DIR
        try:
            with tempfile.TemporaryDirectory(
                prefix="pokemon-encounter-export-", dir="/var/tmp"
            ) as temp_dir:
                db_path = Path(temp_dir) / "pokemon.db"
                export_map.DB_PATH = db_path
                create_zones_and_tiles.DB_PATH = db_path
                create_zones_and_tiles.TILE_IMAGE_OUTPUT_DIR = (
                    Path(temp_dir) / "tile_images"
                )
                with contextlib.redirect_stdout(io.StringIO()):
                    export_map.main()
                    create_zones_and_tiles.main()

                with contextlib.closing(sqlite3.connect(db_path)) as conn:
                    self.assertEqual(
                        conn.execute(
                            """
                            SELECT COUNT(*)
                            FROM tiles AS tile
                            JOIN maps AS map ON map.id = tile.map_id
                            JOIN tilesets AS tileset ON tileset.id = map.tileset_id
                            WHERE map.name = 'ROUTE_1'
                              AND tile.collision_type = 1
                              AND tile.raw_encounter_tile_id = tileset.grass_tile_id
                            """
                        ).fetchone()[0],
                        104,
                    )
                    self.assertEqual(
                        conn.execute(
                            """
                            SELECT COUNT(*) FROM tiles
                            WHERE raw_foot_tile_id IS NULL
                               OR raw_encounter_tile_id IS NULL
                            """
                        ).fetchone()[0],
                        0,
                    )
        finally:
            export_map.DB_PATH = original_map_db
            create_zones_and_tiles.DB_PATH = original_zone_db
            create_zones_and_tiles.TILE_IMAGE_OUTPUT_DIR = original_tile_output


if __name__ == "__main__":
    unittest.main()
