#!/usr/bin/env python3
"""
Extract map script data from the pokered disassembly.

Parses scripts/*.asm files to extract:
  - Script state machines (script pointers per map)
  - NPC movement data sequences
  - Event flag references (CheckEvent/SetEvent/ResetEvent)
  - Coordinate trigger zones
  - Warp events from data/maps/objects/*.asm
  - Raw script text for future Lua conversion

Creates tables:
  - map_scripts: Script state machine entries per map
  - npc_movement_data: Scripted NPC movement sequences
  - event_flags: All event flag references across scripts
  - coordinate_triggers: Coordinate-based script triggers
  - warp_events: Map warp/door connections
"""
import json
import os
import re
import sqlite3
from pathlib import Path

# Constants
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "pokemon.db"
SCRIPTS_DIR = PROJECT_ROOT / "pokemon-game-data/scripts"
OBJECTS_DIR = PROJECT_ROOT / "pokemon-game-data/data/maps/objects"


def create_tables(conn):
    """Create script-related tables."""
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS map_scripts")
    cursor.execute("DROP TABLE IF EXISTS npc_movement_data")
    cursor.execute("DROP TABLE IF EXISTS event_flags")
    cursor.execute("DROP TABLE IF EXISTS coordinate_triggers")
    cursor.execute("DROP TABLE IF EXISTS warp_events")

    # Map script state machine entries
    cursor.execute("""
    CREATE TABLE map_scripts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        map_name TEXT NOT NULL,
        script_index INTEGER NOT NULL,
        script_label TEXT NOT NULL,
        script_constant TEXT NOT NULL,
        raw_asm TEXT
    )
    """)

    # NPC movement sequences (used in cutscenes)
    cursor.execute("""
    CREATE TABLE npc_movement_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        map_name TEXT NOT NULL,
        label TEXT NOT NULL,
        movements TEXT NOT NULL
    )
    """)

    # Event flag references across all scripts
    cursor.execute("""
    CREATE TABLE event_flags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        map_name TEXT NOT NULL,
        flag_name TEXT NOT NULL,
        operation TEXT NOT NULL,
        context_label TEXT
    )
    """)

    # Coordinate-based triggers (player steps on tile -> script fires)
    cursor.execute("""
    CREATE TABLE coordinate_triggers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        map_name TEXT NOT NULL,
        label TEXT NOT NULL,
        x INTEGER NOT NULL,
        y INTEGER NOT NULL
    )
    """)

    # Warp events (doors, stairs, cave entrances)
    cursor.execute("""
    CREATE TABLE warp_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        map_name TEXT NOT NULL,
        map_id INTEGER,
        x INTEGER NOT NULL,
        y INTEGER NOT NULL,
        dest_map TEXT NOT NULL,
        dest_warp_index INTEGER NOT NULL
    )
    """)

    conn.commit()
    return cursor


def load_map_ids(cursor):
    """Load map name -> ID mapping from the maps table."""
    cursor.execute("SELECT id, name FROM maps")
    return {name: mid for mid, name in cursor.fetchall()}


def parse_script_pointers(lines):
    """
    Extract script pointer table entries.
    Format: dw_const ScriptLabel, SCRIPT_CONSTANT
    Returns list of (index, label, constant).
    """
    pointers = []
    in_table = False
    idx = 0

    for line in lines:
        stripped = line.strip()

        if "def_script_pointers" in stripped:
            in_table = True
            idx = 0
            continue

        if in_table:
            match = re.match(r"\s*dw_const\s+(\w+),\s+(\w+)", stripped)
            if match:
                label = match.group(1)
                constant = match.group(2)
                pointers.append((idx, label, constant))
                idx += 1
            elif stripped and not stripped.startswith(";") and not stripped.startswith("dw_const"):
                in_table = False

    return pointers


def parse_movement_data(content, map_name):
    """
    Extract NPC movement data sequences.
    Format:
    MovementDataLabel:
        db NPC_MOVEMENT_DOWN
        db NPC_MOVEMENT_LEFT
        ...
        db -1 ; end
    """
    movements = []
    lines = content.split("\n")
    i = 0

    while i < len(lines):
        stripped = lines[i].strip()

        # Look for movement data labels
        label_match = re.match(r"^(MovementData\w+|\.Movement\w+):", stripped)
        if not label_match:
            # Also match generic movement labels
            label_match = re.match(r"^(\w+Movement\w*|Movement\w+):", stripped)

        if label_match:
            label = label_match.group(1)
            move_list = []
            i += 1

            while i < len(lines):
                mline = lines[i].strip()
                # Match movement commands
                move_match = re.match(r"\s*db\s+(NPC_MOVEMENT_\w+|NPC_MOVEMENT_STEP_\w+)", mline)
                if move_match:
                    move_list.append(move_match.group(1))
                elif "db -1" in mline or "db $ff" in mline.lower():
                    break
                elif mline and not mline.startswith(";") and not mline.startswith("db"):
                    break
                i += 1

            if move_list:
                movements.append({
                    "map_name": map_name,
                    "label": label,
                    "movements": json.dumps(move_list),
                })
            continue

        i += 1

    return movements


