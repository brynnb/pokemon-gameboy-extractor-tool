import contextlib
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import export_map


class MapExportIntegrityTest(unittest.TestCase):
    def test_map_export_uses_resolved_relationships_and_portable_paths(self):
        original_db_path = export_map.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                export_map.DB_PATH = Path(temp_dir) / "pokemon.db"
                with contextlib.redirect_stdout(io.StringIO()):
                    export_map.main()

                with contextlib.closing(sqlite3.connect(export_map.DB_PATH)) as conn:
                    self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
                    self.assertEqual(
                        conn.execute("SELECT COUNT(*) FROM tilesets").fetchone()[0],
                        24,
                    )
                    self.assertEqual(
                        conn.execute(
                            "SELECT COUNT(*) FROM tilesets WHERE source_tileset_id IS NOT NULL"
                        ).fetchone()[0],
                        5,
                    )
                    self.assertEqual(
                        conn.execute(
                            "SELECT name, grass_tile_id FROM tilesets WHERE grass_tile_id IS NOT NULL ORDER BY id"
                        ).fetchall(),
                        [("OVERWORLD", 0x52), ("FOREST", 0x20), ("PLATEAU", 0x45)],
                    )
                    self.assertEqual(
                        conn.execute(
                            """
                            SELECT COUNT(*)
                            FROM tilesets
                            WHERE blockset_path LIKE '/%' OR tileset_path LIKE '/%'
                            """
                        ).fetchone()[0],
                        0,
                    )
                    self.assertEqual(
                        conn.execute(
                            """
                            SELECT COUNT(*)
                            FROM map_connections
                            WHERE typeof(from_map_id) != 'integer'
                               OR typeof(to_map_id) != 'integer'
                            """
                        ).fetchone()[0],
                        0,
                    )
                    self.assertEqual(
                        conn.execute(
                            "SELECT COUNT(*) FROM maps WHERE is_overworld = 1"
                        ).fetchone()[0],
                        36,
                    )
        finally:
            export_map.DB_PATH = original_db_path


if __name__ == "__main__":
    unittest.main()
