#!/usr/bin/env python3
"""Generate a source-derived audio manifest for Pokemon Red/Blue audio."""

import json
import re
import sqlite3
from pathlib import Path

from config import (
    AUDIO_DIR,
    AUDIO_HEADERS_DIR,
    AUDIO_MANIFEST_PATH,
    DB_PATH,
    GAME_DATA_ROOT,
    MAP_CONSTANTS_FILE,
    MAP_DATA_DIR,
    MUSIC_CONSTANTS_FILE,
    POKEMON_DATA_DIR,
)
from export_moves import parse_move_names, parse_move_sounds
from pokemon_names import normalize_pokemon_name

MUSIC_CONST_RE = re.compile(r"\bmusic_const\s+([A-Z0-9_]+),\s*([A-Za-z0-9_]+)")
CONST_DEF_RE = re.compile(r"\bconst_def(?:\s+(\d+))?")
MAP_CONST_RE = re.compile(
    r"\s*map_const\s+([A-Z0-9_]+),\s*\d+,\s*\d+(?:\s*;\s*\$([0-9A-F]+))?",
    re.IGNORECASE,
)
MAP_SONG_RE = re.compile(
    r"\s*db\s+(MUSIC_[A-Z0-9_]+),\s+BANK\([^)]+\)\s*;\s*([A-Z0-9_]+)",
    re.IGNORECASE,
)
HEADER_LABEL_RE = re.compile(r"^([A-Za-z0-9_]+)::")
CHANNEL_COUNT_RE = re.compile(r"\bchannel_count\s+(\d+)")
CHANNEL_RE = re.compile(r"\bchannel\s+(\d+),\s*([A-Za-z0-9_]+)")
INCLUDE_RE = re.compile(r'INCLUDE\s+"([^"]+)"')
CRY_RE = re.compile(
    r"\s*mon_cry\s+(SFX_CRY_([0-9A-F]{2})),\s+\$([0-9A-F]+),\s+\$([0-9A-F]+)\s*;\s*(.+)$",
    re.IGNORECASE,
)


def asset_slug(constant):
    if constant.startswith("MUSIC_"):
        constant = constant.removeprefix("MUSIC_")
    elif constant.startswith("SFX_"):
        constant = constant.removeprefix("SFX_")
    return constant.lower()


def pokemon_cry_slug(name):
    return normalize_pokemon_name(name).lower()


def move_short_name(name):
    return re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")


def rel_path(path):
    return path.relative_to(GAME_DATA_ROOT).as_posix()


def parse_audio_includes():
    includes = []
    audio_asm = GAME_DATA_ROOT / "audio.asm"
    for line in audio_asm.read_text(encoding="utf-8").splitlines():
        match = INCLUDE_RE.search(line)
        if match and match.group(1).startswith("audio/"):
            includes.append(GAME_DATA_ROOT / match.group(1))
    return includes


def label_source_lookup():
    lookup = {}
    for path in parse_audio_includes():
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            match = HEADER_LABEL_RE.match(line.strip())
            if match:
                lookup.setdefault(match.group(1), rel_path(path))
    return lookup


def parse_headers(kind):
    headers = {}
    pattern = f"{kind}headers*.asm"
    for path in sorted(AUDIO_HEADERS_DIR.glob(pattern)):
        current = None
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            label_match = HEADER_LABEL_RE.match(stripped)
            if label_match:
                current = label_match.group(1)
                headers[current] = {
                    "label": current,
                    "headerFile": rel_path(path),
                    "channelCount": 0,
                    "channels": [],
                }
                continue

            if not current:
                continue

            count_match = CHANNEL_COUNT_RE.search(stripped)
            if count_match:
                headers[current]["channelCount"] = int(count_match.group(1))
                continue

            channel_match = CHANNEL_RE.search(stripped)
            if channel_match:
                headers[current]["channels"].append(
                    {
                        "channel": int(channel_match.group(1)),
                        "label": channel_match.group(2),
                    }
                )
    return headers


