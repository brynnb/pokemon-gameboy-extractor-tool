from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_map_scripts import create_tables as create_map_script_tables
from export_script_candidates import (
    create_tables as create_candidate_tables,
    insert_candidate,
    insert_diagnostic,
    insert_ir_block,
    validate_normalized_script_tables,
)
from export_text import create_tables as create_text_tables
from map_references import CanonicalMapResolver, MapReferenceError


class CanonicalMapResolverTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.headers = Path(self.temp_dir.name)
        (self.headers / "PalletTown.asm").write_text(
            "map_header PalletTown, PALLET_TOWN, OVERWORLD, NORTH | SOUTH\n",
            encoding="utf-8",
        )
        (self.headers / "CeruleanCity.asm").write_text(
            "map_header CeruleanCity, CERULEAN_CITY, OVERWORLD, NONE\n",
            encoding="utf-8",
        )
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute(
            "CREATE TABLE maps (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE)"
        )
        self.conn.executemany(
            "INSERT INTO maps VALUES (?, ?)",
            ((0, "PALLET_TOWN"), (3, "CERULEAN_CITY")),
        )

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def resolver(self):
        return CanonicalMapResolver.from_connection(self.conn, self.headers)

    def test_uses_header_aliases_preserves_zero_and_resolves_split_source_files(self):
        resolver = self.resolver()
        self.assertEqual(resolver.resolve("PALLET_TOWN"), 0)
        self.assertEqual(resolver.resolve("PalletTown"), 0)
        self.assertEqual(resolver.resolve("pallettown"), 0)
        self.assertEqual(resolver.resolve("CeruleanCity_2"), 3)
        self.assertIsNone(resolver.resolve("GLOBAL", allow_global=True))

    def test_unknown_names_and_missing_canonical_rows_are_rejected(self):
        resolver = self.resolver()
        with self.assertRaisesRegex(MapReferenceError, "Unknown source map"):
            resolver.resolve("NotARealMap")

        self.conn.execute("DELETE FROM maps WHERE name = 'CERULEAN_CITY'")
        with self.assertRaisesRegex(MapReferenceError, "missing maps.name"):
            CanonicalMapResolver.from_connection(self.conn, self.headers)

    def test_script_text_and_candidate_schemas_enforce_map_foreign_keys(self):
        create_map_script_tables(self.conn)
        create_text_tables(self.conn)
        create_candidate_tables(self.conn)

        tables = (
            "map_scripts",
            "npc_movement_data",
            "spin_tiles",
            "event_flags",
            "coordinate_triggers",
            "warp_events",
            "text_pointers",
            "trainer_headers",
            "script_event_candidates",
            "script_event_ir_blocks",
        )
        for table in tables:
            columns = {
                row[1]: row for row in self.conn.execute(f'PRAGMA table_info("{table}")')
            }
            self.assertIn("map_id", columns, table)
            self.assertEqual(columns["map_id"][3], 1, table)
            relationships = self.conn.execute(
                f'PRAGMA foreign_key_list("{table}")'
            ).fetchall()
            self.assertTrue(
                any(row[2] == "maps" and row[3] == "map_id" for row in relationships),
                table,
            )

    def test_candidate_ir_and_diagnostic_rows_store_canonical_map_ids(self):
        create_candidate_tables(self.conn)
        resolver = self.resolver()
        cursor = self.conn.cursor()

        candidate = {
            "mapName": "PalletTown",
            "scriptLabel": "PalletTownProbe",
            "trigger": {"type": "npc_click", "label": "TEXT_PROBE"},
            "confidence": "source-derived",
            "conditions": {},
            "actions": [],
        }
        insert_candidate(cursor, candidate, resolver)
        insert_ir_block(
            cursor,
            {
                "mapName": "CeruleanCity_2",
                "label": "CeruleanSupplement",
                "kind": "script",
                "features": {},
                "textRefs": [],
                "eventRefs": [],
                "itemRefs": [],
                "pokemonRefs": [],
                "movementRefs": [],
                "objectRefs": [],
                "battleRefs": [],
                "warpRefs": [],
                "rawAsm": "ret",
            },
            resolver,
        )
        for map_name in ("PalletTown", "GLOBAL"):
            insert_diagnostic(
                cursor,
                {
                    "mapName": map_name,
                    "scriptLabel": f"{map_name}Probe",
                    "status": "covered",
                    "reason": "test",
                    "details": {},
                },
                resolver,
            )

        self.assertEqual(
            self.conn.execute(
                "SELECT map_name, map_id FROM script_event_candidates"
            ).fetchall(),
            [("PalletTown", 0)],
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT map_name, map_id FROM script_event_ir_blocks"
            ).fetchall(),
            [("CeruleanCity_2", 3)],
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT map_name, map_id FROM script_event_candidate_diagnostics "
                "ORDER BY map_name"
            ).fetchall(),
            [("GLOBAL", None), ("PalletTown", 0)],
        )
        validate_normalized_script_tables(self.conn)
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
