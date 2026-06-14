#!/usr/bin/env python3
import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import SCRIPTS_DIR, TEXT_DIR
from export_map_scripts import parse_spin_tiles
from export_script_candidates import (
    badge_gated_gym_guide_candidates,
    badge_or_event_gated_dialogue_candidates,
    bills_house_cell_separator_candidates,
    boulder_target_runtime_diagnostics,
    cerulean_city_rival_candidates,
    champion_hall_of_fame_runtime_diagnostics,
    capturequest_authored_runtime_diagnostics,
    cinnabar_gym_default_runtime_diagnostics,
    cinnabar_gym_map_load_reset_candidate,
    cinnabar_gym_trainer_text_candidates,
    conditional_flag_map_script_candidates,
    conditional_dialogue_rows,
    daycare_runtime_diagnostic,
    diagnostic_for_ir_block,
    extract_features,
    extract_label_blocks,
    extract_script_ir,
    parse_text_pointer_map,
    elite_four_room_entrance_guard_candidates,
    fan_boast_toggle_candidates,
    facing_up_dialogue_candidates,
    flag_gated_dialogue_candidates,
    fighting_dojo_karate_master_candidates,
    game_corner_coin_purchase_candidates,
    generated_trade_diagnostic,
    game_corner_rocket_defeated_candidate,
    game_corner_rocket_hideout_tile_override_candidates,
    gym_leader_battle_text_candidates,
    in_game_trade_definitions,
    indigo_plateau_lobby_map_load_reset_candidate,
    lances_room_default_candidates,
    lance_room_entrance_tile_override_candidates,
    mt_moon_fossil_choice_candidates,
    name_rater_runtime_diagnostics,
    oak_intro_runtime_diagnostics,
    pallet_daisy_map_load_runtime_diagnostics,
    pewter_city_escort_candidates,
    pokemon_mansion_switch_candidates,
    pokemon_mansion_switch_tile_override_candidates,
    pokemon_tower_2f_rival_candidates,
    pokemon_tower_5f_purified_zone_candidate,
    pokemon_tower7f_rocket_exit_runtime_diagnostics,
    pokemon_tower_marowak_ghost_candidate,
    pokemon_tower_7f_mr_fuji_rescue_candidate,
    one_shot_object_visibility_map_script_candidate_for_block,
    pure_flag_map_script_candidates,
    route22_rival_candidates,
    route25_bill_visibility_candidates,
    rocket_hideout_b4f_giovanni_candidate,
    rocket_reward_battle_candidates,
    rocket_hideout_door_tile_override_candidates,
    rocket_hideout_door_unlock_candidates,
    safari_zone_gate_candidates,
    silph_co_6f_giovanni_dialogue_candidates,
    silph_co_11f_giovanni_candidate,
    silph_co_7f_rival_candidates,
    ss_anne_2f_rival_candidate,
    silph_co_9f_nurse_candidates,
    simple_flag_side_effect_dialogue_candidates,
    snorlax_wake_battle_candidates,
    SCRIPTS_DIR,
    spin_tile_runtime_diagnostics,
    strip_comment,
    trainer_after_battle_flag_runtime_diagnostics,
    trainer_after_battle_flag_side_effect_candidates,
    trainer_after_battle_object_drop_candidates,
    text_asm_text_pointer_diagnostics,
    viridian_city_progress_blocker_candidates,
    viridian_old_man_catch_tutorial_candidate,
    vermilion_ss_anne_guard_candidates,
    victory_road_boulder_target_definitions,
)

def assert_dialogue_contains(testcase, lines, fragment):
    testcase.assertTrue(
        any(fragment in line for line in lines),
        f"{fragment!r} not found in dialogue pages: {lines!r}",
    )


def generated_label_coverage_for_tests():
    from export_script_candidates import ADAPTERS, TILE_OVERRIDE_ADAPTERS

    candidates = []
    for adapter in ADAPTERS:
        candidates.extend(adapter())
    tile_candidates = []
    for adapter in TILE_OVERRIDE_ADAPTERS:
        tile_candidates.extend(adapter())
    trades = in_game_trade_definitions()
    conditional_rows = conditional_dialogue_rows()

    labels = {candidate["scriptLabel"] for candidate in candidates}
    labels.update(candidate.get("trigger", {}).get("sourceLabel", "") for candidate in candidates)
    for candidate in candidates:
        labels.update(candidate.get("source", {}).get("coveredLabels", []))
    labels.update(trade["scriptLabel"] for trade in trades if trade.get("scriptLabel"))
    labels.update(candidate["scriptLabel"] for candidate in tile_candidates)
    for candidate in tile_candidates:
        labels.update(candidate.get("source", {}).get("coveredLabels", []))
    labels.update(row["scriptLabel"] for row in conditional_rows)
    labels.update(row.get("sourceScriptLabel", "") for row in conditional_rows)
    for row in conditional_rows:
        labels.update(row.get("source", {}).get("coveredLabels", []))

    boulder_targets = victory_road_boulder_target_definitions()
    diagnostic_groups = [
        boulder_target_runtime_diagnostics(boulder_targets),
        trainer_after_battle_flag_runtime_diagnostics(),
        champion_hall_of_fame_runtime_diagnostics(),
        oak_intro_runtime_diagnostics(),
        capturequest_authored_runtime_diagnostics(),
        pallet_daisy_map_load_runtime_diagnostics(),
        pokemon_tower7f_rocket_exit_runtime_diagnostics(),
        cinnabar_gym_default_runtime_diagnostics(),
        name_rater_runtime_diagnostics(),
    ]
    for diagnostics in diagnostic_groups:
        for diagnostic in diagnostics:
            if diagnostic["status"] in {"covered", "generated"}:
                labels.add(diagnostic["scriptLabel"])
                labels.update(diagnostic.get("details", {}).get("source", {}).get("coveredLabels", []))
    return labels


class InGameTradeDiagnosticTest(unittest.TestCase):
    def test_inactive_source_trade_is_covered_not_unsupported(self):
        trades = {trade["tradeKey"]: trade for trade in in_game_trade_definitions()}
        diagnostic = generated_trade_diagnostic(trades["TRADE_FOR_CHIKUCHIKU"])

        self.assertFalse(diagnostic["details"]["active"])
        self.assertEqual(diagnostic["status"], "covered")
        self.assertEqual(diagnostic["reason"], "inactive_in_game_trade_definition_v1")


class CaptureQuestAuthoredRuntimeDiagnosticTest(unittest.TestCase):
    def test_known_authored_runtime_labels_are_covered(self):
        diagnostics = {
            (diagnostic["mapName"], diagnostic["scriptLabel"]): diagnostic
            for diagnostic in capturequest_authored_runtime_diagnostics()
        }

        self.assertEqual(
            set(diagnostics),
            {
                ("MtMoonB2F", "MtMoonB2F_Script"),
                ("PewterCity", "PewterCityDefaultScript"),
                ("VermilionCity", "VermilionCityLeftSSAnneCallbackScript"),
                ("VermilionDock", "VermilionDock_Script"),
            },
        )
        for diagnostic in diagnostics.values():
            self.assertEqual(diagnostic["status"], "covered")
            self.assertEqual(diagnostic["reason"], "capturequest_authored_runtime_v1")
            self.assertIn("captureQuestScript", diagnostic["details"])


class PalletDaisyRuntimeDiagnosticTest(unittest.TestCase):
    def test_pallet_daisy_map_load_effects_are_runtime_covered(self):
        diagnostics = pallet_daisy_map_load_runtime_diagnostics()

        self.assertEqual(len(diagnostics), 1)
        diagnostic = diagnostics[0]
        self.assertEqual(diagnostic["mapName"], "PalletTown")
        self.assertEqual(diagnostic["scriptLabel"], "PalletTownDaisyScript")
        self.assertEqual(diagnostic["status"], "covered")
        self.assertEqual(diagnostic["reason"], "pallet_daisy_map_load_runtime_v1")
        self.assertIn("map_load_visibility_sync", diagnostic["details"]["source"]["runtimeConcepts"])


class PokemonTower7FRocketExitRuntimeDiagnosticTest(unittest.TestCase):
    def test_rocket_exit_movement_table_is_runtime_covered(self):
        diagnostics = pokemon_tower7f_rocket_exit_runtime_diagnostics()

        self.assertEqual(len(diagnostics), 1)
        diagnostic = diagnostics[0]
        self.assertEqual(diagnostic["mapName"], "PokemonTower7F")
        self.assertEqual(diagnostic["scriptLabel"], "PokemonTower7FNPCCoordMovementTable")
        self.assertEqual(diagnostic["status"], "covered")
        self.assertEqual(diagnostic["reason"], "pokemon_tower7f_rocket_exit_runtime_v1")
        self.assertIn("trainer_post_win_cleanup", diagnostic["details"]["source"]["runtimeConcepts"])
        self.assertIn("PokemonTower7FRocketLeaveMovementScript", diagnostic["details"]["source"]["coveredLabels"])


class CinnabarGymDefaultRuntimeDiagnosticTest(unittest.TestCase):
    def test_quiz_trainer_handoff_is_runtime_covered(self):
        diagnostics = cinnabar_gym_default_runtime_diagnostics()

        self.assertEqual(len(diagnostics), 1)
        diagnostic = diagnostics[0]
        self.assertEqual(diagnostic["mapName"], "CinnabarGym")
        self.assertEqual(diagnostic["scriptLabel"], "CinnabarGymDefaultScript")
        self.assertEqual(diagnostic["status"], "covered")
        self.assertEqual(diagnostic["reason"], "cinnabar_gym_quiz_trainer_runtime_v1")
        self.assertIn("quiz_wrong_answer_trainer_handoff", diagnostic["details"]["source"]["runtimeConcepts"])
        self.assertIn("MovementNpcToLeftAndUp", diagnostic["details"]["source"]["coveredLabels"])
        self.assertIn("MovementNpcToLeft", diagnostic["details"]["source"]["coveredLabels"])


class NameRaterRuntimeDiagnosticTest(unittest.TestCase):
    def test_name_rater_yes_no_helper_is_runtime_covered(self):
        diagnostics = name_rater_runtime_diagnostics()

        self.assertEqual(len(diagnostics), 1)
        diagnostic = diagnostics[0]
        self.assertEqual(diagnostic["mapName"], "NameRatersHouse")
        self.assertEqual(diagnostic["scriptLabel"], "NameRatersHouseYesNoScript")
        self.assertEqual(diagnostic["status"], "covered")
        self.assertEqual(diagnostic["reason"], "name_rater_runtime_v1")
        self.assertIn("party_selection_ui", diagnostic["details"]["source"]["runtimeConcepts"])
        self.assertIn("pokemon_nickname_editing", diagnostic["details"]["source"]["runtimeConcepts"])
        self.assertIn("NameRatersHouseCheckMonOTScript", diagnostic["details"]["source"]["coveredLabels"])


class LancesRoomDefaultCandidateTest(unittest.TestCase):
    def test_default_coord_state_machine_splits_source_coord_table(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in lances_room_default_candidates()
        }

        self.assertEqual(
            set(candidates),
            {
                "LancesRoomLanceCoordBattle",
                "LancesRoomEntranceLock",
                "LancesRoomWalkToLance",
            },
        )

        battle = candidates["LancesRoomLanceCoordBattle"]
        self.assertEqual(battle["trigger"]["label"], "LanceBattleCoords")
        self.assertEqual(
            [(coord["x"], coord["y"]) for coord in battle["trigger"]["coordinates"]],
            [(5, 1), (6, 2)],
        )
        self.assertEqual(battle["actions"][2]["trainerClass"], "LANCE")
        self.assertEqual(battle["actions"][2]["partyIndex"], 1)
        self.assertIn("LancesRoomDefaultScript", battle["source"]["coveredLabels"])

        lock = candidates["LancesRoomEntranceLock"]
        self.assertEqual(lock["actions"], [{"type": "setEvent", "event": "EVENT_LANCES_ROOM_LOCK_DOOR"}])
        self.assertEqual(
            [(coord["x"], coord["y"]) for coord in lock["trigger"]["coordinates"]],
            [(5, 11), (6, 11)],
        )

        walk = candidates["LancesRoomWalkToLance"]
        self.assertEqual(walk["trigger"]["label"], "LanceWalkToLanceCoords")
        self.assertEqual(walk["actions"][1]["type"], "movePlayer")
        self.assertEqual(len(walk["actions"][1]["movements"]), 37)
        self.assertEqual(walk["actions"][1]["movements"][:3], ["UP", "UP", "UP"])


class ParseSpinTilesTest(unittest.TestCase):
    def test_parse_spin_tiles_preserves_source_xy_order(self):
        content = """
TestSpinTileSource:
\tmap_coord_movement  4,  9, TestSpinMovement
\tdb -1 ; end

TestSpinMovement:
\tdb D_UP, 2
\tdb D_RIGHT, 1
\tdb -1 ; end
"""

        tiles = parse_spin_tiles(content, "TestMap")

        self.assertEqual(len(tiles), 1)
        self.assertEqual(tiles[0]["source_label"], "TestSpinTileSource")
        self.assertEqual(tiles[0]["x"], 4)
        self.assertEqual(tiles[0]["y"], 9)
        self.assertEqual(
            json.loads(tiles[0]["movements"]),
            [
                {"direction": "RIGHT", "count": 1},
                {"direction": "UP", "count": 2},
            ],
        )

    def test_parse_spin_tiles_accepts_hex_counts(self):
        content = """
TestSpinTileSource:
\tmap_coord_movement 1, 2, TestSpinMovement
\tdb -1 ; end

TestSpinMovement:
\tdb D_LEFT, $3
\tdb -1 ; end
"""

        tiles = parse_spin_tiles(content, "TestMap")

        self.assertEqual(len(tiles), 1)
        self.assertEqual(json.loads(tiles[0]["movements"]), [{"direction": "LEFT", "count": 3}])


