#!/usr/bin/env python3
"""Build a source-derived GBS player (or diagnostic GB ROM) for one sound."""

import argparse
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

from config import AUDIO_MANIFEST_PATH, GAME_DATA_ROOT, PROJECT_ROOT


ENGINE_BY_AUDIO_BANK = {
    "AUDIO_1": "1",
    "AUDIO_2": "2",
    "AUDIO_3": "3",
}


LINKER_SCRIPT = """\
ROM0
\torg $0000
\t"rst0"
\torg $0008
\t"rst8"
\torg $0010
\t"rst10"
\torg $0018
\t"rst18"
\torg $0020
\t"rst20"
\torg $0028
\t"rst28"
\torg $0030
\t"rst30"
\torg $0038
\t"rst38"
\torg $0040
\t"vblank"
\torg $0048
\t"lcd"
\torg $0050
\t"timer"
\torg $0058
\t"serial"
\torg $0060
\t"joypad"
\torg $0100
\t"Header"
\torg $0150
\t"Audio Harness Home"
ROMX $2
\t"Sound Effect Headers 1"
\t"Music Headers 1"
\t"Sound Effects 1"
\t"Audio Engine 1"
\t"Music 1"
ROMX $8
\t"Sound Effect Headers 2"
\t"Music Headers 2"
\t"Sound Effects 2"
\t"Low Health Alarm (Audio Engine 2)"
\t"Audio Engine 2"
\t"Music 2"
ROMX $1f
\t"Sound Effect Headers 3"
\t"Music Headers 3"
\t"Sound Effects 3"
\t"Audio Engine 3"
\t"Music 3"
WRAM0
\t"Audio Harness WRAM"
HRAM
\t"Audio Harness HRAM"
"""


GBS_LINKER_SCRIPT = """\
ROM0
\torg $0400
\t"GBS Forwarded Vectors"
\torg $0440
\t"GBS Audio Harness Home"
ROMX $2
\t"Sound Effect Headers 1"
\t"Music Headers 1"
\t"Sound Effects 1"
\t"Audio Engine 1"
\t"Music 1"
ROMX $8
\t"Sound Effect Headers 2"
\t"Music Headers 2"
\t"Sound Effects 2"
\t"Low Health Alarm (Audio Engine 2)"
\t"Audio Engine 2"
\t"Music 2"
ROMX $1f
\t"Sound Effect Headers 3"
\t"Music Headers 3"
\t"Sound Effects 3"
\t"Audio Engine 3"
\t"Music 3"
WRAM0
\t"Audio Harness WRAM"
HRAM
\t"Audio Harness HRAM"
"""