def parse_music_constants():
    constants = []
    current_bank = None
    for line in MUSIC_CONSTANTS_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("; AUDIO_"):
            current_bank = stripped.removeprefix(";").strip()

        match = MUSIC_CONST_RE.search(line)
        if not match:
            continue
        constant, label = match.groups()
        constants.append(
            {
                "constant": constant,
                "label": label,
                "bank": current_bank,
                "audioId": len(constants) + 1,
            }
        )
    return constants


def parse_map_ids():
    map_ids = {}
    next_id = 0
    for line in MAP_CONSTANTS_FILE.read_text(encoding="utf-8").splitlines():
        const_def_match = CONST_DEF_RE.search(line)
        if const_def_match:
            next_id = int(const_def_match.group(1) or 0)
            continue

        match = MAP_CONST_RE.match(line)
        if not match:
            continue
        map_constant, hex_id = match.groups()
        map_id = int(hex_id, 16) if hex_id else next_id
        map_ids[map_constant] = map_id
        next_id = map_id + 1
    return map_ids


def first_channel_source(header, source_lookup):
    for channel in header.get("channels", []):
        source = source_lookup.get(channel["label"])
        if source:
            return source
    return None


def build_music_manifest(constants, music_headers, source_lookup):
    music = {}
    for row in constants:
        if not row["constant"].startswith("MUSIC_"):
            continue
        header = music_headers.get(row["label"], {})
        source_file = first_channel_source(header, source_lookup)
        music[row["constant"]] = {
            "constant": row["constant"],
            "label": row["label"],
            "audioId": row["audioId"],
            "bank": row["bank"],
            "sourceFile": source_file,
            "headerFile": header.get("headerFile"),
            "channelCount": header.get("channelCount", 0),
            "channels": header.get("channels", []),
            "path": f"/sound/pokemon/music/{asset_slug(row['constant'])}.ogg",
            "loop": not row["constant"].startswith(
                (
                    "MUSIC_DEFEATED_",
                    "MUSIC_PKMN_HEALED",
                    "MUSIC_JIGGLYPUFF_SONG",
                )
            ),
        }
    return music


def sfx_category(constant):
    if constant.startswith("SFX_CRY_"):
        return "pokemon_cry_base"
    if constant.startswith("SFX_BATTLE_") or constant in {
        "SFX_PECK",
        "SFX_FAINT_FALL",
        "SFX_POUND",
        "SFX_DAMAGE",
        "SFX_NOT_VERY_EFFECTIVE",
        "SFX_VINE_WHIP",
        "SFX_SUPER_EFFECTIVE",
        "SFX_DOUBLESLAP",
        "SFX_HORN_DRILL",
        "SFX_PSYBEAM",
        "SFX_PSYCHIC_M",
    }:
        return "battle"
    if constant.startswith("SFX_NOISE_INSTRUMENT"):
        return "noise_instrument"
    return "field_ui"


def build_sfx_manifest(constants, sfx_headers, source_lookup):
    sfx = {}
    for row in constants:
        if not row["constant"].startswith("SFX_"):
            continue
        header = sfx_headers.get(row["label"], {})
        source_file = first_channel_source(header, source_lookup)
        category = sfx_category(row["constant"])
        subdir = "cries" if category == "pokemon_cry_base" else "sfx"
        sfx[row["constant"]] = {
            "constant": row["constant"],
            "label": row["label"],
            "audioId": row["audioId"],
            "bank": row["bank"],
            "category": category,
            "sourceFile": source_file,
            "headerFile": header.get("headerFile"),
            "channelCount": header.get("channelCount", 0),
            "channels": header.get("channels", []),
            "path": f"/sound/pokemon/{subdir}/{asset_slug(row['constant'])}.ogg",
            "loop": False,
        }
    return sfx


