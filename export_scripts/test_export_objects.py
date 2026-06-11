#!/usr/bin/env python3
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_objects import process_map_file
from export_hidden_objects import parse_missable_objects


class ExportObjectsTest(unittest.TestCase):
    def test_process_map_file_accepts_zero_map_id(self):
        conn = sqlite3.connect(":memory:")
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE maps (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        cursor.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, short_name TEXT)")
        cursor.execute("INSERT INTO maps (id, name) VALUES (0, 'PALLET_TOWN')")

        with tempfile.TemporaryDirectory() as temp_dir:
            map_path = Path(temp_dir) / "PalletTown.asm"
            map_path.write_text(
                """
PalletTown_Object:
\tdef_warp_events

\tdef_bg_events
\tbg_event  7,  9, TEXT_PALLETTOWN_SIGN

\tdef_object_events
\tobject_event  3,  8, SPRITE_GIRL, WALK, ANY_DIR, TEXT_PALLETTOWN_GIRL

\tdef_warps_to PALLET_TOWN
""",
                encoding="utf-8",
            )

            objects = process_map_file(
                map_path,
                cursor,
                {"PalletTown": "PALLET_TOWN"},
            )

        self.assertEqual(len(objects), 2)
        self.assertEqual({obj["map_id"] for obj in objects}, {0})


class MissableObjectsTest(unittest.TestCase):
    def test_parse_missable_objects_resolves_victory_road_boulder(self):
        conn = sqlite3.connect(":memory:")
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE objects (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                map_id INTEGER,
                object_type TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            "INSERT INTO objects (id, name, map_id, object_type) VALUES (1, 'VictoryRoad3F_NPC_10', 108, 'npc')"
        )

        missables = parse_missable_objects(cursor, {"VICTORY_ROAD_3F": 108})
        boulder = next(row for row in missables if row["hs_constant"] == "HS_VICTORY_ROAD_3F_BOULDER")

        self.assertEqual(boulder["map_constant"], "VICTORY_ROAD_3F")
        self.assertEqual(boulder["object_constant"], "VICTORYROAD3F_BOULDER4")
        self.assertEqual(boulder["object_index"], 10)
        self.assertEqual(boulder["object_name"], "VictoryRoad3F_NPC_10")
        self.assertEqual(boulder["initial_visible"], 1)


if __name__ == "__main__":
    unittest.main()
