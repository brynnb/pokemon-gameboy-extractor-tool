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
    def test_water_uses_source_tileset_and_foot_sample(self):
        water = create_zones_and_tiles.water_tileset_names()
        classify = create_zones_and_tiles.step_collision_type
        self.assertEqual(classify(0x14, "REDS_HOUSE_1", water, {0x14}), 1)
        self.assertEqual(classify(0x14, "OVERWORLD", water, set()), 2)
        self.assertEqual(classify(0x48, "FOREST", water, set()), 2)
        self.assertEqual(classify(0x32, "SHIP_PORT", water, {0x32}), 1)
        self.assertEqual(classify(0x48, "SHIP_PORT", water, set()), 0)
        self.assertEqual(classify(0x32, "OVERWORLD", water, set()), 2)
        block = bytearray([0x14] * 16)
        block[4] = 0x01
        foot, _ = native_step_tile_ids(block, 0)
        self.assertEqual(classify(foot, "REDS_HOUSE_1", water, {0x01}), 1)
        self.assertEqual(classify(0x00, "REDS_HOUSE_1", water, {0x14}), 0)

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
                        conn.execute("SELECT collision_type FROM tiles WHERE map_id=37 AND local_x=2 AND local_y=7").fetchone()[0],
                        1,
                    )
                    self.assertEqual(
                        conn.execute("SELECT COUNT(*) FROM tiles JOIN maps ON maps.id=tiles.map_id JOIN tilesets ON tilesets.id=maps.tileset_id WHERE tilesets.name='REDS_HOUSE_1' AND collision_type=2").fetchone()[0],
                        0,
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
