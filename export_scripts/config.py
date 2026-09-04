"""Shared paths and constants for extractor scripts."""

import os
from pathlib import Path

_CHECKOUT_ROOT = Path(__file__).resolve().parent.parent
_configured_project_root = os.environ.get("POKEMON_EXTRACTOR_PROJECT_ROOT")
PROJECT_ROOT = Path(_configured_project_root or _CHECKOUT_ROOT).expanduser()
if not PROJECT_ROOT.is_absolute():
    PROJECT_ROOT = Path.cwd() / PROJECT_ROOT
PROJECT_ROOT = PROJECT_ROOT.resolve()


def configured_path(environment_name, default):
    """Resolve an optional path override relative to the project root."""
    configured = os.environ.get(environment_name)
    path = Path(configured) if configured else Path(default)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


DB_PATH = configured_path("POKEMON_EXTRACTOR_DB", PROJECT_ROOT / "pokemon.db")

GAME_DATA_ROOT = configured_path(
    "POKEMON_EXTRACTOR_GAME_DATA_ROOT",
    PROJECT_ROOT / "pokemon-game-data",
)
DATA_DIR = GAME_DATA_ROOT / "data"
CONSTANTS_DIR = GAME_DATA_ROOT / "constants"
GFX_DIR = GAME_DATA_ROOT / "gfx"
AUDIO_DIR = GAME_DATA_ROOT / "audio"
AUDIO_HEADERS_DIR = AUDIO_DIR / "headers"
AUDIO_MUSIC_DIR = AUDIO_DIR / "music"
AUDIO_SFX_DIR = AUDIO_DIR / "sfx"

MAPS_DIR = GAME_DATA_ROOT / "maps"
MAP_DATA_DIR = DATA_DIR / "maps"
MAP_HEADERS_DIR = MAP_DATA_DIR / "headers"
MAP_OBJECTS_DIR = MAP_DATA_DIR / "objects"

SCRIPTS_DIR = GAME_DATA_ROOT / "scripts"
TEXT_DIR = GAME_DATA_ROOT / "text"
GLOBAL_TEXT_DIR = DATA_DIR / "text"

EVENTS_DIR = DATA_DIR / "events"
ITEMS_DATA_DIR = DATA_DIR / "items"
MOVES_DATA_DIR = DATA_DIR / "moves"
POKEMON_DATA_DIR = DATA_DIR / "pokemon"
BASE_STATS_DIR = POKEMON_DATA_DIR / "base_stats"
TRAINERS_DIR = DATA_DIR / "trainers"
WILD_DIR = DATA_DIR / "wild"
WILD_MAPS_DIR = WILD_DIR / "maps"

BLOCKSETS_DIR = GFX_DIR / "blocksets"
TILESETS_DIR = GFX_DIR / "tilesets"
SPRITES_DIR = GFX_DIR / "sprites"
TILESET_DATA_DIR = DATA_DIR / "tilesets"

TILE_IMAGES_DIR = "tile_images"
TILE_IMAGE_OUTPUT_DIR = configured_path(
    "POKEMON_EXTRACTOR_TILE_IMAGE_DIR",
    PROJECT_ROOT / "export_scripts" / TILE_IMAGES_DIR,
)

VIEWER_PUBLIC_DIR = configured_path(
    "POKEMON_EXTRACTOR_VIEWER_PUBLIC_DIR",
    PROJECT_ROOT / "pokemon-phaser" / "public",
)
VIEWER_DATA_DIR = configured_path(
    "POKEMON_EXTRACTOR_VIEWER_DATA_DIR",
    VIEWER_PUBLIC_DIR / "viewer-data",
)
VIEWER_ASSET_DIR = configured_path(
    "POKEMON_EXTRACTOR_VIEWER_ASSET_DIR",
    VIEWER_PUBLIC_DIR / "viewer-assets",
)
VIEWER_TILE_ASSET_DIR = VIEWER_ASSET_DIR / "tile_images"
VIEWER_SPRITE_ASSET_DIR = VIEWER_ASSET_DIR / "sprites"

BATCH_SIZE = 1000

MAP_CONSTANTS_FILE = CONSTANTS_DIR / "map_constants.asm"
TILESET_CONSTANTS_FILE = CONSTANTS_DIR / "tileset_constants.asm"
TILESET_HEADERS_FILE = TILESET_DATA_DIR / "tileset_headers.asm"
ITEM_CONSTANTS_FILE = CONSTANTS_DIR / "item_constants.asm"
MOVE_CONSTANTS_FILE = CONSTANTS_DIR / "move_constants.asm"
POKEDEX_CONSTANTS_FILE = CONSTANTS_DIR / "pokedex_constants.asm"
POKEMON_CONSTANTS_FILE = CONSTANTS_DIR / "pokemon_constants.asm"
SCRIPT_CONSTANTS_FILE = CONSTANTS_DIR / "script_constants.asm"
EVENT_CONSTANTS_FILE = CONSTANTS_DIR / "event_constants.asm"
MUSIC_CONSTANTS_FILE = CONSTANTS_DIR / "music_constants.asm"

EVOS_MOVES_FILE = POKEMON_DATA_DIR / "evos_moves.asm"
COLLISION_TILE_IDS_FILE = TILESET_DATA_DIR / "collision_tile_ids.asm"
TRADES_FILE = EVENTS_DIR / "trades.asm"
CINNABAR_LAB_ENGINE_FILE = GAME_DATA_ROOT / "engine" / "events" / "cinnabar_lab.asm"

