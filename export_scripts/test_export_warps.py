from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_warps import (
    WarpExportError,
    collect_source_warps,
    create_table,
    insert_warps,
    parse_source_warps,
    validate_warps,
)
from map_references import CanonicalMapResolver, MapReferenceError


class WarpRelationshipTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.objects = self.root / "objects"
        self.headers = self.root / "headers"
        self.objects.mkdir()
        self.headers.mkdir()
        (self.headers / "AMap.asm").write_text(
            "map_header AMap, A_MAP, TEST, 0\n", encoding="utf-8"
        )
        (self.headers / "BMap.asm").write_text(
            "map_header BMap, B_MAP, TEST, 0\n", encoding="utf-8"
        )
        (self.objects / "AMap.asm").write_text(
            """def_warp_events
    warp_event 1, 2, B_MAP, 1
    warp_event 3, 4, LAST_MAP, 2
def_bg_events
""",
            encoding="utf-8",
        )
        (self.objects / "BMap.asm").write_text(
            """def_warp_events
    warp_event 5, 6, A_MAP, 1
def_bg_events
""",
            encoding="utf-8",
        )
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute(
            "CREATE TABLE maps (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE)"
        )
        self.conn.executemany(
            "INSERT INTO maps VALUES (?, ?)", [(0, "A_MAP"), (1, "B_MAP")]
        )
        self.resolver = CanonicalMapResolver.from_connection(
            self.conn, self.headers
        )

    def tearDown(self):
        self.conn.close()
        self.temporary.cleanup()

    def test_fixed_and_dynamic_destinations_are_explicit_and_fk_backed(self):
        rows = collect_source_warps(
            self.resolver, objects_dir=self.objects, project_root=self.root
        )
        with self.conn:
            create_table(self.conn)
            insert_warps(self.conn, rows)

        result = validate_warps(
            self.conn,
            objects_dir=self.objects,
            map_headers_dir=self.headers,
            project_root=self.root,
        )
        self.assertEqual(
            result,
            {"warps": 3, "sourceRows": 3, "fixed": 2, "lastMap": 1},
        )
        self.assertEqual(
            self.conn.execute(
                """
                SELECT source_map_id, destination_kind, destination_map_id,
                       destination_x, destination_y
                FROM warps ORDER BY id
                """
            ).fetchall(),
            [
                (0, "fixed", 1, 5, 6),
                (0, "last-map", None, None, None),
                (1, "fixed", 0, 1, 2),
            ],
        )
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_unknown_fixed_destination_is_rejected(self):
        source = self.objects / "AMap.asm"
        source.write_text(
            "def_warp_events\n    warp_event 1, 2, MISSING_MAP, 1\n",
            encoding="utf-8",
        )
        with self.assertRaises(MapReferenceError):
            parse_source_warps(source, self.resolver, project_root=self.root)


if __name__ == "__main__":
    unittest.main()
