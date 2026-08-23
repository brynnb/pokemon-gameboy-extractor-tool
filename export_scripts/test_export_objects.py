#!/usr/bin/env python3
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_objects import process_map_file
from export_hidden_objects import (
    create_tables,
    parse_hidden_object_map_labels,
    parse_hidden_objects_content,
    parse_missable_objects,
)


class ExportObjectsTest(unittest.TestCase):
    def test_process_map_file_accepts_zero_map_id(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
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
        self.addCleanup(conn.close)
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


class HiddenObjectRelationshipsTest(unittest.TestCase):
    SOURCE = """
HiddenObjectMaps:
    db REDS_HOUSE_2F
    db MT_MOON_B2F
    db -1 ; end

HiddenObjectPointers:
    dw RedsHouse2FHiddenObjects
    dw MtMoon3HiddenObjects

MACRO hidden_object
ENDM

RedsHouse2FHiddenObjects:
    hidden_object  1, 2, ANY_FACING, BedroomPC
    db -1 ; end

MtMoon3HiddenObjects:
    hidden_text_predef  3, 4, PickUpItemText, HiddenItems
    db -1 ; end
"""

    def test_uses_the_authoritative_parallel_tables_for_non_matching_labels(self):
        label_map = parse_hidden_object_map_labels(self.SOURCE)
        self.assertEqual(label_map["MtMoon3"], "MT_MOON_B2F")

        rows = parse_hidden_objects_content(
            self.SOURCE, {"REDS_HOUSE_2F": 38, "MT_MOON_B2F": 61}
        )
        self.assertEqual(
            [(row["map_constant"], row["map_id"]) for row in rows],
            [("REDS_HOUSE_2F", 38), ("MT_MOON_B2F", 61)],
        )

    def test_rejects_mismatched_map_and_pointer_tables(self):
        bad_source = self.SOURCE.replace("    dw MtMoon3HiddenObjects\n", "")
        with self.assertRaisesRegex(ValueError, "length mismatch"):
            parse_hidden_object_map_labels(bad_source)

    def test_schema_requires_hidden_object_map_relationships(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("CREATE TABLE maps (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        conn.executemany(
            "INSERT INTO maps (id, name) VALUES (?, ?)",
            [(38, "REDS_HOUSE_2F"), (61, "MT_MOON_B2F")],
        )
        create_tables(conn)

        map_id_column = next(
            column
            for column in conn.execute("PRAGMA table_info(hidden_objects)")
            if column[1] == "map_id"
        )
        self.assertEqual(map_id_column[3], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO hidden_objects (
                    map_constant, map_id, x, y, item_or_direction, routine
                ) VALUES ('REDS_HOUSE_2F', NULL, 1, 2, 'ANY_FACING', 'BedroomPC')
                """
            )
        conn.close()


if __name__ == "__main__":
    unittest.main()
