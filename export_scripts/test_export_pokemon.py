import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_pokemon import (
    create_tables,
    insert_evolutions,
    parse_evolutions,
    validate_pokemon_default_moves,
)


EVOLUTION_FIXTURE = """
EeveeEvosMoves:
; Evolutions
    db EVOLVE_ITEM, FIRE_STONE, 1, FLAREON
    db EVOLVE_ITEM, THUNDER_STONE, 1, JOLTEON
    db EVOLVE_ITEM, WATER_STONE, 1, VAPOREON
    db 0
; Learnset
    db 0

KadabraEvosMoves:
; Evolutions
    db EVOLVE_TRADE, 1, ALAKAZAM
    db 0
; Learnset
    db 0

BulbasaurEvosMoves:
; Evolutions
    db EVOLVE_LEVEL, 16, IVYSAUR
    db 0
; Learnset
    db 0
"""


class EvolutionParserTest(unittest.TestCase):
    def test_preserves_all_branches_methods_levels_items_and_source_order(self):
        rows = parse_evolutions(EVOLUTION_FIXTURE)

        eevee_rows = [row for row in rows if row["source_name"] == "EEVEE"]
        self.assertEqual(
            [
                (
                    row["target_name"],
                    row["method"],
                    row["level"],
                    row["item_constant"],
                    row["source_order"],
                )
                for row in eevee_rows
            ],
            [
                ("FLAREON", "item", 1, "FIRE_STONE", 1),
                ("JOLTEON", "item", 1, "THUNDER_STONE", 2),
                ("VAPOREON", "item", 1, "WATER_STONE", 3),
            ],
        )
        self.assertEqual(
            next(row for row in rows if row["source_name"] == "KADABRA")["method"],
            "trade",
        )
        self.assertEqual(
            next(row for row in rows if row["source_name"] == "BULBASAUR")["level"],
            16,
        )

    def test_rejects_an_unknown_evolution_row_instead_of_silently_losing_it(self):
        content = """
PikachuEvosMoves:
; Evolutions
    db EVOLVE_FRIENDSHIP, 1, RAICHU
    db 0
"""
        with self.assertRaisesRegex(ValueError, "Unsupported evolution row"):
            parse_evolutions(content)


class EvolutionRelationshipTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, short_name TEXT NOT NULL UNIQUE)"
        )
        self.conn.execute(
            """CREATE TABLE moves (
                id INTEGER PRIMARY KEY,
                constant_name TEXT NOT NULL UNIQUE,
                short_name TEXT NOT NULL UNIQUE
            )"""
        )
        self.conn.executemany(
            "INSERT INTO moves VALUES (?, ?, ?)",
            [(1, "POUND", "POUND"), (33, "TACKLE", "TACKLE")],
        )
        create_tables(self.conn)

        species = [
            (1, "EEVEE"),
            (2, "FLAREON"),
            (3, "JOLTEON"),
            (4, "VAPOREON"),
            (5, "KADABRA"),
            (6, "ALAKAZAM"),
            (7, "BULBASAUR"),
            (8, "IVYSAUR"),
        ]
        self.conn.executemany(
            """
            INSERT INTO pokemon (
                id, name, hp, atk, def, spd, spc, type_1, type_2, catch_rate, base_exp
            ) VALUES (?, ?, 1, 1, 1, 1, 1, 'NORMAL', 'NORMAL', 1, 1)
            """,
            species,
        )
        self.conn.executemany(
            "INSERT INTO items (id, short_name) VALUES (?, ?)",
            [(10, "FIRE_STONE"), (11, "THUNDER_STONE"), (12, "WATER_STONE")],
        )

    def tearDown(self):
        self.conn.close()

    def test_inserts_normalized_relationships_with_valid_foreign_keys(self):
        pokemon_ids = dict(self.conn.execute("SELECT name, id FROM pokemon"))
        item_ids = dict(self.conn.execute("SELECT short_name, id FROM items"))
        insert_evolutions(
            self.conn.cursor(), parse_evolutions(EVOLUTION_FIXTURE), pokemon_ids, item_ids
        )

        rows = self.conn.execute(
            """
            SELECT source.name, target.name, evolution.method, evolution.level,
                   item.short_name, evolution.source_order
            FROM pokemon_evolutions AS evolution
            JOIN pokemon AS source ON source.id = evolution.source_pokemon_id
            JOIN pokemon AS target ON target.id = evolution.target_pokemon_id
            LEFT JOIN items AS item ON item.id = evolution.item_id
            ORDER BY source.name, evolution.source_order
            """
        ).fetchall()

        self.assertIn(("EEVEE", "FLAREON", "item", 1, "FIRE_STONE", 1), rows)
        self.assertIn(("EEVEE", "JOLTEON", "item", 1, "THUNDER_STONE", 2), rows)
        self.assertIn(("EEVEE", "VAPOREON", "item", 1, "WATER_STONE", 3), rows)
        self.assertIn(("KADABRA", "ALAKAZAM", "trade", 1, None, 1), rows)
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_default_move_ids_are_typed_fk_backed_and_normalized(self):
        self.conn.execute(
            """
            UPDATE pokemon
            SET default_move_1_id = 1, default_move_1_name = 'POUND',
                default_move_2_id = 33, default_move_2_name = 'TACKLE'
            WHERE name = 'EEVEE'
            """
        )
        eevee_id = self.conn.execute(
            "SELECT id FROM pokemon WHERE name = 'EEVEE'"
        ).fetchone()[0]
        self.conn.executemany(
            "INSERT INTO pokemon_default_moves VALUES (?, ?, ?, ?)",
            [(eevee_id, 1, 1, "POUND"), (eevee_id, 2, 33, "TACKLE")],
        )
        self.assertEqual(validate_pokemon_default_moves(self.conn), 2)
        column_types = {
            row[1]: row[2] for row in self.conn.execute("PRAGMA table_info(pokemon)")
        }
        self.assertEqual(column_types["default_move_1_id"], "INTEGER")
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
