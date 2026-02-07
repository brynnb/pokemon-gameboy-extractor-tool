#!/usr/bin/env python3
"""
Extract all dialogue text from the pokered disassembly.

Parses:
  - text/*.asm files for actual dialogue strings
  - scripts/*.asm files for text pointer tables and trainer headers
  - data/maps/objects/*.asm for NPC/sign -> TEXT_ constant mappings

Creates tables:
  - dialogue_text: All dialogue strings keyed by label
  - text_pointers: Maps TEXT_ constants to dialogue labels per map
  - trainer_headers: Trainer battle/end/after text links per map
"""
import os
import re
import sqlite3
from pathlib import Path

# Constants
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "pokemon.db"
TEXT_DIR = PROJECT_ROOT / "pokemon-game-data/text"
SCRIPTS_DIR = PROJECT_ROOT / "pokemon-game-data/scripts"
OBJECTS_DIR = PROJECT_ROOT / "pokemon-game-data/data/maps/objects"
GLOBAL_TEXT_DIR = PROJECT_ROOT / "pokemon-game-data/data/text"

# Text assembly macros that contain dialogue content
TEXT_MACROS = {"text", "line", "cont", "para", "page", "next"}
# Special tokens in the original game
SPECIAL_TOKENS = {
    "<PLAYER>": "{PLAYER}",
    "<RIVAL>": "{RIVAL}",
    "#MON": "POKéMON",
    "POKé": "POKé",
    "#": "POKé",
}


def create_tables(conn):
    """Create the dialogue-related tables."""
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS dialogue_text")
    cursor.execute("DROP TABLE IF EXISTS text_pointers")
    cursor.execute("DROP TABLE IF EXISTS trainer_headers")

    # Main dialogue text table - stores every labelled text string
    cursor.execute("""
    CREATE TABLE dialogue_text (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        label TEXT NOT NULL UNIQUE,
        source_file TEXT NOT NULL,
        dialogue TEXT NOT NULL
    )
    """)

    # Maps TEXT_ constants to dialogue labels, per map
    # A single TEXT_ constant can map to multiple dialogue labels (branching)
    cursor.execute("""
    CREATE TABLE text_pointers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        map_name TEXT NOT NULL,
        text_constant TEXT NOT NULL,
        local_label TEXT NOT NULL,
        dialogue_label TEXT,
        pointer_index INTEGER DEFAULT 0,
        is_trainer INTEGER DEFAULT 0
    )
    """)

    # Trainer headers - links trainers to their battle/end/after text
    cursor.execute("""
    CREATE TABLE trainer_headers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        map_name TEXT NOT NULL,
        header_label TEXT NOT NULL,
        header_index INTEGER NOT NULL,
        event_flag TEXT,
        sight_range INTEGER,
        battle_text_label TEXT,
        end_battle_text_label TEXT,
        after_battle_text_label TEXT
    )
    """)

    conn.commit()
    return cursor


def parse_dialogue_string(lines, start_idx):
    """
    Parse a dialogue string starting from the given line index.
    Returns (dialogue_text, next_line_index).
    
    Handles: text, line, cont, para, page, next macros.
    Stops at: text_end, done, prompt, text_asm, text_far, or a new label.
    """
    parts = []
    i = start_idx

    while i < len(lines):
        raw = lines[i].strip()

        # Strip comments
        if ";" in raw:
            # Be careful not to strip semicolons inside quotes
            in_quote = False
            for ci, ch in enumerate(raw):
                if ch == '"':
                    in_quote = not in_quote
                elif ch == ';' and not in_quote:
                    raw = raw[:ci].strip()
                    break

        # Stop conditions
        if raw in ("text_end", "done", "prompt", "text_asm"):
            break
        if raw.startswith("text_far "):
            break
        # New label (not indented, ends with colon)
        if raw.endswith(":") and not raw.startswith(".") and not lines[i].startswith("\t") and not lines[i].startswith(" "):
            break

        # Parse text macros
        for macro in TEXT_MACROS:
            pattern = rf'^{macro}\s+"([^"]*)"'
            match = re.match(pattern, raw)
            if match:
                text_content = match.group(1)
                # Apply special token replacements
                for token, replacement in SPECIAL_TOKENS.items():
                    text_content = text_content.replace(token, replacement)

                if macro == "text":
                    if parts:
                        parts.append("\n")
                    parts.append(text_content)
                elif macro == "line":
                    parts.append("\n")
                    parts.append(text_content)
                elif macro == "cont":
                    parts.append(" ")
                    parts.append(text_content)
                elif macro in ("para", "page"):
                    parts.append("\n\n")
                    parts.append(text_content)
                elif macro == "next":
                    parts.append("\n")
                    parts.append(text_content)
                break

        i += 1

    dialogue = "".join(parts).strip()
    return dialogue, i