SCRIPT_EVENT_CANDIDATES_PATH = configured_path(
    "POKEMON_EXTRACTOR_SCRIPT_EVENT_CANDIDATES",
    PROJECT_ROOT / "script_event_candidates.json",
)
SCRIPT_EVENT_IR_PATH = configured_path(
    "POKEMON_EXTRACTOR_SCRIPT_EVENT_IR",
    PROJECT_ROOT / "script_event_ir.json",
)
SCRIPT_EVENT_DIAGNOSTICS_PATH = configured_path(
    "POKEMON_EXTRACTOR_SCRIPT_EVENT_DIAGNOSTICS",
    PROJECT_ROOT / "script_event_diagnostics.json",
)
SCRIPT_EVENT_TRADES_PATH = configured_path(
    "POKEMON_EXTRACTOR_SCRIPT_EVENT_TRADES",
    PROJECT_ROOT / "script_event_in_game_trades.json",
)
SCRIPT_EVENT_TILE_OVERRIDES_PATH = configured_path(
    "POKEMON_EXTRACTOR_SCRIPT_EVENT_TILE_OVERRIDES",
    PROJECT_ROOT / "script_event_tile_overrides.json",
)
SCRIPT_EVENT_BOULDER_TARGETS_PATH = configured_path(
    "POKEMON_EXTRACTOR_SCRIPT_EVENT_BOULDER_TARGETS",
    PROJECT_ROOT / "script_event_boulder_targets.json",
)
SCRIPT_EVENT_OBJECT_VISIBILITY_PATH = configured_path(
    "POKEMON_EXTRACTOR_SCRIPT_EVENT_OBJECT_VISIBILITY",
    PROJECT_ROOT / "script_event_object_visibility.json",
)
SCRIPT_EVENT_CONDITIONAL_DIALOGUE_PATH = configured_path(
    "POKEMON_EXTRACTOR_SCRIPT_EVENT_CONDITIONAL_DIALOGUE",
    PROJECT_ROOT / "script_event_conditional_dialogue.json",
)
AUDIO_MANIFEST_PATH = configured_path(
    "POKEMON_EXTRACTOR_AUDIO_MANIFEST",
    PROJECT_ROOT / "audio_manifest.json",
)
GRAPHICS_OUTPUT_DIR = configured_path(
    "POKEMON_EXTRACTOR_GRAPHICS_DIR",
    PROJECT_ROOT / "build" / "graphics",
)
AUDIO_OUTPUT_DIR = configured_path(
    "POKEMON_EXTRACTOR_AUDIO_DIR",
    PROJECT_ROOT / "build" / "audio",
)

# Some Red/Blue tilesets share blockset/graphics data under a different ID.
TILESET_BLOCKSET_ALIASES = {
    5: 7,   # DOJO -> GYM
    2: 6,   # MART -> POKECENTER
    10: 12, # MUSEUM -> GATE
    9: 12,  # FOREST_GATE -> GATE
    4: 1,   # REDS_HOUSE_2 -> REDS_HOUSE_1
}

TILESET_IMAGE_ALIAS_TARGETS = {
    7: (5,), # GYM images can satisfy DOJO tiles.
    6: (2,), # POKECENTER images can satisfy MART tiles.
}

COLLISION_TILESET_DEFINITIONS = {
    "Underground_Coll": {"id": 11, "name": "UNDERGROUND"},
    "Overworld_Coll": {"id": 0, "name": "OVERWORLD"},
    "RedsHouse1_Coll": {"id": 1, "name": "REDS_HOUSE_1"},
    "RedsHouse2_Coll": {"id": 4, "name": "REDS_HOUSE_2"},
    "Mart_Coll": {"id": 2, "name": "MART"},
    "Pokecenter_Coll": {"id": 6, "name": "POKECENTER"},
    "Dojo_Coll": {"id": 5, "name": "DOJO"},
    "Gym_Coll": {"id": 7, "name": "GYM"},
    "Forest_Coll": {"id": 3, "name": "FOREST"},
    "House_Coll": {"id": 8, "name": "HOUSE"},
    "ForestGate_Coll": {"id": 9, "name": "FOREST_GATE"},
    "Museum_Coll": {"id": 10, "name": "MUSEUM"},
    "Gate_Coll": {"id": 12, "name": "GATE"},
    "Ship_Coll": {"id": 13, "name": "SHIP"},
    "ShipPort_Coll": {"id": 14, "name": "SHIP_PORT"},
    "Cemetery_Coll": {"id": 15, "name": "CEMETERY"},
    "Interior_Coll": {"id": 16, "name": "INTERIOR"},
    "Cavern_Coll": {"id": 17, "name": "CAVERN"},
    "Lobby_Coll": {"id": 18, "name": "LOBBY"},
    "Mansion_Coll": {"id": 19, "name": "MANSION"},
    "Lab_Coll": {"id": 20, "name": "LAB"},
    "Club_Coll": {"id": 21, "name": "CLUB"},
    "Facility_Coll": {"id": 22, "name": "FACILITY"},
    "Plateau_Coll": {"id": 23, "name": "PLATEAU"},
}


def remap_tileset_for_blockset(tileset_id):
    """Return the tileset ID that owns shared blockset/graphics data."""
    return TILESET_BLOCKSET_ALIASES.get(tileset_id, tileset_id)
