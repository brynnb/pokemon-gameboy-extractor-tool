import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_text import parse_dialogue_string


class DialogueTextTokenTest(unittest.TestCase):
    def test_parse_dialogue_string_normalizes_poke_tokens(self):
        lines = [
            '\ttext "A #MON is asleep!"\n',
            '\tline "Use the POKé FLUTE and #DEX."\n',
            '\tcont "Try a # BALL."\n',
            "\ttext_end\n",
        ]

        dialogue, _ = parse_dialogue_string(lines, 0)

        self.assertEqual(
            dialogue,
            "A POKÉMON is asleep!\nUse the POKÉ FLUTE and POKÉDEX. Try a POKÉ BALL.",
        )


if __name__ == "__main__":
    unittest.main()
