#!/usr/bin/env python3
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_wild_encounters import (
    create_tables,
    export_grass_water_encounters,
    load_wild_data_pointer_map,
    parse_wild_map_definition,
    validate_wild_encounters,
)


COMMON_AROUND_CONDITIONALS = """\
OddSourceWildMons:
\tdef_grass_wildmons 25
\tdb  1, RATTATA
IF DEF(_RED)
\tdb  2, EKANS
ENDC
IF DEF(_BLUE)
\tdb  2, SANDSHREW
ENDC
\tdb  3, PIDGEY
\tdb  4, SPEAROW
\tdb  5, ZUBAT
\tdb  6, DITTO
IF DEF(_RED)
\tdb  7, ARBOK
ENDC
IF DEF(_BLUE)
\tdb  7, SANDSLASH
ENDC
\tdb  8, GEODUDE
\tdb  9, ONIX
\tdb 10, CLEFAIRY
\tend_grass_wildmons

\tdef_water_wildmons 0
\tend_water_wildmons
"""


def zero_encounter_source(label):
    return f"""\
{label}:
\tdef_grass_wildmons 0
\tend_grass_wildmons
\tdef_water_wildmons 0
\tend_water_wildmons
"""


class EncounterConditionalParsingTest(unittest.TestCase):
    def test_common_rows_compile_into_complete_release_tables(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "filename-is-not-a-map-name.asm"
            source_path.write_text(COMMON_AROUND_CONDITIONALS, encoding="utf-8")
            label, encounters = parse_wild_map_definition(source_path)

        self.assertEqual(label, "OddSourceWildMons")
        red = [row for row in encounters if row["version"] == "red"]
        blue = [row for row in encounters if row["version"] == "blue"]

        self.assertEqual([row["slot_index"] for row in red], list(range(1, 11)))
        self.assertEqual([row["slot_index"] for row in blue], list(range(1, 11)))
        self.assertEqual(red[1]["pokemon_name"], "EKANS")
        self.assertEqual(blue[1]["pokemon_name"], "SANDSHREW")
        self.assertEqual(red[6]["pokemon_name"], "ARBOK")
        self.assertEqual(blue[6]["pokemon_name"], "SANDSLASH")
        self.assertEqual(
            [row["pokemon_name"] for row in red[7:]],
            [row["pokemon_name"] for row in blue[7:]],
        )

    def test_incomplete_release_table_is_rejected(self):
        incomplete = COMMON_AROUND_CONDITIONALS.replace("\tdb 10, CLEFAIRY\n", "")
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "incomplete.asm"
            source_path.write_text(incomplete, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "has 9 slots; expected 10"):
                parse_wild_map_definition(source_path)


class WildDataPointerMappingTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.maps_dir = self.root / "maps"
        self.maps_dir.mkdir()
        self.constants_path = self.root / "map_constants.asm"
        self.pointer_path = self.root / "grass_water.asm"

        self.constants_path.write_text(
            """\
map_const FIRST_MAP, 1, 1
map_const SECOND_MAP, 1, 1
map_const CAVE_B1F, 1, 1
""",
            encoding="utf-8",
        )
        self.pointer_path.write_text(
            """\
WildDataPointers:
\ttable_width 2
\tdw OddSourceWildMons ; deliberately misleading: CAVE_B1F
\tdw OddSourceWildMons
\tdw EmptyBasementWildMons
\tassert_table_length NUM_MAPS
\tdw -1
""",
            encoding="utf-8",
        )
        (self.maps_dir / "not-first-or-second.asm").write_text(
            COMMON_AROUND_CONDITIONALS,
            encoding="utf-8",
        )
        (self.maps_dir / "not-the-basement.asm").write_text(
            zero_encounter_source("EmptyBasementWildMons"),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_pointer_indices_are_authoritative_and_repeated_labels_are_retained(self):
        pointers = load_wild_data_pointer_map(
            self.pointer_path,
            self.constants_path,
        )
        self.assertEqual(pointers["OddSourceWildMons"], ["FIRST_MAP", "SECOND_MAP"])
        self.assertEqual(pointers["EmptyBasementWildMons"], ["CAVE_B1F"])

    def test_export_uses_canonical_map_relationships_not_filenames(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE maps (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE)")
        conn.executemany(
            "INSERT INTO maps (id, name) VALUES (?, ?)",
            [(10, "FIRST_MAP"), (11, "SECOND_MAP"), (12, "CAVE_B1F")],
        )
        cursor = create_tables(conn)

        count, map_count = export_grass_water_encounters(
            cursor,
            {"FIRST_MAP": 10, "SECOND_MAP": 11, "CAVE_B1F": 12},
            self.maps_dir,
            self.pointer_path,
            self.constants_path,
        )
        validate_wild_encounters(cursor)

        self.assertEqual(count, 40)
        self.assertEqual(map_count, 2)
        self.assertEqual(
            cursor.execute(
                "SELECT DISTINCT map_name, map_id FROM wild_encounters ORDER BY map_id"
            ).fetchall(),
            [("FIRST_MAP", 10), ("SECOND_MAP", 11)],
        )
        self.assertEqual(
            cursor.execute(
                "SELECT DISTINCT source_label FROM wild_encounters"
            ).fetchall(),
            [("OddSourceWildMons",)],
        )
        conn.close()


if __name__ == "__main__":
    unittest.main()