class OneShotObjectVisibilityMapScriptTest(unittest.TestCase):
    def test_generates_flag_gated_visibility_actions(self):
        block = {
            "label": "SilphCo1F_Script",
            "raw": """
SilphCo1F_Script:
\tcall EnableAutoTextBoxDrawing
\tCheckEvent EVENT_BEAT_SILPH_CO_GIOVANNI
\tret z
\tCheckAndSetEvent EVENT_SILPH_CO_RECEPTIONIST_AT_DESK
\tret nz
\tld a, HS_SILPH_CO_1F_RECEPTIONIST
\tld [wMissableObjectIndex], a
\tpredef_jump ShowObject
""",
        }

        candidates = one_shot_object_visibility_map_script_candidate_for_block(
            "SilphCo1F",
            SCRIPTS_DIR / "SilphCo1F.asm",
            TEXT_DIR / "SilphCo1F.asm",
            block,
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["scriptLabel"], "SilphCo1FScriptEventSilphCoReceptionistAtDeskVisibility")
        self.assertEqual(
            candidate["conditions"],
            {
                "requiresEvent": "EVENT_BEAT_SILPH_CO_GIOVANNI",
                "requiresEventAbsent": "EVENT_SILPH_CO_RECEPTIONIST_AT_DESK",
            },
        )
        self.assertEqual(
            candidate["actions"],
            [
                {"type": "setEvent", "event": "EVENT_SILPH_CO_RECEPTIONIST_AT_DESK"},
                {"type": "showObject", "objectKey": "HS_SILPH_CO_1F_RECEPTIONIST"},
            ],
        )
        self.assertEqual(candidate["source"]["coveredLabels"], ["SilphCo1F_Script"])

    def test_rejects_multi_branch_visibility_scripts(self):
        block = {
            "label": "Route25ShowHideBillScript",
            "raw": """
Route25ShowHideBillScript:
\tCheckEventHL EVENT_LEFT_BILLS_HOUSE_AFTER_HELPING
\tret nz
\tCheckEventReuseHL EVENT_MET_BILL_2
\tjr nz, .met_bill
\tResetEventReuseHL EVENT_BILL_SAID_USE_CELL_SEPARATOR
\tld a, HS_BILL_POKEMON
\tld [wMissableObjectIndex], a
\tpredef_jump ShowObject
.met_bill
\tCheckEventAfterBranchReuseHL EVENT_GOT_SS_TICKET, EVENT_MET_BILL_2
\tret z
\tSetEventReuseHL EVENT_LEFT_BILLS_HOUSE_AFTER_HELPING
\tld a, HS_BILL_2
\tld [wMissableObjectIndex], a
\tpredef_jump ShowObject
""",
        }

        candidates = one_shot_object_visibility_map_script_candidate_for_block(
            "Route25",
            SCRIPTS_DIR / "Route25.asm",
            TEXT_DIR / "Route25.asm",
            block,
        )

        self.assertEqual(candidates, [])


class SnorlaxWakeBattleCandidatesTest(unittest.TestCase):
    def test_generates_route12_and_route16_source_candidates(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in snorlax_wake_battle_candidates()
        }

        self.assertEqual(set(candidates), {"Route12SnorlaxEncounter", "Route16SnorlaxEncounter"})

        route12 = candidates["Route12SnorlaxEncounter"]
        self.assertEqual(route12["trigger"]["type"], "npc_click")
        self.assertEqual(route12["trigger"]["label"], "TEXT_ROUTE12_SNORLAX")
        self.assertEqual(route12["trigger"]["sourceLabel"], "Route12DefaultScript")
        self.assertEqual(route12["conditions"], {
            "requiresItem": "POKE_FLUTE",
            "requiresEventAbsent": "EVENT_BEAT_ROUTE12_SNORLAX",
        })
        self.assertEqual(route12["actions"][1]["type"], "dialogue")
        assert_dialogue_contains(self, route12["actions"][1]["lines"], "SNORLAX woke up!")
        battle = route12["actions"][3]
        self.assertEqual(battle["type"], "startWildBattle")
        self.assertEqual(battle["pokemonConstant"], "SNORLAX")
        self.assertEqual(battle["level"], 30)
        self.assertEqual(battle["winFlag"], "EVENT_BEAT_ROUTE12_SNORLAX")
        self.assertEqual(battle["postWinActions"], [
            {"type": "hideObject", "objectKey": "HS_ROUTE_12_SNORLAX"}
        ])
        self.assertEqual(route12["source"]["adapter"], "snorlax_wake_battle_v1")
        self.assertIn("Route12DefaultScript", route12["source"]["coveredLabels"])
        self.assertIn("Route12SnorlaxPostBattleScript", route12["source"]["coveredLabels"])

        route16 = candidates["Route16SnorlaxEncounter"]
        self.assertEqual(route16["trigger"]["label"], "TEXT_ROUTE16_SNORLAX")
        self.assertEqual(route16["conditions"]["requiresEventAbsent"], "EVENT_BEAT_ROUTE16_SNORLAX")
        self.assertEqual(route16["actions"][3]["postWinActions"], [
            {"type": "hideObject", "objectKey": "HS_ROUTE_16_SNORLAX"}
        ])
        self.assertIn("Route16SnorlaxPostBattleScript", route16["source"]["coveredLabels"])


class PokemonTowerMarowakGhostCandidateTest(unittest.TestCase):
    def test_generates_source_coord_wild_battle_with_post_win_dialogue(self):
        candidates = pokemon_tower_marowak_ghost_candidate()

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["scriptLabel"], "PokemonTower6FMarowakGhost")
        self.assertEqual(candidate["trigger"]["type"], "coord")
        self.assertEqual(candidate["trigger"]["label"], "PokemonTower6FMarowakCoords")
        self.assertEqual(candidate["trigger"]["coordinates"], [{"x": 10, "y": 16}])
        self.assertEqual(candidate["conditions"], {"requiresEventAbsent": "EVENT_BEAT_GHOST_MAROWAK"})
        self.assertEqual(candidate["actions"][1], {"type": "dialogue", "lines": ["Be gone...\nIntruders..."]})

        battle = candidate["actions"][3]
        self.assertEqual(battle["type"], "startWildBattle")
        self.assertEqual(battle["pokemonConstant"], "MAROWAK")
        self.assertEqual(battle["level"], 30)
        self.assertEqual(battle["winFlag"], "EVENT_BEAT_GHOST_MAROWAK")
        assert_dialogue_contains(self, battle["postWinActions"][0]["lines"], "The GHOST was the")
        assert_dialogue_contains(self, battle["postWinActions"][0]["lines"], "It departed to")
        self.assertEqual(candidate["source"]["adapter"], "pokemon_tower_marowak_ghost_v1")
        self.assertIn("PokemonTower6FDefaultScript", candidate["source"]["coveredLabels"])
        self.assertIn("PokemonTower6FMarowakBattleScript", candidate["source"]["coveredLabels"])


class ViridianOldManCatchTutorialCandidateTest(unittest.TestCase):
    def test_generates_inverted_choice_and_old_man_weedle_battle(self):
        candidates = viridian_old_man_catch_tutorial_candidate()

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["scriptLabel"], "ViridianCityOldManCatchDemo")
        self.assertEqual(candidate["trigger"]["label"], "TEXT_VIRIDIANCITY_OLD_MAN")
        self.assertEqual(candidate["conditions"], {"requiresEvent": "EVENT_GOT_POKEDEX"})

        choice = candidate["actions"][1]
        self.assertEqual(choice["type"], "choice")
        self.assertTrue(choice["stopOnYes"])
        self.assertTrue(choice["continueOnNo"])
        self.assertIn("Time is money", choice["yesLines"][0])
        self.assertIn("I see you're using", choice["noLines"][0])

        battle = candidate["actions"][2]
        self.assertEqual(battle["type"], "startWildBattle")
        self.assertEqual(battle["pokemonConstant"], "WEEDLE")
        self.assertEqual(battle["level"], 5)
        assert_dialogue_contains(self, battle["postWinActions"][0]["lines"], "First, you need")
        self.assertEqual(candidate["source"]["adapter"], "viridian_old_man_catch_tutorial_v1")
        self.assertIn("ViridianCityOldManEndCatchTrainingScript", candidate["source"]["coveredLabels"])


class BadgeOrEventGatedDialogueCandidateTest(unittest.TestCase):
    def test_generates_viridian_gym_returned_branches_from_badge_or_giovanni_flag(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in badge_or_event_gated_dialogue_candidates()
        }

        badge = candidates["ViridianCityGambler1TextEarthBadgeSet"]
        self.assertEqual(badge["trigger"]["label"], "TEXT_VIRIDIANCITY_GAMBLER1")
        self.assertEqual(badge["conditions"], {"requiresBadge": "EARTHBADGE"})
        self.assertEqual(badge["actions"][1]["lines"], ["VIRIDIAN GYM's\nLEADER returned!"])

        giovanni = candidates["ViridianCityGambler1TextEventBeatViridianGymGiovanniSetEarthBadgeAbsent"]
        self.assertEqual(
            giovanni["conditions"],
            {
                "requiresEvent": "EVENT_BEAT_VIRIDIAN_GYM_GIOVANNI",
                "requiresBadgesAbsent": ["EARTHBADGE"],
            },
        )
        self.assertEqual(giovanni["actions"][1]["lines"], badge["actions"][1]["lines"])

        closed = candidates["ViridianCityGambler1TextEarthBadgeEventBeatViridianGymGiovanniAbsent"]
        self.assertEqual(
            closed["conditions"],
            {
                "requiresEventsAbsent": ["EVENT_BEAT_VIRIDIAN_GYM_GIOVANNI"],
                "requiresBadgesAbsent": ["EARTHBADGE"],
            },
        )
        self.assertEqual(
            closed["actions"][1]["lines"],
            ["This POKEMON GYM\nis always closed.", "I wonder who the\nLEADER is?"],
        )
        self.assertEqual(closed["source"]["adapter"], "badge_or_event_gated_dialogue_v1")


