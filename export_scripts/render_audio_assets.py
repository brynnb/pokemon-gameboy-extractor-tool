#!/usr/bin/env python3
"""Render browser audio assets from Pokemon Red/Blue audio source data."""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from build_audio_rom import build_audio_rom
from config import AUDIO_MANIFEST_PATH, PROJECT_ROOT


def load_manifest(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def selected_assets(manifest, kinds, constants=None):
    if constants:
        assets = []
        for constant in constants:
            normalized = constant.upper()
            asset = manifest.get("music", {}).get(normalized) or manifest.get("sfx", {}).get(normalized)
            if not asset:
                raise KeyError(f"audio constant not found in manifest: {constant}")
            assets.append(asset)
        return assets

    assets = []
    if "music" in kinds:
        assets.extend(manifest["music"].values())
    if "sfx" in kinds:
        assets.extend(
            asset
            for asset in manifest["sfx"].values()
            if asset.get("category") != "pokemon_cry_base"
        )
    if "base-cries" in kinds:
        assets.extend(
            asset
            for asset in manifest["sfx"].values()
            if asset.get("category") == "pokemon_cry_base"
        )
    if "cries" in kinds:
        assets.extend(
            {
                "constant": cry["baseCry"],
                "path": cry["path"],
                "pokemonName": cry["pokemonName"],
                "frequencyModifier": cry["pitch"],
                "tempoModifier": cry["length"],
            }
            for cry in manifest["pokemonCries"].values()
        )
    return sorted(
        assets,
        key=lambda asset: (
            asset.get("audioId", 100000),
            asset.get("pokemonName", ""),
            asset["constant"],
        ),
    )


def output_path(out_dir, asset_path):
    relative = asset_path.lstrip("/")
    return out_dir / relative


def should_trim_tail(asset):
    constant = asset.get("constant", "")
    return constant.startswith("SFX_") or bool(asset.get("pokemonName"))


def trim_wav_tail(wav_path, threshold=512, padding_seconds=0.15):
    with wave.open(str(wav_path), "rb") as handle:
        params = handle.getparams()
        frames = handle.readframes(handle.getnframes())

    if params.sampwidth != 2 or params.nframes == 0:
        return wav_path

    sample_count = len(frames) // params.sampwidth
    last_active_sample = -1
    for index in range(sample_count - 1, -1, -1):
        offset = index * params.sampwidth
        sample = int.from_bytes(frames[offset : offset + params.sampwidth], "little", signed=True)
        if abs(sample) > threshold:
            last_active_sample = index
            break

    if last_active_sample < 0:
        return wav_path

    padding_samples = int(params.framerate * params.nchannels * padding_seconds)
    keep_samples = min(sample_count, last_active_sample + 1 + padding_samples)
    keep_frames = max(1, keep_samples // params.nchannels)
    trimmed_bytes = frames[: keep_frames * params.nchannels * params.sampwidth]

    trimmed_path = wav_path.with_name(f"{wav_path.stem}.trimmed{wav_path.suffix}")
    with wave.open(str(trimmed_path), "wb") as handle:
        handle.setparams(params._replace(nframes=keep_frames))
        handle.writeframes(trimmed_bytes)

    return trimmed_path


def convert_wav_to_ogg(wav_path, out_dir, asset, trim_tail=False):
    source_wav = trim_wav_tail(wav_path) if trim_tail else wav_path
    destination = output_path(out_dir, asset["path"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["oggenc", "-Q", "-o", str(destination), str(source_wav)],
        check=True,
    )
    return destination


def run_gbsplay(command, cwd):
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        result.check_returncode()


def render_gbs_asset(gbs_path, asset, out_dir, seconds, fade, sample_rate):
    audio_id = str(asset["audioId"])
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        run_gbsplay(
            [
                "gbsplay",
                "-q",
                "-o",
                "wav",
                "-r",
                str(sample_rate),
                "-t",
                str(seconds),
                "-f",
                str(fade),
                "-T",
                "1",
                str(gbs_path),
                audio_id,
                audio_id,
            ],
            cwd=temp_path,
        )
        wav_path = temp_path / f"gbsplay-{audio_id}.wav"
        if not wav_path.exists():
            raise FileNotFoundError(f"gbsplay did not produce {wav_path.name}")

        return convert_wav_to_ogg(wav_path, out_dir, asset, should_trim_tail(asset))


def render_rom_asset(asset, out_dir, seconds, fade, sample_rate, manifest_path):
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        rom_path = temp_path / f"{asset['constant'].lower()}.gb"
        build_audio_rom(
            asset["constant"],
            rom_path,
            manifest_path=manifest_path,
            build_dir=temp_path / "build",
            frequency_modifier=asset.get("frequencyModifier", 0),
            tempo_modifier=asset.get("tempoModifier", 0),
        )
        run_gbsplay(
            [
                "gbsplay",
                "-q",
                "-q",
                "-q",
                "-o",
                "wav",
                "-r",
                str(sample_rate),
                "-t",
                str(seconds),
                "-f",
                str(fade),
                "-T",
                "1",
                str(rom_path),
            ],
            cwd=temp_path,
        )
        wav_path = temp_path / "gbsplay-1.wav"
        if not wav_path.exists():
            raise FileNotFoundError("gbsplay did not produce gbsplay-1.wav")

        return convert_wav_to_ogg(wav_path, out_dir, asset, should_trim_tail(asset))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gbs", type=Path, help="Compatible .gbs file")
    parser.add_argument(
        "--build-rom",
        action="store_true",
        help="Build a tiny source-derived Game Boy ROM for each asset instead of using --gbs.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=AUDIO_MANIFEST_PATH,
        help="Manifest from export_audio_manifest.py",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=PROJECT_ROOT / "rendered-audio",
        help="Output root. Use ../capture-quest/public to write CaptureQuest paths.",
    )
    parser.add_argument(
        "--kind",
        action="append",
        choices=["music", "sfx", "cries", "base-cries", "all"],
        default=None,
        help="Asset group to render. May be repeated.",
    )
    parser.add_argument(
        "--constant",
        action="append",
        default=[],
        help="Render a specific source audio constant. May be repeated.",
    )
    parser.add_argument("--seconds", type=int, default=90)
    parser.add_argument("--fade", type=int, default=3)
    parser.add_argument("--sample-rate", type=int, default=44100)
    parser.add_argument("--limit", type=int, default=0, help="Render only first N assets")
    args = parser.parse_args()

    missing_tools = [tool for tool in ("gbsplay", "oggenc") if shutil.which(tool) is None]
    if args.build_rom:
        missing_tools.extend(
            tool for tool in ("rgbasm", "rgblink", "rgbfix") if shutil.which(tool) is None
        )
    if missing_tools:
        print(f"Missing required audio tool(s): {', '.join(missing_tools)}", file=sys.stderr)
        return 1
    if not args.build_rom and not args.gbs:
        print("Provide --gbs or use --build-rom.", file=sys.stderr)
        return 1
    if args.gbs and not args.gbs.exists():
        print(f"GBS file not found: {args.gbs}", file=sys.stderr)
        return 1

    kinds = set(args.kind or ["music"])
    if "all" in kinds:
        kinds = {"music", "sfx", "cries"}

    manifest = load_manifest(args.manifest)
    try:
        assets = selected_assets(manifest, kinds, args.constant)
    except KeyError as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.limit > 0:
        assets = assets[: args.limit]

    for asset in assets:
        if args.build_rom:
            destination = render_rom_asset(
                asset,
                args.out_dir,
                args.seconds,
                args.fade,
                args.sample_rate,
                args.manifest,
            )
        else:
            destination = render_gbs_asset(
                args.gbs,
                asset,
                args.out_dir,
                args.seconds,
                args.fade,
                args.sample_rate,
            )
        print(f"{asset['constant']} -> {destination}")

    print(f"Rendered {len(assets)} audio asset(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
