"""Opt-in compatibility profile for the CaptureQuest downstream project.

The generic extractor does not import or apply this module.  CaptureQuest can
explicitly pass :data:`PROFILE` to ``export_script_candidates.main`` while it
migrates from its historical authored-script mappings.
"""


SCRIPT_MAPPINGS = {
    ("ChampionsRoom", "ChampionsRoomPlayerEntersScript"): "ChampionsRoomRivalIntro",
    ("ChampionsRoom", "ChampionsRoomRivalText"): "ChampionsRoomRivalIntro",
    ("ChampionsRoom", "ChampionsRoomRivalDefeatedScript"): "ChampionsRoomVictory",
    ("ChampionsRoom", "ChampionsRoomOakArrivesScript"): "ChampionsRoomVictory",
    ("ChampionsRoom", "ChampionsRoomOakComeWithMeScript"): "ChampionsRoomVictory",
    ("ChampionsRoom", "ChampionsRoomOakExitsScript"): "ChampionsRoomVictory",
    ("ChampionsRoom", "ChampionsRoomPlayerFollowsOakScript"): "ChampionsRoomVictory",
    ("HallOfFame", "HallOfFameDefaultScript"): "HallOfFameOakCongratulations",
    ("HallOfFame", "HallOfFameOakCongratulationsScript"): "HallOfFameOakCongratulations",
    ("PalletTown", "PalletTownDefaultScript"): "PalletTownOakStopsPlayer",
    ("OaksLab", "OaksLab_Script"): "OaksLabChooseStarterIntro/OaksLabPokedexDelivery",
    ("OaksLab", "OaksLabRivalText"): "OaksLabRivalEntrance/OaksLabChooseStarterIntro/OaksLabRivalPicksStarter",
    ("PalletTown", "PalletTownOakHeyWaitScript"): "PalletTownOakStopsPlayer",
    ("PalletTown", "PalletTownOakWalksToPlayerScript"): "PalletTownOakStopsPlayer",
    ("OaksLab", "OaksLabDefaultScript"): "OaksLabChooseStarterIntro",
    ("OaksLab", "OaksLabOakEntersLabScript"): "OaksLabChooseStarterIntro",
    ("OaksLab", "OaksLabHideShowOaksScript"): "OaksLabChooseStarterIntro",
    ("OaksLab", "OaksLabPlayerEntersLabScript"): "OaksLabChooseStarterIntro",
    ("OaksLab", "OaksLabFollowedOakScript"): "OaksLabChooseStarterIntro",
    ("OaksLab", "OaksLabOakChooseMonSpeechScript"): "OaksLabChooseStarterIntro",
    ("OaksLab", "OaksLabMonChoiceMenu"): "OaksLabChooseBulbasaur/OaksLabChooseCharmander/OaksLabChooseSquirtle",
    ("OaksLab", "OaksLabSelectedPokeBallScript"): "OaksLabChooseBulbasaur/OaksLabChooseCharmander/OaksLabChooseSquirtle",
    ("OaksLab", "OaksLabRivalChoosesStarterScript"): "OaksLabRivalPicksStarter",
    ("OaksLab", "OaksLabRivalChallengesPlayerScript"): "OaksLabChooseBulbasaur/OaksLabChooseCharmander/OaksLabChooseSquirtle",
    ("OaksLab", "OaksLabRivalEndBattleScript"): "OaksLabChooseBulbasaur/OaksLabChooseCharmander/OaksLabChooseSquirtle",
    ("OaksLab", "OaksLabPlayerWatchRivalExitScript"): "OaksLabRivalExitsAfterBattle",
    ("OaksLab", "OaksLabRivalArrivesAtOaksRequestScript"): "OaksLabPokedexDelivery",
    ("OaksLab", "OaksLabOakGivesPokedexScript"): "OaksLabPokedexDelivery",
    ("OaksLab", "OaksLabRivalLeavesWithPokedexScript"): "OaksLabPokedexDelivery",
    ("OaksLab", "OaksLabOak1Text"): "OaksLabPokedexDelivery/OaksLabOakGivePokeballs/OaksLabOakPokemonAroundWorld",
    ("MtMoonB2F", "MtMoonB2F_Script"): "MtMoonB2FFossilChoice/MtMoonB2FDomeFossilChoice/MtMoonB2FHelixFossilChoice",
    ("PewterCity", "PewterCityDefaultScript"): "PewterCityYoungsterGymGuide",
    ("VermilionCity", "VermilionCityLeftSSAnneCallbackScript"): "VermilionCitySSAnneDeparture",
    ("VermilionDock", "VermilionDock_Script"): "VermilionCitySSAnneDeparture",
}

AUTHORED_RUNTIME_KEYS = {
    ("MtMoonB2F", "MtMoonB2F_Script"),
    ("PewterCity", "PewterCityDefaultScript"),
    ("VermilionCity", "VermilionCityLeftSSAnneCallbackScript"),
    ("VermilionDock", "VermilionDock_Script"),
}


class CaptureQuestProfile:
    name = "capturequest"

    def customize_candidates(self, candidates):
        for candidate in candidates:
            label = candidate.get("scriptLabel")
            if label == "GameCornerRocketDefeated":
                candidate.setdefault("source", {}).setdefault("movementVariants", {})["captureQuest"] = [
                    "DOWN",
                    "DOWN",
                    "DOWN",
                    "RIGHT",
                    "RIGHT",
                ]
                for action in candidate.get("actions", []):
                    if action.get("type") == "move" and action.get("actor") == "ROCKET":
                        action["movements"] = ["DOWN", "DOWN", "DOWN", "RIGHT", "RIGHT"]
                        break
            elif label == "CeruleanCityRivalEncounter":
                for action in candidate.get("actions", []):
                    if action.get("type") != "startTrainerBattle":
                        continue
                    for post_win in action.get("postWinActions", []):
                        if post_win.get("type") == "move" and post_win.get("actor") == "RIVAL":
                            post_win["movements"] = ["DOWN"] * 7
                            break
        return candidates

    def customize_diagnostics(self, diagnostics):
        for diagnostic in diagnostics:
            key = (diagnostic.get("mapName"), diagnostic.get("scriptLabel"))
            script = SCRIPT_MAPPINGS.get(key)
            if script:
                details = diagnostic.setdefault("details", {})
                details["runtimeProfile"] = {
                    "name": self.name,
                    "script": script,
                }
                details["captureQuestScript"] = script
                if key in AUTHORED_RUNTIME_KEYS:
                    diagnostic["reason"] = "capturequest_authored_runtime_v1"
        return diagnostics


PROFILE = CaptureQuestProfile()


def authored_runtime_diagnostics():
    """Compatibility entry point for consumers of the former core helper."""
    from export_script_candidates import authored_runtime_diagnostics as neutral_diagnostics
    from runtime_profiles import apply_diagnostic_profile

    return apply_diagnostic_profile(neutral_diagnostics(), PROFILE)


def capturequest_authored_runtime_diagnostics():
    """Deprecated alias retained inside the opt-in adapter module."""
    return authored_runtime_diagnostics()
