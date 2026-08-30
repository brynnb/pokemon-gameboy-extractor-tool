import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_audio_manifest import build_manifest
from render_audio_assets import (
    analyze_wav,
    encode_outputs,
    output_path,
    render_rom_asset,
    selected_assets,
    trim_wav_tail,
    validate_render_bundle,
    write_render_manifest,
)


RENDER_TOOLS_AVAILABLE = all(
    shutil.which(tool) for tool in ("rgbasm", "rgblink", "rgbfix", "gbsplay", "ffmpeg")
)


class AudioRendererTest(unittest.TestCase):
    def build_source_manifest(self):
        with mock.patch(
            "export_audio_manifest.DB_PATH", Path("/database-that-does-not-exist.db")
        ):
            return build_manifest()

    def test_all_selects_every_normalized_audio_asset(self):
        manifest = self.build_source_manifest()
        assets = selected_assets(
            manifest, {"music", "sfx", "base-cries", "cries", "moves"}
        )
        self.assertEqual(len(assets), 561)
        self.assertEqual(sum(row["renderKind"] == "cry" for row in assets), 190)
        self.assertEqual(sum(row["renderKind"] == "move" for row in assets), 165)
        pound = next(row for row in assets if row["assetKey"] == "move:001")
        self.assertEqual(pound["path"], "/sound/pokemon/moves/001-pound.ogg")
        self.assertEqual((pound["frequencyModifier"], pound["tempoModifier"]), (0, 128))

    def test_output_paths_cannot_escape_bundle(self):
        root = Path("/tmp/audio-output")
        self.assertEqual(
            output_path(root, "/sound/pokemon/music/test.ogg"),
            root / "sound/pokemon/music/test.ogg",
        )
        with self.assertRaises(ValueError):
            output_path(root, "/sound/../outside.ogg")

    def test_pcm_validation_and_tail_trimming(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.wav"
            sample_rate = 8000
            active_frames = 800
            silent_frames = 1600
            with wave.open(str(source), "wb") as handle:
                handle.setparams((2, 2, sample_rate, 0, "NONE", "not compressed"))
                samples = []
                for index in range(active_frames):
                    value = int(5000 * math.sin(index * math.pi / 16))
                    samples.extend((value, value))
                samples.extend((0, 0) * silent_frames)
                handle.writeframes(b"".join(struct.pack("<h", value) for value in samples))

            trimmed = trim_wav_tail(source, padding_seconds=0.01)
            analysis = analyze_wav(trimmed)
            self.assertLess(analysis["sampleFrames"], active_frames + silent_frames)
            self.assertGreater(analysis["peak"], 1000)
            self.assertGreater(analysis["rms"], 100)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
    def test_distribution_is_compact_mono_24khz(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.wav"
            with wave.open(str(source), "wb") as handle:
                handle.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
                samples = []
                for index in range(4800):
                    value = int(5000 * math.sin(index * math.pi / 16))
                    samples.extend((value, value))
                handle.writeframes(b"".join(struct.pack("<h", value) for value in samples))

            artifact = encode_outputs(
                source,
                root / "output",
                {
                    "assetKey": "test:compact",
                    "constant": "TEST_COMPACT",
                    "renderKind": "sfx",
                    "path": "/sound/pokemon/sfx/compact.ogg",
                    "masterPath": "/sound/pokemon/sfx/compact.flac",
                },
            )
            distribution = root / "output/sound/pokemon/sfx/compact.ogg"
            probe = json.loads(subprocess.check_output([
                "ffprobe", "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=sample_rate,channels", "-of", "json",
                str(distribution),
            ]))
            self.assertEqual(probe["streams"][0], {"sample_rate": "24000", "channels": 1})
            self.assertEqual(artifact["distribution"]["quality"], 1)

    @unittest.skipUnless(RENDER_TOOLS_AVAILABLE, "RGBDS/gbsplay/ffmpeg are required")
    def test_source_engine_render_is_non_silent_deterministic_and_modifier_aware(self):
        manifest = self.build_source_manifest()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = root / "audio_manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            pallet = {
                **manifest["music"]["MUSIC_PALLET_TOWN"],
                "assetKey": "music:MUSIC_PALLET_TOWN",
                "renderKind": "music",
            }
            first = render_rom_asset(
                pallet, root / "first", 1, 0, 22050, manifest_path
            )
            write_render_manifest(root / "first", manifest_path, [first], 22050)
            validation = validate_render_bundle(root / "first", manifest_path)
            self.assertEqual(validation, {"artifacts": 1, "expectedAssets": 561})
            with self.assertRaisesRegex(ValueError, "incomplete"):
                validate_render_bundle(
                    root / "first", manifest_path, require_complete=True
                )
            second = render_rom_asset(
                pallet, root / "second", 1, 0, 22050, manifest_path
            )
            self.assertGreater(first["peak"], 100)
            self.assertEqual(first["sampleFrames"], second["sampleFrames"])
            self.assertEqual(first["master"]["sha256"], second["master"]["sha256"])

            battle_sound = {
                **manifest["sfx"]["SFX_BATTLE_31"],
                "assetKey": "probe:SFX_BATTLE_31",
                "renderKind": "move",
                "frequencyModifier": 0,
                "tempoModifier": 64,
            }
            modified = render_rom_asset(
                battle_sound, root / "modified", 2, 0, 22050, manifest_path
            )
            battle_sound["frequencyModifier"] = 255
            source_modifier = render_rom_asset(
                battle_sound, root / "source-modifier", 2, 0, 22050, manifest_path
            )
            self.assertNotEqual(
                modified["master"]["sha256"], source_modifier["master"]["sha256"]
            )


if __name__ == "__main__":
    unittest.main()