class SpinTileDiagnosticsTest(unittest.TestCase):
    def test_spin_tile_runtime_diagnostics_tolerates_missing_table(self):
        conn = sqlite3.connect(":memory:")
        try:
            self.assertEqual(spin_tile_runtime_diagnostics(conn), [])
        finally:
            conn.close()

    def test_spin_tile_runtime_diagnostics_cover_source_labels(self):
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                """
                CREATE TABLE spin_tiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    map_name TEXT NOT NULL,
                    source_label TEXT NOT NULL,
                    x INTEGER NOT NULL,
                    y INTEGER NOT NULL,
                    movement_label TEXT NOT NULL,
                    movements TEXT NOT NULL
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO spin_tiles (map_name, source_label, x, y, movement_label, movements)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    ("RocketHideoutB2F", "RocketHideout2ArrowTilePlayerMovement", 4, 9, "Move1", "[]"),
                    ("RocketHideoutB2F", "RocketHideout2ArrowTilePlayerMovement", 4, 11, "Move2", "[]"),
                ],
            )

            diagnostics = spin_tile_runtime_diagnostics(conn)

            self.assertEqual(len(diagnostics), 1)
            self.assertEqual(diagnostics[0]["status"], "covered")
            self.assertEqual(diagnostics[0]["reason"], "spin_tile_runtime_v1")
            self.assertEqual(diagnostics[0]["scriptLabel"], "RocketHideout2ArrowTilePlayerMovement")
            self.assertEqual(diagnostics[0]["details"]["source"]["tileCount"], 2)
        finally:
            conn.close()


class DayCareRuntimeDiagnosticTest(unittest.TestCase):
    def test_daycare_gentleman_is_runtime_covered(self):
        script_path = SCRIPTS_DIR / "Daycare.asm"
        blocks = {block["label"]: block for block in extract_label_blocks(script_path.read_text())}
        block = blocks["DaycareGentlemanText"]
        ir = extract_features(block["label"], block["raw"])
        ir["mapName"] = "Daycare"

        diagnostic = diagnostic_for_ir_block(ir, set())

        self.assertIsNotNone(diagnostic)
        self.assertEqual(diagnostic["status"], "covered")
        self.assertEqual(diagnostic["reason"], "daycare_runtime_v1")
        self.assertIn("daycare_deposit", diagnostic["details"]["source"]["runtimeConcepts"])
        self.assertIn("level_up_move_learning", diagnostic["details"]["source"]["runtimeConcepts"])


class ChampionHallOfFameRuntimeDiagnosticTest(unittest.TestCase):
    def test_champion_finale_source_labels_are_runtime_covered(self):
        diagnostics = {
            diagnostic["scriptLabel"]: diagnostic
            for diagnostic in champion_hall_of_fame_runtime_diagnostics()
        }

        self.assertEqual(
            diagnostics["ChampionsRoomRivalText"]["details"]["captureQuestScript"],
            "ChampionsRoomRivalIntro",
        )
        self.assertEqual(
            diagnostics["ChampionsRoomPlayerFollowsOakScript"]["details"]["captureQuestScript"],
            "ChampionsRoomVictory",
        )
        self.assertEqual(
            diagnostics["HallOfFameOakCongratulationsScript"]["details"]["captureQuestScript"],
            "HallOfFameOakCongratulations",
        )
        self.assertEqual(
            diagnostics["HallOfFameOakCongratulationsScript"]["reason"],
            "champion_hall_of_fame_runtime_v1",
        )


class OakIntroRuntimeDiagnosticTest(unittest.TestCase):
    def test_oak_intro_source_labels_are_runtime_covered(self):
        diagnostics = {
            diagnostic["scriptLabel"]: diagnostic
            for diagnostic in oak_intro_runtime_diagnostics()
        }

        self.assertEqual(
            diagnostics["PalletTownDefaultScript"]["details"]["captureQuestScript"],
            "PalletTownOakStopsPlayer",
        )
        self.assertEqual(
            diagnostics["OaksLabOakChooseMonSpeechScript"]["details"]["captureQuestScript"],
            "OaksLabChooseStarterIntro",
        )
        self.assertIn(
            "OaksLabChooseBulbasaur",
            diagnostics["OaksLabRivalEndBattleScript"]["details"]["captureQuestScript"],
        )
        self.assertEqual(
            diagnostics["OaksLabOakGivesPokedexScript"]["details"]["captureQuestScript"],
            "OaksLabPokedexDelivery",
        )
        self.assertIn(
            "OaksLabPokedexDelivery",
            diagnostics["OaksLab_Script"]["details"]["captureQuestScript"],
        )
        self.assertIn(
            "OaksLabRivalPicksStarter",
            diagnostics["OaksLabRivalText"]["details"]["captureQuestScript"],
        )
        self.assertEqual(diagnostics["OaksLabOak1Text"]["reason"], "oak_intro_runtime_v1")


class PewterCityEscortCandidateTest(unittest.TestCase):
    def test_museum_and_gym_escort_candidates_use_source_branches(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in pewter_city_escort_candidates()
        }

        museum = candidates["PewterCitySuperNerd1MuseumGuide"]
        self.assertEqual(museum["trigger"]["label"], "TEXT_PEWTERCITY_SUPER_NERD1")
        choice = museum["actions"][1]
        self.assertEqual(choice["type"], "choice")
        self.assertTrue(choice["stopOnYes"])
        self.assertTrue(choice["continueOnNo"])
        assert_dialogue_contains(self, choice["promptLines"], "Did you check out")
        assert_dialogue_contains(self, choice["yesLines"], "Weren't those")
        assert_dialogue_contains(self, choice["noLines"], "Really?")
        self.assertEqual(museum["actions"][2], {"type": "move", "actor": "SUPER_NERD", "movements": ["DOWN", "DOWN", "DOWN", "DOWN"]})
        self.assertEqual(museum["actions"][4], {"type": "hideObject", "objectKey": "HS_MUSEUM_GUY"})
        self.assertIn("PewterCityHideSuperNerd1Script", museum["source"]["coveredLabels"])

        gym = candidates["PewterCityYoungsterGymGuide"]
        self.assertEqual(
            gym["trigger"]["coordinates"],
            [
                {"mapName": "PewterCity", "mapId": 2, "x": 35, "y": 17},
                {"mapName": "PewterCity", "mapId": 2, "x": 36, "y": 17},
                {"mapName": "PewterCity", "mapId": 2, "x": 37, "y": 18},
                {"mapName": "PewterCity", "mapId": 2, "x": 37, "y": 19},
            ],
        )
        self.assertEqual(gym["conditions"], {"requiresEventAbsent": "EVENT_BEAT_BROCK"})
        self.assertEqual(gym["actions"][2], {"type": "move", "actor": "YOUNGSTER", "movements": ["RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT"]})
        self.assertEqual(gym["actions"][4], {"type": "hideObject", "objectKey": "HS_GYM_GUY"})
        self.assertIn("PewterCityResetYoungsterScript", gym["source"]["coveredLabels"])


class TextPointerSwitchRuntimeDiagnosticTest(unittest.TestCase):
    def test_viridian_mart_parcel_text_pointer_switch_is_runtime_covered(self):
        script_path = SCRIPTS_DIR / "ViridianMart.asm"
        blocks = {block["label"]: block for block in extract_label_blocks(script_path.read_text())}
        block = blocks["ViridianMartCheckParcelDeliveredScript"]
        ir = extract_features(block["label"], block["raw"])
        ir["mapName"] = "ViridianMart"

        diagnostic = diagnostic_for_ir_block(ir, set())

        self.assertIsNotNone(diagnostic)
        self.assertEqual(diagnostic["status"], "covered")
        self.assertEqual(diagnostic["reason"], "text_pointer_switch_runtime_v1")
        self.assertIn("flag_gated_dialogue", diagnostic["details"]["source"]["runtimeConcepts"])
        self.assertIn(
            {"op": "CheckEvent", "flag": "EVENT_OAK_GOT_PARCEL"},
            diagnostic["details"]["eventRefs"],
        )


class SeafoamRuntimeDiagnosticTest(unittest.TestCase):
    def test_seafoam_boulder_map_script_is_runtime_covered(self):
        script_path = SCRIPTS_DIR / "SeafoamIslands1F.asm"
        blocks = {block["label"]: block for block in extract_label_blocks(script_path.read_text())}
        block = blocks["SeafoamIslands1F_Script"]
        ir = extract_features(block["label"], block["raw"])
        ir["mapName"] = "SeafoamIslands1F"

        diagnostic = diagnostic_for_ir_block(ir, set())

        self.assertIsNotNone(diagnostic)
        self.assertEqual(diagnostic["status"], "covered")
        self.assertEqual(diagnostic["reason"], "seafoam_boulder_current_runtime_v1")
        self.assertIn("strength_boulder_holes", diagnostic["details"]["source"]["runtimeConcepts"])
        self.assertIn("seafoam_currents", diagnostic["details"]["source"]["runtimeConcepts"])

    def test_route20_seafoam_reset_script_is_runtime_covered(self):
        script_path = SCRIPTS_DIR / "Route20.asm"
        blocks = {block["label"]: block for block in extract_label_blocks(script_path.read_text())}
        block = blocks["Route20_Script"]
        ir = extract_features(block["label"], block["raw"])
        ir["mapName"] = "Route20"

        diagnostic = diagnostic_for_ir_block(ir, set())

        self.assertIsNotNone(diagnostic)
        self.assertEqual(diagnostic["status"], "covered")
        self.assertEqual(diagnostic["reason"], "seafoam_boulder_current_runtime_v1")
        self.assertIn("route20_boulder_reset", diagnostic["details"]["source"]["runtimeConcepts"])


class NPCFacePlayerRuntimeDiagnosticTest(unittest.TestCase):
    def test_ss_anne_captain_face_player_flag_is_runtime_covered(self):
        script_path = SCRIPTS_DIR / "SSAnneCaptainsRoom.asm"
        blocks = {block["label"]: block for block in extract_label_blocks(script_path.read_text())}
        block = blocks["SSAnneCaptainsRoomEventScript"]
        ir = extract_features(block["label"], block["raw"])
        ir["mapName"] = "SSAnneCaptainsRoom"

        diagnostic = diagnostic_for_ir_block(ir, set())

        self.assertIsNotNone(diagnostic)
        self.assertEqual(diagnostic["status"], "covered")
        self.assertEqual(diagnostic["reason"], "npc_face_player_runtime_v1")
        self.assertIn("npc_facing_presentation", diagnostic["details"]["source"]["runtimeConcepts"])
        self.assertIn(
            {"op": "CheckEvent", "flag": "EVENT_RUBBED_CAPTAINS_BACK"},
            diagnostic["details"]["eventRefs"],
        )


class VictoryRoadBoulderTargetTest(unittest.TestCase):
    def test_boulder_targets_are_extracted_from_source_switch_coords(self):
        targets = victory_road_boulder_target_definitions()

        self.assertEqual(
            [
                (target["mapName"], target["x"], target["y"], target["flag"], target["dropsThroughHole"])
                for target in targets
            ],
            [
                ("VictoryRoad1F", 17, 13, "EVENT_VICTORY_ROAD_1_BOULDER_ON_SWITCH", False),
                ("VictoryRoad2F", 1, 16, "EVENT_VICTORY_ROAD_2_BOULDER_ON_SWITCH1", False),
                ("VictoryRoad2F", 9, 16, "EVENT_VICTORY_ROAD_2_BOULDER_ON_SWITCH2", False),
                ("VictoryRoad3F", 3, 5, "EVENT_VICTORY_ROAD_3_BOULDER_ON_SWITCH1", False),
                ("VictoryRoad3F", 23, 15, "EVENT_VICTORY_ROAD_3_BOULDER_ON_SWITCH2", True),
            ],
        )
        hole = targets[-1]
        self.assertEqual(hole["sourceMissableObject"], "HS_VICTORY_ROAD_3F_BOULDER")
        self.assertEqual(hole["destinationMapName"], "VictoryRoad2F")
        self.assertEqual(hole["destinationMissableObject"], "HS_VICTORY_ROAD_2F_BOULDER")


class SafariZoneGateCandidateTest(unittest.TestCase):
    def test_gate_candidates_cover_source_state_machine_labels(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in safari_zone_gate_candidates()
        }

        entry = candidates["SafariZoneGateEntryOffer"]
        exit_script = candidates["SafariZoneGateExit"]
        covered = set(entry["source"]["coveredLabels"])

        self.assertEqual(entry["source"]["adapter"], "safari_zone_gate_v1")
        self.assertEqual(exit_script["source"]["adapter"], "safari_zone_gate_v1")
        self.assertIn("SafariZoneGateDefaultScript", covered)
        self.assertIn("SafariZoneGateSafariZoneWorker1WouldYouLikeToJoinText", covered)
        self.assertIn("SafariZoneGateLeavingSafariScript", covered)
        self.assertIn("SafariZoneEntranceAutoWalk", covered)
        self.assertEqual(
            [action["type"] for action in exit_script["actions"]],
            ["lockInput", "choice", "endSafariSession", "unlockInput"],
        )


class ExtractFeaturesObjectRefsTest(unittest.TestCase):
    def test_object_refs_capture_missable_object_index_sequence(self):
        ir = extract_features(
            "TestHideScript",
            """
TestHideScript:
	ld a, HS_BILL_POKEMON
	ld [wMissableObjectIndex], a
	predef HideObject
	SetEvent EVENT_BILL_SAID_USE_CELL_SEPARATOR
	ret
""",
        )

        self.assertEqual(
            ir["objectRefs"],
            [{"op": "HideObject", "object": "HS_BILL_POKEMON", "source": "wMissableObjectIndex"}],
        )

    def test_object_refs_do_not_cross_newlines_into_event_names(self):
        ir = extract_features(
            "TestPredefOnly",
            """
TestPredefOnly:
	predef HideObject
	SetEvent EVENT_TEST
	ret
""",
        )

        self.assertEqual(ir["objectRefs"], [])

    def test_object_refs_capture_direct_macros_line_by_line(self):
        ir = extract_features(
            "TestDirectObject",
            """
TestDirectObject:
	HideObject HS_TEST_OBJECT
	ShowObject HS_OTHER_OBJECT
	ret