def parse_text_file(file_path):
    """
    Parse a text/*.asm file and extract all labelled dialogue strings.
    Returns dict of {label: dialogue_text}.
    """
    dialogues = {}

    with open(file_path, "r") as f:
        lines = f.readlines()

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Look for labels like _PalletTownSignText::
        label_match = re.match(r'^(_\w+)::', stripped)
        if label_match:
            label = label_match.group(1)
            i += 1
            dialogue, i = parse_dialogue_string(lines, i)
            if dialogue:
                dialogues[label] = dialogue
            continue

        i += 1

    return dialogues


def parse_global_text_files():
    """
    Parse the data/text/text_*.asm files for global dialogue strings
    (battle messages, system text, etc.)
    """
    dialogues = {}

    for text_file in sorted(GLOBAL_TEXT_DIR.glob("text_*.asm")):
        with open(text_file, "r") as f:
            lines = f.readlines()

        i = 0
        while i < len(lines):
            stripped = lines[i].strip()
            label_match = re.match(r'^(_\w+)::', stripped)
            if label_match:
                label = label_match.group(1)
                i += 1
                dialogue, i = parse_dialogue_string(lines, i)
                if dialogue:
                    dialogues[label] = dialogue
                continue
            i += 1

    return dialogues


def parse_script_file(file_path):
    """
    Parse a scripts/*.asm file and extract:
    1. Text pointer table (TEXT_ constant -> local label mappings)
    2. text_far references (local label -> dialogue label mappings)
    3. Trainer headers
    
    Returns (text_pointers, text_far_refs, trainer_headers).
    """
    map_name = file_path.stem

    with open(file_path, "r") as f:
        content = f.read()
        lines = content.split("\n")

    text_pointers = []  # (text_constant, local_label, index)
    text_far_refs = {}  # local_label -> [dialogue_labels]
    trainer_headers = []

    # 1. Parse text pointer table
    # Format: dw_const LocalLabel, TEXT_CONSTANT
    pointer_idx = 0
    in_text_pointers = False
    for line in lines:
        stripped = line.strip()

        if "def_text_pointers" in stripped:
            in_text_pointers = True
            pointer_idx = 0
            continue

        if in_text_pointers:
            tp_match = re.match(r'\s*dw_const\s+(\w+),\s+(\w+)', stripped)
            if tp_match:
                local_label = tp_match.group(1)
                text_constant = tp_match.group(2)
                pointer_idx += 1
                text_pointers.append((text_constant, local_label, pointer_idx))
            elif stripped and not stripped.startswith(";"):
                in_text_pointers = False

    # 2. Parse text_far references
    # Find all labels and their text_far targets
    current_label = None
    for line in lines:
        stripped = line.strip()

        # Match labels like "PalletTownSignText:" or ".HeyWaitDontGoOutText:"
        label_match = re.match(r'^(\w+):$', stripped)
        sub_label_match = re.match(r'^\.(\w+):$', stripped)

        if label_match:
            current_label = label_match.group(1)
            if current_label not in text_far_refs:
                text_far_refs[current_label] = []
        elif sub_label_match and current_label:
            sub_name = sub_label_match.group(1)
            # Sub-labels are part of the parent label's text_asm block
            pass

        # Match text_far references
        far_match = re.match(r'\s*text_far\s+(_\w+)', stripped)
        if far_match:
            dialogue_label = far_match.group(1)
            if current_label and current_label in text_far_refs:
                text_far_refs[current_label].append(dialogue_label)
            # Also check if we're in a sub-label context
            # The dialogue_label itself is what matters

    # 3. Parse trainer headers
    # Format: trainer EVENT_FLAG, sight_range, BattleText, EndBattleText, AfterBattleText
    header_label = None
    header_idx = 0
    for line in lines:
        stripped = line.strip()

        # Match trainer header block labels
        th_label_match = re.match(r'^(\w+TrainerHeaders?):$', stripped)
        if th_label_match:
            header_label = th_label_match.group(1)
            header_idx = 0
            continue

        # Match individual trainer header labels
        th_idx_match = re.match(r'^(\w+TrainerHeader\d+):$', stripped)
        if th_idx_match:
            pass  # Just a label, the data follows

        # Match trainer macro
        trainer_match = re.match(
            r'\s*trainer\s+(\w+),\s+(\d+),\s+(\w+),\s+(\w+),\s+(\w+)',
            stripped
        )
        if trainer_match:
            event_flag = trainer_match.group(1)
            sight_range = int(trainer_match.group(2))
            battle_text = trainer_match.group(3)
            end_battle_text = trainer_match.group(4)
            after_battle_text = trainer_match.group(5)

            trainer_headers.append({
                "map_name": map_name,
                "header_label": header_label or f"{map_name}TrainerHeaders",
                "header_index": header_idx,
                "event_flag": event_flag,
                "sight_range": sight_range,
                "battle_text_label": battle_text,
                "end_battle_text_label": end_battle_text,
                "after_battle_text_label": after_battle_text,
            })
            header_idx += 1

    return text_pointers, text_far_refs, trainer_headers


