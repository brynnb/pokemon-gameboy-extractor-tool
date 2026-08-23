import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_audio_manifest import (
    build_manifest,
    relational_pokemon_name,
    validate_audio_tables,
    write_audio_tables,
)
from export_pokemon import extract_cries


class AudioManifestTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path_patch = mock.patch(
            "export_audio_manifest.DB_PATH",
            Path(self.temp_dir.name) / "database-does-not-exist.db",
        )
        self.db_path_patch.start()

    def tearDown(self):
        self.db_path_patch.stop()
        self.temp_dir.cleanup()

    def test_manifest_maps_core_music_and_sfx_paths(self):
        manifest = build_manifest()

        pallet = manifest["music"]["MUSIC_PALLET_TOWN"]
        self.assertEqual(pallet["path"], "/sound/pokemon/music/pallet_town.ogg")
        self.assertEqual(pallet["sourceFile"], "audio/music/pallettown.asm")
        self.assertEqual(pallet["channelCount"], 3)
        self.assertGreater(pallet["audioId"], 0)

        cut = manifest["sfx"]["SFX_CUT"]
        self.assertEqual(cut["path"], "/sound/pokemon/sfx/cut.ogg")
        self.assertEqual(cut["masterPath"], "/sound/pokemon/sfx/cut.flac")
        self.assertEqual(cut["sourceFile"], "audio/sfx/cut_1.asm")
        self.assertEqual(cut["category"], "field_ui")

        self.assertEqual(
            [row["constant"] for row in manifest["sfx"].values() if not row["sourceFile"]],
            [],
        )

    def test_manifest_includes_source_map_music(self):
        manifest = build_manifest()
        map_music = manifest["mapMusic"]

        self.assertGreaterEqual(len(map_music), 200)

        by_map = {row["map_constant"]: row for row in map_music}
        self.assertEqual(by_map["PALLET_TOWN"]["map_id"], 0)
        self.assertEqual(by_map["PALLET_TOWN"]["music_constant"], "MUSIC_PALLET_TOWN")
        self.assertEqual(by_map["PALLET_TOWN"]["path"], "/sound/pokemon/music/pallet_town.ogg")

        self.assertEqual(by_map["ROUTE_1"]["map_id"], 12)
        self.assertEqual(by_map["ROUTE_1"]["music_constant"], "MUSIC_ROUTES1")
        self.assertEqual(by_map["OAKS_LAB"]["music_constant"], "MUSIC_OAKS_LAB")

    def test_manifest_includes_source_move_sounds(self):
        manifest = build_manifest()
        move_sounds = manifest["moveSounds"]

        self.assertEqual(len(move_sounds), 165)
        self.assertEqual(move_sounds["1"]["moveName"], "POUND")
        self.assertEqual(move_sounds["1"]["sfx"], "SFX_POUND")
        self.assertEqual(move_sounds["1"]["basePath"], "/sound/pokemon/sfx/pound.ogg")
        self.assertEqual(move_sounds["1"]["path"], "/sound/pokemon/moves/001-pound.ogg")
        self.assertEqual(move_sounds["1"]["masterPath"], "/sound/pokemon/moves/001-pound.flac")

        self.assertEqual(move_sounds["57"]["moveName"], "SURF")
        self.assertEqual(move_sounds["57"]["sfx"], "SFX_BATTLE_2C")

    def test_cries_keep_base_cry_identity(self):
        cries = extract_cries()
        self.assertEqual(cries["RHYDON"]["base_cry"], 0x11)
        self.assertEqual(cries["RHYDON"]["cry_pitch"], 0x00)
        self.assertEqual(cries["RHYDON"]["cry_length"], 0x80)

        manifest = build_manifest()
        self.assertEqual(len(manifest["pokemonCries"]), 151)
        self.assertEqual(len(manifest["indexedCries"]), 190)
        self.assertEqual(
            sum(row["isGlitchSlot"] for row in manifest["indexedCries"].values()),
            39,
        )
        rhydon = manifest["pokemonCries"]["RHYDON"]
        self.assertEqual(rhydon["internalIndex"], 1)
        self.assertEqual(rhydon["baseCry"], "SFX_CRY_11")
        self.assertEqual(rhydon["basePath"], "/sound/pokemon/cries/cry_11.ogg")
        self.assertEqual(rhydon["path"], "/sound/pokemon/cries/species/rhydon.ogg")

    def test_normalized_audio_tables_preserve_all_relationships(self):
        manifest = build_manifest()
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("CREATE TABLE maps (id INTEGER PRIMARY KEY, name TEXT UNIQUE)")
        conn.execute("CREATE TABLE pokemon (id INTEGER PRIMARY KEY, name TEXT UNIQUE)")
        conn.execute("CREATE TABLE moves (id INTEGER PRIMARY KEY, name TEXT UNIQUE)")
        conn.executemany(
            "INSERT INTO maps (id, name) VALUES (?, ?)",
            sorted(
                {
                    (row["map_id"], row["map_constant"])
                    for row in manifest["mapMusic"]
                }
            ),
        )
        canonical_names = sorted(
            relational_pokemon_name(name) for name in manifest["pokemonCries"]
        )
        conn.executemany(
            "INSERT INTO pokemon (id, name) VALUES (?, ?)",
            enumerate(canonical_names, start=1),
        )
        conn.executemany(
            "INSERT INTO moves (id, name) VALUES (?, ?)",
            (
                (int(move_id), row["moveName"])
                for move_id, row in manifest["moveSounds"].items()
            ),
        )

        write_audio_tables(conn, manifest)
        validate_audio_tables(conn)

        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM audio_assets").fetchone()[0],
            561,
        )
        self.assertEqual(
            conn.execute(
                "SELECT frequency_modifier, tempo_modifier FROM audio_assets "
                "WHERE asset_key = 'move:001'"
            ).fetchone(),
            (0, 128),
        )
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM pokemon_cry_assets WHERE pokemon_id IS NULL"
            ).fetchone()[0],
            39,
        )
        conn.close()


if __name__ == "__main__":
    unittest.main()
