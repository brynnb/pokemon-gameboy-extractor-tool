import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_audio_manifest import move_sound_rows
from export_moves import (
    AUDITED_MOVE_CONSTANTS,
    EXPECTED_MOVE_IDS,
    create_database,
    parse_move_constants,
    parse_move_data,
    parse_move_line,
    parse_move_names,
    parse_move_sounds,
    main as export_moves,
    validate_dependent_move_references,
    validate_move_sources,
    validate_moves_table,
)


class MoveExportTest(unittest.TestCase):
    def test_move_macro_parser_ignores_alignment_whitespace(self):
        move_name, move = parse_move_line(
            "\tmove HYDRO_PUMP ,NO_ADDITIONAL_EFFECT, 120 , WATER,80,   5 ; comment"
        )

        self.assertEqual(move_name, "HYDRO_PUMP")
        self.assertEqual(move["effect"], "NO_ADDITIONAL_EFFECT")
        self.assertEqual(move["power"], 120)
        self.assertEqual(move["type"], "WATER")
        self.assertEqual(move["accuracy"], 80)
        self.assertEqual(move["pp"], 5)

    def test_source_move_tables_cover_all_165_ids_and_audited_moves(self):
        move_constants = parse_move_constants()
        moves_data, _ = parse_move_data()
        move_names = parse_move_names()
        move_sounds = parse_move_sounds()

        validate_move_sources(move_constants, moves_data, move_names, move_sounds)

        self.assertEqual(
            {move_id for name, move_id in move_constants.items() if name != "NO_MOVE"},
            EXPECTED_MOVE_IDS,
        )
        self.assertEqual(set(move_names), EXPECTED_MOVE_IDS)
        self.assertEqual(set(move_sounds), EXPECTED_MOVE_IDS)
        self.assertEqual(len(moves_data), 165)
        self.assertTrue(AUDITED_MOVE_CONSTANTS <= set(moves_data))

    def test_moves_schema_declares_source_constants_as_text(self):
        conn = create_database(":memory:")
        try:
            declared_types = {
                row[1]: row[2] for row in conn.execute("PRAGMA table_info(moves)")
            }
        finally:
            conn.close()

        self.assertEqual(declared_types["effect"], "TEXT")
        self.assertEqual(declared_types["constant_name"], "TEXT")
        self.assertEqual(declared_types["battle_animation"], "TEXT")
        self.assertEqual(declared_types["battle_sound"], "TEXT")

    def test_export_writes_every_move_and_the_previously_missing_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pokemon.db"
            with redirect_stdout(StringIO()):
                export_moves(db_path)

            conn = sqlite3.connect(db_path)
            try:
                rows = conn.execute(
                    "SELECT id, short_name FROM moves ORDER BY id"
                ).fetchall()
            finally:
                conn.close()

        self.assertEqual({row[0] for row in rows}, EXPECTED_MOVE_IDS)
        exported_names = {row[1] for row in rows}
        self.assertTrue(AUDITED_MOVE_CONSTANTS <= exported_names)

    def test_moves_table_validator_rejects_a_partial_id_domain(self):
        conn = create_database(":memory:")
        try:
            conn.executemany(
                "INSERT INTO moves (id, constant_name, name, short_name) VALUES (?, ?, ?, ?)",
                (
                    (move_id, f"MOVE_{move_id}", f"MOVE {move_id}", f"MOVE_{move_id}")
                    for move_id in range(1, 165)
                ),
            )
            with self.assertRaisesRegex(ValueError, r"missing IDs \[165\]"):
                validate_moves_table(conn)
        finally:
            conn.close()

    def test_dependent_reference_validator_rejects_unknown_move_ids(self):
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE TABLE moves (id INTEGER PRIMARY KEY)")
            conn.executemany(
                "INSERT INTO moves (id) VALUES (?)", ((move_id,) for move_id in EXPECTED_MOVE_IDS)
            )
            conn.execute(
                "CREATE TABLE pokemon_learnset (id INTEGER PRIMARY KEY, move_id INTEGER)"
            )
            conn.execute("INSERT INTO pokemon_learnset (move_id) VALUES (166)")

            with self.assertRaisesRegex(ValueError, r"1 dangling"):
                validate_dependent_move_references(conn)
        finally:
            conn.close()

    def test_dependent_reference_validator_allows_only_the_source_unused_sentinel(self):
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE TABLE moves (id INTEGER PRIMARY KEY)")
            conn.execute(
                "CREATE TABLE pokemon_tmhm (id INTEGER PRIMARY KEY, move_name TEXT, move_id INTEGER)"
            )
            conn.execute(
                "INSERT INTO pokemon_tmhm (move_name, move_id) VALUES ('UNUSED', NULL)"
            )
            validate_dependent_move_references(conn)

            conn.execute(
                "INSERT INTO pokemon_tmhm (move_name, move_id) VALUES ('SURF', NULL)"
            )
            with self.assertRaisesRegex(ValueError, r"1 unexpectedly-null"):
                validate_dependent_move_references(conn)
        finally:
            conn.close()

    def test_audio_manifest_rejects_a_partial_moves_table(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "partial.db"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE moves (
                        id INTEGER PRIMARY KEY,
                        name TEXT,
                        short_name TEXT,
                        battle_sound TEXT,
                        battle_sound_pitch INTEGER,
                        battle_sound_tempo INTEGER
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO moves VALUES (1, 'POUND', 'POUND', 'SFX_POUND', 0, 128)"
                )
                conn.commit()
            finally:
                conn.close()

            with self.assertRaisesRegex(ValueError, r"must contain exactly 165 move IDs"):
                move_sound_rows({}, db_path=db_path)

    def test_audio_manifest_rejects_an_existing_database_without_moves(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "missing-moves.db"
            sqlite3.connect(db_path).close()

            with self.assertRaisesRegex(ValueError, r"missing the required moves table"):
                move_sound_rows({}, db_path=db_path)


if __name__ == "__main__":
    unittest.main()