def load_manifest(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def find_audio_asset(manifest, constant):
    normalized = constant.upper()
    for group in ("music", "sfx"):
        asset = manifest.get(group, {}).get(normalized)
        if asset:
            return asset
    raise KeyError(f"audio constant not found in manifest: {constant}")


def choose_engine(asset):
    bank_text = asset.get("bank") or ""
    for bank_name, engine in ENGINE_BY_AUDIO_BANK.items():
        if bank_name in bank_text.split():
            return engine
    raise ValueError(f"cannot infer audio engine for {asset.get('constant')} bank={bank_text!r}")


def harness_asm(
    constant,
    engine,
    frequency_modifier=0,
    tempo_modifier=0,
    mute_finished_sfx=False,
):
    play_label = f"Audio{engine}_PlaySound"
    update_label = f"Audio{engine}_UpdateMusic"
    mute_sfx_call = "\tcall MuteFinishedSfxChannels\n" if mute_finished_sfx else ""
    return f"""\
SECTION "rst0", ROM0[$0000]
\treti
SECTION "rst8", ROM0[$0008]
\treti
SECTION "rst10", ROM0[$0010]
\treti
SECTION "rst18", ROM0[$0018]
\treti
SECTION "rst20", ROM0[$0020]
\treti
SECTION "rst28", ROM0[$0028]
\treti
SECTION "rst30", ROM0[$0030]
\treti
SECTION "rst38", ROM0[$0038]
\treti
SECTION "vblank", ROM0[$0040]
\tjp VBlank
SECTION "lcd", ROM0[$0048]
\treti
SECTION "timer", ROM0[$0050]
\treti
SECTION "serial", ROM0[$0058]
\treti
SECTION "joypad", ROM0[$0060]
\treti

SECTION "Header", ROM0[$0100]
\tnop
\tjp Start
\tds $0150 - @, 0

SECTION "Audio Harness Home", ROM0[$0150]

Start:
\tdi
\tld sp, $dfff

\txor a
\tld hl, $c000
\tld bc, $2000
.clearWRAM
\txor a
\tld [hli], a
\tdec bc
\tld a, b
\tor c
\tjr nz, .clearWRAM

\txor a
\tld hl, $ff80
\tld bc, $007f
.clearHRAM
\txor a
\tld [hli], a
\tdec bc
\tld a, b
\tor c
\tjr nz, .clearHRAM

\tcall InitAudioHarness
\txor a
\tldh [rIF], a
\tld a, 1 << rLCDC_ENABLE | 1 << rLCDC_BG_PRIORITY
\tldh [rLCDC], a

\tld a, BANK({play_label})
\tld [wAudioROMBank], a
\tldh [hLoadedROMBank], a
\tld [MBC1RomBank], a
\tld a, ${frequency_modifier:02x}
\tld [wFrequencyModifier], a
\tld a, ${tempo_modifier:02x}
\tld [wTempoModifier], a
\tld a, {constant}
\tcall {play_label}

\tld a, 1 << VBLANK
\tldh [rIE], a
\tei
.loop
\thalt
\tnop
\tjr .loop

InitAudioHarness:
\tld a, $80
\tldh [rNR52], a
\tldh [rNR30], a
\txor a
\tldh [rNR51], a
\tldh [rNR32], a
\tld a, $08
\tldh [rNR10], a
\tldh [rNR12], a
\tldh [rNR22], a
\tldh [rNR42], a
\tld a, $40
\tldh [rNR14], a
\tldh [rNR24], a
\tldh [rNR44], a
\tld a, $77
\tldh [rNR50], a
\txor a
\tld [wUnusedMusicByte], a
\tld [wDisableChannelOutputWhenSfxEnds], a
\tld [wMuteAudioAndPauseMusic], a
\tld [wMusicTempo + 1], a
\tld [wSfxTempo + 1], a
\tld [wMusicWaveInstrument], a
\tld [wSfxWaveInstrument], a
\tld d, $a0
\tld hl, wChannelCommandPointers
\tcall FillMem
\tld a, $01
\tld d, $18
\tld hl, wChannelNoteDelayCounters
\tcall FillMem
\tld [wMusicTempo], a
\tld [wSfxTempo], a
\tld a, $ff
\tld [wStereoPanning], a
\tret

FillMem:
\tld b, d
.fillLoop
\tld [hli], a
\tdec b
\tjr nz, .fillLoop
\tret

VBlank:
\tpush af
\tpush bc
\tpush de
\tpush hl
\tld a, BANK({update_label})
\tldh [hLoadedROMBank], a
\tld [MBC1RomBank], a
\tcall {update_label}
{mute_sfx_call}\tcall MuteWhenAudioDone
\tpop hl
\tpop de
\tpop bc
\tpop af
\treti

MuteFinishedSfxChannels:
\tld a, [wChannelSoundIDs + CHAN5]
\tand a
\tjr nz, .checkChan6
\tldh a, [rNR51]
\tand HW_CH1_DISABLE_MASK
\tldh [rNR51], a
.checkChan6
\tld a, [wChannelSoundIDs + CHAN6]
\tand a
\tjr nz, .checkChan7
\tldh a, [rNR51]
\tand HW_CH2_DISABLE_MASK
\tldh [rNR51], a
.checkChan7
\tld a, [wChannelSoundIDs + CHAN7]
\tand a
\tjr nz, .checkChan8
\tldh a, [rNR51]
\tand HW_CH3_DISABLE_MASK
\tldh [rNR51], a
\txor a
\tldh [rNR30], a
.checkChan8
\tld a, [wChannelSoundIDs + CHAN8]
\tand a
\tret nz
\tldh a, [rNR51]
\tand HW_CH4_DISABLE_MASK
\tldh [rNR51], a
\tret

MuteWhenAudioDone:
\tld hl, wChannelSoundIDs
\tld b, NUM_CHANNELS
.checkChannel
\tld a, [hli]
\tand a
\tret nz
\tdec b
\tjr nz, .checkChannel
\txor a
\tldh [rNR51], a
\tldh [rNR30], a
\tret

DelayFrame::
\tret

DelayFrames::
\tret

PlayMusic::
\tret

PlaySound::
\tret

PlaySoundWaitForCurrent::
\tret

PlayDefaultMusic::
\tret

SECTION "Audio Harness WRAM", WRAM0

wUnusedMusicByte:: db
wSoundID:: db
wMuteAudioAndPauseMusic:: db
wDisableChannelOutputWhenSfxEnds:: db
wStereoPanning:: db
wSavedVolume:: db
wChannelCommandPointers:: ds NUM_CHANNELS * 2
wChannelReturnAddresses:: ds NUM_CHANNELS * 2
wChannelSoundIDs:: ds NUM_CHANNELS
wChannelFlags1:: ds NUM_CHANNELS
wChannelFlags2:: ds NUM_CHANNELS
wChannelDutyCycles:: ds NUM_CHANNELS
wChannelDutyCyclePatterns:: ds NUM_CHANNELS
wChannelVibratoDelayCounters:: ds NUM_CHANNELS
wChannelVibratoExtents:: ds NUM_CHANNELS
wChannelVibratoRates:: ds NUM_CHANNELS
wChannelFrequencyLowBytes:: ds NUM_CHANNELS
wChannelVibratoDelayCounterReloadValues:: ds NUM_CHANNELS
wChannelPitchSlideLengthModifiers:: ds NUM_CHANNELS
wChannelPitchSlideFrequencySteps:: ds NUM_CHANNELS
wChannelPitchSlideFrequencyStepsFractionalPart:: ds NUM_CHANNELS
wChannelPitchSlideCurrentFrequencyFractionalPart:: ds NUM_CHANNELS
wChannelPitchSlideCurrentFrequencyHighBytes:: ds NUM_CHANNELS
wChannelPitchSlideCurrentFrequencyLowBytes:: ds NUM_CHANNELS
wChannelPitchSlideTargetFrequencyHighBytes:: ds NUM_CHANNELS
wChannelPitchSlideTargetFrequencyLowBytes:: ds NUM_CHANNELS
wChannelNoteDelayCounters:: ds NUM_CHANNELS
wChannelLoopCounters:: ds NUM_CHANNELS
wChannelNoteSpeeds:: ds NUM_CHANNELS
wChannelNoteDelayCountersFractionalPart:: ds NUM_CHANNELS
wChannelOctaves:: ds NUM_CHANNELS
wChannelVolumes:: ds NUM_CHANNELS
wMusicWaveInstrument:: db
wSfxWaveInstrument:: db
wMusicTempo:: dw
wSfxTempo:: dw
wSfxHeaderPointer:: dw
wNewSoundID:: db
wAudioROMBank:: db
wAudioSavedROMBank:: db
wFrequencyModifier:: db
wTempoModifier:: db
wLowHealthAlarm:: db
wAudioFadeOutControl:: db
wAudioFadeOutCounterReloadValue:: db
wAudioFadeOutCounter:: db
wCurOpponent:: db
wGymLeaderNo:: db
\tds $200

SECTION "Audio Harness HRAM", HRAM
hLoadedROMBank:: db
hSavedROMBank:: db
hDexRatingNumMonsOwned:: db

INCLUDE "audio.asm"
"""


def gbs_harness_asm(
    constant,
    engine,
    frequency_modifier=0,
    tempo_modifier=0,
    mute_finished_sfx=False,
):
    """Build a GBS-conformant init/play harness around the source engine."""
    rendered = harness_asm(
        constant,
        engine,
        frequency_modifier=frequency_modifier,
        tempo_modifier=tempo_modifier,
        mute_finished_sfx=mute_finished_sfx,
    )
    common = rendered[rendered.index("InitAudioHarness:") :]
    vblank_start = common.index("VBlank:")
    mute_start = common.index("MuteFinishedSfxChannels:")
    common = common[:vblank_start] + common[mute_start:]
    play_label = f"Audio{engine}_PlaySound"
    update_label = f"Audio{engine}_UpdateMusic"
    mute_call = "\tcall MuteFinishedSfxChannels\n" if mute_finished_sfx else ""
    return f"""\
SECTION "GBS Forwarded Vectors", ROM0[$0400]
\tret
\tds $0408 - @, 0
\tret
\tds $0410 - @, 0
\tret
\tds $0418 - @, 0
\tret
\tds $0420 - @, 0
\tret
\tds $0428 - @, 0
\tret
\tds $0430 - @, 0
\tret
\tds $0438 - @, 0
\tret
\tds $0440 - @, 0

SECTION "GBS Audio Harness Home", ROM0[$0440]

GBSInit::
\tcall InitAudioHarness
\tld a, BANK({play_label})
\tld [wAudioROMBank], a
\tld [wAudioSavedROMBank], a
\tldh [hLoadedROMBank], a
\tld [MBC1RomBank], a
\tld a, ${frequency_modifier:02x}
\tld [wFrequencyModifier], a
\tld a, ${tempo_modifier:02x}
\tld [wTempoModifier], a
\tld a, {constant}
\tcall {play_label}
\tret

GBSPlay::
\tpush af
\tpush bc
\tpush de
\tpush hl
\tld a, BANK({update_label})
\tldh [hLoadedROMBank], a
\tld [MBC1RomBank], a
\tcall {update_label}
{mute_call}\tcall MuteWhenAudioDone
\tpop hl
\tpop de
\tpop bc
\tpop af
\tret

{common}"""


def parse_symbol_address(symbol_path, label):
    for line in Path(symbol_path).read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1] == label:
            return int(fields[0].split(":", 1)[1], 16)
    raise ValueError(f"linked symbol is missing {label}: {symbol_path}")


