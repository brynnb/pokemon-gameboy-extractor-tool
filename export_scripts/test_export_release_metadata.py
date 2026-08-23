import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import export_release_metadata as metadata


EXTRACTOR_REVISION = "1" * 40
SOURCE_REVISION = "2" * 40
SOURCE_DATE_EPOCH = 1_700_000_000


class ReleaseMetadataExportTest(unittest.TestCase):
    def make_source_tree(self, root: Path):
        source_root = root / "source"
        (source_root / "assets").mkdir(parents=True)
        (source_root / "data").mkdir()
        (source_root / ".git").mkdir()
        (source_root / "alpha.asm").write_text("Alpha::\n\tdb 1\n", encoding="utf-8")
        (source_root / "assets" / "sprite.png").write_bytes(b"\x89PNG\r\n")
        (source_root / "data" / "block.bin").write_bytes(b"\x00\x01\x02")
        (source_root / ".git" / "ignored").write_text("not source", encoding="utf-8")
        return source_root

    def make_connection(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            """
            CREATE TABLE dialogue_text (
                id INTEGER PRIMARY KEY,
                label TEXT NOT NULL,
                source_file TEXT NOT NULL
            );
            INSERT INTO dialogue_text VALUES (1, 'AlphaText', 'alpha.asm');
            INSERT INTO dialogue_text VALUES (2, 'UnknownText', 'missing.asm');

            CREATE TABLE tilesets (
                id INTEGER PRIMARY KEY,
                blockset_path TEXT,
                tileset_path TEXT
            );
            INSERT INTO tilesets VALUES (7, 'source/data/block.bin', NULL);

            CREATE TABLE script_rows (
                id INTEGER PRIMARY KEY,
                source_json TEXT NOT NULL
            );
            INSERT INTO script_rows VALUES (
                4,
                '{"source":{"scriptPath":"source/alpha.asm"}}'
            );
            """
        )
        return conn

    def export(self, conn, project_root, source_root, *, epoch=SOURCE_DATE_EPOCH):
        return metadata.export_release_metadata(
            conn,
            project_root=project_root,
            source_root=source_root,
            extractor_revision=EXTRACTOR_REVISION,
            source_revision=SOURCE_REVISION,
            source_date_epoch=epoch,
        )

    def snapshot(self, conn):
        result = {}
        for table in sorted(metadata.OWN_TABLES):
            columns = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            order = ", ".join(f'"{row[1]}"' for row in columns)
            result[table] = conn.execute(f'SELECT * FROM "{table}" ORDER BY {order}').fetchall()
        return result

    def test_exports_deterministic_releases_catalog_and_entity_provenance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            source_root = self.make_source_tree(project_root)
            conn = self.make_connection()
            self.addCleanup(conn.close)

            first_run = self.export(conn, project_root, source_root)
            first_snapshot = self.snapshot(conn)
            metadata.validate_release_metadata(conn)

            self.assertEqual(len(first_run), 64)
            self.assertEqual(
                conn.execute(
                    "SELECT release_code, build_define FROM game_releases ORDER BY source_order"
                ).fetchall(),
                [("red", "_RED"), ("blue", "_BLUE")],
            )
            self.assertEqual(
                conn.execute("SELECT path FROM source_files ORDER BY path").fetchall(),
                [
                    ("source/alpha.asm",),
                    ("source/assets/sprite.png",),
                    ("source/data/block.bin",),
                ],
            )
            self.assertEqual(
                conn.execute(
                    """
                    SELECT entity_table, row_count
                    FROM extracted_tables ORDER BY entity_table
                    """
                ).fetchall(),
                [("dialogue_text", 2), ("script_rows", 1), ("tilesets", 1)],
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(DISTINCT entity_table) FROM table_provenance"
                ).fetchone()[0],
                3,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT path, file_type FROM source_files ORDER BY path"
                ).fetchall(),
                [
                    ("source/alpha.asm", "assembly"),
                    ("source/assets/sprite.png", "image"),
                    ("source/data/block.bin", "binary"),
                ],
            )
            self.assertEqual(
                conn.execute(
                    """
                    SELECT entity_table, entity_key, source_path, source_column, relationship
                    FROM entity_provenance ORDER BY entity_table
                    """
                ).fetchall(),
                [
                    (
                        "dialogue_text",
                        '{"id":1}',
                        "source/alpha.asm",
                        "source_file",
                        "direct-column",
                    ),
                    (
                        "script_rows",
                        '{"id":4}',
                        "source/alpha.asm",
                        "source_json",
                        "json-column",
                    ),
                    (
                        "tilesets",
                        '{"id":7}',
                        "source/data/block.bin",
                        "blockset_path",
                        "direct-column",
                    ),
                ],
            )
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

            second_run = self.export(conn, project_root, source_root)
            self.assertEqual(second_run, first_run)
            self.assertEqual(self.snapshot(conn), first_snapshot)

    def test_source_content_and_epoch_change_run_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            source_root = self.make_source_tree(project_root)
            conn = self.make_connection()
            self.addCleanup(conn.close)

            first_run = self.export(conn, project_root, source_root)
            (source_root / "alpha.asm").write_text("Alpha::\n\tdb 2\n", encoding="utf-8")
            changed_content_run = self.export(conn, project_root, source_root)
            changed_epoch_run = self.export(
                conn,
                project_root,
                source_root,
                epoch=SOURCE_DATE_EPOCH + 1,
            )

            self.assertNotEqual(changed_content_run, first_run)
            self.assertNotEqual(changed_epoch_run, changed_content_run)

    def test_source_date_epoch_environment_is_honored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            source_root = self.make_source_tree(project_root)
            conn = self.make_connection()
            self.addCleanup(conn.close)

            with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "12345"}):
                run_id = metadata.export_release_metadata(
                    conn,
                    project_root=project_root,
                    source_root=source_root,
                    extractor_revision=EXTRACTOR_REVISION,
                    source_revision=SOURCE_REVISION,
                )

            self.assertEqual(
                conn.execute(
                    "SELECT source_date_epoch FROM extraction_runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0],
                12345,
            )
            self.assertEqual(
                conn.execute("SELECT applied_epoch FROM schema_metadata").fetchone()[0],
                12345,
            )

    def test_extractor_fingerprint_tracks_generator_changes_not_generated_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            source_root = project_root / "source"
            source_root.mkdir()
            (source_root / "alpha.asm").write_text("db 1\n", encoding="utf-8")
            generator = project_root / "generator.py"
            generator.write_text("VERSION = 1\n", encoding="utf-8")
            generated = project_root / "pokemon.db"
            generated.write_bytes(b"old generated output")
            subprocess.run(["git", "init", "-q"], cwd=project_root, check=True)
            subprocess.run(["git", "add", "."], cwd=project_root, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Extractor Test",
                    "-c",
                    "user.email=extractor@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                cwd=project_root,
                check=True,
            )

            clean_hash, clean_dirty = metadata.extractor_worktree_state(
                project_root, source_root
            )
            self.assertFalse(clean_dirty)

            generator.write_text("VERSION = 2\n", encoding="utf-8")
            changed_hash, changed_dirty = metadata.extractor_worktree_state(
                project_root, source_root
            )
            self.assertTrue(changed_dirty)
            self.assertNotEqual(changed_hash, clean_hash)

            subprocess.run(
                ["git", "restore", "generator.py"], cwd=project_root, check=True
            )
            generated.write_bytes(b"different generated output")
            staged = project_root / (
                ".pokemon.db." + ("a" * 32) + ".stage"
            )
            staged.write_bytes(b"transient generated output")
            generated_hash, generated_dirty = metadata.extractor_worktree_state(
                project_root, source_root
            )
            self.assertFalse(generated_dirty)
            self.assertEqual(generated_hash, clean_hash)

    def test_constraints_and_validator_reject_tampering(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            source_root = self.make_source_tree(project_root)
            conn = self.make_connection()
            self.addCleanup(conn.close)
            run_id = self.export(conn, project_root, source_root)

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE source_files SET path = '/host/file.asm' "
                    "WHERE run_id = ? AND path = 'source/alpha.asm'",
                    (run_id,),
                )

            conn.execute(
                "UPDATE source_files SET sha256 = ? "
                "WHERE run_id = ? AND path = 'source/alpha.asm'",
                ("0" * 64, run_id),
            )
            with self.assertRaisesRegex(ValueError, "tree hash"):
                metadata.validate_release_metadata(conn)

    def test_validator_requires_complete_provenance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            source_root = self.make_source_tree(project_root)
            conn = self.make_connection()
            self.addCleanup(conn.close)
            self.export(conn, project_root, source_root)

            conn.execute(
                "DELETE FROM entity_provenance WHERE entity_table = 'dialogue_text'"
            )
            with self.assertRaisesRegex(ValueError, "provenance"):
                metadata.validate_release_metadata(conn)

            self.export(conn, project_root, source_root)
            conn.execute(
                "DELETE FROM table_provenance WHERE entity_table = 'tilesets'"
            )
            with self.assertRaisesRegex(ValueError, "Table source-set provenance"):
                metadata.validate_release_metadata(conn)


if __name__ == "__main__":
    unittest.main()
