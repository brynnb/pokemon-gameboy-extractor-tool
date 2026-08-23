from pathlib import Path
import sqlite3
import sys
import unittest
from contextlib import closing

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_viewer_data import deterministic_generation_time


class ViewerManifestTest(unittest.TestCase):
    def test_generation_time_comes_from_reproducible_run_metadata(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.execute("CREATE TABLE extraction_runs (source_date_epoch INTEGER)")
            conn.execute("INSERT INTO extraction_runs VALUES (1700000000)")
            generated_at, epoch = deterministic_generation_time(conn)
        self.assertEqual(epoch, 1700000000)
        self.assertEqual(generated_at, "2023-11-14T22:13:20+00:00")

    def test_requires_exactly_one_run(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.execute("CREATE TABLE extraction_runs (source_date_epoch INTEGER)")
            with self.assertRaisesRegex(ValueError, "found 0"):
                deterministic_generation_time(conn)


if __name__ == "__main__":
    unittest.main()