def gbs_text_field(value):
    encoded = value.encode("ascii", errors="strict")
    if len(encoded) > 31:
        raise ValueError(f"GBS text field is too long: {value!r}")
    return encoded + bytes(32 - len(encoded))


def write_gbs(output, linked_rom, symbol_path):
    load_address = 0x0400
    init_address = parse_symbol_address(symbol_path, "GBSInit")
    play_address = parse_symbol_address(symbol_path, "GBSPlay")
    rom = Path(linked_rom).read_bytes()
    header = struct.pack(
        "<3sBBBHHHHBB",
        b"GBS",
        1,
        1,
        1,
        load_address,
        init_address,
        play_address,
        0xDFFF,
        0,
        0,
    )
    header += gbs_text_field("Pokemon Red/Blue Audio")
    header += gbs_text_field("pokered source engine")
    header += gbs_text_field("See source data license")
    if len(header) != 0x70:
        raise AssertionError(f"invalid GBS header length: {len(header)}")
    Path(output).write_bytes(header + rom[load_address:])


def run(command, cwd):
    subprocess.run(command, cwd=cwd, check=True)


def build_audio_rom(
    constant,
    output,
    manifest_path=AUDIO_MANIFEST_PATH,
    build_dir=None,
    frequency_modifier=0,
    tempo_modifier=0,
):
    for label, value in (
        ("frequency modifier", frequency_modifier),
        ("tempo modifier", tempo_modifier),
    ):
        if not isinstance(value, int) or not 0 <= value <= 0xFF:
            raise ValueError(f"{label} must be an integer from 0 to 255, got {value!r}")
    missing_tools = [tool for tool in ("rgbasm", "rgblink", "rgbfix") if shutil.which(tool) is None]
    if missing_tools:
        raise RuntimeError(f"Missing required tool(s): {', '.join(missing_tools)}")

    manifest = load_manifest(manifest_path)
    asset = find_audio_asset(manifest, constant)
    engine = choose_engine(asset)
    constant = asset["constant"]

    build_root = Path(build_dir or (PROJECT_ROOT / "build" / "audio-rom")).resolve()
    build_root.mkdir(parents=True, exist_ok=True)
    asm_path = build_root / "audio_harness.asm"
    link_path = build_root / "audio_harness.link"
    object_path = build_root / "audio_harness.o"
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    asm_path.write_text(
        harness_asm(
            constant,
            engine,
            frequency_modifier=frequency_modifier,
            tempo_modifier=tempo_modifier,
            mute_finished_sfx=constant.startswith("SFX_"),
        ),
        encoding="utf-8",
    )
    link_path.write_text(LINKER_SCRIPT, encoding="utf-8")

    run(["rgbasm", "-P", "includes.asm", "-D", "_RED", "-o", str(object_path), str(asm_path)], GAME_DATA_ROOT)
    run(["rgblink", "-p", "0", "-l", str(link_path), "-o", str(output), str(object_path)], GAME_DATA_ROOT)
    run(
        [
            "rgbfix",
            "-v",
            "-p",
            "0",
            "-m",
            "MBC1",
            "-r",
            "0",
            "-t",
            "PKMN AUDIO",
            str(output),
        ],
        GAME_DATA_ROOT,
    )
    return output


