"""CaptureQuest importer for ``pokemon-gameboy-extractor`` schema version 2.

This module is an opt-in *consumer* of the canonical database.  It is never
loaded by the extraction pipeline and never writes CaptureQuest vocabulary
back into canonical artifacts.  The adapter output has its own version so its
contract can evolve independently of the extractor schema.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import tempfile
from typing import Any, Mapping

from adapters.capturequest import PROFILE, SCRIPT_MAPPINGS


ADAPTER_SCHEMA_NAME = "capturequest-pokemon-import"
ADAPTER_SCHEMA_VERSION = 1
EXTRACTOR_SCHEMA_NAME = "pokemon-gameboy-extractor"
SUPPORTED_EXTRACTOR_SCHEMA_VERSIONS = frozenset({2})
EXTRACTOR_READER_VERSION = 2
SUPPORTED_RELEASES = frozenset({"red", "blue"})
PORTABLE_PATH_SCOPES = frozenset(
    {"repository", "graphics_output", "audio_output"}
)


class CaptureQuestImportError(ValueError):
    """The source database cannot be consumed safely by this adapter."""


@dataclass(frozen=True)
class AssetRoots:
    """Local roots used to resolve portable references outside the bundle."""

    repository: Path
    graphics_output: Path
    audio_output: Path

    def resolve(self, reference: Mapping[str, str]) -> Path:
        """Resolve a validated portable asset reference beneath its root."""

        scope = reference.get("scope")
        relative_path = reference.get("relativePath")
        if scope not in PORTABLE_PATH_SCOPES or not isinstance(relative_path, str):
            raise CaptureQuestImportError(f"Invalid asset reference: {reference!r}")
        _validate_relative_path(relative_path)
        root = Path(getattr(self, scope)).resolve()
        resolved = root.joinpath(*PurePosixPath(relative_path).parts).resolve()
        if not resolved.is_relative_to(root):
            raise CaptureQuestImportError(
                f"Asset path escapes its {scope!r} root: {relative_path!r}"
            )
        return resolved


def _validate_relative_path(value: str) -> str:
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise CaptureQuestImportError(f"Non-portable relative path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise CaptureQuestImportError(f"Non-portable relative path: {value!r}")
    return value


def portable_reference(scope: str, relative_path: str) -> dict[str, str]:
    """Return a stable path reference without embedding a host-specific root."""

    if scope not in PORTABLE_PATH_SCOPES:
        raise CaptureQuestImportError(f"Unsupported path scope: {scope!r}")
    return {"scope": scope, "relativePath": _validate_relative_path(relative_path)}


def _audio_reference(logical_path: str) -> dict[str, str]:
    if not logical_path.startswith("/") or logical_path.startswith("//"):
        raise CaptureQuestImportError(
            f"Audio output path must be a rooted logical path: {logical_path!r}"
        )
    return portable_reference("audio_output", logical_path[1:])


def _source_reference(source_root: str, source_path: str) -> dict[str, str]:
    """Normalize a source-tree path into the repository path scope.

    Older extractor tables store paths relative to the disassembly root while
    newer relational tables already include that root.  The adapter exposes a
    single representation and never relies on the consumer's working
    directory.
    """

    root = PurePosixPath(_validate_relative_path(source_root))
    path = PurePosixPath(_validate_relative_path(source_path))
    if path.parts[: len(root.parts)] != root.parts:
        path = root / path
    return portable_reference("repository", path.as_posix())


def _require_reference(
    value: Any,
    known_values: set[int],
    context: str,
    *,
    allow_none: bool = False,
) -> None:
    if value is None and allow_none:
        return
    if value not in known_values:
        raise CaptureQuestImportError(f"{context} refers to unknown ID {value!r}")


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


REQUIRED_TABLES = frozenset(
    {
        "schema_metadata",
        "game_releases",
        "extraction_runs",
        "extraction_run_releases",
        "maps",
        "warps",
        "warp_events",
        "tilesets",
        "map_connections",
        "overworld_map_positions",
        "tile_images",
        "tiles",
        "objects",
        "items",
        "moves",
        "pokemon",
        "pokemon_evolutions",
        "pokemon_default_moves",
        "pokemon_learnset",
        "pokemon_tmhm",
        "wild_encounters",
        "encounter_slots",
        "trainer_classes",
        "trainer_parties",
        "trainer_party_pokemon",
        "trainer_headers",
        "dialogue_text",
        "text_pointers",
        "hidden_items",
        "hidden_coins",
        "hidden_objects",
        "missable_objects",
        "map_music",
        "map_scripts",
        "npc_movement_data",
        "spin_tiles",
        "event_flags",
        "coordinate_triggers",
        "script_event_candidates",
        "script_event_candidate_actions",
        "script_event_candidate_conditions",
        "script_event_candidate_references",
        "script_event_candidate_diagnostics",
        "script_event_ir_blocks",
        "script_event_ir_references",
        "script_event_in_game_trades",
        "script_event_tile_overrides",
        "script_event_boulder_targets",
        "script_event_object_visibility",
        "script_event_conditional_dialogue",
        "graphic_assets",
        "graphic_formats",
        "graphic_categories",
        "graphic_source_links",
        "graphic_derivations",
        "audio_assets",
        "audio_asset_sources",
        "map_music_assets",
        "move_audio_assets",
        "pokemon_cry_assets",
    }
)


def _require_tables(conn: sqlite3.Connection) -> None:
    missing = sorted(REQUIRED_TABLES - _table_names(conn))
    if missing:
        raise CaptureQuestImportError(
            "Extractor schema-v2 database is incomplete; missing tables: "
            + ", ".join(missing)
        )


def _query(
    conn: sqlite3.Connection, sql: str, parameters: tuple[Any, ...] = ()
) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, parameters)
    names = [description[0] for description in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _one(
    conn: sqlite3.Connection, sql: str, parameters: tuple[Any, ...] = ()
) -> dict[str, Any]:
    rows = _query(conn, sql, parameters)
    if len(rows) != 1:
        raise CaptureQuestImportError(
            f"Expected exactly one row while importing, found {len(rows)}"
        )
    return rows[0]


def _json_value(raw: str, context: str) -> Any:
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise CaptureQuestImportError(f"Invalid JSON in {context}") from error


def negotiate_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Negotiate and return the extractor schema metadata.

    This adapter intentionally supports only extractor schema v2.  A future
    extractor version is rejected even when its minimum reader happens to be 2:
    forward compatibility must be deliberately tested and released as a new
    CaptureQuest adapter version.
    """

    metadata = _one(
        conn,
        """
        SELECT schema_name, schema_version, minimum_reader_version, applied_epoch
        FROM schema_metadata
        """,
    )
    name = metadata["schema_name"]
    version = metadata["schema_version"]
    minimum_reader = metadata["minimum_reader_version"]
    if name != EXTRACTOR_SCHEMA_NAME:
        raise CaptureQuestImportError(
            f"Unsupported extractor schema {name!r}; expected {EXTRACTOR_SCHEMA_NAME!r}"
        )
    if version not in SUPPORTED_EXTRACTOR_SCHEMA_VERSIONS:
        raise CaptureQuestImportError(
            f"Unsupported extractor schema version {version}; supported: "
            f"{sorted(SUPPORTED_EXTRACTOR_SCHEMA_VERSIONS)}"
        )
    if not isinstance(minimum_reader, int) or not 1 <= minimum_reader <= version:
        raise CaptureQuestImportError(
            f"Invalid minimum_reader_version {minimum_reader!r} for schema {version}"
        )
    if minimum_reader > EXTRACTOR_READER_VERSION:
        raise CaptureQuestImportError(
            f"Extractor requires reader {minimum_reader}, but this adapter implements "
            f"reader {EXTRACTOR_READER_VERSION}"
        )
    return {
        "name": name,
        "version": version,
        "minimumReaderVersion": minimum_reader,
        "appliedEpoch": metadata["applied_epoch"],
    }


