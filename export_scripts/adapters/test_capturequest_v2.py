import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.capturequest_v2 import (
    ADAPTER_SCHEMA_NAME,
    ADAPTER_SCHEMA_VERSION,
    AssetRoots,
    CaptureQuestImportError,
    build_capturequest_bundle,
    dumps_capturequest_bundle,
    load_capturequest_bundle,
    main,
    negotiate_schema,
    portable_reference,
)


RUN_ID = "a" * 64
TREE_HASH = "b" * 64


def create_fixture(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE schema_metadata (
            schema_name TEXT, schema_version INTEGER,
            minimum_reader_version INTEGER, applied_epoch INTEGER
        );
        CREATE TABLE game_releases (
            source_order INTEGER, release_code TEXT, title TEXT, variant TEXT,
            platform TEXT, region TEXT, language TEXT, build_define TEXT
        );
        CREATE TABLE extraction_runs (
            run_id TEXT, schema_name TEXT, schema_version INTEGER,
            extractor_revision TEXT, source_revision TEXT,
            source_date_epoch INTEGER, source_root TEXT,
            source_tree_sha256 TEXT
        );
        CREATE TABLE extraction_run_releases (run_id TEXT, release_code TEXT);

        CREATE TABLE maps (
            id INTEGER, name TEXT, width INTEGER, height INTEGER,
            tileset_id INTEGER, is_overworld INTEGER,
            north_connection INTEGER, south_connection INTEGER,
            west_connection INTEGER, east_connection INTEGER
        );
        CREATE TABLE warps (
            id INTEGER, source_map TEXT, source_map_id INTEGER,
            source_warp_index INTEGER, source_x INTEGER, source_y INTEGER,
            destination_map TEXT, destination_kind TEXT,
            destination_map_id INTEGER, destination_x INTEGER,
            destination_y INTEGER, destination_warp_id INTEGER,
            source_file TEXT
        );
        CREATE TABLE warp_events (
            id INTEGER, map_name TEXT, map_id INTEGER,
            source_warp_index INTEGER, x INTEGER, y INTEGER,
            dest_map TEXT, dest_kind TEXT, dest_map_id INTEGER,
            dest_warp_index INTEGER, source_file TEXT
        );
        CREATE TABLE tilesets (
            id INTEGER, name TEXT, source_tileset_id INTEGER,
            blockset_path TEXT, tileset_path TEXT
        );
        CREATE TABLE map_connections (
            id INTEGER, from_map_id INTEGER, to_map_id INTEGER,
            direction TEXT, offset INTEGER
        );
        CREATE TABLE overworld_map_positions (
            map_id INTEGER, map_name TEXT, x_offset INTEGER, y_offset INTEGER
        );
        CREATE TABLE tile_images (
            id INTEGER, tileset_id INTEGER, block_index INTEGER,
            position INTEGER, image_path TEXT, image_hash TEXT
        );
        CREATE TABLE tiles (
            id INTEGER, x INTEGER, y INTEGER, local_x INTEGER,
            local_y INTEGER, map_id INTEGER, tile_image_id INTEGER,
            is_overworld INTEGER, collision_type INTEGER
        );
        CREATE TABLE objects (
            id INTEGER, name TEXT, map_id INTEGER, object_type TEXT,
            x INTEGER, y INTEGER, local_x INTEGER, local_y INTEGER,
            spriteset_id INTEGER, sprite_name TEXT, text TEXT,
            action_type TEXT, action_direction TEXT, item_id INTEGER,
            movement_type TEXT, trainer_class TEXT,
            trainer_party_index INTEGER
        );
        CREATE TABLE items (
            id INTEGER, name TEXT, short_name TEXT, price INTEGER,
            is_usable INTEGER, uses_party_menu INTEGER,
            vending_price INTEGER, move_id INTEGER,
            is_guard_drink INTEGER, is_key_item INTEGER
        );
        CREATE TABLE moves (
            id INTEGER, constant_name TEXT, name TEXT, type TEXT, power INTEGER,
            accuracy INTEGER, pp INTEGER, field_move_effect INTEGER, is_hm INTEGER
        );
        CREATE TABLE pokemon (
            id INTEGER, name TEXT, hp INTEGER, atk INTEGER, def INTEGER,
            spd INTEGER, spc INTEGER, type_1 TEXT, type_2 TEXT,
            catch_rate INTEGER, base_exp INTEGER, base_cry INTEGER,
            cry_pitch INTEGER, cry_length INTEGER, pokedex_type TEXT,
            height TEXT, weight INTEGER, pokedex_text TEXT,
            icon_image TEXT, palette_type TEXT,
            default_move_1_id INTEGER, default_move_1_name TEXT
        );
        CREATE TABLE pokemon_default_moves (
            pokemon_id INTEGER, slot_index INTEGER, move_id INTEGER,
            source_move_name TEXT
        );
        CREATE TABLE pokemon_evolutions (
            id INTEGER, source_pokemon_id INTEGER,
            target_pokemon_id INTEGER, method TEXT, level INTEGER,
            item_id INTEGER, source_order INTEGER
        );
        CREATE TABLE pokemon_learnset (
            id INTEGER, pokemon_id INTEGER, pokemon_name TEXT,
            level INTEGER, move_name TEXT, move_id INTEGER
        );
        CREATE TABLE pokemon_tmhm (
            id INTEGER, pokemon_id INTEGER, pokemon_name TEXT,
            tm_hm_name TEXT, move_name TEXT, move_id INTEGER,
            is_hm INTEGER
        );
        CREATE TABLE wild_encounters (
            id INTEGER, map_id INTEGER, map_name TEXT, source_label TEXT,
            encounter_type TEXT, encounter_rate INTEGER, slot_index INTEGER,
            pokemon_name TEXT, level INTEGER, version TEXT
        );
        CREATE TABLE encounter_slots (
            id INTEGER, slot_index INTEGER, probability REAL,
            cumulative_probability REAL
        );
        CREATE TABLE trainer_classes (
            id INTEGER, constant_name TEXT, display_name TEXT,
            base_money INTEGER, is_gym_leader INTEGER,
            is_elite_four INTEGER, is_rival INTEGER
        );
        CREATE TABLE trainer_parties (
            id INTEGER, trainer_class_id INTEGER, party_index INTEGER,
            location_comment TEXT, is_variable_level INTEGER
        );
        CREATE TABLE trainer_party_pokemon (
            id INTEGER, trainer_party_id INTEGER, slot_index INTEGER,
            pokemon_name TEXT, level INTEGER
        );
        CREATE TABLE trainer_headers (
            id INTEGER, map_name TEXT, map_id INTEGER, header_label TEXT,
            header_index INTEGER, event_flag TEXT, sight_range INTEGER,
            battle_text_label TEXT, end_battle_text_label TEXT,
            after_battle_text_label TEXT
        );
        CREATE TABLE dialogue_text (
            id INTEGER, label TEXT, source_file TEXT, dialogue TEXT
        );
        CREATE TABLE text_pointers (
            id INTEGER, map_name TEXT, map_id INTEGER,
            text_constant TEXT, local_label TEXT, dialogue_label TEXT,
            pointer_index INTEGER, is_trainer INTEGER
        );
        CREATE TABLE hidden_items (
            id INTEGER, map_constant TEXT, map_id INTEGER,
            x INTEGER, y INTEGER
        );
        CREATE TABLE hidden_coins (
            id INTEGER, map_constant TEXT, map_id INTEGER,
            x INTEGER, y INTEGER
        );
        CREATE TABLE hidden_objects (
            id INTEGER, map_constant TEXT, map_id INTEGER,
            x INTEGER, y INTEGER, item_or_direction TEXT,
            routine TEXT, object_type TEXT
        );
        CREATE TABLE missable_objects (
            id INTEGER, hs_index INTEGER, hs_constant TEXT,
            map_constant TEXT, map_id INTEGER, object_constant TEXT,
            object_index INTEGER, object_name TEXT, object_type TEXT,
            initial_state TEXT, initial_visible INTEGER, label TEXT
        );
        CREATE TABLE map_music (
            id INTEGER, map_constant TEXT, map_id INTEGER,
            music_constant TEXT
        );
        CREATE TABLE map_scripts (
            id INTEGER, map_name TEXT, map_id INTEGER,
            script_index INTEGER, script_label TEXT,
            script_constant TEXT, raw_asm TEXT
        );
        CREATE TABLE npc_movement_data (
            id INTEGER, map_name TEXT, map_id INTEGER,
            label TEXT, movements TEXT
        );
        CREATE TABLE spin_tiles (
            id INTEGER, map_name TEXT, map_id INTEGER,
            source_label TEXT, x INTEGER, y INTEGER,
            movement_label TEXT, movements TEXT
        );
        CREATE TABLE event_flags (
            id INTEGER, map_name TEXT, map_id INTEGER,
            flag_name TEXT, operation TEXT, context_label TEXT
        );
        CREATE TABLE coordinate_triggers (
            id INTEGER, map_name TEXT, map_id INTEGER,
            label TEXT, x INTEGER, y INTEGER
        );

        CREATE TABLE script_event_candidates (
            id INTEGER, map_name TEXT, map_id INTEGER, script_label TEXT,
            trigger_type TEXT, trigger_label TEXT, confidence TEXT,
            candidate_json TEXT
        );
        CREATE TABLE script_event_candidate_actions (
            candidate_id INTEGER, action_index INTEGER, action_type TEXT,
            action_json TEXT
        );
        CREATE TABLE script_event_candidate_conditions (
            candidate_id INTEGER, condition_path TEXT, value_index INTEGER,
            condition_value_json TEXT
        );
        CREATE TABLE script_event_candidate_references (
            candidate_id INTEGER, reference_kind TEXT, json_path TEXT,
            reference_index INTEGER, reference_value_json TEXT
        );
        CREATE TABLE script_event_candidate_diagnostics (
            id INTEGER, map_name TEXT, map_id INTEGER, script_label TEXT,
            status TEXT, reason TEXT, details_json TEXT
        );
        CREATE TABLE script_event_ir_blocks (
            id INTEGER, map_name TEXT, map_id INTEGER, label TEXT, kind TEXT,
            raw_asm TEXT
        );
        CREATE TABLE script_event_ir_references (
            ir_block_id INTEGER, reference_kind TEXT, reference_index INTEGER,
            reference_value_json TEXT
        );
        CREATE TABLE script_event_in_game_trades (
            id INTEGER, trade_key TEXT, map_name TEXT, script_label TEXT,
            text_constant TEXT, requested_pokemon TEXT,
            offered_pokemon TEXT, offered_nickname TEXT,
            dialogue_set TEXT, original_trade_index INTEGER,
            active INTEGER, source_file TEXT
        );
        CREATE TABLE script_event_tile_overrides (
            id INTEGER, map_name TEXT, script_label TEXT,
            candidate_json TEXT
        );
        CREATE TABLE script_event_boulder_targets (
            id INTEGER, target_family TEXT, map_name TEXT,
            source_label TEXT, x INTEGER, y INTEGER, flag TEXT,
            drops_through_hole INTEGER, source_missable_object TEXT,
            destination_map_name TEXT,
            destination_missable_object TEXT, source_file TEXT,
            target_json TEXT
        );
        CREATE TABLE script_event_object_visibility (
            id INTEGER, map_name TEXT, map_id INTEGER,
            object_name TEXT, object_key TEXT, script_label TEXT,
            requires_event TEXT, visible INTEGER, label TEXT,
            rule_json TEXT
        );
        CREATE TABLE script_event_conditional_dialogue (
            id INTEGER, text_constant TEXT, map_name TEXT,
            script_label TEXT, priority INTEGER,
            requires_flags_json TEXT, requires_flags_absent_json TEXT,
            dialogue_labels_json TEXT, source_json TEXT, row_json TEXT
        );

        CREATE TABLE graphic_formats (
            id INTEGER, extension TEXT, media_type TEXT, family TEXT
        );
        CREATE TABLE graphic_categories (
            id INTEGER, category_path TEXT
        );
        CREATE TABLE graphic_assets (
            id INTEGER, asset_role TEXT, path_scope TEXT, relative_path TEXT,
            sha256 TEXT, byte_size INTEGER, width_px INTEGER, height_px INTEGER,
            pixel_mode TEXT, tile_count INTEGER, format_id INTEGER,
            category_id INTEGER
        );
        CREATE TABLE graphic_source_links (
            source_asset_id INTEGER, companion_asset_id INTEGER,
            relation_type TEXT
        );
        CREATE TABLE graphic_derivations (
            source_asset_id INTEGER, derived_asset_id INTEGER,
            transformation TEXT, decoder_version TEXT, layout TEXT,
            tiles_per_row INTEGER, tile_count INTEGER
        );

        CREATE TABLE audio_assets (
            asset_key TEXT, asset_kind TEXT, constant TEXT, display_name TEXT,
            base_asset_key TEXT, audio_bank TEXT, audio_id INTEGER,
            frequency_modifier INTEGER, tempo_modifier INTEGER,
            loop_enabled INTEGER, loop_mode TEXT, ogg_path TEXT, flac_path TEXT
        );
        CREATE TABLE audio_asset_sources (
            asset_key TEXT, source_role TEXT, source_path TEXT, source_label TEXT
        );
        CREATE TABLE map_music_assets (map_id INTEGER, asset_key TEXT);
        CREATE TABLE move_audio_assets (move_id INTEGER, asset_key TEXT);
        CREATE TABLE pokemon_cry_assets (
            internal_index INTEGER, pokemon_id INTEGER, pokemon_name TEXT,
            is_glitch_slot INTEGER, asset_key TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO schema_metadata VALUES (?, 2, 2, 1700000000)",
        ("pokemon-gameboy-extractor",),
    )
    conn.executemany(
        "INSERT INTO game_releases VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "red", "Pokemon Red", "red", "game-boy", "international", "en", "_RED"),
            (2, "blue", "Pokemon Blue", "blue", "game-boy", "international", "en", "_BLUE"),
        ],
    )
    conn.execute(
        "INSERT INTO extraction_runs VALUES (?, ?, 2, ?, ?, ?, ?, ?)",
        (
            RUN_ID,
            "pokemon-gameboy-extractor",
            "extractor-revision",
            "source-revision",
            1700000000,
            "pokemon-game-data",
            TREE_HASH,
        ),
    )
    conn.executemany(
        "INSERT INTO extraction_run_releases VALUES (?, ?)",
        [(RUN_ID, "red"), (RUN_ID, "blue")],
    )
    conn.executemany(
        "INSERT INTO maps VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (0, "PALLET_TOWN", 10, 9, 1, 1, 1, None, None, None),
            (1, "ROUTE_1", 10, 18, 1, 1, None, 0, None, None),
            (2, "GAME_CORNER", 10, 10, 1, 0, None, None, None, None),
        ],
    )
    conn.executemany(
        "INSERT INTO warps VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                1,
                "PalletTown",
                0,
                1,
                5,
                0,
                "Route1",
                "fixed",
                1,
                5,
                17,
                1,
                "pokemon-game-data/data/maps/objects/PalletTown.asm",
            ),
            (
                2,
                "Route1",
                1,
                1,
                5,
                17,
                "LAST_MAP",
                "last-map",
                None,
                None,
                None,
                0,
                "pokemon-game-data/data/maps/objects/Route1.asm",
            ),
        ],
    )
    conn.executemany(
        "INSERT INTO warp_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                1, "PalletTown", 0, 1, 5, 0, "Route1", "fixed", 1, 1,
                "data/maps/objects/PalletTown.asm",
            ),
            (
                2, "Route1", 1, 1, 5, 17, "LAST_MAP", "last-map", None, 0,
                "data/maps/objects/Route1.asm",
            ),
        ],
    )
    conn.execute(
        "INSERT INTO tilesets VALUES (?, ?, ?, ?, ?)",
        (
            1,
            "OVERWORLD",
            None,
            "pokemon-game-data/gfx/blocksets/overworld.bst",
            "pokemon-game-data/gfx/tilesets/overworld.png",
        ),
    )
    conn.execute("INSERT INTO map_connections VALUES (1, 0, 1, 'north', 0)")
    conn.execute("INSERT INTO overworld_map_positions VALUES (0, 'PALLET_TOWN', 0, 0)")
    conn.execute(
        "INSERT INTO tile_images VALUES (1, 1, 0, 0, 'tile_images/tile_0.png', 'abc123')"
    )
    conn.execute("INSERT INTO tiles VALUES (1, 0, 0, 0, 0, 0, 1, 1, 0)")
    conn.executemany(
        "INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "POKE_BALL", "Poke Ball", 200, 1, 0, None, None, 0, 0),
            (10, "MOON_STONE", "Moon Stone", 0, 1, 1, None, None, 0, 0),
            (32, "FIRE_STONE", "Fire Stone", 2100, 1, 1, None, None, 0, 0),
            (33, "THUNDER_STONE", "Thunder Stone", 2100, 1, 1, None, None, 0, 0),
            (34, "WATER_STONE", "Water Stone", 2100, 1, 1, None, None, 0, 0),
        ],
    )
    conn.execute(
        """
        INSERT INTO objects VALUES (
            1, 'PALLET_TOWN_OAK', 0, 'person', 5, 6, 5, 6, 1,
            'SPRITE_OAK', 'TEXT_OAK', 'text', 'down', NULL,
            'WALK', NULL, NULL
        )
        """
    )
    conn.executemany(
        "INSERT INTO moves VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "POUND", "Pound", "NORMAL", 40, 100, 35, 0, 0),
            (2, "GROWL", "Growl", "NORMAL", 0, 100, 40, 0, 0),
        ],
    )
    # The scalar compatibility slot deliberately disagrees with the normalized
    # row.  A schema-v2 consumer must use pokemon_default_moves.
    conn.execute(
        """
        INSERT INTO pokemon VALUES (
            1, 'BULBASAUR', 45, 49, 49, 45, 65, 'GRASS', 'POISON',
            45, 64, 15, 128, 129, 'SEED', '2 ft 4 in', 69,
            'A strange seed was planted.', 'MON_ICON_GRASS', 'PAL_GREEN',
            2, 'GROWL'
        )
        """
    )
    conn.execute("INSERT INTO pokemon_default_moves VALUES (1, 1, 1, 'POUND')")
    conn.executemany(
        "INSERT INTO wild_encounters VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, 1, "ROUTE_1", "Route1WildMons", "grass", 25, 1, "PIDGEY", 3, "red"),
            (2, 1, "ROUTE_1", "Route1WildMons", "grass", 25, 1, "RATTATA", 3, "blue"),
            (3, None, "GLOBAL", None, "good_rod", 0, 1, "GOLDEEN", 10, "both"),
        ],
    )
    conn.executemany(
        "INSERT INTO script_event_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                1,
                "PalletTown",
                0,
                "PalletTownGeneratedCandidate",
                "map_load",
                "PalletTownGeneratedCandidate",
                "exact",
                json.dumps({"actions": [{"type": "wrongJsonOnlyAction"}]}),
            ),
            (
                2,
                "GameCorner",
                2,
                "GameCornerRocketDefeated",
                "post_battle",
                "GameCornerRocketDefeated",
                "exact",
                json.dumps({"actions": []}),
            ),
        ],
    )
    conn.executemany(
        "INSERT INTO script_event_candidate_actions VALUES (?, ?, ?, ?)",
        [
            (1, 0, "showText", json.dumps({"type": "showText", "text": "OAK_HEY_WAIT"})),
            (2, 0, "move", json.dumps({"type": "move", "actor": "ROCKET", "movements": ["UP"]})),
        ],
    )
    conn.execute(
        "INSERT INTO script_event_candidate_conditions VALUES (?, ?, ?, ?)",
        (1, "$.requiresEventsAbsent", 0, json.dumps("EVENT_FOLLOWED_OAK")),
    )
    conn.execute(
        "INSERT INTO script_event_candidate_references VALUES (?, ?, ?, ?, ?)",
        (1, "event", "$.conditions.requiresEventsAbsent", 0, json.dumps("EVENT_FOLLOWED_OAK")),
    )
    conn.execute(
        "INSERT INTO script_event_candidate_diagnostics VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            1,
            "PalletTown",
            0,
            "PalletTownDefaultScript",
            "covered",
            "oak_intro_runtime_v1",
            json.dumps({"source": "fixture"}),
        ),
    )
    conn.execute(
        "INSERT INTO script_event_ir_blocks VALUES (?, ?, ?, ?, ?, ?)",
        (1, "PalletTown", 0, "PalletTownDefaultScript", "script", "ret"),
    )
    conn.execute(
        "INSERT INTO script_event_ir_references VALUES (?, ?, ?, ?)",
        (1, "event", 0, json.dumps("EVENT_FOLLOWED_OAK")),
    )
    conn.execute("INSERT INTO graphic_formats VALUES (1, '.2bpp', 'application/octet-stream', 'tile_graphics')")
    conn.execute("INSERT INTO graphic_formats VALUES (2, '.png', 'image/png', 'raster_image')")
    conn.execute("INSERT INTO graphic_categories VALUES (1, 'gfx/tilesets')")
    conn.executemany(
        "INSERT INTO graphic_assets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "source", "repository", "pokemon-game-data/gfx/tilesets/overworld.2bpp", "c" * 64, 16, None, None, None, 1, 1, 1),
            (2, "derived", "graphics_output", "decoded/gfx/tilesets/overworld.png", "d" * 64, 70, 8, 8, "RGBA", 1, 2, 1),
        ],
    )
    conn.execute("INSERT INTO graphic_source_links VALUES (1, 2, 'same_stem_preview')")
    conn.execute("INSERT INTO graphic_derivations VALUES (1, 2, 'raw_tiles_to_png', '1', 'row-major', 1, 1)")
    conn.executemany(
        "INSERT INTO audio_assets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("music:pallet-town", "music", "MUSIC_PALLET_TOWN", "Pallet Town", None, "bank2", 1, 0, 0, 1, "source-runtime-capture", "/sound/pokemon/music/pallet_town.ogg", "/sound/pokemon/music/pallet_town.flac"),
            ("move:pound", "move", "POUND", "Pound", None, "bank2", 2, 0, 0, 0, "none", "/sound/pokemon/moves/pound.ogg", "/sound/pokemon/moves/pound.flac"),
            ("cry:bulbasaur", "cry", "CRY_0F", "Bulbasaur", None, "bank2", 15, 128, 129, 0, "none", "/sound/pokemon/cries/bulbasaur.ogg", "/sound/pokemon/cries/bulbasaur.flac"),
        ],
    )
    conn.execute(
        "INSERT INTO audio_asset_sources VALUES (?, ?, ?, ?)",
        ("music:pallet-town", "header", "pokemon-game-data/audio/music/pallet_town.asm", "Music_PalletTown"),
    )
    conn.execute("INSERT INTO map_music_assets VALUES (0, 'music:pallet-town')")
    conn.execute("INSERT INTO move_audio_assets VALUES (1, 'move:pound')")
    conn.execute("INSERT INTO pokemon_cry_assets VALUES (1, 1, 'BULBASAUR', 0, 'cry:bulbasaur')")
    conn.commit()