def collect_all_text_far_from_scripts():
    """
    Do a second pass over all script files to find ALL text_far references,
    including those in sub-labels and trainer text labels.
    Returns dict of {local_label: dialogue_label}.
    """
    all_refs = {}

    for script_file in sorted(SCRIPTS_DIR.glob("*.asm")):
        with open(script_file, "r") as f:
            lines = f.readlines()

        current_label = None
        for line in lines:
            stripped = line.strip()

            # Track current label (top-level or sub-label)
            label_match = re.match(r'^(\w+):$', stripped)
            sub_match = re.match(r'^\.(\w+):$', stripped)

            if label_match:
                current_label = label_match.group(1)
            elif sub_match and current_label:
                # Sub-labels like .HeyWaitDontGoOutText belong to parent
                pass

            far_match = re.match(r'\s*text_far\s+(_\w+)', stripped)
            if far_match and current_label:
                dialogue_label = far_match.group(1)
                all_refs[current_label] = dialogue_label

    return all_refs


def main():
    conn = sqlite3.connect(DB_PATH)
    cursor = create_tables(conn)

    # =========================================================================
    # Phase 1: Extract all dialogue strings from text/*.asm files
    # =========================================================================
    print("Phase 1: Parsing text files...")
    all_dialogues = {}
    text_file_count = 0

    for text_file in sorted(TEXT_DIR.glob("*.asm")):
        dialogues = parse_text_file(text_file)
        for label, text in dialogues.items():
            all_dialogues[label] = {
                "dialogue": text,
                "source_file": f"text/{text_file.name}",
            }
        text_file_count += 1

    # Also parse global text files (data/text/text_*.asm)
    global_dialogues = parse_global_text_files()
    for label, text in global_dialogues.items():
        if label not in all_dialogues:
            all_dialogues[label] = {
                "dialogue": text,
                "source_file": "data/text/",
            }

    print(f"  Found {len(all_dialogues)} dialogue strings from {text_file_count} text files")

    # Insert dialogue text
    for label, data in all_dialogues.items():
        cursor.execute(
            "INSERT OR IGNORE INTO dialogue_text (label, source_file, dialogue) VALUES (?, ?, ?)",
            (label, data["source_file"], data["dialogue"]),
        )

    # =========================================================================
    # Phase 2: Parse script files for text pointers and trainer headers
    # =========================================================================
    print("Phase 2: Parsing script files...")

    # First, build a complete map of local_label -> dialogue_label from text_far refs
    all_text_far_refs = collect_all_text_far_from_scripts()
    print(f"  Found {len(all_text_far_refs)} text_far references")

    script_count = 0
    total_pointers = 0
    total_trainers = 0

    for script_file in sorted(SCRIPTS_DIR.glob("*.asm")):
        map_name = script_file.stem
        text_pointers, text_far_refs, trainer_headers = parse_script_file(script_file)

        # Insert text pointers
        for text_constant, local_label, idx in text_pointers:
            # Resolve the dialogue label via text_far
            dialogue_label = all_text_far_refs.get(local_label)

            # Check if this is a trainer NPC
            is_trainer = 0
            if local_label in text_far_refs and not text_far_refs[local_label]:
                # Has text_asm block (no direct text_far) - might be trainer
                is_trainer = 1 if "TalkToTrainer" in open(script_file).read() else 0

            cursor.execute(
                """INSERT INTO text_pointers 
                   (map_name, text_constant, local_label, dialogue_label, pointer_index, is_trainer) 
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (map_name, text_constant, local_label, dialogue_label, idx, is_trainer),
            )
            total_pointers += 1

        # Insert trainer headers
        for th in trainer_headers:
            # Resolve battle/end/after text labels to dialogue labels
            battle_dl = all_text_far_refs.get(th["battle_text_label"])
            end_dl = all_text_far_refs.get(th["end_battle_text_label"])
            after_dl = all_text_far_refs.get(th["after_battle_text_label"])

            cursor.execute(
                """INSERT INTO trainer_headers 
                   (map_name, header_label, header_index, event_flag, sight_range,
                    battle_text_label, end_battle_text_label, after_battle_text_label)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    th["map_name"],
                    th["header_label"],
                    th["header_index"],
                    th["event_flag"],
                    th["sight_range"],
                    battle_dl or th["battle_text_label"],
                    end_dl or th["end_battle_text_label"],
                    after_dl or th["after_battle_text_label"],
                ),
            )
            total_trainers += 1

        script_count += 1

    conn.commit()

    # =========================================================================
    # Summary
    # =========================================================================
    cursor.execute("SELECT COUNT(*) FROM dialogue_text")
    dialogue_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM text_pointers")
    pointer_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM trainer_headers")
    trainer_count = cursor.fetchone()[0]

    print(f"\nResults:")
    print(f"  dialogue_text:   {dialogue_count} entries")
    print(f"  text_pointers:   {pointer_count} entries from {script_count} scripts")
    print(f"  trainer_headers: {trainer_count} entries")

    conn.close()
    print("Done!")


if __name__ == "__main__":
    main()