def _load_release_context(
    conn: sqlite3.Connection, release_code: str, schema: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    release_code = release_code.strip().lower()
    if release_code not in SUPPORTED_RELEASES:
        raise CaptureQuestImportError(
            f"Unsupported release {release_code!r}; choose 'red' or 'blue'"
        )
    run = _one(
        conn,
        """
        SELECT run_id, schema_name, schema_version, extractor_revision,
               source_revision, source_date_epoch, source_root,
               source_tree_sha256
        FROM extraction_runs
        """,
    )
    if (run["schema_name"], run["schema_version"]) != (
        schema["name"],
        schema["version"],
    ):
        raise CaptureQuestImportError("Extraction run does not use negotiated schema")
    release = _one(
        conn,
        """
        SELECT release.release_code, release.title, release.variant,
               release.platform, release.region, release.language,
               release.build_define
        FROM game_releases AS release
        JOIN extraction_run_releases AS link
          ON link.release_code = release.release_code
        WHERE link.run_id = ? AND release.release_code = ?
        """,
        (run["run_id"], release_code),
    )
    _validate_relative_path(run["source_root"])
    return (
        {
            "runId": run["run_id"],
            "extractorRevision": run["extractor_revision"],
            "sourceRevision": run["source_revision"],
            "sourceDateEpoch": run["source_date_epoch"],
            "sourceRoot": portable_reference("repository", run["source_root"]),
            "sourceTreeSha256": run["source_tree_sha256"],
        },
        {
            "code": release["release_code"],
            "title": release["title"],
            "variant": release["variant"],
            "platform": release["platform"],
            "region": release["region"],
            "language": release["language"],
            "buildDefine": release["build_define"],
        },
    )


def _load_maps(conn: sqlite3.Connection) -> tuple[list[dict[str, Any]], set[int]]:
    rows = _query(
        conn,
        """
        SELECT id, name, width, height, tileset_id, is_overworld,
               north_connection, south_connection, west_connection,
               east_connection
        FROM maps ORDER BY id
        """,
    )
    maps = [
        {
            "mapId": row["id"],
            "name": row["name"],
            "width": row["width"],
            "height": row["height"],
            "tilesetId": row["tileset_id"],
            "isOverworld": bool(row["is_overworld"]),
            "connections": {
                "north": row["north_connection"],
                "south": row["south_connection"],
                "west": row["west_connection"],
                "east": row["east_connection"],
            },
        }
        for row in rows
    ]
    map_ids = {row["mapId"] for row in maps}
    for row in maps:
        for direction, destination in row["connections"].items():
            _require_reference(
                destination,
                map_ids,
                f"Map {row['mapId']} {direction} connection",
                allow_none=True,
            )
    return maps, map_ids


def _load_warps(
    conn: sqlite3.Connection, map_ids: set[int]
) -> list[dict[str, Any]]:
    rows = _query(
        conn,
        """
        SELECT id, source_map, source_map_id, source_warp_index,
               source_x, source_y, destination_map, destination_kind,
               destination_map_id, destination_x, destination_y,
               destination_warp_id, source_file
        FROM warps
        ORDER BY source_map_id, source_warp_index, id
        """,
    )
    result = []
    for row in rows:
        source_map_id = row["source_map_id"]
        destination_map_id = row["destination_map_id"]
        kind = row["destination_kind"]
        if source_map_id not in map_ids:
            raise CaptureQuestImportError(
                f"Warp {row['id']} has unknown source map ID {source_map_id!r}"
            )
        if kind == "fixed":
            if destination_map_id not in map_ids:
                raise CaptureQuestImportError(
                    f"Fixed warp {row['id']} has unknown destination map ID "
                    f"{destination_map_id!r}"
                )
        elif kind == "last-map":
            if destination_map_id is not None:
                raise CaptureQuestImportError(
                    f"Dynamic warp {row['id']} must not invent a destination map"
                )
        else:
            raise CaptureQuestImportError(
                f"Warp {row['id']} has unsupported destination kind {kind!r}"
            )
        result.append(
            {
                "warpId": row["id"],
                "sourceMapId": source_map_id,
                "sourceMapLabel": row["source_map"],
                "sourceWarpIndex": row["source_warp_index"],
                "x": row["source_x"],
                "y": row["source_y"],
                "destination": {
                    "kind": kind,
                    "mapId": destination_map_id,
                    "mapLabel": row["destination_map"],
                    "warpIndex": row["destination_warp_id"],
                    "x": row["destination_x"],
                    "y": row["destination_y"],
                },
                "sourceFile": portable_reference("repository", row["source_file"]),
            }
        )
    return result


def _load_items(
    conn: sqlite3.Connection, move_ids: set[int]
) -> tuple[list[dict[str, Any]], set[int]]:
    result = []
    for row in _query(
        conn,
        """
        SELECT id, name, short_name, price, is_usable, uses_party_menu,
               vending_price, move_id, is_guard_drink, is_key_item
        FROM items ORDER BY id
        """,
    ):
        _require_reference(
            row["move_id"], move_ids, f"Item {row['id']} move", allow_none=True
        )
        result.append(
            {
                "itemId": row["id"],
                "sourceConstant": row["short_name"],
                "name": row["name"],
                "price": row["price"],
                "isUsable": bool(row["is_usable"]),
                "usesPartyMenu": bool(row["uses_party_menu"]),
                "vendingPrice": row["vending_price"],
                "moveId": row["move_id"],
                "isGuardDrink": bool(row["is_guard_drink"]),
                "isKeyItem": bool(row["is_key_item"]),
            }
        )
    return result, {row["itemId"] for row in result}


def _load_evolutions(
    conn: sqlite3.Connection,
    pokemon_ids: set[int],
    item_ids: set[int],
) -> list[dict[str, Any]]:
    result = []
    for row in _query(
        conn,
        """
        SELECT evolution.id, evolution.source_pokemon_id,
               source.name AS source_pokemon_name,
               evolution.target_pokemon_id,
               target.name AS target_pokemon_name,
               evolution.method, evolution.level, evolution.item_id,
               item.short_name AS item_constant, item.name AS item_name,
               evolution.source_order
        FROM pokemon_evolutions AS evolution
        JOIN pokemon AS source ON source.id = evolution.source_pokemon_id
        JOIN pokemon AS target ON target.id = evolution.target_pokemon_id
        LEFT JOIN items AS item ON item.id = evolution.item_id
        ORDER BY evolution.source_pokemon_id, evolution.source_order,
                 evolution.id
        """,
    ):
        evolution_id = row["id"]
        method = row["method"]
        _require_reference(
            row["source_pokemon_id"], pokemon_ids, f"Evolution {evolution_id} source"
        )
        _require_reference(
            row["target_pokemon_id"], pokemon_ids, f"Evolution {evolution_id} target"
        )
        if method == "item":
            _require_reference(
                row["item_id"], item_ids, f"Evolution {evolution_id} item"
            )
        elif method in {"level", "trade"}:
            if row["item_id"] is not None:
                raise CaptureQuestImportError(
                    f"Evolution {evolution_id} method {method!r} has an item"
                )
        else:
            raise CaptureQuestImportError(
                f"Evolution {evolution_id} has unsupported method {method!r}"
            )
        result.append(
            {
                "evolutionId": evolution_id,
                "sourcePokemonId": row["source_pokemon_id"],
                "sourcePokemon": row["source_pokemon_name"],
                "targetPokemonId": row["target_pokemon_id"],
                "targetPokemon": row["target_pokemon_name"],
                "method": method,
                "level": row["level"] if method == "level" else None,
                "itemId": row["item_id"],
                "itemConstant": row["item_constant"],
                "itemName": row["item_name"],
                "sourceOrder": row["source_order"],
            }
        )
    return result


def _load_world_data(
    conn: sqlite3.Connection,
    map_ids: set[int],
    item_ids: set[int],
) -> dict[str, Any]:
    tilesets = []
    for row in _query(
        conn,
        """
        SELECT id, name, source_tileset_id, blockset_path, tileset_path
        FROM tilesets ORDER BY id
        """,
    ):
        tilesets.append(
            {
                "tilesetId": row["id"],
                "name": row["name"],
                "sourceTilesetId": row["source_tileset_id"],
                "blockset": (
                    portable_reference("repository", row["blockset_path"])
                    if row["blockset_path"] is not None
                    else None
                ),
                "image": (
                    portable_reference("repository", row["tileset_path"])
                    if row["tileset_path"] is not None
                    else None
                ),
            }
        )
    tileset_ids = {row["tilesetId"] for row in tilesets}
    for row in tilesets:
        _require_reference(
            row["sourceTilesetId"],
            tileset_ids,
            f"Tileset {row['tilesetId']} source tileset",
            allow_none=True,
        )

    connections = []
    for row in _query(
        conn,
        """
        SELECT id, from_map_id, to_map_id, direction, offset
        FROM map_connections ORDER BY from_map_id, direction, to_map_id, id
        """,
    ):
        _require_reference(row["from_map_id"], map_ids, f"Connection {row['id']} source")
        _require_reference(row["to_map_id"], map_ids, f"Connection {row['id']} target")
        connections.append(
            {
                "connectionId": row["id"],
                "fromMapId": row["from_map_id"],
                "toMapId": row["to_map_id"],
                "direction": row["direction"],
                "offset": row["offset"],
            }
        )

    positions = []
    for row in _query(
        conn,
        """
        SELECT map_id, map_name, x_offset, y_offset
        FROM overworld_map_positions ORDER BY map_id
        """,
    ):
        _require_reference(row["map_id"], map_ids, "Overworld position")
        positions.append(
            {
                "mapId": row["map_id"],
                "mapLabel": row["map_name"],
                "xOffset": row["x_offset"],
                "yOffset": row["y_offset"],
            }
        )

    tile_images = []
    for row in _query(
        conn,
        """
        SELECT id, tileset_id, block_index, position, image_path, image_hash
        FROM tile_images ORDER BY id
        """,
    ):
        _require_reference(
            row["tileset_id"], tileset_ids, f"Tile image {row['id']} tileset"
        )
        tile_images.append(
            {
                "tileImageId": row["id"],
                "tilesetId": row["tileset_id"],
                "blockIndex": row["block_index"],
                "position": row["position"],
                "image": portable_reference("graphics_output", row["image_path"]),
                "imageHash": row["image_hash"],
            }
        )
    tile_image_ids = {row["tileImageId"] for row in tile_images}

    tiles = []
    for row in _query(
        conn,
        """
        SELECT id, x, y, local_x, local_y, map_id, tile_image_id,
               is_overworld, collision_type
        FROM tiles ORDER BY id
        """,
    ):
        _require_reference(row["map_id"], map_ids, f"Tile {row['id']} map")
        _require_reference(
            row["tile_image_id"], tile_image_ids, f"Tile {row['id']} image"
        )
        tiles.append(
            {
                "tileId": row["id"],
                "mapId": row["map_id"],
                "x": row["x"],
                "y": row["y"],
                "localX": row["local_x"],
                "localY": row["local_y"],
                "tileImageId": row["tile_image_id"],
                "isOverworld": bool(row["is_overworld"]),
                "collisionType": row["collision_type"],
            }
        )

    objects = []
    for row in _query(
        conn,
        """
        SELECT id, name, map_id, object_type, x, y, local_x, local_y,
               spriteset_id, sprite_name, text, action_type,
               action_direction, item_id, movement_type, trainer_class,
               trainer_party_index
        FROM objects ORDER BY map_id, id
        """,
    ):
        _require_reference(
            row["map_id"], map_ids, f"Object {row['id']} map", allow_none=True
        )
        _require_reference(
            row["item_id"], item_ids, f"Object {row['id']} item", allow_none=True
        )
        objects.append(
            {
                "objectId": row["id"],
                "name": row["name"],
                "mapId": row["map_id"],
                "type": row["object_type"],
                "x": row["x"],
                "y": row["y"],
                "localX": row["local_x"],
                "localY": row["local_y"],
                "spritesetId": row["spriteset_id"],
                "sprite": row["sprite_name"],
                "text": row["text"],
                "actionType": row["action_type"],
                "actionDirection": row["action_direction"],
                "itemId": row["item_id"],
                "movementType": row["movement_type"],
                "trainerClass": row["trainer_class"],
                "trainerPartyIndex": row["trainer_party_index"],
            }
        )
    return {
        "tilesets": tilesets,
        "connections": connections,
        "overworldPositions": positions,
        "tileImages": tile_images,
        "tiles": tiles,
        "objects": objects,
    }


def _load_moves_and_pokemon(
    conn: sqlite3.Connection,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    move_rows = _query(
        conn,
        """
        SELECT id, constant_name, name, type, power, accuracy, pp,
               field_move_effect, is_hm
        FROM moves ORDER BY id
        """,
    )
    moves = [
        {
            "moveId": row["id"],
            "sourceConstant": row["constant_name"],
            "name": row["name"],
            "type": row["type"],
            "power": row["power"],
            "accuracy": row["accuracy"],
            "pp": row["pp"],
            "fieldMoveEffect": row["field_move_effect"],
            "isHm": bool(row["is_hm"]),
        }
        for row in move_rows
    ]
    move_ids = {row["moveId"] for row in moves}
    defaults: dict[int, list[dict[str, Any]]] = {}
    for row in _query(
        conn,
        """
        SELECT default_move.pokemon_id, default_move.slot_index,
               default_move.move_id, default_move.source_move_name,
               move.constant_name, move.name
        FROM pokemon_default_moves AS default_move
        JOIN moves AS move ON move.id = default_move.move_id
        ORDER BY default_move.pokemon_id, default_move.slot_index
        """,
    ):
        if row["move_id"] not in move_ids:
            raise CaptureQuestImportError(
                f"Default move refers to unknown move ID {row['move_id']!r}"
            )
        defaults.setdefault(row["pokemon_id"], []).append(
            {
                "slot": row["slot_index"],
                "moveId": row["move_id"],
                "sourceConstant": row["source_move_name"],
                "canonicalConstant": row["constant_name"],
                "name": row["name"],
            }
        )
    pokemon = []
    for row in _query(
        conn,
        """
        SELECT id, name, hp, atk, def, spd, spc, type_1, type_2,
               catch_rate, base_exp, base_cry, cry_pitch, cry_length,
               pokedex_type, height, weight, pokedex_text,
               icon_image, palette_type
        FROM pokemon ORDER BY id
        """,
    ):
        pokemon.append(
            {
                "pokemonId": row["id"],
                "name": row["name"],
                "stats": {
                    "hp": row["hp"],
                    "attack": row["atk"],
                    "defense": row["def"],
                    "speed": row["spd"],
                    "special": row["spc"],
                },
                "types": [row["type_1"], row["type_2"]],
                "catchRate": row["catch_rate"],
                "baseExperience": row["base_exp"],
                "cry": {
                    "base": row["base_cry"],
                    "pitch": row["cry_pitch"],
                    "length": row["cry_length"],
                },
                "pokedex": {
                    "category": row["pokedex_type"],
                    "height": row["height"],
                    "weight": row["weight"],
                    "text": row["pokedex_text"],
                },
                "iconImage": row["icon_image"],
                "paletteType": row["palette_type"],
                # The normalized relationship is authoritative.  Deliberately
                # do not read the four legacy scalar compatibility columns.
                "defaultMoves": defaults.get(row["id"], []),
            }
        )
    pokemon_ids = {row["pokemonId"] for row in pokemon}
    dangling = sorted(set(defaults) - pokemon_ids)
    if dangling:
        raise CaptureQuestImportError(
            f"Default moves refer to unknown Pokemon IDs: {dangling}"
        )
    return moves, pokemon


def _load_encounters(
    conn: sqlite3.Connection, release_code: str, map_ids: set[int]
) -> list[dict[str, Any]]:
    rows = _query(
        conn,
        """
        SELECT id, map_id, map_name, source_label, encounter_type,
               encounter_rate, slot_index, pokemon_name, level, version
        FROM wild_encounters
        WHERE version IN (?, 'both')
        ORDER BY map_id, encounter_type, slot_index, version, id
        """,
        (release_code,),
    )
    result = []
    for row in rows:
        is_global = row["map_id"] is None and row["map_name"] == "GLOBAL"
        if not is_global and row["map_id"] not in map_ids:
            raise CaptureQuestImportError(
                f"Encounter {row['id']} has unknown map ID {row['map_id']!r}"
            )
        result.append(
            {
                "encounterId": row["id"],
                "scope": "global" if is_global else "map",
                "mapId": row["map_id"],
                "mapLabel": row["map_name"],
                "sourceLabel": row["source_label"],
                "type": row["encounter_type"],
                "rate": row["encounter_rate"],
                "slot": row["slot_index"],
                "pokemon": row["pokemon_name"],
                "level": row["level"],
                "sourceVersion": row["version"],
            }
        )
    return result


def _load_encounter_slots(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        {
            "slot": row["slot_index"],
            "probability": row["probability"],
            "cumulativeProbability": row["cumulative_probability"],
        }
        for row in _query(
            conn,
            """
            SELECT slot_index, probability, cumulative_probability
            FROM encounter_slots ORDER BY slot_index
            """,
        )
    ]


def _load_learnsets(
    conn: sqlite3.Connection,
    pokemon_ids: set[int],
    move_ids: set[int],
) -> dict[str, list[dict[str, Any]]]:
    level_up = []
    for row in _query(
        conn,
        """
        SELECT id, pokemon_id, pokemon_name, level, move_name, move_id
        FROM pokemon_learnset
        ORDER BY pokemon_id, level, id
        """,
    ):
        _require_reference(
            row["pokemon_id"], pokemon_ids, f"Learnset row {row['id']} Pokemon"
        )
        _require_reference(row["move_id"], move_ids, f"Learnset row {row['id']} move")
        level_up.append(
            {
                "learnsetId": row["id"],
                "pokemonId": row["pokemon_id"],
                "pokemon": row["pokemon_name"],
                "level": row["level"],
                "moveConstant": row["move_name"],
                "moveId": row["move_id"],
            }
        )

    tm_hm = []
    for row in _query(
        conn,
        """
        SELECT id, pokemon_id, pokemon_name, tm_hm_name, move_name,
               move_id, is_hm
        FROM pokemon_tmhm
        ORDER BY pokemon_id, is_hm, tm_hm_name, id
        """,
    ):
        _require_reference(
            row["pokemon_id"], pokemon_ids, f"TM/HM row {row['id']} Pokemon"
        )
        _require_reference(
            row["move_id"],
            move_ids,
            f"TM/HM row {row['id']} move",
            allow_none=True,
        )
        tm_hm.append(
            {
                "compatibilityId": row["id"],
                "pokemonId": row["pokemon_id"],
                "pokemon": row["pokemon_name"],
                "machine": row["tm_hm_name"],
                "moveConstant": row["move_name"],
                "moveId": row["move_id"],
                "isHm": bool(row["is_hm"]),
            }
        )
    return {"levelUp": level_up, "tmHm": tm_hm}


def _load_trainers(
    conn: sqlite3.Connection,
    map_ids: set[int],
    pokemon_by_name: Mapping[str, int],
) -> dict[str, list[dict[str, Any]]]:
    classes = [
        {
            "trainerClassId": row["id"],
            "sourceConstant": row["constant_name"],
            "name": row["display_name"],
            "baseMoney": row["base_money"],
            "isGymLeader": bool(row["is_gym_leader"]),
            "isEliteFour": bool(row["is_elite_four"]),
            "isRival": bool(row["is_rival"]),
        }
        for row in _query(
            conn,
            """
            SELECT id, constant_name, display_name, base_money,
                   is_gym_leader, is_elite_four, is_rival
            FROM trainer_classes ORDER BY id
            """,
        )
    ]
    class_ids = {row["trainerClassId"] for row in classes}

    parties = []
    for row in _query(
        conn,
        """
        SELECT id, trainer_class_id, party_index, location_comment,
               is_variable_level
        FROM trainer_parties ORDER BY trainer_class_id, party_index, id
        """,
    ):
        _require_reference(
            row["trainer_class_id"], class_ids, f"Trainer party {row['id']} class"
        )
        parties.append(
            {
                "trainerPartyId": row["id"],
                "trainerClassId": row["trainer_class_id"],
                "partyIndex": row["party_index"],
                "locationComment": row["location_comment"],
                "isVariableLevel": bool(row["is_variable_level"]),
            }
        )
    party_ids = {row["trainerPartyId"] for row in parties}

    party_pokemon = []
    for row in _query(
        conn,
        """
        SELECT id, trainer_party_id, slot_index, pokemon_name, level
        FROM trainer_party_pokemon
        ORDER BY trainer_party_id, slot_index, id
        """,
    ):
        _require_reference(
            row["trainer_party_id"],
            party_ids,
            f"Trainer party Pokemon {row['id']} party",
        )
        pokemon_id = pokemon_by_name.get(row["pokemon_name"])
        if pokemon_id is None:
            raise CaptureQuestImportError(
                f"Trainer party Pokemon {row['id']} names unknown Pokemon "
                f"{row['pokemon_name']!r}"
            )
        party_pokemon.append(
            {
                "partyPokemonId": row["id"],
                "trainerPartyId": row["trainer_party_id"],
                "slot": row["slot_index"],
                "pokemonId": pokemon_id,
                "pokemon": row["pokemon_name"],
                "level": row["level"],
            }
        )

    headers = []
    for row in _query(
        conn,
        """
        SELECT id, map_name, map_id, header_label, header_index,
               event_flag, sight_range, battle_text_label,
               end_battle_text_label, after_battle_text_label
        FROM trainer_headers ORDER BY map_id, header_index, id
        """,
    ):
        _require_reference(row["map_id"], map_ids, f"Trainer header {row['id']} map")
        headers.append(
            {
                "trainerHeaderId": row["id"],
                "mapId": row["map_id"],
                "mapLabel": row["map_name"],
                "label": row["header_label"],
                "index": row["header_index"],
                "eventFlag": row["event_flag"],
                "sightRange": row["sight_range"],
                "battleTextLabel": row["battle_text_label"],
                "endBattleTextLabel": row["end_battle_text_label"],
                "afterBattleTextLabel": row["after_battle_text_label"],
            }
        )
    return {
        "classes": classes,
        "parties": parties,
        "partyPokemon": party_pokemon,
        "headers": headers,
    }


def _load_text(
    conn: sqlite3.Connection,
    map_ids: set[int],
    source_root: str,
) -> dict[str, list[dict[str, Any]]]:
    dialogue = [
        {
            "dialogueId": row["id"],
            "label": row["label"],
            "sourceFile": _source_reference(source_root, row["source_file"]),
            "text": row["dialogue"],
        }
        for row in _query(
            conn,
            """
            SELECT id, label, source_file, dialogue
            FROM dialogue_text ORDER BY id
            """,
        )
    ]
    pointers = []
    for row in _query(
        conn,
        """
        SELECT id, map_name, map_id, text_constant, local_label,
               dialogue_label, pointer_index, is_trainer
        FROM text_pointers ORDER BY map_id, pointer_index, id
        """,
    ):
        _require_reference(row["map_id"], map_ids, f"Text pointer {row['id']} map")
        pointers.append(
            {
                "textPointerId": row["id"],
                "mapId": row["map_id"],
                "mapLabel": row["map_name"],
                "textConstant": row["text_constant"],
                "localLabel": row["local_label"],
                "dialogueLabel": row["dialogue_label"],
                "pointerIndex": row["pointer_index"],
                "isTrainer": bool(row["is_trainer"]),
            }
        )
    return {"dialogue": dialogue, "pointers": pointers}


def _load_hidden(
    conn: sqlite3.Connection, map_ids: set[int]
) -> dict[str, list[dict[str, Any]]]:
    def locations(table: str, key: str) -> list[dict[str, Any]]:
        rows = []
        for row in _query(
            conn,
            f"""
            SELECT id, map_constant, map_id, x, y
            FROM {table} ORDER BY map_id, id
            """,
        ):
            _require_reference(row["map_id"], map_ids, f"{key} {row['id']} map")
            rows.append(
                {
                    f"{key}Id": row["id"],
                    "mapId": row["map_id"],
                    "mapLabel": row["map_constant"],
                    "x": row["x"],
                    "y": row["y"],
                }
            )
        return rows

    objects = []
    for row in _query(
        conn,
        """
        SELECT id, map_constant, map_id, x, y, item_or_direction,
               routine, object_type
        FROM hidden_objects ORDER BY map_id, id
        """,
    ):
        _require_reference(row["map_id"], map_ids, f"Hidden object {row['id']} map")
        objects.append(
            {
                "hiddenObjectId": row["id"],
                "mapId": row["map_id"],
                "mapLabel": row["map_constant"],
                "x": row["x"],
                "y": row["y"],
                "itemOrDirection": row["item_or_direction"],
                "routine": row["routine"],
                "type": row["object_type"],
            }
        )

    missable = []
    for row in _query(
        conn,
        """
        SELECT id, hs_index, hs_constant, map_constant, map_id,
               object_constant, object_index, object_name, object_type,
               initial_state, initial_visible, label
        FROM missable_objects ORDER BY hs_index, id
        """,
    ):
        _require_reference(
            row["map_id"], map_ids, f"Missable object {row['id']} map"
        )
        missable.append(
            {
                "missableObjectId": row["id"],
                "hiddenStateIndex": row["hs_index"],
                "hiddenStateConstant": row["hs_constant"],
                "mapId": row["map_id"],
                "mapLabel": row["map_constant"],
                "objectConstant": row["object_constant"],
                "objectIndex": row["object_index"],
                "objectName": row["object_name"],
                "objectType": row["object_type"],
                "initialState": row["initial_state"],
                "initialVisible": bool(row["initial_visible"]),
                "label": row["label"],
            }
        )
    return {
        "items": locations("hidden_items", "hiddenItem"),
        "coins": locations("hidden_coins", "hiddenCoin"),
        "objects": objects,
        "missableObjects": missable,
    }


def _load_map_events(
    conn: sqlite3.Connection,
    map_ids: set[int],
    source_root: str,
) -> dict[str, list[dict[str, Any]]]:
    music = []
    for row in _query(
        conn,
        """
        SELECT id, map_constant, map_id, music_constant
        FROM map_music ORDER BY map_id, id
        """,
    ):
        _require_reference(row["map_id"], map_ids, f"Map music {row['id']} map")
        music.append(
            {
                "mapMusicId": row["id"],
                "mapId": row["map_id"],
                "mapLabel": row["map_constant"],
                "musicConstant": row["music_constant"],
            }
        )

    scripts = []
    for row in _query(
        conn,
        """
        SELECT id, map_name, map_id, script_index, script_label,
               script_constant, raw_asm
        FROM map_scripts ORDER BY map_id, script_index, id
        """,
    ):
        _require_reference(row["map_id"], map_ids, f"Map script {row['id']} map")
        scripts.append(
            {
                "mapScriptId": row["id"],
                "mapId": row["map_id"],
                "mapLabel": row["map_name"],
                "scriptIndex": row["script_index"],
                "scriptLabel": row["script_label"],
                "scriptConstant": row["script_constant"],
                "rawAsm": row["raw_asm"],
            }
        )

    movements = []
    for row in _query(
        conn,
        """
        SELECT id, map_name, map_id, label, movements
        FROM npc_movement_data ORDER BY map_id, label, id
        """,
    ):
        _require_reference(row["map_id"], map_ids, f"NPC movement {row['id']} map")
        movements.append(
            {
                "movementId": row["id"],
                "mapId": row["map_id"],
                "mapLabel": row["map_name"],
                "label": row["label"],
                "movements": row["movements"],
            }
        )

    spin_tiles = []
    for row in _query(
        conn,
        """
        SELECT id, map_name, map_id, source_label, x, y,
               movement_label, movements
        FROM spin_tiles ORDER BY map_id, y, x, id
        """,
    ):
        _require_reference(row["map_id"], map_ids, f"Spin tile {row['id']} map")
        spin_tiles.append(
            {
                "spinTileId": row["id"],
                "mapId": row["map_id"],
                "mapLabel": row["map_name"],
                "sourceLabel": row["source_label"],
                "x": row["x"],
                "y": row["y"],
                "movementLabel": row["movement_label"],
                "movements": row["movements"],
            }
        )

    flags = []
    for row in _query(
        conn,
        """
        SELECT id, map_name, map_id, flag_name, operation, context_label
        FROM event_flags ORDER BY map_id, id
        """,
    ):
        _require_reference(row["map_id"], map_ids, f"Event flag {row['id']} map")
        flags.append(
            {
                "eventFlagId": row["id"],
                "mapId": row["map_id"],
                "mapLabel": row["map_name"],
                "flag": row["flag_name"],
                "operation": row["operation"],
                "contextLabel": row["context_label"],
            }
        )

    triggers = []
    for row in _query(
        conn,
        """
        SELECT id, map_name, map_id, label, x, y
        FROM coordinate_triggers ORDER BY map_id, y, x, label, id
        """,
    ):
        _require_reference(
            row["map_id"], map_ids, f"Coordinate trigger {row['id']} map"
        )
        triggers.append(
            {
                "coordinateTriggerId": row["id"],
                "mapId": row["map_id"],
                "mapLabel": row["map_name"],
                "label": row["label"],
                "x": row["x"],
                "y": row["y"],
            }
        )

    warp_events = []
    for row in _query(
        conn,
        """
        SELECT id, map_name, map_id, source_warp_index, x, y,
               dest_map, dest_kind, dest_map_id, dest_warp_index,
               source_file
        FROM warp_events ORDER BY map_id, source_warp_index, id
        """,
    ):
        _require_reference(row["map_id"], map_ids, f"Warp event {row['id']} map")
        kind = row["dest_kind"]
        if kind == "fixed":
            _require_reference(
                row["dest_map_id"], map_ids, f"Warp event {row['id']} destination"
            )
        elif kind == "last-map":
            if row["dest_map_id"] is not None:
                raise CaptureQuestImportError(
                    f"Dynamic warp event {row['id']} must not invent a destination"
                )
        else:
            raise CaptureQuestImportError(
                f"Warp event {row['id']} has unsupported destination kind {kind!r}"
            )
        warp_events.append(
            {
                "warpEventId": row["id"],
                "mapId": row["map_id"],
                "mapLabel": row["map_name"],
                "sourceWarpIndex": row["source_warp_index"],
                "x": row["x"],
                "y": row["y"],
                "destination": {
                    "kind": kind,
                    "mapId": row["dest_map_id"],
                    "mapLabel": row["dest_map"],
                    "warpIndex": row["dest_warp_index"],
                },
                "sourceFile": _source_reference(source_root, row["source_file"]),
            }
        )
    return {
        "music": music,
        "scripts": scripts,
        "npcMovements": movements,
        "spinTiles": spin_tiles,
        "eventFlags": flags,
        "coordinateTriggers": triggers,
        "warps": warp_events,
    }


def _load_special_rules(
    conn: sqlite3.Connection,
    map_ids: set[int],
    source_root: str,
) -> dict[str, list[dict[str, Any]]]:
    trades = [
        {
            "tradeId": row["id"],
            "tradeKey": row["trade_key"],
            "mapLabel": row["map_name"],
            "scriptLabel": row["script_label"],
            "textConstant": row["text_constant"],
            "requestedPokemon": row["requested_pokemon"],
            "offeredPokemon": row["offered_pokemon"],
            "offeredNickname": row["offered_nickname"],
            "dialogueSet": row["dialogue_set"],
            "originalTradeIndex": row["original_trade_index"],
            "active": bool(row["active"]),
            "sourceFile": _source_reference(source_root, row["source_file"]),
        }
        for row in _query(
            conn,
            """
            SELECT id, trade_key, map_name, script_label, text_constant,
                   requested_pokemon, offered_pokemon, offered_nickname,
                   dialogue_set, original_trade_index, active, source_file
            FROM script_event_in_game_trades ORDER BY original_trade_index, id
            """,
        )
    ]

    tile_overrides = []
    for row in _query(
        conn,
        """
        SELECT id, map_name, script_label, candidate_json
        FROM script_event_tile_overrides ORDER BY map_name, script_label, id
        """,
    ):
        candidate = _json_value(
            row["candidate_json"], f"script_event_tile_overrides row {row['id']}"
        )
        if not isinstance(candidate, dict):
            raise CaptureQuestImportError(
                f"Tile override {row['id']} candidate must be a JSON object"
            )
        tile_overrides.append(
            {
                "tileOverrideId": row["id"],
                "mapLabel": row["map_name"],
                "scriptLabel": row["script_label"],
                "candidate": candidate,
            }
        )

    boulder_targets = []
    for row in _query(
        conn,
        """
        SELECT id, target_family, map_name, source_label, x, y, flag,
               drops_through_hole, source_missable_object,
               destination_map_name, destination_missable_object,
               source_file, target_json
        FROM script_event_boulder_targets
        ORDER BY target_family, map_name, y, x, id
        """,
    ):
        target = _json_value(
            row["target_json"], f"script_event_boulder_targets row {row['id']}"
        )
        if not isinstance(target, dict):
            raise CaptureQuestImportError(
                f"Boulder target {row['id']} target must be a JSON object"
            )
        boulder_targets.append(
            {
                "boulderTargetId": row["id"],
                "family": row["target_family"],
                "mapLabel": row["map_name"],
                "sourceLabel": row["source_label"],
                "x": row["x"],
                "y": row["y"],
                "flag": row["flag"],
                "dropsThroughHole": bool(row["drops_through_hole"]),
                "sourceMissableObject": row["source_missable_object"],
                "destinationMapLabel": row["destination_map_name"],
                "destinationMissableObject": row["destination_missable_object"],
                "sourceFile": _source_reference(source_root, row["source_file"]),
                "target": target,
            }
        )

    visibility = []
    for row in _query(
        conn,
        """
        SELECT id, map_name, map_id, object_name, object_key, script_label,
               requires_event, visible, label, rule_json
        FROM script_event_object_visibility
        ORDER BY map_id, object_key, requires_event, visible, label, id
        """,
    ):
        _require_reference(
            row["map_id"], map_ids, f"Object visibility rule {row['id']} map"
        )
        rule = _json_value(
            row["rule_json"], f"script_event_object_visibility row {row['id']}"
        )
        if not isinstance(rule, dict):
            raise CaptureQuestImportError(
                f"Object visibility rule {row['id']} must be a JSON object"
            )
        visibility.append(
            {
                "objectVisibilityId": row["id"],
                "mapId": row["map_id"],
                "mapLabel": row["map_name"],
                "objectName": row["object_name"],
                "objectKey": row["object_key"],
                "scriptLabel": row["script_label"],
                "requiresEvent": row["requires_event"],
                "visible": bool(row["visible"]),
                "label": row["label"],
                "rule": rule,
            }
        )

    dialogue = []
    for row in _query(
        conn,
        """
        SELECT id, text_constant, map_name, script_label, priority,
               requires_flags_json, requires_flags_absent_json,
               dialogue_labels_json, source_json, row_json
        FROM script_event_conditional_dialogue
        ORDER BY text_constant, priority, script_label, id
        """,
    ):
        values = {
            "requiresFlags": _json_value(
                row["requires_flags_json"],
                f"conditional dialogue {row['id']} requires flags",
            ),
            "requiresFlagsAbsent": _json_value(
                row["requires_flags_absent_json"],
                f"conditional dialogue {row['id']} absent flags",
            ),
            "dialogueLabels": _json_value(
                row["dialogue_labels_json"],
                f"conditional dialogue {row['id']} labels",
            ),
            "source": _json_value(
                row["source_json"], f"conditional dialogue {row['id']} source"
            ),
            "row": _json_value(
                row["row_json"], f"conditional dialogue {row['id']} row"
            ),
        }
        if not all(isinstance(values[name], list) for name in (
            "requiresFlags", "requiresFlagsAbsent", "dialogueLabels"
        )) or not isinstance(values["source"], dict) or not isinstance(
            values["row"], dict
        ):
            raise CaptureQuestImportError(
                f"Conditional dialogue {row['id']} has invalid normalized JSON"
            )
        dialogue.append(
            {
                "conditionalDialogueId": row["id"],
                "textConstant": row["text_constant"],
                "mapLabel": row["map_name"],
                "scriptLabel": row["script_label"],
                "priority": row["priority"],
                **values,
            }
        )
    return {
        "inGameTrades": trades,
        "tileOverrides": tile_overrides,
        "boulderTargets": boulder_targets,
        "objectVisibility": visibility,
        "conditionalDialogue": dialogue,
    }


def _load_candidates(
    conn: sqlite3.Connection, map_ids: set[int]
) -> list[dict[str, Any]]:
    candidates = []
    for row in _query(
        conn,
        """
        SELECT id, map_name, map_id, script_label, trigger_type,
               trigger_label, confidence
        FROM script_event_candidates
        ORDER BY map_id, script_label, trigger_type, trigger_label, id
        """,
    ):
        candidate_id = row["id"]
        if row["map_id"] not in map_ids:
            raise CaptureQuestImportError(
                f"Script candidate {candidate_id} has unknown map ID {row['map_id']!r}"
            )
        action_rows = _query(
            conn,
            """
            SELECT action_index, action_type, action_json
            FROM script_event_candidate_actions
            WHERE candidate_id = ? ORDER BY action_index
            """,
            (candidate_id,),
        )
        action_values = []
        action_indexes = []
        for action_row in action_rows:
            value = _json_value(
                action_row["action_json"],
                f"script_event_candidate_actions candidate {candidate_id}",
            )
            if not isinstance(value, dict) or value.get("type") != action_row["action_type"]:
                raise CaptureQuestImportError(
                    f"Normalized action type mismatch for candidate {candidate_id}"
                )
            action_values.append(value)
            action_indexes.append(action_row["action_index"])

        # Apply the legacy CaptureQuest movement customization to the
        # normalized actions reconstructed above, not candidate_json.
        profiled = {
            "scriptLabel": row["script_label"],
            "actions": deepcopy(action_values),
            "source": {},
        }
        PROFILE.customize_candidates([profiled])
        actions = [
            {"index": index, "value": value}
            for index, value in zip(action_indexes, profiled["actions"])
        ]
        conditions = [
            {
                "path": condition["condition_path"],
                "index": condition["value_index"],
                "value": _json_value(
                    condition["condition_value_json"],
                    f"script_event_candidate_conditions candidate {candidate_id}",
                ),
            }
            for condition in _query(
                conn,
                """
                SELECT condition_path, value_index, condition_value_json
                FROM script_event_candidate_conditions
                WHERE candidate_id = ? ORDER BY condition_path, value_index
                """,
                (candidate_id,),
            )
        ]
        references = [
            {
                "kind": reference["reference_kind"],
                "path": reference["json_path"],
                "index": reference["reference_index"],
                "value": _json_value(
                    reference["reference_value_json"],
                    f"script_event_candidate_references candidate {candidate_id}",
                ),
            }
            for reference in _query(
                conn,
                """
                SELECT reference_kind, json_path, reference_index,
                       reference_value_json
                FROM script_event_candidate_references
                WHERE candidate_id = ?
                ORDER BY reference_kind, json_path, reference_index
                """,
                (candidate_id,),
            )
        ]
        runtime_script = SCRIPT_MAPPINGS.get(
            (row["map_name"], row["script_label"])
        )
        candidates.append(
            {
                "candidateId": candidate_id,
                "mapId": row["map_id"],
                "mapLabel": row["map_name"],
                "scriptLabel": row["script_label"],
                "trigger": {
                    "type": row["trigger_type"],
                    "label": row["trigger_label"],
                },
                "confidence": row["confidence"],
                "captureQuestRuntimeScript": runtime_script,
                "captureQuestProfile": profiled["source"] or None,
                "actions": actions,
                "conditions": conditions,
                "references": references,
            }
        )
    return candidates


def _load_ir_blocks(
    conn: sqlite3.Connection, map_ids: set[int]
) -> list[dict[str, Any]]:
    result = []
    for row in _query(
        conn,
        """
        SELECT id, map_name, map_id, label, kind, raw_asm
        FROM script_event_ir_blocks
        ORDER BY map_id, label, kind, id
        """,
    ):
        if row["map_id"] not in map_ids:
            raise CaptureQuestImportError(
                f"Script IR block {row['id']} has unknown map ID {row['map_id']!r}"
            )
        references = [
            {
                "kind": reference["reference_kind"],
                "index": reference["reference_index"],
                "value": _json_value(
                    reference["reference_value_json"],
                    f"script_event_ir_references block {row['id']}",
                ),
            }
            for reference in _query(
                conn,
                """
                SELECT reference_kind, reference_index, reference_value_json
                FROM script_event_ir_references
                WHERE ir_block_id = ?
                ORDER BY reference_kind, reference_index
                """,
                (row["id"],),
            )
        ]
        result.append(
            {
                "blockId": row["id"],
                "mapId": row["map_id"],
                "mapLabel": row["map_name"],
                "label": row["label"],
                "kind": row["kind"],
                "rawAsm": row["raw_asm"],
                "references": references,
            }
        )
    return result


def _load_diagnostics(
    conn: sqlite3.Connection, map_ids: set[int]
) -> list[dict[str, Any]]:
    """Apply legacy runtime mappings to their source diagnostic rows."""

    result = []
    for row in _query(
        conn,
        """
        SELECT id, map_name, map_id, script_label, status, reason, details_json
        FROM script_event_candidate_diagnostics
        ORDER BY map_name, script_label, status, id
        """,
    ):
        is_global = row["map_name"] == "GLOBAL" and row["map_id"] is None
        if not is_global and row["map_id"] not in map_ids:
            raise CaptureQuestImportError(
                f"Script diagnostic {row['id']} has unknown map ID {row['map_id']!r}"
            )
        details = _json_value(
            row["details_json"],
            f"script_event_candidate_diagnostics row {row['id']}",
        )
        if not isinstance(details, dict):
            raise CaptureQuestImportError(
                f"Script diagnostic {row['id']} details must be a JSON object"
            )
        profiled = {
            "mapName": row["map_name"],
            "scriptLabel": row["script_label"],
            "status": row["status"],
            "reason": row["reason"],
            "details": deepcopy(details),
        }
        PROFILE.customize_diagnostics([profiled])
        result.append(
            {
                "diagnosticId": row["id"],
                "scope": "global" if is_global else "map",
                "mapId": row["map_id"],
                "mapLabel": row["map_name"],
                "scriptLabel": row["script_label"],
                "status": profiled["status"],
                "reason": profiled["reason"],
                "captureQuestRuntimeScript": SCRIPT_MAPPINGS.get(
                    (row["map_name"], row["script_label"])
                ),
                "details": profiled["details"],
            }
        )
    return result


def _load_graphics(conn: sqlite3.Connection) -> dict[str, Any]:
    assets = []
    for row in _query(
        conn,
        """
        SELECT asset.id, asset.asset_role, asset.path_scope, asset.relative_path,
               asset.sha256, asset.byte_size, asset.width_px, asset.height_px,
               asset.pixel_mode, asset.tile_count, format.extension,
               format.media_type, format.family, category.category_path
        FROM graphic_assets AS asset
        JOIN graphic_formats AS format ON format.id = asset.format_id
        JOIN graphic_categories AS category ON category.id = asset.category_id
        ORDER BY asset.id
        """,
    ):
        assets.append(
            {
                "assetId": row["id"],
                "role": row["asset_role"],
                "path": portable_reference(row["path_scope"], row["relative_path"]),
                "sha256": row["sha256"],
                "byteSize": row["byte_size"],
                "width": row["width_px"],
                "height": row["height_px"],
                "pixelMode": row["pixel_mode"],
                "tileCount": row["tile_count"],
                "format": {
                    "extension": row["extension"],
                    "mediaType": row["media_type"],
                    "family": row["family"],
                },
                "category": row["category_path"],
            }
        )
    source_links = [
        {
            "sourceAssetId": row["source_asset_id"],
            "companionAssetId": row["companion_asset_id"],
            "relation": row["relation_type"],
        }
        for row in _query(
            conn,
            """
            SELECT source_asset_id, companion_asset_id, relation_type
            FROM graphic_source_links
            ORDER BY source_asset_id, companion_asset_id, relation_type
            """,
        )
    ]
    derivations = [
        {
            "sourceAssetId": row["source_asset_id"],
            "derivedAssetId": row["derived_asset_id"],
            "transformation": row["transformation"],
            "decoderVersion": row["decoder_version"],
            "layout": row["layout"],
            "tilesPerRow": row["tiles_per_row"],
            "tileCount": row["tile_count"],
        }
        for row in _query(
            conn,
            """
            SELECT source_asset_id, derived_asset_id, transformation,
                   decoder_version, layout, tiles_per_row, tile_count
            FROM graphic_derivations
            ORDER BY source_asset_id, derived_asset_id
            """,
        )
    ]
    return {"assets": assets, "sourceLinks": source_links, "derivations": derivations}


def _load_audio(conn: sqlite3.Connection) -> dict[str, Any]:
    assets = [
        {
            "assetKey": row["asset_key"],
            "kind": row["asset_kind"],
            "constant": row["constant"],
            "displayName": row["display_name"],
            "baseAssetKey": row["base_asset_key"],
            "bank": row["audio_bank"],
            "audioId": row["audio_id"],
            "frequencyModifier": row["frequency_modifier"],
            "tempoModifier": row["tempo_modifier"],
            "loop": bool(row["loop_enabled"]),
            "loopMode": row["loop_mode"],
            "ogg": _audio_reference(row["ogg_path"]),
            "flac": _audio_reference(row["flac_path"]),
        }
        for row in _query(
            conn,
            """
            SELECT asset_key, asset_kind, constant, display_name, base_asset_key,
                   audio_bank, audio_id, frequency_modifier, tempo_modifier,
                   loop_enabled, loop_mode, ogg_path, flac_path
            FROM audio_assets ORDER BY asset_key
            """,
        )
    ]
    sources = [
        {
            "assetKey": row["asset_key"],
            "role": row["source_role"],
            "path": portable_reference("repository", row["source_path"]),
            "label": row["source_label"],
        }
        for row in _query(
            conn,
            """
            SELECT asset_key, source_role, source_path, source_label
            FROM audio_asset_sources
            ORDER BY asset_key, source_role, source_path, source_label
            """,
        )
    ]
    map_music = [
        {"mapId": row["map_id"], "assetKey": row["asset_key"]}
        for row in _query(
            conn,
            "SELECT map_id, asset_key FROM map_music_assets ORDER BY map_id",
        )
    ]
    move_audio = [
        {"moveId": row["move_id"], "assetKey": row["asset_key"]}
        for row in _query(
            conn,
            "SELECT move_id, asset_key FROM move_audio_assets ORDER BY move_id",
        )
    ]
    cries = [
        {
            "internalIndex": row["internal_index"],
            "pokemonId": row["pokemon_id"],
            "pokemonName": row["pokemon_name"],
            "isGlitchSlot": bool(row["is_glitch_slot"]),
            "assetKey": row["asset_key"],
        }
        for row in _query(
            conn,
            """
            SELECT internal_index, pokemon_id, pokemon_name, is_glitch_slot, asset_key
            FROM pokemon_cry_assets ORDER BY internal_index
            """,
        )
    ]
    return {
        "assets": assets,
        "sources": sources,
        "mapMusic": map_music,
        "moveSounds": move_audio,
        "pokemonCries": cries,
    }


def build_capturequest_bundle(
    conn: sqlite3.Connection, *, release: str
) -> dict[str, Any]:
    """Build deterministic CaptureQuest records from an open schema-v2 DB."""

    try:
        if conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise CaptureQuestImportError("SQLite quick_check failed")
        schema = negotiate_schema(conn)
        _require_tables(conn)
        foreign_key_error = conn.execute("PRAGMA foreign_key_check").fetchone()
        if foreign_key_error is not None:
            raise CaptureQuestImportError(
                f"Extractor database has a foreign-key violation: {foreign_key_error}"
            )
        run, selected_release = _load_release_context(conn, release, schema)
        maps, map_ids = _load_maps(conn)
        moves, pokemon = _load_moves_and_pokemon(conn)
        move_ids = {row["moveId"] for row in moves}
        pokemon_ids = {row["pokemonId"] for row in pokemon}
        pokemon_by_name = {row["name"]: row["pokemonId"] for row in pokemon}
        items, item_ids = _load_items(conn, move_ids)
        release_code = selected_release["code"]
        source_root = run["sourceRoot"]["relativePath"]
        return {
            "adapter": {
                "schemaName": ADAPTER_SCHEMA_NAME,
                "schemaVersion": ADAPTER_SCHEMA_VERSION,
            },
            "extractor": {"schema": schema, "run": run},
            "release": selected_release,
            "maps": maps,
            "warps": _load_warps(conn, map_ids),
            "world": _load_world_data(conn, map_ids, item_ids),
            "items": items,
            "moves": moves,
            "pokemon": pokemon,
            "pokemonEvolutions": _load_evolutions(
                conn, pokemon_ids, item_ids
            ),
            "learnsets": _load_learnsets(conn, pokemon_ids, move_ids),
            "wildEncounters": _load_encounters(conn, release_code, map_ids),
            "encounterSlots": _load_encounter_slots(conn),
            "trainers": _load_trainers(
                conn, map_ids, pokemon_by_name
            ),
            "text": _load_text(conn, map_ids, source_root),
            "hidden": _load_hidden(conn, map_ids),
            "mapEvents": _load_map_events(conn, map_ids, source_root),
            "scriptCandidates": _load_candidates(conn, map_ids),
            "scriptDiagnostics": _load_diagnostics(conn, map_ids),
            "scriptIrBlocks": _load_ir_blocks(conn, map_ids),
            "specialScriptRules": _load_special_rules(
                conn, map_ids, source_root
            ),
            "graphics": _load_graphics(conn),
            "audio": _load_audio(conn),
        }
    except CaptureQuestImportError:
        raise
    except sqlite3.DatabaseError as error:
        raise CaptureQuestImportError(
            f"Malformed or incompatible extractor database: {error}"
        ) from error


def load_capturequest_bundle(
    database: str | Path, *, release: str
) -> dict[str, Any]:
    """Open an existing database read-only and build a CaptureQuest bundle."""

    path = Path(database).expanduser().resolve()
    if not path.is_file():
        raise CaptureQuestImportError(f"Extractor database does not exist: {path}")
    uri = f"{path.as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        return build_capturequest_bundle(conn, release=release)


def dumps_capturequest_bundle(bundle: Mapping[str, Any]) -> str:
    """Serialize a bundle canonically for stable hashing and build inputs."""

    return json.dumps(
        bundle, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for a downstream CaptureQuest bootstrap/build step."""

    parser = argparse.ArgumentParser(
        description="Import a schema-v2 Pokemon extractor DB for CaptureQuest"
    )
    parser.add_argument("database", type=Path, help="freshly generated pokemon.db")
    parser.add_argument(
        "--release", required=True, choices=sorted(SUPPORTED_RELEASES)
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write canonical JSON here instead of standard output",
    )
    arguments = parser.parse_args(argv)
    database_path = arguments.database.expanduser().resolve()
    if arguments.output is not None:
        output_path = arguments.output.expanduser().resolve()
        same_file = output_path == database_path
        if not same_file and output_path.exists() and database_path.exists():
            same_file = os.path.samefile(output_path, database_path)
        if same_file:
            raise CaptureQuestImportError(
                "Adapter output must not replace the extractor database"
            )
        arguments.output = output_path
    payload = dumps_capturequest_bundle(
        load_capturequest_bundle(database_path, release=arguments.release)
    )
    if arguments.output is None:
        print(payload, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=arguments.output.parent,
                prefix=f".{arguments.output.name}.",
                suffix=".tmp",
                delete=False,
            ) as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
                temporary_path = Path(output.name)
            os.replace(temporary_path, arguments.output)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
    return 0


__all__ = [
    "ADAPTER_SCHEMA_NAME",
    "ADAPTER_SCHEMA_VERSION",
    "AssetRoots",
    "CaptureQuestImportError",
    "build_capturequest_bundle",
    "dumps_capturequest_bundle",
    "load_capturequest_bundle",
    "main",
    "negotiate_schema",
    "portable_reference",
]


if __name__ == "__main__":
    raise SystemExit(main())
