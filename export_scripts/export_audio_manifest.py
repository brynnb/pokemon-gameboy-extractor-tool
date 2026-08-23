#!/usr/bin/env python3
"""Generate a source-derived audio manifest for Pokemon Red/Blue audio."""

import json
import re
import sqlite3
from contextlib import closing
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
from export_moves import (
    parse_move_names,
    parse_move_sounds,
    validate_exact_move_ids,
)
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
# Header labels are exported with ``::`` while channel labels in the included
# music/SFX sources intentionally use a single ``:``.  Both are global source
# labels for manifest provenance; local labels begin with a dot and are excluded.
HEADER_LABEL_RE = re.compile(r"^([A-Za-z0-9_]+):{1,2}(?:\s|$)")
CHANNEL_COUNT_RE = re.compile(r"\bchannel_count\s+(\d+)")
CHANNEL_RE = re.compile(r"\bchannel\s+(\d+),\s*([A-Za-z0-9_]+)")
INCLUDE_RE = re.compile(r'INCLUDE\s+"([^"]+)"')
SOURCE_LOOP_RE = re.compile(r"\bsound_loop\s+\d+\s*,\s*([A-Za-z0-9_.]+)")
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
    return re.sub(r"[^a-z0-9]+", "_", normalize_pokemon_name(name).lower()).strip("_")


def move_short_name(name):
    return re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")


def rel_path(path):
    return path.relative_to(GAME_DATA_ROOT).as_posix()


def output_fields(ogg_path):
    """Return backwards-compatible and lossless/distribution output metadata."""
    flac_path = str(Path(ogg_path).with_suffix(".flac")).replace("\\", "/")
    return {
        "path": ogg_path,
        "masterPath": flac_path,
        "outputs": {
            "master": {"path": flac_path, "format": "flac"},
            "distribution": {"path": ogg_path, "format": "ogg-vorbis"},
        },
    }


def source_loop_labels(source_file):
    if not source_file:
        return []
    source_path = GAME_DATA_ROOT / source_file
    if not source_path.exists():
        return []
    labels = []
    for match in SOURCE_LOOP_RE.finditer(source_path.read_text(encoding="utf-8")):
        label = match.group(1)
        if label not in labels:
            labels.append(label)
    return labels


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
        loop_labels = source_loop_labels(source_file)
        is_looping = bool(loop_labels)
        music[row["constant"]] = {
            "constant": row["constant"],
            "label": row["label"],
            "audioId": row["audioId"],
            "bank": row["bank"],
            "sourceFile": source_file,
            "headerFile": header.get("headerFile"),
            "channelCount": header.get("channelCount", 0),
            "channels": header.get("channels", []),
            **output_fields(f"/sound/pokemon/music/{asset_slug(row['constant'])}.ogg"),
            "loop": is_looping,
            "loopMetadata": {
                "enabled": is_looping,
                "mode": "source-runtime-capture" if is_looping else "none",
                "sourceLoopLabels": loop_labels,
            },
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
            **output_fields(
                f"/sound/pokemon/{subdir}/{asset_slug(row['constant'])}.ogg"
            ),
            "loop": False,
            "loopMetadata": {
                "enabled": False,
                "mode": "none",
                "sourceLoopLabels": source_loop_labels(source_file),
            },
        }
    return sfx


