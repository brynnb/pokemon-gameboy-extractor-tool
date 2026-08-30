#!/usr/bin/env python3
"""Render source-faithful Red/Blue audio to FLAC masters and Ogg Vorbis."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import uuid
import wave

from build_audio_rom import build_audio_gbs
from config import AUDIO_MANIFEST_PATH, AUDIO_OUTPUT_DIR


RENDER_MANIFEST_NAME = "audio-render-manifest.json"
RENDER_MANIFEST_SCHEMA_VERSION = 2
DEFAULT_MASTER_SAMPLE_RATE = 48000
DEFAULT_DISTRIBUTION_SAMPLE_RATE = 24000
DEFAULT_DISTRIBUTION_CHANNELS = 1
DEFAULT_VORBIS_QUALITY = 1


def load_manifest(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def derived_asset(row, *, asset_key, constant, kind, frequency=0, tempo=0):
    return {
        **row,
        "assetKey": asset_key,
        "constant": constant,
        "renderKind": kind,
        "frequencyModifier": frequency,
        "tempoModifier": tempo,
        "loop": False,
    }


def selected_assets(manifest, kinds, constants=None, move_ids=None):
    """Select stable base and derived assets without collapsing indexed cries."""
    constants = constants or []
    move_ids = move_ids or []
    assets = []

    for constant in constants:
        normalized = constant.upper()
        if normalized in manifest.get("music", {}):
            row = manifest["music"][normalized]
            assets.append(
                {**row, "assetKey": f"music:{normalized}", "renderKind": "music"}
            )
        elif normalized in manifest.get("sfx", {}):
            row = manifest["sfx"][normalized]
            assets.append({**row, "assetKey": f"sfx:{normalized}", "renderKind": "sfx"})
        else:
            raise KeyError(f"audio constant not found in manifest: {constant}")

    for move_id in move_ids:
        row = manifest.get("moveSounds", {}).get(str(move_id))
        if not row:
            raise KeyError(f"move ID not found in manifest: {move_id}")
        assets.append(
            derived_asset(
                row,
                asset_key=f"move:{int(move_id):03d}",
                constant=row["sfx"],
                kind="move",
                frequency=row["pitch"],
                tempo=row["tempo"],
            )
        )

    if constants or move_ids:
        return deduplicate_and_sort(assets)

    if "music" in kinds:
        assets.extend(
            {**row, "assetKey": f"music:{constant}", "renderKind": "music"}
            for constant, row in manifest["music"].items()
        )
    if "sfx" in kinds:
        assets.extend(
            {**row, "assetKey": f"sfx:{constant}", "renderKind": "sfx"}
            for constant, row in manifest["sfx"].items()
            if row.get("category") != "pokemon_cry_base"
        )
    if "base-cries" in kinds:
        assets.extend(
            {**row, "assetKey": f"sfx:{constant}", "renderKind": "base-cry"}
            for constant, row in manifest["sfx"].items()
            if row.get("category") == "pokemon_cry_base"
        )
    if "cries" in kinds:
        assets.extend(
            derived_asset(
                row,
                asset_key=f"cry:{int(index):03d}",
                constant=row["baseCry"],
                kind="cry",
                frequency=row["pitch"],
                tempo=row["length"],
            )
            for index, row in manifest["indexedCries"].items()
        )
    if "moves" in kinds:
        assets.extend(
            derived_asset(
                row,
                asset_key=f"move:{int(move_id):03d}",
                constant=row["sfx"],
                kind="move",
                frequency=row["pitch"],
                tempo=row["tempo"],
            )
            for move_id, row in manifest["moveSounds"].items()
        )
    return deduplicate_and_sort(assets)


def deduplicate_and_sort(assets):
    by_key = {asset["assetKey"]: asset for asset in assets}
    kind_order = {"music": 0, "sfx": 1, "base-cry": 2, "cry": 3, "move": 4}
    return sorted(
        by_key.values(),
        key=lambda asset: (
            kind_order.get(asset["renderKind"], 99),
            asset.get("audioId", 100000),
            asset.get("internalIndex", asset.get("moveId", 0)),
            asset["assetKey"],
        ),
    )


def output_path(out_dir, asset_path):
    relative = PurePosixPath(str(asset_path).lstrip("/"))
    if not relative.parts or any(part in ("", ".", "..") for part in relative.parts):
        raise ValueError(f"unsafe audio output path: {asset_path!r}")
    return Path(out_dir).joinpath(*relative.parts)


def should_trim_tail(asset):
    return asset.get("renderKind") != "music"


def trim_wav_tail(wav_path, threshold=256, padding_seconds=0.15):
    with wave.open(str(wav_path), "rb") as handle:
        params = handle.getparams()
        frames = handle.readframes(handle.getnframes())

    if params.sampwidth != 2 or params.nframes == 0:
        return Path(wav_path)

    samples = list(struct.iter_unpack("<h", frames))
    last_active_sample = next(
        (index for index in range(len(samples) - 1, -1, -1) if abs(samples[index][0]) > threshold),
        -1,
    )
    if last_active_sample < 0:
        return Path(wav_path)

    padding_samples = int(params.framerate * params.nchannels * padding_seconds)
    keep_samples = min(len(samples), last_active_sample + 1 + padding_samples)
    keep_frames = max(1, math.ceil(keep_samples / params.nchannels))
    trimmed_bytes = frames[: keep_frames * params.nchannels * params.sampwidth]

    trimmed_path = Path(wav_path).with_name(f"{Path(wav_path).stem}.trimmed.wav")
    with wave.open(str(trimmed_path), "wb") as handle:
        handle.setparams(params._replace(nframes=keep_frames))
        handle.writeframes(trimmed_bytes)
    return trimmed_path


def analyze_wav(wav_path):
    with wave.open(str(wav_path), "rb") as handle:
        params = handle.getparams()
        frames = handle.readframes(handle.getnframes())
    if params.nchannels != 2 or params.sampwidth != 2:
        raise ValueError(
            f"expected stereo 16-bit PCM from gbsplay, got {params.nchannels} channels / "
            f"{params.sampwidth * 8} bits"
        )
    values = [sample[0] for sample in struct.iter_unpack("<h", frames)]
    if not values:
        raise ValueError(f"renderer produced an empty WAV: {wav_path}")
    peak = max(abs(value) for value in values)
    rms = math.sqrt(sum(value * value for value in values) / len(values))
    if peak < 64 or rms < 1:
        raise ValueError(
            f"renderer produced silent/near-silent audio: peak={peak}, rms={rms:.2f}"
        )
    return {
        "sampleRate": params.framerate,
        "channels": params.nchannels,
        "bitsPerSample": params.sampwidth * 8,
        "sampleFrames": params.nframes,
        "durationSeconds": params.nframes / params.framerate,
        "peak": peak,
        "rms": round(rms, 4),
    }


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def encode_audio(
    source_wav,
    destination,
    codec,
    metadata,
    *,
    sample_rate=None,
    channels=None,
    vorbis_quality=DEFAULT_VORBIS_QUALITY,
):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.stem}.{uuid.uuid4().hex}.tmp{destination.suffix}"
    )
    command = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source_wav),
        "-map_metadata",
        "-1",
        "-fflags",
        "+bitexact",
        "-flags:a",
        "+bitexact",
    ]
    if codec == "flac":
        command.extend(["-c:a", "flac", "-compression_level", "8"])
    elif codec == "ogg-vorbis":
        # Game Boy audio is generated by four simple synthesis channels. A
        # mono 24 kHz derivative preserves its useful bandwidth while avoiding
        # desktop-master-sized files in browser deployments.
        if sample_rate is not None:
            command.extend(["-ar", str(sample_rate)])
        if channels is not None:
            command.extend(["-ac", str(channels)])
        command.extend(["-c:a", "libvorbis", "-q:a", str(vorbis_quality)])
    else:
        raise ValueError(f"unsupported output codec: {codec}")
    for key, value in sorted(metadata.items()):
        command.extend(["-metadata", f"{key}={value}"])
    command.append(str(temporary))
    try:
        subprocess.run(command, check=True)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def encode_outputs(
    wav_path,
    out_dir,
    asset,
    *,
    distribution_sample_rate=DEFAULT_DISTRIBUTION_SAMPLE_RATE,
    distribution_channels=DEFAULT_DISTRIBUTION_CHANNELS,
    vorbis_quality=DEFAULT_VORBIS_QUALITY,
):
    source_wav = trim_wav_tail(wav_path) if should_trim_tail(asset) else Path(wav_path)
    analysis = analyze_wav(source_wav)
    loop_enabled = bool(asset.get("loop", False))
    tags = {
        "ASSET_KEY": asset["assetKey"],
        "SOURCE_CONSTANT": asset["constant"],
        "LOOP_MODE": "captured-source-runtime" if loop_enabled else "none",
    }
    if loop_enabled:
        tags.update({"LOOPSTART": 0, "LOOPEND": analysis["sampleFrames"]})

    master = encode_audio(
        source_wav,
        output_path(out_dir, asset["masterPath"]),
        "flac",
        tags,
    )
    distribution = encode_audio(
        source_wav,
        output_path(out_dir, asset["path"]),
        "ogg-vorbis",
        tags,
        sample_rate=distribution_sample_rate,
        channels=distribution_channels,
        vorbis_quality=vorbis_quality,
    )
    return {
        **analysis,
        "assetKey": asset["assetKey"],
        "kind": asset["renderKind"],
        "constant": asset["constant"],
        "frequencyModifier": asset.get("frequencyModifier", 0),
        "tempoModifier": asset.get("tempoModifier", 0),
        "loop": {
            "enabled": loop_enabled,
            "startSample": 0 if loop_enabled else None,
            "endSample": analysis["sampleFrames"] if loop_enabled else None,
            "mode": tags["LOOP_MODE"],
        },
        "master": {
            "path": asset["masterPath"],
            "format": "flac",
            "sha256": sha256_file(master),
            "sizeBytes": master.stat().st_size,
        },
        "distribution": {
            "path": asset["path"],
            "format": "ogg-vorbis",
            "sampleRate": distribution_sample_rate,
            "channels": distribution_channels,
            "quality": vorbis_quality,
            "sha256": sha256_file(distribution),
            "sizeBytes": distribution.stat().st_size,
        },
    }


def run_gbsplay(command, cwd, *, strict_warnings=False):
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    warnings = [line for line in result.stderr.splitlines() if line.strip()]
    for warning in warnings:
        print(f"gbsplay: {warning}", file=sys.stderr)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, command, output=result.stdout, stderr=result.stderr
        )
    if strict_warnings and warnings:
        raise RuntimeError(f"gbsplay emitted warnings: {'; '.join(warnings)}")
    return warnings


def gbsplay_command(input_path, seconds, fade, sample_rate):
    return [
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
        "--",
        str(input_path),
    ]


def render_gbs_asset(
    gbs_path,
    asset,
    out_dir,
    seconds,
    fade,
    sample_rate,
    *,
    distribution_sample_rate=DEFAULT_DISTRIBUTION_SAMPLE_RATE,
    distribution_channels=DEFAULT_DISTRIBUTION_CHANNELS,
    vorbis_quality=DEFAULT_VORBIS_QUALITY,
    strict_warnings=False,
):
    if asset.get("frequencyModifier", 0) or asset.get("tempoModifier", 0):
        raise ValueError(
            f"{asset['assetKey']} requires source pitch/tempo modifiers; "
            "use --build-gbs for derived cries and moves"
        )
    audio_id = str(asset["audioId"])
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        command = gbsplay_command(gbs_path, seconds, fade, sample_rate)
        command.extend([audio_id, audio_id])
        warnings = run_gbsplay(command, temp_path, strict_warnings=strict_warnings)
        wav_path = temp_path / f"gbsplay-{audio_id}.wav"
        if not wav_path.exists():
            raise FileNotFoundError(f"gbsplay did not produce {wav_path.name}")
        metadata = encode_outputs(
            wav_path,
            out_dir,
            asset,
            distribution_sample_rate=distribution_sample_rate,
            distribution_channels=distribution_channels,
            vorbis_quality=vorbis_quality,
        )
        metadata["rendererWarnings"] = warnings
        return metadata


def render_rom_asset(
    asset,
    out_dir,
    seconds,
    fade,
    sample_rate,
    manifest_path,
    *,
    distribution_sample_rate=DEFAULT_DISTRIBUTION_SAMPLE_RATE,
    distribution_channels=DEFAULT_DISTRIBUTION_CHANNELS,
    vorbis_quality=DEFAULT_VORBIS_QUALITY,
    strict_warnings=False,
):
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        player_path = temp_path / f"{asset['assetKey'].replace(':', '-')}.gbs"
        build_audio_gbs(
            asset["constant"],
            player_path,
            manifest_path=manifest_path,
            build_dir=temp_path / "build",
            frequency_modifier=asset.get("frequencyModifier", 0),
            tempo_modifier=asset.get("tempoModifier", 0),
        )
        warnings = run_gbsplay(
            gbsplay_command(player_path, seconds, fade, sample_rate),
            temp_path,
            strict_warnings=strict_warnings,
        )
        wav_path = temp_path / "gbsplay-1.wav"
        if not wav_path.exists():
            raise FileNotFoundError("gbsplay did not produce gbsplay-1.wav")
        metadata = encode_outputs(
            wav_path,
            out_dir,
            asset,
            distribution_sample_rate=distribution_sample_rate,
            distribution_channels=distribution_channels,
            vorbis_quality=vorbis_quality,
        )
        metadata["rendererWarnings"] = warnings
        return metadata


def tool_version(command):
    result = subprocess.run(command, text=True, capture_output=True, check=True)
    return (result.stdout or result.stderr).splitlines()[0].strip()


def write_render_manifest(
    stage_dir,
    source_manifest,
    artifacts,
    sample_rate,
    *,
    distribution_sample_rate=DEFAULT_DISTRIBUTION_SAMPLE_RATE,
    distribution_channels=DEFAULT_DISTRIBUTION_CHANNELS,
    vorbis_quality=DEFAULT_VORBIS_QUALITY,
):
    path = Path(stage_dir) / RENDER_MANIFEST_NAME
    previous = {"artifacts": []}
    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8"))
    source_hash = sha256_file(source_manifest)
    render_profile = {
        "master": {"sampleRate": sample_rate, "channels": 2, "codec": "flac"},
        "distribution": {
            "sampleRate": distribution_sample_rate,
            "channels": distribution_channels,
            "codec": "ogg-vorbis",
            "quality": vorbis_quality,
        },
    }
    if (
        previous.get("sourceManifestSha256") != source_hash
        or previous.get("renderProfile") != render_profile
    ):
        # Never carry encoded files forward under metadata for a different
        # source manifest. A partial render remains honest and self-contained.
        previous = {"artifacts": []}
    merged = {row["assetKey"]: row for row in previous.get("artifacts", [])}
    merged.update({row["assetKey"]: row for row in artifacts})
    payload = {
        "schemaVersion": RENDER_MANIFEST_SCHEMA_VERSION,
        "sourceManifestSha256": source_hash,
        "sampleRate": sample_rate,
        "renderProfile": render_profile,
        "renderers": {
            "gbsplay": tool_version(["gbsplay", "-V"]),
            "ffmpeg": tool_version(["ffmpeg", "-version"]),
        },
        "artifacts": [merged[key] for key in sorted(merged)],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def validate_render_bundle(bundle_dir, source_manifest, *, require_complete=False):
    """Validate encoded paths, hashes, metadata, and optional full coverage."""
    bundle_dir = Path(bundle_dir).resolve()
    source_manifest = Path(source_manifest)
    render_manifest_path = bundle_dir / RENDER_MANIFEST_NAME
    if not render_manifest_path.is_file():
        raise ValueError(f"missing audio render manifest: {render_manifest_path}")

    render_manifest = json.loads(render_manifest_path.read_text(encoding="utf-8"))
    if render_manifest.get("schemaVersion") != RENDER_MANIFEST_SCHEMA_VERSION:
        raise ValueError("unsupported audio render manifest schema")
    if render_manifest.get("sourceManifestSha256") != sha256_file(source_manifest):
        raise ValueError("audio bundle was rendered from a different source manifest")

    source = load_manifest(source_manifest)
    expected_assets = {
        row["assetKey"]: row
        for row in selected_assets(
            source, {"music", "sfx", "base-cries", "cries", "moves"}
        )
    }
    artifacts = render_manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("audio render manifest artifacts must be an array")
    actual_assets = {row.get("assetKey"): row for row in artifacts}
    if None in actual_assets or len(actual_assets) != len(artifacts):
        raise ValueError("audio render manifest has missing or duplicate asset keys")
    unknown = sorted(set(actual_assets) - set(expected_assets))
    if unknown:
        raise ValueError(f"audio bundle contains unknown asset keys: {unknown[:5]}")
    if require_complete and set(actual_assets) != set(expected_assets):
        missing = sorted(set(expected_assets) - set(actual_assets))
        raise ValueError(
            f"audio bundle is incomplete: expected {len(expected_assets)} assets, "
            f"found {len(actual_assets)}; missing={missing[:5]}"
        )

    for asset_key, artifact in actual_assets.items():
        source_asset = expected_assets[asset_key]
        if artifact.get("sampleRate") != render_manifest.get("sampleRate"):
            raise ValueError(f"sample-rate mismatch for {asset_key}")
        if artifact.get("sampleFrames", 0) <= 0 or artifact.get("durationSeconds", 0) <= 0:
            raise ValueError(f"empty audio artifact metadata for {asset_key}")
        distribution_profile = render_manifest.get("renderProfile", {}).get(
            "distribution", {}
        )
        distribution = artifact.get("distribution", {})
        for key in ("sampleRate", "channels", "quality"):
            if distribution.get(key) != distribution_profile.get(key):
                raise ValueError(f"distribution {key} mismatch for {asset_key}")
        for role, expected_path in (
            ("master", source_asset["masterPath"]),
            ("distribution", source_asset["path"]),
        ):
            encoded = artifact.get(role, {})
            if encoded.get("path") != expected_path:
                raise ValueError(f"unexpected {role} path for {asset_key}")
            file_path = output_path(bundle_dir, expected_path)
            try:
                file_path.resolve(strict=True).relative_to(bundle_dir)
            except (FileNotFoundError, ValueError) as error:
                raise ValueError(
                    f"missing or escaping {role} file for {asset_key}: {file_path}"
                ) from error
            if not file_path.is_file() or file_path.stat().st_size != encoded.get("sizeBytes"):
                raise ValueError(f"invalid {role} size for {asset_key}")
            if sha256_file(file_path) != encoded.get("sha256"):
                raise ValueError(f"invalid {role} hash for {asset_key}")

    return {"artifacts": len(actual_assets), "expectedAssets": len(expected_assets)}


def publish_directory(stage, final, backup):
    had_previous = final.exists()
    if had_previous:
        os.replace(final, backup)
    try:
        os.replace(stage, final)
    except Exception:
        if had_previous:
            os.replace(backup, final)
        raise
    if had_previous:
        shutil.rmtree(backup)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--gbs", type=Path, help="Compatible source .gbs file")
    source.add_argument(
        "--build-gbs",
        "--build-rom",
        action="store_true",
        help=(
            "Build a supported source-derived GBS player per asset "
            "(--build-rom is a deprecated alias)."
        ),
    )
    parser.add_argument("--manifest", type=Path, default=AUDIO_MANIFEST_PATH)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=AUDIO_OUTPUT_DIR,
        help="Atomic output bundle root.",
    )
    parser.add_argument(
        "--kind",
        action="append",
        choices=["music", "sfx", "cries", "base-cries", "moves", "all"],
        default=None,
    )
    parser.add_argument("--constant", action="append", default=[])
    parser.add_argument("--move-id", action="append", type=int, default=[])
    parser.add_argument("--music-seconds", type=int, default=120)
    parser.add_argument("--effect-seconds", type=int, default=10)
    parser.add_argument("--fade", type=int, default=3)
    parser.add_argument(
        "--sample-rate", type=int, default=DEFAULT_MASTER_SAMPLE_RATE,
        help="Source capture and FLAC master sample rate.",
    )
    parser.add_argument(
        "--distribution-sample-rate",
        type=int,
        default=DEFAULT_DISTRIBUTION_SAMPLE_RATE,
    )
    parser.add_argument(
        "--distribution-channels",
        type=int,
        choices=[1, 2],
        default=DEFAULT_DISTRIBUTION_CHANNELS,
    )
    parser.add_argument(
        "--vorbis-quality",
        type=int,
        choices=range(-1, 11),
        default=DEFAULT_VORBIS_QUALITY,
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--strict-emulator-warnings",
        action="store_true",
        help="Fail if gbsplay reports any otherwise non-fatal emulator warning.",
    )
    args = parser.parse_args()

    missing_tools = [tool for tool in ("gbsplay", "ffmpeg") if shutil.which(tool) is None]
    if args.build_gbs:
        missing_tools.extend(
            tool for tool in ("rgbasm", "rgblink") if shutil.which(tool) is None
        )
    if missing_tools:
        print(f"Missing required audio tool(s): {', '.join(sorted(set(missing_tools)))}", file=sys.stderr)
        return 1
    if args.gbs and (not args.gbs.exists() or args.gbs.suffix.lower() != ".gbs"):
        print(f"GBS file not found or not a .gbs file: {args.gbs}", file=sys.stderr)
        return 1
    if not args.manifest.exists():
        print(f"Audio manifest not found: {args.manifest}", file=sys.stderr)
        return 1
    if (
        args.sample_rate <= 0
        or args.distribution_sample_rate <= 0
        or args.music_seconds <= 0
        or args.effect_seconds <= 0
    ):
        print("Sample rate and render durations must be positive.", file=sys.stderr)
        return 1

    kinds = set(args.kind or ["music"])
    if "all" in kinds:
        kinds = {"music", "sfx", "base-cries", "cries", "moves"}
    manifest = load_manifest(args.manifest)
    try:
        assets = selected_assets(manifest, kinds, args.constant, args.move_id)
    except KeyError as error:
        print(error, file=sys.stderr)
        return 1
    if args.limit > 0:
        assets = assets[: args.limit]

    final_dir = args.out_dir.resolve()
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    stage_dir = final_dir.with_name(f".{final_dir.name}.{token}.stage")
    backup_dir = final_dir.with_name(f".{final_dir.name}.{token}.backup")
    stage_dir.mkdir()
    if final_dir.exists():
        shutil.copytree(final_dir, stage_dir, dirs_exist_ok=True)

    artifacts = []
    try:
        for asset in assets:
            seconds = args.music_seconds if asset["renderKind"] == "music" else args.effect_seconds
            fade = 0 if asset.get("loop", False) else args.fade
            if args.build_gbs:
                metadata = render_rom_asset(
                    asset,
                    stage_dir,
                    seconds,
                    fade,
                    args.sample_rate,
                    args.manifest,
                    distribution_sample_rate=args.distribution_sample_rate,
                    distribution_channels=args.distribution_channels,
                    vorbis_quality=args.vorbis_quality,
                    strict_warnings=args.strict_emulator_warnings,
                )
            else:
                metadata = render_gbs_asset(
                    args.gbs,
                    asset,
                    stage_dir,
                    seconds,
                    fade,
                    args.sample_rate,
                    distribution_sample_rate=args.distribution_sample_rate,
                    distribution_channels=args.distribution_channels,
                    vorbis_quality=args.vorbis_quality,
                    strict_warnings=args.strict_emulator_warnings,
                )
            artifacts.append(metadata)
            print(
                f"{asset['assetKey']} -> {metadata['master']['path']} + "
                f"{metadata['distribution']['path']}"
            )

        write_render_manifest(
            stage_dir,
            args.manifest,
            artifacts,
            args.sample_rate,
            distribution_sample_rate=args.distribution_sample_rate,
            distribution_channels=args.distribution_channels,
            vorbis_quality=args.vorbis_quality,
        )
        complete_selection = (
            kinds == {"music", "sfx", "base-cries", "cries", "moves"}
            and not args.constant
            and not args.move_id
            and args.limit == 0
        )
        validate_render_bundle(
            stage_dir, args.manifest, require_complete=complete_selection
        )
        publish_directory(stage_dir, final_dir, backup_dir)
    except Exception as error:
        print(f"Audio rendering failed; previous bundle was preserved: {error}", file=sys.stderr)
        return 1
    finally:
        if stage_dir.exists():
            shutil.rmtree(stage_dir)

    print(f"Rendered {len(artifacts)} audio asset(s) atomically to {final_dir}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