def parse_event_flags(content, map_name):
    """
    Extract all event flag references (CheckEvent, SetEvent, ResetEvent).
    """
    flags = []
    lines = content.split("\n")
    current_label = None

    for line in lines:
        stripped = line.strip()

        # Track current label context
        label_match = re.match(r"^(\w+):$", stripped)
        if label_match:
            current_label = label_match.group(1)

        # Match event flag operations
        for op in ["CheckEvent", "SetEvent", "ResetEvent"]:
            flag_match = re.search(rf"\b{op}\s+(EVENT_\w+)", stripped)
            if flag_match:
                flag_name = flag_match.group(1)
                flags.append({
                    "map_name": map_name,
                    "flag_name": flag_name,
                    "operation": op.lower(),
                    "context_label": current_label,
                })

    return flags


def parse_coordinate_triggers(content, map_name):
    """
    Extract coordinate trigger arrays.
    Format:
    LabelCoords:
        dbmapcoord x, y
        ...
        db -1 ; end
    """
    triggers = []
    lines = content.split("\n")
    i = 0

    while i < len(lines):
        stripped = lines[i].strip()

        # Look for coordinate array labels
        coord_label_match = re.match(r"^(\w+Coords?\d*):$", stripped)
        if coord_label_match:
            label = coord_label_match.group(1)
            i += 1

            while i < len(lines):
                cline = lines[i].strip()
                coord_match = re.match(r"\s*dbmapcoord\s+(\d+),\s+(\d+)", cline)
                if coord_match:
                    x = int(coord_match.group(1))
                    y = int(coord_match.group(2))
                    triggers.append({
                        "map_name": map_name,
                        "label": label,
                        "x": x,
                        "y": y,
                    })
                elif "db -1" in cline:
                    break
                elif cline and not cline.startswith(";"):
                    break
                i += 1
            continue

        i += 1

    return triggers


def extract_raw_script_blocks(content, script_pointers):
    """
    Extract the raw assembly text for each script pointer label.
    Returns dict of {label: raw_asm_text}.
    """
    blocks = {}
    lines = content.split("\n")

    # Build set of all labels we want to extract
    target_labels = {sp[1] for sp in script_pointers}

    # Find all label positions
    label_positions = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        label_match = re.match(r"^(\w+):$", stripped)
        if label_match:
            label_positions.append((i, label_match.group(1)))

    # Extract blocks between labels
    for idx, (pos, label) in enumerate(label_positions):
        if label not in target_labels:
            continue

        # Find end of this block (next top-level label or EOF)
        end_pos = len(lines)
        for next_pos, _ in label_positions[idx + 1:]:
            end_pos = next_pos
            break

        block_lines = lines[pos:end_pos]
        raw_asm = "\n".join(block_lines).strip()

        # Limit to reasonable size (some blocks are very long)
        if len(raw_asm) > 4000:
            raw_asm = raw_asm[:4000] + "\n; ... (truncated)"

        blocks[label] = raw_asm

    return blocks


def parse_warp_events(file_path, map_ids):
    """
    Parse warp events from a data/maps/objects/*.asm file.
    Format: warp_event x, y, DEST_MAP, warp_index
    """
    warps = []
    map_name = file_path.stem

    with open(file_path, "r") as f:
        content = f.read()

    # Find warp events section
    warp_section = re.search(
        r"def_warp_events(.*?)(?:def_bg_events|def_object_events|\Z)",
        content, re.DOTALL
    )
    if not warp_section:
        return warps

    warp_pattern = r"warp_event\s+(\d+),\s+(\d+),\s+(\w+),\s+(\d+)"
    for match in re.finditer(warp_pattern, warp_section.group(1)):
        x = int(match.group(1))
        y = int(match.group(2))
        dest_map = match.group(3)
        dest_warp = int(match.group(4))

        # Convert CamelCase map name to UPPER_SNAKE_CASE for map_id lookup
        map_name_upper = re.sub(r"([a-z])([A-Z])", r"\1_\2", map_name)
        map_name_upper = re.sub(r"([A-Z])([A-Z][a-z])", r"\1_\2", map_name_upper)
        map_name_upper = re.sub(r"([a-zA-Z])(\d)", r"\1_\2", map_name_upper)
        map_name_upper = map_name_upper.upper()

        map_id = map_ids.get(map_name_upper)

        warps.append({
            "map_name": map_name,
            "map_id": map_id,
            "x": x,
            "y": y,
            "dest_map": dest_map,
            "dest_warp_index": dest_warp,
        })

    return warps