""",
        )

        self.assertEqual(
            ir["objectRefs"],
            [
                {"op": "HideObject", "object": "HS_TEST_OBJECT", "source": "direct"},
                {"op": "ShowObject", "object": "HS_OTHER_OBJECT", "source": "direct"},
            ],
        )


class PureFlagMapScriptCandidateTest(unittest.TestCase):
    def test_map_load_flag_side_effects_generate_map_script_candidates(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in pure_flag_map_script_candidates()
            if candidate["mapName"] in {"BluesHouse", "CeladonCity", "CinnabarIsland"}
        }

        self.assertEqual(
            set(candidates),
            {
                "BluesHouseDefaultScriptFlagSideEffects",
                "CeladonCityScriptFlagSideEffects",
                "CinnabarIslandScriptFlagSideEffects",
            },
        )

        blues_house = candidates["BluesHouseDefaultScriptFlagSideEffects"]
        self.assertEqual(blues_house["trigger"], {
            "type": "map_script",
            "label": "BluesHouseDefaultScript",
            "sourceLabel": "BluesHouseDefaultScript",
        })
        self.assertEqual(blues_house["conditions"], {"requiresEventAbsent": "EVENT_ENTERED_BLUES_HOUSE"})
        self.assertEqual(blues_house["actions"], [{"type": "setEvent", "event": "EVENT_ENTERED_BLUES_HOUSE"}])

        celadon = candidates["CeladonCityScriptFlagSideEffects"]
        self.assertEqual(celadon["conditions"], {})
        self.assertEqual(
            celadon["actions"],
            [
                {"type": "resetEvent", "event": "EVENT_1B8"},
                {"type": "resetEvent", "event": "EVENT_1BF"},
                {"type": "resetEvent", "event": "EVENT_67F"},
            ],
        )

        cinnabar = candidates["CinnabarIslandScriptFlagSideEffects"]
        self.assertEqual(cinnabar["conditions"], {})
        self.assertEqual(
            cinnabar["actions"],
            [
                {"type": "resetEvent", "event": "EVENT_MANSION_SWITCH_ON"},
                {"type": "resetEvent", "event": "EVENT_LAB_STILL_REVIVING_FOSSIL"},
            ],
        )

    def test_state_machine_cleanup_scripts_remain_unsupported(self):
        labels = {
            candidate["scriptLabel"]
            for candidate in pure_flag_map_script_candidates()
            if candidate["mapName"] in {"BillsHouse", "VictoryRoad2F"}
        }

        self.assertNotIn("BillsHouseCleanupScriptFlagSideEffects", labels)
        self.assertNotIn("VictoryRoad2FResetBoulderEventScriptFlagSideEffects", labels)


class ConditionalFlagMapScriptCandidateTest(unittest.TestCase):
    def test_flag_mirror_map_load_script_generates_conditioned_map_script(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in conditional_flag_map_script_candidates()
        }

        self.assertEqual(set(candidates), {"PalletTownScriptEventPalletAfterGettingPokeballsSet"})

        pallet = candidates["PalletTownScriptEventPalletAfterGettingPokeballsSet"]
        self.assertEqual(pallet["trigger"], {
            "type": "map_script",
            "label": "PalletTown_Script",
            "sourceLabel": "PalletTown_Script",
        })
        self.assertEqual(
            pallet["conditions"],
            {
                "requiresEvent": "EVENT_GOT_POKEBALLS_FROM_OAK",
                "requiresEventAbsent": "EVENT_PALLET_AFTER_GETTING_POKEBALLS",
            },
        )
        self.assertEqual(
            pallet["actions"],
            [{"type": "setEvent", "event": "EVENT_PALLET_AFTER_GETTING_POKEBALLS"}],
        )


class SplitTextFileCandidateTest(unittest.TestCase):
    def test_flag_gated_dialogue_uses_split_map_text_files(self):
        labels = {
            candidate["scriptLabel"]
            for candidate in flag_gated_dialogue_candidates()
            if candidate["mapName"] == "FuchsiaGym"
        }

        self.assertIn("FuchsiaGymGymGuideTextEventBeatKogaSet", labels)
        self.assertIn("FuchsiaGymGymGuideTextEventBeatKogaAbsent", labels)

    def test_flag_gated_dialogue_accepts_global_text_labels(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in flag_gated_dialogue_candidates()
            if candidate["mapName"] in {"GameCorner", "ViridianGym"}
        }

        game_corner_after = candidates["GameCornerGymGuideTextEventBeatErikaSet"]
        viridian_before = candidates["ViridianGymGymGuideTextEventBeatViridianGymGiovanniAbsent"]

        self.assertEqual(game_corner_after["conditions"], {"requiresEvent": "EVENT_BEAT_ERIKA"})
        assert_dialogue_contains(self, game_corner_after["actions"][1]["lines"], "They offer rare")
        self.assertEqual(
            viridian_before["conditions"],
            {"requiresEventAbsent": "EVENT_BEAT_VIRIDIAN_GYM_GIOVANNI"},
        )
        assert_dialogue_contains(self, viridian_before["actions"][1]["lines"], "Yo! Champ in")

    def test_flag_gated_dialogue_leaves_rival_state_machines_for_bespoke_adapters(self):
        labels = {
            candidate["scriptLabel"]
            for candidate in flag_gated_dialogue_candidates()
        }

        self.assertNotIn("CeruleanCityRivalTextEventBeatCeruleanRivalAbsent", labels)
        self.assertNotIn("Route22Rival1TextEventBeatRoute22Rival1stBattleAbsent", labels)


class ConditionalDialogueRowTest(unittest.TestCase):
    def test_oaks_lab_rival_nested_state_machine_emits_conditional_dialogue(self):
        rows = [
            row
            for row in conditional_dialogue_rows()
            if row["textConstant"] == "TEXT_OAKSLAB_RIVAL"
        ]

        self.assertEqual(len(rows), 3)
        by_priority = {row["priority"]: row for row in rows}

        self.assertEqual(
            by_priority[300]["conditions"],
            {
                "requiresEvents": [],
                "requiresEventsAbsent": ["EVENT_FOLLOWED_OAK_INTO_LAB_2"],
            },
        )
        self.assertEqual(
            by_priority[300]["dialogueLabels"],
            ["_OaksLabRivalGrampsIsntAroundText"],
        )
        self.assertEqual(
            by_priority[200]["conditions"],
            {
                "requiresEvents": ["EVENT_FOLLOWED_OAK_INTO_LAB_2", "EVENT_GOT_STARTER"],
                "requiresEventsAbsent": [],
            },
        )
        self.assertEqual(
            by_priority[200]["dialogueLabels"],
            ["_OaksLabRivalMyPokemonLooksStrongerText"],
        )
        self.assertEqual(
            by_priority[100]["conditions"],
            {
                "requiresEvents": ["EVENT_FOLLOWED_OAK_INTO_LAB_2"],
                "requiresEventsAbsent": ["EVENT_GOT_STARTER"],
            },
        )
        self.assertEqual(
            by_priority[100]["dialogueLabels"],
            ["_OaksLabRivalGoAheadAndChooseText"],
        )

    def test_simple_flag_gated_dialogue_emits_conditional_dialogue(self):
        rows = [
            row
            for row in conditional_dialogue_rows()
            if row["textConstant"] == "TEXT_GAMECORNER_GYM_GUIDE"
        ]

        self.assertEqual(len(rows), 2)
        by_priority = {row["priority"]: row for row in rows}
        self.assertEqual(by_priority[20]["conditions"], {
            "requiresEvents": ["EVENT_BEAT_ERIKA"],
            "requiresEventsAbsent": [],
        })
        self.assertIn("_GameCornerGymGuideTheyOfferRarePokemonText", by_priority[20]["dialogueLabels"])
        self.assertEqual(by_priority[10]["conditions"], {
            "requiresEvents": [],
            "requiresEventsAbsent": ["EVENT_BEAT_ERIKA"],
        })


class TextAsmPointerDiagnosticTest(unittest.TestCase):
    def test_single_text_asm_pointer_is_classified_as_direct_text_coverage(self):
        labels = generated_label_coverage_for_tests()
        diagnostics = {
            (diagnostic["mapName"], diagnostic["scriptLabel"]): diagnostic
            for diagnostic in text_asm_text_pointer_diagnostics(labels)
        }

        diagnostic = diagnostics[("BikeShop", "BikeShopMiddleAgedWomanText")]
        self.assertEqual(diagnostic["status"], "covered")
        self.assertEqual(diagnostic["reason"], "direct_text_pointer_v1")
        self.assertEqual(diagnostic["details"]["textConstant"], "TEXT_BIKESHOP_MIDDLE_AGED_WOMAN")
        self.assertEqual(diagnostic["details"]["textRefs"], ["BikeShopMiddleAgedWomanText"])

    def test_single_text_asm_label_is_classified_as_direct_text_coverage(self):
        labels = generated_label_coverage_for_tests()
        diagnostics = {
            (diagnostic["mapName"], diagnostic["scriptLabel"]): diagnostic
            for diagnostic in text_asm_text_pointer_diagnostics(labels)
        }

        diagnostic = diagnostics[("CeruleanCaveB1F", "MewtwoBattleText")]
        self.assertEqual(diagnostic["status"], "covered")
        self.assertEqual(diagnostic["reason"], "direct_text_label_v1")
        self.assertEqual(diagnostic["details"]["textConstant"], "")
        self.assertEqual(diagnostic["details"]["textRefs"], ["MewtwoBattleText"])

    def test_multi_branch_text_asm_pointer_is_review_diagnostic(self):
        labels = generated_label_coverage_for_tests()
        diagnostics = {
            (diagnostic["mapName"], diagnostic["scriptLabel"]): diagnostic
            for diagnostic in text_asm_text_pointer_diagnostics(labels)
        }

        diagnostic = diagnostics[("CeladonMansion3F", "CeladonMansion3FGameDesignerText")]
        self.assertEqual(diagnostic["status"], "unsupported")
        self.assertEqual(diagnostic["reason"], "text_asm_multi_text_branch")
        self.assertEqual(diagnostic["details"]["textConstant"], "TEXT_CELADONMANSION3F_GAME_DESIGNER")
        self.assertEqual(
            diagnostic["details"]["textRefs"],
            [
                "CeladonMansion3FGameDesignerText",
                "CeladonMansion3FGameDesignerCompletedDexText",
            ],
        )

    def test_every_text_asm_text_pointer_is_classified(self):
        labels = generated_label_coverage_for_tests()
        pointer_diagnostics = {
            (diagnostic["mapName"], diagnostic["scriptLabel"])
            for diagnostic in text_asm_text_pointer_diagnostics(labels)
        }

        unclassified = []
        for script_path in sorted(SCRIPTS_DIR.glob("*.asm")):
            map_name = script_path.stem
            script_content = script_path.read_text()
            text_pointers = parse_text_pointer_map(script_content)
            blocks = {block["label"]: block for block in extract_label_blocks(script_content)}
            for label in sorted(text_pointers):
                block = blocks.get(label)
                if not block:
                    continue
                clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
                if "text_asm" not in clean:
                    continue
                if label in labels or (map_name, label) in pointer_diagnostics:
                    continue
                ir = extract_features(label, block["raw"])
                ir["mapName"] = map_name
                if diagnostic_for_ir_block(ir, labels):
                    continue
                unclassified.append(f"{map_name}.{label}")

        self.assertEqual(unclassified, [])

    def test_every_text_asm_block_is_classified(self):
        labels = generated_label_coverage_for_tests()
        text_asm_diagnostics = {
            (diagnostic["mapName"], diagnostic["scriptLabel"])
            for diagnostic in text_asm_text_pointer_diagnostics(labels)
        }

        unclassified = []
        for block in extract_script_ir():
            if not block["features"]["hasTextAsm"]:
                continue
            if block["label"] in labels or (block["mapName"], block["label"]) in text_asm_diagnostics:
                continue
            if diagnostic_for_ir_block(block, labels):
                continue
            unclassified.append(f"{block['mapName']}.{block['label']}")

        self.assertEqual(unclassified, [])


class FacingUpDialogueCandidateTest(unittest.TestCase):
    def test_route11_binoculars_emit_facing_gated_branches(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in facing_up_dialogue_candidates()
            if candidate["mapName"] == "Route11Gate2F"
        }

        snorlax = candidates["Route11Gate2FLeftBinocularsTextEventBeatRoute12SnorlaxAbsentFacingUp"]
        clear = candidates["Route11Gate2FLeftBinocularsTextEventBeatRoute12SnorlaxSetFacingUp"]

        self.assertEqual(snorlax["conditions"]["requiresPlayerFacing"], "UP")
        self.assertEqual(snorlax["conditions"]["requiresEventAbsent"], "EVENT_BEAT_ROUTE12_SNORLAX")
        assert_dialogue_contains(self, snorlax["actions"][1]["lines"], "A big POKEMON is")
        assert_dialogue_contains(self, snorlax["actions"][1]["lines"], "asleep on a road!")

        self.assertEqual(clear["conditions"]["requiresPlayerFacing"], "UP")
        self.assertEqual(clear["conditions"]["requiresEvent"], "EVENT_BEAT_ROUTE12_SNORLAX")
        assert_dialogue_contains(self, clear["actions"][1]["lines"], "It's a beautiful")
        assert_dialogue_contains(self, clear["actions"][1]["lines"], "view!")


class PokemonMansionSwitchCandidateTest(unittest.TestCase):
    def test_secret_switches_emit_map_specific_toggle_candidates(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in pokemon_mansion_switch_candidates()
        }

        self.assertEqual(
            set(candidates),
            {
                "PokemonMansion1FSwitchToggle",
                "PokemonMansion2FSwitchToggle",
                "PokemonMansion3FSwitchToggle",
                "PokemonMansionB1FSwitchToggle",
            },
        )

        first_floor = candidates["PokemonMansion1FSwitchToggle"]
        basement = candidates["PokemonMansionB1FSwitchToggle"]

        self.assertEqual(first_floor["trigger"]["label"], "TEXT_POKEMONMANSION1F_SWITCH")
        assert_dialogue_contains(self, first_floor["actions"][1]["promptLines"], "A secret switch!")
        assert_dialogue_contains(self, first_floor["actions"][1]["yesLines"], "Who wouldn't?")
        assert_dialogue_contains(self, first_floor["actions"][1]["noLines"], "Not quite yet!")
        self.assertEqual(first_floor["actions"][2], {"type": "toggleEvent", "event": "EVENT_MANSION_SWITCH_ON"})

        self.assertEqual(basement["mapName"], "PokemonMansionB1F")
        self.assertEqual(basement["trigger"]["label"], "TEXT_POKEMONMANSIONB1F_SWITCH")
        self.assertEqual(basement["source"]["adapter"], "pokemon_mansion_switch_toggle_v1")

    def test_secret_switches_emit_source_tile_override_candidates(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in pokemon_mansion_switch_tile_override_candidates()
        }

        self.assertEqual(
            set(candidates),
            {
                "PokemonMansion1FSwitchDoorTiles",
                "PokemonMansion2FSwitchDoorTiles",
                "PokemonMansion3FSwitchDoorTiles",
                "PokemonMansionB1FSwitchDoorTiles",
            },
        )

        first_floor = candidates["PokemonMansion1FSwitchDoorTiles"]
        basement = candidates["PokemonMansionB1FSwitchDoorTiles"]

        self.assertIn(
            {
                "blockX": 6,
                "blockY": 12,
                "blockId": 14,
                "requiresEventAbsent": "EVENT_MANSION_SWITCH_ON",
                "labelPrefix": "PokemonMansionSwitchOff_1F_6_12",
            },
            first_floor["replacements"],
        )
        self.assertIn(
            {
                "blockX": 6,
                "blockY": 12,
                "blockId": 45,
                "requiresEvent": "EVENT_MANSION_SWITCH_ON",
                "labelPrefix": "PokemonMansionSwitchOn_1F_6_12",
            },
            first_floor["replacements"],
        )
        self.assertIn(
            {
                "blockX": 8,
                "blockY": 13,
                "blockId": 14,
                "requiresEventAbsent": "EVENT_MANSION_SWITCH_ON",
                "labelPrefix": "PokemonMansionSwitchOff_B1F_8_13",
            },
            basement["replacements"],
        )
        self.assertIn(
            {
                "blockX": 8,
                "blockY": 13,
                "blockId": 45,
                "requiresEvent": "EVENT_MANSION_SWITCH_ON",
                "labelPrefix": "PokemonMansionSwitchOn_B1F_8_13",
            },
            basement["replacements"],
        )


class BadgeGatedGymGuideCandidateTest(unittest.TestCase):
    def test_pewter_gym_guide_emits_badge_absent_choice_and_badge_set_dialogue(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in badge_gated_gym_guide_candidates()
        }

        before = candidates["PewterGymGuideTextBoulderBadgeAbsent"]
        after = candidates["PewterGymGuideTextBoulderBadgeSet"]

        self.assertEqual(before["conditions"], {"requiresBadgeAbsent": "BOULDERBADGE"})
        self.assertEqual(before["actions"][1]["type"], "choice")
        assert_dialogue_contains(self, before["actions"][1]["promptLines"], "Let me take you")
        assert_dialogue_contains(self, before["actions"][1]["yesLines"], "All right! Let's")
        assert_dialogue_contains(self, before["actions"][1]["noLines"], "It's a free")
        self.assertEqual(after["conditions"], {"requiresBadge": "BOULDERBADGE"})
        assert_dialogue_contains(self, after["actions"][1]["lines"], "Just as I thought!")

    def test_vermilion_gym_guide_emits_badge_gated_dialogue(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in badge_gated_gym_guide_candidates()
        }

        before = candidates["VermilionGymGymGuideTextThunderBadgeAbsent"]
        after = candidates["VermilionGymGymGuideTextThunderBadgeSet"]

        self.assertEqual(before["conditions"], {"requiresBadgeAbsent": "THUNDERBADGE"})
        assert_dialogue_contains(self, before["actions"][1]["lines"], "Yo! Champ in")
        self.assertEqual(after["conditions"], {"requiresBadge": "THUNDERBADGE"})
        assert_dialogue_contains(self, after["actions"][1]["lines"], "Whew! That match")


class VermilionSSAnneGuardCandidateTest(unittest.TestCase):
    def test_generates_ticket_pass_and_blocked_coord_branches(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in vermilion_ss_anne_guard_candidates()
        }

        self.assertEqual(
            set(candidates),
            {
                "VermilionCitySSAnneGuardPass",
                "VermilionCitySSAnneGuardNoTicketBlocked",
                "VermilionCitySSAnneGuardShipDepartedBlocked",
            },
        )

        passed = candidates["VermilionCitySSAnneGuardPass"]
        self.assertEqual(passed["trigger"]["type"], "coord")
        self.assertEqual(passed["trigger"]["label"], "SSAnneTicketCheckCoords")
        self.assertEqual(passed["trigger"]["coordinates"], [{"mapName": "VermilionCity", "x": 18, "y": 30}])
        self.assertEqual(
            passed["conditions"],
            {
                "requiresPlayerFacing": "DOWN",
                "requiresEventAbsent": "EVENT_SS_ANNE_LEFT",
                "requiresItem": "S_S_TICKET",
            },
        )
        assert_dialogue_contains(self, passed["actions"][1]["lines"], "Welcome to S.S.")
        assert_dialogue_contains(self, passed["actions"][1]["lines"], "(PLAYER) flashed")
        assert_dialogue_contains(self, passed["actions"][1]["lines"], "the S.S.TICKET!")
        self.assertEqual(passed["source"]["adapter"], "vermilion_ss_anne_guard_v1")

        no_ticket = candidates["VermilionCitySSAnneGuardNoTicketBlocked"]
        self.assertEqual(no_ticket["conditions"]["requiresItemAbsent"], "S_S_TICKET")
        self.assertEqual(no_ticket["actions"][2], {"type": "movePlayer", "movements": ["UP"]})
        assert_dialogue_contains(self, no_ticket["actions"][1]["lines"], "You need a ticket")

        departed = candidates["VermilionCitySSAnneGuardShipDepartedBlocked"]
        self.assertEqual(
            departed["conditions"],
            {
                "requiresPlayerFacing": "DOWN",
                "requiresEvent": "EVENT_SS_ANNE_LEFT",
            },
        )
        self.assertEqual(departed["actions"][2], {"type": "movePlayer", "movements": ["UP"]})
        assert_dialogue_contains(self, departed["actions"][1]["lines"], "The ship set sail.")


class ViridianCityProgressBlockerCandidateTest(unittest.TestCase):
    def test_generates_gym_and_old_man_coord_blockers(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in viridian_city_progress_blocker_candidates()
        }

        self.assertEqual(
            set(candidates),
            {
                "ViridianCityGymOpenFromEarthBadge",
                "ViridianCityGymLockedBlocked",
                "ViridianCityOldManSleepyBlocked",
            },
        )

        gym_open = candidates["ViridianCityGymOpenFromEarthBadge"]
        self.assertEqual(gym_open["trigger"]["label"], "ViridianCityGymLockedCoords")
        self.assertEqual(gym_open["trigger"]["coordinates"], [{"mapName": "ViridianCity", "x": 32, "y": 8}])
        self.assertEqual(
            gym_open["conditions"],
            {
                "requiresEvent": "EVENT_GOT_EARTHBADGE",
                "requiresEventAbsent": "EVENT_VIRIDIAN_GYM_OPEN",
            },
        )
        self.assertEqual(gym_open["actions"], [{"type": "setEvent", "event": "EVENT_VIRIDIAN_GYM_OPEN"}])

        gym_blocked = candidates["ViridianCityGymLockedBlocked"]
        self.assertEqual(
            gym_blocked["conditions"],
            {"requiresEventsAbsent": ["EVENT_VIRIDIAN_GYM_OPEN", "EVENT_GOT_EARTHBADGE"]},
        )
        assert_dialogue_contains(self, gym_blocked["actions"][1]["lines"], "The GYM's doors")
        self.assertEqual(gym_blocked["actions"][2], {"type": "movePlayer", "movements": ["DOWN"]})

        old_man = candidates["ViridianCityOldManSleepyBlocked"]
        self.assertEqual(old_man["trigger"]["label"], "ViridianCityOldManSleepyCoords")
        self.assertEqual(old_man["trigger"]["coordinates"], [{"mapName": "ViridianCity", "x": 19, "y": 9}])
        self.assertEqual(old_man["conditions"], {"requiresEventAbsent": "EVENT_GOT_POKEDEX"})
        assert_dialogue_contains(self, old_man["actions"][1]["lines"], "You can't go")
        self.assertEqual(old_man["actions"][2], {"type": "movePlayer", "movements": ["DOWN"]})
        self.assertEqual(old_man["source"]["adapter"], "viridian_city_progress_blocker_v1")
        self.assertIn("ViridianCityCheckGymOpenScript", old_man["source"]["coveredLabels"])
        self.assertIn("ViridianCityCheckGotPokedexScript", old_man["source"]["coveredLabels"])


class EliteFourRoomEntranceGuardCandidateTest(unittest.TestCase):
    def test_generates_entry_and_exit_denial_branches_for_simple_elite_four_rooms(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in elite_four_room_entrance_guard_candidates()
        }

        self.assertEqual(
            set(candidates),
            {
                "LoreleisRoomEntranceAutoWalk",
                "LoreleisRoomDontRunAway",
                "BrunosRoomEntranceAutoWalk",
                "BrunosRoomDontRunAway",
                "AgathasRoomEntranceAutoWalk",
                "AgathasRoomDontRunAway",
            },
        )

        lorelei_entry = candidates["LoreleisRoomEntranceAutoWalk"]
        self.assertEqual(lorelei_entry["trigger"]["type"], "coord")
        self.assertEqual(lorelei_entry["trigger"]["label"], "LoreleiEntranceCoords")
        self.assertEqual(
            lorelei_entry["trigger"]["coordinates"],
            [
                {"mapName": "LoreleisRoom", "mapId": 245, "x": 4, "y": 10},
                {"mapName": "LoreleisRoom", "mapId": 245, "x": 5, "y": 10},
                {"mapName": "LoreleisRoom", "mapId": 245, "x": 4, "y": 11},
                {"mapName": "LoreleisRoom", "mapId": 245, "x": 5, "y": 11},
            ],
        )
        self.assertEqual(
            lorelei_entry["conditions"],
            {
                "requiresEventsAbsent": [
                    "EVENT_AUTOWALKED_INTO_LORELEIS_ROOM",
                    "EVENT_BEAT_LORELEIS_ROOM_TRAINER_0",
                ],
            },
        )
        self.assertEqual(lorelei_entry["actions"][1], {"type": "setEvent", "event": "EVENT_AUTOWALKED_INTO_LORELEIS_ROOM"})
        self.assertEqual(
            lorelei_entry["actions"][2],
            {"type": "movePlayer", "movements": ["UP", "UP", "UP", "UP", "UP", "UP"]},
        )
        self.assertEqual(lorelei_entry["source"]["adapter"], "elite_four_room_entrance_guard_v1")
        self.assertIn("LoreleisRoomDefaultScript", lorelei_entry["source"]["coveredLabels"])
        self.assertIn("LoreleiScriptWalkIntoRoom", lorelei_entry["source"]["coveredLabels"])

        agatha_blocked = candidates["AgathasRoomDontRunAway"]
        self.assertEqual(
            agatha_blocked["conditions"],
            {
                "requiresEvent": "EVENT_AUTOWALKED_INTO_AGATHAS_ROOM",
                "requiresEventAbsent": "EVENT_BEAT_AGATHAS_ROOM_TRAINER_0",
            },
        )
        self.assertIn("Don't run away", "\n".join(agatha_blocked["actions"][1]["lines"]))
        self.assertEqual(agatha_blocked["actions"][2], {"type": "movePlayer", "movements": ["UP"]})


class LanceRoomEntranceTileOverrideCandidateTest(unittest.TestCase):
    def test_generates_lance_entrance_open_and_closed_blocks(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in lance_room_entrance_tile_override_candidates()
        }

        candidate = candidates["LancesRoomEntranceBlocks"]

        self.assertEqual(candidate["mapName"], "LancesRoom")
        self.assertEqual(candidate["source"]["adapter"], "lance_room_entrance_tile_override_v1")
        self.assertEqual(candidate["source"]["coveredLabels"], ["LanceShowOrHideEntranceBlocks"])
        self.assertEqual(
            candidate["replacements"],
            [
                {
                    "blockX": 6,
                    "blockY": 2,
                    "blockId": 0x31,
                    "requiresEventAbsent": "EVENT_LANCES_ROOM_LOCK_DOOR",
                    "labelPrefix": "LanceEntranceLeftOpen",
                },
                {
                    "blockX": 6,
                    "blockY": 3,
                    "blockId": 0x32,
                    "requiresEventAbsent": "EVENT_LANCES_ROOM_LOCK_DOOR",
                    "labelPrefix": "LanceEntranceRightOpen",
                },
                {
                    "blockX": 6,
                    "blockY": 2,
                    "blockId": 0x72,
                    "requiresEvent": "EVENT_LANCES_ROOM_LOCK_DOOR",
                    "labelPrefix": "LanceEntranceLeftClosed",
                },
                {
                    "blockX": 6,
                    "blockY": 3,
                    "blockId": 0x73,
                    "requiresEvent": "EVENT_LANCES_ROOM_LOCK_DOOR",
                    "labelPrefix": "LanceEntranceRightClosed",
                },
            ],
        )


class FightingDojoKarateMasterCandidateTest(unittest.TestCase):
    def test_generates_special_karate_master_battle_and_reward_prompt(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in fighting_dojo_karate_master_candidates()
        }

        self.assertEqual(
            set(candidates),
            {
                "FightingDojoKarateMasterBattle",
                "FightingDojoKarateMasterCoordBattle",
                "FightingDojoKarateMasterPostBattle",
                "FightingDojoKarateMasterRewardPrompt",
                "FightingDojoKarateMasterStayAndTrain",
            },
        )

        battle = candidates["FightingDojoKarateMasterBattle"]
        self.assertEqual(battle["trigger"]["label"], "TEXT_FIGHTINGDOJO_KARATE_MASTER")
        self.assertEqual(battle["conditions"], {"requiresEventAbsent": "EVENT_BEAT_KARATE_MASTER"})
        assert_dialogue_contains(self, battle["actions"][1]["lines"], "Fwaaa!")
        self.assertEqual(battle["actions"][2]["type"], "startTrainerBattle")
        self.assertEqual(battle["actions"][2]["trainerClass"], "BLACKBELT")
        self.assertEqual(battle["actions"][2]["partyIndex"], 1)
        self.assertEqual(battle["actions"][2]["winFlag"], "EVENT_BEAT_KARATE_MASTER")
        self.assertEqual(battle["actions"][2].get("postWinActions"), [])
        self.assertIn("FightingDojoKarateMasterText", battle["source"]["coveredLabels"])
        self.assertIn("FightingDojoKarateMasterPostBattleScript", battle["source"]["coveredLabels"])

        coord = candidates["FightingDojoKarateMasterCoordBattle"]
        self.assertEqual(coord["trigger"]["type"], "coord")
        self.assertEqual(coord["trigger"]["coordinates"], [{"mapName": "FightingDojo", "mapId": 177, "x": 4, "y": 3}])
        self.assertEqual(coord["actions"][2]["trainerClass"], "BLACKBELT")

        prompt = candidates["FightingDojoKarateMasterPostBattle"]
        self.assertEqual(
            prompt["conditions"],
            {
                "requiresEvent": "EVENT_BEAT_KARATE_MASTER",
                "requiresEventAbsent": "EVENT_GOT_FIGHTING_DOJO_POKEMON",
            },
        )
        assert_dialogue_contains(self, prompt["actions"][1]["lines"], "Choose whichever")
        self.assertEqual(
            prompt["actions"][2:7],
            [
                {"type": "setEvent", "event": "EVENT_BEAT_KARATE_MASTER"},
                {"type": "setEvent", "event": "EVENT_BEAT_FIGHTING_DOJO_TRAINER_0"},
                {"type": "setEvent", "event": "EVENT_BEAT_FIGHTING_DOJO_TRAINER_1"},
                {"type": "setEvent", "event": "EVENT_BEAT_FIGHTING_DOJO_TRAINER_2"},
                {"type": "setEvent", "event": "EVENT_BEAT_FIGHTING_DOJO_TRAINER_3"},
            ],
        )

        reward_click = candidates["FightingDojoKarateMasterRewardPrompt"]
        self.assertEqual(
            reward_click["conditions"],
            {
                "requiresEvent": "EVENT_BEAT_KARATE_MASTER",
                "requiresEventAbsent": "EVENT_GOT_FIGHTING_DOJO_POKEMON",
            },
        )
        assert_dialogue_contains(self, reward_click["actions"][1]["lines"], "Choose whichever")

        stay = candidates["FightingDojoKarateMasterStayAndTrain"]
        self.assertEqual(stay["conditions"], {"requiresEvent": "EVENT_DEFEATED_FIGHTING_DOJO"})
        assert_dialogue_contains(self, stay["actions"][1]["lines"], "Karate with us!")


class FanBoastToggleCandidateTest(unittest.TestCase):
    def test_pokemon_fan_club_fans_emit_stateful_toggle_branches(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in fan_boast_toggle_candidates()
        }

        pikachu_normal = candidates["PokemonFanClubPikachuFanTextNormal"]
        pikachu_better = candidates["PokemonFanClubPikachuFanTextMineIsBetter"]
        seel_normal = candidates["PokemonFanClubSeelFanTextNormal"]
        seel_better = candidates["PokemonFanClubSeelFanTextMineIsBetter"]

        self.assertEqual(pikachu_normal["conditions"], {"requiresEventAbsent": "EVENT_PIKACHU_FAN_BOAST"})
        self.assertEqual(pikachu_normal["actions"][2], {"type": "setEvent", "event": "EVENT_SEEL_FAN_BOAST"})
        self.assertEqual(pikachu_better["conditions"], {"requiresEvent": "EVENT_PIKACHU_FAN_BOAST"})
        self.assertEqual(pikachu_better["actions"][2], {"type": "resetEvent", "event": "EVENT_PIKACHU_FAN_BOAST"})

        self.assertEqual(seel_normal["conditions"], {"requiresEventAbsent": "EVENT_SEEL_FAN_BOAST"})
        self.assertEqual(seel_normal["actions"][2], {"type": "setEvent", "event": "EVENT_PIKACHU_FAN_BOAST"})
        self.assertEqual(seel_better["conditions"], {"requiresEvent": "EVENT_SEEL_FAN_BOAST"})
        self.assertEqual(seel_better["actions"][2], {"type": "resetEvent", "event": "EVENT_SEEL_FAN_BOAST"})


class GameCornerPosterCandidateTest(unittest.TestCase):
    def test_poster_switch_emits_flag_script_candidate(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in simple_flag_side_effect_dialogue_candidates()
            if candidate["mapName"] == "GameCorner"
        }

        poster = candidates["GameCornerPosterTextEventFoundRocketHideout"]

        self.assertEqual(poster["trigger"]["label"], "TEXT_GAMECORNER_POSTER")
        self.assertEqual(poster["actions"][2], {"type": "setEvent", "event": "EVENT_FOUND_ROCKET_HIDEOUT"})
        assert_dialogue_contains(self, poster["actions"][1]["lines"], "A switch behind")
        assert_dialogue_contains(self, poster["actions"][1]["lines"], "the poster!?")
        self.assertEqual(
            poster["source"]["tileReplacement"],
            {"blockX": 8, "blockY": 2, "blockId": 67, "event": "EVENT_FOUND_ROCKET_HIDEOUT"},
        )

    def test_poster_switch_emits_event_tile_override_candidate(self):
        candidates = game_corner_rocket_hideout_tile_override_candidates()

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["scriptLabel"], "GameCornerRocketHideoutDoorTile")
        self.assertEqual(
            candidate["replacements"],
            [
                {
                    "blockX": 8,
                    "blockY": 2,
                    "blockId": 42,
                    "requiresEventAbsent": "EVENT_FOUND_ROCKET_HIDEOUT",
                    "labelPrefix": "GameCornerRocketHideoutDoorClosed",
                },
                {
                    "blockX": 8,
                    "blockY": 2,
                    "blockId": 67,
                    "requiresEvent": "EVENT_FOUND_ROCKET_HIDEOUT",
                    "labelPrefix": "GameCornerRocketHideoutDoorOpen",
                },
            ],
        )


class GameCornerRocketDefeatedCandidateTest(unittest.TestCase):
    def test_rocket_post_battle_cleanup_is_generated_from_source(self):
        candidates = game_corner_rocket_defeated_candidate()

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["scriptLabel"], "GameCornerRocketDefeated")
        self.assertEqual(candidate["trigger"], {"type": "map_script", "label": "GameCornerRocketBattleScript"})
        self.assertEqual(
            candidate["conditions"],
            {
                "requiresEvent": "EVENT_BEAT_GAME_CORNER_ROCKET",
                "requiresEventAbsent": "EVENT_GAME_CORNER_ROCKET_LEFT",
            },
        )
        self.assertEqual(candidate["actions"][0], {"type": "lockInput"})
        assert_dialogue_contains(self, candidate["actions"][1]["lines"], "Dang!")
        assert_dialogue_contains(self, candidate["actions"][1]["lines"], "Our hideout might")
        self.assertEqual(
            candidate["actions"][2],
            {"type": "move", "actor": "ROCKET", "movements": ["DOWN", "DOWN", "DOWN", "RIGHT", "RIGHT"]},
        )
        self.assertEqual(candidate["actions"][3], {"type": "setEvent", "event": "EVENT_GAME_CORNER_ROCKET_LEFT"})
        self.assertEqual(candidate["actions"][4], {"type": "hideActor", "actor": "ROCKET"})
        self.assertEqual(candidate["source"]["adapter"], "game_corner_rocket_defeated_v1")
        self.assertIn("GameCornerRocketBattleScript", candidate["source"]["coveredLabels"])
        self.assertIn("GameCornerRocketExitScript", candidate["source"]["coveredLabels"])
        self.assertEqual(
            candidate["source"]["movementVariants"]["direct"],
            ["RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT"],
        )
        self.assertEqual(
            candidate["source"]["movementVariants"]["aroundPlayer"],
            ["DOWN", "RIGHT", "RIGHT", "UP", "RIGHT", "RIGHT", "RIGHT", "RIGHT"],
        )


class SSAnne2FRivalCandidateTest(unittest.TestCase):
    def test_rival_encounter_is_generated_but_preservable_as_capturequest_override(self):
        candidates = ss_anne_2f_rival_candidate()

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["scriptLabel"], "SSAnne2FRivalEncounter")
        self.assertEqual(candidate["trigger"]["label"], "SSAnne2FRivalCoords")
        self.assertEqual(candidate["trigger"]["coordinates"], [{"x": 36, "y": 8}, {"x": 37, "y": 8}])
        self.assertEqual(candidate["conditions"], {"requiresEventAbsent": "EVENT_BEAT_SS_ANNE_RIVAL"})
        self.assertEqual(candidate["actions"][1], {"type": "showObject", "objectKey": "HS_SS_ANNE_2F_RIVAL"})
        self.assertEqual(candidate["actions"][2]["movements"], ["DOWN", "DOWN", "DOWN", "DOWN"])
        self.assertIn("Bonjour!", candidate["actions"][3]["lines"][0])
        battle = candidate["actions"][4]
        self.assertEqual(battle["type"], "startTrainerBattle")
        self.assertEqual(battle["trainerClass"], "RIVAL2")
        self.assertEqual(battle["winFlag"], "EVENT_BEAT_SS_ANNE_RIVAL")
        self.assertEqual(battle["postWinActions"][1]["movements"], ["RIGHT", "DOWN", "DOWN", "DOWN", "DOWN", "DOWN"])
        self.assertEqual(battle["postWinActions"][2], {"type": "hideObject", "objectKey": "HS_SS_ANNE_2F_RIVAL"})
        self.assertEqual(candidate["source"]["adapter"], "ss_anne_2f_rival_v1")
        self.assertIn("SSAnne2FDefaultScript", candidate["source"]["coveredLabels"])
        self.assertIn("SSAnne2FRivalExitScript", candidate["source"]["coveredLabels"])
        self.assertEqual(candidate["source"]["movementVariants"]["approachFromRightCoord"], ["DOWN", "DOWN", "DOWN"])
        self.assertEqual(candidate["source"]["movementVariants"]["exitFromRightCoord"], ["DOWN", "DOWN", "DOWN", "DOWN"])


class SilphCo11FGiovanniCandidateTest(unittest.TestCase):
    def test_giovanni_encounter_and_cleanup_are_generated_from_source(self):
        candidates = silph_co_11f_giovanni_candidate()

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["scriptLabel"], "SilphCo11FGiovanniEncounter")
        self.assertEqual(candidate["trigger"]["label"], "SilphCo11FGiovanniCoords")
        self.assertEqual(
            candidate["trigger"]["coordinates"],
            [
                {"mapName": "SilphCo11F", "mapId": 235, "x": 6, "y": 13},
                {"mapName": "SilphCo11F", "mapId": 235, "x": 7, "y": 12},
            ],
        )
        self.assertEqual(candidate["conditions"], {"requiresEventAbsent": "EVENT_BEAT_SILPH_CO_GIOVANNI"})
        self.assertEqual(candidate["actions"][0], {"type": "lockInput"})
        self.assertEqual(candidate["actions"][1], {"type": "move", "actor": "GIOVANNI", "movements": ["DOWN", "DOWN", "DOWN"]})
        self.assertIn("Ah (PLAYER)!", candidate["actions"][2]["lines"][0])

        battle = candidate["actions"][3]
        self.assertEqual(battle["type"], "startTrainerBattle")
        self.assertEqual(battle["trainerClass"], "GIOVANNI")
        self.assertEqual(battle["partyIndex"], 2)
        self.assertEqual(battle["winFlag"], "EVENT_BEAT_SILPH_CO_GIOVANNI")
        self.assertEqual(battle["postWinActions"][0]["type"], "dialogue")
        self.assertIn("Blast it all!", battle["postWinActions"][0]["lines"][0])
        self.assertEqual(battle["postWinActions"][1], {"type": "hideActor", "actor": "GIOVANNI"})
        self.assertEqual(battle["postWinActions"][2], {"type": "setEvent", "event": "EVENT_SILPH_GIOVANNI_LEFT"})
        self.assertIn({"type": "hideObject", "objectKey": "HS_SILPH_CO_11F_1"}, battle["postWinActions"])
        self.assertIn({"type": "showObject", "objectKey": "HS_SAFFRON_CITY_8"}, battle["postWinActions"])
        self.assertEqual(candidate["source"]["adapter"], "silph_co_11f_giovanni_v1")
        self.assertIn("SilphCo11FGiovanniAfterBattleScript", candidate["source"]["coveredLabels"])
        self.assertIn("SilphCo11FTeamRocketLeavesScript", candidate["source"]["coveredLabels"])


class SilphCo6FGiovanniDialogueCandidateTest(unittest.TestCase):
    def test_shared_hl_de_giovanni_dialogue_helper_generates_worker_branches(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in silph_co_6f_giovanni_dialogue_candidates()
        }

        self.assertEqual(len(candidates), 10)
        before = candidates["SilphCo6FSilphWorkerM1TextEventBeatSilphCoGiovanniAbsent"]
        after = candidates["SilphCo6FSilphWorkerM1TextEventBeatSilphCoGiovanniSet"]

        self.assertEqual(before["trigger"]["label"], "TEXT_SILPHCO6F_SILPH_WORKER_M1")
        self.assertEqual(before["conditions"], {"requiresEventAbsent": "EVENT_BEAT_SILPH_CO_GIOVANNI"})
        self.assertIn("The ROCKETs came", before["actions"][1]["lines"][0])
        self.assertEqual(after["conditions"], {"requiresEvent": "EVENT_BEAT_SILPH_CO_GIOVANNI"})
        self.assertIn("Well, better get", after["actions"][1]["lines"][0])
        self.assertEqual(before["source"]["adapter"], "silph_co_6f_giovanni_dialogue_v1")
        self.assertIn("SilphCo6FBeatGiovanniPrintDEOrPrintHLScript", before["source"]["coveredLabels"])

        f2_after = candidates["SilphCo6FSilphWorkerF2TextEventBeatSilphCoGiovanniSet"]
        self.assertEqual(f2_after["trigger"]["label"], "TEXT_SILPHCO6F_SILPH_WORKER_F2")
        self.assertIn("TEAM ROCKET ran", f2_after["actions"][1]["lines"][0])


class PokemonTower7FMrFujiRescueCandidateTest(unittest.TestCase):
    def test_mr_fuji_rescue_includes_source_flags_objects_and_warp(self):
        candidates = pokemon_tower_7f_mr_fuji_rescue_candidate()

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["scriptLabel"], "PokemonTower7FMrFujiRescue")
        self.assertEqual(candidate["trigger"], {
            "type": "npc_click",
            "label": "TEXT_POKEMONTOWER7F_MR_FUJI",
            "sourceLabel": "PokemonTower7FMrFujiText",
        })
        self.assertEqual(candidate["conditions"], {
            "requiresEvents": [
                "EVENT_BEAT_POKEMONTOWER_7_TRAINER_0",
                "EVENT_BEAT_POKEMONTOWER_7_TRAINER_1",
                "EVENT_BEAT_POKEMONTOWER_7_TRAINER_2",
            ],
            "requiresEventAbsent": "EVENT_RESCUED_MR_FUJI",
        })
        self.assertIn("MR.FUJI: Heh?", candidate["actions"][1]["lines"][0])
        self.assertIn({"type": "setEvent", "event": "EVENT_RESCUED_MR_FUJI"}, candidate["actions"])
        self.assertIn({"type": "setEvent", "event": "EVENT_RESCUED_MR_FUJI_2"}, candidate["actions"])
        self.assertIn({"type": "showObject", "objectKey": "HS_MR_FUJIS_HOUSE_MR_FUJI"}, candidate["actions"])
        self.assertIn({"type": "hideObject", "objectKey": "HS_POKEMON_TOWER_7F_MR_FUJI"}, candidate["actions"])
        self.assertIn({"type": "warp", "mapId": 149, "x": 3, "y": 7, "direction": "UP"}, candidate["actions"])
        self.assertEqual(candidate["source"]["adapter"], "pokemon_tower_7f_mr_fuji_rescue_v1")
        self.assertIn("PokemonTower7FWarpToMrFujiHouseScript", candidate["source"]["coveredLabels"])


class GameCornerCoinPurchaseCandidateTest(unittest.TestCase):
    def test_clerk1_buy_coins_emits_server_gated_purchase_branches(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in game_corner_coin_purchase_candidates()
        }

        buy = candidates["GameCornerClerk1BuyCoins"]
        no_money = candidates["GameCornerClerk1InsufficientMoney"]
        full = candidates["GameCornerClerk1CoinCaseFull"]
        no_case = candidates["GameCornerClerk1NoCoinCase"]

        self.assertEqual(buy["trigger"]["label"], "TEXT_GAMECORNER_CLERK1")
        self.assertEqual(
            buy["conditions"],
            {"requiresItem": "COIN_CASE", "requiresCoinsBelow": 9990, "requiresMoney": 1000},
        )
        assert_dialogue_contains(self, buy["actions"][1]["promptLines"], "It's 1000 Pokedollars for 50")
        assert_dialogue_contains(self, buy["actions"][1]["noLines"], "No? Please come")
        self.assertEqual(buy["actions"][2], {"type": "takeMoney", "money": 1000})
        self.assertEqual(buy["actions"][3], {"type": "giveCoins", "coins": 50})
        assert_dialogue_contains(self, buy["actions"][4]["lines"], "your 50 coins!")

        self.assertEqual(
            no_money["conditions"],
            {"requiresItem": "COIN_CASE", "requiresCoinsBelow": 9990, "requiresMoneyBelow": 1000},
        )
        assert_dialogue_contains(self, no_money["actions"][2]["lines"], "You can't afford")
        self.assertEqual(full["conditions"], {"requiresItem": "COIN_CASE", "requiresCoins": 9990})
        assert_dialogue_contains(self, full["actions"][2]["lines"], "CASE is full.")
        self.assertEqual(no_case["conditions"], {"requiresItemAbsent": "COIN_CASE"})
        assert_dialogue_contains(self, no_case["actions"][2]["lines"], "COIN CASE!")


class GymLeaderBattleTextCandidateTest(unittest.TestCase):
    def test_brock_prebattle_and_post_tm_advice_emit_source_battle_branches(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in gym_leader_battle_text_candidates()
        }

        before = candidates["PewterGymBrockTextPreBattle"]
        after = candidates["PewterGymBrockTextEventGotTm34Set"]

        self.assertEqual(before["trigger"]["label"], "TEXT_PEWTERGYM_BROCK")
        self.assertEqual(before["conditions"], {"requiresEventAbsent": "EVENT_BEAT_BROCK"})
        self.assertEqual(
            before["actions"][1]["lines"],
            [
                "I'm BROCK!\nI'm PEWTER's GYM LEADER!",
                "I believe in rock\nhard defense and determination!",
                "That's why my\nPOKEMON are all the rock-type!",
                "Do you still want\nto challenge me? Fine then! Show me your best!",
            ],
        )
        self.assertEqual(before["actions"][2]["type"], "startTrainerBattle")
        self.assertEqual(before["actions"][2]["trainerClass"], "BROCK")
        self.assertEqual(before["actions"][2]["partyIndex"], 1)
        self.assertEqual(before["actions"][2]["winFlag"], "EVENT_BEAT_BROCK")

        self.assertEqual(after["conditions"], {"requiresEvent": "EVENT_GOT_TM34"})
        self.assertEqual(
            after["actions"][1]["lines"][0],
            "There are all\nkinds of trainers in the world!",
        )

    def test_cinnabar_helper_jump_still_emits_blaine_battle_branch(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in gym_leader_battle_text_candidates()
        }

        before = candidates["CinnabarGymBlaineTextPreBattle"]

        self.assertEqual(before["trigger"]["label"], "TEXT_CINNABARGYM_BLAINE")
        self.assertEqual(before["conditions"], {"requiresEventAbsent": "EVENT_BEAT_BLAINE"})
        assert_dialogue_contains(self, before["actions"][1]["lines"], "Hah!")
        self.assertEqual(before["actions"][2]["trainerClass"], "BLAINE")
        self.assertEqual(before["actions"][2]["partyIndex"], 1)
        self.assertEqual(before["actions"][2]["winFlag"], "EVENT_BEAT_BLAINE")

    def test_vermilion_snake_case_branch_labels_emit_surge_branches(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in gym_leader_battle_text_candidates()
        }

        before = candidates["VermilionGymLTSurgeTextPreBattle"]
        after = candidates["VermilionGymLTSurgeTextEventGotTm24Set"]

        self.assertEqual(before["trigger"]["label"], "TEXT_VERMILIONGYM_LT_SURGE")
        self.assertEqual(before["conditions"], {"requiresEventAbsent": "EVENT_BEAT_LT_SURGE"})
        self.assertEqual(
            before["actions"][1]["lines"][0],
            "Hey, kid! What do\nyou think you're doing here?",
        )
        self.assertEqual(before["actions"][2]["trainerClass"], "LT_SURGE")
        self.assertEqual(before["actions"][2]["partyIndex"], 1)
        self.assertEqual(before["actions"][2]["winFlag"], "EVENT_BEAT_LT_SURGE")

        self.assertEqual(after["conditions"], {"requiresEvent": "EVENT_GOT_TM24"})
        self.assertEqual(after["actions"][1]["lines"][0], "A little word of\nadvice, kid!")


class CinnabarGymTrainerTextCandidateTest(unittest.TestCase):
    def test_map_load_reset_candidate_covers_temporary_event_reset(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in cinnabar_gym_map_load_reset_candidate()
        }

        reset = candidates["CinnabarGymMapLoadReset"]
        self.assertEqual(reset["trigger"]["type"], "map_script")
        self.assertEqual(reset["trigger"]["label"], "CinnabarGymSetMapAndTiles")
        self.assertEqual(reset["conditions"], {})
        self.assertEqual(reset["actions"], [{"type": "resetEvent", "event": "EVENT_2A7"}])
        self.assertEqual(reset["source"]["adapter"], "cinnabar_gym_map_load_reset_v1")
        self.assertEqual(reset["source"]["coveredLabels"], ["CinnabarGymSetMapAndTiles"])

    def test_custom_quiz_trainer_text_emits_battle_and_after_battle_branches(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in cinnabar_gym_trainer_text_candidates()
        }

        battle = candidates["CinnabarGymSuperNerd4Battle"]
        after = candidates["CinnabarGymSuperNerd4AfterBattle"]

        self.assertEqual(battle["trigger"]["label"], "TEXT_CINNABARGYM_SUPER_NERD4")
        self.assertEqual(
            battle["conditions"],
            {"requiresEventAbsent": "EVENT_BEAT_CINNABAR_GYM_TRAINER_3"},
        )
        assert_dialogue_contains(self, battle["actions"][1]["lines"], "I just like using")
        self.assertEqual(battle["actions"][2]["type"], "startTrainerBattle")
        self.assertEqual(battle["actions"][2]["trainerClass"], "BURGLAR")
        self.assertEqual(battle["actions"][2]["partyIndex"], 5)
        self.assertEqual(battle["actions"][2]["winFlag"], "EVENT_BEAT_CINNABAR_GYM_TRAINER_3")
        self.assertEqual(
            battle["actions"][2]["postWinActions"][-1],
            {"type": "setEvent", "event": "EVENT_CINNABAR_GYM_GATE3_UNLOCKED"},
        )

        self.assertEqual(after["conditions"], {"requiresEvent": "EVENT_BEAT_CINNABAR_GYM_TRAINER_3"})
        assert_dialogue_contains(self, after["actions"][1]["lines"], "I wish there was")


class IndigoPlateauLobbyMapLoadResetCandidateTest(unittest.TestCase):
    def test_map_load_reset_candidate_clears_source_elite_four_range(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in indigo_plateau_lobby_map_load_reset_candidate()
        }

        reset = candidates["IndigoPlateauLobbyMapLoadReset"]
        self.assertEqual(reset["trigger"], {
            "type": "map_script",
            "label": "IndigoPlateauLobby_Script",
            "sourceLabel": "IndigoPlateauLobby_Script",
        })
        self.assertEqual(reset["conditions"], {})
        reset_events = [action["event"] for action in reset["actions"]]
        self.assertEqual(reset_events[0], "EVENT_VICTORY_ROAD_1_BOULDER_ON_SWITCH")
        self.assertIn("EVENT_BEAT_LORELEIS_ROOM_TRAINER_0", reset_events)
        self.assertIn("EVENT_AUTOWALKED_INTO_LORELEIS_ROOM", reset_events)
        self.assertIn("EVENT_BEAT_LANCE", reset_events)
        self.assertEqual(reset_events[-1], "EVENT_LANCES_ROOM_LOCK_DOOR")
        self.assertNotIn("EVENT_BEAT_CHAMPION_RIVAL", reset_events)
        self.assertEqual(reset["source"]["adapter"], "indigo_plateau_lobby_map_load_reset_v1")
        self.assertEqual(reset["source"]["coveredLabels"], ["IndigoPlateauLobby_Script"])


class CeruleanCityRivalCandidateTest(unittest.TestCase):
    def test_bridge_rival_candidate_emits_battle_and_cleanup(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in cerulean_city_rival_candidates()
        }

        rival = candidates["CeruleanCityRivalEncounter"]
        self.assertEqual(rival["trigger"]["type"], "coord")
        self.assertEqual(rival["trigger"]["label"], "CeruleanCityCoords2")
        self.assertEqual(rival["trigger"]["coordinates"], [{"x": 20, "y": 6}, {"x": 21, "y": 6}])
        self.assertEqual(rival["conditions"], {"requiresEventAbsent": "EVENT_BEAT_CERULEAN_RIVAL"})
        self.assertEqual(rival["actions"][1], {"type": "showActor", "actor": "RIVAL", "x": 20, "y": 4})
        self.assertEqual(rival["actions"][3], {"type": "facePlayer", "actor": "RIVAL", "direction": "DOWN"})
        assert_dialogue_contains(self, rival["actions"][4]["lines"], "<RIVAL>: Yo!")
        assert_dialogue_contains(self, rival["actions"][4]["lines"], "what you caught,")

        battle = rival["actions"][5]
        self.assertEqual(battle["type"], "startTrainerBattle")
        self.assertEqual(battle["trainerClass"], "RIVAL1")
        self.assertEqual(
            battle["partyByFlag"],
            {
                "EVENT_PLAYER_CHOSE_SQUIRTLE": 8,
                "EVENT_PLAYER_CHOSE_BULBASAUR": 9,
                "EVENT_PLAYER_CHOSE_CHARMANDER": 7,
            },
        )
        self.assertEqual(battle["postWinActions"][-2], {"type": "hideObject", "textConstant": "TEXT_CERULEANCITY_RIVAL"})
        self.assertEqual(battle["postWinActions"][-1], {"type": "setEvent", "event": "EVENT_CERULEAN_RIVAL_LEFT"})
        self.assertEqual(rival["source"]["adapter"], "cerulean_city_rival_v1")
        self.assertIn("CeruleanCityRivalCleanupScript", rival["source"]["coveredLabels"])


class Route22RivalCandidateTest(unittest.TestCase):
    def test_route22_rival_candidates_emit_first_and_second_battles(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in route22_rival_candidates()
        }

        rival1 = candidates["Route22Rival1Encounter"]
        self.assertEqual(rival1["trigger"]["type"], "coord")
        self.assertEqual(rival1["trigger"]["coordinates"], [{"x": 29, "y": 4}, {"x": 29, "y": 5}])
        self.assertEqual(
            rival1["conditions"],
            {
                "requiresEvents": ["EVENT_1ST_ROUTE22_RIVAL_BATTLE", "EVENT_ROUTE22_RIVAL_WANTS_BATTLE"],
                "requiresEventAbsent": "EVENT_BEAT_ROUTE22_RIVAL_1ST_BATTLE",
            },
        )
        assert_dialogue_contains(self, rival1["actions"][4]["lines"], "<RIVAL>: Hey!")
        battle1 = rival1["actions"][5]
        self.assertEqual(battle1["trainerClass"], "RIVAL1")
        self.assertEqual(battle1["winFlag"], "EVENT_BEAT_ROUTE22_RIVAL_1ST_BATTLE")
        self.assertEqual(
            battle1["partyByFlag"],
            {
                "EVENT_PLAYER_CHOSE_SQUIRTLE": 5,
                "EVENT_PLAYER_CHOSE_BULBASAUR": 6,
                "EVENT_PLAYER_CHOSE_CHARMANDER": 4,
            },
        )
        self.assertEqual(battle1["postWinActions"][-2], {"type": "resetEvent", "event": "EVENT_1ST_ROUTE22_RIVAL_BATTLE"})

        rival2 = candidates["Route22Rival2Encounter"]
        self.assertEqual(
            rival2["conditions"],
            {
                "requiresEvents": ["EVENT_2ND_ROUTE22_RIVAL_BATTLE", "EVENT_ROUTE22_RIVAL_WANTS_BATTLE"],
                "requiresEventAbsent": "EVENT_BEAT_ROUTE22_RIVAL_2ND_BATTLE",
            },
        )
        battle2 = rival2["actions"][5]
        self.assertEqual(battle2["trainerClass"], "RIVAL2")
        self.assertEqual(battle2["winFlag"], "EVENT_BEAT_ROUTE22_RIVAL_2ND_BATTLE")
        self.assertEqual(battle2["postWinActions"][-2], {"type": "resetEvent", "event": "EVENT_2ND_ROUTE22_RIVAL_BATTLE"})
        self.assertEqual(rival2["source"]["adapter"], "route22_rival_v1")
        self.assertIn("Route22Rival2ExitScript", rival2["source"]["coveredLabels"])


class SilphCo7FRivalCandidateTest(unittest.TestCase):
    def test_upper_and_lower_rival_candidates_preserve_movement_branches(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in silph_co_7f_rival_candidates()
        }

        upper = candidates["SilphCo7FRivalEncounter"]
        lower = candidates["SilphCo7FRivalEncounterLower"]

        self.assertEqual(upper["trigger"]["coordinates"], [{"x": 3, "y": 2}])
        self.assertEqual(lower["trigger"]["coordinates"], [{"x": 3, "y": 3}])
        self.assertEqual(upper["conditions"], {"requiresEventAbsent": "EVENT_BEAT_SILPH_CO_RIVAL"})
        assert_dialogue_contains(self, upper["actions"][1]["lines"], "<RIVAL>: What")
        self.assertEqual(upper["actions"][2], {"type": "move", "actor": "RIVAL", "movements": ["UP", "UP", "UP"]})
        self.assertEqual(lower["actions"][2], {"type": "move", "actor": "RIVAL", "movements": ["UP", "UP", "UP", "UP"]})

        battle = upper["actions"][4]
        self.assertEqual(battle["type"], "startTrainerBattle")
        self.assertEqual(battle["trainerClass"], "RIVAL2")
        self.assertEqual(
            battle["partyByFlag"],
            {
                "EVENT_PLAYER_CHOSE_SQUIRTLE": 8,
                "EVENT_PLAYER_CHOSE_BULBASAUR": 9,
                "EVENT_PLAYER_CHOSE_CHARMANDER": 7,
            },
        )
        self.assertEqual(
            battle["postWinActions"][1],
            {"type": "move", "actor": "RIVAL", "movements": ["LEFT", "UP", "UP", "RIGHT", "RIGHT", "RIGHT", "DOWN"]},
        )
        self.assertEqual(
            lower["actions"][4]["postWinActions"][1],
            {"type": "move", "actor": "RIVAL", "movements": ["RIGHT", "RIGHT"]},
        )
        self.assertEqual(upper["source"]["adapter"], "silph_co_7f_rival_v1")
        self.assertIn("SilphCo7FRivalExitScript", upper["source"]["coveredLabels"])


class PokemonTower2FRivalCandidateTest(unittest.TestCase):
    def test_tower_rival_candidates_preserve_source_coordinate_branches(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in pokemon_tower_2f_rival_candidates()
        }

        self.assertEqual(set(candidates), {"PokemonTower2FRivalEncounter", "PokemonTower2FRivalEncounterBelow"})
        right = candidates["PokemonTower2FRivalEncounter"]
        below = candidates["PokemonTower2FRivalEncounterBelow"]

        self.assertEqual(
            right["trigger"]["coordinates"],
            [{"mapName": "PokemonTower2F", "mapId": 143, "x": 15, "y": 5}],
        )
        self.assertEqual(
            below["trigger"]["coordinates"],
            [{"mapName": "PokemonTower2F", "mapId": 143, "x": 14, "y": 6}],
        )
        self.assertEqual(right["actions"][1], {"type": "facePlayer", "actor": "RIVAL", "direction": "RIGHT"})
        self.assertEqual(below["actions"][1], {"type": "facePlayer", "actor": "RIVAL", "direction": "DOWN"})
        self.assertIn("Hey,", right["actions"][2]["lines"][0])

        battle = right["actions"][3]
        self.assertEqual(battle["type"], "startTrainerBattle")
        self.assertEqual(battle["trainerClass"], "RIVAL2")
        self.assertEqual(
            battle["partyByFlag"],
            {
                "EVENT_PLAYER_CHOSE_SQUIRTLE": 5,
                "EVENT_PLAYER_CHOSE_BULBASAUR": 6,
                "EVENT_PLAYER_CHOSE_CHARMANDER": 4,
            },
        )
        self.assertEqual(
            battle["postWinActions"][1],
            {"type": "move", "actor": "RIVAL", "movements": ["DOWN", "DOWN", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "DOWN", "DOWN"]},
        )
        self.assertEqual(
            below["actions"][3]["postWinActions"][1],
            {"type": "move", "actor": "RIVAL", "movements": ["RIGHT", "DOWN", "DOWN", "RIGHT", "DOWN", "DOWN", "RIGHT", "RIGHT"]},
        )
        self.assertEqual(battle["postWinActions"][2], {"type": "hideObject", "objectKey": "HS_POKEMON_TOWER_2F_RIVAL"})
        self.assertEqual(right["source"]["adapter"], "pokemon_tower_2f_rival_v1")
        self.assertIn("PokemonTower2FDefeatedRivalScript", right["source"]["coveredLabels"])
        self.assertIn("PokemonTower2FRivalExitsScript", right["source"]["coveredLabels"])


class PokemonTower5FPurifiedZoneCandidateTest(unittest.TestCase):
    def test_purified_zone_candidate_uses_source_coords_and_heal_text(self):
        candidates = pokemon_tower_5f_purified_zone_candidate()

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["scriptLabel"], "PokemonTower5FPurifiedZone")
        self.assertEqual(candidate["trigger"]["label"], "PokemonTower5FPurifiedZoneCoords")
        self.assertEqual(
            candidate["trigger"]["coordinates"],
            [
                {"mapName": "PokemonTower5F", "mapId": 146, "x": 10, "y": 8},
                {"mapName": "PokemonTower5F", "mapId": 146, "x": 11, "y": 8},
                {"mapName": "PokemonTower5F", "mapId": 146, "x": 10, "y": 9},
                {"mapName": "PokemonTower5F", "mapId": 146, "x": 11, "y": 9},
            ],
        )
        self.assertEqual(candidate["conditions"], {"requiresEventAbsent": "EVENT_IN_PURIFIED_ZONE"})
        self.assertEqual(candidate["actions"][1], {"type": "setEvent", "event": "EVENT_IN_PURIFIED_ZONE"})
        self.assertEqual(candidate["actions"][2], {"type": "healParty"})
        assert_dialogue_contains(self, candidate["actions"][3]["lines"], "Entered purified,")
        assert_dialogue_contains(self, candidate["actions"][3]["lines"], "are fully healed!")
        self.assertEqual(candidate["source"]["adapter"], "pokemon_tower_5f_purified_zone_v1")
        self.assertIn("PokemonTower5FDefaultScript", candidate["source"]["coveredLabels"])


class TrainerAfterBattleObjectDropCandidateTest(unittest.TestCase):
    def test_rocket_hideout_lift_key_drop_emits_reveal_and_repeat_branches(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in trainer_after_battle_object_drop_candidates()
        }

        drop = candidates["RocketHideoutB4FRocket3AfterBattleTextObjectDrop"]
        repeat = candidates["RocketHideoutB4FRocket3AfterBattleTextAfterObjectDrop"]

        self.assertEqual(drop["trigger"]["label"], "TEXT_ROCKETHIDEOUTB4F_ROCKET3")
        self.assertEqual(
            drop["conditions"],
            {
                "requiresEvent": "EVENT_BEAT_ROCKET_HIDEOUT_4_TRAINER_2",
                "requiresEventAbsent": "EVENT_ROCKET_DROPPED_LIFT_KEY",
            },
        )
        assert_dialogue_contains(self, drop["actions"][1]["lines"], "Oh no! I dropped")
        self.assertEqual(drop["actions"][2], {"type": "setEvent", "event": "EVENT_ROCKET_DROPPED_LIFT_KEY"})
        self.assertEqual(
            drop["actions"][3],
            {"type": "showObject", "objectKey": "HS_ROCKET_HIDEOUT_B4F_ITEM_5"},
        )
        self.assertEqual(drop["source"]["adapter"], "trainer_after_battle_object_drop_v1")
        self.assertIn("RocketHideoutB4FRocket3AfterBattleText", drop["source"]["coveredLabels"])

        self.assertEqual(repeat["conditions"], {"requiresEvent": "EVENT_ROCKET_DROPPED_LIFT_KEY"})
        assert_dialogue_contains(self, repeat["actions"][1]["lines"], "the LIFT KEY!")


class RocketHideoutB4FGiovanniCandidateTest(unittest.TestCase):
    def test_giovanni_npc_battle_and_silph_scope_reveal_are_generated_from_source(self):
        candidates = rocket_hideout_b4f_giovanni_candidate()

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["scriptLabel"], "RocketHideoutB4FGiovanniEncounter")
        self.assertEqual(candidate["trigger"], {
            "type": "npc_click",
            "label": "TEXT_ROCKETHIDEOUTB4F_GIOVANNI",
            "sourceLabel": "RocketHideoutB4FGiovanniText",
        })
        self.assertEqual(candidate["conditions"], {"requiresEventAbsent": "EVENT_BEAT_ROCKET_HIDEOUT_GIOVANNI"})
        self.assertIn("So! I must say", candidate["actions"][1]["lines"][0])

        battle = candidate["actions"][2]
        self.assertEqual(battle["type"], "startTrainerBattle")
        self.assertEqual(battle["trainerClass"], "GIOVANNI")
        self.assertEqual(battle["partyIndex"], 1)
        self.assertEqual(battle["winFlag"], "EVENT_BEAT_ROCKET_HIDEOUT_GIOVANNI")
        self.assertIn("WHAT!", battle["postWinActions"][0]["lines"][0])
        assert_dialogue_contains(self, battle["postWinActions"][0]["lines"], "I hope we meet")
        self.assertEqual(battle["postWinActions"][1], {"type": "hideActor", "actor": "GIOVANNI"})
        self.assertEqual(
            battle["postWinActions"][2],
            {"type": "setEvent", "event": "EVENT_ROCKET_HIDEOUT_GIOVANNI_LEFT"},
        )
        self.assertEqual(
            battle["postWinActions"][3],
            {"type": "hideObject", "objectKey": "HS_ROCKET_HIDEOUT_B4F_GIOVANNI"},
        )
        self.assertEqual(
            battle["postWinActions"][4],
            {"type": "showObject", "objectKey": "HS_ROCKET_HIDEOUT_B4F_ITEM_4"},
        )
        self.assertEqual(candidate["source"]["adapter"], "rocket_hideout_b4f_giovanni_v1")
        self.assertIn("RocketHideoutB4FGiovanniText", candidate["source"]["coveredLabels"])
        self.assertIn("RocketHideoutB4FBeatGiovanniScript", candidate["source"]["coveredLabels"])


class TrainerAfterBattleFlagSideEffectCandidateTest(unittest.TestCase):
    def test_lance_after_battle_text_sets_progression_flag(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in trainer_after_battle_flag_side_effect_candidates()
        }

        candidate = candidates["LancesRoomLanceAfterBattleTextEventBeatLanceSet"]

        self.assertEqual(candidate["mapName"], "LancesRoom")
        self.assertEqual(candidate["trigger"]["type"], "map_script")
        self.assertEqual(candidate["trigger"]["label"], "LancesRoomLanceAfterBattleText")
        self.assertEqual(
            candidate["conditions"],
            {
                "requiresEvent": "EVENT_BEAT_LANCES_ROOM_TRAINER_0",
                "requiresEventAbsent": "EVENT_BEAT_LANCE",
            },
        )
        assert_dialogue_contains(self, candidate["actions"][1]["lines"], "That's it!")
        assert_dialogue_contains(self, candidate["actions"][2]["lines"], "I still can't")
        self.assertEqual(candidate["actions"][3], {"type": "setEvent", "event": "EVENT_BEAT_LANCE"})
        self.assertEqual(candidate["source"]["adapter"], "trainer_after_battle_flag_side_effect_v1")
        self.assertIn("LancesRoomTrainerHeader0", candidate["source"]["coveredLabels"])
        self.assertIn("LancesRoomLanceEndBattleText", candidate["source"]["coveredLabels"])
        self.assertIn("LancesRoomLanceAfterBattleText", candidate["source"]["coveredLabels"])

    def test_same_flag_end_battle_text_is_runtime_covered(self):
        generated = {
            candidate["scriptLabel"]
            for candidate in trainer_after_battle_flag_side_effect_candidates()
        }
        self.assertNotIn("RocketHideoutB1FRocket5EndBattleTextEventBeatRocketHideout1Trainer4Set", generated)

        diagnostics = {
            diagnostic["scriptLabel"]: diagnostic
            for diagnostic in trainer_after_battle_flag_runtime_diagnostics()
        }
        rocket = diagnostics["RocketHideoutB1FRocket5EndBattleText"]

        self.assertEqual(rocket["status"], "covered")
        self.assertEqual(rocket["reason"], "trainer_after_battle_flag_runtime_v1")
        self.assertEqual(rocket["details"]["source"]["trainerHeader"], "RocketHideout1TrainerHeader4")


class RocketHideoutDoorCandidateTest(unittest.TestCase):
    def test_door_unlock_map_scripts_sync_source_flags(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in rocket_hideout_door_unlock_candidates()
        }

        b1f = candidates["RocketHideoutB1FDoorUnlock"]
        self.assertEqual(b1f["mapName"], "RocketHideoutB1F")
        self.assertEqual(b1f["trigger"], {"type": "map_script", "label": "RocketHideoutB1FDoorCallbackScript"})
        self.assertEqual(
            b1f["conditions"],
            {
                "requiresEvents": ["EVENT_BEAT_ROCKET_HIDEOUT_1_TRAINER_4"],
                "requiresEventAbsent": "EVENT_677",
            },
        )
        self.assertEqual(b1f["actions"], [{"type": "setEvent", "event": "EVENT_677"}])
        self.assertIn("RocketHideoutB1FDoorCallbackScript", b1f["source"]["coveredLabels"])

        b4f = candidates["RocketHideoutB4FDoorUnlock"]
        self.assertEqual(
            b4f["conditions"],
            {
                "requiresEvents": ["EVENT_BEAT_ROCKET_HIDEOUT_4_TRAINER_0", "EVENT_BEAT_ROCKET_HIDEOUT_4_TRAINER_1"],
                "requiresEventAbsent": "EVENT_ROCKET_HIDEOUT_4_DOOR_UNLOCKED",
            },
        )
        self.assertEqual(b4f["actions"], [{"type": "setEvent", "event": "EVENT_ROCKET_HIDEOUT_4_DOOR_UNLOCKED"}])

    def test_door_tile_overrides_use_source_block_coordinates(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in rocket_hideout_door_tile_override_candidates()
        }

        self.assertEqual(
            candidates["RocketHideoutB1FDoorTile"]["replacements"],
            [
                {
                    "blockX": 12,
                    "blockY": 8,
                    "blockId": 0x54,
                    "requiresEventAbsent": "EVENT_677",
                    "labelPrefix": "RocketHideoutB1FDoorClosed",
                },
                {
                    "blockX": 12,
                    "blockY": 8,
                    "blockId": 0x0E,
                    "requiresEvent": "EVENT_677",
                    "labelPrefix": "RocketHideoutB1FDoorOpen",
                },
            ],
        )
        self.assertEqual(
            candidates["RocketHideoutB4FDoorTile"]["replacements"],
            [
                {
                    "blockX": 12,
                    "blockY": 5,
                    "blockId": 0x2D,
                    "requiresEventAbsent": "EVENT_ROCKET_HIDEOUT_4_DOOR_UNLOCKED",
                    "labelPrefix": "RocketHideoutB4FDoorClosed",
                },
                {
                    "blockX": 12,
                    "blockY": 5,
                    "blockId": 0x0E,
                    "requiresEvent": "EVENT_ROCKET_HIDEOUT_4_DOOR_UNLOCKED",
                    "labelPrefix": "RocketHideoutB4FDoorOpen",
                },
            ],
        )


class BillsHouseCellSeparatorCandidateTest(unittest.TestCase):
    def test_bill_pokemon_choice_continues_after_no(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in bills_house_cell_separator_candidates()
        }

        walk = candidates["BillsHousePokemonWalkToMachine"]

        self.assertEqual(walk["trigger"]["label"], "TEXT_BILLSHOUSE_BILL_POKEMON")
        self.assertEqual(
            walk["conditions"],
            {"requiresEventAbsent": "EVENT_BILL_SAID_USE_CELL_SEPARATOR"},
        )
        self.assertEqual(walk["actions"][1]["type"], "choice")
        self.assertTrue(walk["actions"][1]["continueOnNo"])
        self.assertIn("No!?", walk["actions"][1]["noLines"][0])
        self.assertIn("When I'm in the", walk["actions"][2]["lines"][0])
        self.assertEqual(
            walk["actions"][-3:],
            [
                {"type": "hideObject", "objectKey": "HS_BILL_POKEMON"},
                {"type": "setEvent", "event": "EVENT_BILL_SAID_USE_CELL_SEPARATOR"},
                {"type": "unlockInput"},
            ],
        )

    def test_bill_exit_machine_sets_source_cleanup_flags(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in bills_house_cell_separator_candidates()
        }

        exit_machine = candidates["BillsHouseBillExitsMachine"]

        self.assertEqual(exit_machine["trigger"]["type"], "map_script")
        self.assertEqual(
            exit_machine["conditions"],
            {
                "requiresEvent": "EVENT_USED_CELL_SEPARATOR",
                "requiresEventAbsent": "EVENT_MET_BILL",
            },
        )
        self.assertEqual(exit_machine["actions"][1], {"type": "showActor", "actor": "BILL_1", "x": 5, "y": 6})
        self.assertEqual(exit_machine["actions"][2], {"type": "showObject", "objectKey": "HS_BILL_1"})
        self.assertEqual(exit_machine["actions"][-3], {"type": "setEvent", "event": "EVENT_MET_BILL_2"})
        self.assertEqual(exit_machine["actions"][-2], {"type": "setEvent", "event": "EVENT_MET_BILL"})
        self.assertEqual(exit_machine["source"]["adapter"], "bills_house_cell_separator_v1")
        self.assertIn("BillsHouseCleanupScript", exit_machine["source"]["coveredLabels"])


class Route25BillVisibilityCandidateTest(unittest.TestCase):
    def test_generates_source_visibility_sync_branches(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in route25_bill_visibility_candidates()
        }

        self.assertEqual(
            set(candidates),
            {
                "Route25BillPokemonVisibleBeforeHelp",
                "Route25BillReturnedOutsideAfterSSTicket",
            },
        )

        before_help = candidates["Route25BillPokemonVisibleBeforeHelp"]
        self.assertEqual(before_help["trigger"]["type"], "map_script")
        self.assertEqual(before_help["trigger"]["label"], "Route25ShowHideBillScript")
        self.assertEqual(
            before_help["conditions"],
            {
                "requiresEventAbsent": "EVENT_LEFT_BILLS_HOUSE_AFTER_HELPING",
                "requiresEventsAbsent": ["EVENT_MET_BILL_2"],
            },
        )
        self.assertEqual(
            before_help["actions"],
            [
                {"type": "resetEvent", "event": "EVENT_BILL_SAID_USE_CELL_SEPARATOR"},
                {"type": "showObject", "objectKey": "HS_BILL_POKEMON"},
            ],
        )

        after_ticket = candidates["Route25BillReturnedOutsideAfterSSTicket"]
        self.assertEqual(
            after_ticket["conditions"],
            {
                "requiresEvents": ["EVENT_MET_BILL_2", "EVENT_GOT_SS_TICKET"],
                "requiresEventAbsent": "EVENT_LEFT_BILLS_HOUSE_AFTER_HELPING",
            },
        )
        self.assertEqual(
            after_ticket["actions"],
            [
                {"type": "setEvent", "event": "EVENT_LEFT_BILLS_HOUSE_AFTER_HELPING"},
                {"type": "hideObject", "objectKey": "HS_NUGGET_BRIDGE_GUY"},
                {"type": "hideObject", "objectKey": "HS_BILL_1"},
                {"type": "showObject", "objectKey": "HS_BILL_2"},
            ],
        )
        self.assertEqual(after_ticket["source"]["adapter"], "route25_bill_visibility_v1")
        self.assertEqual(after_ticket["source"]["coveredLabels"], ["Route25ShowHideBillScript"])


class RocketRewardBattleCandidateTest(unittest.TestCase):
    def test_cerulean_rocket_covers_hide_helper_label(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in rocket_reward_battle_candidates()
        }

        cerulean = candidates["CeruleanCityRocketBattleTM28"]
        battle = next(action for action in cerulean["actions"] if action["type"] == "startTrainerBattle")

        self.assertIn("CeruleanHideRocket", cerulean["source"]["coveredLabels"])
        self.assertEqual(
            battle["postWinActions"][-3:],
            [
                {"type": "showObject", "objectKey": "HS_CERULEAN_GUARD_1"},
                {"type": "hideObject", "objectKey": "HS_CERULEAN_GUARD_2"},
                {"type": "hideObject", "objectKey": "HS_CERULEAN_ROCKET"},
            ],
        )


class MtMoonFossilChoiceCandidateTest(unittest.TestCase):
    def test_super_nerd_prompt_branch_is_generated_from_source(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in mt_moon_fossil_choice_candidates()
        }

        prompt = candidates["MtMoonB2FFossilChoice"]

        self.assertEqual(prompt["trigger"]["type"], "map_script")
        self.assertEqual(prompt["trigger"]["label"], "MtMoonB2FDefaultScript")
        self.assertEqual(
            prompt["conditions"],
            {
                "requiresEvent": "EVENT_BEAT_MT_MOON_EXIT_SUPER_NERD",
                "requiresEventAbsent": "EVENT_GOT_MT_MOON_FOSSIL",
            },
        )
        self.assertEqual(prompt["actions"][1]["speaker"], "SUPER NERD")
        assert_dialogue_contains(self, prompt["actions"][1]["lines"], "OK!")
        self.assertEqual(prompt["source"]["adapter"], "mt_moon_fossil_choice_v1")
        self.assertIn("MtMoonB2FDefeatedSuperNerdScript", prompt["source"]["coveredLabels"])

    def test_fossil_choice_covers_collapsed_super_nerd_movement(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in mt_moon_fossil_choice_candidates()
        }

        dome = candidates["MtMoonB2FDomeFossilChoice"]
        helix = candidates["MtMoonB2FHelixFossilChoice"]

        self.assertIn("MtMoonB2FMoveSuperNerdScript", dome["source"]["coveredLabels"])
        self.assertIn("MtMoonB2FMoveSuperNerdScript", helix["source"]["coveredLabels"])


class SilphCo9FNurseCandidateTest(unittest.TestCase):
    def test_nurse_emits_heal_and_post_giovanni_branches(self):
        candidates = {
            candidate["scriptLabel"]: candidate
            for candidate in silph_co_9f_nurse_candidates()
        }

        heal = candidates["SilphCo9FNurseHeal"]
        thanks = candidates["SilphCo9FNurseThankYou"]

        self.assertEqual(heal["trigger"]["label"], "TEXT_SILPHCO9F_NURSE")
        self.assertEqual(
            heal["conditions"],
            {"requiresEventAbsent": "EVENT_BEAT_SILPH_CO_GIOVANNI"},
        )
        assert_dialogue_contains(self, heal["actions"][1]["lines"], "You look tired!")
        self.assertEqual(heal["actions"][2], {"type": "healParty"})
        assert_dialogue_contains(self, heal["actions"][3]["lines"], "Don't give up!")

        self.assertEqual(thanks["conditions"], {"requiresEvent": "EVENT_BEAT_SILPH_CO_GIOVANNI"})
        assert_dialogue_contains(self, thanks["actions"][1]["lines"], "Thank you so")


if __name__ == "__main__":
    unittest.main()
