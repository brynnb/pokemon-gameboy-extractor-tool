import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokemon_gameboy_extractor.cli import configure_workspace, render_audio


class DistributionCliTests(unittest.TestCase):
    def test_configures_checkout_relative_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(configure_workspace(workspace), workspace)
                self.assertEqual(
                    os.environ["POKEMON_EXTRACTOR_PROJECT_ROOT"], str(workspace)
                )
                self.assertEqual(
                    os.environ["POKEMON_EXTRACTOR_GAME_DATA_ROOT"],
                    str(workspace / "pokemon-game-data"),
                )
                self.assertEqual(
                    os.environ["POKEMON_EXTRACTOR_DB"], str(workspace / "pokemon.db")
                )
                self.assertEqual(
                    os.environ["POKEMON_EXTRACTOR_AUDIO_DIR"],
                    str(workspace / "build" / "audio"),
                )

    def test_preserves_explicit_output_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            custom_database = str(Path(directory) / "custom.sqlite")
            with mock.patch.dict(
                os.environ,
                {"POKEMON_EXTRACTOR_DB": custom_database},
                clear=True,
            ):
                configure_workspace(Path(directory) / "workspace")
                self.assertEqual(os.environ["POKEMON_EXTRACTOR_DB"], custom_database)

    def test_audio_console_wrapper_propagates_renderer_failures(self):
        renderer = SimpleNamespace(main=lambda: 7)
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {}, clear=True):
                with mock.patch.dict(sys.modules, {"render_audio_assets": renderer}):
                    with mock.patch(
                        "pokemon_gameboy_extractor.cli.configure_workspace"
                    ) as configure:
                        self.assertEqual(render_audio(), 7)
        configure.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