def parse_cries(sfx_manifest):
    """Return canonical species cries and all 190 internal-index cry slots."""
    species_cries = {}
    indexed_cries = {}
    cry_path = POKEMON_DATA_DIR / "cries.asm"
    for line in cry_path.read_text(encoding="utf-8").splitlines():
        match = CRY_RE.match(line)
        if not match:
            continue
        constant, cry_hex, pitch, length, name = match.groups()
        internal_index = len(indexed_cries) + 1
        normalized = normalize_pokemon_name(name.strip())
        is_glitch_slot = normalized.startswith("MISSINGNO")
        asset = sfx_manifest.get(constant, {})
        output_path = (
            f"/sound/pokemon/cries/internal/{internal_index:03d}-missingno.ogg"
            if is_glitch_slot
            else f"/sound/pokemon/cries/species/{pokemon_cry_slug(name)}.ogg"
        )
        row = {
            "internalIndex": internal_index,
            "pokemonName": normalized,
            "isGlitchSlot": is_glitch_slot,
            "baseCry": constant,
            "baseCryIndex": int(cry_hex, 16),
            "pitch": int(pitch, 16),
            "length": int(length, 16),
            "basePath": asset.get("path", f"/sound/pokemon/cries/{asset_slug(constant)}.ogg"),
            **output_fields(output_path),
        }
        indexed_cries[str(internal_index)] = row
        if not is_glitch_slot:
            if normalized in species_cries:
                raise ValueError(f"duplicate canonical cry row for {normalized}")
            species_cries[normalized] = row

    if len(indexed_cries) != 190:
        raise ValueError(
            f"expected 190 internal Pokemon cry slots, found {len(indexed_cries)}"
        )
    if len(species_cries) != 151:
        raise ValueError(
            f"expected 151 canonical Pokemon cries, found {len(species_cries)}"
        )
    return species_cries, indexed_cries


def load_rows(query, db_path=None):
    db_path = db_path or DB_PATH
    if not Path(db_path).exists():
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(query).fetchall()]
    except sqlite3.OperationalError as exc:
        if "no such table: moves" in str(exc).lower():
            raise ValueError(
                f"existing database is missing the required moves table: {db_path}"
            ) from exc
        raise
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


def move_sound_rows(sfx_manifest, db_path=None):
    rows = load_rows(
        """
        SELECT id, name, short_name, battle_sound, battle_sound_pitch, battle_sound_tempo
        FROM moves
        ORDER BY id
        """,
        db_path=db_path,
    )
    by_id = {}
    if rows is not None:
        validate_exact_move_ids(
            "moves table used by the audio manifest", (row["id"] for row in rows)
        )
        missing_sounds = [
            row["id"]
            for row in rows
            if not row["battle_sound"] or row["battle_sound"] == "NO_SOUND"
        ]
        if missing_sounds:
            raise ValueError(
                "moves table used by the audio manifest is missing sound constants "
                f"for IDs {missing_sounds}"
            )
        for row in rows:
            asset = sfx_manifest.get(row["battle_sound"], {})
            derived_path = (
                f"/sound/pokemon/moves/{row['id']:03d}-"
                f"{asset_slug(row['short_name'])}.ogg"
            )
            by_id[str(row["id"])] = {
                "moveId": row["id"],
                "moveName": row["name"],
                "shortName": row["short_name"],
                "sfx": row["battle_sound"],
                "pitch": row["battle_sound_pitch"],
                "tempo": row["battle_sound_tempo"],
                "basePath": asset.get("path"),
                **output_fields(derived_path),
            }
        return by_id

    move_names = parse_move_names()
    move_sounds = parse_move_sounds()
    validate_exact_move_ids("move names used by the audio manifest", move_names)
    validate_exact_move_ids("move sounds used by the audio manifest", move_sounds)
    for move_id, sound_data in move_sounds.items():
        sfx_constant = sound_data["sound"]
        if sfx_constant == "NO_SOUND":
            continue
        asset = sfx_manifest.get(sfx_constant, {})
        move_name = move_names.get(move_id, f"MOVE_{move_id}")
        derived_path = (
            f"/sound/pokemon/moves/{move_id:03d}-{move_short_name(move_name).lower()}.ogg"
        )
        by_id[str(move_id)] = {
            "moveId": move_id,
            "moveName": move_name,
            "shortName": move_short_name(move_name),
            "sfx": sfx_constant,
            "pitch": sound_data["pitch"],
            "tempo": sound_data["tempo"],
            "basePath": asset.get("path"),
            **output_fields(derived_path),
        }
    return by_id


