import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_audio_manifest import build_manifest
from export_pokemon import extract_cries


class AudioManifestTest(unittest.TestCase):
    def test_manifest_maps_core_music_and_sfx_paths(self):
        manifest = build_manifest()

        pallet = manifest["music"]["MUSIC_PALLET_TOWN"]
        self.assertEqual(pallet["path"], "/sound/pokemon/music/pallet_town.ogg")
        self.assertEqual(pallet["sourceFile"], "audio/music/pallettown.asm")
        self.assertEqual(pallet["channelCount"], 3)
        self.assertGreater(pallet["audioId"], 0)

        cut = manifest["sfx"]["SFX_CUT"]
        self.assertEqual(cut["path"], "/sound/pokemon/sfx/cut.ogg")
        self.assertEqual(cut["category"], "field_ui")

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

        self.assertGreaterEqual(len(move_sounds), 100)
        self.assertEqual(move_sounds["1"]["moveName"], "POUND")
        self.assertEqual(move_sounds["1"]["sfx"], "SFX_POUND")
        self.assertEqual(move_sounds["1"]["path"], "/sound/pokemon/sfx/pound.ogg")

        self.assertEqual(move_sounds["57"]["moveName"], "SURF")
        self.assertEqual(move_sounds["57"]["sfx"], "SFX_BATTLE_2C")

    def test_cries_keep_base_cry_identity(self):
        cries = extract_cries()
        self.assertEqual(cries["RHYDON"]["base_cry"], 0x11)
        self.assertEqual(cries["RHYDON"]["cry_pitch"], 0x00)
        self.assertEqual(cries["RHYDON"]["cry_length"], 0x80)

        manifest = build_manifest()
        rhydon = manifest["pokemonCries"]["RHYDON"]
        self.assertEqual(rhydon["baseCry"], "SFX_CRY_11")
        self.assertEqual(rhydon["basePath"], "/sound/pokemon/cries/cry_11.ogg")
        self.assertEqual(rhydon["path"], "/sound/pokemon/cries/species/rhydon.ogg")


if __name__ == "__main__":
    unittest.main()
