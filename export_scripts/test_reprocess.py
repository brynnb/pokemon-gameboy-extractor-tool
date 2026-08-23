import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reprocess


def output_record(root, name, environment_name):
    return {
        "environment_name": environment_name,
        "final": root / name,
        "staging": root / f".{name}.token.stage",
        "backup": root / f".{name}.token.backup",
        "is_directory": False,
    }


class AtomicPublicationTests(unittest.TestCase):
    def test_database_is_published_last_and_backups_are_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = output_record(root, "pokemon.db", "POKEMON_EXTRACTOR_DB")
            manifest = output_record(
                root,
                "manifest.json",
                "POKEMON_EXTRACTOR_AUDIO_MANIFEST",
            )
            for output in (database, manifest):
                output["final"].write_text("old", encoding="utf-8")
                output["staging"].write_text("new", encoding="utf-8")

            replacements = []
            real_replace = os.replace

            def recording_replace(source, destination):
                replacements.append((Path(source), Path(destination)))
                real_replace(source, destination)

            with mock.patch.object(reprocess.os, "replace", recording_replace):
                reprocess.publish_outputs([database, manifest])

            installed_destinations = [
                destination
                for source, destination in replacements
                if source in {database["staging"], manifest["staging"]}
            ]
            self.assertEqual(
                installed_destinations,
                [manifest["final"], database["final"]],
            )
            for output in (database, manifest):
                self.assertEqual(output["final"].read_text(encoding="utf-8"), "new")
                self.assertFalse(output["backup"].exists())


class ProjectNeutralReleaseTests(unittest.TestCase):
    def test_database_scan_checks_every_text_column(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.executescript(
            """
            CREATE TABLE ordinary_data (id INTEGER PRIMARY KEY, payload TEXT, note BLOB);
            INSERT INTO ordinary_data VALUES (1, '{"project":"generic"}', X'00');
            """
        )
        reprocess.validate_neutral_database(conn)

        conn.execute(
            "UPDATE ordinary_data SET payload = ? WHERE id = 1",
            ('{"runtime":"CaptureQuest"}',),
        )
        with self.assertRaisesRegex(
            reprocess.PipelineError,
            r"ordinary_data\.payload \(1\)",
        ):
            reprocess.validate_neutral_database(conn)

    def test_generated_file_scan_covers_nested_catalogs_and_skips_binary_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / ".viewer.token.stage"
            (staged / "nested").mkdir(parents=True)
            (staged / "nested" / "catalog.json").write_text(
                '{"project":"generic"}', encoding="utf-8"
            )
            (staged / "sprite.png").write_bytes(b"capturequest")
            outputs = [
                {
                    "environment_name": "POKEMON_EXTRACTOR_VIEWER_DATA_DIR",
                    "final": root / "viewer",
                    "staging": staged,
                    "backup": root / ".viewer.token.backup",
                    "is_directory": True,
                }
            ]
            reprocess.validate_neutral_generated_files(outputs)

            (staged / "nested" / "catalog.json").write_text(
                '{"project":"capture-quest"}', encoding="utf-8"
            )
            with self.assertRaisesRegex(
                reprocess.PipelineError,
                "downstream-project vocabulary",
            ):
                reprocess.validate_neutral_generated_files(outputs)

    def test_failed_publication_restores_every_previous_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = output_record(root, "pokemon.db", "POKEMON_EXTRACTOR_DB")
            manifest = output_record(
                root,
                "manifest.json",
                "POKEMON_EXTRACTOR_AUDIO_MANIFEST",
            )
            for output in (database, manifest):
                output["final"].write_text("old", encoding="utf-8")
                output["staging"].write_text("new", encoding="utf-8")

            real_replace = os.replace
            failed = False

            def fail_database_install_once(source, destination):
                nonlocal failed
                if source == database["staging"] and not failed:
                    failed = True
                    raise OSError("injected publication failure")
                real_replace(source, destination)

            with mock.patch.object(reprocess.os, "replace", fail_database_install_once):
                with self.assertRaisesRegex(
                    reprocess.PipelineError,
                    "Could not publish generated release",
                ):
                    reprocess.publish_outputs([database, manifest])

            for output in (database, manifest):
                self.assertEqual(output["final"].read_text(encoding="utf-8"), "old")
                self.assertFalse(output["backup"].exists())


if __name__ == "__main__":
    unittest.main()