def build_manifest():
    constants = parse_music_constants()
    source_lookup = label_source_lookup()
    music_headers = parse_headers("music")
    sfx_headers = parse_headers("sfx")
    music = build_music_manifest(constants, music_headers, source_lookup)
    sfx = build_sfx_manifest(constants, sfx_headers, source_lookup)
    species_cries, indexed_cries = parse_cries(sfx)

    return {
        "schemaVersion": 2,
        "source": {
            "gameDataRoot": "pokemon-game-data",
            "musicConstantsFile": rel_path(MUSIC_CONSTANTS_FILE),
            "audioDirectory": rel_path(AUDIO_DIR),
        },
        "assetBasePath": "/sound/pokemon",
        # Retained for v1 consumers; new consumers should use ``formats`` and
        # each asset's outputs/masterPath fields.
        "format": "ogg",
        "formats": {
            "master": {
                "container": "flac",
                "codec": "flac",
                "lossless": True,
            },
            "distribution": {
                "container": "ogg",
                "codec": "vorbis",
                "lossless": False,
            },
        },
        "music": music,
        "sfx": sfx,
        "pokemonCries": species_cries,
        "indexedCries": indexed_cries,
        "mapMusic": map_music_rows(music),
        "moveSounds": move_sound_rows(sfx),
    }


def relational_pokemon_name(name):
    """Translate source comment spelling to the relational Pokemon key style."""
    name = normalize_pokemon_name(name)
    name = name.replace("'", "").replace("’", "").replace(".", "")
    key = re.sub(r"[^A-Z0-9]+", "_", name).strip("_")
    return {"MRMIME": "MR_MIME"}.get(key, key)


def create_audio_tables(conn):
    cursor = conn.cursor()
    for table in (
        "pokemon_cry_assets",
        "move_audio_assets",
        "map_music_assets",
        "audio_asset_sources",
        "audio_channels",
        "audio_assets",
    ):
        cursor.execute(f'DROP TABLE IF EXISTS "{table}"')

    cursor.executescript(
        """
        CREATE TABLE audio_assets (
            asset_key TEXT PRIMARY KEY,
            asset_kind TEXT NOT NULL
                CHECK (asset_kind IN ('music', 'sfx', 'cry', 'move')),
            constant TEXT NOT NULL,
            display_name TEXT,
            base_asset_key TEXT,
            audio_bank TEXT,
            audio_id INTEGER,
            frequency_modifier INTEGER NOT NULL DEFAULT 0
                CHECK (frequency_modifier BETWEEN 0 AND 255),
            tempo_modifier INTEGER NOT NULL DEFAULT 0
                CHECK (tempo_modifier BETWEEN 0 AND 255),
            loop_enabled INTEGER NOT NULL DEFAULT 0
                CHECK (loop_enabled IN (0, 1)),
            loop_mode TEXT NOT NULL CHECK (
                loop_mode IN ('none', 'source-runtime-capture')
            ),
            ogg_path TEXT NOT NULL,
            flac_path TEXT NOT NULL,
            FOREIGN KEY (base_asset_key) REFERENCES audio_assets (asset_key),
            UNIQUE (asset_kind, constant, display_name)
        );

        CREATE TABLE audio_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_key TEXT NOT NULL,
            channel_number INTEGER NOT NULL CHECK (channel_number BETWEEN 1 AND 8),
            source_label TEXT NOT NULL,
            FOREIGN KEY (asset_key) REFERENCES audio_assets (asset_key),
            UNIQUE (asset_key, channel_number, source_label)
        );

        CREATE TABLE audio_asset_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_key TEXT NOT NULL,
            source_role TEXT NOT NULL CHECK (
                source_role IN ('header', 'channel_data', 'cry_parameters', 'move_parameters')
            ),
            source_path TEXT NOT NULL CHECK (
                source_path NOT LIKE '/%' AND source_path NOT GLOB '[A-Za-z]:*'
            ),
            source_label TEXT,
            FOREIGN KEY (asset_key) REFERENCES audio_assets (asset_key),
            UNIQUE (asset_key, source_role, source_path, source_label)
        );

        CREATE TABLE map_music_assets (
            map_id INTEGER PRIMARY KEY,
            map_constant TEXT NOT NULL UNIQUE,
            asset_key TEXT NOT NULL,
            FOREIGN KEY (map_id) REFERENCES maps (id),
            FOREIGN KEY (asset_key) REFERENCES audio_assets (asset_key)
        );

        CREATE TABLE move_audio_assets (
            move_id INTEGER PRIMARY KEY,
            asset_key TEXT NOT NULL UNIQUE,
            FOREIGN KEY (move_id) REFERENCES moves (id),
            FOREIGN KEY (asset_key) REFERENCES audio_assets (asset_key)
        );

        CREATE TABLE pokemon_cry_assets (
            internal_index INTEGER PRIMARY KEY CHECK (internal_index BETWEEN 1 AND 190),
            pokemon_id INTEGER UNIQUE,
            pokemon_name TEXT NOT NULL,
            is_glitch_slot INTEGER NOT NULL CHECK (is_glitch_slot IN (0, 1)),
            asset_key TEXT NOT NULL UNIQUE,
            FOREIGN KEY (pokemon_id) REFERENCES pokemon (id),
            FOREIGN KEY (asset_key) REFERENCES audio_assets (asset_key),
            CHECK (
                (is_glitch_slot = 1 AND pokemon_id IS NULL)
                OR (is_glitch_slot = 0 AND pokemon_id IS NOT NULL)
            )
        );

        CREATE INDEX idx_audio_assets_base ON audio_assets (base_asset_key);
        CREATE INDEX idx_audio_asset_sources_path ON audio_asset_sources (source_path);
        """
    )