def parse_cries(sfx_manifest):
    cries = {}
    cry_path = POKEMON_DATA_DIR / "cries.asm"
    for line in cry_path.read_text(encoding="utf-8").splitlines():
        match = CRY_RE.match(line)
        if not match:
            continue
        constant, cry_hex, pitch, length, name = match.groups()
        normalized = normalize_pokemon_name(name.strip())
        asset = sfx_manifest.get(constant, {})
        cries[normalized] = {
            "pokemonName": normalized,
            "baseCry": constant,
            "baseCryIndex": int(cry_hex, 16),
            "pitch": int(pitch, 16),
            "length": int(length, 16),
            "basePath": asset.get("path", f"/sound/pokemon/cries/{asset_slug(constant)}.ogg"),
            "path": f"/sound/pokemon/cries/species/{pokemon_cry_slug(name)}.ogg",
        }
    return cries


def load_rows(query):
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(query).fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def map_music_rows(music_manifest):
    rows = []
    map_ids = parse_map_ids()
    songs_path = MAP_DATA_DIR / "songs.asm"
    for line in songs_path.read_text(encoding="utf-8").splitlines():
        match = MAP_SONG_RE.match(line)
        if not match:
            continue
        music_constant, map_constant = match.groups()
        asset = music_manifest.get(music_constant, {})
        rows.append(
            {
                "map_constant": map_constant,
                "map_id": map_ids.get(map_constant),
                "music_constant": music_constant,
                "path": asset.get("path"),
            }
        )
    return rows


def move_sound_rows(sfx_manifest):
    rows = load_rows(
        """
        SELECT id, name, short_name, battle_sound, battle_sound_pitch, battle_sound_tempo
        FROM moves
        WHERE battle_sound IS NOT NULL AND battle_sound != 'NO_SOUND'
        ORDER BY id
        """
    )
    by_id = {}
    if rows:
        for row in rows:
            asset = sfx_manifest.get(row["battle_sound"], {})
            by_id[str(row["id"])] = {
                "moveId": row["id"],
                "moveName": row["name"],
                "shortName": row["short_name"],
                "sfx": row["battle_sound"],
                "pitch": row["battle_sound_pitch"],
                "tempo": row["battle_sound_tempo"],
                "path": asset.get("path"),
            }
        return by_id

    move_names = parse_move_names()
    for move_id, sound_data in parse_move_sounds().items():
        sfx_constant = sound_data["sound"]
        if sfx_constant == "NO_SOUND":
            continue
        asset = sfx_manifest.get(sfx_constant, {})
        move_name = move_names.get(move_id, f"MOVE_{move_id}")
        by_id[str(move_id)] = {
            "moveId": move_id,
            "moveName": move_name,
            "shortName": move_short_name(move_name),
            "sfx": sfx_constant,
            "pitch": sound_data["pitch"],
            "tempo": sound_data["tempo"],
            "path": asset.get("path"),
        }
    return by_id


def build_manifest():
    constants = parse_music_constants()
    source_lookup = label_source_lookup()
    music_headers = parse_headers("music")
    sfx_headers = parse_headers("sfx")
    music = build_music_manifest(constants, music_headers, source_lookup)
    sfx = build_sfx_manifest(constants, sfx_headers, source_lookup)

    return {
        "schemaVersion": 1,
        "source": {
            "gameDataRoot": "pokemon-game-data",
            "musicConstantsFile": rel_path(MUSIC_CONSTANTS_FILE),
            "audioDirectory": rel_path(AUDIO_DIR),
        },
        "assetBasePath": "/sound/pokemon",
        "format": "ogg",
        "music": music,
        "sfx": sfx,
        "pokemonCries": parse_cries(sfx),
        "mapMusic": map_music_rows(music),
        "moveSounds": move_sound_rows(sfx),
    }


def main():
    manifest = build_manifest()
    AUDIO_MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "Generated audio_manifest.json: "
        f"{len(manifest['music'])} music, "
        f"{len(manifest['sfx'])} sfx, "
        f"{len(manifest['pokemonCries'])} cries, "
        f"{len(manifest['mapMusic'])} map music, "
        f"{len(manifest['moveSounds'])} move sounds"
    )


if __name__ == "__main__":
    main()