def build_audio_gbs(
    constant,
    output,
    manifest_path=AUDIO_MANIFEST_PATH,
    build_dir=None,
    frequency_modifier=0,
    tempo_modifier=0,
):
    """Build a standards-compliant GBS with returning init/play entry points."""
    for label, value in (
        ("frequency modifier", frequency_modifier),
        ("tempo modifier", tempo_modifier),
    ):
        if not isinstance(value, int) or not 0 <= value <= 0xFF:
            raise ValueError(f"{label} must be an integer from 0 to 255, got {value!r}")
    missing_tools = [tool for tool in ("rgbasm", "rgblink") if shutil.which(tool) is None]
    if missing_tools:
        raise RuntimeError(f"Missing required tool(s): {', '.join(missing_tools)}")

    manifest = load_manifest(manifest_path)
    asset = find_audio_asset(manifest, constant)
    engine = choose_engine(asset)
    constant = asset["constant"]

    build_root = Path(build_dir or (PROJECT_ROOT / "build" / "audio-gbs")).resolve()
    build_root.mkdir(parents=True, exist_ok=True)
    asm_path = build_root / "audio_harness.asm"
    link_path = build_root / "audio_harness.link"
    object_path = build_root / "audio_harness.o"
    linked_rom = build_root / "audio_harness.bin"
    symbol_path = build_root / "audio_harness.sym"
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    asm_path.write_text(
        gbs_harness_asm(
            constant,
            engine,
            frequency_modifier=frequency_modifier,
            tempo_modifier=tempo_modifier,
            mute_finished_sfx=constant.startswith("SFX_"),
        ),
        encoding="utf-8",
    )
    link_path.write_text(GBS_LINKER_SCRIPT, encoding="utf-8")
    run(
        [
            "rgbasm",
            "-P",
            "includes.asm",
            "-D",
            "_RED",
            "-o",
            str(object_path),
            str(asm_path),
        ],
        GAME_DATA_ROOT,
    )
    run(
        [
            "rgblink",
            "-p",
            "0",
            "-l",
            str(link_path),
            "-n",
            str(symbol_path),
            "-o",
            str(linked_rom),
            str(object_path),
        ],
        GAME_DATA_ROOT,
    )
    write_gbs(output, linked_rom, symbol_path)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("constant", help="Audio constant, e.g. MUSIC_PALLET_TOWN")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=AUDIO_MANIFEST_PATH,
        help="Manifest from export_audio_manifest.py",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "build" / "audio-gbs" / "audio.gbs",
        help="Output path (defaults to a supported .gbs player).",
    )
    parser.add_argument(
        "--container",
        choices=["gbs", "gb"],
        default="gbs",
        help="GBS is intended for rendering; GB is a diagnostic hardware ROM.",
    )
    parser.add_argument(
        "--build-dir",
        type=Path,
        default=None,
        help="Directory for generated assembly/object files",
    )
    parser.add_argument(
        "--frequency-modifier",
        type=lambda value: int(value, 0),
        default=0,
        help="Cry frequency modifier byte, e.g. 0xee.",
    )
    parser.add_argument(
        "--tempo-modifier",
        type=lambda value: int(value, 0),
        default=0,
        help="Cry tempo/length modifier byte, e.g. 0x80.",
    )
    args = parser.parse_args()

    try:
        builder = build_audio_gbs if args.container == "gbs" else build_audio_rom
        output = builder(
            args.constant,
            args.out,
            args.manifest,
            args.build_dir,
            args.frequency_modifier,
            args.tempo_modifier,
        )
    except Exception as exc:
        print(f"failed to build audio player: {exc}", file=sys.stderr)
        return 1

    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