def main():
    conn = sqlite3.connect(DB_PATH)
    cursor = create_tables(conn)
    map_ids = load_map_ids(cursor)

    total_scripts = 0
    total_movements = 0
    total_flags = 0
    total_coords = 0
    total_warps = 0

    # =========================================================================
    # Phase 1: Parse script files
    # =========================================================================
    print("Phase 1: Parsing script files...")

    for script_file in sorted(SCRIPTS_DIR.glob("*.asm")):
        map_name = script_file.stem

        with open(script_file, "r") as f:
            content = f.read()
            lines = content.split("\n")

        # 1a. Script pointers (state machine)
        script_pointers = parse_script_pointers(lines)
        raw_blocks = extract_raw_script_blocks(content, script_pointers)

        for idx, label, constant in script_pointers:
            raw_asm = raw_blocks.get(label, "")
            cursor.execute(
                """INSERT INTO map_scripts 
                   (map_name, script_index, script_label, script_constant, raw_asm)
                   VALUES (?, ?, ?, ?, ?)""",
                (map_name, idx, label, constant, raw_asm),
            )
            total_scripts += 1

        # 1b. Movement data
        movements = parse_movement_data(content, map_name)
        for mv in movements:
            cursor.execute(
                "INSERT INTO npc_movement_data (map_name, label, movements) VALUES (?, ?, ?)",
                (mv["map_name"], mv["label"], mv["movements"]),
            )
            total_movements += 1

        # 1c. Event flags
        flags = parse_event_flags(content, map_name)
        for fl in flags:
            cursor.execute(
                """INSERT INTO event_flags 
                   (map_name, flag_name, operation, context_label)
                   VALUES (?, ?, ?, ?)""",
                (fl["map_name"], fl["flag_name"], fl["operation"], fl["context_label"]),
            )
            total_flags += 1

        # 1d. Coordinate triggers
        coords = parse_coordinate_triggers(content, map_name)
        for ct in coords:
            cursor.execute(
                """INSERT INTO coordinate_triggers 
                   (map_name, label, x, y) VALUES (?, ?, ?, ?)""",
                (ct["map_name"], ct["label"], ct["x"], ct["y"]),
            )
            total_coords += 1

    print(f"  Scripts: {total_scripts}")
    print(f"  Movement sequences: {total_movements}")
    print(f"  Event flag refs: {total_flags}")
    print(f"  Coordinate triggers: {total_coords}")

    # =========================================================================
    # Phase 2: Parse warp events from object files
    # =========================================================================
    print("\nPhase 2: Parsing warp events...")

    for obj_file in sorted(OBJECTS_DIR.glob("*.asm")):
        warps = parse_warp_events(obj_file, map_ids)
        for w in warps:
            cursor.execute(
                """INSERT INTO warp_events 
                   (map_name, map_id, x, y, dest_map, dest_warp_index)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (w["map_name"], w["map_id"], w["x"], w["y"],
                 w["dest_map"], w["dest_warp_index"]),
            )
            total_warps += 1

    print(f"  Warp events: {total_warps}")

    conn.commit()

    # =========================================================================
    # Summary
    # =========================================================================
    print(f"\nResults:")
    for table in ["map_scripts", "npc_movement_data", "event_flags",
                   "coordinate_triggers", "warp_events"]:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"  {table}: {count}")

    # Show unique event flags
    cursor.execute("SELECT COUNT(DISTINCT flag_name) FROM event_flags")
    unique_flags = cursor.fetchone()[0]
    print(f"\n  Unique event flags: {unique_flags}")

    # Show maps with scripts
    cursor.execute("SELECT COUNT(DISTINCT map_name) FROM map_scripts")
    maps_with_scripts = cursor.fetchone()[0]
    print(f"  Maps with scripts: {maps_with_scripts}")

    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    main()