class CaptureQuestV2AdapterTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        create_fixture(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_end_to_end_red_bundle_uses_normalized_relationships(self):
        bundle = build_capturequest_bundle(self.conn, release="RED")

        self.assertEqual(
            bundle["adapter"],
            {"schemaName": ADAPTER_SCHEMA_NAME, "schemaVersion": ADAPTER_SCHEMA_VERSION},
        )
        self.assertEqual(bundle["extractor"]["schema"]["version"], 2)
        self.assertEqual(bundle["release"]["code"], "red")
        self.assertEqual(bundle["maps"][0]["mapId"], 0)

        fixed, dynamic = bundle["warps"]
        self.assertEqual(fixed["sourceMapId"], 0)
        self.assertEqual(fixed["destination"]["mapId"], 1)
        self.assertEqual(dynamic["destination"]["kind"], "last-map")
        self.assertIsNone(dynamic["destination"]["mapId"])
        self.assertIsNone(dynamic["destination"]["x"])

        default_move = bundle["pokemon"][0]["defaultMoves"][0]
        self.assertEqual(default_move["moveId"], 1)
        self.assertEqual(default_move["sourceConstant"], "POUND")

        pallet_candidate = bundle["scriptCandidates"][0]
        self.assertEqual(pallet_candidate["mapId"], 0)
        self.assertEqual(pallet_candidate["actions"][0]["value"]["type"], "showText")
        self.assertIsNone(pallet_candidate["captureQuestRuntimeScript"])
        self.assertEqual(pallet_candidate["conditions"][0]["value"], "EVENT_FOLLOWED_OAK")
        self.assertEqual(pallet_candidate["references"][0]["kind"], "event")
        self.assertEqual(bundle["scriptIrBlocks"][0]["references"][0]["value"], "EVENT_FOLLOWED_OAK")

        rocket = bundle["scriptCandidates"][1]
        self.assertEqual(
            rocket["actions"][0]["value"]["movements"],
            ["DOWN", "DOWN", "DOWN", "RIGHT", "RIGHT"],
        )
        self.assertIsNotNone(rocket["captureQuestProfile"])

        diagnostic = bundle["scriptDiagnostics"][0]
        self.assertEqual(
            diagnostic["captureQuestRuntimeScript"], "PalletTownOakStopsPlayer"
        )
        self.assertEqual(
            diagnostic["details"]["runtimeProfile"]["name"], "capturequest"
        )

        encounter_names = [row["pokemon"] for row in bundle["wildEncounters"]]
        self.assertEqual(encounter_names, ["GOLDEEN", "PIDGEY"])
        self.assertNotIn("RATTATA", encounter_names)
        self.assertEqual(bundle["wildEncounters"][0]["scope"], "global")
        self.assertIsNone(bundle["wildEncounters"][0]["mapId"])

        self.assertEqual(
            bundle["graphics"]["assets"][1]["path"],
            {
                "scope": "graphics_output",
                "relativePath": "decoded/gfx/tilesets/overworld.png",
            },
        )
        self.assertEqual(
            bundle["audio"]["assets"][0]["ogg"]["scope"], "audio_output"
        )
        self.assertEqual(bundle["audio"]["mapMusic"][0]["mapId"], 0)

        first = dumps_capturequest_bundle(bundle)
        second = dumps_capturequest_bundle(
            build_capturequest_bundle(self.conn, release="red")
        )
        self.assertEqual(first, second)
        self.assertEqual(json.loads(first)["release"]["code"], "red")

    def test_blue_release_filters_variant_rows_but_keeps_shared_rows(self):
        bundle = build_capturequest_bundle(self.conn, release="blue")
        self.assertEqual(
            [row["pokemon"] for row in bundle["wildEncounters"]],
            ["GOLDEEN", "RATTATA"],
        )

    def test_rejects_unsupported_or_unreadable_schema(self):
        self.conn.execute("UPDATE schema_metadata SET schema_version = 3")
        with self.assertRaisesRegex(CaptureQuestImportError, "Unsupported.*version 3"):
            negotiate_schema(self.conn)

        self.conn.execute(
            "UPDATE schema_metadata SET schema_version = 2, minimum_reader_version = 3"
        )
        with self.assertRaisesRegex(CaptureQuestImportError, "minimum_reader_version"):
            negotiate_schema(self.conn)

    def test_rejects_invented_last_map_destination(self):
        self.conn.execute(
            "UPDATE warps SET destination_map_id = 0 WHERE destination_kind = 'last-map'"
        )
        with self.assertRaisesRegex(CaptureQuestImportError, "must not invent"):
            build_capturequest_bundle(self.conn, release="red")

    def test_path_scopes_are_portable_and_resolve_under_explicit_roots(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            roots = AssetRoots(temp / "repo", temp / "graphics", temp / "audio")
            reference = portable_reference(
                "graphics_output", "decoded/gfx/tilesets/overworld.png"
            )
            self.assertEqual(
                roots.resolve(reference),
                (temp / "graphics/decoded/gfx/tilesets/overworld.png").resolve(),
            )
        for invalid in ("../secret", "/absolute", "C:/windows", "a\\b"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(CaptureQuestImportError):
                    portable_reference("repository", invalid)

    def test_module_cli_writes_a_deterministic_bootstrap_bundle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "pokemon.db"
            output_path = Path(temp_dir) / "capturequest-red.json"
            destination = sqlite3.connect(database_path)
            try:
                self.conn.backup(destination)
            finally:
                destination.close()
            self.assertEqual(
                main(
                    [
                        str(database_path),
                        "--release",
                        "red",
                        "--output",
                        str(output_path),
                    ]
                ),
                0,
            )
            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                dumps_capturequest_bundle(
                    load_capturequest_bundle(database_path, release="red")
                ),
            )
            original_header = database_path.read_bytes()[:16]
            with self.assertRaisesRegex(
                CaptureQuestImportError,
                "must not replace the extractor database",
            ):
                main(
                    [
                        str(database_path),
                        "--release",
                        "red",
                        "--output",
                        str(database_path),
                    ]
                )
            self.assertEqual(database_path.read_bytes()[:16], original_header)

    @unittest.skipUnless(
        os.environ.get("POKEMON_EXTRACTOR_INTEGRATION_DB"),
        "set POKEMON_EXTRACTOR_INTEGRATION_DB for the optional production check",
    )
    def test_optional_production_database(self):
        bundle = load_capturequest_bundle(
            os.environ["POKEMON_EXTRACTOR_INTEGRATION_DB"], release="red"
        )
        self.assertEqual(bundle["extractor"]["schema"]["version"], 2)
        self.assertEqual(bundle["release"]["code"], "red")
        self.assertTrue(bundle["maps"])
        self.assertTrue(bundle["graphics"]["assets"])
        self.assertTrue(bundle["audio"]["assets"])
        self.assertEqual(len(bundle["maps"]), 248)
        self.assertEqual(len(bundle["warps"]), 805)
        self.assertEqual(len(bundle["world"]["tiles"]), 94876)
        self.assertEqual(len(bundle["items"]), 138)
        self.assertEqual(len(bundle["moves"]), 165)
        self.assertEqual(len(bundle["pokemon"]), 151)
        self.assertEqual(len(bundle["pokemonEvolutions"]), 72)
        self.assertEqual(len(bundle["learnsets"]["levelUp"]), 728)
        self.assertEqual(len(bundle["learnsets"]["tmHm"]), 2980)
        self.assertEqual(len(bundle["trainers"]["headers"]), 322)
        self.assertEqual(len(bundle["text"]["pointers"]), 1207)
        self.assertEqual(len(bundle["hidden"]["objects"]), 198)
        self.assertEqual(len(bundle["mapEvents"]["warps"]), 805)
        self.assertEqual(len(bundle["specialScriptRules"]["inGameTrades"]), 10)
        self.assertEqual(len(bundle["specialScriptRules"]["tileOverrides"]), 25)
        self.assertEqual(len(bundle["specialScriptRules"]["boulderTargets"]), 5)
        self.assertEqual(len(bundle["specialScriptRules"]["objectVisibility"]), 34)
        self.assertEqual(len(bundle["specialScriptRules"]["conditionalDialogue"]), 49)
        eevee = [
            row["targetPokemon"]
            for row in bundle["pokemonEvolutions"]
            if row["sourcePokemon"] == "EEVEE"
        ]
        self.assertEqual(eevee, ["FLAREON", "JOLTEON", "VAPOREON"])
        mapped_diagnostics = {
            (row["mapLabel"], row["scriptLabel"])
            for row in bundle["scriptDiagnostics"]
            if row["captureQuestRuntimeScript"]
        }
        self.assertEqual(len(mapped_diagnostics), 34)


if __name__ == "__main__":
    unittest.main()