def insert_audio_asset(cursor, key, kind, row, *, base_key=None, display_name=None):
    loop_metadata = row.get("loopMetadata", {})
    cursor.execute(
        """
        INSERT INTO audio_assets (
            asset_key, asset_kind, constant, display_name, base_asset_key,
            audio_bank, audio_id, frequency_modifier, tempo_modifier,
            loop_enabled, loop_mode, ogg_path, flac_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            key,
            kind,
            row["constant"] if "constant" in row else row["baseCry"],
            display_name,
            base_key,
            row.get("bank"),
            row.get("audioId"),
            row.get("frequencyModifier", row.get("pitch", 0)),
            row.get("tempoModifier", row.get("tempo", row.get("length", 0))),
            int(bool(row.get("loop", False))),
            loop_metadata.get("mode", "none"),
            row["path"],
            row["masterPath"],
        ),
    )


def add_asset_source(cursor, asset_key, role, path, label=None):
    if not path:
        return
    cursor.execute(
        """
        INSERT OR IGNORE INTO audio_asset_sources (
            asset_key, source_role, source_path, source_label
        ) VALUES (?, ?, ?, ?)
        """,
        (asset_key, role, path, label),
    )


def write_audio_tables(conn, manifest):
    """Materialize manifest relationships in normalized SQLite tables."""
    create_audio_tables(conn)
    cursor = conn.cursor()

    for kind in ("music", "sfx"):
        for constant, row in manifest[kind].items():
            key = f"{kind}:{constant}"
            insert_audio_asset(cursor, key, kind, row, display_name=constant)
            add_asset_source(cursor, key, "header", row.get("headerFile"), row.get("label"))
            add_asset_source(
                cursor, key, "channel_data", row.get("sourceFile"), row.get("label")
            )
            for channel in row.get("channels", []):
                cursor.execute(
                    """
                    INSERT INTO audio_channels (
                        asset_key, channel_number, source_label
                    ) VALUES (?, ?, ?)
                    """,
                    (key, channel["channel"], channel["label"]),
                )

    pokemon_ids = {
        relational_pokemon_name(name): pokemon_id
        for pokemon_id, name in cursor.execute("SELECT id, name FROM pokemon")
    }
    for index_text, row in manifest["indexedCries"].items():
        internal_index = int(index_text)
        key = f"cry:{internal_index:03d}"
        base_key = f"sfx:{row['baseCry']}"
        derived = {**row, "constant": row["baseCry"]}
        insert_audio_asset(
            cursor,
            key,
            "cry",
            derived,
            base_key=base_key,
            display_name=f"{internal_index:03d}:{row['pokemonName']}",
        )
        add_asset_source(
            cursor,
            key,
            "cry_parameters",
            "data/pokemon/cries.asm",
            str(internal_index),
        )
        pokemon_id = None
        if not row["isGlitchSlot"]:
            pokemon_id = pokemon_ids.get(relational_pokemon_name(row["pokemonName"]))
            if pokemon_id is None:
                raise ValueError(
                    f"could not resolve canonical cry Pokemon {row['pokemonName']}"
                )
        cursor.execute(
            """
            INSERT INTO pokemon_cry_assets (
                internal_index, pokemon_id, pokemon_name, is_glitch_slot, asset_key
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                internal_index,
                pokemon_id,
                row["pokemonName"],
                int(row["isGlitchSlot"]),
                key,
            ),
        )

    for move_id_text, row in manifest["moveSounds"].items():
        move_id = int(move_id_text)
        key = f"move:{move_id:03d}"
        derived = {
            **row,
            "constant": row["sfx"],
            "frequencyModifier": row["pitch"],
            "tempoModifier": row["tempo"],
        }
        insert_audio_asset(
            cursor,
            key,
            "move",
            derived,
            base_key=f"sfx:{row['sfx']}",
            display_name=row["moveName"],
        )
        add_asset_source(
            cursor,
            key,
            "move_parameters",
            "data/moves/sfx.asm",
            str(move_id),
        )
        cursor.execute(
            "INSERT INTO move_audio_assets (move_id, asset_key) VALUES (?, ?)",
            (move_id, key),
        )

    for row in manifest["mapMusic"]:
        if row["map_id"] is None:
            raise ValueError(f"could not resolve map music row {row['map_constant']}")
        cursor.execute(
            """
            INSERT INTO map_music_assets (map_id, map_constant, asset_key)
            VALUES (?, ?, ?)
            """,
            (
                row["map_id"],
                row["map_constant"],
                f"music:{row['music_constant']}",
            ),
        )

    conn.commit()


