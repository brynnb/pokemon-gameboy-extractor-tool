#!/usr/bin/env python3
"""
Extract map script data from the pokered disassembly.

Parses scripts/*.asm files to extract:
  - Script state machines (script pointers per map)
  - NPC movement data sequences
  - Spin/arrow tile forced movement data
  - Event flag references (CheckEvent/SetEvent/ResetEvent)
  - Coordinate trigger zones
  - Warp events from data/maps/objects/*.asm
  - Raw script text for future Lua conversion

Creates tables:
  - map_scripts: Script state machine entries per map
  - npc_movement_data: Scripted NPC movement sequences
  - spin_tiles: Forced player movement tiles decoded from map_coord_movement
  - event_flags: All event flag references across scripts
  - coordinate_triggers: Coordinate-based script triggers
  - warp_events: Map warp/door connections
"""
import json
import os
import re
import sqlite3
from pathlib import Path

from config import DB_PATH, MAP_OBJECTS_DIR, PROJECT_ROOT, SCRIPTS_DIR
from map_references import CanonicalMapResolver

OBJECTS_DIR = MAP_OBJECTS_DIR


def create_tables(conn):
    """Create script-related tables."""
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS map_scripts")
    cursor.execute("DROP TABLE IF EXISTS npc_movement_data")
    cursor.execute("DROP TABLE IF EXISTS event_flags")
    cursor.execute("DROP TABLE IF EXISTS coordinate_triggers")
    cursor.execute("DROP TABLE IF EXISTS warp_events")
    cursor.execute("DROP TABLE IF EXISTS spin_tiles")

    # Map script state machine entries
    cursor.execute("""
    CREATE TABLE map_scripts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        map_name TEXT NOT NULL,
        map_id INTEGER NOT NULL,
        script_index INTEGER NOT NULL,
        script_label TEXT NOT NULL,
        script_constant TEXT NOT NULL,
        raw_asm TEXT,
        FOREIGN KEY (map_id) REFERENCES maps (id)
    )
    """)

    # NPC movement sequences (used in cutscenes)
    cursor.execute("""
    CREATE TABLE npc_movement_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        map_name TEXT NOT NULL,
        map_id INTEGER NOT NULL,
        label TEXT NOT NULL,
        movements TEXT NOT NULL,
        FOREIGN KEY (map_id) REFERENCES maps (id)
    )
    """)

    # Spin/arrow tile forced movement sequences.
    cursor.execute("""
    CREATE TABLE spin_tiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        map_name TEXT NOT NULL,
        map_id INTEGER NOT NULL,
        source_label TEXT NOT NULL,
        x INTEGER NOT NULL,
        y INTEGER NOT NULL,
        movement_label TEXT NOT NULL,
        movements TEXT NOT NULL,
        FOREIGN KEY (map_id) REFERENCES maps (id)
    )
    """)

    # Event flag references across all scripts
    cursor.execute("""
    CREATE TABLE event_flags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        map_name TEXT NOT NULL,
        map_id INTEGER NOT NULL,
        flag_name TEXT NOT NULL,
        operation TEXT NOT NULL,
        context_label TEXT,
        FOREIGN KEY (map_id) REFERENCES maps (id)
    )
    """)

    # Coordinate-based triggers (player steps on tile -> script fires)
    cursor.execute("""
    CREATE TABLE coordinate_triggers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        map_name TEXT NOT NULL,
        map_id INTEGER NOT NULL,
        label TEXT NOT NULL,
        x INTEGER NOT NULL,
        y INTEGER NOT NULL,
        FOREIGN KEY (map_id) REFERENCES maps (id)
    )
    """)

    # Warp events (doors, stairs, cave entrances)
    cursor.execute("""
    CREATE TABLE warp_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        map_name TEXT NOT NULL,
        map_id INTEGER NOT NULL,
        source_warp_index INTEGER NOT NULL CHECK(source_warp_index >= 1),
        x INTEGER NOT NULL,
        y INTEGER NOT NULL,
        dest_map TEXT NOT NULL,
        dest_kind TEXT NOT NULL CHECK(dest_kind IN ('fixed', 'last-map')),
        dest_map_id INTEGER,
        dest_warp_index INTEGER NOT NULL CHECK(dest_warp_index >= 0),
        source_file TEXT NOT NULL,
        UNIQUE(map_name, source_warp_index),
        FOREIGN KEY (map_id) REFERENCES maps (id),
        FOREIGN KEY (dest_map_id) REFERENCES maps (id),
        CHECK(
            (dest_kind = 'fixed' AND dest_map <> 'LAST_MAP'
                AND dest_map_id IS NOT NULL)
            OR
            (dest_kind = 'last-map' AND dest_map = 'LAST_MAP'
                AND dest_map_id IS NULL)
        )
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


def parse_int_literal(value):
    value = value.strip()
    if value.startswith("$"):
        return int(value[1:], 16)
    return int(value)


def decode_direction(direction):
    return {
        "D_UP": "UP",
        "D_DOWN": "DOWN",
        "D_LEFT": "LEFT",
        "D_RIGHT": "RIGHT",
        "NPC_MOVEMENT_UP": "UP",
        "NPC_MOVEMENT_DOWN": "DOWN",
        "NPC_MOVEMENT_LEFT": "LEFT",
        "NPC_MOVEMENT_RIGHT": "RIGHT",
    }.get(direction)


def parse_rle_movement_blocks(content):
    movements = {}
    lines = content.split("\n")
    i = 0

    while i < len(lines):
        stripped = lines[i].strip()
        label_match = re.match(r"^(\.?[A-Za-z0-9_]*Movement[A-Za-z0-9_]*|\.?RLEList_[A-Za-z0-9_]+):", stripped)
        if not label_match:
            i += 1
            continue

        label = label_match.group(1).lstrip(".")
        sequence = []
        i += 1

        while i < len(lines):
            mline = re.sub(r";.*$", "", lines[i]).strip()
            pair = re.match(r"db\s+(D_\w+|NPC_MOVEMENT_\w+),\s+(\$[0-9a-fA-F]+|\d+)", mline)
            if pair:
                direction = decode_direction(pair.group(1))
                if direction:
                    sequence.append({
                        "direction": direction,
                        "count": parse_int_literal(pair.group(2)),
                    })
                i += 1
                continue
            if "db -1" in mline or "db $ff" in mline.lower():
                break
            if mline and not mline.startswith(";") and not mline.startswith("db"):
                break
            i += 1

        if sequence:
            movements[label] = sequence
        continue

    return movements


def parse_spin_tiles(content, map_name):
    """
    Extract arrow/spin tile coordinate movement tables.

    Red/Blue stores these as map_coord_movement rows pointing at RLE movement
    lists. The macro passes through dbmapcoord x, y, so the first argument is
    the runtime x coordinate and the second is y. The simulated joypad buffer
    consumes decoded entries from its end index, so store the compact pairs in
    runtime order by reversing them.
    """
    movement_blocks = parse_rle_movement_blocks(content)
    spin_tiles = []
    current_label = None

    for raw_line in content.splitlines():
        stripped = re.sub(r";.*$", "", raw_line).strip()
        label_match = re.match(r"^([A-Za-z0-9_]+):$", stripped)
        if label_match:
            current_label = label_match.group(1)
            continue

        match = re.match(r"map_coord_movement\s+(\d+),\s+(\d+),\s+([A-Za-z0-9_]+)", stripped)
        if not match:
            continue

        movement_label = match.group(3)
        movements = movement_blocks.get(movement_label)
        if not movements:
            continue
        spin_tiles.append({
            "map_name": map_name,
            "source_label": current_label or "",
            "x": int(match.group(1)),
            "y": int(match.group(2)),
            "movement_label": movement_label,
            "movements": json.dumps(list(reversed(movements))),
        })

    return spin_tiles


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


def parse_warp_events(file_path, map_resolver, project_root=PROJECT_ROOT):
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
    map_id = map_resolver.resolve(map_name)
    source_file = Path(file_path).resolve().relative_to(
        Path(project_root).resolve()
    ).as_posix()
    for source_warp_index, match in enumerate(
        re.finditer(warp_pattern, warp_section.group(1)), start=1
    ):
        x = int(match.group(1))
        y = int(match.group(2))
        dest_map = match.group(3)
        dest_warp = int(match.group(4))

        is_last_map = dest_map == "LAST_MAP"

        warps.append({
            "map_name": map_name,
            "map_id": map_id,
            "source_warp_index": source_warp_index,
            "x": x,
            "y": y,
            "dest_map": dest_map,
            "dest_kind": "last-map" if is_last_map else "fixed",
            "dest_map_id": None if is_last_map else map_resolver.resolve(dest_map),
            "dest_warp_index": dest_warp,
            "source_file": source_file,
        })

    return warps


def validate_map_script_relationships(conn):
    """Reject unresolved or dangling map relationships in script tables."""
    required_map_tables = (
        "map_scripts",
        "npc_movement_data",
        "spin_tiles",
        "event_flags",
        "coordinate_triggers",
        "warp_events",
    )
    for table in required_map_tables:
        unresolved = conn.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE map_id IS NULL'
        ).fetchone()[0]
        if unresolved:
            raise ValueError(f"{table} has {unresolved} unresolved source maps")
    invalid_destinations = conn.execute(
        """
        SELECT COUNT(*) FROM warp_events
        WHERE (dest_kind = 'fixed' AND dest_map_id IS NULL)
           OR (dest_kind = 'last-map' AND dest_map_id IS NOT NULL)
        """
    ).fetchone()[0]
    if invalid_destinations:
        raise ValueError(
            f"warp_events has {invalid_destinations} invalid destination relationships"
        )
    absolute_paths = conn.execute(
        """
        SELECT COUNT(*) FROM warp_events
        WHERE source_file LIKE '/%'
           OR source_file GLOB '[A-Za-z]:*'
           OR instr(source_file, '\\') > 0
        """
    ).fetchone()[0]
    if absolute_paths:
        raise ValueError(f"warp_events has {absolute_paths} non-portable source paths")
    errors = []
    for table in required_map_tables:
        errors.extend(conn.execute(f'PRAGMA foreign_key_check("{table}")').fetchall())
    if errors:
        raise ValueError(f"Map-script foreign-key violations: {errors[:10]}")
    return {
        table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        for table in required_map_tables
    }


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = create_tables(conn)
    map_resolver = CanonicalMapResolver.from_connection(conn)

    total_scripts = 0
    total_movements = 0
    total_spin_tiles = 0
    total_flags = 0
    total_coords = 0
    total_warps = 0

    # =========================================================================
    # Phase 1: Parse script files
    # =========================================================================
    print("Phase 1: Parsing script files...")

    for script_file in sorted(SCRIPTS_DIR.glob("*.asm")):
        map_name = script_file.stem
        map_id = map_resolver.resolve(map_name)

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
                   (map_name, map_id, script_index, script_label, script_constant, raw_asm)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (map_name, map_id, idx, label, constant, raw_asm),
            )
            total_scripts += 1

        # 1b. Movement data
        movements = parse_movement_data(content, map_name)
        for mv in movements:
            cursor.execute(
                """INSERT INTO npc_movement_data
                   (map_name, map_id, label, movements) VALUES (?, ?, ?, ?)""",
                (mv["map_name"], map_id, mv["label"], mv["movements"]),
            )
            total_movements += 1

        # 1c. Spin/arrow tile forced movement
        spin_tiles = parse_spin_tiles(content, map_name)
        for tile in spin_tiles:
            cursor.execute(
                """INSERT INTO spin_tiles
                   (map_name, map_id, source_label, x, y, movement_label, movements)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    tile["map_name"],
                    map_id,
                    tile["source_label"],
                    tile["x"],
                    tile["y"],
                    tile["movement_label"],
                    tile["movements"],
                ),
            )
            total_spin_tiles += 1

        # 1d. Event flags
        flags = parse_event_flags(content, map_name)
        for fl in flags:
            cursor.execute(
                """INSERT INTO event_flags 
                   (map_name, map_id, flag_name, operation, context_label)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    fl["map_name"],
                    map_id,
                    fl["flag_name"],
                    fl["operation"],
                    fl["context_label"],
                ),
            )
            total_flags += 1

        # 1e. Coordinate triggers
        coords = parse_coordinate_triggers(content, map_name)
        for ct in coords:
            cursor.execute(
                """INSERT INTO coordinate_triggers 
                   (map_name, map_id, label, x, y) VALUES (?, ?, ?, ?, ?)""",
                (ct["map_name"], map_id, ct["label"], ct["x"], ct["y"]),
            )
            total_coords += 1

    print(f"  Scripts: {total_scripts}")
    print(f"  Movement sequences: {total_movements}")
    print(f"  Spin tiles: {total_spin_tiles}")
    print(f"  Event flag refs: {total_flags}")
    print(f"  Coordinate triggers: {total_coords}")

    # =========================================================================
    # Phase 2: Parse warp events from object files
    # =========================================================================
    print("\nPhase 2: Parsing warp events...")

    for obj_file in sorted(OBJECTS_DIR.glob("*.asm")):
        warps = parse_warp_events(obj_file, map_resolver)
        for w in warps:
            cursor.execute(
                """INSERT INTO warp_events 
                   (map_name, map_id, source_warp_index, x, y, dest_map,
                    dest_kind, dest_map_id, dest_warp_index, source_file)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    w["map_name"], w["map_id"], w["source_warp_index"],
                    w["x"], w["y"], w["dest_map"], w["dest_kind"],
                    w["dest_map_id"], w["dest_warp_index"], w["source_file"],
                ),
            )
            total_warps += 1

    print(f"  Warp events: {total_warps}")

    validate_map_script_relationships(conn)
    conn.commit()

    # =========================================================================
    # Summary
    # =========================================================================
    print(f"\nResults:")
    for table in ["map_scripts", "npc_movement_data", "spin_tiles",
                   "event_flags", "coordinate_triggers", "warp_events"]:
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
