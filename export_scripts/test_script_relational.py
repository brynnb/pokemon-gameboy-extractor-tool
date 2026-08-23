import json
from pathlib import Path
import sqlite3
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_script_candidates import (
    create_tables,
    insert_candidate,
    insert_ir_block,
    validate_normalized_script_tables,
)
from map_references import CanonicalMapResolver


class ScriptRelationalProjectionTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute(
            "CREATE TABLE maps (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE)"
        )
        self.conn.execute("INSERT INTO maps VALUES (0, 'PALLET_TOWN')")
        self.cursor = create_tables(self.conn)
        self.map_resolver = CanonicalMapResolver(
            {"PalletTown": 0, "PALLET_TOWN": 0},
            {"pallettown": 0, "pallet_town": 0},
        )

    def tearDown(self):
        self.conn.close()

    def test_actions_conditions_and_references_are_lossless_relations(self):
        candidate = {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": "PalletTown",
            "scriptLabel": "StarterGift",
            "trigger": {
                "type": "npc_click",
                "label": "StarterBall",
                "sourceLabel": "PalletTownStarterText",
            },
            "conditions": {
                "requiresEventsAbsent": ["EVENT_GOT_STARTER"],
                "minimumBadges": 0,
            },
            "actions": [
                {"type": "givePokemon", "species": "BULBASAUR"},
                {"type": "setEvent", "event": "EVENT_GOT_STARTER"},
            ],
            "source": {"scriptPath": "pokemon-game-data/scripts/PalletTown.asm"},
            "confidence": "exact",
        }
        insert_candidate(self.cursor, candidate, self.map_resolver)

        ir_block = {
            "mapName": "PalletTown",
            "label": "PalletTownStarterText",
            "kind": "text",
            "features": [],
            "textRefs": ["StarterPromptText"],
            "eventRefs": ["EVENT_GOT_STARTER"],
            "itemRefs": [],
            "pokemonRefs": ["BULBASAUR"],
            "movementRefs": [],
            "objectRefs": ["HS_STARTER_BALL"],
            "battleRefs": [],
            "warpRefs": [],
            "rawAsm": "ret",
        }
        insert_ir_block(self.cursor, ir_block, self.map_resolver)

        result = validate_normalized_script_tables(self.conn)
        self.assertEqual(result["actions"], 2)
        self.assertEqual(result["conditions"], 2)
        self.assertGreaterEqual(result["candidateReferences"], 4)
        self.assertEqual(result["irReferences"], 4)

        action_json = self.conn.execute(
            """
            SELECT action_json FROM script_event_candidate_actions
            WHERE candidate_id = 1 AND action_index = 0
            """
        ).fetchone()[0]
        self.assertEqual(json.loads(action_json), candidate["actions"][0])

    def test_validator_rejects_projection_drift(self):
        candidate = {
            "mapName": "PalletTown",
            "scriptLabel": "Example",
            "trigger": {"type": "map_load", "label": "Example"},
            "conditions": {},
            "actions": [{"type": "setEvent", "event": "EVENT_EXAMPLE"}],
            "source": {},
            "confidence": "exact",
        }
        insert_candidate(self.cursor, candidate, self.map_resolver)
        self.conn.execute(
            "DELETE FROM script_event_candidate_actions WHERE candidate_id = 1"
        )
        with self.assertRaisesRegex(ValueError, "action coverage mismatch"):
            validate_normalized_script_tables(self.conn)


if __name__ == "__main__":
    unittest.main()