def validate_audio_tables(conn):
    expected_kinds = {"music": 45, "sfx": 161, "cry": 190, "move": 165}
    actual_kinds = dict(
        conn.execute(
            "SELECT asset_kind, COUNT(*) FROM audio_assets GROUP BY asset_kind"
        ).fetchall()
    )
    if actual_kinds != expected_kinds:
        raise ValueError(
            f"audio asset coverage mismatch: expected {expected_kinds}, found {actual_kinds}"
        )
    expected_tables = {
        "map_music_assets": 248,
        "move_audio_assets": 165,
        "pokemon_cry_assets": 190,
    }
    for table, expected_count in expected_tables.items():
        count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        if count != expected_count:
            raise ValueError(
                f"{table} coverage mismatch: expected {expected_count}, found {count}"
            )
    canonical, glitch = conn.execute(
        """
        SELECT
            SUM(CASE WHEN is_glitch_slot = 0 THEN 1 ELSE 0 END),
            SUM(CASE WHEN is_glitch_slot = 1 THEN 1 ELSE 0 END)
        FROM pokemon_cry_assets
        """
    ).fetchone()
    if (canonical, glitch) != (151, 39):
        raise ValueError(
            f"cry slot split mismatch: expected (151, 39), found {(canonical, glitch)}"
        )
    missing_sources = conn.execute(
        """
        SELECT COUNT(*) FROM audio_assets a
        WHERE a.asset_kind IN ('music', 'sfx')
          AND NOT EXISTS (
              SELECT 1 FROM audio_asset_sources s
              WHERE s.asset_key = a.asset_key AND s.source_role = 'channel_data'
          )
        """
    ).fetchone()[0]
    if missing_sources:
        raise ValueError(f"{missing_sources} source audio assets lack channel provenance")
    foreign_key_errors = []
    for table in (
        "audio_assets",
        "audio_channels",
        "audio_asset_sources",
        "map_music_assets",
        "move_audio_assets",
        "pokemon_cry_assets",
    ):
        foreign_key_errors.extend(conn.execute(f'PRAGMA foreign_key_check("{table}")'))
    if foreign_key_errors:
        raise ValueError(f"audio table foreign-key errors: {foreign_key_errors[:10]}")


def main():
    manifest = build_manifest()
    if DB_PATH.exists():
        with closing(sqlite3.connect(DB_PATH)) as conn, conn:
            write_audio_tables(conn, manifest)
            validate_audio_tables(conn)
    AUDIO_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIO_MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "Generated audio_manifest.json: "
        f"{len(manifest['music'])} music, "
        f"{len(manifest['sfx'])} sfx, "
        f"{len(manifest['pokemonCries'])} canonical cries / "
        f"{len(manifest['indexedCries'])} indexed cry slots, "
        f"{len(manifest['mapMusic'])} map music, "
        f"{len(manifest['moveSounds'])} move sounds"
    )


if __name__ == "__main__":
    main()
