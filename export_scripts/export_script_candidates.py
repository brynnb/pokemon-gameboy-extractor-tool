#!/usr/bin/env python3
"""
Emit structured script event candidates from Red/Blue ASM.

The rows produced here are intentionally not tied to any downstream game. They
describe Pokemon script behavior in a small neutral action vocabulary, while
leaving app-specific imports to downstream tools.
"""
import json
import re
import sqlite3

from config import (
    CINNABAR_LAB_ENGINE_FILE,
    DB_PATH,
    EVENT_CONSTANTS_FILE,
    ITEM_CONSTANTS_FILE,
    MAP_OBJECTS_DIR,
    POKEMON_CONSTANTS_FILE,
    PROJECT_ROOT,
    SCRIPT_CONSTANTS_FILE,
    SCRIPT_EVENT_BOULDER_TARGETS_PATH,
    SCRIPT_EVENT_CANDIDATES_PATH,
    SCRIPT_EVENT_CONDITIONAL_DIALOGUE_PATH,
    SCRIPT_EVENT_DIAGNOSTICS_PATH,
    SCRIPT_EVENT_IR_PATH,
    SCRIPT_EVENT_OBJECT_VISIBILITY_PATH,
    SCRIPT_EVENT_TILE_OVERRIDES_PATH,
    SCRIPT_EVENT_TRADES_PATH,
    SCRIPTS_DIR,
    TEXT_DIR,
    TRADES_FILE,
)
from runtime_profiles import apply_candidate_profile, apply_diagnostic_profile
from map_references import CanonicalMapResolver
from text_tokens import normalize_game_text_tokens

OBJECTS_DIR = MAP_OBJECTS_DIR
OUTPUT_PATH = SCRIPT_EVENT_CANDIDATES_PATH
IR_OUTPUT_PATH = SCRIPT_EVENT_IR_PATH
DIAGNOSTICS_OUTPUT_PATH = SCRIPT_EVENT_DIAGNOSTICS_PATH
TRADE_OUTPUT_PATH = SCRIPT_EVENT_TRADES_PATH
TILE_OUTPUT_PATH = SCRIPT_EVENT_TILE_OVERRIDES_PATH
BOULDER_OUTPUT_PATH = SCRIPT_EVENT_BOULDER_TARGETS_PATH
OBJECT_VISIBILITY_OUTPUT_PATH = SCRIPT_EVENT_OBJECT_VISIBILITY_PATH
CONDITIONAL_DIALOGUE_OUTPUT_PATH = SCRIPT_EVENT_CONDITIONAL_DIALOGUE_PATH


def create_tables(conn):
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS script_event_candidate_references")
    cursor.execute("DROP TABLE IF EXISTS script_event_ir_references")
    cursor.execute("DROP TABLE IF EXISTS script_event_candidate_conditions")
    cursor.execute("DROP TABLE IF EXISTS script_event_candidate_actions")
    cursor.execute("DROP TABLE IF EXISTS script_event_candidates")
    cursor.execute("DROP TABLE IF EXISTS script_event_ir_blocks")
    cursor.execute("DROP TABLE IF EXISTS script_event_candidate_diagnostics")
    cursor.execute("DROP TABLE IF EXISTS script_event_in_game_trades")
    cursor.execute("DROP TABLE IF EXISTS script_event_tile_overrides")
    cursor.execute("DROP TABLE IF EXISTS script_event_boulder_targets")
    cursor.execute("DROP TABLE IF EXISTS script_event_object_visibility")
    cursor.execute("DROP TABLE IF EXISTS script_event_conditional_dialogue")
    cursor.execute(
        """
        CREATE TABLE script_event_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            map_name TEXT NOT NULL,
            map_id INTEGER NOT NULL,
            script_label TEXT NOT NULL,
            trigger_type TEXT NOT NULL,
            trigger_label TEXT NOT NULL,
            confidence TEXT NOT NULL,
            candidate_json TEXT NOT NULL,
            FOREIGN KEY (map_id) REFERENCES maps (id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE script_event_ir_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            map_name TEXT NOT NULL,
            map_id INTEGER NOT NULL,
            label TEXT NOT NULL,
            kind TEXT NOT NULL,
            features_json TEXT NOT NULL,
            text_refs_json TEXT NOT NULL,
            event_refs_json TEXT NOT NULL,
            item_refs_json TEXT NOT NULL,
            pokemon_refs_json TEXT NOT NULL,
            movement_refs_json TEXT NOT NULL,
            object_refs_json TEXT NOT NULL,
            battle_refs_json TEXT NOT NULL,
            warp_refs_json TEXT NOT NULL,
            raw_asm TEXT NOT NULL,
            FOREIGN KEY (map_id) REFERENCES maps (id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE script_event_candidate_actions (
            candidate_id INTEGER NOT NULL,
            action_index INTEGER NOT NULL CHECK(action_index >= 0),
            action_type TEXT NOT NULL CHECK(action_type <> ''),
            action_json TEXT NOT NULL CHECK(json_valid(action_json)),
            PRIMARY KEY(candidate_id, action_index),
            FOREIGN KEY(candidate_id) REFERENCES script_event_candidates(id)
                ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE script_event_candidate_conditions (
            candidate_id INTEGER NOT NULL,
            condition_path TEXT NOT NULL CHECK(condition_path <> ''),
            value_index INTEGER NOT NULL CHECK(value_index >= 0),
            condition_value_json TEXT NOT NULL CHECK(json_valid(condition_value_json)),
            PRIMARY KEY(candidate_id, condition_path, value_index),
            FOREIGN KEY(candidate_id) REFERENCES script_event_candidates(id)
                ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE script_event_candidate_references (
            candidate_id INTEGER NOT NULL,
            reference_kind TEXT NOT NULL CHECK(reference_kind IN (
                'event', 'item', 'pokemon', 'movement', 'object', 'map',
                'script', 'text', 'battle', 'warp'
            )),
            json_path TEXT NOT NULL CHECK(json_path <> ''),
            reference_index INTEGER NOT NULL CHECK(reference_index >= 0),
            reference_value_json TEXT NOT NULL CHECK(json_valid(reference_value_json)),
            PRIMARY KEY(candidate_id, reference_kind, json_path, reference_index),
            FOREIGN KEY(candidate_id) REFERENCES script_event_candidates(id)
                ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE script_event_ir_references (
            ir_block_id INTEGER NOT NULL,
            reference_kind TEXT NOT NULL CHECK(reference_kind IN (
                'text', 'event', 'item', 'pokemon', 'movement', 'object',
                'battle', 'warp'
            )),
            reference_index INTEGER NOT NULL CHECK(reference_index >= 0),
            reference_value_json TEXT NOT NULL CHECK(json_valid(reference_value_json)),
            PRIMARY KEY(ir_block_id, reference_kind, reference_index),
            FOREIGN KEY(ir_block_id) REFERENCES script_event_ir_blocks(id)
                ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE script_event_candidate_diagnostics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            map_name TEXT NOT NULL,
            map_id INTEGER,
            script_label TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT NOT NULL,
            details_json TEXT NOT NULL,
            FOREIGN KEY (map_id) REFERENCES maps (id),
            CHECK (
                (map_name = 'GLOBAL' AND map_id IS NULL)
                OR (map_name <> 'GLOBAL' AND map_id IS NOT NULL)
            )
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS coordinate_triggers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            map_name TEXT NOT NULL,
            map_id INTEGER NOT NULL,
            label TEXT NOT NULL,
            x INTEGER NOT NULL,
            y INTEGER NOT NULL,
            FOREIGN KEY (map_id) REFERENCES maps (id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE script_event_in_game_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_key TEXT NOT NULL UNIQUE,
            map_name TEXT,
            script_label TEXT,
            text_constant TEXT,
            requested_pokemon TEXT NOT NULL,
            offered_pokemon TEXT NOT NULL,
            offered_nickname TEXT NOT NULL,
            dialogue_set TEXT NOT NULL,
            original_trade_index INTEGER NOT NULL,
            active INTEGER NOT NULL,
            source_file TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE script_event_tile_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            map_name TEXT NOT NULL,
            script_label TEXT NOT NULL,
            candidate_json TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE script_event_boulder_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_family TEXT NOT NULL,
            map_name TEXT NOT NULL,
            source_label TEXT NOT NULL,
            x INTEGER NOT NULL,
            y INTEGER NOT NULL,
            flag TEXT NOT NULL,
            drops_through_hole INTEGER NOT NULL,
            source_missable_object TEXT NOT NULL,
            destination_map_name TEXT NOT NULL,
            destination_missable_object TEXT NOT NULL,
            source_file TEXT NOT NULL,
            target_json TEXT NOT NULL,
            UNIQUE(target_family, map_name, x, y)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE script_event_object_visibility (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            map_name TEXT NOT NULL,
            map_id INTEGER NOT NULL,
            object_name TEXT NOT NULL,
            object_key TEXT NOT NULL,
            script_label TEXT NOT NULL,
            requires_event TEXT NOT NULL,
            visible INTEGER NOT NULL,
            label TEXT NOT NULL,
            rule_json TEXT NOT NULL,
            UNIQUE(map_id, object_name, requires_event, visible, label)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE script_event_conditional_dialogue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text_constant TEXT NOT NULL,
            map_name TEXT NOT NULL,
            script_label TEXT NOT NULL,
            priority INTEGER NOT NULL,
            requires_flags_json TEXT NOT NULL,
            requires_flags_absent_json TEXT NOT NULL,
            dialogue_labels_json TEXT NOT NULL,
            source_json TEXT NOT NULL,
            row_json TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return cursor


def normalize_text(text):
    text = re.sub(r"¥(\d+)", r"\1 Pokedollars", text)
    return normalize_game_text_tokens(text).replace("{PLAYER}", "(PLAYER)").rstrip("@")


def append_text_macro_page(pages, current_lines):
    if current_lines:
        pages.append("\n".join(current_lines).strip())
    current_lines.clear()


def extract_text_labels(text_path):
    if not text_path.exists():
        return {}

    labels = {}
    current_label = None
    pages = []
    current_lines = []

    for raw_line in text_path.read_text().splitlines():
        stripped = raw_line.strip()
        label_match = re.match(r"^_?(\w+)::$", stripped)
        if label_match:
            if current_label is not None:
                append_text_macro_page(pages, current_lines)
                labels[current_label] = [page for page in pages if page]
            current_label = label_match.group(1)
            pages = []
            current_lines = []
            continue

        if current_label is None:
            continue

        text_match = re.match(r'^(?:text|line|cont|para)\s+"(.*)"$', stripped)
        if text_match:
            macro = stripped.split(maxsplit=1)[0]
            line = normalize_text(text_match.group(1))
            if not line:
                continue
            if macro == "para":
                append_text_macro_page(pages, current_lines)
                current_lines.append(line)
            elif macro == "line":
                current_lines.append(line)
            elif macro == "cont":
                if current_lines:
                    current_lines[-1] = f"{current_lines[-1]} {line}".strip()
                else:
                    current_lines.append(line)
            else:
                if current_lines:
                    current_lines.append(line)
                else:
                    current_lines = [line]
        elif stripped in {"done", "text_end", "prompt"}:
            append_text_macro_page(pages, current_lines)
            labels[current_label] = [page for page in pages if page]
            current_label = None
            pages = []
            current_lines = []

    if current_label is not None:
        append_text_macro_page(pages, current_lines)
        labels[current_label] = [page for page in pages if page]

    return labels


def extract_map_text_labels(map_name):
    labels = {}
    for text_path in [TEXT_DIR / f"{map_name}.asm", *sorted(TEXT_DIR.glob(f"{map_name}_*.asm"))]:
        labels.update(extract_text_labels(text_path))
    return labels


def parse_coord_array(script_content, label):
    coords = []
    in_array = False
    for raw_line in script_content.splitlines():
        stripped = raw_line.strip()
        if stripped in {label, f"{label}:"}:
            in_array = True
            continue
        if not in_array:
            continue
        coord = re.match(r"dbmapcoord\s+(\d+),\s+(\d+)", stripped)
        if coord:
            coords.append({"x": int(coord.group(1)), "y": int(coord.group(2))})
            continue
        if "db -1" in stripped:
            break
        if stripped and not stripped.startswith(";"):
            break
    return coords


_SOURCE_MAP_IDS = None


def camel_to_upper_snake(value):
    value = re.sub(r"[^A-Za-z0-9]+", "_", value.strip())
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"([A-Za-z])([0-9])", r"\1_\2", value)
    value = value.strip("_").upper()
    value = re.sub(r"_((?:B)?\d+)_F\b", r"_\1F", value)
    return re.sub(r"_B_(\d+)F\b", r"_B\1F", value)


def source_map_id(map_name):
    global _SOURCE_MAP_IDS
    if _SOURCE_MAP_IDS is None:
        conn = sqlite3.connect(DB_PATH)
        try:
            _SOURCE_MAP_IDS = {
                name: map_id
                for map_id, name in conn.execute("SELECT id, name FROM maps")
            }
        finally:
            conn.close()
    return _SOURCE_MAP_IDS.get(camel_to_upper_snake(map_name), 0)


def parse_db_constants_array(script_content, label):
    constants = []
    in_array = False
    for raw_line in script_content.splitlines():
        stripped = strip_comment(raw_line)
        if stripped == f"{label}:":
            in_array = True
            continue
        if not in_array:
            continue
        match = re.match(r"db\s+([A-Z0-9_]+|\d+)\b", stripped)
        if match:
            value = match.group(1)
            if value in {"0", "-1"}:
                break
            constants.append(value)
            continue
        if stripped:
            break
    return constants


def parse_local_dbmapcoords(raw_asm, label):
    coords = []
    active = False
    for raw_line in raw_asm.splitlines():
        stripped = strip_comment(raw_line)
        if stripped == f"{label}:":
            active = True
            continue
        if not active:
            continue
        coord = re.match(r"dbmapcoord\s+(\d+),\s+(\d+)", stripped)
        if coord:
            coords.append({"x": int(coord.group(1)), "y": int(coord.group(2))})
            continue
        if re.match(r"db\s+-?1\b", stripped):
            break
        if stripped and re.match(r"^\w", stripped):
            break
    return coords


def parse_text_pointer_map(script_content):
    pointers = {}
    in_table = False
    for raw_line in script_content.splitlines():
        stripped = strip_comment(raw_line)
        if stripped == "def_text_pointers":
            in_table = True
            continue
        if not in_table:
            continue
        if not stripped:
            continue
        match = re.match(r"dw_const\s+(\w+),\s+(TEXT_\w+)", stripped)
        if match:
            pointers[match.group(1)] = match.group(2)
            continue
        break
    return pointers


def parse_text_pointer_entries(script_content):
    entries = []
    in_table = False
    for raw_line in script_content.splitlines():
        stripped = strip_comment(raw_line)
        if stripped == "def_text_pointers":
            in_table = True
            continue
        if not in_table:
            continue
        if not stripped:
            continue
        match = re.match(r"dw_const\s+(\w+),\s+(TEXT_\w+)", stripped)
        if match:
            entries.append({"label": match.group(1), "textConstant": match.group(2)})
            continue
        break
    return entries


def parse_dw_const_map(script_content):
    pointers = {}
    for raw_line in script_content.splitlines():
        stripped = strip_comment(raw_line)
        match = re.match(r"dw_const\s+(\w+),\s+((?:TEXT|SCRIPT)_\w+)", stripped)
        if match:
            pointers[match.group(2)] = match.group(1)
    return pointers


def strip_comment(line):
    return line.split(";", 1)[0].strip()


def unique_sorted(values):
    return sorted({value for value in values if value})


def extract_label_blocks(content):
    lines = content.splitlines()
    positions = []
    for idx, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        label_match = re.match(r"^(\w+):$", stripped)
        if label_match:
            positions.append((idx, label_match.group(1)))

    blocks = []
    for pos_idx, (start, label) in enumerate(positions):
        end = positions[pos_idx + 1][0] if pos_idx + 1 < len(positions) else len(lines)
        raw = "\n".join(lines[start:end]).strip()
        blocks.append({"label": label, "raw": raw})
    return blocks


def detect_block_kind(label, raw_asm):
    if "trainer " in raw_asm or "TalkToTrainer" in raw_asm:
        return "trainer"
    if "script_prize_vendor" in raw_asm:
        return "text_script"
    if "Script" in label:
        return "script"
    if re.search(r"\b(?:db|dw)\s+NPC_MOVEMENT_", raw_asm) or label.endswith("Movement"):
        return "movement"
    if "dbmapcoord" in raw_asm or label.endswith("Coords") or "CoordsArray" in label:
        return "coordinate_array"
    if "text_far" in raw_asm or "text_asm" in raw_asm or label.endswith("Text"):
        return "text"
    return "generic"


def extract_object_refs(clean_lines):
    object_refs = []
    for line in clean_lines:
        match = re.fullmatch(r"(HideObject|ShowObject|MissableObject|RemoveMissableObject)\s+([A-Z0-9_]+)", line)
        if match:
            object_refs.append({"op": match.group(1), "object": match.group(2), "source": "direct"})

    for idx, line in enumerate(clean_lines):
        match = re.fullmatch(r"ld\s+a,\s+(HS_[A-Z0-9_]+)", line)
        if not match:
            continue
        if idx + 2 >= len(clean_lines):
            continue
        if not re.fullmatch(r"ld\s+\[wMissableObjectIndex\],\s+a", clean_lines[idx + 1]):
            continue
        op_match = re.fullmatch(r"predef(?:_jump)?\s+(HideObject|ShowObject)", clean_lines[idx + 2])
        if not op_match:
            continue
        object_refs.append({"op": op_match.group(1), "object": match.group(1), "source": "wMissableObjectIndex"})

    return dedupe_dicts(object_refs)


def extract_features(label, raw_asm):
    clean_lines = [strip_comment(line) for line in raw_asm.splitlines()]
    clean = "\n".join(clean_lines)

    text_refs = unique_sorted(
        re.findall(r"\btext_far\s+_?(\w+)", clean)
        + re.findall(r"\bld\s+a,\s+(TEXT_\w+)", clean)
        + re.findall(r"\bdw_const\s+(\w+),\s+TEXT_\w+", clean)
    )

    event_refs = []
    for line in clean_lines:
        event_op = re.search(
            r"\b(CheckEvent|SetEvent|ResetEvent|CheckAndSetEvent|CheckAndResetEvent|"
            r"SetEventReuseHL|ResetEventReuseHL|SetEventAfterBranchReuseHL|"
            r"ResetEventAfterBranchReuseHL|ResetEvents)\b",
            line,
        )
        if not event_op:
            continue
        for event_name in re.findall(r"EVENT_\w+", line):
            event_refs.append({"op": event_op.group(1), "flag": event_name})

    item_refs = []
    for item, quantity in re.findall(r"\blb\s+bc,\s+([A-Z0-9_]+),\s+(\d+)", clean):
        if item not in {"HIGH", "LOW"}:
            item_refs.append({"item": item, "quantity": int(quantity), "source": "lb_bc"})
    for item in re.findall(r"\bld\s+b,\s+([A-Z0-9_]+)", clean):
        if item not in {"HIGH", "LOW"}:
            item_refs.append({"item": item, "quantity": 1, "source": "ld_b"})

    pokemon_refs = []
    for species, level in re.findall(r"\blb\s+bc,\s+([A-Z0-9_]+),\s+(\d+)", clean):
        if species not in {"HIGH", "LOW"} and ("GivePokemon" in clean or "MON" in species or species in known_gift_species()):
            pokemon_refs.append({"species": species, "level": int(level)})

    movement_refs = []
    for movement in re.findall(r"\bld\s+de,\s+(\w*Movement\w*)", clean):
        movement_refs.append({"label": movement})
    for x, y, movement in re.findall(r"\bmap_coord_movement\s+(\d+),\s+(\d+),\s+(\w+)", clean):
        movement_refs.append({"label": movement, "x": int(x), "y": int(y), "source": "map_coord_movement"})
    movement_commands = unique_sorted(re.findall(r"\bNPC_MOVEMENT_\w+", clean))

    object_refs = extract_object_refs(clean_lines)

    battle_refs = []
    if re.search(r"\b(?:InitTrainerBattle|StartTrainerBattle|TalkToTrainer|trainer\s+)", clean):
        battle_refs.append({"type": "trainer"})
    if re.search(r"\b(?:InitWildBattle|StartWildBattle)\b", clean):
        battle_refs.append({"type": "wild"})

    warp_refs = []
    for op in ["SpecialWarpToLastPokemonCenter", "WarpToLastPokemonCenter", "SetWarpDestination", "SafariZoneEntranceAutoWalk"]:
        if op in clean:
            warp_refs.append({"op": op})

    features = {
        "hasChoice": "YesNoChoice" in clean,
        "hasGiveItem": "GiveItem" in clean or "ReceiveItem" in clean,
        "hasGivePokemon": "GivePokemon" in clean,
        "hasMoneyCheck": "HasEnoughMoney" in clean,
        "hasTextAsm": "text_asm" in clean,
        "hasScriptPrizeVendor": "script_prize_vendor" in clean,
        "hasTrainerBattle": len(battle_refs) > 0 and any(ref["type"] == "trainer" for ref in battle_refs),
        "hasWildBattle": len(battle_refs) > 0 and any(ref["type"] == "wild" for ref in battle_refs),
        "movementCommands": movement_commands,
    }

    return {
        "mapName": "",
        "label": label,
        "kind": detect_block_kind(label, raw_asm),
        "features": features,
        "textRefs": text_refs,
        "eventRefs": event_refs,
        "itemRefs": dedupe_dicts(item_refs),
        "pokemonRefs": dedupe_dicts(pokemon_refs),
        "movementRefs": dedupe_dicts(movement_refs),
        "objectRefs": object_refs,
        "battleRefs": dedupe_dicts(battle_refs),
        "warpRefs": dedupe_dicts(warp_refs),
        "rawAsm": raw_asm,
    }


def known_gift_species():
    return {
        "BULBASAUR",
        "CHARMANDER",
        "SQUIRTLE",
        "EEVEE",
        "HITMONLEE",
        "HITMONCHAN",
        "LAPRAS",
        "OMANYTE",
        "KABUTO",
        "AERODACTYL",
    }


def dedupe_dicts(rows):
    seen = set()
    result = []
    for row in rows:
        key = json.dumps(row, sort_keys=True, separators=(",", ":"))
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def extract_script_ir():
    blocks = []
    for script_path in sorted(SCRIPTS_DIR.glob("*.asm")):
        map_name = script_path.stem
        content = script_path.read_text()
        for block in extract_label_blocks(content):
            ir = extract_features(block["label"], block["raw"])
            ir["mapName"] = map_name
            blocks.append(ir)
    return blocks


def interesting_ir_block(block):
    features = block["features"]
    return (
        features["hasChoice"]
        or features["hasGiveItem"]
        or features["hasGivePokemon"]
        or features["hasMoneyCheck"]
        or features["hasScriptPrizeVendor"]
        or features["hasTrainerBattle"]
        or features["hasWildBattle"]
        or len(block["eventRefs"]) > 0
        or len(block["movementRefs"]) > 0
        or len(block["objectRefs"]) > 0
        or len(block["warpRefs"]) > 0
    )


def diagnostic_for_ir_block(block, generated_labels):
    if block["label"] in generated_labels:
        return None
    if not interesting_ir_block(block):
        return None
    if is_daycare_runtime_block(block):
        return daycare_runtime_diagnostic(block)
    if is_standard_trainer_runtime_block(block):
        return trainer_runtime_diagnostic(block)
    if is_text_pointer_switch_runtime_block(block):
        return text_pointer_switch_runtime_diagnostic(block)
    if is_seafoam_runtime_block(block):
        return seafoam_runtime_diagnostic(block)
    if is_npc_face_player_runtime_block(block):
        return npc_face_player_runtime_diagnostic(block)

    reasons = []
    features = block["features"]
    if features["hasChoice"]:
        reasons.append("choice")
    if features["hasGiveItem"]:
        reasons.append("item_reward")
    if features["hasGivePokemon"]:
        reasons.append("pokemon_reward")
    if features["hasTrainerBattle"]:
        reasons.append("trainer_battle")
    if features["hasWildBattle"]:
        reasons.append("wild_battle")
    if features["hasMoneyCheck"]:
        reasons.append("money_check")
    if features.get("hasScriptPrizeVendor"):
        reasons.append("prize_vendor")
    if block["eventRefs"]:
        reasons.append("event_flags")
    if block["movementRefs"]:
        reasons.append("movement")
    if block["objectRefs"]:
        reasons.append("object_visibility")
    if block["warpRefs"]:
        reasons.append("warp")

    branch_weight = len(block["eventRefs"]) + len(block["movementRefs"]) + len(block["objectRefs"])
    status = "ambiguous" if features["hasChoice"] and branch_weight >= 3 else "unsupported"
    return {
        "mapName": block["mapName"],
        "scriptLabel": block["label"],
        "status": status,
        "reason": ",".join(reasons),
        "details": {
            "kind": block["kind"],
            "features": features,
            "textRefs": block["textRefs"],
            "eventRefs": block["eventRefs"],
            "itemRefs": block["itemRefs"],
            "pokemonRefs": block["pokemonRefs"],
            "movementRefs": block["movementRefs"],
            "objectRefs": block["objectRefs"],
            "battleRefs": block["battleRefs"],
            "warpRefs": block["warpRefs"],
        },
    }


def text_asm_text_pointer_diagnostics(generated_labels):
    diagnostics = []
    for script_path in sorted(SCRIPTS_DIR.glob("*.asm")):
        map_name = script_path.stem
        script_content = script_path.read_text()
        text_pointers = parse_text_pointer_map(script_content)
        blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
        for label, block in sorted(blocks_by_label.items()):
            if label in generated_labels:
                continue
            clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
            if "text_asm" not in clean:
                continue
            ir = extract_features(label, block["raw"])
            ir["mapName"] = map_name
            if diagnostic_for_ir_block(ir, generated_labels):
                continue

            text_refs = ordered_text_refs(block["raw"])
            text_constant = text_pointers.get(label, "")
            details = {
                "textConstant": text_constant,
                "kind": ir["kind"],
                "features": ir["features"],
                "textRefs": text_refs,
                "eventRefs": ir["eventRefs"],
                "itemRefs": ir["itemRefs"],
                "pokemonRefs": ir["pokemonRefs"],
                "movementRefs": ir["movementRefs"],
                "objectRefs": ir["objectRefs"],
                "battleRefs": ir["battleRefs"],
                "warpRefs": ir["warpRefs"],
                "source": {
                    "adapter": "text_asm_text_pointer_audit_v1",
                    "scriptPath": str(script_path.relative_to(PROJECT_ROOT)),
                    "mapName": map_name,
                    "coveredLabels": [label] if len(text_refs) == 1 else [],
                },
            }
            if len(text_refs) == 1:
                reason = "direct_text_pointer_v1" if label in text_pointers else "direct_text_label_v1"
                details["source"]["notes"] = [
                    "This text_asm block has exactly one source text_far target.",
                    "The extracted dialogue text data already provides this text; helper calls such as cries or Pokedex display are presentation side effects.",
                ]
                diagnostics.append(
                    {
                        "mapName": map_name,
                        "scriptLabel": label,
                        "status": "covered",
                        "reason": reason,
                        "details": details,
                    }
                )
                continue

            reason = "text_asm_no_text_refs"
            if len(text_refs) > 1:
                reason = "text_asm_multi_text_branch"
            details["source"]["notes"] = [
                "This text_asm block is not generated by a safe adapter and is not a simple direct text wrapper.",
                "Downstream runtimes should add a generated adapter or authored script before relying on the fallback dialogue label exported from this block.",
            ]
            diagnostics.append(
                {
                    "mapName": map_name,
                    "scriptLabel": label,
                    "status": "unsupported",
                    "reason": reason,
                    "details": details,
                }
            )
    return diagnostics


def is_standard_trainer_runtime_block(block):
    features = block["features"]
    if not features["hasTrainerBattle"] or features["hasWildBattle"]:
        return False
    if (
        features["hasChoice"]
        or features["hasGiveItem"]
        or features["hasGivePokemon"]
        or features["hasMoneyCheck"]
        or block["eventRefs"]
        or block["movementRefs"]
        or block["objectRefs"]
        or block["warpRefs"]
    ):
        return False

    clean = "\n".join(strip_comment(line) for line in block["rawAsm"].splitlines())
    return bool(
        re.search(r"\bld\s+hl,\s+\w+TrainerHeader\d+\s*\n\s*call\s+TalkToTrainer\b", clean)
        or re.search(r"^\w+TalkToTrainer:\s*\n\s*call\s+TalkToTrainer\s*\n\s*jp\s+TextScriptEnd\s*$", clean)
        or re.search(r"\btrainer\s+EVENT_\w+,\s*\d+\s*,\s*\w+\s*,\s*\w+\s*,\s*\w+\b", clean)
    )


def trainer_runtime_diagnostic(block):
    return {
        "mapName": block["mapName"],
        "scriptLabel": block["label"],
        "status": "covered",
        "reason": "trainer_battle_runtime_v1",
        "details": {
            "kind": block["kind"],
            "features": block["features"],
            "textRefs": block["textRefs"],
            "eventRefs": block["eventRefs"],
            "battleRefs": block["battleRefs"],
            "source": {
                "runtimeTables": [
                    "trainer_headers",
                    "trainer_parties",
                    "trainer_party_pokemon",
                ],
                "notes": [
                    "Standard trainer macros are exported through trainer_headers and party tables.",
                    "Downstream runtimes should handle TalkToTrainer via their trainer encounter/click systems instead of generated script JSON.",
                ],
            },
        },
    }


def is_daycare_runtime_block(block):
    if block["mapName"] != "Daycare" or block["label"] != "DaycareGentlemanText":
        return False
    clean = "\n".join(strip_comment(line) for line in block["rawAsm"].splitlines())
    required = [
        "wDayCareInUse",
        "PARTY_TO_DAYCARE",
        "DAYCARE_TO_PARTY",
        "DisplayPartyMenu",
        "HasEnoughMoney",
        "KnowsHMMove",
        "WriteMonMoves",
    ]
    return all(token in clean for token in required)


def daycare_runtime_diagnostic(block):
    return {
        "mapName": block["mapName"],
        "scriptLabel": block["label"],
        "status": "covered",
        "reason": "daycare_runtime_v1",
        "details": {
            "kind": block["kind"],
            "features": block["features"],
            "textRefs": block["textRefs"],
            "eventRefs": block["eventRefs"],
            "itemRefs": block["itemRefs"],
            "pokemonRefs": block["pokemonRefs"],
            "source": {
                "runtimeConcepts": [
                    "daycare_deposit",
                    "daycare_step_growth",
                    "daycare_withdraw",
                    "party_selection",
                    "money_fee",
                    "hm_move_restriction",
                    "level_up_move_learning",
                ],
                "notes": [
                    "The Red/Blue Day Care script is a multi-step party, money, storage, and move-learning state machine.",
                    "Downstream runtimes should handle it with server-authoritative Day Care mechanics instead of generated linear cutscene JSON.",
                ],
            },
        },
    }


def is_text_pointer_switch_runtime_block(block):
    if block["mapName"] != "ViridianMart" or block["label"] != "ViridianMartCheckParcelDeliveredScript":
        return False
    clean = "\n".join(strip_comment(line) for line in block["rawAsm"].splitlines())
    required = [
        "CheckEvent EVENT_OAK_GOT_PARCEL",
        "ViridianMart_TextPointers",
        "ViridianMart_TextPointers2",
        "wCurMapTextPtr",
    ]
    return all(token in clean for token in required)


def text_pointer_switch_runtime_diagnostic(block):
    return {
        "mapName": block["mapName"],
        "scriptLabel": block["label"],
        "status": "covered",
        "reason": "text_pointer_switch_runtime_v1",
        "details": {
            "kind": block["kind"],
            "features": block["features"],
            "textRefs": block["textRefs"],
            "eventRefs": block["eventRefs"],
            "source": {
                "runtimeConcepts": [
                    "flag_gated_dialogue",
                    "map_text_pointer_selection",
                ],
                "notes": [
                    "The Red/Blue script swaps the active map text pointer table after a story flag is set.",
                    "Downstream runtimes should represent this as explicit flag-gated dialogue/shop branches instead of generated map-load cutscene JSON.",
                ],
            },
        },
    }


def is_seafoam_runtime_block(block):
    clean = "\n".join(strip_comment(line) for line in block["rawAsm"].splitlines())
    seafoam_map_scripts = {
        "SeafoamIslands1F": "Seafoam1HolesCoords",
        "SeafoamIslandsB1F": "Seafoam2HolesCoords",
        "SeafoamIslandsB2F": "Seafoam3HolesCoords",
        "SeafoamIslandsB3F": "Seafoam4HolesCoords",
    }
    if block["mapName"] in seafoam_map_scripts and block["label"] == f'{block["mapName"]}_Script':
        required = [
            "BIT_PUSHED_BOULDER",
            seafoam_map_scripts[block["mapName"]],
            "CheckBoulderCoords",
            "IsPlayerOnDungeonWarp",
        ]
        return all(token in clean for token in required)
    if block["mapName"] == "Route20" and block["label"] == "Route20_Script":
        required = [
            "CheckAndResetEvent EVENT_IN_SEAFOAM_ISLANDS",
            "Route20BoulderScript",
        ]
        return all(token in clean for token in required)
    return False


def seafoam_runtime_diagnostic(block):
    return {
        "mapName": block["mapName"],
        "scriptLabel": block["label"],
        "status": "covered",
        "reason": "seafoam_boulder_current_runtime_v1",
        "details": {
            "kind": block["kind"],
            "features": block["features"],
            "eventRefs": block["eventRefs"],
            "movementRefs": block["movementRefs"],
            "objectRefs": block["objectRefs"],
            "source": {
                "runtimeConcepts": [
                    "strength_boulder_holes",
                    "per_character_object_visibility",
                    "dungeon_hole_warps",
                    "seafoam_currents",
                    "route20_boulder_reset",
                ],
                "notes": [
                    "Seafoam boulder holes and currents are multi-map runtime mechanics, not isolated linear cutscenes.",
                    "Downstream runtimes should model boulder drops, object visibility, hole warps, strong currents, and Route 20 reset behavior in authoritative map/movement systems.",
                ],
            },
        },
    }


def is_npc_face_player_runtime_block(block):
    clean = "\n".join(strip_comment(line) for line in block["rawAsm"].splitlines())
    if "BIT_NO_NPC_FACE_PLAYER" not in clean:
        return False
    disallowed_tokens = [
        "GiveItem",
        "GivePokemon",
        "EngageMapTrainer",
        "InitBattleEnemyParameters",
        "YesNoChoice",
        "predef HideObject",
        "predef ShowObject",
    ]
    if any(token in clean for token in disallowed_tokens):
        return False
    return "CheckEvent" in clean and ("set BIT_NO_NPC_FACE_PLAYER" in clean or "res BIT_NO_NPC_FACE_PLAYER" in clean)


def npc_face_player_runtime_diagnostic(block):
    return {
        "mapName": block["mapName"],
        "scriptLabel": block["label"],
        "status": "covered",
        "reason": "npc_face_player_runtime_v1",
        "details": {
            "kind": block["kind"],
            "features": block["features"],
            "eventRefs": block["eventRefs"],
            "source": {
                "runtimeConcepts": [
                    "npc_facing_presentation",
                    "interaction_orientation",
                ],
                "notes": [
                    "The source block only toggles whether an NPC turns to face the player.",
                    "Downstream runtimes may model this in NPC interaction presentation instead of generated gameplay cutscene JSON.",
                ],
            },
        },
    }


def spin_tile_runtime_diagnostics(conn):
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'spin_tiles'"
    ).fetchone()
    if not exists:
        return []

    rows = conn.execute(
        """
        SELECT map_name, source_label, COUNT(*) AS tile_count
        FROM spin_tiles
        WHERE source_label != ''
        GROUP BY map_name, source_label
        ORDER BY map_name, source_label
        """
    ).fetchall()
    diagnostics = []
    for map_name, source_label, tile_count in rows:
        diagnostics.append(
            {
                "mapName": map_name,
                "scriptLabel": source_label,
                "status": "covered",
                "reason": "spin_tile_runtime_v1",
                "details": {
                    "source": {
                        "runtimeTables": ["spin_tiles"],
                        "tileCount": tile_count,
                        "notes": [
                            "map_coord_movement arrow/spin tiles are exported through spin_tiles.",
                            "Downstream runtimes should import spin_tiles into their server-authoritative forced-movement table instead of generating script JSON.",
                        ],
                    },
                },
            }
        )
    return diagnostics


def source_metadata(map_name, adapter, script_path, text_path, notes=None):
    return {
        "adapter": adapter,
        "scriptPath": str(script_path.relative_to(PROJECT_ROOT)),
        "textPath": str(text_path.relative_to(PROJECT_ROOT)),
        "mapName": map_name,
        "notes": notes or [],
    }


def ordered_text_refs(raw_asm):
    clean = "\n".join(strip_comment(line) for line in raw_asm.splitlines())
    return re.findall(r"\btext_far\s+_?(\w+)", clean)


TEXT_SOUND_MACRO_TO_SFX = {
    "sound_get_item_1": "SFX_GET_ITEM_1",
    "sound_get_item_1_duplicate": "SFX_GET_ITEM_1",
    "sound_get_item_2": "SFX_GET_ITEM_2",
    "sound_get_key_item": "SFX_GET_KEY_ITEM",
    "sound_level_up": "SFX_LEVEL_UP",
    "sound_pokedex_rating": "SFX_POKEDEX_RATING",
    "sound_caught_mon": "SFX_CAUGHT_MON",
}


def sound_actions_for_raw(raw_asm):
    actions = []
    clean_lines = [strip_comment(line) for line in raw_asm.splitlines()]
    for index, line in enumerate(clean_lines):
        if not line:
            continue
        macro = line.split(maxsplit=1)[0]
        if macro in TEXT_SOUND_MACRO_TO_SFX:
            actions.append({"type": "playSFX", "sfxConstant": TEXT_SOUND_MACRO_TO_SFX[macro]})
            continue

        sfx_load = re.fullmatch(r"ld\s+a,\s+(SFX_[A-Z0-9_]+)", line)
        if not sfx_load:
            continue
        lookahead = "\n".join(clean_lines[index + 1 : index + 4])
        if re.search(r"\bcall\s+PlaySound(?:WaitForCurrent)?\b", lookahead):
            actions.append({"type": "playSFX", "sfxConstant": sfx_load.group(1)})

    deduped = []
    for action in actions:
        if deduped and deduped[-1] == action:
            continue
        deduped.append(action)
    return deduped


def local_label_section_raw(raw_asm, local_label):
    lines = []
    active = False
    for raw_line in raw_asm.splitlines():
        stripped = strip_comment(raw_line)
        if re.match(r"^(\.\w+):?$", stripped):
            if active:
                break
            active = stripped.rstrip(":") == local_label
            continue
        if active:
            lines.append(raw_line)
    return "\n".join(lines)


def sound_actions_for_script_text_ref(ref, blocks_by_label, container_raw=""):
    if ref.startswith("."):
        return sound_actions_for_raw(local_label_section_raw(container_raw, ref))
    block = blocks_by_label.get(ref)
    if not block:
        return []
    return sound_actions_for_raw(block["raw"])


def sound_actions_for_text_constant(text_constant, all_const_map, blocks_by_label):
    label = all_const_map.get(text_constant)
    if not label:
        return []
    return sound_actions_for_script_text_ref(label, blocks_by_label)


def lines_for_labels(text_labels, labels):
    lines = []
    for label in labels:
        lines.extend(text_labels.get(label, []))
    return lines


def parse_const_sequence(path, start_marker, end_marker):
    constants = []
    active = False
    for raw_line in path.read_text().splitlines():
        stripped = raw_line.strip()
        if start_marker in stripped:
            active = True
            continue
        if not active:
            continue
        if end_marker in stripped:
            break
        match = re.match(r"const\s+([A-Z0-9_]+)", strip_comment(raw_line))
        if match:
            constants.append(match.group(1))
    return constants


def parse_npc_movements(block_raw):
    return re.findall(r"\bNPC_MOVEMENT_(UP|DOWN|LEFT|RIGHT)\b", block_raw)


def parse_trade_mons():
    constants = parse_const_sequence(
        SCRIPT_CONSTANTS_FILE,
        "TradeMons indexes",
        "DEF NUM_NPC_TRADES",
    )
    trades_path = TRADES_FILE
    trades = {}
    trade_index = 0
    for raw_line in trades_path.read_text().splitlines():
        stripped = strip_comment(raw_line)
        match = re.match(
            r'db\s+([A-Z0-9_]+),\s+([A-Z0-9_]+),\s+TRADE_DIALOGSET_([A-Z0-9_]+),\s+"([^"]+)"',
            stripped,
        )
        if not match:
            continue
        if trade_index >= len(constants):
            break
        trade_key = constants[trade_index]
        trades[trade_key] = {
            "tradeKey": trade_key,
            "requestedPokemon": match.group(1),
            "offeredPokemon": match.group(2),
            "dialogueSet": match.group(3),
            "offeredNickname": match.group(4).rstrip("@"),
            "originalTradeIndex": trade_index,
            "active": False,
            "mapName": "",
            "scriptLabel": "",
            "textConstant": "",
            "sourceFile": "pokemon-game-data/data/events/trades.asm",
        }
        trade_index += 1
    return trades


def parse_pokemon_constants():
    constants = set()
    path = POKEMON_CONSTANTS_FILE
    for raw_line in path.read_text().splitlines():
        stripped = strip_comment(raw_line)
        if "DEF NUM_POKEMON_INDEXES" in stripped:
            break
        match = re.match(r"const\s+([A-Z0-9_]+)", stripped)
        if match:
            constants.add(match.group(1))
    return constants


def trade_key_for_block(raw_asm):
    match = re.search(r"\bld\s+a,\s+(TRADE_FOR_[A-Z0-9_]+)", raw_asm)
    if match:
        return match.group(1)
    match = re.search(r";\s*(TRADE_FOR_[A-Z0-9_]+)", raw_asm)
    if match:
        return match.group(1)
    return ""


def block_branches_to_trade_call(block, blocks_by_label):
    if "DoInGameTradeDialogue" in block["raw"]:
        return True
    for target in re.findall(r"\b(?:jr|jp)\s+(\w+)", "\n".join(strip_comment(line) for line in block["raw"].splitlines())):
        target_block = blocks_by_label.get(target)
        if target_block and "DoInGameTradeDialogue" in target_block["raw"]:
            return True
    return False


def parse_in_game_trade_usages_for_script(script_path):
    content = script_path.read_text()
    text_pointers = parse_text_pointer_map(content)
    blocks = extract_label_blocks(content)
    blocks_by_label = {block["label"]: block for block in blocks}
    usages = {}
    for idx, block in enumerate(blocks):
        trade_key = trade_key_for_block(block["raw"])
        if not trade_key:
            continue
        has_trade_call = block_branches_to_trade_call(block, blocks_by_label)
        if not has_trade_call and idx + 1 < len(blocks) and "DoInGameTradeDialogue" in blocks[idx + 1]["raw"]:
            has_trade_call = True
        if not has_trade_call:
            continue
        text_constant = text_pointers.get(block["label"])
        if not text_constant:
            continue
        usages[trade_key] = {
            "mapName": script_path.stem,
            "scriptLabel": block["label"],
            "textConstant": text_constant,
            "sourceFile": f"pokemon-game-data/scripts/{script_path.name}",
        }
    return usages


def in_game_trade_definitions():
    trades = parse_trade_mons()
    for script_path in sorted(SCRIPTS_DIR.glob("*.asm")):
        for trade_key, usage in parse_in_game_trade_usages_for_script(script_path).items():
            trade = trades.get(trade_key)
            if not trade:
                continue
            trade.update(usage)
            trade["active"] = True
    return sorted(trades.values(), key=lambda trade: trade["originalTradeIndex"])


def local_text_label_map(raw_asm):
    labels = {}
    current_label = None
    for raw_line in raw_asm.splitlines():
        stripped = strip_comment(raw_line)
        label_match = re.match(r"^\.(\w+)$", stripped)
        if label_match:
            current_label = label_match.group(1)
            continue
        text_match = re.match(r"\btext_far\s+_?(\w+)", stripped)
        if current_label and text_match:
            labels[current_label] = text_match.group(1)
            current_label = None
    return labels


def local_print_text_refs_around(raw_asm, marker):
    lines = raw_asm.splitlines()
    marker_index = None
    for idx, raw_line in enumerate(lines):
        if marker in strip_comment(raw_line):
            marker_index = idx
            break
    if marker_index is None:
        return [], []

    before = []
    for raw_line in reversed(lines[:marker_index]):
        match = re.search(r"\bld\s+hl,\s+\.(\w+)", strip_comment(raw_line))
        if match:
            before.append(match.group(1))
            break

    after = []
    for raw_line in lines[marker_index + 1 :]:
        stripped = strip_comment(raw_line)
        if re.match(r"^\.\w+", stripped) or stripped == "jp TextScriptEnd":
            break
        match = re.search(r"\bld\s+hl,\s+\.(\w+)", stripped)
        if match:
            after.append(match.group(1))
            break

    return before, after


def hidden_object_constant(raw_asm):
    clean = "\n".join(strip_comment(line) for line in raw_asm.splitlines())
    match = re.search(
        r"\bld\s+a,\s+(HS_[A-Z0-9_]+)\s+"
        r"ld\s+\[wMissableObjectIndex\],\s+a\s+"
        r"predef\s+HideObject",
        clean,
    )
    if match:
        return match.group(1)
    return ""


def pokemon_gift_completion_flag(raw_asm, ir, species):
    set_events = [ref["flag"] for ref in ir["eventRefs"] if ref["op"] in {"SetEvent", "CheckAndSetEvent"}]
    set_events = unique_sorted(flag for flag in set_events if flag.startswith("EVENT_GOT_"))
    if len(set_events) == 1:
        return set_events[0]

    clean = "\n".join(strip_comment(line) for line in raw_asm.splitlines())
    for bit_name in re.findall(r"\bset\s+(BIT_GOT_[A-Z0-9_]+),", clean):
        return "EVENT_GOT_" + bit_name.removeprefix("BIT_GOT_")

    hidden_object = hidden_object_constant(raw_asm)
    if hidden_object and species in hidden_object:
        return f"EVENT_GOT_{species}"
    return ""


def pokemon_gift_text_groups(raw_asm, text_labels):
    local_map = local_text_label_map(raw_asm)
    before, after = local_print_text_refs_around(raw_asm, "call GivePokemon")
    prelude_labels = [local_map[label] for label in before if label in local_map]
    explanation_labels = [local_map[label] for label in after if label in local_map]
    return {
        "prelude": lines_for_labels(text_labels, prelude_labels),
        "explanation": lines_for_labels(text_labels, explanation_labels),
    }


def gift_text_label_groups(raw_asm):
    refs = ordered_text_refs(raw_asm)
    no_room = [label for label in refs if re.search(r"(?:NoRoom|BagFull|Full)", label)]
    received = [label for label in refs if re.search(r"(?:Received|Got|Obtained)", label) and label not in no_room]
    explanation = [
        label
        for label in refs
        if re.search(r"(?:Explanation|Explain|Already|Describe|Description|Info)", label)
        and label not in no_room
        and label not in received
    ]
    prelude = [
        label
        for label in refs
        if label not in set(no_room)
        and label not in set(received)
        and label not in set(explanation)
    ]
    return {
        "prelude": prelude,
        "received": received,
        "explanation": explanation,
        "noRoom": no_room,
    }


def simple_item_gift_candidate_for_block(map_name, script_path, text_path, text_pointers, text_labels, block):
    ir = extract_features(block["label"], block["raw"])
    features = ir["features"]
    raw = block["raw"]
    if not features["hasGiveItem"]:
        return []
    if features["hasChoice"] or features["hasGivePokemon"] or features["hasMoneyCheck"]:
        return []
    if features["hasTrainerBattle"] or features["hasWildBattle"]:
        return []
    if ir["movementRefs"] or ir["objectRefs"] or ir["warpRefs"]:
        return []
    if re.search(
        r"\b(?:IsItemInBag|RemoveItemByID|EngageMapTrainer|InitBattleEnemyParameters|"
        r"SaveEndBattleTextPointers|StartSimulatingJoypadStates)\b",
        raw,
    ):
        return []
    if re.search(r"\bpredef\s+(?:HideObject|ShowObject)\b", raw) or "wMissableObjectIndex" in raw:
        return []

    trigger_label = text_pointers.get(block["label"])
    if not trigger_label:
        return []

    item_refs = [ref for ref in ir["itemRefs"] if ref.get("source") == "lb_bc"]
    if len(item_refs) != 1:
        return []

    set_events = [ref["flag"] for ref in ir["eventRefs"] if ref["op"] in {"SetEvent", "CheckAndSetEvent"}]
    set_events = unique_sorted(flag for flag in set_events if flag.startswith("EVENT_GOT_"))
    if len(set_events) != 1:
        return []
    event_flag = set_events[0]

    groups = gift_text_label_groups(block["raw"])
    prelude_lines = lines_for_labels(text_labels, groups["prelude"])
    explanation_lines = lines_for_labels(text_labels, groups["explanation"])
    if not prelude_lines:
        return []

    source = source_metadata(
        map_name,
        "simple_item_gift_v1",
        script_path,
        text_path,
        notes=[
            f"sourceBlock={block['label']}",
            "Generated only for a single GiveItem plus one EVENT_GOT_* flag.",
            "Bag-full/no-room branches remain downstream behavior.",
        ],
    )

    item = item_refs[0]
    gift_actions = [{"type": "lockInput"}]
    if prelude_lines:
        gift_actions.append({"type": "dialogue", "lines": prelude_lines})
    gift_actions.append(
        {
            "type": "giveItem",
            "itemConstant": item["item"],
            "quantity": item.get("quantity", 1),
        }
    )
    gift_actions.extend(sound_actions_for_raw(block["raw"]))
    gift_actions.append({"type": "setEvent", "event": event_flag})
    gift_actions.append({"type": "unlockInput"})

    candidates = [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{block['label']}Gift",
            "trigger": {
                "type": "npc_click",
                "label": trigger_label,
                "sourceLabel": block["label"],
            },
            "conditions": {"requiresEventAbsent": event_flag},
            "actions": gift_actions,
            "source": source,
            "confidence": "adapter",
        }
    ]

    if explanation_lines:
        candidates.append(
            {
                "version": 1,
                "kind": "scriptEventCandidate",
                "mapName": map_name,
                "scriptLabel": f"{block['label']}AlreadyGot",
                "trigger": {
                    "type": "npc_click",
                    "label": trigger_label,
                    "sourceLabel": block["label"],
                },
                "conditions": {"requiresEvent": event_flag},
                "actions": [
                    {"type": "lockInput"},
                    {"type": "dialogue", "lines": explanation_lines},
                    {"type": "unlockInput"},
                ],
                "source": source,
                "confidence": "adapter",
            }
        )

    return candidates


def simple_item_gift_candidates():
    candidates = []
    for script_path in sorted(SCRIPTS_DIR.glob("*.asm")):
        map_name = script_path.stem
        text_path = TEXT_DIR / f"{map_name}.asm"
        script_content = script_path.read_text()
        text_pointers = parse_text_pointer_map(script_content)
        if not text_pointers:
            continue
        text_labels = extract_map_text_labels(map_name)
        for block in extract_label_blocks(script_content):
            candidates.extend(
                simple_item_gift_candidate_for_block(
                    map_name,
                    script_path,
                    text_path,
                    text_pointers,
                    text_labels,
                    block,
                )
            )
    return candidates


TMHM_DISPLAY_NAMES = None


def tmhm_display_names():
    global TMHM_DISPLAY_NAMES
    if TMHM_DISPLAY_NAMES is not None:
        return TMHM_DISPLAY_NAMES

    mapping = {}
    tm_count = 0
    hm_count = 0
    constants_path = ITEM_CONSTANTS_FILE
    for raw_line in constants_path.read_text().splitlines():
        stripped = strip_comment(raw_line)
        hm_match = re.match(r"add_hm\s+([A-Z0-9_]+)", stripped)
        if hm_match:
            hm_count += 1
            mapping[f"HM_{hm_match.group(1)}"] = f"HM{hm_count:02d}"
            continue
        tm_match = re.match(r"add_tm\s+([A-Z0-9_]+)", stripped)
        if tm_match:
            tm_count += 1
            mapping[f"TM_{tm_match.group(1)}"] = f"TM{tm_count:02d}"

    TMHM_DISPLAY_NAMES = mapping
    return mapping


def item_display_name(item_constant):
    special = {
        "EXP_ALL": "EXP.ALL",
    }
    if item_constant in special:
        return special[item_constant]
    tmhm_name = tmhm_display_names().get(item_constant)
    if tmhm_name:
        return tmhm_name
    return item_constant.replace("_", " ")


def hydrate_received_item_lines(lines, item_constant):
    if not lines:
        return lines

    item_name = item_display_name(item_constant)
    hydrated = []
    i = 0
    while i < len(lines):
        current = lines[i]
        if (
            current.rstrip().endswith("received")
            and i + 1 < len(lines)
            and re.fullmatch(r"(?:a|an|the)?\s*", lines[i + 1])
        ):
            article = lines[i + 1].strip()
            suffix = "!"
            skip = 2
            if i + 2 < len(lines) and re.fullmatch(r"!+", lines[i + 2].strip()):
                suffix = lines[i + 2].strip()
                skip = 3
            prefix = f"{article} " if article else ""
            hydrated.append(current.rstrip())
            hydrated.append(f"{prefix}{item_name}{suffix}")
            i += skip
            continue

        if (
            current.rstrip().endswith("received")
            and i + 1 < len(lines)
            and re.fullmatch(r"!+", lines[i + 1].strip())
        ):
            hydrated.append(current.rstrip())
            hydrated.append(f"{item_name}{lines[i + 1].strip()}")
            i += 2
            continue

        if (
            current.endswith("got the")
            and i + 1 < len(lines)
            and re.fullmatch(r"!+", lines[i + 1].strip())
        ):
            hydrated.append(current)
            hydrated.append(f"{item_name}{lines[i + 1].strip()}")
            i += 2
            continue

        hydrated.append(current)
        i += 1

    return hydrated


def hydrate_buffered_tm_reward_lines(lines, item_constant):
    if not lines:
        return lines

    item_name = item_display_name(item_constant)
    hydrated = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if line.rstrip().endswith("received"):
            hydrated.append(line.rstrip())
            next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if next_line == f"{item_name}!":
                hydrated.append(next_line)
                i += 2
                continue
            hydrated.append(f"{item_name}!")
            if re.fullmatch(r"!+", next_line):
                i += 2
                continue
            i += 1
            continue
        if stripped == "contains":
            hydrated.append(f"{item_name} contains")
            i += 1
            continue
        hydrated.append(line)
        i += 1
    return hydrated


def pascal_from_constant(value):
    special = {
        "HM05": "HM05",
        "HM_FLASH": "HM05",
        "EXP_ALL": "ExpAll",
    }
    if value in special:
        return special[value]
    return "".join(part[:1] + part[1:].lower() for part in value.split("_") if part)


def oaks_aide_candidate_for_block(map_name, script_path, text_path, text_pointers, text_labels, block):
    raw = block["raw"]
    clean = "\n".join(strip_comment(line) for line in raw.splitlines())
    if "predef OaksAideScript" not in clean:
        return []

    trigger_label = text_pointers.get(block["label"])
    if not trigger_label:
        return []

    requirement_match = re.search(
        r"\bld\s+a,\s+(\d+)\s+ldh\s+\[hOaksAideRequirement\],\s+a",
        clean,
    )
    item_match = re.search(
        r"\bld\s+a,\s+([A-Z0-9_]+)\s+ldh\s+\[hOaksAideRewardItem\],\s+a",
        clean,
    )
    event_match = re.search(r"\bSetEvent\s+(EVENT_GOT_[A-Z0-9_]+)", clean)
    if not requirement_match or not item_match or not event_match:
        return []

    requirement = int(requirement_match.group(1))
    item = item_match.group(1)
    event_flag = event_match.group(1)
    item_name = item_display_name(item)
    item_token = pascal_from_constant(event_flag.removeprefix("EVENT_GOT_"))
    label_base = block["label"].removesuffix("Text") + item_token

    local_map = local_text_label_map(raw)
    explanation_labels = [label for local, label in local_map.items() if local.lower().endswith("text")]
    explanation_lines = lines_for_labels(text_labels, explanation_labels)

    source = source_metadata(
        map_name,
        "oaks_aide_v1",
        script_path,
        text_path,
        notes=[
            f"sourceBlock={block['label']}",
            "Generated for map-local OaksAideScript setup blocks.",
            "Bag-full behavior remains downstream inventory behavior.",
        ],
    )
    prompt_lines = [
        "Hi! Remember me?",
        "I'm PROF.OAK's AIDE!",
        f"If you caught {requirement} kinds of POKEMON, I'm supposed to give you {item_name}!",
        f"Have you caught at least {requirement} kinds of POKEMON?",
    ]
    no_lines = [
        "Oh. I see.",
        f"When you get {requirement} kinds, come back for {item_name}.",
    ]

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{label_base}Reward",
            "trigger": {
                "type": "npc_click",
                "label": trigger_label,
                "sourceLabel": block["label"],
            },
            "conditions": {
                "requiresEventAbsent": event_flag,
                "requiresPokedexCaught": requirement,
            },
            "actions": [
                {"type": "lockInput"},
                {"type": "choice", "speaker": "OAKS AIDE", "promptLines": prompt_lines, "noLines": no_lines},
                {
                    "type": "dialogue",
                    "speaker": "OAKS AIDE",
                    "lines": [
                        f"Great! You have caught at least {requirement} kinds of POKEMON!",
                        "Congratulations!",
                        "Here you go!",
                    ],
                },
                {"type": "giveItem", "itemConstant": item, "quantity": 1},
                {"type": "dialogue", "lines": [f"(PLAYER) got the {item_name}!"]},
                {"type": "setEvent", "event": event_flag},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{label_base}Blocked",
            "trigger": {
                "type": "npc_click",
                "label": trigger_label,
                "sourceLabel": block["label"],
            },
            "conditions": {"requiresEventAbsent": event_flag},
            "actions": [
                {"type": "lockInput"},
                {"type": "choice", "speaker": "OAKS AIDE", "promptLines": prompt_lines, "noLines": no_lines},
                {
                    "type": "dialogue",
                    "speaker": "OAKS AIDE",
                    "lines": [
                        "Let's see...",
                        f"Uh-oh! You need {requirement} kinds if you want the {item_name}.",
                    ],
                },
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{label_base}Got",
            "trigger": {
                "type": "npc_click",
                "label": trigger_label,
                "sourceLabel": block["label"],
            },
            "conditions": {"requiresEvent": event_flag},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "speaker": "OAKS AIDE", "lines": explanation_lines or [f"Use {item_name} wisely."]},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
    ]


def oaks_aide_candidates():
    candidates = []
    for script_path in sorted(SCRIPTS_DIR.glob("*.asm")):
        map_name = script_path.stem
        text_path = TEXT_DIR / f"{map_name}.asm"
        script_content = script_path.read_text()
        text_pointers = parse_text_pointer_map(script_content)
        if not text_pointers:
            continue
        text_labels = extract_map_text_labels(map_name)
        for block in extract_label_blocks(script_content):
            candidates.extend(
                oaks_aide_candidate_for_block(
                    map_name,
                    script_path,
                    text_path,
                    text_pointers,
                    text_labels,
                    block,
                )
            )
    return candidates


def simple_pokemon_gift_candidate_for_block(map_name, script_path, text_path, text_pointers, text_labels, block):
    ir = extract_features(block["label"], block["raw"])
    features = ir["features"]
    raw = block["raw"]
    if not features["hasGivePokemon"]:
        return []
    if features["hasChoice"] or features["hasGiveItem"] or features["hasMoneyCheck"]:
        return []
    if features["hasTrainerBattle"] or features["hasWildBattle"]:
        return []
    if ir["movementRefs"] or ir["warpRefs"]:
        return []
    if len(re.findall(r"\bcall\s+GivePokemon\b", "\n".join(strip_comment(line) for line in raw.splitlines()))) != 1:
        return []

    trigger_label = text_pointers.get(block["label"])
    if not trigger_label:
        return []

    pokemon_refs = ir["pokemonRefs"]
    if len(pokemon_refs) != 1:
        return []
    pokemon = pokemon_refs[0]
    species = pokemon["species"]
    completion_flag = pokemon_gift_completion_flag(raw, ir, species)
    if not completion_flag:
        return []

    groups = pokemon_gift_text_groups(raw, text_labels)
    hidden_object = hidden_object_constant(raw)
    notes = [
        f"sourceBlock={block['label']}",
        "Generated only for a single GivePokemon with a clear completion flag/status/object marker.",
        "Party-full/no-room branches remain downstream behavior.",
    ]
    if hidden_object:
        notes.append(f"hiddenObject={hidden_object}")
    if completion_flag == f"EVENT_GOT_{species}" and completion_flag not in [ref["flag"] for ref in ir["eventRefs"]]:
        notes.append("Completion flag was derived from original status bit or hidden object state.")

    actions = [{"type": "lockInput"}]
    if groups["prelude"]:
        actions.append({"type": "dialogue", "lines": groups["prelude"]})
    actions.append(
        {
            "type": "givePokemon",
            "pokemonConstant": species,
            "level": pokemon["level"],
            "message": f"Received {species}!",
        }
    )
    if groups["explanation"]:
        actions.append({"type": "dialogue", "lines": groups["explanation"]})
    actions.append({"type": "setEvent", "event": completion_flag})
    if hidden_object:
        actions.append({"type": "hideObject", "triggerLabel": trigger_label})
    actions.append({"type": "unlockInput"})

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{block['label']}PokemonGift",
            "trigger": {
                "type": "npc_click",
                "label": trigger_label,
                "sourceLabel": block["label"],
            },
            "conditions": {"requiresEventAbsent": completion_flag},
            "actions": actions,
            "source": source_metadata(
                map_name,
                "simple_pokemon_gift_v1",
                script_path,
                text_path,
                notes=notes,
            ),
            "confidence": "adapter",
        }
    ]


def simple_pokemon_gift_candidates():
    candidates = []
    for script_path in sorted(SCRIPTS_DIR.glob("*.asm")):
        map_name = script_path.stem
        text_path = TEXT_DIR / f"{map_name}.asm"
        script_content = script_path.read_text()
        text_pointers = parse_text_pointer_map(script_content)
        if not text_pointers:
            continue
        text_labels = extract_map_text_labels(map_name)
        for block in extract_label_blocks(script_content):
            candidates.extend(
                simple_pokemon_gift_candidate_for_block(
                    map_name,
                    script_path,
                    text_path,
                    text_pointers,
                    text_labels,
                    block,
                )
            )
    return candidates


def safari_zone_gate_candidates():
    map_name = "SafariZoneGate"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists():
        return []

    script_content = script_path.read_text()
    text = extract_text_labels(text_path)
    coords = parse_coord_array(script_content, ".PlayerNextToSafariZoneWorker1CoordsArray")
    notes = []
    if not coords:
        coords = [{"x": 3, "y": 2}, {"x": 4, "y": 2}]
        notes.append("Coordinate array missing; used known Safari Zone gate worker tiles.")

    welcome = text.get("SafariZoneGateSafariZoneWorker1Text", ["Welcome to the SAFARI ZONE!"])
    join = text.get(
        "SafariZoneGateSafariZoneWorker1WouldYouLikeToJoinText",
        [
            "For just 500 Pokedollars, you can catch all the POKEMON you want in the park!",
            "Would you like to join the hunt?",
        ],
    )
    please_come_again = text.get("SafariZoneGateSafariZoneWorker1PleaseComeAgainText", ["OK! Please come again!"])
    payment = text.get(
        "SafariZoneGateSafariZoneWorker1ThatllBe500PleaseText",
        ["That'll be 500 Pokedollars please!", "We only use a special POKE BALL here.", "(PLAYER) received 30 SAFARI BALLs!"],
    )
    pa = text.get(
        "SafariZoneGateSafariZoneWorker1CallYouOnThePAText",
        ["We'll call you on the PA when you run out of time or SAFARI BALLs!"],
    )
    leaving = text.get("SafariZoneGateSafariZoneWorker1LeavingEarlyText", ["Leaving early?"])
    return_balls = text.get("SafariZoneGateSafariZoneWorker1ReturnSafariBallsText", ["Please return any SAFARI BALLs you have left."])
    good_luck = text.get("SafariZoneGateSafariZoneWorker1GoodLuckText", ["Good Luck!"])
    good_haul = text.get("SafariZoneGateSafariZoneWorker1GoodHaulComeAgainText", ["Did you get a good haul?", "Come again!"])

    source = source_metadata(map_name, "safari_zone_gate_v1", script_path, text_path, notes)
    source["coveredLabels"] = [
        "SafariZoneGateDefaultScript",
        "SafariZoneGatePlayerMovingRightScript",
        "SafariZoneGateWouldYouLikeToJoinScript",
        "SafariZoneGatePlayerMovingUpScript",
        "SafariZoneGatePlayerMovingDownScript",
        "SafariZoneGateLeavingSafariScript",
        "SafariZoneGateSetScriptAfterMoveScript",
        "SafariZoneEntranceAutoWalk",
        "SafariZoneGateSafariZoneWorker1WouldYouLikeToJoinText",
        "SafariZoneGateSafariZoneWorker1LeavingEarlyText",
    ]
    common_trigger = {
        "type": "coord",
        "coordinates": coords,
        "sourceLabel": ".PlayerNextToSafariZoneWorker1CoordsArray",
    }

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "SafariZoneGateEntryOffer",
            "trigger": {**common_trigger, "label": "SafariZoneGateEntryOffer"},
            "conditions": {"requiresEventAbsent": "EVENT_IN_SAFARI_ZONE"},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "speaker": "SAFARI ZONE WORKER", "lines": welcome},
                {
                    "type": "choice",
                    "speaker": "SAFARI ZONE WORKER",
                    "promptLines": join,
                    "noLines": please_come_again,
                },
                {
                    "type": "startSafariSession",
                    "fee": 500,
                    "balls": 30,
                    "steps": 500,
                    "successLines": payment + pa,
                    "destination": {
                        "mapName": "SafariZoneCenter",
                        "mapId": 220,
                        "x": 14,
                        "y": 25,
                        "direction": "UP",
                    },
                },
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "SafariZoneGateExit",
            "trigger": {**common_trigger, "label": "SafariZoneGateExit"},
            "conditions": {"requiresEvent": "EVENT_IN_SAFARI_ZONE"},
            "actions": [
                {"type": "lockInput"},
                {
                    "type": "choice",
                    "speaker": "SAFARI ZONE WORKER",
                    "promptLines": leaving,
                    "yesLines": return_balls + good_haul,
                    "noLines": good_luck,
                },
                {"type": "endSafariSession"},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
    ]


def parse_object_events_for_map(map_name):
    objects_path = OBJECTS_DIR / f"{map_name}.asm"
    if not objects_path.exists():
        return []

    objects = []
    for raw_line in objects_path.read_text().splitlines():
        stripped = strip_comment(raw_line)
        match = re.match(
            r"object_event\s+(\d+),\s*(\d+),\s*(SPRITE_\w+),\s*(\w+),\s*(\w+),\s*(TEXT_\w+)(?:,\s*([A-Z0-9_]+)(?:,\s*(\d+))?)?",
            stripped,
        )
        if not match:
            continue
        objects.append(
            {
                "x": int(match.group(1)),
                "y": int(match.group(2)),
                "sprite": match.group(3),
                "movement": match.group(4),
                "direction": match.group(5),
                "textConstant": match.group(6),
                "payload": match.group(7) or "",
                "level": int(match.group(8)) if match.group(8) else 0,
            }
        )
    return objects


def parse_trainer_header_blocks(script_content):
    headers = {}
    for match in re.finditer(
        r"^(\w+):\s*\n\s*trainer\s+(EVENT_\w+),\s*(\d+),\s*(\w+),\s*(\w+),\s*(\w+)",
        script_content,
        flags=re.MULTILINE,
    ):
        headers[match.group(1)] = {
            "label": match.group(1),
            "event": match.group(2),
            "sightRange": int(match.group(3)),
            "battleText": match.group(4),
            "endBattleText": match.group(5),
            "afterBattleText": match.group(6),
        }
    return headers


def find_trainer_header_for_text_block(block_raw):
    match = re.search(r"\bld\s+hl,\s+(\w+TrainerHeader\w*)", block_raw)
    if match:
        return match.group(1)
    return ""


def static_battle_lines(header, blocks_by_label, text_labels):
    battle_block = blocks_by_label.get(header["battleText"])
    if not battle_block:
        return []
    return lines_for_labels(text_labels, ordered_text_refs(battle_block["raw"]))


def static_wild_battle_candidates():
    candidates = []
    pokemon_constants = parse_pokemon_constants()
    for script_path in sorted(SCRIPTS_DIR.glob("*.asm")):
        map_name = script_path.stem
        script_content = script_path.read_text()
        text_path = TEXT_DIR / f"{map_name}.asm"
        text_labels = extract_map_text_labels(map_name)
        text_pointers = parse_text_pointer_map(script_content)
        source_label_by_text_constant = {text_constant: label for label, text_constant in text_pointers.items()}
        blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
        trainer_headers = parse_trainer_header_blocks(script_content)

        for obj in parse_object_events_for_map(map_name):
            species = obj["payload"]
            if obj["level"] <= 0 or species not in pokemon_constants:
                continue
            source_label = source_label_by_text_constant.get(obj["textConstant"], "")
            source_block = blocks_by_label.get(source_label)
            if not source_block:
                continue
            header_label = find_trainer_header_for_text_block(source_block["raw"])
            header = trainer_headers.get(header_label)
            if not header:
                continue

            script_label = source_label.removesuffix("Text") + "Encounter"
            helper_labels = unique_sorted(
                re.findall(r"\bjr\s+(\w+)", "\n".join(strip_comment(line) for line in source_block["raw"].splitlines()))
            )
            lines = static_battle_lines(header, blocks_by_label, text_labels)
            notes = [
                f"sourceBlock={source_label}",
                f"trainerHeader={header_label}",
                "Generated from object_event species/level plus trainer header win flag.",
            ]
            if helper_labels:
                notes.append("helperLabels=" + ",".join(helper_labels))
            source = source_metadata(map_name, "static_wild_battle_v1", script_path, text_path, notes)
            source["coveredLabels"] = [source_label, header_label, *helper_labels]

            candidates.append(
                {
                    "version": 1,
                    "kind": "scriptEventCandidate",
                    "mapName": map_name,
                    "scriptLabel": script_label,
                    "trigger": {
                        "type": "npc_click",
                        "label": obj["textConstant"],
                        "sourceLabel": source_label,
                    },
                    "conditions": {"requiresEventAbsent": header["event"]},
                    "actions": [
                        {"type": "lockInput"},
                        {"type": "dialogue", "lines": lines},
                        {"type": "unlockInput"},
                        {
                            "type": "startWildBattle",
                            "pokemonConstant": species,
                            "level": obj["level"],
                            "winFlag": header["event"],
                            "postWinActions": [
                                {"type": "hideObject", "textConstant": obj["textConstant"]}
                            ],
                        },
                    ],
                    "source": source,
                    "confidence": "adapter",
                }
            )
    return candidates


def snorlax_wake_battle_candidate_for_map(map_name):
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    text_pointers = parse_text_pointer_map(script_content)
    text_labels = extract_map_text_labels(map_name)

    default_block = blocks_by_label.get(f"{map_name}DefaultScript")
    if not default_block:
        return []
    clean = "\n".join(strip_comment(line) for line in default_block["raw"].splitlines())
    if "SNORLAX" not in clean or "wCurEnemyLevel" not in clean:
        return []

    beat_match = re.search(r"\bCheckEventHL\s+(EVENT_BEAT_ROUTE\d+_SNORLAX)", clean)
    fight_match = re.search(r"\bCheckEventReuseHL\s+(EVENT_FIGHT_ROUTE\d+_SNORLAX)", clean)
    reset_match = re.search(r"\bResetEventReuseHL\s+(EVENT_FIGHT_ROUTE\d+_SNORLAX)", clean)
    text_match = re.search(r"\bld\s+a,\s+(TEXT_ROUTE\d+_SNORLAX_WOKE_UP)", clean)
    level_match = re.search(r"\bld\s+a,\s+(\d+)\s+ld\s+\[wCurEnemyLevel\],\s+a", clean)
    object_match = re.search(r"\bld\s+a,\s+(HS_ROUTE_\d+_SNORLAX)", clean)
    if not beat_match or not fight_match or not reset_match or not text_match or not level_match or not object_match:
        return []
    beat_flag = beat_match.group(1)
    fight_flag = fight_match.group(1)
    if reset_match.group(1) != fight_flag:
        return []

    snorlax_object = next(
        (
            obj
            for obj in parse_object_events_for_map(map_name)
            if obj["sprite"] == "SPRITE_SNORLAX" and obj["textConstant"].startswith(f"TEXT_{map_name.upper()}_SNORLAX")
        ),
        None,
    )
    if not snorlax_object:
        return []

    intro_lines = lines_for_text_constant(snorlax_object["textConstant"], text_pointers, blocks_by_label, text_labels)
    woke_lines = lines_for_text_constant(text_match.group(1), text_pointers, blocks_by_label, text_labels)
    if not intro_lines or not woke_lines:
        return []

    source = source_metadata(
        map_name,
        "snorlax_wake_battle_v1",
        script_path,
        text_path,
        [
            f"sourceBlock={default_block['label']}",
            f"fightFlag={fight_flag}",
            f"beatFlag={beat_flag}",
            "Generated from the Red/Blue Snorlax wake-up map script and Snorlax object text.",
            "The neutral direct-interaction candidate requires POKE_FLUTE instead of modeling the separate Game Boy item-use flag handoff.",
            "Post-battle caught-vs-calmed flavor text remains diagnostics until battle-result-specific action branches are modeled.",
        ],
    )
    source["coveredLabels"] = [
        default_block["label"],
        f"{map_name}SnorlaxPostBattleScript",
        f"{map_name}SnorlaxText",
        f"{map_name}SnorlaxWokeUpText",
    ]

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{map_name}SnorlaxEncounter",
            "trigger": {
                "type": "npc_click",
                "label": snorlax_object["textConstant"],
                "sourceLabel": default_block["label"],
            },
            "conditions": {
                "requiresItem": "POKE_FLUTE",
                "requiresEventAbsent": beat_flag,
            },
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": intro_lines + woke_lines},
                {"type": "unlockInput"},
                {
                    "type": "startWildBattle",
                    "pokemonConstant": "SNORLAX",
                    "level": int(level_match.group(1)),
                    "winFlag": beat_flag,
                    "postWinActions": [
                        {"type": "hideObject", "objectKey": object_match.group(1)}
                    ],
                },
            ],
            "source": source,
            "confidence": "adapter",
        }
    ]


def snorlax_wake_battle_candidates():
    candidates = []
    for map_name in ["Route12", "Route16"]:
        candidates.extend(snorlax_wake_battle_candidate_for_map(map_name))
    return candidates


def pokemon_tower_marowak_ghost_candidate():
    map_name = "PokemonTower6F"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    text_pointers = parse_text_pointer_map(script_content)
    text_labels = extract_map_text_labels(map_name)

    default_block = blocks_by_label.get("PokemonTower6FDefaultScript")
    battle_block = blocks_by_label.get("PokemonTower6FMarowakBattleScript")
    if not default_block or not battle_block:
        return []

    default_clean = "\n".join(strip_comment(line) for line in default_block["raw"].splitlines())
    battle_clean = "\n".join(strip_comment(line) for line in battle_block["raw"].splitlines())
    required_default = [
        "CheckEvent EVENT_BEAT_GHOST_MAROWAK",
        "ld hl, PokemonTower6FMarowakCoords",
        "ld a, TEXT_POKEMONTOWER6F_BEGONE",
        "ld a, RESTLESS_SOUL",
        "ld [wCurOpponent], a",
        "ld [wCurEnemyLevel], a",
        "SCRIPT_POKEMONTOWER6F_MAROWAK_BATTLE",
    ]
    if any(snippet not in default_clean for snippet in required_default):
        return []
    if "SetEvent EVENT_BEAT_GHOST_MAROWAK" not in battle_clean or "TEXT_POKEMONTOWER6F_MAROWAK_DEPARTED" not in battle_clean:
        return []

    level_match = re.search(r"\bld\s+a,\s+(\d+)\s*\n\s*ld\s+\[wCurEnemyLevel\],\s+a", default_clean)
    if not level_match:
        return []
    coords = parse_coord_array(script_content, "PokemonTower6FMarowakCoords")
    if not coords:
        return []

    begone_lines = lines_for_text_constant(
        "TEXT_POKEMONTOWER6F_BEGONE",
        text_pointers,
        blocks_by_label,
        text_labels,
    )
    mother_lines = lines_for_script_text_ref(
        "PokemonTower6FGhostWasCubonesMotherText",
        blocks_by_label,
        text_labels,
        {},
    )
    calmed_lines = lines_for_script_text_ref(
        "PokemonTower6FSoulWasCalmedText",
        blocks_by_label,
        text_labels,
        {},
    )
    if not begone_lines or not mother_lines or not calmed_lines:
        return []

    source = source_metadata(
        map_name,
        "pokemon_tower_marowak_ghost_v1",
        script_path,
        text_path,
        [
            "sourceBlock=PokemonTower6FDefaultScript",
            "battleBlock=PokemonTower6FMarowakBattleScript",
            "Generated from the Pokemon Tower 6F RESTLESS_SOUL special wild battle.",
            "The original non-defeat branch pushes the player right; downstream runtimes may model that as a future post-lose action.",
        ],
    )
    source["coveredLabels"] = [
        "PokemonTower6FDefaultScript",
        "PokemonTower6FMarowakBattleScript",
        "PokemonTower6FBeGoneText",
        "PokemonTower6FMarowakDepartedText",
    ]

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "PokemonTower6FMarowakGhost",
            "trigger": {
                "type": "coord",
                "label": "PokemonTower6FMarowakCoords",
                "sourceLabel": "PokemonTower6FDefaultScript",
                "coordinates": coords,
            },
            "conditions": {"requiresEventAbsent": "EVENT_BEAT_GHOST_MAROWAK"},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": begone_lines},
                {"type": "unlockInput"},
                {
                    "type": "startWildBattle",
                    "pokemonConstant": "MAROWAK",
                    "level": int(level_match.group(1)),
                    "winFlag": "EVENT_BEAT_GHOST_MAROWAK",
                    "postWinActions": [
                        {"type": "dialogue", "lines": mother_lines + calmed_lines},
                    ],
                },
            ],
            "source": source,
            "confidence": "adapter",
        }
    ]


def viridian_old_man_catch_tutorial_candidate():
    map_name = "ViridianCity"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    text_pointers = parse_text_pointer_map(script_content)
    text_labels = extract_map_text_labels(map_name)

    old_man_block = blocks_by_label.get("ViridianCityOldManText")
    start_block = blocks_by_label.get("ViridianCityOldManStartCatchTrainingScript")
    end_block = blocks_by_label.get("ViridianCityOldManEndCatchTrainingScript")
    followup_block = blocks_by_label.get("ViridianCityOldManYouNeedToWeakenTheTargetText")
    if not all([old_man_block, start_block, end_block, followup_block]):
        return []

    text_constant = text_pointers.get("ViridianCityOldManText")
    if text_constant != "TEXT_VIRIDIANCITY_OLD_MAN":
        return []

    old_clean = "\n".join(strip_comment(line) for line in old_man_block["raw"].splitlines())
    start_clean = "\n".join(strip_comment(line) for line in start_block["raw"].splitlines())
    end_clean = "\n".join(strip_comment(line) for line in end_block["raw"].splitlines())
    if not all(
        snippet in old_clean
        for snippet in [
            "call YesNoChoice",
            "jr z, .refused",
            "ld a, SCRIPT_VIRIDIANCITY_OLD_MAN_START_CATCH_TRAINING",
        ]
    ):
        return []
    if not all(
        snippet in start_clean
        for snippet in [
            "ld a, BATTLE_TYPE_OLD_MAN",
            "ld a, 5",
            "ld a, WEEDLE",
            "ld a, SCRIPT_VIRIDIANCITY_OLD_MAN_END_CATCH_TRAINING",
        ]
    ):
        return []
    if "TEXT_VIRIDIANCITY_OLD_MAN_YOU_NEED_TO_WEAKEN_THE_TARGET" not in end_clean:
        return []

    local_refs = local_text_ref_map(old_man_block["raw"])
    prompt_lines = local_lines(text_labels, local_refs, ".HadMyCoffeeNowText")
    tutorial_lines = local_lines(text_labels, local_refs, ".KnowHowToCatchPokemonText")
    hurry_lines = local_lines(text_labels, local_refs, ".TimeIsMoneyText")
    followup_lines = lines_for_labels(text_labels, ordered_text_refs(followup_block["raw"]))
    if not prompt_lines or not tutorial_lines or not hurry_lines or not followup_lines:
        return []

    source = source_metadata(
        map_name,
        "viridian_old_man_catch_tutorial_v1",
        script_path,
        text_path,
        [
            "sourceBlock=ViridianCityOldManText",
            "sourceBlock=ViridianCityOldManStartCatchTrainingScript",
            "sourceBlock=ViridianCityOldManEndCatchTrainingScript",
            "The original prompt asks if the player is in a hurry: YES stops, NO continues into the Weedle catch tutorial.",
            "BATTLE_TYPE_OLD_MAN is represented as a scripted wild Weedle L5 battle with source follow-up dialogue.",
        ],
    )
    source["coveredLabels"] = [
        "ViridianCityOldManText",
        "ViridianCityOldManStartCatchTrainingScript",
        "ViridianCityOldManEndCatchTrainingScript",
        "ViridianCityOldManYouNeedToWeakenTheTargetText",
    ]

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "ViridianCityOldManCatchDemo",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": "ViridianCityOldManText",
            },
            "conditions": {
                "requiresEvent": "EVENT_GOT_POKEDEX",
                "requiresEventAbsent": "EVENT_OLD_MAN_CATCH_TUTORIAL_DONE",
            },
            "actions": [
                {"type": "lockInput"},
                {
                    "type": "choice",
                    "speaker": "OLD MAN",
                    "promptLines": prompt_lines,
                    "yesLines": hurry_lines,
                    "stopOnYes": True,
                    "noLines": tutorial_lines,
                    "continueOnNo": True,
                },
                {"type": "giveItem", "itemConstant": "POKE_BALL", "quantity": 1},
                {
                    "type": "startWildBattle",
                    "pokemonConstant": "WEEDLE",
                    "level": 5,
                    "allowedActions": ["item"],
                    "guaranteedCatch": True,
                    "postWinActions": [
                        {"type": "dialogue", "speaker": "OLD MAN", "lines": followup_lines},
                        {"type": "setEvent", "event": "EVENT_OLD_MAN_CATCH_TUTORIAL_DONE"},
                    ],
                },
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "ViridianCityOldManCatchDemoDone",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": "ViridianCityOldManText",
            },
            "conditions": {"requiresEvent": "EVENT_OLD_MAN_CATCH_TUTORIAL_DONE"},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "speaker": "OLD MAN", "lines": followup_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        }
    ]


def local_text_ref_map(raw_asm):
    refs = {}
    current_label = ""
    current_lines = []
    for raw_line in raw_asm.splitlines():
        stripped = strip_comment(raw_line)
        label_match = re.match(r"^(\.\w+):?$", stripped)
        if label_match:
            if current_label:
                refs[current_label] = ordered_text_refs("\n".join(current_lines))
            current_label = label_match.group(1)
            current_lines = []
            continue
        if current_label:
            current_lines.append(raw_line)
    if current_label:
        refs[current_label] = ordered_text_refs("\n".join(current_lines))
    return refs


def local_lines(text_labels, text_ref_map, local_label):
    return lines_for_labels(text_labels, text_ref_map.get(local_label, []))


def script_label_lines(text_labels, text_ref_map, label):
    if label.startswith("."):
        return local_lines(text_labels, text_ref_map, label)
    return text_labels.get(label, [])


def parse_simple_yes_no_dialogue(clean):
    call_targets = re.findall(r"\bcall\s+(\w+)", clean)
    if any(target not in {"PrintText", "YesNoChoice"} for target in call_targets):
        return None
    if re.search(
        r"\b(?:CurScript|NPCMovement|wSpriteIndex|hJoy|wJoy|wMissable|wBeatGymFlags|"
        r"wDoNotWaitForButtonPressAfterDisplayingText|wFilteredBagItemsCount)\b",
        clean,
    ):
        return None
    if re.search(r"\b(?:predef|farcall|jp\s+(?!TextScriptEnd\b)\w+)\b", clean):
        return None

    prompt_match = re.search(
        r"\bld\s+hl,\s+(\.\w+)\s*\n\s*call\s+PrintText\s*\n\s*call\s+YesNoChoice\b",
        clean,
    )
    if not prompt_match:
        return None

    branch_match = re.search(
        r"\bld\s+a,\s+\[wCurrentMenuItem\]\s*\n\s*and\s+a\s*\n\s*ld\s+hl,\s+(\.\w+)"
        r"\s*\n\s*jr\s+nz,\s*(\.\w+)\s*\n\s*ld\s+hl,\s+(\.\w+)\s*\n\s*\2:?"
        r"\s*\n\s*call\s+PrintText\s*\n\s*jp\s+TextScriptEnd\b",
        clean,
    )
    if branch_match:
        return {
            "prompt": prompt_match.group(1),
            "yes": branch_match.group(3),
            "no": branch_match.group(1),
        }

    branch_match = re.search(
        r"\bld\s+a,\s+\[wCurrentMenuItem\]\s*\n\s*(?:and\s+a|cp\s+\$0)"
        r"\s*\n\s*jr\s+nz,\s*(\.\w+)\s*\n\s*ld\s+hl,\s+(\.\w+)"
        r"\s*\n\s*call\s+PrintText\s*\n\s*jr\s+(\.\w+)\s*\n\s*\1:?"
        r"\s*\n\s*ld\s+hl,\s+(\.\w+)\s*\n\s*call\s+PrintText\s*\n\s*\3:?"
        r"\s*\n\s*jp\s+TextScriptEnd\b",
        clean,
    )
    if branch_match:
        return {
            "prompt": prompt_match.group(1),
            "yes": branch_match.group(2),
            "no": branch_match.group(4),
        }

    return None


def simple_yes_no_dialogue_candidate_for_block(map_name, script_path, text_path, text_pointers, text_labels, block):
    ir = extract_features(block["label"], block["raw"])
    features = ir["features"]
    if not features["hasChoice"]:
        return []
    if features["hasGiveItem"] or features["hasGivePokemon"] or features["hasMoneyCheck"]:
        return []
    if features["hasTrainerBattle"] or features["hasWildBattle"]:
        return []
    if ir["eventRefs"] or ir["movementRefs"] or ir["objectRefs"] or ir["warpRefs"]:
        return []

    text_constant = text_pointers.get(block["label"], "")
    if not text_constant:
        return []

    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    branches = parse_simple_yes_no_dialogue(clean)
    if not branches:
        return []

    text_refs = local_text_ref_map(block["raw"])
    prompt_lines = local_lines(text_labels, text_refs, branches["prompt"])
    yes_lines = local_lines(text_labels, text_refs, branches["yes"])
    no_lines = local_lines(text_labels, text_refs, branches["no"])
    if not prompt_lines or not yes_lines or not no_lines:
        return []

    source = source_metadata(
        map_name,
        "simple_yes_no_dialogue_v1",
        script_path,
        text_path,
        [
            f"sourceBlock={block['label']}",
            "Generated only for side-effect-free Yes/No informational dialogue.",
        ],
    )
    source["coveredLabels"] = [block["label"]]

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": block["label"] + "Choice",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": block["label"],
            },
            "actions": [
                {"type": "lockInput"},
                {
                    "type": "choice",
                    "promptLines": prompt_lines,
                    "yesLines": yes_lines,
                    "noLines": no_lines,
                },
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        }
    ]


def simple_yes_no_dialogue_candidates():
    candidates = []
    for script_path in sorted(SCRIPTS_DIR.glob("*.asm")):
        map_name = script_path.stem
        text_path = TEXT_DIR / f"{map_name}.asm"
        script_content = script_path.read_text()
        text_pointers = parse_text_pointer_map(script_content)
        if not text_pointers:
            continue
        text_labels = extract_map_text_labels(map_name)
        for block in extract_label_blocks(script_content):
            candidates.extend(
                simple_yes_no_dialogue_candidate_for_block(
                    map_name,
                    script_path,
                    text_path,
                    text_pointers,
                    text_labels,
                    block,
                )
            )
    return candidates


def pokemon_mansion_switch_candidate_for_map(target_map, text_constant, source_map, source_label):
    script_path = SCRIPTS_DIR / f"{source_map}.asm"
    text_path = TEXT_DIR / f"{source_map}.asm"
    if not script_path.exists() or not text_path.exists():
        return None

    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_path.read_text())}
    block = blocks_by_label.get(source_label)
    if not block:
        return None

    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    flag_match = re.search(r"\bCheckAndSetEvent\s+(EVENT_\w+).*?\bResetEventReuseHL\s+\1\b", clean, re.DOTALL)
    if not flag_match:
        return None
    if "call YesNoChoice" not in clean or "SFX_GO_INSIDE" not in clean:
        return None
    if re.search(r"\b(?:GiveItem|GivePokemon|TalkToTrainer|EngageMapTrainer)\b", clean):
        return None

    prompt_match = re.search(
        r"\bld\s+hl,\s+(\.\w+)\s*\n\s*call\s+PrintText\s*\n\s*call\s+YesNoChoice\b",
        clean,
    )
    pressed_match = re.search(
        r"\bset\s+BIT_CUR_MAP_LOADED_1,\s+\[hl\]\s*\n\s*ld\s+hl,\s+(\.\w+)\s*\n\s*call\s+PrintText\b",
        clean,
    )
    not_pressed_match = re.search(
        r"\.not_pressed:?\s*\n\s*ld\s+hl,\s+(\.\w+)\s*\n\s*call\s+PrintText\b",
        clean,
    )
    if not prompt_match or not pressed_match or not not_pressed_match:
        return None

    text_labels = extract_map_text_labels(source_map)
    text_refs = local_text_ref_map(block["raw"])
    prompt_lines = local_lines(text_labels, text_refs, prompt_match.group(1))
    pressed_lines = local_lines(text_labels, text_refs, pressed_match.group(1))
    not_pressed_lines = local_lines(text_labels, text_refs, not_pressed_match.group(1))
    if not prompt_lines or not pressed_lines or not not_pressed_lines:
        return None

    source = source_metadata(
        target_map,
        "pokemon_mansion_switch_toggle_v1",
        script_path,
        text_path,
        notes=[
            f"sourceBlock={source_label}",
            "Generated from Pokemon Mansion's secret switch Yes/No toggle state machine.",
            "Tile replacements are emitted through event-tile override data; this candidate covers the switch prompt and flag toggle.",
        ],
    )
    source["coveredLabels"] = unique_sorted([source_label])
    if target_map != source_map:
        source["notes"].append(f"{target_map} reuses {source_map}.{source_label} in the source text pointer table.")

    return {
        "version": 1,
        "kind": "scriptEventCandidate",
        "mapName": target_map,
        "scriptLabel": f"{target_map}SwitchToggle",
        "trigger": {
            "type": "npc_click",
            "label": text_constant,
            "sourceLabel": source_label,
        },
        "conditions": {},
        "actions": [
            {"type": "lockInput"},
            {
                "type": "choice",
                "textConstant": text_constant,
                "promptLines": prompt_lines,
                "yesLines": pressed_lines,
                "noLines": not_pressed_lines,
            },
            {"type": "toggleEvent", "event": flag_match.group(1)},
            {"type": "unlockInput"},
        ],
        "source": source,
        "confidence": "adapter",
    }


def pokemon_mansion_switch_candidates():
    specs = [
        ("PokemonMansion1F", "TEXT_POKEMONMANSION1F_SWITCH", "PokemonMansion1F", "PokemonMansion1FSwitchText"),
        ("PokemonMansion2F", "TEXT_POKEMONMANSION2F_SWITCH", "PokemonMansion2F", "PokemonMansion2FSwitchText"),
        ("PokemonMansion3F", "TEXT_POKEMONMANSION3F_SWITCH", "PokemonMansion2F", "PokemonMansion2FSwitchText"),
        ("PokemonMansionB1F", "TEXT_POKEMONMANSIONB1F_SWITCH", "PokemonMansion2F", "PokemonMansion2FSwitchText"),
    ]
    candidates = []
    for target_map, text_constant, source_map, source_label in specs:
        candidate = pokemon_mansion_switch_candidate_for_map(target_map, text_constant, source_map, source_label)
        if candidate:
            candidates.append(candidate)
    return candidates


def event_condition_branches(condition, fallthrough_label, branch_label):
    if condition == "nz":
        return {"present": branch_label, "absent": fallthrough_label}
    if condition == "z":
        return {"present": fallthrough_label, "absent": branch_label}
    return None


def parse_flag_gated_dialogue(clean):
    label_ref = r"(?:\.\w+|\w+)"
    event_matches = re.findall(r"\bCheckEvent\s+(EVENT_\w+)", clean)
    if len(event_matches) != 1:
        return None
    if len(re.findall(r"\bCheckEvent\w*\s+EVENT_\w+", clean)) != 1:
        return None
    event_flag = event_matches[0]

    call_targets = re.findall(r"\bcall\s+(\w+)", clean)
    if any(target != "PrintText" for target in call_targets):
        return None
    if re.search(
        r"\b(?:SetEvent|ResetEvent|CheckAndSetEvent|CheckAndResetEvent|SetEventReuseHL|"
        r"ResetEventReuseHL|predef|farcall|EngageMapTrainer|InitBattleEnemyParameters|"
        r"SaveEndBattleTextPointers|DisableWaitingAfterTextDisplay|PlaySound|ReplaceTileBlock|"
        r"GateUpstairsScript_PrintIfFacingUp)\b",
        clean,
    ):
        return None
    if re.search(r"\b(?:CurScript|wSpriteIndex|hJoy|wJoy|wMissable|wNewTileBlockID|wStatusFlags)\b", clean):
        return None
    if re.search(r"\bjp\s+(?!TextScriptEnd\b)\w+", clean):
        return None

    match = re.search(
        r"\bCheckEvent\s+" + re.escape(event_flag) +
        r"\s*\n\s*ld\s+hl,\s+(" + label_ref + r")\s*\n\s*jr\s+(nz|z),\s*(\.\w+)"
        r"\s*\n\s*ld\s+hl,\s+(" + label_ref + r")\s*\n\s*\3:?"
        r"\s*\n\s*call\s+PrintText\s*\n\s*jp\s+TextScriptEnd\b",
        clean,
    )
    if match:
        branches = event_condition_branches(match.group(2), match.group(4), match.group(1))
        if branches:
            branches["event"] = event_flag
            return branches

    match = re.search(
        r"\bCheckEvent\s+" + re.escape(event_flag) +
        r"\s*\n\s*jr\s+(nz|z),\s*(\.\w+)\s*\n\s*ld\s+hl,\s+(" + label_ref + r")"
        r"\s*\n\s*call\s+PrintText\s*\n\s*jr\s+(\.\w+)\s*\n\s*\2:?"
        r"\s*\n\s*ld\s+hl,\s+(" + label_ref + r")\s*\n\s*call\s+PrintText\s*\n\s*\4:?"
        r"\s*\n\s*jp\s+TextScriptEnd\b",
        clean,
    )
    if match:
        branches = event_condition_branches(match.group(1), match.group(3), match.group(5))
        if branches:
            branches["event"] = event_flag
            return branches

    match = re.search(
        r"\bCheckEvent\s+" + re.escape(event_flag) +
        r"\s*\n\s*jr\s+(nz|z),\s*(\.\w+)\s*\n\s*ld\s+hl,\s+(" + label_ref + r")"
        r"\s*\n\s*jr\s+(\.\w+)\s*\n\s*\2:?"
        r"\s*\n\s*ld\s+hl,\s+(" + label_ref + r")\s*\n\s*\4:?"
        r"\s*\n\s*call\s+PrintText\s*\n\s*jp\s+TextScriptEnd\b",
        clean,
    )
    if match:
        branches = event_condition_branches(match.group(1), match.group(3), match.group(5))
        if branches:
            branches["event"] = event_flag
            return branches

    return None


def dialogue_labels_for_branch(text_ref_map, label):
    if label.startswith("."):
        labels = text_ref_map.get(label, [])
    else:
        labels = [label]
    return [dialogue_label if dialogue_label.startswith("_") else f"_{dialogue_label}" for dialogue_label in labels]


def has_conditional_dialogue_side_effects(block):
    ir = extract_features(block["label"], block["raw"])
    features = ir["features"]
    if features["hasChoice"] or features["hasGiveItem"] or features["hasGivePokemon"] or features["hasMoneyCheck"]:
        return True
    if features["hasTrainerBattle"] or features["hasWildBattle"]:
        return True
    if ir["movementRefs"] or ir["objectRefs"] or ir["warpRefs"]:
        return True

    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    if re.search(
        r"\b(?:SetEvent|ResetEvent|CheckAndSetEvent|CheckAndResetEvent|SetEventReuseHL|"
        r"ResetEventReuseHL|predef|predef_jump|farcall|EngageMapTrainer|InitBattleEnemyParameters|"
        r"SaveEndBattleTextPointers|DisableWaitingAfterTextDisplay|PlaySound|ReplaceTileBlock|"
        r"GateUpstairsScript_PrintIfFacingUp|DisplayTextID|GiveItem|GivePokemon|YesNoChoice)\b",
        clean,
    ):
        return True
    call_targets = re.findall(r"\bcall\s+(\w+)", clean)
    if any(target != "PrintText" for target in call_targets):
        return True
    if re.search(r"\bjp\s+(?!TextScriptEnd\b)\w+", clean):
        return True
    return False


def conditional_dialogue_row(map_name, script_path, text_path, block, text_constant, priority, requires_flags, requires_flags_absent, dialogue_labels, adapter, notes=None):
    source = source_metadata(
        map_name,
        adapter,
        script_path,
        text_path,
        [
            f"sourceBlock={block['label']}",
            *(notes or []),
        ],
    )
    source["coveredLabels"] = [block["label"]]
    return {
        "version": 1,
        "kind": "conditionalDialogue",
        "mapName": map_name,
        "scriptLabel": f"{block['label']}ConditionalDialogue{priority}",
        "sourceScriptLabel": block["label"],
        "textConstant": text_constant,
        "priority": priority,
        "conditions": {
            "requiresEvents": sorted(requires_flags),
            "requiresEventsAbsent": sorted(requires_flags_absent),
        },
        "dialogueLabels": dialogue_labels,
        "source": source,
        "confidence": "adapter",
    }


def conditional_dialogue_rows_for_simple_flag_block(map_name, script_path, text_path, text_pointers, text_labels, block):
    if has_conditional_dialogue_side_effects(block):
        return []

    text_constant = text_pointers.get(block["label"], "")
    if not text_constant:
        return []

    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    branches = parse_flag_gated_dialogue(clean)
    if not branches:
        return []

    text_refs = local_text_ref_map(block["raw"])
    present_lines = script_label_lines(text_labels, text_refs, branches["present"])
    absent_lines = script_label_lines(text_labels, text_refs, branches["absent"])
    present_labels = dialogue_labels_for_branch(text_refs, branches["present"])
    absent_labels = dialogue_labels_for_branch(text_refs, branches["absent"])
    if not present_lines or not absent_lines or not present_labels or not absent_labels:
        return []

    return [
        conditional_dialogue_row(
            map_name,
            script_path,
            text_path,
            block,
            text_constant,
            20,
            [branches["event"]],
            [],
            present_labels,
            "text_asm_flag_gated_dialogue_v1",
            ["Generated from a dialogue-only text_asm with one CheckEvent branch."],
        ),
        conditional_dialogue_row(
            map_name,
            script_path,
            text_path,
            block,
            text_constant,
            10,
            [],
            [branches["event"]],
            absent_labels,
            "text_asm_flag_gated_dialogue_v1",
            ["Generated from a dialogue-only text_asm with one CheckEvent branch."],
        ),
    ]


def parse_nested_two_event_dialogue(clean):
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    try:
        start = next(i for i, line in enumerate(lines) if line.startswith("CheckEvent "))
    except StopIteration:
        return None
    lines = lines[start:]

    def match_line(index, pattern):
        if index >= len(lines):
            return None
        return re.match(pattern, lines[index])

    def is_local_label(index, label):
        return index < len(lines) and lines[index] in {label, f"{label}:"}

    event1 = match_line(0, r"CheckEvent\s+(EVENT_\w+)$")
    first_branch = match_line(1, r"jr\s+nz,\s+(\.\w+)$")
    absent_label = match_line(2, r"ld\s+hl,\s+(\.\w+|\w+)$")
    first_done = match_line(4, r"jr\s+(\.\w+)$")
    if (
        not event1
        or not first_branch
        or not absent_label
        or lines[3] != "call PrintText"
        or not first_done
        or not is_local_label(5, first_branch.group(1))
    ):
        return None

    event2 = match_line(6, r"CheckEventReuseA\s+(EVENT_\w+)$")
    second_branch = match_line(7, r"jr\s+nz,\s+(\.\w+)$")
    middle_label = match_line(8, r"ld\s+hl,\s+(\.\w+|\w+)$")
    second_done = match_line(10, r"jr\s+(\.\w+)$")
    if (
        not event2
        or not second_branch
        or not middle_label
        or lines[9] != "call PrintText"
        or not second_done
        or second_done.group(1) != first_done.group(1)
        or not is_local_label(11, second_branch.group(1))
    ):
        return None

    present_label = match_line(12, r"ld\s+hl,\s+(\.\w+|\w+)$")
    if (
        not present_label
        or lines[13] != "call PrintText"
        or not is_local_label(14, first_done.group(1))
        or lines[15] != "jp TextScriptEnd"
    ):
        return None

    return [
        {
            "priority": 300,
            "requires": [],
            "requiresAbsent": [event1.group(1)],
            "label": absent_label.group(1),
        },
        {
            "priority": 200,
            "requires": [event1.group(1), event2.group(1)],
            "requiresAbsent": [],
            "label": present_label.group(1),
        },
        {
            "priority": 100,
            "requires": [event1.group(1)],
            "requiresAbsent": [event2.group(1)],
            "label": middle_label.group(1),
        },
    ]


def conditional_dialogue_rows_for_nested_event_block(map_name, script_path, text_path, text_pointers, text_labels, block):
    if has_conditional_dialogue_side_effects(block):
        return []

    text_constant = text_pointers.get(block["label"], "")
    if not text_constant:
        return []

    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    branches = parse_nested_two_event_dialogue(clean)
    if not branches:
        return []

    text_refs = local_text_ref_map(block["raw"])
    rows = []
    for branch in branches:
        lines = script_label_lines(text_labels, text_refs, branch["label"])
        labels = dialogue_labels_for_branch(text_refs, branch["label"])
        if not lines or not labels:
            return []
        rows.append(
            conditional_dialogue_row(
                map_name,
                script_path,
                text_path,
                block,
                text_constant,
                branch["priority"],
                branch["requires"],
                branch["requiresAbsent"],
                labels,
                "text_asm_nested_event_dialogue_v1",
                ["Generated from a dialogue-only text_asm with nested CheckEvent/CheckEventReuseA branches."],
            )
        )
    return rows


def conditional_dialogue_rows_for_block(map_name, script_path, text_path, text_pointers, text_labels, block):
    rows = conditional_dialogue_rows_for_nested_event_block(
        map_name,
        script_path,
        text_path,
        text_pointers,
        text_labels,
        block,
    )
    if rows:
        return rows
    return conditional_dialogue_rows_for_simple_flag_block(
        map_name,
        script_path,
        text_path,
        text_pointers,
        text_labels,
        block,
    )


def conditional_dialogue_rows():
    rows = []
    for script_path in sorted(SCRIPTS_DIR.glob("*.asm")):
        map_name = script_path.stem
        text_path = TEXT_DIR / f"{map_name}.asm"
        script_content = script_path.read_text()
        text_pointers = parse_text_pointer_map(script_content)
        if not text_pointers:
            continue
        text_labels = extract_map_text_labels(map_name)
        for block in extract_label_blocks(script_content):
            rows.extend(
                conditional_dialogue_rows_for_block(
                    map_name,
                    script_path,
                    text_path,
                    text_pointers,
                    text_labels,
                    block,
                )
            )
    return rows


def badge_name_from_bit(bit_name):
    badge = bit_name.removeprefix("BIT_")
    return badge if badge.endswith("BADGE") else ""


def pascal_badge_name(badge):
    if badge.endswith("BADGE"):
        return pascal_from_constant(badge.removesuffix("BADGE") + "_BADGE")
    return pascal_from_constant(badge)


def badge_gated_gym_guide_candidate_for_block(map_name, script_path, text_path, text_pointers, text_labels, block):
    if "GymGuideText" not in block["label"] and "GuideText" not in block["label"]:
        return []

    text_constant = text_pointers.get(block["label"], "")
    if not text_constant:
        return []

    ir = extract_features(block["label"], block["raw"])
    features = ir["features"]
    if features["hasGiveItem"] or features["hasGivePokemon"] or features["hasMoneyCheck"]:
        return []
    if features["hasTrainerBattle"] or features["hasWildBattle"]:
        return []
    if ir["eventRefs"] or ir["movementRefs"] or ir["objectRefs"] or ir["warpRefs"]:
        return []

    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    if "wBeatGymFlags" not in clean:
        return []
    if re.search(r"\b(?:SetEvent|ResetEvent|CheckEvent|predef|farcall|EngageMapTrainer|InitBattleEnemyParameters)\b", clean):
        return []
    call_targets = re.findall(r"\bcall\s+(\w+)", clean)
    allowed_calls = {"PrintText", "YesNoChoice"}
    if any(target not in allowed_calls for target in call_targets):
        return []

    badge_match = re.search(
        r"\bld\s+a,\s+\[wBeatGymFlags\]\s+"
        r"bit\s+(BIT_[A-Z0-9_]+BADGE),\s+a\s+"
        r"jr\s+nz,\s+(\.\w+)",
        clean,
    )
    if not badge_match:
        return []
    badge = badge_name_from_bit(badge_match.group(1))
    after_label = badge_match.group(2)
    if not badge:
        return []

    label_ref = r"(?:\.\w+|\w+)"
    text_refs = local_text_ref_map(block["raw"])
    source = source_metadata(
        map_name,
        "badge_gated_gym_guide_v1",
        script_path,
        text_path,
        [
            f"sourceBlock={block['label']}",
            "Generated from gym-guide dialogue gated by Red/Blue wBeatGymFlags badge bits.",
            "Uses neutral requiresBadge/requiresBadgeAbsent conditions for downstream runtime mapping.",
        ],
    )
    source["coveredLabels"] = [block["label"]]
    suffix = pascal_badge_name(badge)

    if "YesNoChoice" in clean:
        choice_match = re.search(
            r"\bld\s+hl,\s+(" + label_ref + r")\s+"
            r"call\s+PrintText\s+"
            r"call\s+YesNoChoice\s+"
            r"ld\s+a,\s+\[wCurrentMenuItem\]\s+"
            r"and\s+a\s+"
            r"jr\s+nz,\s+(\.\w+)\s+"
            r"ld\s+hl,\s+(" + label_ref + r")\s+"
            r"call\s+PrintText\s+"
            r"jr\s+(\.\w+)\s+"
            r"\2:?\s+"
            r"ld\s+hl,\s+(" + label_ref + r")\s+"
            r"call\s+PrintText\s+"
            r"\4:?\s+"
            r"ld\s+hl,\s+(" + label_ref + r")\s+"
            r"call\s+PrintText\s+"
            r"jr\s+(\.\w+)\s+"
            + re.escape(after_label) + r":?\s+"
            r"ld\s+hl,\s+(" + label_ref + r")\s+"
            r"call\s+PrintText\s+"
            r"\7:?\s+"
            r"jp\s+TextScriptEnd\b",
            clean,
        )
        if not choice_match:
            return []
        prompt_label = choice_match.group(1)
        yes_label = choice_match.group(3)
        no_label = choice_match.group(5)
        shared_label = choice_match.group(6)
        post_label = choice_match.group(8)
        prompt_lines = script_label_lines(text_labels, text_refs, prompt_label)
        yes_lines = script_label_lines(text_labels, text_refs, yes_label) + script_label_lines(text_labels, text_refs, shared_label)
        no_lines = script_label_lines(text_labels, text_refs, no_label) + script_label_lines(text_labels, text_refs, shared_label)
        post_lines = script_label_lines(text_labels, text_refs, post_label)
        if not prompt_lines or not yes_lines or not no_lines or not post_lines:
            return []
        absent_actions = [
            {"type": "lockInput"},
            {
                "type": "choice",
                "promptLines": prompt_lines,
                "yesLines": yes_lines,
                "noLines": no_lines,
            },
            {"type": "unlockInput"},
        ]
    else:
        simple_match = re.search(
            r"\bld\s+a,\s+\[wBeatGymFlags\]\s+"
            r"bit\s+" + re.escape(badge_match.group(1)) + r",\s+a\s+"
            r"jr\s+nz,\s+" + re.escape(after_label) + r"\s+"
            r"ld\s+hl,\s+(" + label_ref + r")\s+"
            r"call\s+PrintText\s+"
            r"jr\s+(\.\w+)\s+"
            + re.escape(after_label) + r":?\s+"
            r"ld\s+hl,\s+(" + label_ref + r")\s+"
            r"call\s+PrintText\s+"
            r"\2:?\s+"
            r"jp\s+TextScriptEnd\b",
            clean,
        )
        if not simple_match:
            return []
        before_label = simple_match.group(1)
        post_label = simple_match.group(3)
        before_lines = script_label_lines(text_labels, text_refs, before_label)
        post_lines = script_label_lines(text_labels, text_refs, post_label)
        if not before_lines or not post_lines:
            return []
        absent_actions = [
            {"type": "lockInput"},
            {"type": "dialogue", "lines": before_lines},
            {"type": "unlockInput"},
        ]

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{block['label']}{suffix}Absent",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": block["label"],
            },
            "conditions": {"requiresBadgeAbsent": badge},
            "actions": absent_actions,
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{block['label']}{suffix}Set",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": block["label"],
            },
            "conditions": {"requiresBadge": badge},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": post_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
    ]


def badge_gated_gym_guide_candidates():
    candidates = []
    for script_path in sorted(SCRIPTS_DIR.glob("*.asm")):
        map_name = script_path.stem
        text_path = TEXT_DIR / f"{map_name}.asm"
        if not text_path.exists():
            continue
        script_content = script_path.read_text()
        text_pointers = parse_text_pointer_map(script_content)
        if not text_pointers:
            continue
        text_labels = extract_map_text_labels(map_name)
        for block in extract_label_blocks(script_content):
            candidates.extend(
                badge_gated_gym_guide_candidate_for_block(
                    map_name,
                    script_path,
                    text_path,
                    text_pointers,
                    text_labels,
                    block,
                )
            )
    return candidates


def flag_gated_dialogue_candidate_for_block(map_name, script_path, text_path, text_pointers, text_labels, block):
    if "Rival" in block["label"]:
        return []

    ir = extract_features(block["label"], block["raw"])
    features = ir["features"]
    if features["hasChoice"] or features["hasGiveItem"] or features["hasGivePokemon"] or features["hasMoneyCheck"]:
        return []
    if features["hasTrainerBattle"] or features["hasWildBattle"]:
        return []
    if ir["movementRefs"] or ir["objectRefs"] or ir["warpRefs"]:
        return []
    if len(ir["eventRefs"]) != 1 or ir["eventRefs"][0].get("op") != "CheckEvent":
        return []

    text_constant = text_pointers.get(block["label"], "")
    if not text_constant:
        return []

    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    branches = parse_flag_gated_dialogue(clean)
    if not branches:
        return []

    text_refs = local_text_ref_map(block["raw"])
    present_lines = script_label_lines(text_labels, text_refs, branches["present"])
    absent_lines = script_label_lines(text_labels, text_refs, branches["absent"])
    if not present_lines or not absent_lines:
        return []

    source = source_metadata(
        map_name,
        "flag_gated_dialogue_v1",
        script_path,
        text_path,
        [
            f"sourceBlock={block['label']}",
            "Generated only for a single CheckEvent selecting between two text branches.",
        ],
    )
    source["coveredLabels"] = [block["label"]]

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{block['label']}{pascal_from_constant(branches['event'])}Set",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": block["label"],
            },
            "conditions": {"requiresEvent": branches["event"]},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": present_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{block['label']}{pascal_from_constant(branches['event'])}Absent",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": block["label"],
            },
            "conditions": {"requiresEventAbsent": branches["event"]},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": absent_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
    ]


def parse_badge_or_event_gated_dialogue(clean):
    match = re.search(
        r"\bld\s+a,\s+\[wObtainedBadges\]\s*\n"
        r"\s*cp\s+~\(1\s*<<\s*(BIT_\w+)\)\s*\n"
        r"\s*ld\s+hl,\s+(\.\w+)\s*\n"
        r"\s*jr\s+z,\s+(\.\w+)\s*\n"
        r"\s*CheckEvent\s+(EVENT_\w+)\s*\n"
        r"\s*jr\s+nz,\s*\3\s*\n"
        r"\s*ld\s+hl,\s+(\.\w+)\s*\n"
        r"\s*\3:?\s*\n"
        r"\s*call\s+PrintText\s*\n"
        r"\s*jp\s+TextScriptEnd\b",
        clean,
    )
    if not match:
        return None
    badge = badge_name_from_bit(match.group(1))
    if not badge:
        return None
    return {
        "badge": badge,
        "present": match.group(2),
        "event": match.group(4),
        "absent": match.group(5),
    }


def badge_or_event_gated_dialogue_candidate_for_block(map_name, script_path, text_path, text_pointers, text_labels, block):
    ir = extract_features(block["label"], block["raw"])
    features = ir["features"]
    if features["hasChoice"] or features["hasGiveItem"] or features["hasGivePokemon"] or features["hasMoneyCheck"]:
        return []
    if features["hasTrainerBattle"] or features["hasWildBattle"]:
        return []
    if ir["movementRefs"] or ir["objectRefs"] or ir["warpRefs"]:
        return []
    if len(ir["eventRefs"]) != 1 or ir["eventRefs"][0].get("op") != "CheckEvent":
        return []

    text_constant = text_pointers.get(block["label"], "")
    if not text_constant:
        return []

    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    call_targets = re.findall(r"\bcall\s+(\w+)", clean)
    if any(target != "PrintText" for target in call_targets):
        return []
    if re.search(
        r"\b(?:SetEvent|ResetEvent|CheckAndSetEvent|CheckAndResetEvent|SetEventReuseHL|"
        r"ResetEventReuseHL|predef|farcall|EngageMapTrainer|InitBattleEnemyParameters|"
        r"SaveEndBattleTextPointers|DisableWaitingAfterTextDisplay|PlaySound|ReplaceTileBlock|"
        r"GateUpstairsScript_PrintIfFacingUp)\b",
        clean,
    ):
        return []

    branches = parse_badge_or_event_gated_dialogue(clean)
    if not branches:
        return []

    text_refs = local_text_ref_map(block["raw"])
    present_lines = local_lines(text_labels, text_refs, branches["present"])
    absent_lines = local_lines(text_labels, text_refs, branches["absent"])
    if not present_lines or not absent_lines:
        return []

    badge_suffix = pascal_badge_name(branches["badge"])
    event_suffix = pascal_from_constant(branches["event"])
    source = source_metadata(
        map_name,
        "badge_or_event_gated_dialogue_v1",
        script_path,
        text_path,
        [
            f"sourceBlock={block['label']}",
            "Generated for Red/Blue badge-bit OR event-flag dialogue branches.",
            "Uses multiple generated branches so downstream runtimes can preserve the original OR without order-dependent fallthrough.",
        ],
    )
    source["coveredLabels"] = [block["label"]]

    trigger = {
        "type": "npc_click",
        "label": text_constant,
        "sourceLabel": block["label"],
    }
    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{block['label']}{badge_suffix}Set",
            "trigger": trigger,
            "conditions": {"requiresBadge": branches["badge"]},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": present_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{block['label']}{event_suffix}Set{badge_suffix}Absent",
            "trigger": trigger,
            "conditions": {
                "requiresEvent": branches["event"],
                "requiresBadgesAbsent": [branches["badge"]],
            },
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": present_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{block['label']}{badge_suffix}{event_suffix}Absent",
            "trigger": trigger,
            "conditions": {
                "requiresEventsAbsent": [branches["event"]],
                "requiresBadgesAbsent": [branches["badge"]],
            },
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": absent_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
    ]


def badge_or_event_gated_dialogue_candidates():
    candidates = []
    for script_path in sorted(SCRIPTS_DIR.glob("*.asm")):
        map_name = script_path.stem
        text_path = TEXT_DIR / f"{map_name}.asm"
        script_content = script_path.read_text()
        text_pointers = parse_text_pointer_map(script_content)
        if not text_pointers:
            continue
        text_labels = extract_map_text_labels(map_name)
        for block in extract_label_blocks(script_content):
            candidates.extend(
                badge_or_event_gated_dialogue_candidate_for_block(
                    map_name,
                    script_path,
                    text_path,
                    text_pointers,
                    text_labels,
                    block,
                )
            )
    return candidates


def parse_facing_up_flag_gated_dialogue(clean):
    event_matches = re.findall(r"\bCheckEvent\s+(EVENT_\w+)", clean)
    if len(event_matches) != 1:
        return None
    event_flag = event_matches[0]
    if not re.search(
        r"\bld\s+a,\s+\[wSpritePlayerStateData1FacingDirection\]\s*\n"
        r"\s*cp\s+SPRITE_FACING_UP\s*\n"
        r"\s*jp\s+nz,\s+GateUpstairsScript_PrintIfFacingUp\b",
        clean,
    ):
        return None

    match = re.search(
        r"\bCheckEvent\s+" + re.escape(event_flag) +
        r"\s*\n\s*ld\s+hl,\s+(\.\w+)\s*\n\s*jr\s+(nz|z),\s*(\.\w+)"
        r"\s*\n\s*ld\s+hl,\s+(\.\w+)\s*\n\s*\3:?"
        r"\s*\n\s*call\s+PrintText\s*\n\s*jp\s+TextScriptEnd\b",
        clean,
    )
    if not match:
        return None
    branches = event_condition_branches(match.group(2), match.group(4), match.group(1))
    if branches:
        branches["event"] = event_flag
    return branches


def parse_facing_up_simple_dialogue(clean):
    match = re.search(
        r"\bld\s+hl,\s+(\.\w+)\s*\n\s*jp\s+GateUpstairsScript_PrintIfFacingUp\b",
        clean,
    )
    if not match:
        return None
    if re.search(r"\b(?:CheckEvent|SetEvent|ResetEvent|GiveItem|GivePokemon|predef|farcall|call)\b", clean):
        return None
    return match.group(1)


def facing_up_dialogue_candidate_for_block(map_name, script_path, text_path, text_pointers, text_labels, block):
    text_constant = text_pointers.get(block["label"], "")
    if not text_constant:
        return []

    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    text_refs = local_text_ref_map(block["raw"])

    branches = parse_facing_up_flag_gated_dialogue(clean)
    if branches:
        present_lines = local_lines(text_labels, text_refs, branches["present"])
        absent_lines = local_lines(text_labels, text_refs, branches["absent"])
        if not present_lines or not absent_lines:
            return []
        source = source_metadata(
            map_name,
            "facing_up_flag_gated_dialogue_v1",
            script_path,
            text_path,
            [
                f"sourceBlock={block['label']}",
                "Generated for Red/Blue GateUpstairsScript_PrintIfFacingUp binocular-style text.",
                "requiresPlayerFacing=UP preserves the original facing gate.",
            ],
        )
        source["coveredLabels"] = [block["label"]]
        return [
            {
                "version": 1,
                "kind": "scriptEventCandidate",
                "mapName": map_name,
                "scriptLabel": f"{block['label']}{pascal_from_constant(branches['event'])}SetFacingUp",
                "trigger": {
                    "type": "npc_click",
                    "label": text_constant,
                    "sourceLabel": block["label"],
                },
                "conditions": {"requiresEvent": branches["event"], "requiresPlayerFacing": "UP"},
                "actions": [
                    {"type": "lockInput"},
                    {"type": "dialogue", "lines": present_lines},
                    {"type": "unlockInput"},
                ],
                "source": source,
                "confidence": "adapter",
            },
            {
                "version": 1,
                "kind": "scriptEventCandidate",
                "mapName": map_name,
                "scriptLabel": f"{block['label']}{pascal_from_constant(branches['event'])}AbsentFacingUp",
                "trigger": {
                    "type": "npc_click",
                    "label": text_constant,
                    "sourceLabel": block["label"],
                },
                "conditions": {"requiresEventAbsent": branches["event"], "requiresPlayerFacing": "UP"},
                "actions": [
                    {"type": "lockInput"},
                    {"type": "dialogue", "lines": absent_lines},
                    {"type": "unlockInput"},
                ],
                "source": source,
                "confidence": "adapter",
            },
        ]

    local_label = parse_facing_up_simple_dialogue(clean)
    if not local_label:
        return []
    lines = local_lines(text_labels, text_refs, local_label)
    if not lines:
        return []
    source = source_metadata(
        map_name,
        "facing_up_dialogue_v1",
        script_path,
        text_path,
        [
            f"sourceBlock={block['label']}",
            "Generated for Red/Blue GateUpstairsScript_PrintIfFacingUp binocular-style text.",
            "requiresPlayerFacing=UP preserves the original facing gate.",
        ],
    )
    source["coveredLabels"] = [block["label"]]
    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{block['label']}FacingUp",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": block["label"],
            },
            "conditions": {"requiresPlayerFacing": "UP"},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        }
    ]


def facing_up_dialogue_candidates():
    candidates = []
    for script_path in sorted(SCRIPTS_DIR.glob("*.asm")):
        map_name = script_path.stem
        text_path = TEXT_DIR / f"{map_name}.asm"
        if not text_path.exists():
            continue
        script_content = script_path.read_text()
        text_pointers = parse_text_pointer_map(script_content)
        if not text_pointers:
            continue
        text_labels = extract_map_text_labels(map_name)
        for block in extract_label_blocks(script_content):
            candidates.extend(
                facing_up_dialogue_candidate_for_block(
                    map_name,
                    script_path,
                    text_path,
                    text_pointers,
                    text_labels,
                    block,
                )
            )
    return candidates


def fuchsia_fossil_sign_candidates():
    map_name = "FuchsiaCity"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    block = blocks_by_label.get("FuchsiaCityFossilSignText")
    if not block:
        return []

    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    required = [
        "CheckEvent EVENT_GOT_DOME_FOSSIL",
        "CheckEventReuseA EVENT_GOT_HELIX_FOSSIL",
        "DisplayPokedex",
    ]
    if any(snippet not in clean for snippet in required):
        return []

    text_pointers = parse_text_pointer_map(script_content)
    text_constant = text_pointers.get("FuchsiaCityFossilSignText")
    if not text_constant:
        return []

    text_labels = extract_map_text_labels(map_name)
    text_refs = local_text_ref_map(block["raw"])
    dome_lines = local_lines(text_labels, text_refs, ".OmanyteText")
    helix_lines = local_lines(text_labels, text_refs, ".KabutoText")
    unknown_lines = local_lines(text_labels, text_refs, ".UndeterminedText")
    if not dome_lines or not helix_lines or not unknown_lines:
        return []

    source = source_metadata(
        map_name,
        "fuchsia_fossil_sign_v1",
        script_path,
        text_path,
        [
            "sourceBlock=FuchsiaCityFossilSignText",
            "Generated for the two-fossil flag branch on the Fuchsia zoo fossil sign.",
            "DisplayPokedex side effects are recorded in diagnostics but are not modeled as generic script actions yet.",
        ],
    )
    source["coveredLabels"] = ["FuchsiaCityFossilSignText"]
    trigger = {
        "type": "npc_click",
        "label": text_constant,
        "sourceLabel": "FuchsiaCityFossilSignText",
    }
    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "FuchsiaCityFossilSignDomeFossil",
            "trigger": trigger,
            "conditions": {"requiresEvent": "EVENT_GOT_DOME_FOSSIL"},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": dome_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "FuchsiaCityFossilSignHelixFossil",
            "trigger": trigger,
            "conditions": {
                "requiresEvent": "EVENT_GOT_HELIX_FOSSIL",
                "requiresEventAbsent": "EVENT_GOT_DOME_FOSSIL",
            },
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": helix_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "FuchsiaCityFossilSignUndetermined",
            "trigger": trigger,
            "conditions": {"requiresEventAbsent": "EVENT_GOT_DOME_FOSSIL"},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": unknown_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
    ]


def flag_gated_dialogue_candidates():
    candidates = []
    for script_path in sorted(SCRIPTS_DIR.glob("*.asm")):
        map_name = script_path.stem
        text_path = TEXT_DIR / f"{map_name}.asm"
        script_content = script_path.read_text()
        text_pointers = parse_text_pointer_map(script_content)
        if not text_pointers:
            continue
        text_labels = extract_map_text_labels(map_name)
        for block in extract_label_blocks(script_content):
            candidates.extend(
                flag_gated_dialogue_candidate_for_block(
                    map_name,
                    script_path,
                    text_path,
                    text_pointers,
                    text_labels,
                    block,
                )
            )
    return candidates


def fan_boast_toggle_candidate_for_block(map_name, script_path, text_path, text_pointers, text_labels, block):
    text_constant = text_pointers.get(block["label"], "")
    if not text_constant:
        return []

    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    match = re.search(
        r"\bCheckEvent\s+(EVENT_\w+)\s*\n"
        r"\s*jr\s+nz,\s+(\.\w+)\s*\n"
        r"\s*ld\s+hl,\s+(\.\w+)\s*\n"
        r"\s*call\s+PrintText\s*\n"
        r"\s*SetEvent\s+(EVENT_\w+)\s*\n"
        r"\s*jr\s+(\.\w+)\s*\n"
        r"\s*\2:?\s*\n"
        r"\s*ld\s+hl,\s+(\.\w+)\s*\n"
        r"\s*call\s+PrintText\s*\n"
        r"\s*ResetEvent\s+\1\s*\n"
        r"\s*\5:?\s*\n"
        r"\s*jp\s+TextScriptEnd\b",
        clean,
    )
    if not match:
        return []

    own_flag = match.group(1)
    normal_label = match.group(3)
    other_flag = match.group(4)
    better_label = match.group(6)
    text_refs = local_text_ref_map(block["raw"])
    normal_lines = local_lines(text_labels, text_refs, normal_label)
    better_lines = local_lines(text_labels, text_refs, better_label)
    if not normal_lines or not better_lines:
        return []

    source = source_metadata(
        map_name,
        "fan_boast_toggle_v1",
        script_path,
        text_path,
        [
            f"sourceBlock={block['label']}",
            "Generated from the Pokemon Fan Club paired boast toggle state machine.",
            "Normal branch sets the other fan's boast flag; better branch resets this fan's boast flag.",
        ],
    )
    source["coveredLabels"] = [block["label"]]
    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{block['label']}Normal",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": block["label"],
            },
            "conditions": {"requiresEventAbsent": own_flag},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": normal_lines},
                {"type": "setEvent", "event": other_flag},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{block['label']}MineIsBetter",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": block["label"],
            },
            "conditions": {"requiresEvent": own_flag},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": better_lines},
                {"type": "resetEvent", "event": own_flag},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
    ]


def fan_boast_toggle_candidates():
    map_name = "PokemonFanClub"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []
    script_content = script_path.read_text()
    text_pointers = parse_text_pointer_map(script_content)
    text_labels = extract_map_text_labels(map_name)
    candidates = []
    for block in extract_label_blocks(script_content):
        candidates.extend(
            fan_boast_toggle_candidate_for_block(
                map_name,
                script_path,
                text_path,
                text_pointers,
                text_labels,
                block,
            )
        )
    return candidates


def simple_flag_side_effect_candidate_for_block(map_name, script_path, text_path, text_pointers, text_labels, block):
    text_constant = text_pointers.get(block["label"], "")
    if not text_constant:
        return []

    ir = extract_features(block["label"], block["raw"])
    features = ir["features"]
    if features["hasChoice"] or features["hasGiveItem"] or features["hasGivePokemon"] or features["hasMoneyCheck"]:
        return []
    if features["hasTrainerBattle"] or features["hasWildBattle"]:
        return []
    if ir["movementRefs"] or ir["objectRefs"] or ir["warpRefs"]:
        return []
    if len(ir["eventRefs"]) != 1 or ir["eventRefs"][0]["op"] not in {"SetEvent", "ResetEvent"}:
        return []

    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    if re.search(r"\b(?:CheckEvent|CheckAndSetEvent|CheckAndResetEvent|YesNoChoice|GiveItem|GivePokemon)\b", clean):
        return []
    if re.search(r"\b(?:predef|predef_jump)\s+(?!ReplaceTileBlock\b)\w+", clean):
        return []
    if re.search(r"\b(?:call|farcall)\s+(?!PrintText\b|WaitForSoundToFinish\b|PlaySound\b)\w+", clean):
        return []
    if re.search(r"\b(?:jp|jr)\s+(?!TextScriptEnd\b)\w+", clean):
        return []

    printed_labels = re.findall(r"\bld\s+hl,\s+(\.\w+)\s*\n\s*call\s+PrintText\b", clean)
    if len(printed_labels) != 1:
        return []
    text_refs = local_text_ref_map(block["raw"])
    lines = local_lines(text_labels, text_refs, printed_labels[0])
    if not lines:
        return []

    event_op = ir["eventRefs"][0]["op"]
    event_flag = ir["eventRefs"][0]["flag"]
    action_type = "setEvent" if event_op == "SetEvent" else "resetEvent"
    source_notes = [
        f"sourceBlock={block['label']}",
        "Generated from a single-text script with one event-flag side effect.",
    ]
    replacement_match = re.search(
        r"\bld\s+a,\s+(\$[0-9a-fA-F]+|\d+)\s+"
        r"ld\s+\[wNewTileBlockID\],\s+a\s+"
        r"lb\s+bc,\s+(\d+),\s+(\d+)\s+"
        r"predef(?:_jump)?\s+ReplaceTileBlock\b",
        clean,
    )
    if replacement_match:
        source_notes.append(
            "This script also performs ReplaceTileBlock; downstream runtimes should pair it with generated event tile overrides when available."
        )

    source = source_metadata(
        map_name,
        "simple_flag_side_effect_dialogue_v1",
        script_path,
        text_path,
        source_notes,
    )
    source["coveredLabels"] = [block["label"]]
    if replacement_match:
        block_id, block_y, block_x = replacement_match.groups()
        source["tileReplacement"] = {
            "blockX": int(block_x),
            "blockY": int(block_y),
            "blockId": asm_literal_to_int(block_id),
            "event": event_flag,
        }

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{block['label']}{pascal_from_constant(event_flag)}",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": block["label"],
            },
            "conditions": {},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": lines},
                {"type": action_type, "event": event_flag},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        }
    ]


def simple_flag_side_effect_dialogue_candidates():
    candidates = []
    for script_path in sorted(SCRIPTS_DIR.glob("*.asm")):
        map_name = script_path.stem
        text_path = TEXT_DIR / f"{map_name}.asm"
        if not text_path.exists():
            continue
        script_content = script_path.read_text()
        text_pointers = parse_text_pointer_map(script_content)
        if not text_pointers:
            continue
        text_labels = extract_map_text_labels(map_name)
        for block in extract_label_blocks(script_content):
            candidates.extend(
                simple_flag_side_effect_candidate_for_block(
                    map_name,
                    script_path,
                    text_path,
                    text_pointers,
                    text_labels,
                    block,
                )
            )
    return candidates


def script_label_base(label):
    return re.sub(r"[^A-Za-z0-9]+", "", label)


def simple_play_cry_candidate_for_block(
    map_name,
    script_path,
    text_path,
    text_pointers,
    text_labels,
    cry_helper_labels,
    block,
):
    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    text_constant = text_pointers.get(block["label"])
    if not text_constant:
        return []

    extra_label_match = re.search(r"\bld\s+hl,\s+(\.\w+)\s*\n\s*ret\b", clean)
    text_refs = ordered_text_refs(block["raw"])
    if len(text_refs) == 0 or len(text_refs) > 2:
        return []
    if len(text_refs) == 2 and not extra_label_match:
        return []

    ir = extract_features(block["label"], block["raw"])
    features = ir["features"]
    if (
        features["hasChoice"]
        or features["hasGiveItem"]
        or features["hasGivePokemon"]
        or features["hasMoneyCheck"]
        or features["hasTrainerBattle"]
        or features["hasWildBattle"]
    ):
        return []
    if ir["eventRefs"] or ir["movementRefs"] or ir["objectRefs"] or ir["warpRefs"]:
        return []

    call_targets = re.findall(r"\bcall\s+(\w+)", clean)
    allowed_calls = {"PrintText", "PlayCry", "WaitForSoundToFinish"}
    if any(target not in allowed_calls for target in call_targets):
        return []
    if re.search(r"\b(?:jr|predef|farcall|DisplayTextID|YesNoChoice)\b", clean):
        return []

    extra_lines = []
    if re.search(r"\bret\b", clean):
        if not extra_label_match:
            return []
        text_ref_map = local_text_ref_map(block["raw"])
        extra_lines = local_lines(text_labels, text_ref_map, extra_label_match.group(1))
        if not extra_lines:
            return []

    pokemon_constant = ""
    cry_match = re.search(r"\bld\s+a,\s+([A-Z0-9_]+)\s*\n\s*call\s+PlayCry\b", clean)
    if cry_match:
        pokemon_constant = cry_match.group(1)
    else:
        helper_match = re.search(r"\bld\s+a,\s+([A-Z0-9_]+)\s*\n\s*jp\s+(\w+)\b", clean)
        if not helper_match or helper_match.group(2) not in cry_helper_labels:
            return []
        pokemon_constant = helper_match.group(1)

    helper_jumps = set(re.findall(r"\bjp\s+(\w+)\b", clean)) - {"TextScriptEnd"}
    if helper_jumps - cry_helper_labels:
        return []

    if not pokemon_constant:
        return []

    source = source_metadata(
        map_name,
        "simple_play_cry_text_v1",
        script_path,
        text_path,
        [
            "Generated for direct text_asm blocks that print one text label and play one Pokemon cry.",
            "Branching/stateful PlayCry scripts are intentionally left for more specific adapters.",
        ],
    )
    source["coveredLabels"] = [block["label"]]
    actions = [
        {"type": "lockInput"},
        {"type": "dialogueText", "textConstant": text_constant},
        {"type": "playCry", "pokemonConstant": pokemon_constant},
    ]
    if extra_lines:
        actions.append({"type": "dialogue", "lines": extra_lines})
    actions.append({"type": "unlockInput"})

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{script_label_base(block['label'])}Cry",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": block["label"],
            },
            "conditions": {},
            "actions": actions,
            "source": source,
            "confidence": "adapter",
        }
    ]


def simple_play_cry_text_candidates():
    candidates = []
    for script_path in sorted(SCRIPTS_DIR.glob("*.asm")):
        map_name = script_path.stem
        text_path = TEXT_DIR / f"{map_name}.asm"
        if not text_path.exists():
            continue
        script_content = script_path.read_text()
        text_pointers = parse_text_pointer_map(script_content)
        if not text_pointers:
            continue
        text_labels = extract_map_text_labels(map_name)
        cry_helper_labels = {
            block["label"]
            for block in extract_label_blocks(script_content)
            if "call PlayCry" in block["raw"]
            and "text_far" not in block["raw"]
            and "text_asm" not in block["raw"]
        }
        for block in extract_label_blocks(script_content):
            candidates.extend(
                simple_play_cry_candidate_for_block(
                    map_name,
                    script_path,
                    text_path,
                    text_pointers,
                    text_labels,
                    cry_helper_labels,
                    block,
                )
            )
    return candidates


def pure_flag_map_script_candidate_for_block(map_name, script_path, text_path, block):
    ir = extract_features(block["label"], block["raw"])
    features = ir["features"]
    if features["hasChoice"] or features["hasGiveItem"] or features["hasGivePokemon"] or features["hasMoneyCheck"]:
        return []
    if features["hasTrainerBattle"] or features["hasWildBattle"]:
        return []
    if ir["textRefs"] or ir["movementRefs"] or ir["objectRefs"] or ir["warpRefs"]:
        return []
    if not ir["eventRefs"]:
        return []

    event_refs = []
    for ref in ir["eventRefs"]:
        if "BOULDER_ON_SWITCH" in ref["flag"]:
            return []
        if ref["op"] == "SetEvent":
            event_refs.append({"type": "setEvent", "event": ref["flag"]})
        elif ref["op"] in {"ResetEvent", "ResetEvents"}:
            event_refs.append({"type": "resetEvent", "event": ref["flag"]})
        else:
            return []

    clean_lines = [strip_comment(line) for line in block["raw"].splitlines()]
    for line in clean_lines:
        if not line or line == f"{block['label']}:":
            continue
        if line == "call EnableAutoTextBoxDrawing":
            continue
        if re.fullmatch(r"SetEvent\s+EVENT_\w+", line):
            continue
        if re.fullmatch(r"ResetEvent\s+EVENT_\w+", line):
            continue
        if re.fullmatch(r"ResetEvents\s+EVENT_\w+(?:,\s*EVENT_\w+)*", line):
            continue
        if line == "ld hl, wCurrentMapScriptFlags":
            continue
        if re.fullmatch(r"set\s+BIT_CUR_MAP_LOADED_\d,\s+\[hl\]", line):
            continue
        if re.fullmatch(r"bit\s+BIT_CUR_MAP_LOADED_\d,\s+\[hl\]", line):
            continue
        if re.fullmatch(r"res\s+BIT_CUR_MAP_LOADED_\d,\s+\[hl\]", line):
            continue
        if re.fullmatch(r"ld\s+hl,\s+\w+_ScriptPointers", line):
            continue
        if re.fullmatch(r"ld\s+a,\s+\[w\w+CurScript\]", line):
            continue
        if line == "jp CallFunctionInTable":
            continue
        if re.fullmatch(r"ld\s+a,\s+SCRIPT_\w+", line):
            continue
        if re.fullmatch(r"ld\s+\[w\w+CurScript\],\s+a", line):
            continue
        if line == "ret":
            continue
        return []

    conditions = {}
    if event_refs and all(action["type"] == "setEvent" for action in event_refs):
        conditions["requiresEventAbsent"] = event_refs[0]["event"]

    source = source_metadata(
        map_name,
        "pure_flag_map_script_v1",
        script_path,
        text_path,
        [
            f"sourceBlock={block['label']}",
            "Generated for map scripts whose source behavior is limited to event flag side effects.",
            "Map script state-register writes are treated as source control flow and omitted from neutral downstream actions.",
        ],
    )
    source["coveredLabels"] = [block["label"]]

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{script_label_base(block['label'])}FlagSideEffects",
            "trigger": {
                "type": "map_script",
                "label": block["label"],
                "sourceLabel": block["label"],
            },
            "conditions": conditions,
            "actions": event_refs,
            "source": source,
            "confidence": "adapter",
        }
    ]


def pure_flag_map_script_candidates():
    candidates = []
    for script_path in sorted(SCRIPTS_DIR.glob("*.asm")):
        map_name = script_path.stem
        text_path = TEXT_DIR / f"{map_name}.asm"
        script_content = script_path.read_text()
        for block in extract_label_blocks(script_content):
            candidates.extend(pure_flag_map_script_candidate_for_block(map_name, script_path, text_path, block))
    return candidates


def conditional_flag_map_script_candidate_for_block(map_name, script_path, text_path, block):
    ir = extract_features(block["label"], block["raw"])
    features = ir["features"]
    if features["hasChoice"] or features["hasGiveItem"] or features["hasGivePokemon"] or features["hasMoneyCheck"]:
        return []
    if features["hasTrainerBattle"] or features["hasWildBattle"]:
        return []
    if ir["textRefs"] or ir["movementRefs"] or ir["objectRefs"] or ir["warpRefs"]:
        return []

    check_refs = [ref for ref in ir["eventRefs"] if ref["op"] == "CheckEvent"]
    set_refs = [ref for ref in ir["eventRefs"] if ref["op"] == "SetEvent"]
    if len(check_refs) != 1 or len(set_refs) != 1 or len(ir["eventRefs"]) != 2:
        return []

    source_event = check_refs[0]["flag"]
    target_event = set_refs[0]["flag"]
    clean_lines = [
        strip_comment(line)
        for line in block["raw"].splitlines()
        if strip_comment(line)
    ]
    body = [line for line in clean_lines if line != f"{block['label']}:"]
    if len(body) < 4:
        return []
    branch_match = re.fullmatch(r"jr\s+z,\s+(\.\w+)", body[1])
    if body[0] != f"CheckEvent {source_event}" or not branch_match or body[2] != f"SetEvent {target_event}":
        return []
    branch_label = branch_match.group(1)
    if body[3] not in {branch_label, f"{branch_label}:"}:
        return []

    for line in body[4:]:
        if line == "call EnableAutoTextBoxDrawing":
            continue
        if re.fullmatch(r"ld\s+hl,\s+\w+_ScriptPointers", line):
            continue
        if re.fullmatch(r"ld\s+a,\s+\[w\w+CurScript\]", line):
            continue
        if line == "jp CallFunctionInTable":
            continue
        if line == "ret":
            continue
        return []

    source = source_metadata(
        map_name,
        "conditional_flag_map_script_v1",
        script_path,
        text_path,
        [
            f"sourceBlock={block['label']}",
            f"requiresEvent={source_event}",
            f"setsEvent={target_event}",
            "Generated for map scripts that mirror one source flag into one downstream event before normal script dispatch.",
        ],
    )
    source["coveredLabels"] = [block["label"]]

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{script_label_base(block['label'])}{pascal_from_constant(target_event)}Set",
            "trigger": {
                "type": "map_script",
                "label": block["label"],
                "sourceLabel": block["label"],
            },
            "conditions": {
                "requiresEvent": source_event,
                "requiresEventAbsent": target_event,
            },
            "actions": [{"type": "setEvent", "event": target_event}],
            "source": source,
            "confidence": "adapter",
        }
    ]


def conditional_flag_map_script_candidates():
    candidates = []
    for script_path in sorted(SCRIPTS_DIR.glob("*.asm")):
        map_name = script_path.stem
        text_path = TEXT_DIR / f"{map_name}.asm"
        script_content = script_path.read_text()
        for block in extract_label_blocks(script_content):
            candidates.extend(conditional_flag_map_script_candidate_for_block(map_name, script_path, text_path, block))
    return candidates


def one_shot_object_visibility_map_script_candidate_for_block(map_name, script_path, text_path, block):
    ir = extract_features(block["label"], block["raw"])
    features = ir["features"]
    if features["hasChoice"] or features["hasGiveItem"] or features["hasGivePokemon"] or features["hasMoneyCheck"]:
        return []
    if features["hasTrainerBattle"] or features["hasWildBattle"]:
        return []
    if ir["textRefs"] or ir["movementRefs"] or ir["warpRefs"]:
        return []
    if not ir["objectRefs"]:
        return []

    check_refs = [ref for ref in ir["eventRefs"] if ref["op"] == "CheckEvent"]
    check_and_set_refs = [ref for ref in ir["eventRefs"] if ref["op"] == "CheckAndSetEvent"]
    if len(check_refs) != 1 or len(check_and_set_refs) != 1 or len(ir["eventRefs"]) != 2:
        return []

    source_event = check_refs[0]["flag"]
    idempotence_event = check_and_set_refs[0]["flag"]
    visibility_actions = missable_object_actions("\n".join(strip_comment(line) for line in block["raw"].splitlines()))
    if not visibility_actions:
        return []

    clean_lines = [
        strip_comment(line)
        for line in block["raw"].splitlines()
        if strip_comment(line)
    ]
    body = [line for line in clean_lines if line != f"{block['label']}:"]
    allowed_patterns = [
        rf"CheckEvent\s+{source_event}",
        r"ret\s+z",
        rf"CheckAndSetEvent\s+{idempotence_event}",
        r"ret\s+nz",
        r"ld\s+a,\s+HS_[A-Z0-9_]+",
        r"ld\s+\[wMissableObjectIndex\],\s+a",
        r"predef(?:_jump)?\s+(?:HideObject|ShowObject)",
        r"call\s+EnableAutoTextBoxDrawing",
        r"ld\s+hl,\s+\w+_ScriptPointers",
        r"ld\s+a,\s+\[w\w+CurScript\]",
        r"jp\s+CallFunctionInTable",
        r"ret",
    ]
    for line in body:
        if any(re.fullmatch(pattern, line) for pattern in allowed_patterns):
            continue
        return []

    actions = [{"type": "setEvent", "event": idempotence_event}]
    actions.extend(visibility_actions)
    source = source_metadata(
        map_name,
        "one_shot_object_visibility_map_script_v1",
        script_path,
        text_path,
        [
            f"sourceBlock={block['label']}",
            f"requiresEvent={source_event}",
            f"setsEvent={idempotence_event}",
            "Generated for one-shot map-load scripts that mirror a source flag into missable-object visibility state.",
            "Multi-branch visibility scripts remain diagnostics until the neutral condition model can represent their full flag logic.",
        ],
    )
    source["coveredLabels"] = [block["label"]]

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{script_label_base(block['label'])}{pascal_from_constant(idempotence_event)}Visibility",
            "trigger": {
                "type": "map_script",
                "label": block["label"],
                "sourceLabel": block["label"],
            },
            "conditions": {
                "requiresEvent": source_event,
                "requiresEventAbsent": idempotence_event,
            },
            "actions": actions,
            "source": source,
            "confidence": "adapter",
        }
    ]


def one_shot_object_visibility_map_script_candidates():
    candidates = []
    for script_path in sorted(SCRIPTS_DIR.glob("*.asm")):
        map_name = script_path.stem
        text_path = TEXT_DIR / f"{map_name}.asm"
        script_content = script_path.read_text()
        for block in extract_label_blocks(script_content):
            candidates.extend(
                one_shot_object_visibility_map_script_candidate_for_block(
                    map_name,
                    script_path,
                    text_path,
                    block,
                )
            )
    return candidates


def fishing_guru_candidate_for_block(map_name, script_path, text_path, text_constant, block, text_labels):
    raw = block["raw"]
    clean = "\n".join(strip_comment(line) for line in raw.splitlines())
    bit_match = re.search(r"\bbit\s+(BIT_GOT_[A-Z0-9_]+_ROD),\s*a", clean)
    item_match = re.search(r"\blb\s+bc,\s+(OLD_ROD|GOOD_ROD|SUPER_ROD),\s+1\b", clean)
    if not bit_match or not item_match or "YesNoChoice" not in clean or "GiveItem" not in clean:
        return []

    prompt_match = re.search(r"\bld\s+hl,\s+(\.\w+)\s*\n\s*call\s+PrintText\s*\n\s*call\s+YesNoChoice\b", clean)
    got_match = re.search(r"(?m)^\s*\.got_\w+:?\s*\n\s*ld\s+hl,\s+(\.\w+)", clean)
    refused_match = re.search(r"(?m)^\s*\.refused:?\s*\n\s*ld\s+hl,\s+(\.\w+)", clean)
    gift_match = re.search(r"\bset\s+" + re.escape(bit_match.group(1)) + r",\s*\[hl\]\s*\n\s*ld\s+hl,\s+(\.\w+)", clean)
    if not prompt_match or not got_match or not refused_match or not gift_match:
        return []

    text_refs = local_text_ref_map(raw)
    prompt_lines = local_lines(text_labels, text_refs, prompt_match.group(1))
    got_lines = local_lines(text_labels, text_refs, got_match.group(1))
    refused_lines = local_lines(text_labels, text_refs, refused_match.group(1))
    gift_lines = local_lines(text_labels, text_refs, gift_match.group(1))
    item = item_match.group(1)
    gift_lines = hydrate_received_item_lines(gift_lines, item)
    source_label = block["label"]
    source = source_metadata(
        map_name,
        "fishing_guru_rod_gift_v1",
        script_path,
        text_path,
        [
            f"sourceBlock={source_label}",
            f"statusBit={bit_match.group(1)}",
            "Generated from Fishing Guru rod gift Yes/No state machine.",
        ],
    )
    source["coveredLabels"] = [source_label]

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": source_label + "AlreadyGot",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": source_label,
            },
            "conditions": {"requiresItem": item},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "speaker": "FISHING GURU", "lines": got_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": source_label + "Gift",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": source_label,
            },
            "conditions": {"requiresItemAbsent": item},
            "actions": [
                {"type": "lockInput"},
                {
                    "type": "choice",
                    "speaker": "FISHING GURU",
                    "promptLines": prompt_lines,
                    "noLines": refused_lines,
                },
                {"type": "giveItem", "itemConstant": item, "quantity": 1},
                {"type": "dialogue", "lines": gift_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
    ]


def fishing_guru_rod_candidates():
    candidates = []
    for script_path in sorted(SCRIPTS_DIR.glob("*.asm")):
        map_name = script_path.stem
        script_content = script_path.read_text()
        text_path = TEXT_DIR / f"{map_name}.asm"
        text_labels = extract_map_text_labels(map_name)
        text_pointers = parse_text_pointer_map(script_content)
        for block in extract_label_blocks(script_content):
            text_constant = text_pointers.get(block["label"], "")
            if not text_constant:
                continue
            candidates.extend(
                fishing_guru_candidate_for_block(
                    map_name,
                    script_path,
                    text_path,
                    text_constant,
                    block,
                    text_labels,
                )
            )
    return candidates


def pokemon_fan_club_chairman_candidates():
    map_name = "PokemonFanClub"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    text_labels = extract_map_text_labels(map_name)
    text_pointers = parse_text_pointer_map(script_content)
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    block = blocks_by_label.get("PokemonFanClubChairmanText")
    if not block:
        return []
    raw = block["raw"]
    clean = "\n".join(strip_comment(line) for line in raw.splitlines())
    if (
        "PokemonFanClub_CheckBikeInBag" not in clean
        or "YesNoChoice" not in clean
        or "GiveItem" not in clean
        or "SetEvent EVENT_GOT_BIKE_VOUCHER" not in clean
    ):
        return []

    item_match = re.search(r"\blb\s+bc,\s+(BIKE_VOUCHER),\s+1\b", clean)
    if not item_match:
        return []

    text_refs = local_text_ref_map(raw)
    source_label = block["label"]
    text_constant = text_pointers.get(source_label, "")
    if not text_constant:
        return []
    source = source_metadata(
        map_name,
        "pokemon_fan_club_chairman_v1",
        script_path,
        text_path,
        [
            f"sourceBlock={source_label}",
            "Generated from PokemonFanClub_CheckBikeInBag plus Bike Voucher Yes/No state machine.",
        ],
    )
    source["coveredLabels"] = [source_label, "PokemonFanClub_CheckBikeInBag"]

    item = item_match.group(1)
    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": source_label + "AlreadyGot",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": source_label,
            },
            "conditions": {"requiresEvent": "EVENT_GOT_BIKE_VOUCHER"},
            "actions": [
                {"type": "lockInput"},
                {
                    "type": "dialogue",
                    "speaker": "CHAIRMAN",
                    "lines": local_lines(text_labels, text_refs, ".FinalText"),
                },
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": source_label + "HasVoucher",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": source_label,
            },
            "conditions": {"requiresItem": item},
            "actions": [
                {"type": "lockInput"},
                {
                    "type": "dialogue",
                    "speaker": "CHAIRMAN",
                    "lines": local_lines(text_labels, text_refs, ".FinalText"),
                },
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": source_label + "Gift",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": source_label,
            },
            "conditions": {
                "requiresEventAbsent": "EVENT_GOT_BIKE_VOUCHER",
                "requiresItemAbsent": item,
            },
            "actions": [
                {"type": "lockInput"},
                {
                    "type": "choice",
                    "speaker": "CHAIRMAN",
                    "promptLines": local_lines(text_labels, text_refs, ".IntroText"),
                    "yesLines": local_lines(text_labels, text_refs, ".StoryText"),
                    "noLines": local_lines(text_labels, text_refs, ".NoStoryText"),
                },
                {"type": "giveItem", "itemConstant": item, "quantity": 1},
                {
                    "type": "dialogue",
                    "lines": hydrate_received_item_lines(
                        local_lines(text_labels, text_refs, ".BikeVoucherText"),
                        item,
                    ),
                },
                {"type": "setEvent", "event": "EVENT_GOT_BIKE_VOUCHER"},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
    ]


def fighting_dojo_reward_candidates():
    map_name = "FightingDojo"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    text_labels = extract_map_text_labels(map_name)
    text_pointers = parse_text_pointer_map(script_content)
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    greedy_block = blocks_by_label.get("FightingDojoBetterNotGetGreedyText")
    greedy_lines = lines_for_labels(text_labels, ordered_text_refs(greedy_block["raw"] if greedy_block else ""))
    if not greedy_lines:
        return []

    source = source_metadata(
        map_name,
        "fighting_dojo_reward_v1",
        script_path,
        text_path,
        [
            "Generated from Fighting Dojo prize Poke Ball Yes/No + GivePokemon state machines.",
            "Uses EVENT_GOT_FIGHTING_DOJO_POKEMON as the downstream mutual-exclusion flag.",
        ],
    )

    candidates = []
    for label, species, object_text, script_label in [
        (
            "FightingDojoHitmonleePokeBallText",
            "HITMONLEE",
            "TEXT_FIGHTINGDOJO_HITMONLEE_POKE_BALL",
            "FightingDojoHitmonleeChoice",
        ),
        (
            "FightingDojoHitmonchanPokeBallText",
            "HITMONCHAN",
            "TEXT_FIGHTINGDOJO_HITMONCHAN_POKE_BALL",
            "FightingDojoHitmonchanChoice",
        ),
    ]:
        block = blocks_by_label.get(label)
        text_constant = text_pointers.get(label, object_text)
        if not block or text_constant != object_text:
            continue
        clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
        if (
            "CheckEitherEventSet EVENT_GOT_HITMONLEE, EVENT_GOT_HITMONCHAN" not in clean
            or f"ld a, {species}" not in clean
            or "call YesNoChoice" not in clean
            or "call GivePokemon" not in clean
            or "predef HideObject" not in clean
            or f"SetEvents EVENT_GOT_{species}, EVENT_DEFEATED_FIGHTING_DOJO" not in clean
        ):
            continue
        level_match = re.search(r"\bld\s+c,\s+(\d+)\s*\n\s*call\s+GivePokemon\b", clean)
        if not level_match:
            continue

        prompt_lines = local_lines(text_labels, local_text_ref_map(block["raw"]), ".Text")
        if not prompt_lines:
            continue

        branch_source = dict(source)
        branch_source["coveredLabels"] = [label, "FightingDojoBetterNotGetGreedyText"]
        candidates.extend(
            [
                {
                    "version": 1,
                    "kind": "scriptEventCandidate",
                    "mapName": map_name,
                    "scriptLabel": script_label,
                    "trigger": {
                        "type": "npc_click",
                        "label": text_constant,
                        "sourceLabel": label,
                    },
                    "conditions": {
                        "requiresEvent": "EVENT_BEAT_KARATE_MASTER",
                        "requiresEventAbsent": "EVENT_GOT_FIGHTING_DOJO_POKEMON",
                    },
                    "actions": [
                        {"type": "lockInput"},
                        {"type": "choice", "promptLines": prompt_lines},
                        {
                            "type": "givePokemon",
                            "pokemonConstant": species,
                            "level": int(level_match.group(1)),
                        },
                        {"type": "hideObject", "textConstant": text_constant},
                        {"type": "setEvent", "event": "EVENT_GOT_FIGHTING_DOJO_POKEMON"},
                        {"type": "setEvent", "event": f"EVENT_GOT_{species}"},
                        {"type": "setEvent", "event": "EVENT_DEFEATED_FIGHTING_DOJO"},
                        {"type": "unlockInput"},
                    ],
                    "source": branch_source,
                    "confidence": "adapter",
                },
                {
                    "version": 1,
                    "kind": "scriptEventCandidate",
                    "mapName": map_name,
                    "scriptLabel": script_label.replace("Choice", "AlreadyGot"),
                    "trigger": {
                        "type": "npc_click",
                        "label": text_constant,
                        "sourceLabel": label,
                    },
                    "conditions": {"requiresEvent": "EVENT_GOT_FIGHTING_DOJO_POKEMON"},
                    "actions": [
                        {"type": "lockInput"},
                        {"type": "dialogue", "lines": greedy_lines},
                        {"type": "unlockInput"},
                    ],
                    "source": branch_source,
                    "confidence": "adapter",
                },
            ]
        )

    return candidates


def fighting_dojo_karate_master_candidates():
    map_name = "FightingDojo"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    text_labels = extract_map_text_labels(map_name)
    text_pointers = parse_text_pointer_map(script_content)
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}

    default_block = blocks_by_label.get("FightingDojoDefaultScript")
    post_battle_block = blocks_by_label.get("FightingDojoKarateMasterPostBattleScript")
    master_block = blocks_by_label.get("FightingDojoKarateMasterText")
    if not default_block or not post_battle_block or not master_block:
        return []

    text_constant = text_pointers.get("FightingDojoKarateMasterText")
    if text_constant != "TEXT_FIGHTINGDOJO_KARATE_MASTER":
        return []

    trainer_obj = trainer_object_for_text(map_name, text_constant)
    if not trainer_obj or trainer_obj["payload"] != "OPP_BLACKBELT" or trainer_obj["level"] != 1:
        return []

    default_clean = "\n".join(strip_comment(line) for line in default_block["raw"].splitlines())
    master_clean = "\n".join(strip_comment(line) for line in master_block["raw"].splitlines())
    post_clean = "\n".join(strip_comment(line) for line in post_battle_block["raw"].splitlines())
    required = [
        (default_clean, "ld a, [wYCoord]\ncp 3"),
        (default_clean, "ld a, [wXCoord]\ncp 4"),
        (default_clean, "ld a, TEXT_FIGHTINGDOJO_KARATE_MASTER"),
        (master_clean, "CheckEvent EVENT_DEFEATED_FIGHTING_DOJO"),
        (master_clean, "CheckEventReuseA EVENT_BEAT_KARATE_MASTER"),
        (master_clean, "call EngageMapTrainer"),
        (master_clean, "SCRIPT_FIGHTINGDOJO_KARATE_MASTER_POST_BATTLE"),
        (post_clean, "SetEventRange EVENT_BEAT_KARATE_MASTER, EVENT_BEAT_FIGHTING_DOJO_TRAINER_3"),
        (post_clean, "TEXT_FIGHTINGDOJO_KARATE_MASTER_I_WILL_GIVE_YOU_A_POKEMON"),
    ]
    if any(snippet not in clean for clean, snippet in required):
        return []

    local_refs = local_text_ref_map(master_block["raw"])
    battle_lines = local_lines(text_labels, local_refs, ".Text")
    defeated_lines = local_lines(text_labels, local_refs, ".DefeatedText")
    reward_lines = local_lines(text_labels, local_refs, ".IWillGiveYouAPokemonText")
    stay_lines = local_lines(text_labels, local_refs, ".StayAndTrainWithUsText")
    if not battle_lines or not defeated_lines or not reward_lines or not stay_lines:
        return []

    map_id = source_map_id(map_name)
    if not map_id:
        return []

    source = source_metadata(
        map_name,
        "fighting_dojo_karate_master_v1",
        script_path,
        text_path,
        [
            "Generated from the Fighting Dojo Karate Master special trainer state machine.",
            "The source coordinate auto-talk at local (4,3) is represented as a coordinate trigger using the same battle action as direct interaction.",
            "The post-battle map script owns the reward prompt and source SetEventRange side effects for the Karate Master and four Blackbelts.",
        ],
    )
    source["coveredLabels"] = [
        "FightingDojoDefaultScript",
        "FightingDojoKarateMasterText",
        "FightingDojoKarateMasterPostBattleScript",
    ]
    set_range_actions = [
        {"type": "setEvent", "event": "EVENT_BEAT_KARATE_MASTER"},
        {"type": "setEvent", "event": "EVENT_BEAT_FIGHTING_DOJO_TRAINER_0"},
        {"type": "setEvent", "event": "EVENT_BEAT_FIGHTING_DOJO_TRAINER_1"},
        {"type": "setEvent", "event": "EVENT_BEAT_FIGHTING_DOJO_TRAINER_2"},
        {"type": "setEvent", "event": "EVENT_BEAT_FIGHTING_DOJO_TRAINER_3"},
    ]
    battle_action = trainer_battle_action_from_object(
        trainer_obj,
        "EVENT_BEAT_KARATE_MASTER",
        [],
    )
    battle_actions = [
        {"type": "lockInput"},
        {"type": "dialogue", "lines": battle_lines},
        battle_action,
        {"type": "unlockInput"},
    ]

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "FightingDojoKarateMasterBattle",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": "FightingDojoKarateMasterText",
            },
            "conditions": {"requiresEventAbsent": "EVENT_BEAT_KARATE_MASTER"},
            "actions": battle_actions,
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "FightingDojoKarateMasterCoordBattle",
            "trigger": {
                "type": "coord",
                "label": "FightingDojoKarateMasterCoords",
                "sourceLabel": "FightingDojoDefaultScript",
                "coordinates": [{"mapName": map_name, "mapId": map_id, "x": 4, "y": 3}],
            },
            "conditions": {"requiresEventAbsent": "EVENT_BEAT_KARATE_MASTER"},
            "actions": battle_actions,
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "FightingDojoKarateMasterPostBattle",
            "trigger": {
                "type": "map_script",
                "label": "FightingDojoKarateMasterPostBattleScript",
                "sourceLabel": "FightingDojoKarateMasterPostBattleScript",
            },
            "conditions": {
                "requiresEvent": "EVENT_BEAT_KARATE_MASTER",
                "requiresEventAbsent": "EVENT_GOT_FIGHTING_DOJO_POKEMON",
            },
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": defeated_lines + reward_lines},
                *set_range_actions,
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "FightingDojoKarateMasterRewardPrompt",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": "FightingDojoKarateMasterText",
            },
            "conditions": {
                "requiresEvent": "EVENT_BEAT_KARATE_MASTER",
                "requiresEventAbsent": "EVENT_GOT_FIGHTING_DOJO_POKEMON",
            },
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": reward_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "FightingDojoKarateMasterStayAndTrain",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": "FightingDojoKarateMasterText",
            },
            "conditions": {"requiresEvent": "EVENT_DEFEATED_FIGHTING_DOJO"},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": stay_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
    ]


def bills_house_cell_separator_candidates():
    map_name = "BillsHouse"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    text_pointers = parse_text_pointer_map(script_content)
    text_labels = extract_map_text_labels(map_name)

    bill_text = blocks_by_label.get("BillsHouseBillPokemonText")
    walk_script = blocks_by_label.get("BillsHousePokemonWalkToMachineScript")
    enter_script = blocks_by_label.get("BillsHousePokemonEntersMachineScript")
    exit_script = blocks_by_label.get("BillsHouseBillExitsMachineScript")
    cleanup_script = blocks_by_label.get("BillsHouseCleanupScript")
    if not all([bill_text, walk_script, enter_script, exit_script, cleanup_script]):
        return []

    text_constant = text_pointers.get("BillsHouseBillPokemonText")
    if text_constant != "TEXT_BILLSHOUSE_BILL_POKEMON":
        return []

    bill_clean = "\n".join(strip_comment(line) for line in bill_text["raw"].splitlines())
    walk_clean = "\n".join(strip_comment(line) for line in walk_script["raw"].splitlines())
    enter_clean = "\n".join(strip_comment(line) for line in enter_script["raw"].splitlines())
    exit_clean = "\n".join(strip_comment(line) for line in exit_script["raw"].splitlines())
    cleanup_clean = "\n".join(strip_comment(line) for line in cleanup_script["raw"].splitlines())
    if not all(
        snippet in bill_clean
        for snippet in [
            "call YesNoChoice",
            "ld a, SCRIPT_BILLSHOUSE_POKEMON_WALK_TO_MACHINE",
            "ld [wBillsHouseCurScript], a",
            "jr .use_machine",
        ]
    ):
        return []
    if not all(
        snippet in walk_clean
        for snippet in [
            "ld de, .PokemonWalkToMachineMovement",
            "ld de, .PokemonWalkAroundPlayerMovement",
            "ld a, BILLSHOUSE_BILL_POKEMON",
            "ld a, SCRIPT_BILLSHOUSE_POKEMON_ENTERS_MACHINE",
        ]
    ):
        return []
    if not all(
        snippet in enter_clean
        for snippet in [
            "predef HideObject",
            "SetEvent EVENT_BILL_SAID_USE_CELL_SEPARATOR",
            "ld a, SCRIPT_BILLSHOUSE_BILL_EXITS_MACHINE",
        ]
    ):
        return []
    if not all(
        snippet in exit_clean
        for snippet in [
            "CheckEvent EVENT_USED_CELL_SEPARATOR_ON_BILL",
            "predef ShowObject",
            "ld de, BillExitMachineMovement",
            "ld a, SCRIPT_BILLSHOUSE_CLEANUP",
        ]
    ):
        return []
    if not all(
        snippet in cleanup_clean
        for snippet in [
            "SetEvent EVENT_MET_BILL_2",
            "SetEvent EVENT_MET_BILL",
            "ld a, SCRIPT_BILLSHOUSE_DEFAULT",
        ]
    ):
        return []

    text_refs = local_text_ref_map(bill_text["raw"])
    prompt_lines = local_lines(text_labels, text_refs, ".ImNotAPokemonText")
    no_lines = local_lines(text_labels, text_refs, ".NoYouGottaHelpText")
    use_machine_lines = local_lines(text_labels, text_refs, ".UseSeparationSystemText")
    if not prompt_lines or not no_lines or not use_machine_lines:
        return []

    source = source_metadata(
        map_name,
        "bills_house_cell_separator_v1",
        script_path,
        text_path,
        [
            "sourceBlock=BillsHouseBillPokemonText",
            "sourceBlock=BillsHousePokemonWalkToMachineScript",
            "sourceBlock=BillsHousePokemonEntersMachineScript",
            "sourceBlock=BillsHouseBillExitsMachineScript",
            "sourceBlock=BillsHouseCleanupScript",
            "Bill's No answer prints an extra line, then continues to the machine sequence in the original script.",
            "The Game Boy's PC activation flag is represented downstream as EVENT_USED_CELL_SEPARATOR.",
        ],
    )
    source["coveredLabels"] = [
        "BillsHouseBillPokemonText",
        "BillsHousePokemonWalkToMachineScript",
        "BillsHousePokemonEntersMachineScript",
        "BillsHouseBillExitsMachineScript",
        "BillsHouseCleanupScript",
    ]

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "BillsHousePokemonWalkToMachine",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": "BillsHouseBillPokemonText",
            },
            "conditions": {"requiresEventAbsent": "EVENT_BILL_SAID_USE_CELL_SEPARATOR"},
            "actions": [
                {"type": "lockInput"},
                {
                    "type": "choice",
                    "speaker": "BILL",
                    "promptLines": prompt_lines,
                    "noLines": no_lines,
                    "continueOnNo": True,
                },
                {"type": "dialogue", "speaker": "BILL", "lines": use_machine_lines},
                {"type": "move", "actor": "BILL_POKEMON", "movements": ["UP", "UP", "UP"]},
                {"type": "hideActor", "actor": "BILL_POKEMON"},
                {"type": "hideObject", "objectKey": "HS_BILL_POKEMON"},
                {"type": "setEvent", "event": "EVENT_BILL_SAID_USE_CELL_SEPARATOR"},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "BillsHouseBillExitsMachine",
            "trigger": {
                "type": "map_script",
                "label": "BillsHouseBillExitsMachineScript",
                "sourceLabel": "BillsHouseBillExitsMachineScript",
            },
            "conditions": {
                "requiresEvent": "EVENT_USED_CELL_SEPARATOR",
                "requiresEventAbsent": "EVENT_MET_BILL",
            },
            "actions": [
                {"type": "lockInput"},
                {"type": "showActor", "actor": "BILL_1", "x": 5, "y": 6},
                {"type": "showObject", "objectKey": "HS_BILL_1"},
                {"type": "delay", "ms": 500},
                {"type": "move", "actor": "BILL_1", "movements": ["DOWN", "RIGHT", "RIGHT", "RIGHT", "DOWN"]},
                {"type": "setEvent", "event": "EVENT_MET_BILL_2"},
                {"type": "setEvent", "event": "EVENT_MET_BILL"},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
    ]


def route25_bill_visibility_candidates():
    map_name = "Route25"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    block = blocks_by_label.get("Route25ShowHideBillScript")
    if not block:
        return []

    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    required_snippets = [
        "CheckEventHL EVENT_LEFT_BILLS_HOUSE_AFTER_HELPING",
        "CheckEventReuseHL EVENT_MET_BILL_2",
        "ResetEventReuseHL EVENT_BILL_SAID_USE_CELL_SEPARATOR",
        "ld a, HS_BILL_POKEMON",
        "predef_jump ShowObject",
        "CheckEventAfterBranchReuseHL EVENT_GOT_SS_TICKET, EVENT_MET_BILL_2",
        "SetEventReuseHL EVENT_LEFT_BILLS_HOUSE_AFTER_HELPING",
        "ld a, HS_NUGGET_BRIDGE_GUY",
        "predef HideObject",
        "ld a, HS_BILL_1",
        "ld a, HS_BILL_2",
        "predef_jump ShowObject",
    ]
    if not all(snippet in clean for snippet in required_snippets):
        return []

    source = source_metadata(
        map_name,
        "route25_bill_visibility_v1",
        script_path,
        text_path,
        [
            "sourceBlock=Route25ShowHideBillScript",
            "Generated from Route 25's global missable-object visibility sync for Bill's house aftermath.",
            "The Game Boy map-load flag check is represented downstream by map_script execution conditions.",
        ],
    )
    source["coveredLabels"] = ["Route25ShowHideBillScript"]

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "Route25BillPokemonVisibleBeforeHelp",
            "trigger": {
                "type": "map_script",
                "label": "Route25ShowHideBillScript",
                "sourceLabel": "Route25ShowHideBillScript",
            },
            "conditions": {
                "requiresEventAbsent": "EVENT_LEFT_BILLS_HOUSE_AFTER_HELPING",
                "requiresEventsAbsent": ["EVENT_MET_BILL_2"],
            },
            "actions": [
                {"type": "resetEvent", "event": "EVENT_BILL_SAID_USE_CELL_SEPARATOR"},
                {"type": "showObject", "objectKey": "HS_BILL_POKEMON"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "Route25BillReturnedOutsideAfterSSTicket",
            "trigger": {
                "type": "map_script",
                "label": "Route25ShowHideBillScript",
                "sourceLabel": "Route25ShowHideBillScript",
            },
            "conditions": {
                "requiresEvents": ["EVENT_MET_BILL_2", "EVENT_GOT_SS_TICKET"],
                "requiresEventAbsent": "EVENT_LEFT_BILLS_HOUSE_AFTER_HELPING",
            },
            "actions": [
                {"type": "setEvent", "event": "EVENT_LEFT_BILLS_HOUSE_AFTER_HELPING"},
                {"type": "hideObject", "objectKey": "HS_NUGGET_BRIDGE_GUY"},
                {"type": "hideObject", "objectKey": "HS_BILL_1"},
                {"type": "showObject", "objectKey": "HS_BILL_2"},
            ],
            "source": source,
            "confidence": "adapter",
        },
    ]


def mt_moon_fossil_choice_candidates():
    map_name = "MtMoonB2F"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    text_labels = extract_map_text_labels(map_name)
    text_pointers = parse_text_pointer_map(script_content)
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    received_block = blocks_by_label.get("MtMoonB2FReceivedFossilText")
    super_nerd_block = blocks_by_label.get("MtMoonB2FSuperNerdThenThisIsMineText")
    share_block = blocks_by_label.get("MtMoonB2FSuperNerdOkIllShareText")
    super_nerd_lines = lines_for_labels(
        text_labels, ordered_text_refs(super_nerd_block["raw"] if super_nerd_block else "")
    )
    share_lines = lines_for_labels(
        text_labels, ordered_text_refs(share_block["raw"] if share_block else "")
    )

    source = source_metadata(
        map_name,
        "mt_moon_fossil_choice_v1",
        script_path,
        text_path,
        [
            "Generated from Mt. Moon fossil Yes/No + GiveItem state machines.",
            "Uses EVENT_GOT_MT_MOON_FOSSIL as the downstream mutual-exclusion flag.",
            "Collapses the original Super Nerd follow-up movement into immediate hide-other-fossil behavior.",
        ],
    )

    candidates = []
    default_block = blocks_by_label.get("MtMoonB2FDefaultScript")
    defeated_block = blocks_by_label.get("MtMoonB2FDefeatedSuperNerdScript")
    super_nerd_text_block = blocks_by_label.get("MtMoonB2FSuperNerdText")
    if default_block and defeated_block and super_nerd_text_block and share_lines:
        default_clean = "\n".join(strip_comment(line) for line in default_block["raw"].splitlines())
        defeated_clean = "\n".join(strip_comment(line) for line in defeated_block["raw"].splitlines())
        super_nerd_clean = "\n".join(strip_comment(line) for line in super_nerd_text_block["raw"].splitlines())
        if (
            "CheckEvent EVENT_BEAT_MT_MOON_EXIT_SUPER_NERD" in default_clean
            and "ld a, TEXT_MTMOONB2F_SUPER_NERD" in default_clean
            and "SetEvent EVENT_BEAT_MT_MOON_EXIT_SUPER_NERD" in defeated_clean
            and "ld hl, MtMoonB2FSuperNerdOkIllShareText" in super_nerd_clean
        ):
            prompt_source = dict(source)
            prompt_source["coveredLabels"] = [
                "MtMoonB2FDefaultScript",
                "MtMoonB2FDefeatedSuperNerdScript",
                "MtMoonB2FSuperNerdText",
            ]
            candidates.append(
                {
                    "version": 1,
                    "kind": "scriptEventCandidate",
                    "mapName": map_name,
                    "scriptLabel": "MtMoonB2FFossilChoice",
                    "trigger": {
                        "type": "map_script",
                        "label": "MtMoonB2FDefaultScript",
                        "sourceLabel": "MtMoonB2FDefaultScript",
                    },
                    "conditions": {
                        "requiresEvent": "EVENT_BEAT_MT_MOON_EXIT_SUPER_NERD",
                        "requiresEventAbsent": "EVENT_GOT_MT_MOON_FOSSIL",
                    },
                    "actions": [
                        {"type": "lockInput"},
                        {"type": "dialogue", "speaker": "SUPER NERD", "lines": share_lines},
                        {"type": "unlockInput"},
                    ],
                    "source": prompt_source,
                    "confidence": "adapter",
                }
            )

    for label, item, selected_text, other_text, script_label in [
        (
            "MtMoonB2FDomeFossilText",
            "DOME_FOSSIL",
            "TEXT_MTMOONB2F_DOME_FOSSIL",
            "TEXT_MTMOONB2F_HELIX_FOSSIL",
            "MtMoonB2FDomeFossilChoice",
        ),
        (
            "MtMoonB2FHelixFossilText",
            "HELIX_FOSSIL",
            "TEXT_MTMOONB2F_HELIX_FOSSIL",
            "TEXT_MTMOONB2F_DOME_FOSSIL",
            "MtMoonB2FHelixFossilChoice",
        ),
    ]:
        block = blocks_by_label.get(label)
        text_constant = text_pointers.get(label, selected_text)
        if not block or text_constant != selected_text:
            continue
        clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
        if (
            "call YesNoChoice" not in clean
            or f"lb bc, {item}, 1" not in clean
            or "call GiveItem" not in clean
            or "call MtMoonB2FReceivedFossilText" not in clean
            or "predef HideObject" not in clean
            or f"SetEvent EVENT_GOT_{item}" not in clean
            or "SCRIPT_MTMOONB2F_MOVE_SUPER_NERD" not in clean
        ):
            continue

        prompt_lines = local_lines(text_labels, local_text_ref_map(block["raw"]), ".YouWantText")
        received_lines = hydrate_received_item_lines(
            lines_for_labels(
                text_labels,
                ordered_text_refs(received_block["raw"] if received_block else ""),
            ),
            item,
        )
        if not prompt_lines or not received_lines or not super_nerd_lines:
            continue

        branch_source = dict(source)
        branch_source["coveredLabels"] = [
            label,
            "MtMoonB2FReceivedFossilText",
            "MtMoonB2FMoveSuperNerdScript",
            "MtMoonB2FSuperNerdThenThisIsMineText",
            "MtMoonB2FSuperNerdTakesOtherFossilScript",
        ]
        candidates.append(
            {
                "version": 1,
                "kind": "scriptEventCandidate",
                "mapName": map_name,
                "scriptLabel": script_label,
                "trigger": {
                    "type": "npc_click",
                    "label": text_constant,
                    "sourceLabel": label,
                },
                "conditions": {
                    "requiresEvent": "EVENT_BEAT_MT_MOON_EXIT_SUPER_NERD",
                    "requiresEventAbsent": "EVENT_GOT_MT_MOON_FOSSIL",
                },
                "actions": [
                    {"type": "lockInput"},
                    {
                        "type": "choice",
                        "prompt": " ".join(prompt_lines),
                        "textConstant": text_constant,
                    },
                    {"type": "giveItem", "itemConstant": item, "quantity": 1},
                    {"type": "hideObject", "textConstant": selected_text},
                    {"type": "hideObject", "textConstant": other_text},
                    {"type": "dialogue", "lines": received_lines + super_nerd_lines},
                    {"type": "setEvent", "event": "EVENT_GOT_MT_MOON_FOSSIL"},
                    {"type": "setEvent", "event": f"EVENT_GOT_{item}"},
                    {"type": "unlockInput"},
                ],
                "source": branch_source,
                "confidence": "adapter",
            }
        )

    return candidates


def celadon_roof_drink_trade_candidates():
    map_name = "CeladonMartRoof"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    text_labels = extract_map_text_labels(map_name)
    text_pointers = parse_text_pointer_map(script_content)
    girl_block = blocks_by_label.get("CeladonMartRoofLittleGirlText")
    trade_block = blocks_by_label.get("CeladonMartRoofScript_GiveDrinkToGirl")
    if not girl_block or not trade_block:
        return []

    clean_script = "\n".join(strip_comment(line) for line in script_content.splitlines())
    required_snippets = [
        "db FRESH_WATER",
        "db SODA_POP",
        "db LEMONADE",
        "call CeladonMartRoofScript_GetDrinksInBag",
        "call YesNoChoice",
        "call CeladonMartRoofScript_GiveDrinkToGirl",
        "RemoveItemByIDBank12",
    ]
    if any(snippet not in clean_script for snippet in required_snippets):
        return []

    text_constant = text_pointers.get("CeladonMartRoofLittleGirlText")
    if not text_constant:
        return []

    local_refs = local_text_ref_map(girl_block["raw"])
    prelude_lines = local_lines(text_labels, local_refs, ".GiveHerADrinkText")
    if prelude_lines and prelude_lines[-1].strip().lower() == "give her a drink?":
        prelude_lines = prelude_lines[:-1]

    specs = [
        {
            "drink": "FRESH_WATER",
            "tm": "TM_ICE_BEAM",
            "flag": "EVENT_GOT_TM13",
            "yayLabel": "CeladonMartRoofLittleGirlYayFreshWaterText",
            "receivedLabel": "CeladonMartRoofLittleGirlReceivedTM13Text",
            "scriptLabel": "CeladonMartRoofTM13IceBeam",
        },
        {
            "drink": "SODA_POP",
            "tm": "TM_ROCK_SLIDE",
            "flag": "EVENT_GOT_TM48",
            "yayLabel": "CeladonMartRoofLittleGirlYaySodaPopText",
            "receivedLabel": "CeladonMartRoofLittleGirlReceivedTM48Text",
            "scriptLabel": "CeladonMartRoofTM48RockSlide",
        },
        {
            "drink": "LEMONADE",
            "tm": "TM_TRI_ATTACK",
            "flag": "EVENT_GOT_TM49",
            "yayLabel": "CeladonMartRoofLittleGirlYayLemonadeText",
            "receivedLabel": "CeladonMartRoofLittleGirlReceivedTM49Text",
            "scriptLabel": "CeladonMartRoofTM49TriAttack",
        },
    ]

    source = source_metadata(
        map_name,
        "celadon_roof_drink_trade_v1",
        script_path,
        text_path,
        [
            "Generated from the Celadon rooftop girl drink menu state machine.",
            "Emits one conditional branch per drink because the neutral action vocabulary does not yet model item menus.",
            "Bag-full/no-room behavior remains downstream inventory behavior.",
        ],
    )

    candidates = []
    trade_clean = "\n".join(strip_comment(line) for line in trade_block["raw"].splitlines())
    for spec in specs:
        branch_snippets = [
            f"CheckEvent {spec['flag']}",
            f"lb bc, {spec['tm']}, 1",
            f"SetEvent {spec['flag']}",
        ]
        if any(snippet not in trade_clean for snippet in branch_snippets):
            continue

        yay_lines = lines_for_script_text_ref(spec["yayLabel"], blocks_by_label, text_labels, {})
        received_lines = hydrate_buffered_tm_reward_lines(
            lines_for_script_text_ref(spec["receivedLabel"], blocks_by_label, text_labels, {}),
            spec["tm"],
        )
        if not prelude_lines or not yay_lines or not received_lines:
            continue

        tm_name = item_display_name(spec["tm"])
        drink_name = item_display_name(spec["drink"])
        branch_source = dict(source)
        branch_source["coveredLabels"] = unique_sorted(
            [
                "CeladonMartRoofLittleGirlText",
                "CeladonMartRoofScript_GetDrinksInBag",
                "CeladonMartRoofScript_GiveDrinkToGirl",
                "CeladonMartRoofScript_PrintDrinksInBag",
                spec["yayLabel"],
                spec["receivedLabel"],
            ]
        )

        candidates.append(
            {
                "version": 1,
                "kind": "scriptEventCandidate",
                "mapName": map_name,
                "scriptLabel": spec["scriptLabel"],
                "trigger": {
                    "type": "npc_click",
                    "label": text_constant,
                    "sourceLabel": "CeladonMartRoofLittleGirlText",
                },
                "conditions": {
                    "requiresItem": spec["drink"],
                    "requiresEventAbsent": spec["flag"],
                },
                "actions": [
                    {"type": "lockInput"},
                    {"type": "dialogue", "speaker": "LITTLE GIRL", "lines": prelude_lines},
                    {
                        "type": "choice",
                        "speaker": "LITTLE GIRL",
                        "textConstant": text_constant,
                        "prompt": f"Trade {drink_name} for {tm_name}?",
                    },
                    {"type": "dialogue", "speaker": "LITTLE GIRL", "lines": yay_lines},
                    {"type": "takeItem", "itemConstant": spec["drink"], "quantity": 1},
                    {"type": "giveItem", "itemConstant": spec["tm"], "quantity": 1},
                    {"type": "dialogue", "lines": received_lines},
                    {"type": "setEvent", "event": spec["flag"]},
                    {"type": "unlockInput"},
                ],
                "source": branch_source,
                "confidence": "adapter",
            }
        )

    return candidates


def museum_entry_ticket_candidates():
    map_name = "Museum1F"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    text_labels = extract_map_text_labels(map_name)
    block = blocks_by_label.get("Museum1FScientist1Text")
    default_script = blocks_by_label.get("Museum1FDefaultScript")
    if not block or not default_script:
        return []

    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    required_snippets = [
        "CheckEvent EVENT_BOUGHT_MUSEUM_TICKET",
        "call YesNoChoice",
        "call HasEnoughMoney",
        "SetEvent EVENT_BOUGHT_MUSEUM_TICKET",
        "predef SubBCDPredef",
    ]
    if any(snippet not in clean for snippet in required_snippets):
        return []
    if "cp 4" not in default_script["raw"] or "cp 9" not in default_script["raw"] or "cp 10" not in default_script["raw"]:
        return []

    local_refs = local_text_ref_map(block["raw"])
    offer_lines = local_lines(text_labels, local_refs, ".WouldYouLikeToComeInText")
    thank_you_lines = local_lines(text_labels, local_refs, ".ThankYouText")
    no_money_lines = local_lines(text_labels, local_refs, ".DontHaveEnoughMoneyText")
    already_lines = local_lines(text_labels, local_refs, ".TakePlentyOfTimeText")
    if not offer_lines or not thank_you_lines or not no_money_lines or not already_lines:
        return []

    source = source_metadata(
        map_name,
        "paid_choice_v1",
        script_path,
        text_path,
        [
            "Generated from the Museum 1F ticket gate money-check state machine.",
            "The original automatic coordinate trigger is represented as a source coordinate candidate.",
            "No-choice denial movement is not modeled until neutral actions support choice-specific movement branches.",
        ],
    )
    source["coveredLabels"] = [
        "Museum1FDefaultScript",
        "Museum1FScientist1Text",
    ]
    trigger = {
        "type": "coord",
        "label": "Museum1FEntryTicketCoords",
        "sourceLabel": "Museum1FDefaultScript",
        "coordinates": [{"x": 9, "y": 4}, {"x": 10, "y": 4}],
    }

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "Museum1FEntryTicketPurchase",
            "trigger": trigger,
            "conditions": {
                "requiresEventAbsent": "EVENT_BOUGHT_MUSEUM_TICKET",
                "requiresMoney": 50,
            },
            "actions": [
                {"type": "lockInput"},
                {
                    "type": "choice",
                    "textConstant": "TEXT_MUSEUM1F_SCIENTIST1",
                    "promptLines": offer_lines,
                },
                {"type": "dialogue", "lines": thank_you_lines},
                {"type": "takeMoney", "money": 50},
                {"type": "setEvent", "event": "EVENT_BOUGHT_MUSEUM_TICKET"},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "Museum1FEntryTicketInsufficientMoney",
            "trigger": trigger,
            "conditions": {
                "requiresEventAbsent": "EVENT_BOUGHT_MUSEUM_TICKET",
                "requiresMoneyBelow": 50,
            },
            "actions": [
                {"type": "lockInput"},
                {
                    "type": "choice",
                    "textConstant": "TEXT_MUSEUM1F_SCIENTIST1",
                    "promptLines": offer_lines,
                },
                {"type": "dialogue", "lines": no_money_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "Museum1FEntryTicketAlreadyBought",
            "trigger": trigger,
            "conditions": {"requiresEvent": "EVENT_BOUGHT_MUSEUM_TICKET"},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": already_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
    ]


def mt_moon_magikarp_salesman_candidates():
    map_name = "MtMoonPokecenter"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    text_labels = extract_map_text_labels(map_name)
    text_pointers = parse_text_pointer_map(script_content)
    block = blocks_by_label.get("MtMoonPokecenterMagikarpSalesmanText")
    text_constant = text_pointers.get("MtMoonPokecenterMagikarpSalesmanText")
    if not block or not text_constant:
        return []

    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    required_snippets = [
        "CheckEvent EVENT_BOUGHT_MAGIKARP",
        "call YesNoChoice",
        "call HasEnoughMoney",
        "lb bc, MAGIKARP, 5",
        "call GivePokemon",
        "SetEvent EVENT_BOUGHT_MAGIKARP",
    ]
    if any(snippet not in clean for snippet in required_snippets):
        return []

    local_refs = local_text_ref_map(block["raw"])
    offer_lines = local_lines(text_labels, local_refs, ".IGotADealText")
    no_lines = local_lines(text_labels, local_refs, ".NoText")
    no_money_lines = local_lines(text_labels, local_refs, ".NoMoneyText")
    already_lines = local_lines(text_labels, local_refs, ".NoRefundsText")
    if not offer_lines or not no_lines or not no_money_lines or not already_lines:
        return []

    source = source_metadata(
        map_name,
        "paid_choice_v1",
        script_path,
        text_path,
        [
            "Generated from the Mt. Moon Pokecenter Magikarp salesman money-check state machine.",
            "Money is deducted before GivePokemon in the neutral action list so stale completion cannot grant a free Pokemon.",
        ],
    )
    source["coveredLabels"] = ["MtMoonPokecenterMagikarpSalesmanText"]
    trigger = {
        "type": "npc_click",
        "label": text_constant,
        "sourceLabel": "MtMoonPokecenterMagikarpSalesmanText",
    }

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "MtMoonPokecenterMagikarpSalesmanPurchase",
            "trigger": trigger,
            "conditions": {
                "requiresEventAbsent": "EVENT_BOUGHT_MAGIKARP",
                "requiresMoney": 500,
            },
            "actions": [
                {"type": "lockInput"},
                {
                    "type": "choice",
                    "textConstant": text_constant,
                    "promptLines": offer_lines,
                    "noLines": no_lines,
                },
                {"type": "takeMoney", "money": 500},
                {"type": "givePokemon", "pokemonConstant": "MAGIKARP", "level": 5},
                {"type": "setEvent", "event": "EVENT_BOUGHT_MAGIKARP"},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "MtMoonPokecenterMagikarpSalesmanInsufficientMoney",
            "trigger": trigger,
            "conditions": {
                "requiresEventAbsent": "EVENT_BOUGHT_MAGIKARP",
                "requiresMoneyBelow": 500,
            },
            "actions": [
                {"type": "lockInput"},
                {
                    "type": "choice",
                    "textConstant": text_constant,
                    "promptLines": offer_lines,
                    "noLines": no_lines,
                },
                {"type": "dialogue", "lines": no_money_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "MtMoonPokecenterMagikarpSalesmanAlreadyBought",
            "trigger": trigger,
            "conditions": {"requiresEvent": "EVENT_BOUGHT_MAGIKARP"},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": already_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
    ]


def paid_choice_candidates():
    return museum_entry_ticket_candidates() + mt_moon_magikarp_salesman_candidates()


def bcd_literal_to_int(value):
    if value.startswith("$"):
        return int(value[1:], 10)
    return int(value)


def text_ref_after_local_label(raw_asm, local_label):
    active = False
    for raw_line in raw_asm.splitlines():
        stripped = strip_comment(raw_line)
        if stripped.rstrip(":") == local_label:
            active = True
            continue
        if not active:
            continue
        if stripped.startswith("."):
            break
        match = re.search(r"\bld\s+hl,\s+(\.?\w+)", stripped)
        if match:
            return match.group(1)
    return ""


def first_local_text_ref(raw_asm):
    match = re.search(r"\bld\s+hl,\s+(\.\w+)\s+call\s+PrintText", "\n".join(strip_comment(line) for line in raw_asm.splitlines()))
    return match.group(1) if match else ""


def game_corner_coin_amount(raw_asm):
    clean = "\n".join(strip_comment(line) for line in raw_asm.splitlines())
    match = re.search(r"\bld\s+a,\s+(\$[0-9A-Fa-f]+|\d+)\s+ldh\s+\[hCoins \+ 1\],\s+a", clean)
    if not match:
        return 0
    return bcd_literal_to_int(match.group(1))


def game_corner_coin_purchase_candidates():
    map_name = "GameCorner"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    block = blocks_by_label.get("GameCornerClerk1Text")
    if not block:
        return []

    text_pointers = parse_text_pointer_map(script_content)
    text_constant = text_pointers.get("GameCornerClerk1Text")
    if not text_constant:
        return []

    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    required_snippets = [
        "call YesNoChoice",
        "ld b, COIN_CASE",
        "call IsItemInBag",
        "call Has9990Coins",
        "call HasEnoughMoney",
        "predef SubBCDPredef",
        "predef AddBCDPredef",
    ]
    if any(snippet not in clean for snippet in required_snippets):
        return []

    text_labels = extract_map_text_labels(map_name)
    text_refs = local_text_ref_map(block["raw"])
    offer_lines = local_lines(text_labels, text_refs, ".DoYouNeedSomeGameCoins")
    thanks_lines = local_lines(text_labels, text_refs, ".ThanksHereAre50Coins")
    declined_lines = local_lines(text_labels, text_refs, ".PleaseComePlaySometime")
    no_money_lines = local_lines(text_labels, text_refs, ".CantAffordTheCoins")
    full_lines = local_lines(text_labels, text_refs, ".CoinCaseIsFull")
    no_case_lines = local_lines(text_labels, text_refs, ".DontHaveCoinCase")
    if not all([offer_lines, thanks_lines, declined_lines, no_money_lines, full_lines, no_case_lines]):
        return []

    source = source_metadata(
        map_name,
        "game_corner_coin_purchase_v1",
        script_path,
        text_path,
        notes=[
            "Generated from Game Corner Clerk 1's COIN_CASE + Has9990Coins + HasEnoughMoney state machine.",
            "The source asks the Yes/No purchase prompt before checking Coin Case, coin capacity, or money.",
        ],
    )
    source["coveredLabels"] = ["GameCornerClerk1Text"]
    trigger = {
        "type": "npc_click",
        "label": text_constant,
        "sourceLabel": "GameCornerClerk1Text",
    }
    choice_action = {
        "type": "choice",
        "textConstant": text_constant,
        "promptLines": offer_lines,
        "noLines": declined_lines,
    }
    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "GameCornerClerk1BuyCoins",
            "trigger": trigger,
            "conditions": {
                "requiresItem": "COIN_CASE",
                "requiresCoinsBelow": 9990,
                "requiresMoney": 1000,
            },
            "actions": [
                {"type": "lockInput"},
                choice_action,
                {"type": "takeMoney", "money": 1000},
                {"type": "giveCoins", "coins": 50},
                {"type": "dialogue", "lines": thanks_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "GameCornerClerk1InsufficientMoney",
            "trigger": trigger,
            "conditions": {
                "requiresItem": "COIN_CASE",
                "requiresCoinsBelow": 9990,
                "requiresMoneyBelow": 1000,
            },
            "actions": [
                {"type": "lockInput"},
                choice_action,
                {"type": "dialogue", "lines": no_money_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "GameCornerClerk1CoinCaseFull",
            "trigger": trigger,
            "conditions": {
                "requiresItem": "COIN_CASE",
                "requiresCoins": 9990,
            },
            "actions": [
                {"type": "lockInput"},
                choice_action,
                {"type": "dialogue", "lines": full_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "GameCornerClerk1NoCoinCase",
            "trigger": trigger,
            "conditions": {"requiresItemAbsent": "COIN_CASE"},
            "actions": [
                {"type": "lockInput"},
                choice_action,
                {"type": "dialogue", "lines": no_case_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
    ]


def game_corner_prize_vendor_candidates():
    map_name = "GameCornerPrizeRoom"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    pointer_entries = parse_text_pointer_entries(script_content)

    candidates = []
    covered_text_constants = []
    for entry in pointer_entries:
        label = entry["label"]
        text_constant = entry["textConstant"]
        block = blocks_by_label.get(label)
        if not block:
            continue
        clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
        if "script_prize_vendor" not in clean:
            continue

        vendor_match = re.search(r"_PRIZE_VENDOR_(\d+)$", text_constant)
        if not vendor_match:
            continue
        prize_window = int(vendor_match.group(1))
        covered_text_constants.append(text_constant)
        candidates.append(
            {
                "version": 1,
                "kind": "scriptEventCandidate",
                "mapName": map_name,
                "scriptLabel": f"GameCornerPrizeRoomPrizeVendor{prize_window}",
                "trigger": {
                    "type": "npc_click",
                    "label": text_constant,
                    "sourceLabel": label,
                },
                "actions": [
                    {
                        "type": "gameCornerPrizeVendor",
                        "textConstant": text_constant,
                        "prizeWindow": prize_window,
                    }
                ],
                "source": source_metadata(
                    map_name,
                    "game_corner_prize_vendor_v1",
                    script_path,
                    text_path,
                    notes=[
                        "Generated from script_prize_vendor/TX_SCRIPT_PRIZE_VENDOR text scripts.",
                        "Duplicate text pointers are preserved because the original room has three prize counters sharing one script label.",
                    ],
                ),
                "confidence": "adapter",
            }
        )

    for candidate in candidates:
        candidate["source"]["coveredLabels"] = unique_sorted(
            [candidate["trigger"]["sourceLabel"], candidate["trigger"]["label"], *covered_text_constants]
        )
    return candidates


def silph_co_9f_nurse_candidates():
    map_name = "SilphCo9F"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    block = blocks_by_label.get("SilphCo9FNurseText")
    if not block:
        return []

    text_pointers = parse_text_pointer_map(script_content)
    text_constant = text_pointers.get("SilphCo9FNurseText")
    if not text_constant:
        return []

    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    required_snippets = [
        "CheckEvent EVENT_BEAT_SILPH_CO_GIOVANNI",
        "predef HealParty",
        "call GBFadeOutToWhite",
        "call GBFadeInFromWhite",
    ]
    if any(snippet not in clean for snippet in required_snippets):
        return []

    text_labels = extract_map_text_labels(map_name)
    text_refs = local_text_ref_map(block["raw"])
    tired_lines = local_lines(text_labels, text_refs, ".YouLookTiredText")
    dont_give_up_lines = local_lines(text_labels, text_refs, ".DontGiveUpText")
    thank_you_lines = local_lines(text_labels, text_refs, ".ThankYouText")
    if not tired_lines or not dont_give_up_lines or not thank_you_lines:
        return []

    source = source_metadata(
        map_name,
        "silph_co_9f_nurse_heal_v1",
        script_path,
        text_path,
        notes=[
            "Generated from Silph Co. 9F nurse's EVENT_BEAT_SILPH_CO_GIOVANNI + HealParty state machine.",
            "Fade and music details are intentionally collapsed to a neutral healParty action plus source dialogue.",
        ],
    )
    source["coveredLabels"] = ["SilphCo9FNurseText"]
    trigger = {
        "type": "npc_click",
        "label": text_constant,
        "sourceLabel": "SilphCo9FNurseText",
    }
    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "SilphCo9FNurseHeal",
            "trigger": trigger,
            "conditions": {"requiresEventAbsent": "EVENT_BEAT_SILPH_CO_GIOVANNI"},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": tired_lines},
                {"type": "healParty"},
                {"type": "dialogue", "lines": dont_give_up_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "SilphCo9FNurseThankYou",
            "trigger": trigger,
            "conditions": {"requiresEvent": "EVENT_BEAT_SILPH_CO_GIOVANNI"},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": thank_you_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
    ]


def game_corner_npc_coin_gift_candidate_for_block(map_name, script_path, text_path, text_pointers, text_labels, block):
    raw = block["raw"]
    clean = "\n".join(strip_comment(line) for line in raw.splitlines())
    if "COIN_CASE" not in clean or "predef AddBCDPredef" not in clean:
        return []
    event_match = re.search(r"\bCheckEvent\s+(EVENT_GOT_\d+_COINS(?:_\d+)?)", clean)
    if not event_match:
        return []
    if f"SetEvent {event_match.group(1)}" not in clean:
        return []

    trigger_label = text_pointers.get(block["label"])
    coin_amount = game_corner_coin_amount(raw)
    if not trigger_label or coin_amount <= 0:
        return []

    text_refs = local_text_ref_map(raw)
    prelude_label = first_local_text_ref(raw)
    received_labels = [label for label in text_refs if "Received" in label]
    received_label = received_labels[0] if len(received_labels) == 1 else ""
    already_label = text_ref_after_local_label(raw, ".alreadyGotNpcCoins")
    full_label = text_ref_after_local_label(raw, ".coinCaseFull")
    no_case_lines = text_labels.get("GameCornerOopsForgotCoinCaseText", [])

    prelude_lines = local_lines(text_labels, text_refs, prelude_label)
    received_lines = local_lines(text_labels, text_refs, received_label)
    already_lines = local_lines(text_labels, text_refs, already_label)
    full_lines = local_lines(text_labels, text_refs, full_label)
    if not prelude_lines or not received_lines or not already_lines or not full_lines or not no_case_lines:
        return []

    event_flag = event_match.group(1)
    label_base = block["label"].removesuffix("Text")
    source = source_metadata(
        map_name,
        "game_corner_npc_coin_gift_v1",
        script_path,
        text_path,
        notes=[
            f"sourceBlock={block['label']}",
            "Generated for Game Corner NPC coin gifts gated by COIN_CASE, EVENT_GOT_*_COINS, and Has9990Coins.",
            "requiresCoinsBelow/requiresCoins preserve the original full Coin Case branch threshold.",
        ],
    )
    trigger = {
        "type": "npc_click",
        "label": trigger_label,
        "sourceLabel": block["label"],
    }
    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{label_base}CoinsGift",
            "trigger": trigger,
            "conditions": {
                "requiresEventAbsent": event_flag,
                "requiresItem": "COIN_CASE",
                "requiresCoinsBelow": 9990,
            },
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": prelude_lines},
                {"type": "giveCoins", "coins": coin_amount},
                {"type": "setEvent", "event": event_flag},
                {"type": "dialogue", "lines": received_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{label_base}AlreadyGot",
            "trigger": trigger,
            "conditions": {"requiresEvent": event_flag},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": already_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{label_base}CoinCaseFull",
            "trigger": trigger,
            "conditions": {
                "requiresEventAbsent": event_flag,
                "requiresItem": "COIN_CASE",
                "requiresCoins": 9990,
            },
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": full_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{label_base}NoCoinCase",
            "trigger": trigger,
            "conditions": {
                "requiresEventAbsent": event_flag,
                "requiresItemAbsent": "COIN_CASE",
            },
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": no_case_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
    ]


def game_corner_npc_coin_gift_candidates():
    map_name = "GameCorner"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    text_pointers = parse_text_pointer_map(script_content)
    text_labels = extract_map_text_labels(map_name)
    candidates = []
    for block in extract_label_blocks(script_content):
        candidates.extend(
            game_corner_npc_coin_gift_candidate_for_block(
                map_name,
                script_path,
                text_path,
                text_pointers,
                text_labels,
                block,
            )
        )
    return candidates


def route23_badge_gate_candidates():
    map_name = "Route23"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    clean_script = "\n".join(strip_comment(line) for line in script_content.splitlines())
    required_snippets = [
        "ld hl, Route23GuardsYCoords",
        "call Route23CopyBadgeTextScript",
        "call Route23CheckForBadgeScript",
        "Route23MovePlayerDownScript:",
        "Route23YouDontHaveTheBadgeYetText:",
        "Route23OhThatIsTheBadgeText:",
    ]
    if any(snippet not in clean_script for snippet in required_snippets):
        return []

    guard_y = route23_guard_y_coords(script_content)
    objects = parse_object_events_for_map(map_name)
    gate_objects = [obj for obj in objects if obj["textConstant"].startswith("TEXT_ROUTE23_")]
    if len(guard_y) != 7 or len(gate_objects) < 7:
        return []

    specs = [
        {
            "badge": "EARTHBADGE",
            "sourcePassFlag": "EVENT_PASSED_EARTHBADGE_CHECK",
            "textConstant": "TEXT_ROUTE23_GUARD1",
            "triggerLabel": "Route23BadgeCheckEarthCoords",
            "scriptIndex": 8,
        },
        {
            "badge": "VOLCANOBADGE",
            "sourcePassFlag": "EVENT_PASSED_VOLCANOBADGE_CHECK",
            "textConstant": "TEXT_ROUTE23_GUARD2",
            "triggerLabel": "Route23BadgeCheckVolcanoCoords",
            "scriptIndex": 7,
        },
        {
            "badge": "MARSHBADGE",
            "sourcePassFlag": "EVENT_PASSED_MARSHBADGE_CHECK",
            "textConstant": "TEXT_ROUTE23_SWIMMER1",
            "triggerLabel": "Route23BadgeCheckMarshCoords",
            "scriptIndex": 6,
        },
        {
            "badge": "SOULBADGE",
            "sourcePassFlag": "EVENT_PASSED_SOULBADGE_CHECK",
            "textConstant": "TEXT_ROUTE23_SWIMMER2",
            "triggerLabel": "Route23BadgeCheckSoulCoords",
            "scriptIndex": 5,
        },
        {
            "badge": "RAINBOWBADGE",
            "sourcePassFlag": "EVENT_PASSED_RAINBOWBADGE_CHECK",
            "textConstant": "TEXT_ROUTE23_GUARD3",
            "triggerLabel": "Route23BadgeCheckRainbowCoords",
            "scriptIndex": 4,
        },
        {
            "badge": "THUNDERBADGE",
            "sourcePassFlag": "EVENT_PASSED_THUNDERBADGE_CHECK",
            "textConstant": "TEXT_ROUTE23_GUARD4",
            "triggerLabel": "Route23BadgeCheckThunderCoords",
            "scriptIndex": 3,
        },
        {
            "badge": "CASCADEBADGE",
            "sourcePassFlag": "EVENT_PASSED_CASCADEBADGE_CHECK",
            "textConstant": "TEXT_ROUTE23_GUARD5",
            "triggerLabel": "Route23BadgeCheckCascadeCoords",
            "scriptIndex": 2,
        },
    ]

    object_by_text = {obj["textConstant"]: obj for obj in gate_objects}
    for y, spec in zip(guard_y, specs):
        obj = object_by_text.get(spec["textConstant"])
        if not obj or obj["y"] != y:
            return []

    source = source_metadata(
        map_name,
        "route23_badge_gate_v1",
        script_path,
        text_path,
        [
            "Generated from Route23DefaultScript, Route23GuardsYCoords, object_event rows, and Route23CheckForBadgeScript.",
            "Uses neutral requiresBadge/requiresBadgeAbsent conditions because Red/Blue stores obtained badges separately from event flags.",
            "Pass completion flags use the original EVENT_PASSED_*BADGE_CHECK names; downstream runtimes may map those to compatibility flags.",
        ],
    )
    source["coveredLabels"] = [
        "Route23DefaultScript",
        "Route23CheckForBadgeScript",
        "Route23MovePlayerDownScript",
        "Route23YouDontHaveTheBadgeYetText",
        "Route23OhThatIsTheBadgeText",
    ]

    candidates = []
    for spec in specs:
        obj = object_by_text[spec["textConstant"]]
        coordinate = {"mapName": map_name, "x": obj["x"], "y": obj["y"]}
        badge = spec["badge"]
        pass_lines = [
            f"You can pass here only if you have the {badge}!",
            f"Oh! That is the {badge}!",
            "OK then! Please, go right ahead!",
        ]
        blocked_lines = [
            f"You can pass here only if you have the {badge}!",
            f"You don't have the {badge} yet!",
            "You have to have it to get to POKEMON LEAGUE!",
        ]
        common_trigger = {
            "type": "coord",
            "label": spec["triggerLabel"],
            "sourceLabel": spec["textConstant"],
            "coordinates": [coordinate],
        }

        candidates.append(
            {
                "version": 1,
                "kind": "scriptEventCandidate",
                "mapName": map_name,
                "scriptLabel": f"Route23BadgeCheck{spec['scriptIndex']}Pass",
                "trigger": common_trigger,
                "conditions": {
                    "requiresBadge": badge,
                    "requiresEventAbsent": spec["sourcePassFlag"],
                },
                "actions": [
                    {"type": "lockInput"},
                    {"type": "dialogue", "lines": pass_lines},
                    {"type": "setEvent", "event": spec["sourcePassFlag"]},
                    {"type": "unlockInput"},
                ],
                "source": source,
                "confidence": "adapter",
            }
        )
        candidates.append(
            {
                "version": 1,
                "kind": "scriptEventCandidate",
                "mapName": map_name,
                "scriptLabel": f"Route23BadgeCheck{spec['scriptIndex']}Blocked",
                "trigger": common_trigger,
                "conditions": {"requiresBadgeAbsent": badge},
                "actions": [
                    {"type": "lockInput"},
                    {"type": "dialogue", "lines": blocked_lines},
                    {"type": "movePlayer", "movements": ["DOWN"]},
                    {"type": "unlockInput"},
                ],
                "source": source,
                "confidence": "adapter",
            }
        )

    return candidates


def vermilion_ss_anne_guard_candidates():
    map_name = "VermilionCity"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    text_pointers = parse_text_pointer_map(script_content)
    text_labels = extract_map_text_labels(map_name)

    default_block = blocks_by_label.get("VermilionCityDefaultScript")
    sailor_block = blocks_by_label.get("VermilionCitySailor1Text")
    coords = parse_coord_array(script_content, "SSAnneTicketCheckCoords")
    if not default_block or not sailor_block or coords != [{"x": 18, "y": 30}]:
        return []

    default_clean = "\n".join(strip_comment(line) for line in default_block["raw"].splitlines())
    required_default = [
        "ld a, [wSpritePlayerStateData1FacingDirection]",
        "and a",
        "ret nz",
        "ld hl, SSAnneTicketCheckCoords",
        "call ArePlayerCoordsInArray",
        "ld a, TEXT_VERMILIONCITY_SAILOR1",
        "CheckEvent EVENT_SS_ANNE_LEFT",
        "ld b, S_S_TICKET",
        "predef GetQuantityOfItemInBag",
        "ld a, D_UP",
        "ld a, SCRIPT_VERMILIONCITY_PLAYER_MOVING_UP1",
    ]
    if any(snippet not in default_clean for snippet in required_default):
        return []

    sailor_clean = "\n".join(strip_comment(line) for line in sailor_block["raw"].splitlines())
    required_sailor = [
        "CheckEvent EVENT_SS_ANNE_LEFT",
        "ld hl, .DoYouHaveATicketText",
        "ld b, S_S_TICKET",
        "ld hl, .YouNeedATicketText",
        "ld hl, .FlashedTicketText",
        "ld a, SCRIPT_VERMILIONCITY_PLAYER_ALLOWED_TO_PASS",
        "ld hl, .ShipSetSailText",
    ]
    if any(snippet not in sailor_clean for snippet in required_sailor):
        return []

    if text_pointers.get("VermilionCitySailor1Text") != "TEXT_VERMILIONCITY_SAILOR1":
        return []

    local_refs = local_text_ref_map(sailor_block["raw"])
    ticket_prompt_lines = local_lines(text_labels, local_refs, ".DoYouHaveATicketText")
    flashed_ticket_lines = local_lines(text_labels, local_refs, ".FlashedTicketText")
    no_ticket_lines = local_lines(text_labels, local_refs, ".YouNeedATicketText")
    departed_lines = local_lines(text_labels, local_refs, ".ShipSetSailText")
    if not ticket_prompt_lines or not flashed_ticket_lines or not no_ticket_lines or not departed_lines:
        return []

    source = source_metadata(
        map_name,
        "vermilion_ss_anne_guard_v1",
        script_path,
        text_path,
        [
            "Generated from VermilionCityDefaultScript, SSAnneTicketCheckCoords, and VermilionCitySailor1Text.",
            "The source coordinate script triggers only while facing down at the dock guard; the candidate preserves that with requiresPlayerFacing=DOWN.",
            "The Red/Blue state-script handoff that allows passage is represented by the absence of a push-back movement on the ticket branch.",
        ],
    )
    source["coveredLabels"] = [
        "VermilionCityDefaultScript",
        "VermilionCitySailor1Text",
        "SSAnneTicketCheckCoords",
        "VermilionCityPlayerAllowedToPassScript",
        "VermilionCityPlayerMovingUp1Script",
    ]

    trigger = {
        "type": "coord",
        "label": "SSAnneTicketCheckCoords",
        "sourceLabel": "VermilionCityDefaultScript",
        "coordinates": [{"mapName": map_name, "x": 18, "y": 30}],
    }
    base_conditions = {"requiresPlayerFacing": "DOWN"}

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "VermilionCitySSAnneGuardPass",
            "trigger": trigger,
            "conditions": {
                **base_conditions,
                "requiresEventAbsent": "EVENT_SS_ANNE_LEFT",
                "requiresItem": "S_S_TICKET",
            },
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": ticket_prompt_lines + flashed_ticket_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "VermilionCitySSAnneGuardNoTicketBlocked",
            "trigger": trigger,
            "conditions": {
                **base_conditions,
                "requiresEventAbsent": "EVENT_SS_ANNE_LEFT",
                "requiresItemAbsent": "S_S_TICKET",
            },
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": ticket_prompt_lines + no_ticket_lines},
                {"type": "movePlayer", "movements": ["UP"]},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "VermilionCitySSAnneGuardShipDepartedBlocked",
            "trigger": trigger,
            "conditions": {
                **base_conditions,
                "requiresEvent": "EVENT_SS_ANNE_LEFT",
            },
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": departed_lines},
                {"type": "movePlayer", "movements": ["UP"]},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
    ]


def viridian_city_progress_blocker_candidates():
    map_name = "ViridianCity"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    text_labels = extract_map_text_labels(map_name)

    gym_block = blocks_by_label.get("ViridianCityCheckGymOpenScript")
    old_man_block = blocks_by_label.get("ViridianCityCheckGotPokedexScript")
    old_man_text_block = blocks_by_label.get("ViridianCityOldManSleepyText")
    if not gym_block or not old_man_block or not old_man_text_block:
        return []

    gym_clean = "\n".join(strip_comment(line) for line in gym_block["raw"].splitlines())
    required_gym = [
        "CheckEvent EVENT_VIRIDIAN_GYM_OPEN",
        "cp ~(1 << BIT_EARTHBADGE)",
        "SetEvent EVENT_VIRIDIAN_GYM_OPEN",
        "cp 8",
        "cp 32",
        "ld a, TEXT_VIRIDIANCITY_GYM_LOCKED",
        "call ViridianCityMovePlayerDownScript",
    ]
    if any(snippet not in gym_clean for snippet in required_gym):
        return []

    old_man_clean = "\n".join(strip_comment(line) for line in old_man_block["raw"].splitlines())
    required_old_man = [
        "CheckEvent EVENT_GOT_POKEDEX",
        "cp 9",
        "cp 19",
        "ld a, TEXT_VIRIDIANCITY_OLD_MAN_SLEEPY",
        "call ViridianCityMovePlayerDownScript",
    ]
    if any(snippet not in old_man_clean for snippet in required_old_man):
        return []

    old_man_local_refs = local_text_ref_map(old_man_text_block["raw"])
    old_man_lines = local_lines(text_labels, old_man_local_refs, ".PrivatePropertyText")
    gym_locked_lines = text_labels.get("ViridianCityGymLockedText", [])
    if not old_man_lines or not gym_locked_lines:
        return []

    source = source_metadata(
        map_name,
        "viridian_city_progress_blocker_v1",
        script_path,
        text_path,
        [
            "Generated from ViridianCityCheckGymOpenScript and ViridianCityCheckGotPokedexScript.",
            "The Red/Blue Earth Badge bit is represented by the neutral EVENT_GOT_EARTHBADGE flag.",
            "The source state-script handoff that moves the player down is represented by a direct movePlayer DOWN action.",
        ],
    )
    source["coveredLabels"] = [
        "ViridianCityCheckGymOpenScript",
        "ViridianCityCheckGotPokedexScript",
        "ViridianCityOldManSleepyText",
        "ViridianCityGymLockedText",
        "ViridianCityMovePlayerDownScript",
    ]

    gym_trigger = {
        "type": "coord",
        "label": "ViridianCityGymLockedCoords",
        "sourceLabel": "ViridianCityCheckGymOpenScript",
        "coordinates": [{"mapName": map_name, "x": 32, "y": 8}],
    }
    old_man_trigger = {
        "type": "coord",
        "label": "ViridianCityOldManSleepyCoords",
        "sourceLabel": "ViridianCityCheckGotPokedexScript",
        "coordinates": [{"mapName": map_name, "x": 19, "y": 9}],
    }

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "ViridianCityGymOpenFromEarthBadge",
            "trigger": gym_trigger,
            "conditions": {
                "requiresEvent": "EVENT_GOT_EARTHBADGE",
                "requiresEventAbsent": "EVENT_VIRIDIAN_GYM_OPEN",
            },
            "actions": [
                {"type": "setEvent", "event": "EVENT_VIRIDIAN_GYM_OPEN"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "ViridianCityGymLockedBlocked",
            "trigger": gym_trigger,
            "conditions": {
                "requiresEventsAbsent": ["EVENT_VIRIDIAN_GYM_OPEN", "EVENT_GOT_EARTHBADGE"],
            },
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": gym_locked_lines},
                {"type": "movePlayer", "movements": ["DOWN"]},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "ViridianCityOldManSleepyBlocked",
            "trigger": old_man_trigger,
            "conditions": {"requiresEventAbsent": "EVENT_GOT_POKEDEX"},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": old_man_lines},
                {"type": "movePlayer", "movements": ["DOWN"]},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
    ]


def elite_four_room_entrance_guard_candidates():
    specs = [
        {
            "mapName": "LoreleisRoom",
            "trainerWinFlag": "EVENT_BEAT_LORELEIS_ROOM_TRAINER_0",
            "autoWalkFlag": "EVENT_AUTOWALKED_INTO_LORELEIS_ROOM",
            "coordsLabel": "LoreleiEntranceCoords",
            "textConstant": "TEXT_LORELEISROOM_DONT_RUN_AWAY",
            "walkFunction": "LoreleiScriptWalkIntoRoom",
            "defaultLabel": "LoreleisRoomDefaultScript",
            "movingLabel": "LoreleisRoomPlayerIsMovingScript",
        },
        {
            "mapName": "BrunosRoom",
            "trainerWinFlag": "EVENT_BEAT_BRUNOS_ROOM_TRAINER_0",
            "autoWalkFlag": "EVENT_AUTOWALKED_INTO_BRUNOS_ROOM",
            "coordsLabel": "BrunoEntranceCoords",
            "textConstant": "TEXT_BRUNOSROOM_BRUNO_DONT_RUN_AWAY",
            "walkFunction": "BrunoScriptWalkIntoRoom",
            "defaultLabel": "BrunosRoomDefaultScript",
            "movingLabel": "BrunosRoomPlayerIsMovingScript",
        },
        {
            "mapName": "AgathasRoom",
            "trainerWinFlag": "EVENT_BEAT_AGATHAS_ROOM_TRAINER_0",
            "autoWalkFlag": "EVENT_AUTOWALKED_INTO_AGATHAS_ROOM",
            "coordsLabel": "AgathaEntranceCoords",
            "textConstant": "TEXT_AGATHASROOM_AGATHA_DONT_RUN_AWAY",
            "walkFunction": "AgathaScriptWalkIntoRoom",
            "defaultLabel": "AgathasRoomDefaultScript",
            "movingLabel": "AgathasRoomPlayerIsMovingScript",
        },
    ]

    candidates = []
    for spec in specs:
        map_name = spec["mapName"]
        script_path = SCRIPTS_DIR / f"{map_name}.asm"
        text_path = TEXT_DIR / f"{map_name}.asm"
        if not script_path.exists() or not text_path.exists():
            continue

        script_content = script_path.read_text()
        blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
        text_pointers = parse_text_pointer_map(script_content)
        text_labels = extract_map_text_labels(map_name)
        default_block = blocks_by_label.get(spec["defaultLabel"])
        walk_block = blocks_by_label.get(spec["walkFunction"])
        if not default_block or not walk_block:
            continue

        coords = parse_coord_array(script_content, spec["coordsLabel"])
        if coords != [{"x": 4, "y": 10}, {"x": 5, "y": 10}, {"x": 4, "y": 11}, {"x": 5, "y": 11}]:
            continue

        default_clean = "\n".join(strip_comment(line) for line in default_block["raw"].splitlines())
        required_default = [
            f"ld hl, {spec['coordsLabel']}",
            "call ArePlayerCoordsInArray",
            "cp $3",
            f"CheckAndSetEvent {spec['autoWalkFlag']}",
            f"jr z, {spec['walkFunction']}",
            f"ld a, {spec['textConstant']}",
            "ld a, D_UP",
            f"ld a, SCRIPT_{map_name.upper()}_PLAYER_IS_MOVING",
        ]
        if any(snippet not in default_clean for snippet in required_default):
            continue

        walk_clean = "\n".join(strip_comment(line) for line in walk_block["raw"].splitlines())
        if walk_clean.count("ld [hli], a") < 5 or "ld a, $6" not in walk_clean:
            continue

        dont_run_lines = lines_for_text_constant(spec["textConstant"], text_pointers, blocks_by_label, text_labels)
        if not dont_run_lines:
            continue

        source = source_metadata(
            map_name,
            "elite_four_room_entrance_guard_v1",
            script_path,
            text_path,
            [
                f"sourceBlock={spec['defaultLabel']}",
                f"coordsLabel={spec['coordsLabel']}",
                "Generated from the Red/Blue Elite Four room entrance/exit coordinate gate.",
                "The first visit auto-walk and pre-battle exit denial are represented as server-side movePlayer actions.",
            ],
        )
        source["coveredLabels"] = [
            spec["defaultLabel"],
            spec["walkFunction"],
            spec["movingLabel"],
            spec["coordsLabel"],
        ]

        map_id = source_map_id(map_name)
        if not map_id:
            continue

        trigger = {
            "type": "coord",
            "label": spec["coordsLabel"],
            "sourceLabel": spec["defaultLabel"],
            "coordinates": [{"mapName": map_name, "mapId": map_id, **coord} for coord in coords],
        }

        candidates.append(
            {
                "version": 1,
                "kind": "scriptEventCandidate",
                "mapName": map_name,
                "scriptLabel": f"{map_name}EntranceAutoWalk",
                "trigger": trigger,
                "conditions": {
                    "requiresEventsAbsent": [spec["autoWalkFlag"], spec["trainerWinFlag"]],
                },
                "actions": [
                    {"type": "lockInput"},
                    {"type": "setEvent", "event": spec["autoWalkFlag"]},
                    {"type": "movePlayer", "movements": ["UP", "UP", "UP", "UP", "UP", "UP"]},
                    {"type": "unlockInput"},
                ],
                "source": source,
                "confidence": "adapter",
            }
        )
        candidates.append(
            {
                "version": 1,
                "kind": "scriptEventCandidate",
                "mapName": map_name,
                "scriptLabel": f"{map_name}DontRunAway",
                "trigger": trigger,
                "conditions": {
                    "requiresEvent": spec["autoWalkFlag"],
                    "requiresEventAbsent": spec["trainerWinFlag"],
                },
                "actions": [
                    {"type": "lockInput"},
                    {"type": "dialogue", "lines": dont_run_lines},
                    {"type": "movePlayer", "movements": ["UP"]},
                    {"type": "unlockInput"},
                ],
                "source": source,
                "confidence": "adapter",
            }
        )

    return candidates


def route23_guard_y_coords(script_content):
    coords = []
    in_table = False
    for raw_line in script_content.splitlines():
        stripped = strip_comment(raw_line)
        if stripped == "Route23GuardsYCoords:":
            in_table = True
            continue
        if not in_table:
            continue
        if re.match(r"db\s+-1\b", stripped):
            break
        match = re.match(r"db\s+(\d+)\b", stripped)
        if match:
            coords.append(int(match.group(1)))
            continue
        if stripped:
            break
    return coords


def cinnabar_lab_fossil_revival_candidates():
    map_name = "CinnabarLabFossilRoom"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    engine_path = CINNABAR_LAB_ENGINE_FILE
    if not script_path.exists() or not text_path.exists() or not engine_path.exists():
        return []

    script_content = script_path.read_text()
    engine_content = engine_path.read_text()
    clean_script = "\n".join(strip_comment(line) for line in script_content.splitlines())
    clean_engine = "\n".join(strip_comment(line) for line in engine_content.splitlines())
    required_script_snippets = [
        "call Lab4Script_GetFossilsInBag",
        "farcall GiveFossilToCinnabarLab",
        "CheckEvent EVENT_GAVE_FOSSIL_TO_LAB",
        "CheckEventAfterBranchReuseA EVENT_LAB_STILL_REVIVING_FOSSIL",
        "SetEvent EVENT_LAB_HANDING_OVER_FOSSIL_MON",
        "call GivePokemon",
        "ResetEvents EVENT_GAVE_FOSSIL_TO_LAB, EVENT_LAB_STILL_REVIVING_FOSSIL, EVENT_LAB_HANDING_OVER_FOSSIL_MON",
    ]
    required_engine_snippets = [
        "cp DOME_FOSSIL",
        "cp HELIX_FOSSIL",
        "ld b, AERODACTYL",
        "ld b, OMANYTE",
        "ld b, KABUTO",
        "call YesNoChoice",
        "SetEvents EVENT_GAVE_FOSSIL_TO_LAB, EVENT_LAB_STILL_REVIVING_FOSSIL",
    ]
    if any(snippet not in clean_script for snippet in required_script_snippets):
        return []
    if any(snippet not in clean_engine for snippet in required_engine_snippets):
        return []

    fossils = parse_db_constants_array(script_content, "FossilsList")
    specs = [
        {
            "item": "DOME_FOSSIL",
            "pokemon": "KABUTO",
            "revivingFlag": "EVENT_LAB_REVIVING_DOME_FOSSIL",
            "submitLabel": "CinnabarLabSubmitDomeFossil",
            "receiveLabel": "CinnabarLabReceiveKabuto",
        },
        {
            "item": "HELIX_FOSSIL",
            "pokemon": "OMANYTE",
            "revivingFlag": "EVENT_LAB_REVIVING_HELIX_FOSSIL",
            "submitLabel": "CinnabarLabSubmitHelixFossil",
            "receiveLabel": "CinnabarLabReceiveOmanyte",
        },
        {
            "item": "OLD_AMBER",
            "pokemon": "AERODACTYL",
            "revivingFlag": "EVENT_LAB_REVIVING_OLD_AMBER",
            "submitLabel": "CinnabarLabSubmitOldAmber",
            "receiveLabel": "CinnabarLabReceiveAerodactyl",
        },
    ]
    if fossils != [spec["item"] for spec in specs]:
        return []

    text_pointers = parse_text_pointer_map(script_content)
    text_constant = text_pointers.get("CinnabarLabFossilRoomScientist1Text")
    if text_constant != "TEXT_CINNABARLABFOSSILROOM_SCIENTIST1":
        return []

    source = source_metadata(
        map_name,
        "cinnabar_lab_fossil_revival_v1",
        script_path,
        text_path,
        [
            "Generated from the Cinnabar Lab fossil scientist, FossilsList, and engine/events/cinnabar_lab.asm.",
            "The Game Boy stores selected fossil identity in WRAM; candidates emit item-specific revival flags so downstream runtimes can persist that state.",
            "The original fossil-selection menu is represented as one conditioned branch per fossil item.",
            "Party-full/no-room behavior remains downstream GivePokemon behavior.",
        ],
    )
    source["enginePath"] = str(engine_path.relative_to(PROJECT_ROOT))
    source["coveredLabels"] = [
        "CinnabarLabFossilRoomScientist1Text",
        "Lab4Script_GetFossilsInBag",
        "FossilsList",
        "GiveFossilToCinnabarLab",
        "LoadFossilItemAndMonName",
    ]

    trigger = {
        "type": "npc_click",
        "label": text_constant,
        "sourceLabel": "CinnabarLabFossilRoomScientist1Text",
    }
    intro_lines = [
        "Hiya! I am important doctor!",
        "I study here rare POKEMON fossils!",
        "You! Have you a fossil for me?",
    ]
    walk_lines = [
        "I take a little time!",
        "You go for walk a little while!",
    ]

    candidates = [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "CinnabarLabFossilNoFossils",
            "trigger": trigger,
            "conditions": {"requiresEventAbsent": "EVENT_GAVE_FOSSIL_TO_LAB"},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "speaker": "DOCTOR", "lines": intro_lines + ["No! Is too bad!"]},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "CinnabarLabFossilStillReviving",
            "trigger": trigger,
            "conditions": {"requiresEvent": "EVENT_LAB_STILL_REVIVING_FOSSIL"},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "speaker": "DOCTOR", "lines": walk_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "CinnabarLabFossilReadyAfterWalk",
            "trigger": {
                "type": "map_script",
                "label": "CinnabarLabFossilReadyAfterWalk",
                "sourceLabel": "CinnabarLabFossilRoomScientist1Text",
            },
            "conditions": {"requiresEvent": "EVENT_LAB_STILL_REVIVING_FOSSIL"},
            "actions": [
                {"type": "resetEvent", "event": "EVENT_LAB_STILL_REVIVING_FOSSIL"},
            ],
            "source": source,
            "confidence": "adapter",
        },
    ]

    for spec in specs:
        item_name = item_display_name(spec["item"])
        pokemon_name = item_display_name(spec["pokemon"])
        candidates.append(
            {
                "version": 1,
                "kind": "scriptEventCandidate",
                "mapName": map_name,
                "scriptLabel": spec["submitLabel"],
                "trigger": trigger,
                "conditions": {
                    "requiresEventAbsent": "EVENT_GAVE_FOSSIL_TO_LAB",
                    "requiresItem": spec["item"],
                },
                "actions": [
                    {"type": "lockInput"},
                    {
                        "type": "dialogue",
                        "speaker": "DOCTOR",
                        "lines": intro_lines
                        + [
                            f"Oh! That is {item_name}!",
                            f"It is fossil of {pokemon_name}, a POKEMON that is already extinct!",
                            "My Resurrection Machine will make that POKEMON live again!",
                        ],
                    },
                    {
                        "type": "choice",
                        "speaker": "DOCTOR",
                        "textConstant": text_constant,
                        "prompt": f"Give {item_name}?",
                        "noLines": ["Aiyah! You come again!"],
                        "yesLines": [
                            "So! You hurry and give me that!",
                            f"(PLAYER) handed over {item_name}!",
                        ],
                    },
                    {"type": "takeItem", "itemConstant": spec["item"], "quantity": 1},
                    {"type": "dialogue", "speaker": "DOCTOR", "lines": walk_lines},
                    {"type": "setEvent", "event": "EVENT_GAVE_FOSSIL_TO_LAB"},
                    {"type": "setEvent", "event": "EVENT_LAB_STILL_REVIVING_FOSSIL"},
                    {"type": "setEvent", "event": spec["revivingFlag"]},
                    {"type": "unlockInput"},
                ],
                "source": source,
                "confidence": "adapter",
            }
        )
        candidates.append(
            {
                "version": 1,
                "kind": "scriptEventCandidate",
                "mapName": map_name,
                "scriptLabel": spec["receiveLabel"],
                "trigger": trigger,
                "conditions": {
                    "requiresEvent": spec["revivingFlag"],
                    "requiresEventAbsent": "EVENT_LAB_STILL_REVIVING_FOSSIL",
                },
                "actions": [
                    {"type": "lockInput"},
                    {
                        "type": "dialogue",
                        "speaker": "DOCTOR",
                        "lines": [
                            "Where were you?",
                            "Your fossil is back to life!",
                            f"It was {pokemon_name} like I think!",
                        ],
                    },
                    {
                        "type": "givePokemon",
                        "pokemonConstant": spec["pokemon"],
                        "level": 30,
                        "message": f"Received {pokemon_name}!",
                    },
                    {"type": "resetEvent", "event": "EVENT_GAVE_FOSSIL_TO_LAB"},
                    {"type": "resetEvent", "event": "EVENT_LAB_STILL_REVIVING_FOSSIL"},
                    {"type": "resetEvent", "event": spec["revivingFlag"]},
                    {"type": "unlockInput"},
                ],
                "source": source,
                "confidence": "adapter",
            }
        )

    return candidates


def lines_for_text_constant(text_constant, text_pointers, blocks_by_label, text_labels):
    for label, constant in text_pointers.items():
        if constant != text_constant:
            continue
        block = blocks_by_label.get(label)
        if not block:
            return []
        return lines_for_labels(text_labels, ordered_text_refs(block["raw"]))
    return []


def lines_for_script_text_ref(ref, blocks_by_label, text_labels, local_ref_map):
    if ref.startswith("."):
        return lines_for_labels(text_labels, local_ref_map.get(ref, []))
    block = blocks_by_label.get(ref)
    if not block:
        return []
    return lines_for_labels(text_labels, ordered_text_refs(block["raw"]))


def lines_for_script_text_constant(text_constant, all_const_map, blocks_by_label, text_labels):
    label = all_const_map.get(text_constant)
    if not label:
        return []
    block = blocks_by_label.get(label)
    if not block:
        return []
    return lines_for_labels(text_labels, ordered_text_refs(block["raw"]))


def story_item_reward_specs():
    return [
        {
            "mapName": "BikeShop",
            "blockLabel": "BikeShopClerkText",
            "scriptLabel": "BikeShopClerkVoucherExchange",
            "triggerType": "npc_click",
            "requiresItem": "BIKE_VOUCHER",
            "requiresEventAbsent": "EVENT_GOT_BICYCLE",
            "requiredSnippets": [
                "CheckEvent EVENT_GOT_BICYCLE",
                "ld b, BIKE_VOUCHER",
                "lb bc, BICYCLE, 1",
                "farcall RemoveItemByID",
                "SetEvent EVENT_GOT_BICYCLE",
            ],
            "actions": [
                {"kind": "dialogue", "ref": "BikeShopClerkOhThatsAVoucherText"},
                {"kind": "giveItem", "item": "BICYCLE", "quantity": 1},
                {"kind": "takeItem", "item": "BIKE_VOUCHER", "quantity": 1},
                {"kind": "setEvent", "event": "EVENT_GOT_BICYCLE"},
                {"kind": "dialogue", "ref": "BikeShopExchangedVoucherText", "hydrateItem": "BICYCLE"},
            ],
        },
        {
            "mapName": "BillsHouse",
            "blockLabel": "BillsHouseBillSSTicketText",
            "scriptLabel": "BillsHouseBillSSTicketReward",
            "triggerType": "npc_click",
            "requiresEventAbsent": "EVENT_GOT_SS_TICKET",
            "requiredSnippets": [
                "CheckEvent EVENT_GOT_SS_TICKET",
                "lb bc, S_S_TICKET, 1",
                "SetEvent EVENT_GOT_SS_TICKET",
                "predef ShowObject",
                "predef HideObject",
            ],
            "actions": [
                {"kind": "dialogue", "ref": ".ThankYouText"},
                {"kind": "giveItem", "item": "S_S_TICKET", "quantity": 1},
                {"kind": "dialogue", "ref": ".SSTicketReceivedText", "hydrateItem": "S_S_TICKET"},
                {"kind": "setEvent", "event": "EVENT_GOT_SS_TICKET"},
                {"kind": "showObject", "objectKey": "HS_CERULEAN_GUARD_1"},
                {"kind": "hideObject", "objectKey": "HS_CERULEAN_GUARD_2"},
                {"kind": "dialogue", "ref": ".WhyDontYouGoInsteadOfMeText"},
            ],
        },
        {
            "mapName": "ViridianMart",
            "blockLabel": "ViridianMartOaksParcelScript",
            "scriptLabel": "ViridianMartOaksParcel",
            "triggerType": "npc_click",
            "triggerLabel": "TEXT_VIRIDIANMART_CLERK",
            "requiresEvent": "EVENT_OAK_ASKED_TO_CHOOSE_MON",
            "requiresEventAbsent": "EVENT_GOT_OAKS_PARCEL",
            "requiredSnippets": [
                "TEXT_VIRIDIANMART_CLERK_PARCEL_QUEST",
                "lb bc, OAKS_PARCEL, 1",
                "SetEvent EVENT_GOT_OAKS_PARCEL",
            ],
            "actions": [
                {"kind": "dialogueTextConstant", "textConstant": "TEXT_VIRIDIANMART_CLERK_YOU_CAME_FROM_PALLET_TOWN"},
                {"kind": "dialogueTextConstant", "textConstant": "TEXT_VIRIDIANMART_CLERK_PARCEL_QUEST", "hydrateItem": "OAKS_PARCEL"},
                {"kind": "giveItem", "item": "OAKS_PARCEL", "quantity": 1},
                {"kind": "setEvent", "event": "EVENT_GOT_OAKS_PARCEL"},
            ],
        },
        {
            "mapName": "WardensHouse",
            "blockLabel": "WardensHouseWardenText",
            "scriptLabel": "WardensHouseWardenHM04Reward",
            "triggerType": "npc_click",
            "requiresItem": "GOLD_TEETH",
            "requiresEventAbsent": "EVENT_GOT_HM04",
            "requiredSnippets": [
                "call IsItemInBag",
                "farcall RemoveItemByID",
                "SetEvent EVENT_GAVE_GOLD_TEETH",
                "lb bc, HM_STRENGTH, 1",
                "SetEvent EVENT_GOT_HM04",
            ],
            "actions": [
                {"kind": "dialogue", "ref": ".GaveTheGoldTeethText"},
                {"kind": "dialogue", "ref": ".PoppedInHisTeethText"},
                {"kind": "takeItem", "item": "GOLD_TEETH", "quantity": 1},
                {"kind": "setEvent", "event": "EVENT_GAVE_GOLD_TEETH"},
                {"kind": "dialogue", "ref": ".ThanksText"},
                {"kind": "giveItem", "item": "HM_STRENGTH", "quantity": 1},
                {"kind": "dialogue", "ref": ".ReceivedHM04Text", "hydrateItem": "HM_STRENGTH"},
                {"kind": "setEvent", "event": "EVENT_GOT_HM04"},
            ],
        },
        {
            "mapName": "Museum1F",
            "blockLabel": "Museum1FScientist2Text",
            "scriptLabel": "Museum1FScientist2OldAmberReward",
            "triggerType": "npc_click",
            "requiresEventAbsent": "EVENT_GOT_OLD_AMBER",
            "requiredSnippets": [
                "CheckEvent EVENT_GOT_OLD_AMBER",
                "lb bc, OLD_AMBER, 1",
                "SetEvent EVENT_GOT_OLD_AMBER",
                "predef HideObject",
            ],
            "actions": [
                {"kind": "dialogue", "ref": ".TakeThisToAPokemonLabText"},
                {"kind": "giveItem", "item": "OLD_AMBER", "quantity": 1},
                {"kind": "setEvent", "event": "EVENT_GOT_OLD_AMBER"},
                {"kind": "hideObject", "objectKey": "HS_OLD_AMBER"},
                {"kind": "dialogue", "ref": ".ReceivedOldAmberText", "hydrateItem": "OLD_AMBER"},
            ],
        },
        {
            "mapName": "SSAnneCaptainsRoom",
            "blockLabel": "SSAnneCaptainsRoomCaptainText",
            "scriptLabel": "SSAnneCaptainsRoomCaptainHM01Reward",
            "triggerType": "npc_click",
            "requiresEventAbsent": "EVENT_GOT_HM01",
            "requiredSnippets": [
                "CheckEvent EVENT_GOT_HM01",
                "lb bc, HM_CUT, 1",
                "SetEvent EVENT_GOT_HM01",
            ],
            "actions": [
                {"kind": "dialogue", "ref": "SSAnneCaptainsRoomRubCaptainsBackText"},
                {"kind": "setEvent", "event": "EVENT_RUBBED_CAPTAINS_BACK"},
                {"kind": "dialogue", "ref": "SSAnneCaptainsRoomCaptainIFeelMuchBetterText"},
                {"kind": "giveItem", "item": "HM_CUT", "quantity": 1},
                {"kind": "dialogue", "ref": "SSAnneCaptainsRoomCaptainReceivedHM01Text", "hydrateItem": "HM_CUT"},
                {"kind": "setEvent", "event": "EVENT_GOT_HM01"},
            ],
        },
        {
            "mapName": "BluesHouse",
            "blockLabel": "BluesHouseDaisySittingText",
            "scriptLabel": "BluesHouseDaisyTownMapReward",
            "triggerType": "npc_click",
            "requiresEvent": "EVENT_GOT_POKEDEX",
            "requiresEventAbsent": "EVENT_GOT_TOWN_MAP",
            "requiredSnippets": [
                "CheckEvent EVENT_GOT_POKEDEX",
                "lb bc, TOWN_MAP, 1",
                "SetEvent EVENT_GOT_TOWN_MAP",
                "predef HideObject",
            ],
            "actions": [
                {"kind": "dialogue", "ref": "BluesHouseDaisyOfferMapText"},
                {"kind": "giveItem", "item": "TOWN_MAP", "quantity": 1},
                {"kind": "hideObject", "objectKey": "HS_TOWN_MAP"},
                {"kind": "dialogue", "ref": "GotMapText", "hydrateItem": "TOWN_MAP"},
                {"kind": "setEvent", "event": "EVENT_GOT_TOWN_MAP"},
            ],
        },
        {
            "mapName": "CopycatsHouse2F",
            "blockLabel": "CopycatsHouse2FCopycatText",
            "scriptLabel": "CopycatsHouse2FCopycatTM31Reward",
            "triggerType": "npc_click",
            "requiresItem": "POKE_DOLL",
            "requiresEventAbsent": "EVENT_GOT_TM31",
            "requiredSnippets": [
                "call IsItemInBag",
                "lb bc, TM_MIMIC, 1",
                "farcall RemoveItemByID",
                "SetEvent EVENT_GOT_TM31",
            ],
            "actions": [
                {"kind": "dialogue", "ref": ".DoYouLikePokemonText"},
                {"kind": "dialogue", "ref": ".TM31PreReceiveText"},
                {"kind": "giveItem", "item": "TM_MIMIC", "quantity": 1},
                {"kind": "dialogue", "ref": ".ReceivedTM31Text", "hydrateItem": "TM_MIMIC"},
                {"kind": "takeItem", "item": "POKE_DOLL", "quantity": 1},
                {"kind": "setEvent", "event": "EVENT_GOT_TM31"},
            ],
        },
    ]


def story_item_reward_candidate_for_spec(spec):
    map_name = spec["mapName"]
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    block = blocks_by_label.get(spec["blockLabel"])
    if not block:
        return []

    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    if any(snippet not in clean for snippet in spec.get("requiredSnippets", [])):
        return []

    text_pointers = parse_text_pointer_map(script_content)
    all_const_map = parse_dw_const_map(script_content)
    text_labels = extract_map_text_labels(map_name)
    local_ref_map = local_text_ref_map(block["raw"])
    covered_labels = {spec["blockLabel"]}

    actions = [{"type": "lockInput"}]
    for action in spec["actions"]:
        kind = action["kind"]
        if kind == "dialogue":
            lines = lines_for_script_text_ref(action["ref"], blocks_by_label, text_labels, local_ref_map)
            if action.get("hydrateItem"):
                lines = hydrate_received_item_lines(lines, action["hydrateItem"])
            if not lines:
                return []
            actions.append({"type": "dialogue", "lines": lines})
            actions.extend(sound_actions_for_script_text_ref(action["ref"], blocks_by_label, block["raw"]))
            if not action["ref"].startswith("."):
                covered_labels.add(action["ref"])
        elif kind == "dialogueTextConstant":
            lines = lines_for_script_text_constant(action["textConstant"], all_const_map, blocks_by_label, text_labels)
            if action.get("hydrateItem"):
                lines = hydrate_received_item_lines(lines, action["hydrateItem"])
            if not lines:
                return []
            actions.append({"type": "dialogue", "lines": lines})
            actions.extend(sound_actions_for_text_constant(action["textConstant"], all_const_map, blocks_by_label))
            if label := all_const_map.get(action["textConstant"]):
                covered_labels.add(label)
        elif kind in {"giveItem", "takeItem"}:
            actions.append(
                {
                    "type": kind,
                    "itemConstant": action["item"],
                    "quantity": action.get("quantity", 1),
                }
            )
        elif kind == "setEvent":
            actions.append({"type": "setEvent", "event": action["event"]})
        elif kind in {"hideObject", "showObject"}:
            actions.append(
                {
                    "type": kind,
                    "objectKey": action.get("objectKey", ""),
                    "textConstant": action.get("textConstant", ""),
                }
            )
        else:
            return []
    actions.append({"type": "unlockInput"})

    trigger_label = spec.get("triggerLabel") or text_pointers.get(spec["blockLabel"])
    if not trigger_label:
        return []

    conditions = {}
    for source_key, target_key in [
        ("requiresEvent", "requiresEvent"),
        ("requiresEventAbsent", "requiresEventAbsent"),
        ("requiresItem", "requiresItem"),
        ("requiresItemAbsent", "requiresItemAbsent"),
    ]:
        if spec.get(source_key):
            conditions[target_key] = spec[source_key]

    source = source_metadata(
        map_name,
        "story_item_reward_v1",
        script_path,
        text_path,
        [
            f"sourceBlock={spec['blockLabel']}",
            "Generated from fixed Red/Blue story item reward state machines.",
            "Bag-full/no-room branches remain downstream inventory behavior.",
        ],
    )
    source["coveredLabels"] = unique_sorted(covered_labels)

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": spec["scriptLabel"],
            "trigger": {
                "type": spec["triggerType"],
                "label": trigger_label,
                "sourceLabel": spec["blockLabel"],
            },
            "conditions": conditions,
            "actions": actions,
            "source": source,
            "confidence": "adapter",
        }
    ]


def story_item_reward_candidates():
    candidates = []
    for spec in story_item_reward_specs():
        candidates.extend(story_item_reward_candidate_for_spec(spec))
    return candidates


def dialogue_action_for_script_ref(ref, blocks_by_label, text_labels, local_ref_map, hydrate_item=""):
    lines = lines_for_script_text_ref(ref, blocks_by_label, text_labels, local_ref_map)
    if hydrate_item:
        lines = hydrate_received_item_lines(lines, hydrate_item)
    if not lines:
        return None
    return {"type": "dialogue", "lines": lines}


def trainer_object_for_text(map_name, text_constant):
    for obj in parse_object_events_for_map(map_name):
        if obj["textConstant"] == text_constant and obj["payload"].startswith("OPP_") and obj["level"] > 0:
            return obj
    return None


def trainer_battle_action_from_object(obj, win_flag, post_win_actions):
    return {
        "type": "startTrainerBattle",
        "trainerClass": obj["payload"].removeprefix("OPP_"),
        "partyIndex": obj["level"],
        "trainerName": obj["payload"].removeprefix("OPP_").replace("_", " "),
        "winFlag": win_flag,
        "postWinActions": post_win_actions,
    }


def gym_leader_battle_text_candidates_for_block(map_name, script_path, text_path, text_pointers, text_labels, block):
    text_constant = text_pointers.get(block["label"], "")
    if not text_constant:
        return []

    trainer_obj = trainer_object_for_text(map_name, text_constant)
    if not trainer_obj:
        return []

    raw = block["raw"]
    clean = "\n".join(strip_comment(line) for line in raw.splitlines())
    if "wGymLeaderNo" not in clean or "CheckEventReuseA" not in clean:
        return []

    beat_match = re.search(r"\bCheckEvent\s+(EVENT_BEAT_[A-Z0-9_]+)", clean)
    tm_match = re.search(r"\bCheckEventReuseA\s+(EVENT_GOT_TM\d+)", clean)
    if not beat_match or not tm_match:
        return []
    beat_flag = beat_match.group(1)
    tm_flag = tm_match.group(1)

    before_branch_match = re.search(
        r"\bCheckEvent\s+" + re.escape(beat_flag) + r"\s*\n\s*jr\s+z,\s+(\.\w+)",
        clean,
    )
    after_branch_match = re.search(
        r"\bCheckEventReuseA\s+" + re.escape(tm_flag) + r"\s*\n\s*jr\s+nz,\s+(\.\w+)",
        clean,
    )
    before_label = before_branch_match.group(1) if before_branch_match else ".beforeBeat"
    after_label = after_branch_match.group(1) if after_branch_match else ".afterBeat"

    before_match = re.search(
        r"^" + re.escape(before_label) + r":?\s*$.*?\bld\s+hl,\s+(\.?\w+)\s*\n\s*call\s+PrintText\b",
        clean,
        re.M | re.S,
    )
    after_match = re.search(
        r"^" + re.escape(after_label) + r":?\s*$.*?\bld\s+hl,\s+(\.?\w+)\s*\n\s*call\s+PrintText\b",
        clean,
        re.M | re.S,
    )
    if not before_match and not after_match:
        return []

    blocks_by_label = {b["label"]: b for b in extract_label_blocks(script_path.read_text())}
    local_ref_map = local_text_ref_map(raw)
    covered_labels = {block["label"]}

    source = source_metadata(
        map_name,
        "gym_leader_battle_text_v1",
        script_path,
        text_path,
        [
            f"sourceBlock={block['label']}",
            "Generated from gym leader pre-battle/start-battle and post-TM advice branches.",
            "Post-battle badge/TM reward ownership remains in the separate gym_leader_tm_reward candidates or downstream overrides.",
        ],
    )

    candidates = []
    if before_match:
        before_ref = before_match.group(1)
        before_lines = lines_for_script_text_ref(before_ref, blocks_by_label, text_labels, local_ref_map)
        if before_lines:
            if not before_ref.startswith("."):
                covered_labels.add(before_ref)
            candidates.append(
                {
                    "version": 1,
                    "kind": "scriptEventCandidate",
                    "mapName": map_name,
                    "scriptLabel": f"{block['label']}PreBattle",
                    "trigger": {
                        "type": "npc_click",
                        "label": text_constant,
                        "sourceLabel": block["label"],
                    },
                    "conditions": {"requiresEventAbsent": beat_flag},
                    "actions": [
                        {"type": "lockInput"},
                        {"type": "dialogue", "lines": before_lines},
                        trainer_battle_action_from_object(trainer_obj, beat_flag, []),
                        {"type": "unlockInput"},
                    ],
                    "source": source,
                    "confidence": "adapter",
                }
            )

    if after_match:
        after_ref = after_match.group(1)
        after_lines = lines_for_script_text_ref(after_ref, blocks_by_label, text_labels, local_ref_map)
        if after_lines:
            if not after_ref.startswith("."):
                covered_labels.add(after_ref)
            candidates.append(
                {
                    "version": 1,
                    "kind": "scriptEventCandidate",
                    "mapName": map_name,
                    "scriptLabel": f"{block['label']}{pascal_from_constant(tm_flag)}Set",
                    "trigger": {
                        "type": "npc_click",
                        "label": text_constant,
                        "sourceLabel": block["label"],
                    },
                    "conditions": {"requiresEvent": tm_flag},
                    "actions": [
                        {"type": "lockInput"},
                        {"type": "dialogue", "lines": after_lines},
                        {"type": "unlockInput"},
                    ],
                    "source": source,
                    "confidence": "adapter",
                }
            )

    source["coveredLabels"] = unique_sorted(covered_labels)
    return candidates


def gym_leader_battle_text_candidates():
    candidates = []
    for script_path in sorted(SCRIPTS_DIR.glob("*Gym.asm")):
        map_name = script_path.stem
        text_path = TEXT_DIR / f"{map_name}.asm"
        if not text_path.exists():
            continue
        script_content = script_path.read_text()
        text_pointers = parse_text_pointer_map(script_content)
        if not text_pointers:
            continue
        text_labels = extract_map_text_labels(map_name)
        for block in extract_label_blocks(script_content):
            candidates.extend(
                gym_leader_battle_text_candidates_for_block(
                    map_name,
                    script_path,
                    text_path,
                    text_pointers,
                    text_labels,
                    block,
                )
            )
    return candidates


def cinnabar_gym_trainer_text_candidate_for_block(script_path, text_path, text_pointers, text_labels, block):
    label_match = re.fullmatch(r"CinnabarGymSuperNerd(\d+)", block["label"])
    if not label_match:
        return []

    text_constant = text_pointers.get(block["label"], "")
    if not text_constant:
        return []
    trainer_obj = trainer_object_for_text("CinnabarGym", text_constant)
    if not trainer_obj:
        return []

    raw = block["raw"]
    clean = "\n".join(strip_comment(line) for line in raw.splitlines())
    if "call CinnabarGymSetTrainerHeader" not in clean or "jp CinnabarGymStartBattleScript" not in clean:
        return []

    beat_match = re.search(r"\bCheckEvent\s+(EVENT_BEAT_CINNABAR_GYM_TRAINER_(\d+))", clean)
    battle_match = re.search(
        r"\bCheckEvent\s+EVENT_BEAT_CINNABAR_GYM_TRAINER_\d+"
        r".*?\bld\s+hl,\s+(\.\w+)\s*\n\s*call\s+PrintText"
        r"\s*\n\s*ld\s+hl,\s+(\.\w+)\s*\n\s*ld\s+de,\s+\2"
        r"\s*\n\s*call\s+SaveEndBattleTextPointers"
        r".*?^\.defeated:?\s*$"
        r"\s*ld\s+hl,\s+(\.\w+)\s*\n\s*call\s+PrintText",
        clean,
        re.M | re.S,
    )
    if not beat_match or not battle_match:
        return []

    beat_flag = beat_match.group(1)
    trainer_index = int(beat_match.group(2))
    gate_flag = f"EVENT_CINNABAR_GYM_GATE{trainer_index}_UNLOCKED"
    battle_ref, end_ref, after_ref = battle_match.groups()

    blocks_by_label = {b["label"]: b for b in extract_label_blocks(script_path.read_text())}
    local_ref_map = local_text_ref_map(raw)
    battle_lines = lines_for_script_text_ref(battle_ref, blocks_by_label, text_labels, local_ref_map)
    end_lines = lines_for_script_text_ref(end_ref, blocks_by_label, text_labels, local_ref_map)
    after_lines = lines_for_script_text_ref(after_ref, blocks_by_label, text_labels, local_ref_map)
    if not battle_lines or not end_lines or not after_lines:
        return []

    source = source_metadata(
        "CinnabarGym",
        "cinnabar_gym_trainer_text_v1",
        script_path,
        text_path,
        [
            f"sourceBlock={block['label']}",
            "Generated from Cinnabar Gym's custom trainer text that routes through CinnabarGymStartBattleScript.",
            "The source sets the matching EVENT_CINNABAR_GYM_GATE*_UNLOCKED flag from the same trainer index after battle.",
            "Quiz sign branching remains a separate runtime/source-data concern.",
        ],
    )
    source["coveredLabels"] = [block["label"]]

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": "CinnabarGym",
            "scriptLabel": f"{block['label']}Battle",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": block["label"],
            },
            "conditions": {"requiresEventAbsent": beat_flag},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": battle_lines},
                trainer_battle_action_from_object(
                    trainer_obj,
                    beat_flag,
                    [
                        {"type": "dialogue", "lines": end_lines},
                        {"type": "setEvent", "event": gate_flag},
                    ],
                ),
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": "CinnabarGym",
            "scriptLabel": f"{block['label']}AfterBattle",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": block["label"],
            },
            "conditions": {"requiresEvent": beat_flag},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": after_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
    ]


def cinnabar_gym_trainer_text_candidates():
    map_name = "CinnabarGym"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []
    script_content = script_path.read_text()
    text_pointers = parse_text_pointer_map(script_content)
    text_labels = extract_map_text_labels(map_name)
    candidates = []
    for block in extract_label_blocks(script_content):
        candidates.extend(
            cinnabar_gym_trainer_text_candidate_for_block(
                script_path,
                text_path,
                text_pointers,
                text_labels,
                block,
            )
        )
    return candidates


def cinnabar_gym_map_load_reset_candidate():
    map_name = "CinnabarGym"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists():
        return []

    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_path.read_text())}
    block = blocks_by_label.get("CinnabarGymSetMapAndTiles")
    if not block:
        return []

    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    required_snippets = [
        "call nz, .LoadNames",
        "call nz, UpdateCinnabarGymGateTileBlocks",
        "ResetEvent EVENT_2A7",
    ]
    if not all(snippet in clean for snippet in required_snippets):
        return []

    source = source_metadata(
        map_name,
        "cinnabar_gym_map_load_reset_v1",
        script_path,
        text_path,
        [
            "sourceBlock=CinnabarGymSetMapAndTiles",
            "Generated for the source map-load reset of temporary EVENT_2A7.",
            "Gym leader/city name loading is presentation-only, and Cinnabar Gym gate tile state is handled by downstream event tile overrides.",
        ],
    )
    source["coveredLabels"] = ["CinnabarGymSetMapAndTiles"]

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "CinnabarGymMapLoadReset",
            "trigger": {
                "type": "map_script",
                "label": "CinnabarGymSetMapAndTiles",
                "sourceLabel": "CinnabarGymSetMapAndTiles",
            },
            "conditions": {},
            "actions": [{"type": "resetEvent", "event": "EVENT_2A7"}],
            "source": source,
            "confidence": "adapter",
        }
    ]


def indigo_plateau_lobby_map_load_reset_candidate():
    map_name = "IndigoPlateauLobby"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    constants_path = EVENT_CONSTANTS_FILE
    if not script_path.exists() or not constants_path.exists():
        return []

    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_path.read_text())}
    block = blocks_by_label.get("IndigoPlateauLobby_Script")
    if not block:
        return []

    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    required_snippets = [
        "ResetEvent EVENT_VICTORY_ROAD_1_BOULDER_ON_SWITCH",
        "bit BIT_STARTED_ELITE_4, [hl]",
        "res BIT_STARTED_ELITE_4, [hl]",
        "ResetEventRange INDIGO_PLATEAU_EVENTS_START, EVENT_LANCES_ROOM_LOCK_DOOR",
    ]
    if not all(snippet in clean for snippet in required_snippets):
        return []

    elite_flags = parse_const_sequence(
        constants_path,
        "DEF INDIGO_PLATEAU_EVENTS_START EQU const_value",
        "const EVENT_BEAT_CHAMPION_RIVAL",
    )
    if not elite_flags or elite_flags[-1] != "EVENT_LANCES_ROOM_LOCK_DOOR":
        return []

    reset_flags = ["EVENT_VICTORY_ROAD_1_BOULDER_ON_SWITCH", *elite_flags]

    source = source_metadata(
        map_name,
        "indigo_plateau_lobby_map_load_reset_v1",
        script_path,
        text_path,
        [
            "Generated from IndigoPlateauLobby_Script map-load cleanup.",
            "The source resets EVENT_VICTORY_ROAD_1_BOULDER_ON_SWITCH when the map-load bit is set.",
            "The source conditionally clears Elite Four progress from INDIGO_PLATEAU_EVENTS_START through EVENT_LANCES_ROOM_LOCK_DOOR after BIT_STARTED_ELITE_4; the candidate represents this as idempotent map-load reset actions.",
        ],
    )
    source["coveredLabels"] = ["IndigoPlateauLobby_Script"]
    source["resetRange"] = {
        "start": "INDIGO_PLATEAU_EVENTS_START",
        "end": "EVENT_LANCES_ROOM_LOCK_DOOR",
        "flags": elite_flags,
    }

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "IndigoPlateauLobbyMapLoadReset",
            "trigger": {
                "type": "map_script",
                "label": "IndigoPlateauLobby_Script",
                "sourceLabel": "IndigoPlateauLobby_Script",
            },
            "conditions": {},
            "actions": [{"type": "resetEvent", "event": flag} for flag in reset_flags],
            "source": source,
            "confidence": "adapter",
        }
    ]


def cerulean_city_rival_candidates():
    map_name = "CeruleanCity"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    required_labels = [
        "CeruleanCityDefaultScript",
        "CeruleanCityRivalBattleScript",
        "CeruleanCityRivalDefeatedScript",
        "CeruleanCityRivalCleanupScript",
        "CeruleanCityRivalText",
        "CeruleanCityRivalDefeatedText",
        "CeruleanCityRivalIWentToBillsText",
    ]
    if any(label not in blocks_by_label for label in required_labels):
        return []

    clean = "\n".join(strip_comment(line) for line in script_content.splitlines())
    required_snippets = [
        "CheckEvent EVENT_BEAT_CERULEAN_RIVAL",
        "ld hl, CeruleanCityCoords2",
        "ld de, CeruleanCityMovement1",
        "ld a, OPP_RIVAL1",
        "SetEvent EVENT_BEAT_CERULEAN_RIVAL",
        "ld de, CeruleanCityMovement3",
        "ld de, CeruleanCityMovement4",
        "predef HideObject",
    ]
    if any(snippet not in clean for snippet in required_snippets):
        return []

    coords = parse_coord_array(script_content, "CeruleanCityCoords2")
    if not coords:
        return []

    text_labels = extract_map_text_labels(map_name)
    local_ref_map = local_text_ref_map(blocks_by_label["CeruleanCityRivalText"]["raw"])
    pre_battle_lines = lines_for_script_text_ref(".PreBattleText", blocks_by_label, text_labels, local_ref_map)
    defeated_lines = lines_for_script_text_ref("CeruleanCityRivalDefeatedText", blocks_by_label, text_labels, local_ref_map)
    bill_lines = lines_for_script_text_ref("CeruleanCityRivalIWentToBillsText", blocks_by_label, text_labels, local_ref_map)
    if not pre_battle_lines or not defeated_lines or not bill_lines:
        return []

    source = source_metadata(
        map_name,
        "cerulean_city_rival_v1",
        script_path,
        text_path,
        [
            "sourceBlock=CeruleanCityDefaultScript",
            "Generated from the Cerulean bridge rival coordinate trigger, battle branch, and cleanup scripts.",
            "The source left/right bridge cleanup branches are retained in movementVariants; the representative action uses the first trigger coordinate's branch.",
        ],
    )
    source["coveredLabels"] = [
        "CeruleanCityClearScripts",
        "CeruleanCityDefaultScript",
        "CeruleanCityFaceRivalScript",
        "CeruleanCityRivalBattleScript",
        "CeruleanCityRivalCleanupScript",
        "CeruleanCityRivalDefeatedScript",
        "CeruleanCityRivalText",
    ]

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "CeruleanCityRivalEncounter",
            "trigger": {
                "type": "coord",
                "label": "CeruleanCityCoords2",
                "sourceLabel": "CeruleanCityDefaultScript",
                "coordinates": coords,
            },
            "conditions": {"requiresEventAbsent": "EVENT_BEAT_CERULEAN_RIVAL"},
            "actions": [
                {"type": "lockInput"},
                {"type": "showActor", "actor": "RIVAL", "x": 20, "y": 4},
                {"type": "move", "actor": "RIVAL", "movements": ["DOWN", "DOWN", "DOWN"]},
                {"type": "facePlayer", "actor": "RIVAL", "direction": "DOWN"},
                {"type": "dialogue", "speaker": "RIVAL", "lines": pre_battle_lines},
                {
                    "type": "startTrainerBattle",
                    "trainerClass": "RIVAL1",
                    "trainerName": "RIVAL",
                    "winFlag": "EVENT_BEAT_CERULEAN_RIVAL",
                    "partyByFlag": {
                        "EVENT_PLAYER_CHOSE_SQUIRTLE": 8,
                        "EVENT_PLAYER_CHOSE_BULBASAUR": 9,
                        "EVENT_PLAYER_CHOSE_CHARMANDER": 7,
                    },
                    "postWinActions": [
                        {"type": "dialogue", "speaker": "RIVAL", "lines": defeated_lines + bill_lines},
                        {
                            "type": "move",
                            "actor": "RIVAL",
                            "movements": ["RIGHT", "DOWN", "DOWN", "DOWN", "DOWN", "DOWN", "DOWN"],
                            "movementVariants": [
                                {
                                    "when": {"playerX": 20},
                                    "movements": ["RIGHT", "DOWN", "DOWN", "DOWN", "DOWN", "DOWN", "DOWN"],
                                },
                                {
                                    "when": {"playerXNot": 20},
                                    "movements": ["LEFT", "DOWN", "DOWN", "DOWN", "DOWN", "DOWN", "DOWN"],
                                },
                            ],
                        },
                        {"type": "hideObject", "textConstant": "TEXT_CERULEANCITY_RIVAL"},
                        {"type": "setEvent", "event": "EVENT_CERULEAN_RIVAL_LEFT"},
                    ],
                },
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        }
    ]


def route22_rival_candidates():
    map_name = "Route22"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    required_labels = [
        "Route22DefaultScript",
        "Route22MoveRivalRightScript",
        "Route22Rival1StartBattleScript",
        "Route22Rival1AfterBattleScript",
        "Route22Rival1ExitScript",
        "Route22Rival1Text",
        "Route22Rival2StartBattleScript",
        "Route22Rival2AfterBattleScript",
        "Route22Rival2ExitScript",
        "Route22Rival2Text",
    ]
    if any(label not in blocks_by_label for label in required_labels):
        return []

    clean = "\n".join(strip_comment(line) for line in script_content.splitlines())
    required_snippets = [
        "CheckEvent EVENT_ROUTE22_RIVAL_WANTS_BATTLE",
        "CheckEvent EVENT_1ST_ROUTE22_RIVAL_BATTLE",
        "CheckEventReuseA EVENT_2ND_ROUTE22_RIVAL_BATTLE",
        "SetEvent EVENT_BEAT_ROUTE22_RIVAL_1ST_BATTLE",
        "SetEvent EVENT_BEAT_ROUTE22_RIVAL_2ND_BATTLE",
        "ResetEvents EVENT_1ST_ROUTE22_RIVAL_BATTLE, EVENT_ROUTE22_RIVAL_WANTS_BATTLE",
        "ResetEvents EVENT_2ND_ROUTE22_RIVAL_BATTLE, EVENT_ROUTE22_RIVAL_WANTS_BATTLE",
    ]
    if any(snippet not in clean for snippet in required_snippets):
        return []

    coords = parse_coord_array(script_content, ".Route22RivalBattleCoords")
    if not coords:
        return []

    text_labels = extract_map_text_labels(map_name)
    source = source_metadata(
        map_name,
        "route22_rival_v1",
        script_path,
        text_path,
        [
            "sourceBlock=Route22DefaultScript",
            "Generated from the Route 22 rival coordinate trigger, starter-specific battle tables, and exit cleanup scripts.",
            "The source has upper/lower trigger-index exit movement variants; this neutral candidate uses the current downstream movement path until branch-on-trigger-index cutscenes are modeled.",
        ],
    )
    source["coveredLabels"] = [
        "Route22DefaultScript",
        "Route22MoveRivalRightScript",
        "Route22Rival1AfterBattleScript",
        "Route22Rival1ExitScript",
        "Route22Rival1StartBattleScript",
        "Route22Rival1Text",
        "Route22Rival2AfterBattleScript",
        "Route22Rival2ExitScript",
        "Route22Rival2StartBattleScript",
        "Route22Rival2Text",
    ]

    specs = [
        {
            "scriptLabel": "Route22Rival1Encounter",
            "actor": "RIVAL",
            "textConstant": "TEXT_ROUTE22_RIVAL1",
            "trainerClass": "RIVAL1",
            "requiresEvent": "EVENT_1ST_ROUTE22_RIVAL_BATTLE",
            "winEvent": "EVENT_BEAT_ROUTE22_RIVAL_1ST_BATTLE",
            "beforeText": "Route22RivalBeforeBattleText1",
            "defeatedText": "Route22Rival1DefeatedText",
            "afterText": "Route22RivalAfterBattleText1",
            "partyByFlag": {
                "EVENT_PLAYER_CHOSE_SQUIRTLE": 5,
                "EVENT_PLAYER_CHOSE_BULBASAUR": 6,
                "EVENT_PLAYER_CHOSE_CHARMANDER": 4,
            },
            "postWinMovement": ["RIGHT", "RIGHT", "DOWN", "DOWN", "DOWN", "DOWN", "DOWN"],
        },
        {
            "scriptLabel": "Route22Rival2Encounter",
            "actor": "RIVAL2",
            "textConstant": "TEXT_ROUTE22_RIVAL2",
            "trainerClass": "RIVAL2",
            "requiresEvent": "EVENT_2ND_ROUTE22_RIVAL_BATTLE",
            "winEvent": "EVENT_BEAT_ROUTE22_RIVAL_2ND_BATTLE",
            "beforeText": "Route22RivalBeforeBattleText2",
            "defeatedText": "Route22Rival2DefeatedText",
            "afterText": "Route22RivalAfterBattleText2",
            "partyByFlag": {
                "EVENT_PLAYER_CHOSE_SQUIRTLE": 11,
                "EVENT_PLAYER_CHOSE_BULBASAUR": 12,
                "EVENT_PLAYER_CHOSE_CHARMANDER": 10,
            },
            "postWinMovement": ["LEFT", "LEFT", "LEFT"],
        },
    ]

    candidates = []
    for spec in specs:
        local_ref_map = {}
        before_lines = lines_for_script_text_ref(spec["beforeText"], blocks_by_label, text_labels, local_ref_map)
        defeated_lines = lines_for_script_text_ref(spec["defeatedText"], blocks_by_label, text_labels, local_ref_map)
        after_lines = lines_for_script_text_ref(spec["afterText"], blocks_by_label, text_labels, local_ref_map)
        if not before_lines or not defeated_lines or not after_lines:
            return []
        candidates.append(
            {
                "version": 1,
                "kind": "scriptEventCandidate",
                "mapName": map_name,
                "scriptLabel": spec["scriptLabel"],
                "trigger": {
                    "type": "coord",
                    "label": "Route22RivalBattleCoords",
                    "sourceLabel": "Route22DefaultScript",
                    "coordinates": coords,
                },
                "conditions": {
                    "requiresEvents": [spec["requiresEvent"], "EVENT_ROUTE22_RIVAL_WANTS_BATTLE"],
                    "requiresEventAbsent": spec["winEvent"],
                },
                "actions": [
                    {"type": "lockInput"},
                    {"type": "showActor", "actor": spec["actor"], "x": 29, "y": 5},
                    {"type": "move", "actor": spec["actor"], "movements": ["LEFT", "LEFT", "LEFT", "LEFT"]},
                    {"type": "facePlayer", "actor": spec["actor"], "direction": "LEFT"},
                    {"type": "dialogue", "speaker": "RIVAL", "lines": before_lines},
                    {
                        "type": "startTrainerBattle",
                        "trainerClass": spec["trainerClass"],
                        "trainerName": "RIVAL",
                        "winFlag": spec["winEvent"],
                        "partyByFlag": spec["partyByFlag"],
                        "postWinActions": [
                            {"type": "dialogue", "speaker": "RIVAL", "lines": defeated_lines + after_lines},
                            {"type": "move", "actor": spec["actor"], "movements": spec["postWinMovement"]},
                            {"type": "hideObject", "textConstant": spec["textConstant"]},
                            {"type": "resetEvent", "event": spec["requiresEvent"]},
                            {"type": "resetEvent", "event": "EVENT_ROUTE22_RIVAL_WANTS_BATTLE"},
                        ],
                    },
                    {"type": "unlockInput"},
                ],
                "source": source,
                "confidence": "adapter",
            }
        )
    return candidates


def silph_co_7f_rival_candidates():
    map_name = "SilphCo7F"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    required_labels = [
        "SilphCo7FDefaultScript",
        "SilphCo7FRivalStartBattleScript",
        "SilphCo7FRivalAfterBattleScript",
        "SilphCo7FRivalExitScript",
        "SilphCo7FRivalText",
        "SilphCo7FRivalWaitedHereText",
        "SilphCo7FRivalGoodLuckToYouText",
    ]
    if any(label not in blocks_by_label for label in required_labels):
        return []

    clean = "\n".join(strip_comment(line) for line in script_content.splitlines())
    required_snippets = [
        "CheckEvent EVENT_BEAT_SILPH_CO_RIVAL",
        "ld hl, .RivalEncounterCoordinates",
        "ld de, .RivalMovementUp",
        "ld a, OPP_RIVAL2",
        "SetEvent EVENT_BEAT_SILPH_CO_RIVAL",
        "ld de, .RivalWalkAroundPlayerMovement",
        "ld de, .RivalExitRightMovement",
        "predef HideObject",
    ]
    if any(snippet not in clean for snippet in required_snippets):
        return []

    coords = parse_coord_array(script_content, ".RivalEncounterCoordinates")
    if coords != [{"x": 3, "y": 2}, {"x": 3, "y": 3}]:
        return []

    text_labels = extract_map_text_labels(map_name)
    local_ref_map = local_text_ref_map(blocks_by_label["SilphCo7FRivalText"]["raw"])
    intro_lines = lines_for_script_text_ref(".Text", blocks_by_label, text_labels, local_ref_map)
    waited_lines = lines_for_script_text_ref("SilphCo7FRivalWaitedHereText", blocks_by_label, text_labels, local_ref_map)
    good_luck_lines = lines_for_script_text_ref("SilphCo7FRivalGoodLuckToYouText", blocks_by_label, text_labels, local_ref_map)
    if not intro_lines or not waited_lines or not good_luck_lines:
        return []

    source = source_metadata(
        map_name,
        "silph_co_7f_rival_v1",
        script_path,
        text_path,
        [
            "sourceBlock=SilphCo7FDefaultScript",
            "Generated from the Silph Co. 7F rival coordinate trigger, starter-specific Rival2 battle branch, and exit cleanup scripts.",
            "The source has upper/lower trigger-index movement variants; downstream runtimes may preserve them as separate candidates.",
        ],
    )
    source["coveredLabels"] = [
        "SilphCo7FDefaultScript",
        "SilphCo7FRivalAfterBattleScript",
        "SilphCo7FRivalExitScript",
        "SilphCo7FRivalStartBattleScript",
        "SilphCo7FRivalText",
    ]

    specs = [
        {
            "scriptLabel": "SilphCo7FRivalEncounter",
            "label": "SilphCo7FRivalUpperCoords",
            "coordinates": [coords[0]],
            "approachMovement": ["UP", "UP", "UP"],
            "postWinMovement": ["LEFT", "UP", "UP", "RIGHT", "RIGHT", "RIGHT", "DOWN"],
        },
        {
            "scriptLabel": "SilphCo7FRivalEncounterLower",
            "label": "SilphCo7FRivalLowerCoords",
            "coordinates": [coords[1]],
            "approachMovement": ["UP", "UP", "UP", "UP"],
            "postWinMovement": ["RIGHT", "RIGHT"],
        },
    ]

    candidates = []
    for spec in specs:
        candidates.append(
            {
                "version": 1,
                "kind": "scriptEventCandidate",
                "mapName": map_name,
                "scriptLabel": spec["scriptLabel"],
                "trigger": {
                    "type": "coord",
                    "label": spec["label"],
                    "sourceLabel": "SilphCo7FDefaultScript",
                    "coordinates": spec["coordinates"],
                },
                "conditions": {"requiresEventAbsent": "EVENT_BEAT_SILPH_CO_RIVAL"},
                "actions": [
                    {"type": "lockInput"},
                    {"type": "dialogue", "speaker": "RIVAL", "lines": intro_lines},
                    {"type": "move", "actor": "RIVAL", "movements": spec["approachMovement"]},
                    {"type": "dialogue", "speaker": "RIVAL", "lines": waited_lines},
                    {
                        "type": "startTrainerBattle",
                        "trainerClass": "RIVAL2",
                        "trainerName": "RIVAL",
                        "winFlag": "EVENT_BEAT_SILPH_CO_RIVAL",
                        "partyByFlag": {
                            "EVENT_PLAYER_CHOSE_SQUIRTLE": 8,
                            "EVENT_PLAYER_CHOSE_BULBASAUR": 9,
                            "EVENT_PLAYER_CHOSE_CHARMANDER": 7,
                        },
                        "postWinActions": [
                            {"type": "dialogue", "speaker": "RIVAL", "lines": good_luck_lines},
                            {"type": "move", "actor": "RIVAL", "movements": spec["postWinMovement"]},
                            {"type": "hideObject", "textConstant": "TEXT_SILPHCO7F_RIVAL"},
                            {"type": "setEvent", "event": "EVENT_SILPH_CO_RIVAL_LEFT"},
                        ],
                    },
                    {"type": "unlockInput"},
                ],
                "source": source,
                "confidence": "adapter",
            }
        )
    return candidates


def pokemon_tower_2f_rival_candidates():
    map_name = "PokemonTower2F"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    required_labels = [
        "PokemonTower2FDefaultScript",
        "PokemonTower2FDefeatedRivalScript",
        "PokemonTower2FRivalExitsScript",
        "PokemonTower2FRivalText",
    ]
    if any(label not in blocks_by_label for label in required_labels):
        return []

    clean = "\n".join(strip_comment(line) for line in script_content.splitlines())
    required_snippets = [
        "CheckEvent EVENT_BEAT_POKEMON_TOWER_RIVAL",
        "ld hl, PokemonTower2FRivalEncounterEventCoords",
        "ResetEvent EVENT_POKEMON_TOWER_RIVAL_ON_LEFT",
        "SetEvent EVENT_POKEMON_TOWER_RIVAL_ON_LEFT",
        "ld a, OPP_RIVAL2",
        "ld a, SCRIPT_POKEMONTOWER2F_DEFEATED_RIVAL",
        "SetEvent EVENT_BEAT_POKEMON_TOWER_RIVAL",
        "CheckEvent EVENT_POKEMON_TOWER_RIVAL_ON_LEFT",
        "ld de, PokemonTower2FRivalDownThenRightMovement",
        "ld de, PokemonTower2FRivalRightThenDownMovement",
        "ld a, HS_POKEMON_TOWER_2F_RIVAL",
        "predef HideObject",
    ]
    if any(snippet not in clean for snippet in required_snippets):
        return []

    coords = parse_coord_array(script_content, "PokemonTower2FRivalEncounterEventCoords")
    if coords != [{"x": 15, "y": 5}, {"x": 14, "y": 6}]:
        return []
    map_id = source_map_id(map_name)
    if not map_id:
        return []

    right_then_down = re.findall(
        r"\bNPC_MOVEMENT_(UP|DOWN|LEFT|RIGHT)\b",
        blocks_by_label["PokemonTower2FRivalRightThenDownMovement"]["raw"],
    )
    down_then_right = re.findall(
        r"\bNPC_MOVEMENT_(UP|DOWN|LEFT|RIGHT)\b",
        blocks_by_label["PokemonTower2FRivalDownThenRightMovement"]["raw"],
    )
    if right_then_down != ["RIGHT", "DOWN", "DOWN", "RIGHT", "DOWN", "DOWN", "RIGHT", "RIGHT"]:
        return []
    if down_then_right != ["DOWN", "DOWN", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "DOWN", "DOWN"]:
        return []

    text_labels = extract_map_text_labels(map_name)
    local_refs = local_text_ref_map(blocks_by_label["PokemonTower2FRivalText"]["raw"])
    intro_lines = lines_for_script_text_ref(".WhatBringsYouHereText", blocks_by_label, text_labels, local_refs)
    defeated_lines = lines_for_script_text_ref(".DefeatedText", blocks_by_label, text_labels, local_refs)
    after_lines = lines_for_script_text_ref(".HowsYourDexText", blocks_by_label, text_labels, local_refs)
    if not intro_lines or not defeated_lines or not after_lines:
        return []

    source = source_metadata(
        map_name,
        "pokemon_tower_2f_rival_v1",
        script_path,
        text_path,
        [
            "sourceBlock=PokemonTower2FDefaultScript",
            "sourceBlock=PokemonTower2FRivalText",
            "sourceBlock=PokemonTower2FDefeatedRivalScript",
            "sourceBlock=PokemonTower2FRivalExitsScript",
            "Generated from the Pokemon Tower 2F rival coordinate trigger, starter-specific Rival2 battle branch, and exit cleanup scripts.",
            "The source stores EVENT_POKEMON_TOWER_RIVAL_ON_LEFT for the right-side coordinate; downstream candidates split that into explicit coordinate branches.",
        ],
    )
    source["coveredLabels"] = [
        "PokemonTower2FDefaultScript",
        "PokemonTower2FDefeatedRivalScript",
        "PokemonTower2FRivalExitsScript",
        "PokemonTower2FRivalText",
        "PokemonTower2FRivalEncounterEventCoords",
        "PokemonTower2FRivalRightThenDownMovement",
        "PokemonTower2FRivalDownThenRightMovement",
    ]
    source["movementVariants"] = {
        "rightSideCoord": down_then_right,
        "belowCoord": right_then_down,
    }

    specs = [
        {
            "scriptLabel": "PokemonTower2FRivalEncounter",
            "label": "PokemonTower2FRivalRightSideCoords",
            "coordinates": [coords[0]],
            "faceDirection": "RIGHT",
            "postWinMovement": down_then_right,
        },
        {
            "scriptLabel": "PokemonTower2FRivalEncounterBelow",
            "label": "PokemonTower2FRivalBelowCoords",
            "coordinates": [coords[1]],
            "faceDirection": "DOWN",
            "postWinMovement": right_then_down,
        },
    ]
    candidates = []
    for spec in specs:
        candidates.append(
            {
                "version": 1,
                "kind": "scriptEventCandidate",
                "mapName": map_name,
                "scriptLabel": spec["scriptLabel"],
                "trigger": {
                    "type": "coord",
                    "label": spec["label"],
                    "sourceLabel": "PokemonTower2FDefaultScript",
                    "coordinates": [{"mapName": map_name, "mapId": map_id, **coord} for coord in spec["coordinates"]],
                },
                "conditions": {"requiresEventAbsent": "EVENT_BEAT_POKEMON_TOWER_RIVAL"},
                "actions": [
                    {"type": "lockInput"},
                    {"type": "facePlayer", "actor": "RIVAL", "direction": spec["faceDirection"]},
                    {"type": "dialogue", "speaker": "RIVAL", "lines": intro_lines},
                    {
                        "type": "startTrainerBattle",
                        "trainerClass": "RIVAL2",
                        "trainerName": "RIVAL",
                        "winFlag": "EVENT_BEAT_POKEMON_TOWER_RIVAL",
                        "partyByFlag": {
                            "EVENT_PLAYER_CHOSE_SQUIRTLE": 5,
                            "EVENT_PLAYER_CHOSE_BULBASAUR": 6,
                            "EVENT_PLAYER_CHOSE_CHARMANDER": 4,
                        },
                        "postWinActions": [
                            {"type": "dialogue", "speaker": "RIVAL", "lines": defeated_lines + after_lines},
                            {"type": "move", "actor": "RIVAL", "movements": spec["postWinMovement"]},
                            {"type": "hideObject", "objectKey": "HS_POKEMON_TOWER_2F_RIVAL"},
                            {"type": "setEvent", "event": "EVENT_POKEMON_TOWER_RIVAL_LEFT"},
                        ],
                    },
                    {"type": "unlockInput"},
                ],
                "source": source,
                "confidence": "adapter",
            }
        )
    return candidates


def pokemon_tower_5f_purified_zone_candidate():
    map_name = "PokemonTower5F"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    default_block = blocks_by_label.get("PokemonTower5FDefaultScript")
    if not default_block:
        return []

    clean = "\n".join(strip_comment(line) for line in default_block["raw"].splitlines())
    required_snippets = [
        "ld hl, PokemonTower5FPurifiedZoneCoords",
        "call ArePlayerCoordsInArray",
        "res BIT_NO_BATTLES",
        "ResetEvent EVENT_IN_PURIFIED_ZONE",
        "CheckAndSetEvent EVENT_IN_PURIFIED_ZONE",
        "set BIT_NO_BATTLES",
        "predef HealParty",
        "ld a, TEXT_POKEMONTOWER5F_PURIFIEDZONE",
    ]
    if any(snippet not in clean for snippet in required_snippets):
        return []

    coords = parse_coord_array(script_content, "PokemonTower5FPurifiedZoneCoords")
    if coords != [{"x": 10, "y": 8}, {"x": 11, "y": 8}, {"x": 10, "y": 9}, {"x": 11, "y": 9}]:
        return []

    map_id = source_map_id(map_name)
    if not map_id:
        return []

    text_pointers = parse_text_pointer_map(script_content)
    text_labels = extract_map_text_labels(map_name)
    purified_lines = lines_for_text_constant(
        "TEXT_POKEMONTOWER5F_PURIFIEDZONE",
        text_pointers,
        blocks_by_label,
        text_labels,
    )
    if not purified_lines:
        return []

    source = source_metadata(
        map_name,
        "pokemon_tower_5f_purified_zone_v1",
        script_path,
        text_path,
        [
            "sourceBlock=PokemonTower5FDefaultScript",
            "sourceBlock=PokemonTower5FPurifiedZoneText",
            "Generated from the Pokemon Tower 5F purified-zone coordinate check and HealParty branch.",
            "The source toggles BIT_NO_BATTLES while inside/outside the protected zone; downstream runtimes should suppress wild encounters on the same coordinates and reset EVENT_IN_PURIFIED_ZONE after leaving them.",
        ],
    )
    source["coveredLabels"] = [
        "PokemonTower5FDefaultScript",
        "PokemonTower5FPurifiedZoneCoords",
        "PokemonTower5FPurifiedZoneText",
    ]

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "PokemonTower5FPurifiedZone",
            "trigger": {
                "type": "coord",
                "label": "PokemonTower5FPurifiedZoneCoords",
                "sourceLabel": "PokemonTower5FDefaultScript",
                "coordinates": [{"mapName": map_name, "mapId": map_id, **coord} for coord in coords],
            },
            "conditions": {"requiresEventAbsent": "EVENT_IN_PURIFIED_ZONE"},
            "actions": [
                {"type": "lockInput"},
                {"type": "setEvent", "event": "EVENT_IN_PURIFIED_ZONE"},
                {"type": "healParty"},
                {"type": "dialogue", "lines": purified_lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        }
    ]


def ss_anne_2f_rival_candidate():
    map_name = "SSAnne2F"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    required_labels = [
        "SSAnne2FDefaultScript",
        "SSAnne2FRivalStartBattleScript",
        "SSAnne2FRivalAfterBattleScript",
        "SSAnne2FRivalExitScript",
        "SSAnne2FRivalText",
        "SSAnne2FRivalDefeatedText",
        "SSAnne2FRivalCutMasterText",
    ]
    if any(label not in blocks_by_label for label in required_labels):
        return []

    default_block = blocks_by_label["SSAnne2FDefaultScript"]
    after_block = blocks_by_label["SSAnne2FRivalAfterBattleScript"]
    exit_block = blocks_by_label["SSAnne2FRivalExitScript"]
    clean = "\n".join(strip_comment(line) for line in script_content.splitlines())
    required_snippets = [
        "ld a, HS_SS_ANNE_2F_RIVAL",
        "predef ShowObject",
        "ld a, OPP_RIVAL2",
        "ld a, SCRIPT_SSANNE2F_RIVAL_AFTER_BATTLE",
        "ld a, TEXT_SSANNE2F_RIVAL_CUT_MASTER",
        "ld a, SCRIPT_SSANNE2F_RIVAL_EXIT",
        "predef HideObject",
    ]
    if any(snippet not in clean for snippet in required_snippets):
        return []

    coords = parse_local_dbmapcoords(default_block["raw"], ".PlayerCoordinatesArray")
    if coords != [{"x": 36, "y": 8}, {"x": 37, "y": 8}]:
        return []
    approach_down_four = re.findall(r"\bNPC_MOVEMENT_(UP|DOWN|LEFT|RIGHT)\b", default_block["raw"])
    post_win_movements = re.findall(r"\bNPC_MOVEMENT_(UP|DOWN|LEFT|RIGHT)\b", after_block["raw"])
    if approach_down_four != ["DOWN", "DOWN", "DOWN", "DOWN"]:
        return []
    if post_win_movements != ["RIGHT", "DOWN", "DOWN", "DOWN", "DOWN", "DOWN"]:
        return []
    if "HS_SS_ANNE_2F_RIVAL" not in exit_block["raw"]:
        return []

    text_labels = extract_map_text_labels(map_name)
    local_ref_map = local_text_ref_map(blocks_by_label["SSAnne2FRivalText"]["raw"])
    intro_lines = lines_for_script_text_ref(".Text", blocks_by_label, text_labels, local_ref_map)
    defeated_lines = lines_for_script_text_ref("SSAnne2FRivalDefeatedText", blocks_by_label, text_labels, local_ref_map)
    cut_master_lines = lines_for_script_text_ref("SSAnne2FRivalCutMasterText", blocks_by_label, text_labels, local_ref_map)
    if not intro_lines or not defeated_lines or not cut_master_lines:
        return []

    source = source_metadata(
        map_name,
        "ss_anne_2f_rival_v1",
        script_path,
        text_path,
        [
            "sourceBlock=SSAnne2FDefaultScript",
            "Generated from the S.S. Anne 2F rival coordinate trigger, Rival2 battle branch, and exit cleanup scripts.",
            "The source coordinate-dependent approach and exit movement branches are retained in movementVariants.",
        ],
    )
    source["coveredLabels"] = [
        "SSAnne2FDefaultScript",
        "SSAnne2FRivalAfterBattleScript",
        "SSAnne2FRivalDefeatedText",
        "SSAnne2FRivalExitScript",
        "SSAnne2FRivalStartBattleScript",
        "SSAnne2FRivalText",
        "SSAnne2FRivalVictoryText",
        "SSAnne2FRivalCutMasterText",
    ]
    source["movementVariants"] = {
        "approachFromLeftCoord": ["DOWN", "DOWN", "DOWN", "DOWN"],
        "approachFromRightCoord": ["DOWN", "DOWN", "DOWN"],
        "exitFromLeftCoord": ["RIGHT", "DOWN", "DOWN", "DOWN", "DOWN", "DOWN"],
        "exitFromRightCoord": ["DOWN", "DOWN", "DOWN", "DOWN"],
    }

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "SSAnne2FRivalEncounter",
            "trigger": {
                "type": "coord",
                "label": "SSAnne2FRivalCoords",
                "sourceLabel": "SSAnne2FDefaultScript",
                "coordinates": coords,
            },
            "conditions": {"requiresEventAbsent": "EVENT_BEAT_SS_ANNE_RIVAL"},
            "actions": [
                {"type": "lockInput"},
                {"type": "showObject", "objectKey": "HS_SS_ANNE_2F_RIVAL"},
                {"type": "move", "actor": "RIVAL", "movements": ["DOWN", "DOWN", "DOWN", "DOWN"]},
                {"type": "dialogue", "speaker": "RIVAL", "lines": intro_lines},
                {
                    "type": "startTrainerBattle",
                    "trainerClass": "RIVAL2",
                    "trainerName": "RIVAL",
                    "winFlag": "EVENT_BEAT_SS_ANNE_RIVAL",
                    "partyByFlag": {
                        "EVENT_PLAYER_CHOSE_SQUIRTLE": 1,
                        "EVENT_PLAYER_CHOSE_BULBASAUR": 2,
                        "EVENT_PLAYER_CHOSE_CHARMANDER": 3,
                    },
                    "postWinActions": [
                        {"type": "dialogue", "speaker": "RIVAL", "lines": defeated_lines + cut_master_lines},
                        {
                            "type": "move",
                            "actor": "RIVAL",
                            "movements": ["RIGHT", "DOWN", "DOWN", "DOWN", "DOWN", "DOWN"],
                        },
                        {"type": "hideObject", "objectKey": "HS_SS_ANNE_2F_RIVAL"},
                        {"type": "setEvent", "event": "EVENT_SS_ANNE_RIVAL_LEFT"},
                    ],
                },
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        }
    ]


def silph_co_11f_giovanni_candidate():
    map_name = "SilphCo11F"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    required_labels = [
        "SilphCo11FDefaultScript",
        "SilphCo11FGiovanniStartBattleScript",
        "SilphCo11FGiovanniAfterBattleScript",
        "SilphCo11FTeamRocketLeavesScript",
        "SilphCo11FGiovanniText",
        "SilphCo11FGiovanniYouRuinedOurPlansText",
    ]
    if any(label not in blocks_by_label for label in required_labels):
        return []

    clean = "\n".join(strip_comment(line) for line in script_content.splitlines())
    required_snippets = [
        "CheckEvent EVENT_BEAT_SILPH_CO_GIOVANNI",
        "ld hl, .PlayerCoordsArray",
        "ld a, TEXT_SILPHCO11F_GIOVANNI",
        "ld de, .GiovanniMovement",
        "call MoveSprite",
        "call EngageMapTrainer",
        "SetEvent EVENT_BEAT_SILPH_CO_GIOVANNI",
        "call SilphCo11FTeamRocketLeavesScript",
    ]
    if any(snippet not in clean for snippet in required_snippets):
        return []

    default_block = blocks_by_label["SilphCo11FDefaultScript"]
    coords = parse_local_dbmapcoords(default_block["raw"], ".PlayerCoordsArray")
    if coords != [{"x": 6, "y": 13}, {"x": 7, "y": 12}]:
        return []
    map_id = source_map_id(map_name)
    if not map_id:
        return []

    approach_movements = re.findall(r"\bNPC_MOVEMENT_(UP|DOWN|LEFT|RIGHT)\b", default_block["raw"])
    if approach_movements != ["DOWN", "DOWN", "DOWN"]:
        return []

    text_labels = extract_map_text_labels(map_name)
    intro_lines = lines_for_script_text_ref("SilphCo11FGiovanniText", blocks_by_label, text_labels, {})
    defeated_lines = lines_for_script_text_ref("SilphCo11FGiovanniYouRuinedOurPlansText", blocks_by_label, text_labels, {})
    if not intro_lines or not defeated_lines:
        return []

    leaves_block = blocks_by_label["SilphCo11FTeamRocketLeavesScript"]["raw"]
    hide_objects = []
    show_objects = []
    active = None
    for raw_line in leaves_block.splitlines():
        stripped = strip_comment(raw_line)
        if stripped == ".ShowMissableObjectIDs:":
            active = show_objects
            continue
        if stripped == ".HideMissableObjectIDs:":
            active = hide_objects
            continue
        if active is None:
            continue
        match = re.match(r"db\s+([A-Z0-9_]+)\b", stripped)
        if match:
            active.append(match.group(1))
            continue
        if re.match(r"db\s+-?1\b", stripped):
            active = None

    if "HS_SILPH_CO_11F_1" not in hide_objects or "HS_SAFFRON_CITY_8" not in show_objects:
        return []

    source = source_metadata(
        map_name,
        "silph_co_11f_giovanni_v1",
        script_path,
        text_path,
        [
            "sourceBlock=SilphCo11FDefaultScript",
            "Generated from the Silph Co. 11F Giovanni coordinate trigger, movement, battle branch, and Team Rocket departure cleanup.",
            "The source has coordinate-dependent facing after the approach; this neutral candidate keeps the encounter behavior and battle cleanup while leaving exact facing as presentation metadata.",
        ],
    )
    source["coveredLabels"] = [
        "SilphCo11FDefaultScript",
        "SilphCo11FGiovanniAfterBattleScript",
        "SilphCo11FGiovanniBattleFacingScript",
        "SilphCo11FGiovanniStartBattleScript",
        "SilphCo11FGiovanniText",
        "SilphCo11FGiovanniYouRuinedOurPlansText",
        "SilphCo11FTeamRocketLeavesScript",
    ]
    source["movementVariants"] = {
        "approach": approach_movements,
        "postBattleFacingFromLowerLeftCoord": {"player": "LEFT", "giovanni": "RIGHT"},
        "postBattleFacingFromUpperRightCoord": {"player": "UP", "giovanni": "DOWN"},
    }

    post_win_actions = [
        {"type": "dialogue", "speaker": "GIOVANNI", "lines": defeated_lines},
        {"type": "hideActor", "actor": "GIOVANNI"},
        {"type": "setEvent", "event": "EVENT_SILPH_GIOVANNI_LEFT"},
    ]
    post_win_actions.extend({"type": "hideObject", "objectKey": object_key} for object_key in hide_objects)
    post_win_actions.extend({"type": "showObject", "objectKey": object_key} for object_key in show_objects)

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "SilphCo11FGiovanniEncounter",
            "trigger": {
                "type": "coord",
                "label": "SilphCo11FGiovanniCoords",
                "sourceLabel": "SilphCo11FDefaultScript",
                "coordinates": [{"mapName": map_name, "mapId": map_id, **coord} for coord in coords],
            },
            "conditions": {"requiresEventAbsent": "EVENT_BEAT_SILPH_CO_GIOVANNI"},
            "actions": [
                {"type": "lockInput"},
                {"type": "move", "actor": "GIOVANNI", "movements": approach_movements},
                {"type": "dialogue", "speaker": "GIOVANNI", "lines": intro_lines},
                {
                    "type": "startTrainerBattle",
                    "trainerClass": "GIOVANNI",
                    "trainerName": "GIOVANNI",
                    "partyIndex": 2,
                    "winFlag": "EVENT_BEAT_SILPH_CO_GIOVANNI",
                    "postWinActions": post_win_actions,
                },
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        }
    ]


def silph_co_6f_giovanni_dialogue_candidates():
    map_name = "SilphCo6F"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    helper = blocks_by_label.get("SilphCo6FBeatGiovanniPrintDEOrPrintHLScript")
    if not helper:
        return []
    helper_clean = "\n".join(strip_comment(line) for line in helper["raw"].splitlines())
    required_helper_lines = [
        "CheckEvent EVENT_BEAT_SILPH_CO_GIOVANNI",
        "jr nz, .beat_giovanni",
        "jr .print_text",
        ".beat_giovanni",
        "ld h, d",
        "ld l, e",
        ".print_text",
        "jp PrintText",
    ]
    if any(line not in helper_clean for line in required_helper_lines):
        return []

    text_pointers = parse_text_pointer_map(script_content)
    text_labels = extract_map_text_labels(map_name)
    specs = [
        "SilphCo6FSilphWorkerM1Text",
        "SilphCo6FSilphWorkerM2Text",
        "SilphCo6FSilphWorkerF1Text",
        "SilphCo6FSilphWorkerF2Text",
        "SilphCo6FSilphWorkerM3Text",
    ]
    candidates = []
    for label in specs:
        block = blocks_by_label.get(label)
        text_constant = text_pointers.get(label, "")
        if not block or not text_constant:
            return []
        clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
        match = re.search(
            r"\bld\s+hl,\s+(\.\w+)\s+"
            r"ld\s+de,\s+(\.\w+)\s+"
            r"call\s+SilphCo6FBeatGiovanniPrintDEOrPrintHLScript\s+"
            r"jp\s+TextScriptEnd\b",
            clean,
        )
        if not match:
            return []
        before_ref, after_ref = match.group(1), match.group(2)
        text_refs = local_text_ref_map(block["raw"])
        before_lines = script_label_lines(text_labels, text_refs, before_ref)
        after_lines = script_label_lines(text_labels, text_refs, after_ref)
        if not before_lines or not after_lines:
            return []

        source = source_metadata(
            map_name,
            "silph_co_6f_giovanni_dialogue_v1",
            script_path,
            text_path,
            [
                f"sourceBlock={label}",
                "sourceBlock=SilphCo6FBeatGiovanniPrintDEOrPrintHLScript",
                "Generated from Silph Co. 6F's shared hl/de text selector gated by EVENT_BEAT_SILPH_CO_GIOVANNI.",
            ],
        )
        source["coveredLabels"] = [label, "SilphCo6FBeatGiovanniPrintDEOrPrintHLScript"]
        candidates.extend(
            [
                {
                    "version": 1,
                    "kind": "scriptEventCandidate",
                    "mapName": map_name,
                    "scriptLabel": f"{label}EventBeatSilphCoGiovanniAbsent",
                    "trigger": {
                        "type": "npc_click",
                        "label": text_constant,
                        "sourceLabel": label,
                    },
                    "conditions": {"requiresEventAbsent": "EVENT_BEAT_SILPH_CO_GIOVANNI"},
                    "actions": [
                        {"type": "lockInput"},
                        {"type": "dialogue", "lines": before_lines},
                        {"type": "unlockInput"},
                    ],
                    "source": source,
                    "confidence": "adapter",
                },
                {
                    "version": 1,
                    "kind": "scriptEventCandidate",
                    "mapName": map_name,
                    "scriptLabel": f"{label}EventBeatSilphCoGiovanniSet",
                    "trigger": {
                        "type": "npc_click",
                        "label": text_constant,
                        "sourceLabel": label,
                    },
                    "conditions": {"requiresEvent": "EVENT_BEAT_SILPH_CO_GIOVANNI"},
                    "actions": [
                        {"type": "lockInput"},
                        {"type": "dialogue", "lines": after_lines},
                        {"type": "unlockInput"},
                    ],
                    "source": source,
                    "confidence": "adapter",
                },
            ]
        )

    return candidates


def pokemon_tower_7f_mr_fuji_rescue_candidate():
    map_name = "PokemonTower7F"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    required_labels = [
        "PokemonTower7FMrFujiText",
        "PokemonTower7FWarpToMrFujiHouseScript",
    ]
    if any(label not in blocks_by_label for label in required_labels):
        return []

    clean = "\n".join(strip_comment(line) for line in script_content.splitlines())
    required_snippets = [
        "SetEvent EVENT_RESCUED_MR_FUJI",
        "SetEvent EVENT_RESCUED_MR_FUJI_2",
        "ld a, HS_MR_FUJIS_HOUSE_MR_FUJI",
        "ld a, HS_SAFFRON_CITY_E",
        "ld a, HS_SAFFRON_CITY_F",
        "ld a, SCRIPT_POKEMONTOWER7F_WARP_TO_MR_FUJI_HOUSE",
        "ld a, HS_POKEMON_TOWER_7F_MR_FUJI",
        "ld a, MR_FUJIS_HOUSE",
        "ld a, $1",
        "set BIT_WARP_FROM_CUR_SCRIPT, [hl]",
    ]
    if any(snippet not in clean for snippet in required_snippets):
        return []

    text_labels = extract_map_text_labels(map_name)
    rescue_lines = lines_for_script_text_ref(".RescueText", blocks_by_label, text_labels, local_text_ref_map(blocks_by_label["PokemonTower7FMrFujiText"]["raw"]))
    if not rescue_lines:
        return []

    source = source_metadata(
        map_name,
        "pokemon_tower_7f_mr_fuji_rescue_v1",
        script_path,
        text_path,
        [
            "sourceBlock=PokemonTower7FMrFujiText",
            "sourceBlock=PokemonTower7FWarpToMrFujiHouseScript",
            "Generated from Mr. Fuji's rescue text script, source rescue flags, missable-object updates, and scripted warp to Mr. Fuji's House.",
            "The original map geometry/Rocket trainers gate reaching Mr. Fuji; this neutral candidate also requires the three source Rocket trainer win flags so downstream runtimes cannot trigger it early through ranged interaction.",
        ],
    )
    source["coveredLabels"] = [
        "PokemonTower7FMrFujiText",
        "PokemonTower7FWarpToMrFujiHouseScript",
    ]

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "PokemonTower7FMrFujiRescue",
            "trigger": {
                "type": "npc_click",
                "label": "TEXT_POKEMONTOWER7F_MR_FUJI",
                "sourceLabel": "PokemonTower7FMrFujiText",
            },
            "conditions": {
                "requiresEvents": [
                    "EVENT_BEAT_POKEMONTOWER_7_TRAINER_0",
                    "EVENT_BEAT_POKEMONTOWER_7_TRAINER_1",
                    "EVENT_BEAT_POKEMONTOWER_7_TRAINER_2",
                ],
                "requiresEventAbsent": "EVENT_RESCUED_MR_FUJI",
            },
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "speaker": "MR. FUJI", "lines": rescue_lines},
                {"type": "setEvent", "event": "EVENT_RESCUED_MR_FUJI"},
                {"type": "setEvent", "event": "EVENT_RESCUED_MR_FUJI_2"},
                {"type": "showObject", "objectKey": "HS_MR_FUJIS_HOUSE_MR_FUJI"},
                {"type": "hideObject", "objectKey": "HS_SAFFRON_CITY_E"},
                {"type": "showObject", "objectKey": "HS_SAFFRON_CITY_F"},
                {"type": "hideObject", "objectKey": "HS_POKEMON_TOWER_7F_MR_FUJI"},
                {"type": "warp", "mapId": 149, "x": 3, "y": 7, "direction": "UP"},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        }
    ]


def standard_trainer_text_source_for_header(text_pointers, blocks_by_label, header_label):
    for source_label, text_constant in text_pointers.items():
        block = blocks_by_label.get(source_label)
        if not block:
            continue
        if find_trainer_header_for_text_block(block["raw"]) == header_label:
            return source_label, text_constant
    return "", ""


def trainer_after_battle_object_drop_candidate_for_header(
    map_name,
    script_path,
    text_path,
    text_pointers,
    text_labels,
    blocks_by_label,
    header,
):
    after_block = blocks_by_label.get(header["afterBattleText"])
    if not after_block:
        return []

    clean = "\n".join(strip_comment(line) for line in after_block["raw"].splitlines())
    match = re.search(
        r"\bld\s+hl,\s+(\.?\w+)\s*\n"
        r"\s*call\s+PrintText\s*\n"
        r"\s*CheckAndSetEvent\s+(EVENT_\w+)\s*\n"
        r"\s*jr\s+nz,\s+(\.\w+)\s*\n"
        r"\s*ld\s+a,\s+(HS_[A-Z0-9_]+)\s*\n"
        r"\s*ld\s+\[wMissableObjectIndex\],\s+a\s*\n"
        r"\s*predef\s+ShowObject\s*\n"
        r"\s*\3:?\s*\n"
        r"\s*jp\s+TextScriptEnd\b",
        clean,
    )
    if not match:
        return []

    text_ref, drop_event, _, object_key = match.groups()
    source_label, text_constant = standard_trainer_text_source_for_header(
        text_pointers,
        blocks_by_label,
        header["label"],
    )
    if not source_label or not text_constant:
        return []

    local_ref_map = local_text_ref_map(after_block["raw"])
    lines = lines_for_script_text_ref(text_ref, blocks_by_label, text_labels, local_ref_map)
    if not lines:
        return []

    source = source_metadata(
        map_name,
        "trainer_after_battle_object_drop_v1",
        script_path,
        text_path,
        [
            f"sourceBlock={after_block['label']}",
            f"trainerHeader={header['label']}",
            f"dropEvent={drop_event}",
            f"shownObject={object_key}",
            "Generated from standard trainer after-battle text that reveals a hidden object exactly once.",
        ],
    )
    source["coveredLabels"] = unique_sorted([source_label, header["label"], after_block["label"]])

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{after_block['label']}ObjectDrop",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": source_label,
            },
            "conditions": {
                "requiresEvent": header["event"],
                "requiresEventAbsent": drop_event,
            },
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": lines},
                {"type": "setEvent", "event": drop_event},
                {"type": "showObject", "objectKey": object_key},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": f"{after_block['label']}AfterObjectDrop",
            "trigger": {
                "type": "npc_click",
                "label": text_constant,
                "sourceLabel": source_label,
            },
            "conditions": {"requiresEvent": drop_event},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "lines": lines},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        },
    ]


def trainer_after_battle_object_drop_candidates():
    candidates = []
    for script_path in sorted(SCRIPTS_DIR.glob("*.asm")):
        map_name = script_path.stem
        text_path = TEXT_DIR / f"{map_name}.asm"
        script_content = script_path.read_text()
        text_pointers = parse_text_pointer_map(script_content)
        if not text_pointers:
            continue

        blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
        text_labels = extract_map_text_labels(map_name)
        for header in parse_trainer_header_blocks(script_content).values():
            candidates.extend(
                trainer_after_battle_object_drop_candidate_for_header(
                    map_name,
                    script_path,
                    text_path,
                    text_pointers,
                    text_labels,
                    blocks_by_label,
                    header,
                )
            )
    return candidates


def simple_trainer_post_battle_flag_effect(block):
    ir = extract_features(block["label"], block["raw"])
    features = ir["features"]
    if features["hasChoice"] or features["hasGiveItem"] or features["hasGivePokemon"] or features["hasMoneyCheck"]:
        return ""
    if features["hasTrainerBattle"] or features["hasWildBattle"]:
        return ""
    if ir["movementRefs"] or ir["objectRefs"] or ir["warpRefs"]:
        return ""

    set_refs = [ref["flag"] for ref in ir["eventRefs"] if ref["op"] == "SetEvent"]
    if len(set_refs) != 1 or len(ir["eventRefs"]) != 1:
        return ""
    if len(ordered_text_refs(block["raw"])) != 1:
        return ""

    clean_lines = [strip_comment(line) for line in block["raw"].splitlines() if strip_comment(line)]
    allowed_patterns = [
        rf"{block['label']}:",
        r"text_far\s+_?\w+",
        r"text_asm",
        rf"SetEvent\s+{set_refs[0]}",
        r"jp\s+TextScriptEnd",
        r"ld\s+hl,\s+\.\w+",
        r"ret",
        r"\.\w+:",
        r"text_promptbutton",
        r"text_end",
    ]
    for line in clean_lines:
        if any(re.fullmatch(pattern, line) for pattern in allowed_patterns):
            continue
        return ""

    return set_refs[0]


def trainer_after_battle_flag_side_effect_candidate_for_header(
    map_name,
    script_path,
    text_path,
    text_pointers,
    text_labels,
    blocks_by_label,
    header,
):
    candidates = []
    source_label, _ = standard_trainer_text_source_for_header(text_pointers, blocks_by_label, header["label"])
    for field in ["endBattleText", "afterBattleText"]:
        block = blocks_by_label.get(header[field])
        if not block:
            continue
        side_effect_flag = simple_trainer_post_battle_flag_effect(block)
        if not side_effect_flag or side_effect_flag == header["event"]:
            continue

        lines = lines_for_labels(text_labels, ordered_text_refs(block["raw"]))
        if not lines:
            continue

        prelude_lines = []
        if field == "afterBattleText":
            end_block = blocks_by_label.get(header["endBattleText"])
            if end_block and end_block["label"] != block["label"]:
                prelude_lines = lines_for_labels(text_labels, ordered_text_refs(end_block["raw"]))

        covered_labels = unique_sorted([source_label, header["label"], block["label"]])
        if prelude_lines:
            covered_labels = unique_sorted([*covered_labels, header["endBattleText"]])
        source = source_metadata(
            map_name,
            "trainer_after_battle_flag_side_effect_v1",
            script_path,
            text_path,
            [
                f"sourceBlock={block['label']}",
                f"trainerHeader={header['label']}",
                f"sourceTrainerWinFlag={header['event']}",
                f"setsEvent={side_effect_flag}",
                "Generated from trainer end/after-battle text whose text_asm sets an additional progression flag.",
                "When the side effect lives in after-battle text, the trainer end-battle text is prepended because downstream post-battle hooks run after battle close.",
            ],
        )
        source["coveredLabels"] = covered_labels

        actions = [{"type": "lockInput"}]
        if prelude_lines:
            actions.append({"type": "dialogue", "lines": prelude_lines})
        actions.extend(
            [
                {"type": "dialogue", "lines": lines},
                {"type": "setEvent", "event": side_effect_flag},
                {"type": "unlockInput"},
            ]
        )

        candidates.append(
            {
                "version": 1,
                "kind": "scriptEventCandidate",
                "mapName": map_name,
                "scriptLabel": f"{block['label']}{pascal_from_constant(side_effect_flag)}Set",
                "trigger": {
                    "type": "map_script",
                    "label": block["label"],
                    "sourceLabel": block["label"],
                },
                "conditions": {
                    "requiresEvent": header["event"],
                    "requiresEventAbsent": side_effect_flag,
                },
                "actions": actions,
                "source": source,
                "confidence": "adapter",
            }
        )
    return candidates


def trainer_after_battle_flag_side_effect_candidates():
    candidates = []
    for script_path in sorted(SCRIPTS_DIR.glob("*.asm")):
        map_name = script_path.stem
        text_path = TEXT_DIR / f"{map_name}.asm"
        script_content = script_path.read_text()
        text_pointers = parse_text_pointer_map(script_content)
        if not text_pointers:
            continue

        blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
        text_labels = extract_map_text_labels(map_name)
        for header in parse_trainer_header_blocks(script_content).values():
            candidates.extend(
                trainer_after_battle_flag_side_effect_candidate_for_header(
                    map_name,
                    script_path,
                    text_path,
                    text_pointers,
                    text_labels,
                    blocks_by_label,
                    header,
                )
            )
    return candidates


def trainer_after_battle_flag_runtime_diagnostics():
    diagnostics = []
    for script_path in sorted(SCRIPTS_DIR.glob("*.asm")):
        map_name = script_path.stem
        script_content = script_path.read_text()
        blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
        for header in parse_trainer_header_blocks(script_content).values():
            for field in ["endBattleText", "afterBattleText"]:
                block = blocks_by_label.get(header[field])
                if not block:
                    continue
                side_effect_flag = simple_trainer_post_battle_flag_effect(block)
                if not side_effect_flag or side_effect_flag != header["event"]:
                    continue
                diagnostics.append(
                    {
                        "mapName": map_name,
                        "scriptLabel": block["label"],
                        "status": "covered",
                        "reason": "trainer_after_battle_flag_runtime_v1",
                        "details": {
                            "kind": "text",
                            "eventRefs": [{"op": "SetEvent", "flag": side_effect_flag}],
                            "source": {
                                "trainerHeader": header["label"],
                                "field": field,
                                "runtimeTables": [
                                    "trainer_headers",
                                    "trainer_parties",
                                    "trainer_party_pokemon",
                                ],
                                "notes": [
                                    "The text_asm sets the same flag as the trainer header win flag.",
                                    "Downstream runtimes already persist this flag when the trainer battle is won.",
                                ],
                            },
                        },
                    }
                )
    return diagnostics


def route24_rocket_reward_battle_candidate():
    map_name = "Route24"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    block = blocks_by_label.get("Route24CooltrainerM1Text")
    if not block:
        return []
    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    for snippet in [
        "lb bc, NUGGET, 1",
        "SetEvent EVENT_GOT_NUGGET",
        "call EngageMapTrainer",
        "call InitBattleEnemyParameters",
        "SCRIPT_ROUTE24_AFTER_ROCKET_BATTLE",
    ]:
        if snippet not in clean:
            return []

    trainer_obj = trainer_object_for_text(map_name, "TEXT_ROUTE24_COOLTRAINER_M1")
    if not trainer_obj:
        return []

    text_labels = extract_map_text_labels(map_name)
    local_ref_map = local_text_ref_map(block["raw"])
    prelude = dialogue_action_for_script_ref(".YouBeatOurContestText", blocks_by_label, text_labels, local_ref_map)
    received = dialogue_action_for_script_ref(".ReceivedNuggetText", blocks_by_label, text_labels, local_ref_map, "NUGGET")
    join = dialogue_action_for_script_ref(".JoinTeamRocketText", blocks_by_label, text_labels, local_ref_map)
    after = dialogue_action_for_script_ref(".YouCouldBecomeATopLeaderText", blocks_by_label, text_labels, local_ref_map)
    if not prelude or not received or not join or not after:
        return []

    coords = [
        {"mapName": map_name, "x": coord["x"], "y": coord["y"]}
        for coord in parse_coord_array(script_content, ".PlayerCoordsArray")
    ]
    if not coords:
        return []

    source = source_metadata(
        map_name,
        "rocket_reward_battle_v1",
        script_path,
        text_path,
        [
            "sourceBlock=Route24CooltrainerM1Text",
            "Generated from Nugget Bridge reward plus scripted Rocket battle state machine.",
            "Original one-step player movement after a full bag is not represented.",
        ],
    )
    source["coveredLabels"] = [
        "Route24DefaultScript",
        "Route24AfterRocketBattleScript",
        "Route24CooltrainerM1Text",
    ]

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "Route24NuggetBridgeRocketBattle",
            "trigger": {
                "type": "coord",
                "label": "Route24NuggetBridgeRocketCoords",
                "sourceLabel": "Route24CooltrainerM1Text",
                "coordinates": coords,
            },
            "conditions": {"requiresEventAbsent": "EVENT_GOT_NUGGET"},
            "actions": [
                {"type": "lockInput"},
                prelude,
                {"type": "giveItem", "itemConstant": "NUGGET", "quantity": 1},
                {"type": "setEvent", "event": "EVENT_GOT_NUGGET"},
                received,
                join,
                trainer_battle_action_from_object(
                    trainer_obj,
                    "EVENT_BEAT_ROUTE24_ROCKET",
                    [after],
                ),
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        }
    ]


def cerulean_rocket_reward_battle_candidate():
    map_name = "CeruleanCity"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    block = blocks_by_label.get("CeruleanCityRocketText")
    if not block:
        return []
    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    for snippet in [
        "CheckEvent EVENT_BEAT_CERULEAN_ROCKET_THIEF",
        "call EngageMapTrainer",
        "call InitBattleEnemyParameters",
        "lb bc, TM_DIG, 1",
        "farcall CeruleanHideRocket",
    ]:
        if snippet not in clean:
            return []

    trainer_obj = trainer_object_for_text(map_name, "TEXT_CERULEANCITY_ROCKET")
    if not trainer_obj:
        return []

    text_labels = extract_map_text_labels(map_name)
    local_ref_map = local_text_ref_map(block["raw"])
    battle_text = dialogue_action_for_script_ref(".Text", blocks_by_label, text_labels, local_ref_map)
    give_up = dialogue_action_for_script_ref(".IGiveUpText", blocks_by_label, text_labels, local_ref_map)
    return_tm = dialogue_action_for_script_ref(".IllReturnTheTMText", blocks_by_label, text_labels, local_ref_map)
    received = dialogue_action_for_script_ref(".ReceivedTM28Text", blocks_by_label, text_labels, local_ref_map, "TM_DIG")
    if not battle_text or not give_up or not return_tm or not received:
        return []

    source = source_metadata(
        map_name,
        "rocket_reward_battle_v1",
        script_path,
        text_path,
        [
            "sourceBlock=CeruleanCityRocketText",
            "Generated from scripted Rocket battle plus post-win TM28 reward state machine.",
            "The original fade effect around CeruleanHideRocket is collapsed to object visibility actions.",
        ],
    )
    source["coveredLabels"] = [
        "CeruleanCityDefaultScript",
        "CeruleanCityRocketDefeatedScript",
        "CeruleanCityRocketText",
        "CeruleanHideRocket",
    ]

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "CeruleanCityRocketBattleTM28",
            "trigger": {
                "type": "npc_click",
                "label": "TEXT_CERULEANCITY_ROCKET",
                "sourceLabel": "CeruleanCityRocketText",
            },
            "conditions": {"requiresEventAbsent": "EVENT_BEAT_CERULEAN_ROCKET_THIEF"},
            "actions": [
                {"type": "lockInput"},
                battle_text,
                trainer_battle_action_from_object(
                    trainer_obj,
                    "EVENT_BEAT_CERULEAN_ROCKET_THIEF",
                    [
                        give_up,
                        return_tm,
                        {"type": "giveItem", "itemConstant": "TM_DIG", "quantity": 1},
                        received,
                        {"type": "showObject", "objectKey": "HS_CERULEAN_GUARD_1"},
                        {"type": "hideObject", "objectKey": "HS_CERULEAN_GUARD_2"},
                        {"type": "hideObject", "objectKey": "HS_CERULEAN_ROCKET"},
                    ],
                ),
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        }
    ]


def rocket_reward_battle_candidates():
    return route24_rocket_reward_battle_candidate() + cerulean_rocket_reward_battle_candidate()


def display_text_constants(clean):
    return re.findall(
        r"\bld\s+a,\s+(TEXT_\w+)\s*\n\s*ldh\s+\[hTextID\],\s*a\s*\n\s*call\s+DisplayTextID\b",
        clean,
    )


def missable_object_actions(clean):
    actions = []
    for object_key, op in re.findall(
        r"\bld\s+a,\s+(HS_[A-Z0-9_]+)\s*\n"
        r"\s*ld\s+\[wMissableObjectIndex\],\s*a\s*\n"
        r"\s*predef(?:_jump)?\s+(HideObject|ShowObject)\b",
        clean,
    ):
        actions.append(
            {
                "type": "hideObject" if op == "HideObject" else "showObject",
                "objectKey": object_key,
            }
        )
    return actions


def indexed_progression_events_and_object_actions(raw):
    clean_lines = [strip_comment(line) for line in raw.splitlines()]
    events = []
    objects = []
    idx = 0
    while idx < len(clean_lines):
        line = clean_lines[idx]
        event_match = re.fullmatch(r"(SetEvent|CheckAndSetEvent)\s+(EVENT_[A-Z0-9_]+)", line)
        if event_match:
            events.append(
                {
                    "index": idx,
                    "op": event_match.group(1),
                    "flag": event_match.group(2),
                }
            )

        direct_match = re.fullmatch(r"(HideObject|ShowObject)\s+(HS_[A-Z0-9_]+)", line)
        if direct_match:
            objects.append(
                {
                    "index": idx,
                    "op": direct_match.group(1),
                    "object": direct_match.group(2),
                }
            )

        object_match = re.fullmatch(r"ld\s+a,\s+(HS_[A-Z0-9_]+)", line)
        if object_match and idx + 2 < len(clean_lines):
            if re.fullmatch(r"ld\s+\[wMissableObjectIndex\],\s+a", clean_lines[idx + 1]):
                op_match = re.fullmatch(r"predef(?:_jump)?\s+(HideObject|ShowObject)", clean_lines[idx + 2])
                if op_match:
                    objects.append(
                        {
                            "index": idx + 2,
                            "op": op_match.group(1),
                            "object": object_match.group(1),
                        }
                    )
                    idx += 2
        idx += 1
    return events, objects


def nearby_progression_flags(object_index, events):
    if not events:
        return []

    previous = [event for event in events if event["index"] < object_index]
    if previous:
        flags = []
        cursor = len(previous) - 1
        last_index = previous[cursor]["index"]
        while cursor >= 0:
            event = previous[cursor]
            if last_index - event["index"] > len(flags):
                break
            flags.append(event["flag"])
            cursor -= 1
        return unique_sorted(flags)

    following = [event for event in events if event["index"] > object_index]
    if not following:
        return []
    flags = []
    first_index = following[0]["index"]
    for event in following:
        if event["index"] - first_index > len(flags):
            break
        flags.append(event["flag"])
    return unique_sorted(flags)


def missable_object_lookup(conn):
    rows = conn.execute(
        """
        SELECT hs_constant, map_constant, map_id, object_name
        FROM missable_objects
        WHERE object_name IS NOT NULL AND object_name <> ''
        """
    )
    return {
        hs_constant: {
            "mapName": map_constant,
            "mapId": map_id,
            "objectName": object_name,
        }
        for hs_constant, map_constant, map_id, object_name in rows
    }


def object_visibility_rule_candidates(conn, ir_blocks):
    missables = missable_object_lookup(conn)
    candidates = []
    seen = set()
    for block in ir_blocks:
        events, objects = indexed_progression_events_and_object_actions(block["rawAsm"])
        if not events or not objects:
            continue
        for obj in objects:
            missable = missables.get(obj["object"])
            if not missable:
                continue
            flags = nearby_progression_flags(obj["index"], events)
            if not flags:
                continue
            visible = obj["op"] == "ShowObject"
            for flag in flags:
                label = f"{block['label']}:{flag}:{obj['object']}:{obj['op']}"
                key = (missable["mapId"], missable["objectName"], flag, visible, label)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    {
                        "version": 1,
                        "kind": "objectVisibility",
                        "mapName": missable["mapName"],
                        "mapId": missable["mapId"],
                        "objectName": missable["objectName"],
                        "objectKey": obj["object"],
                        "visible": visible,
                        "requiresEvent": flag,
                        "label": label,
                        "sourceMapName": block["mapName"],
                        "scriptLabel": block["label"],
                        "confidence": "adapter",
                        "source": {
                            "adapter": "flagged_missable_object_visibility_v1",
                            "sourceLabel": block["label"],
                            "sourceMapName": block["mapName"],
                            "objectOperation": obj["op"],
                            "objectKey": obj["object"],
                            "requiresEvent": flag,
                        },
                    }
                )
    return candidates


def multi_event_actions(clean):
    actions = []
    for raw_line in clean.splitlines():
        match = re.match(r"\s*(SetEvents|ResetEvents)\s+(.+)$", raw_line)
        if not match:
            continue
        action_type = "setEvent" if match.group(1) == "SetEvents" else "resetEvent"
        for flag in re.findall(r"EVENT_\w+", match.group(2)):
            actions.append({"type": action_type, "event": flag})
    return actions


def badge_flag_from_block(clean):
    match = re.search(r"\bset\s+BIT_([A-Z0-9_]+BADGE),\s+\[hl\]", clean)
    if not match:
        return ""
    return f"EVENT_GOT_{match.group(1)}"


def gym_leader_tm_reward_candidate_for_block(map_name, script_path, text_path, text_pointers, text_labels, block):
    raw = block["raw"]
    clean = "\n".join(strip_comment(line) for line in raw.splitlines())
    if "GiveItem" not in clean or "DisplayTextID" not in clean:
        return []
    if "wObtainedBadges" not in clean or "wBeatGymFlags" not in clean:
        return []
    if not re.search(r"\b\w*ReceiveTM\d+\w*:?", clean):
        return []

    ir = extract_features(block["label"], raw)
    item_refs = [ref for ref in ir["itemRefs"] if ref.get("source") == "lb_bc"]
    if len(item_refs) != 1:
        return []
    item = item_refs[0]

    set_events = [ref["flag"] for ref in ir["eventRefs"] if ref["op"] in {"SetEvent", "SetEvents"}]
    beat_flags = [
        flag
        for flag in set_events
        if flag.startswith("EVENT_BEAT_") and "_GYM_TRAINER_" not in flag
    ]
    tm_flags = [flag for flag in set_events if flag.startswith("EVENT_GOT_TM")]
    if len(beat_flags) != 1 or len(tm_flags) != 1:
        return []
    beat_flag = beat_flags[0]
    tm_flag = tm_flags[0]
    badge_flag = badge_flag_from_block(clean)
    if not badge_flag:
        return []

    dialogue_actions = []
    covered_labels = [block["label"]]
    blocks_by_label = {b["label"]: b for b in extract_label_blocks(script_path.read_text())}
    for text_constant in display_text_constants(clean):
        if re.search(r"(?:NO_ROOM|NO_SPACE|BAG_FULL)", text_constant):
            continue
        lines = lines_for_text_constant(text_constant, text_pointers, blocks_by_label, text_labels)
        lines = hydrate_received_item_lines(lines, item["item"])
        if lines:
            dialogue_actions.append({"type": "dialogue", "lines": lines})
        for label, constant in text_pointers.items():
            if constant == text_constant:
                covered_labels.append(label)
                break

    if not dialogue_actions:
        return []

    source = source_metadata(
        map_name,
        "gym_leader_tm_reward_v1",
        script_path,
        text_path,
        [
            f"sourceBlock={block['label']}",
            "Generated from post-battle gym leader GiveItem + badge-bit state machines.",
            "Emits EVENT_GOT_*BADGE compatibility flags for downstream runtimes.",
            "Bag-full/no-room branches remain downstream behavior.",
        ],
    )
    source["coveredLabels"] = unique_sorted(covered_labels)

    actions = [{"type": "lockInput"}]
    actions.extend(dialogue_actions)
    actions.append(
        {
            "type": "giveItem",
            "itemConstant": item["item"],
            "quantity": item.get("quantity", 1),
        }
    )
    for flag in [beat_flag, badge_flag, tm_flag]:
        actions.append({"type": "setEvent", "event": flag})
    actions.extend(missable_object_actions(clean))
    actions.extend(multi_event_actions(clean))
    actions.append({"type": "unlockInput"})

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": block["label"],
            "trigger": {
                "type": "map_script",
                "label": block["label"],
                "sourceLabel": block["label"],
            },
            "conditions": {
                "requiresEvent": beat_flag,
                "requiresEventAbsent": tm_flag,
            },
            "actions": actions,
            "source": source,
            "confidence": "adapter",
        }
    ]


def gym_leader_tm_reward_candidates():
    candidates = []
    for script_path in sorted(SCRIPTS_DIR.glob("*Gym.asm")):
        map_name = script_path.stem
        text_path = TEXT_DIR / f"{map_name}.asm"
        script_content = script_path.read_text()
        text_pointers = parse_text_pointer_map(script_content)
        if not text_pointers:
            continue
        text_labels = extract_map_text_labels(map_name)
        for block in extract_label_blocks(script_content):
            candidates.extend(
                gym_leader_tm_reward_candidate_for_block(
                    map_name,
                    script_path,
                    text_path,
                    text_pointers,
                    text_labels,
                    block,
                )
            )
    return candidates


def asm_literal_to_int(value):
    value = value.strip()
    if value.startswith("$"):
        return int(value[1:], 16)
    return int(value)


def elite_four_exit_tile_override_candidates():
    specs = [
        {
            "mapName": "LoreleisRoom",
            "scriptLabel": "LoreleiShowOrHideExitBlock",
            "labelPrefix": "LoreleiExitBlock",
        },
        {
            "mapName": "BrunosRoom",
            "scriptLabel": "BrunoShowOrHideExitBlock",
            "labelPrefix": "BrunoExitBlock",
        },
        {
            "mapName": "AgathasRoom",
            "scriptLabel": "AgathaShowOrHideExitBlock",
            "labelPrefix": "AgathaExitBlock",
        },
    ]
    candidates = []
    for spec in specs:
        map_name = spec["mapName"]
        script_path = SCRIPTS_DIR / f"{map_name}.asm"
        text_path = TEXT_DIR / f"{map_name}.asm"
        if not script_path.exists():
            continue
        script_content = script_path.read_text()
        blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
        block = blocks_by_label.get(spec["scriptLabel"])
        if not block:
            continue
        clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
        if "ReplaceTileBlock" not in clean:
            continue
        match = re.search(
            r"CheckEvent\s+(EVENT_\w+)\s+"
            r"jr\s+z,\s+\.blockExitToNextRoom\s+"
            r"ld\s+a,\s+(\$[0-9a-fA-F]+|\d+)\s+"
            r"(?:jp|jr)\s+\.setExitBlock\s+"
            r"\.blockExitToNextRoom\s+"
            r"ld\s+a,\s+(\$[0-9a-fA-F]+|\d+)\s+"
            r"\.setExitBlock\s+"
            r"ld\s+\[wNewTileBlockID\],\s+a\s+"
            r"lb\s+bc,\s+(\d+),\s+(\d+)\s+"
            r"predef_jump\s+ReplaceTileBlock",
            clean,
        )
        if not match:
            continue
        beat_flag, open_block, closed_block, block_x, block_y = match.groups()
        source = source_metadata(
            map_name,
            "elite_four_exit_tile_override_v1",
            script_path,
            text_path,
            [
                f"sourceBlock={spec['scriptLabel']}",
                "Generated from CheckEvent + ReplaceTileBlock Elite Four exit-block map-load state.",
            ],
        )
        source["coveredLabels"] = [spec["scriptLabel"]]
        candidates.append(
            {
                "version": 1,
                "kind": "eventTileOverrideCandidate",
                "mapName": map_name,
                "scriptLabel": spec["scriptLabel"],
                "replacements": [
                    {
                        "blockX": int(block_x),
                        "blockY": int(block_y),
                        "blockId": asm_literal_to_int(closed_block),
                        "requiresEventAbsent": beat_flag,
                        "labelPrefix": f"{spec['labelPrefix']}Closed",
                    },
                    {
                        "blockX": int(block_x),
                        "blockY": int(block_y),
                        "blockId": asm_literal_to_int(open_block),
                        "requiresEvent": beat_flag,
                        "labelPrefix": f"{spec['labelPrefix']}Open",
                    },
                ],
                "source": source,
                "confidence": "adapter",
            }
        )
    return candidates


def lance_room_entrance_tile_override_candidates():
    map_name = "LancesRoom"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    block = blocks_by_label.get("LanceShowOrHideEntranceBlocks")
    if not block:
        return []

    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    match = re.search(
        r"CheckEvent\s+(EVENT_LANCES_ROOM_LOCK_DOOR)\s+"
        r"jr\s+nz,\s+\.closeEntrance\s+"
        r"ld\s+a,\s+(\$[0-9a-fA-F]+|\d+)\s+"
        r"ld\s+b,\s+(\$[0-9a-fA-F]+|\d+)\s+"
        r"jp\s+\.setEntranceBlocks\s+"
        r"\.closeEntrance\s+"
        r"ld\s+a,\s+(\$[0-9a-fA-F]+|\d+)\s+"
        r"ld\s+b,\s+(\$[0-9a-fA-F]+|\d+)\s+"
        r"\.setEntranceBlocks\s+"
        r"push\s+bc\s+"
        r"ld\s+\[wNewTileBlockID\],\s+a\s+"
        r"lb\s+bc,\s+(\d+),\s+(\d+)\s+"
        r"call\s+\.SetEntranceBlock\s+"
        r"pop\s+bc\s+"
        r"ld\s+a,\s+b\s+"
        r"ld\s+\[wNewTileBlockID\],\s+a\s+"
        r"lb\s+bc,\s+(\d+),\s+(\d+)\s+"
        r"\.SetEntranceBlock:\s+"
        r"predef_jump\s+ReplaceTileBlock",
        clean,
    )
    if not match:
        return []

    (
        lock_flag,
        open_block_left,
        open_block_right,
        closed_block_left,
        closed_block_right,
        left_block_x,
        left_block_y,
        right_block_x,
        right_block_y,
    ) = match.groups()

    source = source_metadata(
        map_name,
        "lance_room_entrance_tile_override_v1",
        script_path,
        text_path,
        [
            "sourceBlock=LanceShowOrHideEntranceBlocks",
            "Generated from Lance room's two-block entrance lock ReplaceTileBlock map-load state.",
            "The source sets EVENT_LANCES_ROOM_LOCK_DOOR from LancesRoomDefaultScript; this candidate covers tile state only.",
        ],
    )
    source["coveredLabels"] = ["LanceShowOrHideEntranceBlocks"]

    return [
        {
            "version": 1,
            "kind": "eventTileOverrideCandidate",
            "mapName": map_name,
            "scriptLabel": "LancesRoomEntranceBlocks",
            "replacements": [
                {
                    "blockX": int(left_block_x),
                    "blockY": int(left_block_y),
                    "blockId": asm_literal_to_int(open_block_left),
                    "requiresEventAbsent": lock_flag,
                    "labelPrefix": "LanceEntranceLeftOpen",
                },
                {
                    "blockX": int(right_block_x),
                    "blockY": int(right_block_y),
                    "blockId": asm_literal_to_int(open_block_right),
                    "requiresEventAbsent": lock_flag,
                    "labelPrefix": "LanceEntranceRightOpen",
                },
                {
                    "blockX": int(left_block_x),
                    "blockY": int(left_block_y),
                    "blockId": asm_literal_to_int(closed_block_left),
                    "requiresEvent": lock_flag,
                    "labelPrefix": "LanceEntranceLeftClosed",
                },
                {
                    "blockX": int(right_block_x),
                    "blockY": int(right_block_y),
                    "blockId": asm_literal_to_int(closed_block_right),
                    "requiresEvent": lock_flag,
                    "labelPrefix": "LanceEntranceRightClosed",
                },
            ],
            "source": source,
            "confidence": "adapter",
        }
    ]


def asm_bc_literal_to_xy(value):
    value = value.strip()
    if value.startswith("$"):
        encoded = int(value[1:], 16)
        return encoded >> 8, encoded & 0xFF
    return None


def mansion_helper_block_ids(script_content):
    helper_ids = {}
    for block in extract_label_blocks(script_content):
        clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
        match = re.search(
            r"\bld\s+a,\s+(\$[0-9a-fA-F]+|\d+)\s+"
            r"ld\s+\[wNewTileBlockID\],\s+a\b",
            clean,
        )
        if match:
            helper_ids[block["label"]] = asm_literal_to_int(match.group(1))
    return helper_ids


def mansion_branch_text(clean, label):
    source_lines = [line.strip() for line in clean.splitlines() if line.strip()]
    lines = []
    collecting = False
    for line in source_lines:
        if label:
            if line == f".{label}" or line == f".{label}:":
                collecting = True
                continue
        elif re.match(r"jr\s+nz,\s+\.switchTurnedOn$", line):
            collecting = True
            continue

        if not collecting:
            continue
        if not label and (line == ".switchTurnedOn" or line == ".switchTurnedOn:"):
            break
        if line == "ret":
            break
        lines.append(line)
    return "\n".join(lines)


def mansion_replacements_for_branch(section, helper_ids):
    replacements = []
    current_block = None
    current_xy = None
    for line in section.splitlines():
        block_match = re.match(r"ld\s+a,\s+(\$[0-9a-fA-F]+|\d+)$", line)
        if block_match:
            current_block = asm_literal_to_int(block_match.group(1))
            continue
        coord_match = re.match(r"lb\s+bc,\s+(\d+),\s+(\d+)$", line)
        if coord_match:
            current_xy = (int(coord_match.group(1)), int(coord_match.group(2)))
            continue
        packed_coord_match = re.match(r"ld\s+bc,\s+(\$[0-9a-fA-F]+)$", line)
        if packed_coord_match:
            current_xy = asm_bc_literal_to_xy(packed_coord_match.group(1))
            continue
        call_match = re.match(r"(?:call|jp)\s+(\w+)$", line)
        if not call_match or not current_xy:
            continue
        helper = call_match.group(1)
        block_id = current_block
        if helper in helper_ids:
            block_id = helper_ids[helper]
        if block_id is None:
            continue
        replacements.append({"blockX": current_xy[0], "blockY": current_xy[1], "blockId": block_id})
        current_xy = None
    return replacements


def pokemon_mansion_switch_tile_override_candidates():
    specs = [
        ("PokemonMansion1F", "Mansion1CheckReplaceSwitchDoorBlocks", "1F"),
        ("PokemonMansion2F", "Mansion2CheckReplaceSwitchDoorBlocks", "2F"),
        ("PokemonMansion3F", "Mansion3CheckReplaceSwitchDoorBlocks", "3F"),
        ("PokemonMansionB1F", "MansionB1FCheckReplaceSwitchDoorBlocks", "B1F"),
    ]
    candidates = []
    for map_name, source_label, floor_label in specs:
        script_path = SCRIPTS_DIR / f"{map_name}.asm"
        text_path = TEXT_DIR / f"{map_name}.asm"
        if not script_path.exists():
            continue
        script_content = script_path.read_text()
        blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
        block = blocks_by_label.get(source_label)
        if not block:
            continue
        clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
        if "EVENT_MANSION_SWITCH_ON" not in clean:
            continue

        helper_ids = mansion_helper_block_ids(script_content)
        off_replacements = mansion_replacements_for_branch(mansion_branch_text(clean, ""), helper_ids)
        on_replacements = mansion_replacements_for_branch(mansion_branch_text(clean, "switchTurnedOn"), helper_ids)
        if not off_replacements or not on_replacements:
            continue

        replacements = []
        for replacement in off_replacements:
            replacements.append(
                {
                    **replacement,
                    "requiresEventAbsent": "EVENT_MANSION_SWITCH_ON",
                    "labelPrefix": f"PokemonMansionSwitchOff_{floor_label}_{replacement['blockX']}_{replacement['blockY']}",
                }
            )
        for replacement in on_replacements:
            replacements.append(
                {
                    **replacement,
                    "requiresEvent": "EVENT_MANSION_SWITCH_ON",
                    "labelPrefix": f"PokemonMansionSwitchOn_{floor_label}_{replacement['blockX']}_{replacement['blockY']}",
                }
            )

        source = source_metadata(
            map_name,
            "pokemon_mansion_switch_tile_override_v1",
            script_path,
            text_path,
            [
                f"sourceBlock={source_label}",
                "Generated from Pokemon Mansion switch map-load ReplaceTileBlock state.",
            ],
        )
        source["coveredLabels"] = [source_label]
        candidates.append(
            {
                "version": 1,
                "kind": "eventTileOverrideCandidate",
                "mapName": map_name,
                "scriptLabel": f"{map_name}SwitchDoorTiles",
                "replacements": replacements,
                "source": source,
                "confidence": "adapter",
            }
        )
    return candidates


def silph_card_key_tile_override_candidates():
    candidates = []
    for script_path in sorted(SCRIPTS_DIR.glob("SilphCo*F.asm")):
        map_name = script_path.stem
        floor_match = re.match(r"SilphCo(\d+)F$", map_name)
        if not floor_match:
            continue
        floor = f"{floor_match.group(1)}F"
        text_path = TEXT_DIR / f"{map_name}.asm"
        script_content = script_path.read_text()
        blocks = extract_label_blocks(script_content)
        blocks_by_label = {block["label"]: block for block in blocks}
        callback = next((block for block in blocks if "GateCallbackScript" in block["label"]), None)
        if not callback:
            continue

        clean = "\n".join(strip_comment(line) for line in callback["raw"].splitlines())
        if "ReplaceTileBlock" not in clean or "CardKeyDoor" not in clean:
            continue

        coords = [
            (int(x), int(y))
            for x, y in re.findall(r"dbmapcoord\s+(\d+),\s+(\d+)", callback["raw"])
        ]
        if not coords and "SilphCo11GateCoords" in blocks_by_label:
            coords = [
                (int(x), int(y))
                for x, y in re.findall(
                    r"dbmapcoord\s+(\d+),\s+(\d+)",
                    blocks_by_label["SilphCo11GateCoords"]["raw"],
                )
            ]

        flags = []
        for flag in re.findall(
            r"CheckEvent(?:AfterBranchReuseA)?\s+(EVENT_SILPH_CO_\d+_UNLOCKED_DOOR\d*)",
            clean,
        ):
            if flag not in flags:
                flags.append(flag)

        closed_blocks = [
            (asm_literal_to_int(block_id), int(block_y), int(block_x))
            for block_id, block_y, block_x in re.findall(
                r"ld\s+a,\s+(\$[0-9a-fA-F]+|\d+)\s+"
                r"ld\s+\[wNewTileBlockID\],\s+a\s+"
                r"lb\s+bc,\s+(\d+),\s+(\d+)\s+"
                r"predef(?:_jump)?\s+ReplaceTileBlock",
                clean,
            )
        ]
        if not coords or len(coords) != len(flags) or len(flags) != len(closed_blocks):
            continue

        open_block = 0x03 if map_name == "SilphCo11F" else 0x0E
        replacements = []
        for index, (flag, (closed_block, closed_y, closed_x), coord) in enumerate(
            zip(flags, closed_blocks, coords),
            start=1,
        ):
            block_x, block_y = coord
            if (closed_x, closed_y) != (block_x, block_y):
                continue
            label_prefix = f"SilphCardKeyDoor_{floor}_{index}"
            replacements.extend(
                [
                    {
                        "blockX": block_x,
                        "blockY": block_y,
                        "blockId": closed_block,
                        "requiresEventAbsent": flag,
                        "labelPrefix": f"{label_prefix}_Closed",
                    },
                    {
                        "blockX": block_x,
                        "blockY": block_y,
                        "blockId": open_block,
                        "requiresEvent": flag,
                        "labelPrefix": f"{label_prefix}_Open",
                    },
                ]
            )
        if len(replacements) != len(flags) * 2:
            continue

        helper_labels = [
            block["label"]
            for block in blocks
            if block["label"] != callback["label"]
            and (
                "SetCardKeyDoorYScript" in block["label"]
                or "UnlockedDoorEventScript" in block["label"]
                or "UnlockedSilphCoDoorsScript" in block["label"]
                or "SetUnlockedDoorEventScript" in block["label"]
            )
        ]
        source = source_metadata(
            map_name,
            "silph_card_key_tile_override_v1",
            script_path,
            text_path,
            [
                f"sourceBlock={callback['label']}",
                "Generated from Silph Co Card Key map-load gate callback and shared Card Key open-block behavior.",
            ],
        )
        source["coveredLabels"] = unique_sorted([callback["label"], *helper_labels])
        candidates.append(
            {
                "version": 1,
                "kind": "eventTileOverrideCandidate",
                "mapName": map_name,
                "scriptLabel": f"{map_name}CardKeyDoors",
                "replacements": replacements,
                "source": source,
                "confidence": "adapter",
            }
        )
    return candidates


def victory_road_boulder_tile_override_candidates():
    specs = [
        {
            "mapName": "VictoryRoad1F",
            "sourceLabel": "VictoryRoad1F_Script",
            "replacements": [
                {
                    "flag": "EVENT_VICTORY_ROAD_1_BOULDER_ON_SWITCH",
                    "blockId": 0x1D,
                    "blockX": 4,
                    "blockY": 6,
                    "labelPrefix": "VictoryRoadBoulderBlock_1F_Switch_1d",
                }
            ],
        },
        {
            "mapName": "VictoryRoad2F",
            "sourceLabel": "VictoryRoad2FCheckBoulderEventScript",
            "helperLabels": ["VictoryRoad2FReplaceTileBlockScript"],
            "replacements": [
                {
                    "flag": "EVENT_VICTORY_ROAD_2_BOULDER_ON_SWITCH1",
                    "blockId": 0x15,
                    "blockX": 3,
                    "blockY": 4,
                    "labelPrefix": "VictoryRoadBoulderBlock_2F_Switch1_15",
                },
                {
                    "flag": "EVENT_VICTORY_ROAD_2_BOULDER_ON_SWITCH2",
                    "blockId": 0x1D,
                    "blockX": 11,
                    "blockY": 7,
                    "labelPrefix": "VictoryRoadBoulderBlock_2F_Switch2_1d",
                },
            ],
        },
        {
            "mapName": "VictoryRoad3F",
            "sourceLabel": "VictoryRoad3FCheckBoulderEventScript",
            "replacements": [
                {
                    "flag": "EVENT_VICTORY_ROAD_3_BOULDER_ON_SWITCH1",
                    "blockId": 0x1D,
                    "blockX": 3,
                    "blockY": 5,
                    "labelPrefix": "VictoryRoadBoulderBlock_3F_Switch_1d",
                }
            ],
        },
    ]
    candidates = []
    for spec in specs:
        map_name = spec["mapName"]
        script_path = SCRIPTS_DIR / f"{map_name}.asm"
        text_path = TEXT_DIR / f"{map_name}.asm"
        if not script_path.exists():
            continue
        blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_path.read_text())}
        block = blocks_by_label.get(spec["sourceLabel"])
        if not block:
            continue
        clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
        helper_clean = "\n".join(
            "\n".join(strip_comment(line) for line in blocks_by_label[label]["raw"].splitlines())
            for label in spec.get("helperLabels", [])
            if label in blocks_by_label
        )
        replacements = []
        all_verified = True
        for replacement in spec["replacements"]:
            direct_pattern = (
                rf"(?:CheckEvent(?:HL|ReuseA)?|CheckEventReuseA)\s+{replacement['flag']}[\s\S]*?"
                rf"ld\s+a,\s+\${replacement['blockId']:x}[\s\S]*?"
                rf"lb\s+bc,\s+{replacement['blockY']},\s+{replacement['blockX']}[\s\S]*?"
                rf"(?:predef(?:_jump)?\s+ReplaceTileBlock|call\s+VictoryRoad2FReplaceTileBlockScript)"
            )
            helper_pattern = (
                rf"(?:CheckEvent(?:HL|ReuseA)?|CheckEventReuseA)\s+{replacement['flag']}[\s\S]*?"
                rf"ld\s+a,\s+\${replacement['blockId']:x}[\s\S]*?"
                rf"lb\s+bc,\s+{replacement['blockY']},\s+{replacement['blockX']}\s*$"
            )
            if not re.search(direct_pattern, clean, re.IGNORECASE) and not (
                helper_clean
                and "ReplaceTileBlock" in helper_clean
                and re.search(helper_pattern, clean, re.IGNORECASE)
            ):
                all_verified = False
                break
            replacements.append(
                {
                    "blockX": replacement["blockX"],
                    "blockY": replacement["blockY"],
                    "blockId": replacement["blockId"],
                    "requiresEvent": replacement["flag"],
                    "labelPrefix": replacement["labelPrefix"],
                }
            )
        if not all_verified:
            continue

        source = source_metadata(
            map_name,
            "victory_road_boulder_tile_override_v1",
            script_path,
            text_path,
            [
                f"sourceBlock={spec['sourceLabel']}",
                "Generated from Victory Road boulder switch map-load tile replacement checks.",
                "This candidate covers tile state only; boulder push/switch-setting behavior remains a separate runtime concern.",
            ],
        )
        source["coveredLabels"] = unique_sorted([spec["sourceLabel"], *spec.get("helperLabels", [])])
        candidates.append(
            {
                "version": 1,
                "kind": "eventTileOverrideCandidate",
                "mapName": map_name,
                "scriptLabel": f"{map_name}BoulderSwitchTiles",
                "replacements": replacements,
                "source": source,
                "confidence": "adapter",
            }
        )
    return candidates


def vermilion_gym_trash_door_tile_override_candidates():
    map_name = "VermilionGym"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists():
        return []

    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_path.read_text())}
    block = blocks_by_label.get("VermilionGymSetDoorTile")
    if not block:
        return []
    clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
    match = re.search(
        r"CheckEvent\s+(EVENT_\w+)\s+"
        r"jr\s+nz,\s+\.doorsOpen\s+"
        r"ld\s+a,\s+(\$[0-9a-fA-F]+|\d+)\s+"
        r"jr\s+\.replaceTile\s+"
        r"\.doorsOpen\s+"
        r"(?:ld\s+a,\s+SFX_\w+\s+call\s+PlaySound\s+)?"
        r"ld\s+a,\s+(\$[0-9a-fA-F]+|\d+)\s+"
        r"\.replaceTile\s+"
        r"ld\s+\[wNewTileBlockID\],\s+a\s+"
        r"lb\s+bc,\s+(\d+),\s+(\d+)\s+"
        r"predef_jump\s+ReplaceTileBlock",
        clean,
    )
    if not match:
        return []

    open_flag, closed_block, open_block, block_x, block_y = match.groups()
    source = source_metadata(
        map_name,
        "vermilion_gym_trash_door_tile_override_v1",
        script_path,
        text_path,
        [
            "sourceBlock=VermilionGymSetDoorTile",
            "Generated from the Vermilion Gym second-lock map-load door tile replacement.",
        ],
    )
    source["coveredLabels"] = ["VermilionGymSetDoorTile"]
    return [
        {
            "version": 1,
            "kind": "eventTileOverrideCandidate",
            "mapName": map_name,
            "scriptLabel": "VermilionGymTrashDoorTiles",
            "replacements": [
                {
                    "blockX": int(block_x),
                    "blockY": int(block_y),
                    "blockId": asm_literal_to_int(closed_block),
                    "requiresEventAbsent": open_flag,
                    "labelPrefix": "VermilionGymTrashDoorClosed",
                },
                {
                    "blockX": int(block_x),
                    "blockY": int(block_y),
                    "blockId": asm_literal_to_int(open_block),
                    "requiresEvent": open_flag,
                    "labelPrefix": "VermilionGymTrashDoorOpen",
                },
            ],
            "source": source,
            "confidence": "adapter",
        }
    ]


def game_corner_rocket_hideout_tile_override_candidates():
    map_name = "GameCorner"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    map_load_block = blocks_by_label.get("GameCornerSetRocketHideoutDoorTile")
    poster_block = blocks_by_label.get("GameCornerPosterText")
    if not map_load_block or not poster_block:
        return []

    map_load_clean = "\n".join(strip_comment(line) for line in map_load_block["raw"].splitlines())
    poster_clean = "\n".join(strip_comment(line) for line in poster_block["raw"].splitlines())
    closed_match = re.search(
        r"\bCheckEvent\s+(EVENT_\w+)\s+"
        r"ret\s+nz\s+"
        r"ld\s+a,\s+(\$[0-9a-fA-F]+|\d+)\s+"
        r"ld\s+\[wNewTileBlockID\],\s+a\s+"
        r"lb\s+bc,\s+(\d+),\s+(\d+)\s+"
        r"predef(?:_jump)?\s+ReplaceTileBlock\b",
        map_load_clean,
    )
    open_match = re.search(
        r"\bSetEvent\s+(EVENT_\w+).*?"
        r"ld\s+a,\s+(\$[0-9a-fA-F]+|\d+)\s+"
        r"ld\s+\[wNewTileBlockID\],\s+a\s+"
        r"lb\s+bc,\s+(\d+),\s+(\d+)\s+"
        r"predef(?:_jump)?\s+ReplaceTileBlock\b",
        poster_clean,
        re.DOTALL,
    )
    if not closed_match or not open_match:
        return []

    closed_flag, closed_block, closed_y, closed_x = closed_match.groups()
    open_flag, open_block, open_y, open_x = open_match.groups()
    if closed_flag != open_flag or (closed_x, closed_y) != (open_x, open_y):
        return []

    source = source_metadata(
        map_name,
        "game_corner_rocket_hideout_tile_override_v1",
        script_path,
        text_path,
        [
            "sourceBlock=GameCornerSetRocketHideoutDoorTile",
            "sourceBlock=GameCornerPosterText",
            "Generated from the Game Corner poster switch's map-load and immediate ReplaceTileBlock behavior.",
        ],
    )
    source["coveredLabels"] = ["GameCornerSetRocketHideoutDoorTile"]
    return [
        {
            "version": 1,
            "kind": "eventTileOverrideCandidate",
            "mapName": map_name,
            "scriptLabel": "GameCornerRocketHideoutDoorTile",
            "replacements": [
                {
                    "blockX": int(closed_x),
                    "blockY": int(closed_y),
                    "blockId": asm_literal_to_int(closed_block),
                    "requiresEventAbsent": closed_flag,
                    "labelPrefix": "GameCornerRocketHideoutDoorClosed",
                },
                {
                    "blockX": int(open_x),
                    "blockY": int(open_y),
                    "blockId": asm_literal_to_int(open_block),
                    "requiresEvent": open_flag,
                    "labelPrefix": "GameCornerRocketHideoutDoorOpen",
                },
            ],
            "source": source,
            "confidence": "adapter",
        }
    ]


def game_corner_rocket_defeated_candidate():
    map_name = "GameCorner"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    text_labels = extract_text_labels(text_path)
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    battle_block = blocks_by_label.get("GameCornerRocketBattleScript")
    exit_block = blocks_by_label.get("GameCornerRocketExitScript")
    direct_movement_block = blocks_by_label.get("GameCornerMovement_Rocket_WalkDirect")
    around_movement_block = blocks_by_label.get("GameCornerMovement_Rocket_WalkAroundPlayer")
    if not battle_block or not exit_block or not direct_movement_block or not around_movement_block:
        return []

    battle_clean = "\n".join(strip_comment(line) for line in battle_block["raw"].splitlines())
    exit_clean = "\n".join(strip_comment(line) for line in exit_block["raw"].splitlines())
    if "TEXT_GAMECORNER_ROCKET_AFTER_BATTLE" not in battle_clean:
        return []
    if "HS_GAME_CORNER_ROCKET" not in exit_clean or not re.search(r"\bpredef(?:_jump)?\s+HideObject\b", exit_clean):
        return []

    direct_movement = re.findall(r"\bNPC_MOVEMENT_(UP|DOWN|LEFT|RIGHT)\b", direct_movement_block["raw"])
    around_movement = re.findall(r"\bNPC_MOVEMENT_(UP|DOWN|LEFT|RIGHT)\b", around_movement_block["raw"])
    if direct_movement != ["RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT"]:
        return []
    if around_movement != ["DOWN", "RIGHT", "RIGHT", "UP", "RIGHT", "RIGHT", "RIGHT", "RIGHT"]:
        return []

    battle_end_lines = text_labels.get("GameCornerRocketBattleEndText", [])
    after_battle_lines = text_labels.get("GameCornerRocketAfterBattleText", [])
    if not after_battle_lines:
        return []

    source = source_metadata(
        map_name,
        "game_corner_rocket_defeated_v1",
        script_path,
        text_path,
        [
            "sourceBlock=GameCornerRocketBattleScript",
            "sourceBlock=GameCornerRocketExitScript",
            "The Game Boy script branches between direct and around-player movement based on player coordinates; both source branches are retained.",
            "Trainer battle startup remains handled by the trainer runtime/manual guard script; this candidate covers the post-battle dialogue, movement, and object hide cleanup.",
        ],
    )
    source["coveredLabels"] = [
        "GameCornerRocketBattleScript",
        "GameCornerRocketExitScript",
        "GameCornerMovement_Rocket_WalkAroundPlayer",
        "GameCornerMovement_Rocket_WalkDirect",
        "GameCornerRocketBattleEndText",
        "GameCornerRocketAfterBattleText",
    ]
    source["movementVariants"] = {
        "direct": direct_movement,
        "aroundPlayer": around_movement,
    }
    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "GameCornerRocketDefeated",
            "trigger": {
                "type": "map_script",
                "label": "GameCornerRocketBattleScript",
            },
            "conditions": {
                "requiresEvent": "EVENT_BEAT_GAME_CORNER_ROCKET",
                "requiresEventAbsent": "EVENT_GAME_CORNER_ROCKET_LEFT",
            },
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "speaker": "ROCKET", "lines": battle_end_lines + after_battle_lines},
                {
                    "type": "move",
                    "actor": "ROCKET",
                    "movements": around_movement,
                    "movementVariants": [
                        {"when": {"playerY": 6}, "movements": direct_movement},
                        {"when": {"playerX": 8}, "movements": direct_movement},
                        {"when": {"default": True}, "movements": around_movement},
                    ],
                },
                {"type": "setEvent", "event": "EVENT_GAME_CORNER_ROCKET_LEFT"},
                {"type": "hideActor", "actor": "ROCKET"},
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        }
    ]


def rocket_hideout_b4f_giovanni_candidate():
    map_name = "RocketHideoutB4F"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    object_path = OBJECTS_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists() or not object_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    required_labels = [
        "RocketHideoutB4FGiovanniText",
        "RocketHideoutB4FBeatGiovanniScript",
        "RocketHideoutB4FGiovanniHopeWeMeetAgainText",
    ]
    if any(label not in blocks_by_label for label in required_labels):
        return []

    clean = "\n".join(strip_comment(line) for line in script_content.splitlines())
    required_snippets = [
        "CheckEvent EVENT_BEAT_ROCKET_HIDEOUT_GIOVANNI",
        "call EngageMapTrainer",
        "call InitBattleEnemyParameters",
        "SetEvent EVENT_BEAT_ROCKET_HIDEOUT_GIOVANNI",
        "ld a, HS_ROCKET_HIDEOUT_B4F_GIOVANNI",
        "predef HideObject",
        "ld a, HS_ROCKET_HIDEOUT_B4F_ITEM_4",
        "predef ShowObject",
    ]
    if any(snippet not in clean for snippet in required_snippets):
        return []

    object_content = object_path.read_text()
    object_match = re.search(
        r"object_event\s+25,\s+3,\s+SPRITE_GIOVANNI,\s+STAY,\s+DOWN,\s+"
        r"TEXT_ROCKETHIDEOUTB4F_GIOVANNI,\s+OPP_GIOVANNI,\s+(\d+)",
        object_content,
    )
    if not object_match:
        return []
    party_index = int(object_match.group(1))

    text_labels = extract_map_text_labels(map_name)
    intro_lines = lines_for_labels(text_labels, ["RocketHideoutB4FGiovanniImpressedYouGotHereText"])
    defeated_lines = lines_for_labels(text_labels, ["RocketHideoutB4FGiovanniWhatCannotBeText"])
    after_lines = lines_for_script_text_ref("RocketHideoutB4FGiovanniHopeWeMeetAgainText", blocks_by_label, text_labels, {})
    if not intro_lines or not defeated_lines or not after_lines:
        return []

    source = source_metadata(
        map_name,
        "rocket_hideout_b4f_giovanni_v1",
        script_path,
        text_path,
        [
            "sourceBlock=RocketHideoutB4FGiovanniText",
            "sourceBlock=RocketHideoutB4FBeatGiovanniScript",
            "Generated from Giovanni's Rocket Hideout B4F NPC battle and post-battle hide/show cleanup.",
            "The source performs a fade around object visibility updates; this neutral candidate preserves authoritative gameplay state and leaves fade presentation to downstream renderers.",
        ],
    )
    source["coveredLabels"] = [
        "RocketHideoutB4FGiovanniText",
        "RocketHideoutB4FBeatGiovanniScript",
        "RocketHideoutB4FGiovanniHopeWeMeetAgainText",
    ]
    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "RocketHideoutB4FGiovanniEncounter",
            "trigger": {
                "type": "npc_click",
                "label": "TEXT_ROCKETHIDEOUTB4F_GIOVANNI",
                "sourceLabel": "RocketHideoutB4FGiovanniText",
            },
            "conditions": {"requiresEventAbsent": "EVENT_BEAT_ROCKET_HIDEOUT_GIOVANNI"},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "speaker": "GIOVANNI", "lines": intro_lines},
                {
                    "type": "startTrainerBattle",
                    "trainerClass": "GIOVANNI",
                    "trainerName": "GIOVANNI",
                    "partyIndex": party_index,
                    "winFlag": "EVENT_BEAT_ROCKET_HIDEOUT_GIOVANNI",
                    "postWinActions": [
                        {"type": "dialogue", "speaker": "GIOVANNI", "lines": defeated_lines + after_lines},
                        {"type": "hideActor", "actor": "GIOVANNI"},
                        {"type": "setEvent", "event": "EVENT_ROCKET_HIDEOUT_GIOVANNI_LEFT"},
                        {"type": "hideObject", "objectKey": "HS_ROCKET_HIDEOUT_B4F_GIOVANNI"},
                        {"type": "showObject", "objectKey": "HS_ROCKET_HIDEOUT_B4F_ITEM_4"},
                    ],
                },
                {"type": "unlockInput"},
            ],
            "source": source,
            "confidence": "adapter",
        }
    ]


def rocket_hideout_door_unlock_candidates():
    specs = [
        {
            "mapName": "RocketHideoutB1F",
            "sourceLabel": "RocketHideoutB1FDoorCallbackScript",
            "scriptLabel": "RocketHideoutB1FDoorUnlock",
            "requiresEvents": ["EVENT_BEAT_ROCKET_HIDEOUT_1_TRAINER_4"],
            "unlockEvent": "EVENT_677",
            "notes": [
                "The source opens the B1F door once Rocket 5 is beaten, then stores EVENT_677 so later map-load tile replacement uses the open floor block.",
                "Sound playback is intentionally omitted from this neutral map-load flag-sync candidate.",
            ],
        },
        {
            "mapName": "RocketHideoutB4F",
            "sourceLabel": "RocketHideoutB4FDoorCallbackScript",
            "scriptLabel": "RocketHideoutB4FDoorUnlock",
            "requiresEvents": ["EVENT_BEAT_ROCKET_HIDEOUT_4_TRAINER_0", "EVENT_BEAT_ROCKET_HIDEOUT_4_TRAINER_1"],
            "unlockEvent": "EVENT_ROCKET_HIDEOUT_4_DOOR_UNLOCKED",
            "notes": [
                "The source opens the B4F door once both guarding Rockets are beaten, then stores EVENT_ROCKET_HIDEOUT_4_DOOR_UNLOCKED for map-load tile replacement.",
                "Sound playback is intentionally omitted from this neutral map-load flag-sync candidate.",
            ],
        },
    ]
    candidates = []
    for spec in specs:
        script_path = SCRIPTS_DIR / f"{spec['mapName']}.asm"
        text_path = TEXT_DIR / f"{spec['mapName']}.asm"
        if not script_path.exists():
            continue
        blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_path.read_text())}
        block = blocks_by_label.get(spec["sourceLabel"])
        if not block:
            continue
        clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
        if spec["unlockEvent"] not in clean or "ReplaceTileBlock" not in clean:
            continue
        if any(flag not in clean for flag in spec["requiresEvents"]):
            continue

        source = source_metadata(
            spec["mapName"],
            "rocket_hideout_door_unlock_v1",
            script_path,
            text_path,
            [f"sourceBlock={spec['sourceLabel']}", *spec["notes"]],
        )
        source["coveredLabels"] = [spec["sourceLabel"]]
        candidates.append(
            {
                "version": 1,
                "kind": "scriptEventCandidate",
                "mapName": spec["mapName"],
                "scriptLabel": spec["scriptLabel"],
                "trigger": {
                    "type": "map_script",
                    "label": spec["sourceLabel"],
                },
                "conditions": {
                    "requiresEvents": spec["requiresEvents"],
                    "requiresEventAbsent": spec["unlockEvent"],
                },
                "actions": [
                    {"type": "setEvent", "event": spec["unlockEvent"]},
                ],
                "source": source,
                "confidence": "adapter",
            }
        )
    return candidates


def rocket_hideout_door_tile_override_candidates():
    specs = [
        {
            "mapName": "RocketHideoutB1F",
            "sourceLabel": "RocketHideoutB1FDoorCallbackScript",
            "scriptLabel": "RocketHideoutB1FDoorTile",
            "unlockEvent": "EVENT_677",
            "closedBlock": 0x54,
            "openBlock": 0x0E,
            "blockX": 12,
            "blockY": 8,
            "labelPrefix": "RocketHideoutB1FDoor",
        },
        {
            "mapName": "RocketHideoutB4F",
            "sourceLabel": "RocketHideoutB4FDoorCallbackScript",
            "scriptLabel": "RocketHideoutB4FDoorTile",
            "unlockEvent": "EVENT_ROCKET_HIDEOUT_4_DOOR_UNLOCKED",
            "closedBlock": 0x2D,
            "openBlock": 0x0E,
            "blockX": 12,
            "blockY": 5,
            "labelPrefix": "RocketHideoutB4FDoor",
        },
    ]
    candidates = []
    for spec in specs:
        script_path = SCRIPTS_DIR / f"{spec['mapName']}.asm"
        text_path = TEXT_DIR / f"{spec['mapName']}.asm"
        if not script_path.exists():
            continue
        blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_path.read_text())}
        block = blocks_by_label.get(spec["sourceLabel"])
        if not block:
            continue
        clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
        expected_block_pattern = (
            rf"ld\s+a,\s+\${spec['closedBlock']:x}\b.*?"
            rf"ld\s+a,\s+\${spec['openBlock']:x}\b.*?"
            rf"lb\s+bc,\s+{spec['blockY']},\s+{spec['blockX']}\s+"
            r"predef(?:_jump)?\s+ReplaceTileBlock\b"
        )
        if not re.search(expected_block_pattern, clean, re.DOTALL | re.IGNORECASE):
            continue
        if spec["unlockEvent"] not in clean:
            continue

        source = source_metadata(
            spec["mapName"],
            "rocket_hideout_door_tile_override_v1",
            script_path,
            text_path,
            [
                f"sourceBlock={spec['sourceLabel']}",
                "Generated from Rocket Hideout door map-load ReplaceTileBlock behavior.",
                "Door unlock flag sync is emitted as a companion map_script candidate.",
            ],
        )
        source["coveredLabels"] = [spec["sourceLabel"]]
        candidates.append(
            {
                "version": 1,
                "kind": "eventTileOverrideCandidate",
                "mapName": spec["mapName"],
                "scriptLabel": spec["scriptLabel"],
                "replacements": [
                    {
                        "blockX": spec["blockX"],
                        "blockY": spec["blockY"],
                        "blockId": spec["closedBlock"],
                        "requiresEventAbsent": spec["unlockEvent"],
                        "labelPrefix": f"{spec['labelPrefix']}Closed",
                    },
                    {
                        "blockX": spec["blockX"],
                        "blockY": spec["blockY"],
                        "blockId": spec["openBlock"],
                        "requiresEvent": spec["unlockEvent"],
                        "labelPrefix": f"{spec['labelPrefix']}Open",
                    },
                ],
                "source": source,
                "confidence": "adapter",
            }
        )
    return candidates


def victory_road_boulder_target_definitions():
    specs = [
        {
            "mapName": "VictoryRoad1F",
            "sourceLabel": "VictoryRoad1FDefaultScript",
            "coordsLabel": ".SwitchCoords",
            "flags": ["EVENT_VICTORY_ROAD_1_BOULDER_ON_SWITCH"],
        },
        {
            "mapName": "VictoryRoad2F",
            "sourceLabel": "VictoryRoad2FDefaultScript",
            "coordsLabel": ".SwitchCoords",
            "flags": [
                "EVENT_VICTORY_ROAD_2_BOULDER_ON_SWITCH1",
                "EVENT_VICTORY_ROAD_2_BOULDER_ON_SWITCH2",
            ],
        },
        {
            "mapName": "VictoryRoad3F",
            "sourceLabel": "VictoryRoad3FDefaultScript",
            "coordsLabel": ".SwitchOrHoleCoords",
            "flags": [
                "EVENT_VICTORY_ROAD_3_BOULDER_ON_SWITCH1",
                "EVENT_VICTORY_ROAD_3_BOULDER_ON_SWITCH2",
            ],
            "holeIndex": 1,
            "sourceMissableObject": "HS_VICTORY_ROAD_3F_BOULDER",
            "destinationMapName": "VictoryRoad2F",
            "destinationMissableObject": "HS_VICTORY_ROAD_2F_BOULDER",
        },
    ]

    targets = []
    for spec in specs:
        map_name = spec["mapName"]
        script_path = SCRIPTS_DIR / f"{map_name}.asm"
        if not script_path.exists():
            continue
        blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_path.read_text())}
        block = blocks_by_label.get(spec["sourceLabel"])
        if not block:
            continue
        clean = "\n".join(strip_comment(line) for line in block["raw"].splitlines())
        if "CheckBoulderCoords" not in clean:
            continue
        coords = parse_local_dbmapcoords(block["raw"], spec["coordsLabel"])
        if len(coords) != len(spec["flags"]):
            continue
        if any(flag not in clean for flag in spec["flags"]):
            continue

        for idx, (coord, flag) in enumerate(zip(coords, spec["flags"])):
            drops_through_hole = idx == spec.get("holeIndex", -1)
            target = {
                "version": 1,
                "kind": "boulderTarget",
                "targetFamily": "victory_road",
                "mapName": map_name,
                "sourceLabel": spec["sourceLabel"],
                "x": coord["x"],
                "y": coord["y"],
                "flag": flag,
                "dropsThroughHole": drops_through_hole,
                "sourceMissableObject": spec.get("sourceMissableObject", "") if drops_through_hole else "",
                "destinationMapName": spec.get("destinationMapName", "") if drops_through_hole else "",
                "destinationMissableObject": spec.get("destinationMissableObject", "") if drops_through_hole else "",
                "sourceFile": f"pokemon-game-data/scripts/{script_path.name}",
                "source": {
                    "adapter": "victory_road_boulder_target_v1",
                    "scriptPath": str(script_path.relative_to(PROJECT_ROOT)),
                    "mapName": map_name,
                    "notes": [
                        f"sourceBlock={spec['sourceLabel']}",
                        f"sourceCoords={spec['coordsLabel']}",
                        "Generated from CheckBoulderCoords switch/hole target tables and source event flags.",
                    ],
                },
                "confidence": "adapter",
            }
            if drops_through_hole:
                required = [
                    spec["sourceMissableObject"],
                    spec["destinationMissableObject"],
                    "predef HideObject",
                    "predef_jump ShowObject",
                ]
                if not all(token in clean for token in required):
                    continue
                target["source"]["notes"].append("This target drops the boulder through a source hole and swaps missable objects.")
            targets.append(target)

    return targets


def boulder_target_runtime_diagnostics(targets):
    diagnostics = []
    for target in targets:
        diagnostics.append(
            {
                "mapName": target["mapName"],
                "scriptLabel": f"{target['sourceLabel']}:{target['x']},{target['y']}",
                "status": "generated",
                "reason": "victory_road_boulder_target_v1",
                "details": target,
            }
        )

    by_source_label = {}
    for target in targets:
        by_source_label.setdefault((target["mapName"], target["sourceLabel"]), []).append(target)
    for (map_name, source_label), rows in sorted(by_source_label.items()):
        diagnostics.append(
            {
                "mapName": map_name,
                "scriptLabel": source_label,
                "status": "covered",
                "reason": "victory_road_boulder_runtime_v1",
                "details": {
                    "targetFamily": "victory_road",
                    "targets": [
                        {
                            "x": row["x"],
                            "y": row["y"],
                            "flag": row["flag"],
                            "dropsThroughHole": row["dropsThroughHole"],
                        }
                        for row in rows
                    ],
                    "source": {
                        "runtimeTables": ["script_event_boulder_targets"],
                        "notes": [
                            "The source label sets boulder switch/hole flags when a pushed boulder reaches one of these coordinates.",
                            "Downstream runtimes should execute boulder pushing and switch/hole side effects server-side.",
                        ],
                    },
                },
            }
        )

    reset_specs = [
        {
            "mapName": "Route23",
            "scriptLabel": "Route23SetVictoryRoadBoulders",
            "resetFlags": [
                "EVENT_VICTORY_ROAD_2_BOULDER_ON_SWITCH1",
                "EVENT_VICTORY_ROAD_2_BOULDER_ON_SWITCH2",
                "EVENT_VICTORY_ROAD_3_BOULDER_ON_SWITCH1",
                "EVENT_VICTORY_ROAD_3_BOULDER_ON_SWITCH2",
            ],
            "affectedMaps": ["VictoryRoad2F", "VictoryRoad3F"],
        },
        {
            "mapName": "VictoryRoad2F",
            "scriptLabel": "VictoryRoad2FResetBoulderEventScript",
            "resetFlags": ["EVENT_VICTORY_ROAD_1_BOULDER_ON_SWITCH"],
            "affectedMaps": ["VictoryRoad1F"],
        },
    ]
    for spec in reset_specs:
        diagnostics.append(
            {
                "mapName": spec["mapName"],
                "scriptLabel": spec["scriptLabel"],
                "status": "covered",
                "reason": "victory_road_boulder_map_load_runtime_v1",
                "details": {
                    "targetFamily": "victory_road",
                    "resetFlags": spec["resetFlags"],
                    "affectedMaps": spec["affectedMaps"],
                    "source": {
                        "runtimeConcepts": ["map_load_boulder_reset", "clear_boulder_positions"],
                        "notes": [
                            "The source label resets Victory Road boulder switch/hole flags during map-load state handling.",
                            "Downstream runtimes should clear persistent pushed-boulder positions with the same server-side map-load effect.",
                        ],
                    },
                },
            }
        )
    return diagnostics


def champion_hall_of_fame_runtime_diagnostics():
    specs = [
        {
            "mapName": "ChampionsRoom",
            "scriptLabel": "ChampionsRoomPlayerEntersScript",
            "runtimeConcepts": ["champion_rival_intro", "forced_player_movement"],
        },
        {
            "mapName": "ChampionsRoom",
            "scriptLabel": "ChampionsRoomRivalText",
            "runtimeConcepts": ["champion_rival_intro", "starter_specific_trainer_battle"],
        },
        {
            "mapName": "ChampionsRoom",
            "scriptLabel": "ChampionsRoomRivalDefeatedScript",
            "runtimeConcepts": ["champion_victory_sequence", "post_battle_dialogue"],
        },
        {
            "mapName": "ChampionsRoom",
            "scriptLabel": "ChampionsRoomOakArrivesScript",
            "runtimeConcepts": ["champion_victory_sequence", "show_oak_actor", "scripted_movement"],
        },
        {
            "mapName": "ChampionsRoom",
            "scriptLabel": "ChampionsRoomOakComeWithMeScript",
            "runtimeConcepts": ["champion_victory_sequence", "scripted_movement"],
        },
        {
            "mapName": "ChampionsRoom",
            "scriptLabel": "ChampionsRoomOakExitsScript",
            "runtimeConcepts": ["champion_victory_sequence", "hide_oak_actor"],
        },
        {
            "mapName": "ChampionsRoom",
            "scriptLabel": "ChampionsRoomPlayerFollowsOakScript",
            "runtimeConcepts": ["champion_victory_sequence", "warp_to_hall_of_fame"],
        },
        {
            "mapName": "HallOfFame",
            "scriptLabel": "HallOfFameDefaultScript",
            "runtimeConcepts": ["hall_of_fame_intro_movement"],
        },
        {
            "mapName": "HallOfFame",
            "scriptLabel": "HallOfFameOakCongratulationsScript",
            "runtimeConcepts": ["hall_of_fame_congratulations", "cerulean_cave_unlock"],
        },
    ]
    diagnostics = []
    for spec in specs:
        diagnostics.append(
            {
                "mapName": spec["mapName"],
                "scriptLabel": spec["scriptLabel"],
                "status": "covered",
                "reason": "champion_hall_of_fame_runtime_v1",
                "details": {
                    "source": {
                        "runtimeConcepts": spec["runtimeConcepts"],
                        "notes": [
                            "The Champion and Hall of Fame finale requires a coordinated runtime state machine.",
                            "These source labels are state-machine fragments of that finale, not independent reusable runtime events.",
                        ],
                    },
                },
            }
        )
    return diagnostics


def oak_intro_runtime_diagnostics():
    specs = [
        {
            "mapName": "PalletTown",
            "scriptLabel": "PalletTownDefaultScript",
            "runtimeConcepts": ["oak_blocks_north_exit", "oak_intro_start_flag"],
        },
        {
            "mapName": "OaksLab",
            "scriptLabel": "OaksLab_Script",
            "runtimeConcepts": ["oak_lab_state_dispatch", "post_pokedex_text_pointer_state"],
        },
        {
            "mapName": "OaksLab",
            "scriptLabel": "OaksLabRivalText",
            "runtimeConcepts": ["rival_flag_gated_intro_dialogue", "starter_choice_dialogue", "post_starter_rival_dialogue"],
        },
        {
            "mapName": "PalletTown",
            "scriptLabel": "PalletTownOakHeyWaitScript",
            "runtimeConcepts": ["oak_appears", "oak_warning_dialogue"],
        },
        {
            "mapName": "PalletTown",
            "scriptLabel": "PalletTownOakWalksToPlayerScript",
            "runtimeConcepts": ["oak_approaches_player", "forced_walk_to_lab"],
        },
        {
            "mapName": "OaksLab",
            "scriptLabel": "OaksLabDefaultScript",
            "runtimeConcepts": ["oak_lab_intro", "oak_actor_visibility"],
        },
        {
            "mapName": "OaksLab",
            "scriptLabel": "OaksLabOakEntersLabScript",
            "runtimeConcepts": ["oak_lab_intro", "oak_movement"],
        },
        {
            "mapName": "OaksLab",
            "scriptLabel": "OaksLabHideShowOaksScript",
            "runtimeConcepts": ["oak_lab_intro", "oak_actor_visibility"],
        },
        {
            "mapName": "OaksLab",
            "scriptLabel": "OaksLabPlayerEntersLabScript",
            "runtimeConcepts": ["oak_lab_intro", "forced_player_movement"],
        },
        {
            "mapName": "OaksLab",
            "scriptLabel": "OaksLabFollowedOakScript",
            "runtimeConcepts": ["oak_lab_intro", "followed_oak_flags"],
        },
        {
            "mapName": "OaksLab",
            "scriptLabel": "OaksLabOakChooseMonSpeechScript",
            "runtimeConcepts": ["oak_lab_intro", "starter_choice_unlocked"],
        },
        {
            "mapName": "OaksLab",
            "scriptLabel": "OaksLabMonChoiceMenu",
            "runtimeConcepts": ["starter_choice_prompt"],
        },
        {
            "mapName": "OaksLab",
            "scriptLabel": "OaksLabSelectedPokeBallScript",
            "runtimeConcepts": ["starter_choice_gate"],
        },
        {
            "mapName": "OaksLab",
            "scriptLabel": "OaksLabRivalChoosesStarterScript",
            "runtimeConcepts": ["rival_starter_choice"],
        },
        {
            "mapName": "OaksLab",
            "scriptLabel": "OaksLabRivalChallengesPlayerScript",
            "runtimeConcepts": ["first_rival_battle"],
        },
        {
            "mapName": "OaksLab",
            "scriptLabel": "OaksLabRivalEndBattleScript",
            "runtimeConcepts": ["first_rival_battle_completion"],
        },
        {
            "mapName": "OaksLab",
            "scriptLabel": "OaksLabPlayerWatchRivalExitScript",
            "runtimeConcepts": ["rival_exit_after_first_battle"],
        },
        {
            "mapName": "OaksLab",
            "scriptLabel": "OaksLabRivalArrivesAtOaksRequestScript",
            "runtimeConcepts": ["pokedex_delivery_rival_return"],
        },
        {
            "mapName": "OaksLab",
            "scriptLabel": "OaksLabOakGivesPokedexScript",
            "runtimeConcepts": ["pokedex_delivery", "parcel_turn_in", "route22_rival_setup"],
        },
        {
            "mapName": "OaksLab",
            "scriptLabel": "OaksLabRivalLeavesWithPokedexScript",
            "runtimeConcepts": ["pokedex_delivery_rival_exit", "route22_rival_setup"],
        },
        {
            "mapName": "OaksLab",
            "scriptLabel": "OaksLabOak1Text",
            "runtimeConcepts": ["oak_parcel_pokedex_dialogue", "oak_free_pokeballs", "oak_pokedex_progress_dialogue"],
        },
    ]
    diagnostics = []
    for spec in specs:
        diagnostics.append(
            {
                "mapName": spec["mapName"],
                "scriptLabel": spec["scriptLabel"],
                "status": "covered",
                "reason": "oak_intro_runtime_v1",
                "details": {
                    "source": {
                        "runtimeConcepts": spec["runtimeConcepts"],
                        "notes": [
                            "The Oak intro, starter choice, first rival battle, and Pokedex delivery require coordinated runtime state machines.",
                            "These source labels are state-machine fragments that span map transitions, forced movement, starter choice, rival battle setup, and object visibility.",
                        ],
                    },
                },
            }
        )
    return diagnostics


def authored_runtime_diagnostics():
    """Describe source state machines that require downstream runtime support."""
    specs = [
        {
            "mapName": "MtMoonB2F",
            "scriptLabel": "MtMoonB2F_Script",
            "runtimeConcepts": [
                "fossil_area_prompt",
                "fossil_choice",
                "post_super_nerd_wild_encounter_suppression",
            ],
            "notes": [
                "The Mt. Moon fossil choice spans several source labels and requires coordinated runtime state.",
                "The outer map script also toggles no-battle status inside the fossil area after the Super Nerd is beaten; this belongs in server-authoritative encounter suppression rather than a standalone cutscene JSON file.",
            ],
        },
        {
            "mapName": "PewterCity",
            "scriptLabel": "PewterCityDefaultScript",
            "runtimeConcepts": ["pre_brock_east_exit_block", "gym_guide_escort", "museum_ticket_reset"],
            "notes": [
                "The generated Pewter City gym-guide candidate covers the east-exit coordinate check called by the default script.",
                "The museum ticket reset is a map/session bookkeeping side effect, not a separate player-facing script event.",
            ],
        },
        {
            "mapName": "VermilionCity",
            "scriptLabel": "VermilionCityLeftSSAnneCallbackScript",
            "runtimeConcepts": ["ss_anne_departure_callback", "post_ship_guard_state"],
            "notes": [
                "The S.S. Anne departure requires a coordinated runtime flow and guard state.",
                "This source callback is the map-load latch that hands control to the visible departure flow after EVENT_SS_ANNE_LEFT is set.",
            ],
        },
        {
            "mapName": "VermilionDock",
            "scriptLabel": "VermilionDock_Script",
            "runtimeConcepts": ["ss_anne_departure", "dock_exit_walkout", "ship_departed_flag"],
            "notes": [
                "A downstream runtime should set EVENT_SS_ANNE_LEFT during the departure flow after HM01 is obtained.",
                "The original dock scroll/smoke animation is presentation-specific and should be added as a renderer effect, not generic generated script JSON.",
            ],
        },
    ]
    diagnostics = []
    for spec in specs:
        diagnostics.append(
            {
                "mapName": spec["mapName"],
                "scriptLabel": spec["scriptLabel"],
                "status": "covered",
                "reason": "authored_runtime_coverage_v1",
                "details": {
                    "source": {
                        "runtimeConcepts": spec["runtimeConcepts"],
                        "notes": spec["notes"],
                    },
                },
            }
        )
    return diagnostics


def pallet_daisy_map_load_runtime_diagnostics():
    return [
        {
            "mapName": "PalletTown",
            "scriptLabel": "PalletTownDaisyScript",
            "status": "covered",
            "reason": "pallet_daisy_map_load_runtime_v1",
            "details": {
                "source": {
                    "runtimeConcepts": [
                        "map_load_visibility_sync",
                        "multi_flag_condition",
                        "post_pokeballs_progress_flag",
                    ],
                    "notes": [
                        "The source script has two independent Pallet map-load side effects: Daisy's sitting/walking object swap after Town Map + Blue's House, and a post-Oak-Pokeballs progress flag.",
                        "Downstream runtimes should apply these as server-authoritative map-load state effects rather than competing single-cutscene map_script candidates.",
                    ],
                },
            },
        }
    ]


def pokemon_tower7f_rocket_exit_runtime_diagnostics():
    return [
        {
            "mapName": "PokemonTower7F",
            "scriptLabel": "PokemonTower7FNPCCoordMovementTable",
            "status": "covered",
            "reason": "pokemon_tower7f_rocket_exit_runtime_v1",
            "details": {
                "source": {
                    "runtimeConcepts": [
                        "trainer_post_win_cleanup",
                        "coordinate_dependent_npc_exit_movement",
                        "hide_defeated_trainer_object",
                    ],
                    "coveredLabels": [
                        "PokemonTower7FEndBattleScript",
                        "PokemonTower7FHideNPCScript",
                        "PokemonTower7FRocketLeaveMovementScript",
                        "PokemonTower7FNPCCoordMovementTable",
                    ],
                    "notes": [
                        "The source movement table is keyed by the Rocket sprite index and the player's battle tile.",
                        "Downstream runtimes should attach these movements and object hide cleanup to the standard trainer battle post-win actions instead of generating duplicate NPC-click scripts.",
                    ],
                },
            },
        }
    ]


def cinnabar_gym_default_runtime_diagnostics():
    return [
        {
            "mapName": "CinnabarGym",
            "scriptLabel": "CinnabarGymDefaultScript",
            "status": "covered",
            "reason": "cinnabar_gym_quiz_trainer_runtime_v1",
            "details": {
                "source": {
                    "runtimeConcepts": [
                        "quiz_wrong_answer_trainer_handoff",
                        "trainer_pre_battle_movement",
                        "gym_gate_unlock_runtime",
                    ],
                    "coveredLabels": [
                        "CinnabarGymDefaultScript",
                        "MovementNpcToLeftAndUp",
                        "MovementNpcToLeft",
                        "CinnabarGymGetOpponentTextScript",
                        "CinnabarGymOpenGateScript",
                    ],
                    "notes": [
                        "The source default script consumes wOpponentAfterWrongAnswer, nudges the selected trainer, then hands off to the trainer text and gate-open scripts.",
                        "Downstream runtimes should keep this as part of the Cinnabar Gym quiz/trainer/gate state machine; generated trainer text candidates and event tile overrides own the persistent battle/gate effects.",
                    ],
                },
            },
        }
    ]


def name_rater_runtime_diagnostics():
    return [
        {
            "mapName": "NameRatersHouse",
            "scriptLabel": "NameRatersHouseYesNoScript",
            "status": "covered",
            "reason": "name_rater_runtime_v1",
            "details": {
                "source": {
                    "runtimeConcepts": [
                        "party_selection_ui",
                        "pokemon_original_trainer_validation",
                        "pokemon_nickname_editing",
                        "multi_step_yes_no_flow",
                    ],
                    "coveredLabels": [
                        "NameRatersHouseYesNoScript",
                        "NameRatersHouseCheckMonOTScript",
                        "NameRatersHouseNameRaterText",
                    ],
                    "notes": [
                        "The shared source yes/no helper is only meaningful inside the Name Rater's party-picker and nickname-entry state machine.",
                        "Downstream runtimes should implement this as a dedicated Pokémon party/nickname UI flow instead of flattening it into linear cutscene JSON.",
                    ],
                },
            },
        }
    ]


def parse_rle_movement(block_raw):
    movements = []
    for direction, count in re.findall(r"\bdb\s+D_(UP|DOWN|LEFT|RIGHT),\s+(\$[0-9a-fA-F]+|\d+)", block_raw):
        repeat = asm_literal_to_int(count)
        movements.extend([direction] * repeat)
    return movements


def lances_room_default_candidates():
    map_name = "LancesRoom"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    text_labels = extract_map_text_labels(map_name)
    map_id = source_map_id(map_name)
    required = [
        "LancesRoomDefaultScript",
        "LanceTriggerMovementCoords",
        "WalkToLance",
        "WalkToLance_RLEList",
    ]
    if not map_id or any(label not in blocks_by_label for label in required):
        return []

    trigger_coords = parse_coord_array(script_content, "LanceTriggerMovementCoords")
    expected_coords = [
        {"x": 5, "y": 1},
        {"x": 6, "y": 2},
        {"x": 5, "y": 11},
        {"x": 6, "y": 11},
        {"x": 24, "y": 16},
    ]
    if trigger_coords != expected_coords:
        return []

    before_battle = text_labels.get("LancesRoomLanceBeforeBattleText", [])
    if not before_battle:
        return []
    walk_to_lance = parse_rle_movement(blocks_by_label["WalkToLance_RLEList"]["raw"])
    if walk_to_lance != (["UP"] * 12 + ["LEFT"] * 12 + ["DOWN"] * 7 + ["LEFT"] * 6):
        return []

    source = source_metadata(
        map_name,
        "lances_room_default_v1",
        script_path,
        text_path,
        [
            "Generated from Lance room's default coordinate state machine.",
            "The source reuses one coordinate table for three branches; generated candidates split it into distinct coordinate labels so downstream runtimes can resolve each branch precisely.",
        ],
    )
    covered_labels = [
        "LancesRoomDefaultScript",
        "LanceTriggerMovementCoords",
        "WalkToLance",
        "WalkToLance_RLEList",
    ]

    def coord(map_coord):
        return {"mapName": map_name, "mapId": map_id, **map_coord}

    battle_source = {**source}
    battle_source["coveredLabels"] = covered_labels
    lock_source = {**source}
    lock_source["coveredLabels"] = covered_labels
    walk_source = {**source}
    walk_source["coveredLabels"] = covered_labels

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "LancesRoomLanceCoordBattle",
            "trigger": {
                "type": "coord",
                "label": "LanceBattleCoords",
                "sourceLabel": "LanceTriggerMovementCoords",
                "coordinates": [coord(trigger_coords[0]), coord(trigger_coords[1])],
            },
            "conditions": {
                "requiresEventsAbsent": [
                    "EVENT_BEAT_LANCES_ROOM_TRAINER_0",
                    "EVENT_BEAT_LANCE",
                ],
            },
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "speaker": "LANCE", "lines": before_battle},
                {
                    "type": "startTrainerBattle",
                    "trainerClass": "LANCE",
                    "trainerName": "LANCE",
                    "partyIndex": 1,
                    "winFlag": "EVENT_BEAT_LANCES_ROOM_TRAINER_0",
                },
                {"type": "unlockInput"},
            ],
            "source": battle_source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "LancesRoomEntranceLock",
            "trigger": {
                "type": "coord",
                "label": "LanceEntranceLockCoords",
                "sourceLabel": "LanceTriggerMovementCoords",
                "coordinates": [coord(trigger_coords[2]), coord(trigger_coords[3])],
            },
            "conditions": {
                "requiresEventsAbsent": [
                    "EVENT_LANCES_ROOM_LOCK_DOOR",
                    "EVENT_BEAT_LANCE",
                ],
            },
            "actions": [
                {"type": "setEvent", "event": "EVENT_LANCES_ROOM_LOCK_DOOR"},
            ],
            "source": lock_source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "LancesRoomWalkToLance",
            "trigger": {
                "type": "coord",
                "label": "LanceWalkToLanceCoords",
                "sourceLabel": "LanceTriggerMovementCoords",
                "coordinates": [coord(trigger_coords[4])],
            },
            "conditions": {"requiresEventAbsent": "EVENT_BEAT_LANCE"},
            "actions": [
                {"type": "lockInput"},
                {"type": "movePlayer", "movements": walk_to_lance},
                {"type": "unlockInput"},
            ],
            "source": walk_source,
            "confidence": "adapter",
        },
    ]


def pewter_city_escort_candidates():
    map_name = "PewterCity"
    script_path = SCRIPTS_DIR / f"{map_name}.asm"
    text_path = TEXT_DIR / f"{map_name}.asm"
    if not script_path.exists() or not text_path.exists():
        return []

    script_content = script_path.read_text()
    blocks_by_label = {block["label"]: block for block in extract_label_blocks(script_content)}
    text_pointers = parse_text_pointer_map(script_content)
    text_labels = extract_map_text_labels(map_name)
    map_id = source_map_id(map_name)
    if not map_id:
        return []

    required = [
        "PewterCityCheckPlayerLeavingEastScript",
        "PewterCitySuperNerd1Text",
        "PewterCitySuperNerd1ShowsPlayerMuseumScript",
        "PewterCityHideSuperNerd1Script",
        "PewterCityResetSuperNerd1Script",
        "PewterCityYoungsterShowsPlayerGymScript",
        "PewterCityHideYoungsterScript",
        "PewterCityResetYoungsterScript",
        "MovementData_PewterMuseumGuyExit",
        "MovementData_PewterGymGuyExit",
    ]
    if any(label not in blocks_by_label for label in required):
        return []

    east_coords = parse_coord_array(script_content, "PewterCityPlayerLeavingEastCoords")
    if east_coords != [{"x": 35, "y": 17}, {"x": 36, "y": 17}, {"x": 37, "y": 18}, {"x": 37, "y": 19}]:
        return []

    museum_refs = local_text_ref_map(blocks_by_label["PewterCitySuperNerd1Text"]["raw"])
    museum_prompt = script_label_lines(text_labels, museum_refs, ".DidYouCheckOutMuseumText")
    museum_yes = script_label_lines(text_labels, museum_refs, ".WerentThoseFossilsAmazingText")
    museum_no = script_label_lines(text_labels, museum_refs, ".YouHaveToGoText")
    museum_arrival = lines_for_text_constant(
        "TEXT_PEWTERCITY_SUPER_NERD1_ITS_RIGHT_HERE",
        text_pointers,
        blocks_by_label,
        text_labels,
    )
    gym_intro = lines_for_text_constant(
        "TEXT_PEWTERCITY_YOUNGSTER",
        text_pointers,
        blocks_by_label,
        text_labels,
    )
    gym_arrival = lines_for_text_constant(
        "TEXT_PEWTERCITY_YOUNGSTER_GO_TAKE_ON_BROCK",
        text_pointers,
        blocks_by_label,
        text_labels,
    )
    if not all([museum_prompt, museum_yes, museum_no, museum_arrival, gym_intro, gym_arrival]):
        return []

    museum_movement = parse_npc_movements(blocks_by_label["MovementData_PewterMuseumGuyExit"]["raw"])
    gym_movement = parse_npc_movements(blocks_by_label["MovementData_PewterGymGuyExit"]["raw"])
    if museum_movement != ["DOWN", "DOWN", "DOWN", "DOWN"]:
        return []
    if gym_movement != ["RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT"]:
        return []

    source = source_metadata(
        map_name,
        "pewter_city_escort_v1",
        script_path,
        text_path,
        [
            "Generated from Pewter City's museum and pre-Brock escort state-machine scripts.",
            "The source temporarily hides and restores the escort NPC missable objects after scripted movement; the candidate models those side effects directly.",
        ],
    )
    museum_source = {**source}
    museum_source["coveredLabels"] = [
        "PewterCitySuperNerd1Text",
        "PewterCitySuperNerd1ShowsPlayerMuseumScript",
        "PewterCityHideSuperNerd1Script",
        "PewterCityResetSuperNerd1Script",
        "MovementData_PewterMuseumGuyExit",
    ]
    gym_source = {**source}
    gym_source["coveredLabels"] = [
        "PewterCityCheckPlayerLeavingEastScript",
        "PewterCityYoungsterShowsPlayerGymScript",
        "PewterCityHideYoungsterScript",
        "PewterCityResetYoungsterScript",
        "MovementData_PewterGymGuyExit",
    ]

    return [
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "PewterCitySuperNerd1MuseumGuide",
            "trigger": {
                "type": "npc_click",
                "label": "TEXT_PEWTERCITY_SUPER_NERD1",
                "sourceLabel": "PewterCitySuperNerd1Text",
            },
            "actions": [
                {"type": "lockInput"},
                {
                    "type": "choice",
                    "speaker": "SUPER NERD",
                    "promptLines": museum_prompt,
                    "yesLines": museum_yes,
                    "noLines": museum_no,
                    "stopOnYes": True,
                    "continueOnNo": True,
                },
                {"type": "move", "actor": "SUPER_NERD", "movements": museum_movement},
                {"type": "dialogue", "speaker": "SUPER NERD", "lines": museum_arrival},
                {"type": "hideObject", "objectKey": "HS_MUSEUM_GUY"},
                {"type": "showObject", "objectKey": "HS_MUSEUM_GUY"},
                {"type": "unlockInput"},
            ],
            "source": museum_source,
            "confidence": "adapter",
        },
        {
            "version": 1,
            "kind": "scriptEventCandidate",
            "mapName": map_name,
            "scriptLabel": "PewterCityYoungsterGymGuide",
            "trigger": {
                "type": "coord",
                "label": "PewterCityPlayerLeavingEastCoords",
                "sourceLabel": "PewterCityCheckPlayerLeavingEastScript",
                "coordinates": [{"mapName": map_name, "mapId": map_id, **coord} for coord in east_coords],
            },
            "conditions": {"requiresEventAbsent": "EVENT_BEAT_BROCK"},
            "actions": [
                {"type": "lockInput"},
                {"type": "dialogue", "speaker": "YOUNGSTER", "lines": gym_intro},
                {"type": "move", "actor": "YOUNGSTER", "movements": gym_movement},
                {"type": "dialogue", "speaker": "YOUNGSTER", "lines": gym_arrival},
                {"type": "hideObject", "objectKey": "HS_GYM_GUY"},
                {"type": "showObject", "objectKey": "HS_GYM_GUY"},
                {"type": "unlockInput"},
            ],
            "source": gym_source,
            "confidence": "adapter",
        },
    ]


ADAPTERS = [
    safari_zone_gate_candidates,
    static_wild_battle_candidates,
    snorlax_wake_battle_candidates,
    pokemon_tower_marowak_ghost_candidate,
    viridian_old_man_catch_tutorial_candidate,
    lances_room_default_candidates,
    pewter_city_escort_candidates,
    simple_yes_no_dialogue_candidates,
    pokemon_mansion_switch_candidates,
    fuchsia_fossil_sign_candidates,
    flag_gated_dialogue_candidates,
    badge_gated_gym_guide_candidates,
    badge_or_event_gated_dialogue_candidates,
    facing_up_dialogue_candidates,
    simple_play_cry_text_candidates,
    fan_boast_toggle_candidates,
    simple_flag_side_effect_dialogue_candidates,
    pure_flag_map_script_candidates,
    conditional_flag_map_script_candidates,
    one_shot_object_visibility_map_script_candidates,
    fishing_guru_rod_candidates,
    pokemon_fan_club_chairman_candidates,
    fighting_dojo_reward_candidates,
    fighting_dojo_karate_master_candidates,
    bills_house_cell_separator_candidates,
    route25_bill_visibility_candidates,
    mt_moon_fossil_choice_candidates,
    celadon_roof_drink_trade_candidates,
    paid_choice_candidates,
    viridian_city_progress_blocker_candidates,
    vermilion_ss_anne_guard_candidates,
    elite_four_room_entrance_guard_candidates,
    game_corner_coin_purchase_candidates,
    game_corner_prize_vendor_candidates,
    silph_co_9f_nurse_candidates,
    game_corner_npc_coin_gift_candidates,
    cinnabar_gym_trainer_text_candidates,
    trainer_after_battle_object_drop_candidates,
    route23_badge_gate_candidates,
    cinnabar_lab_fossil_revival_candidates,
    gym_leader_tm_reward_candidates,
    gym_leader_battle_text_candidates,
    cinnabar_gym_map_load_reset_candidate,
    indigo_plateau_lobby_map_load_reset_candidate,
    cerulean_city_rival_candidates,
    route22_rival_candidates,
    silph_co_7f_rival_candidates,
    pokemon_tower_2f_rival_candidates,
    pokemon_tower_5f_purified_zone_candidate,
    ss_anne_2f_rival_candidate,
    silph_co_6f_giovanni_dialogue_candidates,
    silph_co_11f_giovanni_candidate,
    pokemon_tower_7f_mr_fuji_rescue_candidate,
    game_corner_rocket_defeated_candidate,
    rocket_hideout_b4f_giovanni_candidate,
    rocket_hideout_door_unlock_candidates,
    story_item_reward_candidates,
    rocket_reward_battle_candidates,
    trainer_after_battle_flag_side_effect_candidates,
    simple_item_gift_candidates,
    oaks_aide_candidates,
    simple_pokemon_gift_candidates,
]

TILE_OVERRIDE_ADAPTERS = [
    elite_four_exit_tile_override_candidates,
    lance_room_entrance_tile_override_candidates,
    pokemon_mansion_switch_tile_override_candidates,
    silph_card_key_tile_override_candidates,
    victory_road_boulder_tile_override_candidates,
    vermilion_gym_trash_door_tile_override_candidates,
    game_corner_rocket_hideout_tile_override_candidates,
    rocket_hideout_door_tile_override_candidates,
]


def insert_candidate(cursor, candidate, map_resolver):
    encoded = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
    map_id = map_resolver.resolve(candidate["mapName"])
    cursor.execute(
        """
        INSERT INTO script_event_candidates
            (map_name, map_id, script_label, trigger_type, trigger_label,
             confidence, candidate_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate["mapName"],
            map_id,
            candidate["scriptLabel"],
            candidate["trigger"]["type"],
            candidate["trigger"]["label"],
            candidate["confidence"],
            encoded,
        ),
    )
    candidate_id = cursor.lastrowid

    for action_index, action in enumerate(candidate.get("actions", [])):
        cursor.execute(
            """
            INSERT INTO script_event_candidate_actions
                (candidate_id, action_index, action_type, action_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                candidate_id,
                action_index,
                action.get("type", "unknown"),
                canonical_json(action),
            ),
        )

    for condition_path, value_index, value in normalized_condition_rows(
        candidate.get("conditions", {})
    ):
        cursor.execute(
            """
            INSERT INTO script_event_candidate_conditions
                (candidate_id, condition_path, value_index, condition_value_json)
            VALUES (?, ?, ?, ?)
            """,
            (candidate_id, condition_path, value_index, canonical_json(value)),
        )

    for reference_kind, json_path, reference_index, value in candidate_reference_rows(
        candidate
    ):
        cursor.execute(
            """
            INSERT INTO script_event_candidate_references
                (candidate_id, reference_kind, json_path, reference_index,
                 reference_value_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                reference_kind,
                json_path,
                reference_index,
                canonical_json(value),
            ),
        )

    if candidate["trigger"]["type"] == "coord":
        cursor.execute(
            "DELETE FROM coordinate_triggers WHERE map_name = ? AND label = ?",
            (candidate["mapName"], candidate["trigger"]["label"]),
        )
        for coord in candidate["trigger"].get("coordinates", []):
            cursor.execute(
                """INSERT INTO coordinate_triggers
                   (map_name, map_id, label, x, y) VALUES (?, ?, ?, ?, ?)""",
                (
                    candidate["mapName"],
                    map_id,
                    candidate["trigger"]["label"],
                    coord["x"],
                    coord["y"],
                ),
            )


def insert_ir_block(cursor, block, map_resolver):
    map_id = map_resolver.resolve(block["mapName"])
    cursor.execute(
        """
        INSERT INTO script_event_ir_blocks (
            map_name, map_id, label, kind, features_json, text_refs_json,
            event_refs_json, item_refs_json, pokemon_refs_json, movement_refs_json,
            object_refs_json, battle_refs_json, warp_refs_json, raw_asm
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            block["mapName"],
            map_id,
            block["label"],
            block["kind"],
            canonical_json(block["features"]),
            canonical_json(block["textRefs"]),
            canonical_json(block["eventRefs"]),
            canonical_json(block["itemRefs"]),
            canonical_json(block["pokemonRefs"]),
            canonical_json(block["movementRefs"]),
            canonical_json(block["objectRefs"]),
            canonical_json(block["battleRefs"]),
            canonical_json(block["warpRefs"]),
            block["rawAsm"],
        ),
    )
    ir_block_id = cursor.lastrowid
    for reference_kind, field_name in (
        ("text", "textRefs"),
        ("event", "eventRefs"),
        ("item", "itemRefs"),
        ("pokemon", "pokemonRefs"),
        ("movement", "movementRefs"),
        ("object", "objectRefs"),
        ("battle", "battleRefs"),
        ("warp", "warpRefs"),
    ):
        for reference_index, value in enumerate(block.get(field_name, [])):
            cursor.execute(
                """
                INSERT INTO script_event_ir_references
                    (ir_block_id, reference_kind, reference_index,
                     reference_value_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    ir_block_id,
                    reference_kind,
                    reference_index,
                    canonical_json(value),
                ),
            )


def insert_in_game_trade(cursor, trade):
    cursor.execute(
        """
        INSERT INTO script_event_in_game_trades (
            trade_key, map_name, script_label, text_constant,
            requested_pokemon, offered_pokemon, offered_nickname,
            dialogue_set, original_trade_index, active, source_file
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade["tradeKey"],
            trade.get("mapName", ""),
            trade.get("scriptLabel", ""),
            trade.get("textConstant", ""),
            trade["requestedPokemon"],
            trade["offeredPokemon"],
            trade["offeredNickname"],
            trade["dialogueSet"],
            trade["originalTradeIndex"],
            1 if trade.get("active") else 0,
            trade["sourceFile"],
        ),
    )


def insert_tile_override_candidate(cursor, candidate):
    cursor.execute(
        """
        INSERT INTO script_event_tile_overrides (
            map_name, script_label, candidate_json
        )
        VALUES (?, ?, ?)
        """,
        (
            candidate["mapName"],
            candidate["scriptLabel"],
            canonical_json(candidate),
        ),
    )


def insert_conditional_dialogue(cursor, row):
    cursor.execute(
        """
        INSERT INTO script_event_conditional_dialogue (
            text_constant, map_name, script_label, priority,
            requires_flags_json, requires_flags_absent_json,
            dialogue_labels_json, source_json, row_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["textConstant"],
            row["mapName"],
            row["scriptLabel"],
            row["priority"],
            canonical_json(row["conditions"].get("requiresEvents", [])),
            canonical_json(row["conditions"].get("requiresEventsAbsent", [])),
            canonical_json(row.get("dialogueLabels", [])),
            canonical_json(row.get("source", {})),
            canonical_json(row),
        ),
    )


def insert_boulder_target(cursor, target):
    cursor.execute(
        """
        INSERT INTO script_event_boulder_targets (
            target_family, map_name, source_label, x, y, flag,
            drops_through_hole, source_missable_object, destination_map_name,
            destination_missable_object, source_file, target_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            target["targetFamily"],
            target["mapName"],
            target["sourceLabel"],
            target["x"],
            target["y"],
            target["flag"],
            1 if target.get("dropsThroughHole") else 0,
            target.get("sourceMissableObject", ""),
            target.get("destinationMapName", ""),
            target.get("destinationMissableObject", ""),
            target["sourceFile"],
            canonical_json(target),
        ),
    )


def insert_object_visibility_rule(cursor, rule):
    cursor.execute(
        """
        INSERT INTO script_event_object_visibility (
            map_name, map_id, object_name, object_key, script_label,
            requires_event, visible, label, rule_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rule["mapName"],
            rule["mapId"],
            rule["objectName"],
            rule["objectKey"],
            rule["scriptLabel"],
            rule["requiresEvent"],
            1 if rule["visible"] else 0,
            rule["label"],
            canonical_json(rule),
        ),
    )


def insert_diagnostic(cursor, diagnostic, map_resolver):
    map_id = map_resolver.resolve(diagnostic["mapName"], allow_global=True)
    cursor.execute(
        """
        INSERT INTO script_event_candidate_diagnostics
            (map_name, map_id, script_label, status, reason, details_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            diagnostic["mapName"],
            map_id,
            diagnostic["scriptLabel"],
            diagnostic["status"],
            diagnostic["reason"],
            canonical_json(diagnostic["details"]),
        ),
    )


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def normalized_condition_rows(conditions):
    """Flatten condition leaves without discarding their JSON type or order."""
    rows = []

    def visit(value, path):
        if isinstance(value, dict):
            for key in sorted(value):
                visit(value[key], f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            if not value:
                rows.append((path, 0, []))
            else:
                for index, item in enumerate(value):
                    if isinstance(item, (dict, list)):
                        visit(item, f"{path}[{index}]")
                    else:
                        rows.append((path, index, item))
        else:
            rows.append((path or "$", 0, value))

    visit(conditions, "")
    return rows


REFERENCE_KEYS = {
    "event": {
        "event", "events", "flag", "flags", "requiresevent",
        "requiresevents", "requireseventabsent", "requireseventsabsent",
        "completionevent", "winevent",
    },
    "item": {"item", "itemid", "itemname", "requireditem"},
    "pokemon": {"pokemon", "pokemonid", "species", "requestedpokemon", "offeredpokemon"},
    "movement": {"movement", "movementlabel", "movementsequence"},
    "object": {"object", "objectkey", "objectname", "missableobject"},
    "map": {"map", "mapid", "mapname", "destinationmap", "destinationmapname"},
    "script": {"script", "scriptlabel", "sourcelabel", "label"},
    "text": {"text", "textconstant", "textlabel", "dialoguelabel", "dialoguelabels"},
    "battle": {"battle", "trainer", "trainerclass", "trainerid"},
    "warp": {"warp", "warpid", "destinationwarp", "destinationwarpid"},
}


def candidate_reference_rows(candidate):
    """Extract typed gameplay references from the neutral candidate structure."""
    key_to_kind = {
        key: kind for kind, keys in REFERENCE_KEYS.items() for key in keys
    }
    rows = []

    def emit(kind, path, value):
        values = value if isinstance(value, list) else [value]
        for index, item in enumerate(values):
            if isinstance(item, (str, int)) and not isinstance(item, bool):
                rows.append((kind, path, index, item))

    def visit(value, path, *, include=True):
        if isinstance(value, dict):
            for key in sorted(value):
                child = value[key]
                child_path = f"{path}.{key}" if path else key
                normalized_key = re.sub(r"[^a-z0-9]", "", key.lower())
                kind = key_to_kind.get(normalized_key)
                if kind:
                    emit(kind, child_path, child)
                visit(child, child_path, include=include)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]", include=include)

    # Source metadata is provenance rather than a gameplay reference.
    for section in ("trigger", "conditions", "actions"):
        visit(candidate.get(section, {}), section)
    return rows


def validate_normalized_script_tables(conn):
    """Prove the relational projections exactly cover their compatibility JSON."""
    required = {
        "script_event_candidate_actions",
        "script_event_candidate_conditions",
        "script_event_candidate_references",
        "script_event_ir_references",
    }
    present = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if missing := required - present:
        raise ValueError(f"Missing normalized script tables: {sorted(missing)}")

    for table in ("script_event_candidates", "script_event_ir_blocks"):
        unresolved = conn.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE map_id IS NULL'
        ).fetchone()[0]
        if unresolved:
            raise ValueError(f"{table} has {unresolved} unresolved map relationships")

    invalid_diagnostic_maps = conn.execute(
        """
        SELECT COUNT(*) FROM script_event_candidate_diagnostics
        WHERE (map_name = 'GLOBAL' AND map_id IS NOT NULL)
           OR (map_name <> 'GLOBAL' AND map_id IS NULL)
        """
    ).fetchone()[0]
    if invalid_diagnostic_maps:
        raise ValueError(
            "script_event_candidate_diagnostics has invalid global/map relationships"
        )

    for candidate_id, encoded in conn.execute(
        "SELECT id, candidate_json FROM script_event_candidates ORDER BY id"
    ):
        candidate = json.loads(encoded)
        expected_actions = [
            (index, action.get("type", "unknown"), canonical_json(action))
            for index, action in enumerate(candidate.get("actions", []))
        ]
        actual_actions = conn.execute(
            """
            SELECT action_index, action_type, action_json
            FROM script_event_candidate_actions
            WHERE candidate_id = ? ORDER BY action_index
            """,
            (candidate_id,),
        ).fetchall()
        if actual_actions != expected_actions:
            raise ValueError(f"Normalized action coverage mismatch for candidate {candidate_id}")

        expected_conditions = [
            (path, index, canonical_json(value))
            for path, index, value in normalized_condition_rows(
                candidate.get("conditions", {})
            )
        ]
        actual_conditions = conn.execute(
            """
            SELECT condition_path, value_index, condition_value_json
            FROM script_event_candidate_conditions
            WHERE candidate_id = ? ORDER BY condition_path, value_index
            """,
            (candidate_id,),
        ).fetchall()
        if actual_conditions != sorted(expected_conditions):
            raise ValueError(
                f"Normalized condition coverage mismatch for candidate {candidate_id}"
            )

        expected_references = sorted(
            (kind, path, index, canonical_json(value))
            for kind, path, index, value in candidate_reference_rows(candidate)
        )
        actual_references = conn.execute(
            """
            SELECT reference_kind, json_path, reference_index, reference_value_json
            FROM script_event_candidate_references
            WHERE candidate_id = ?
            ORDER BY reference_kind, json_path, reference_index
            """,
            (candidate_id,),
        ).fetchall()
        if actual_references != expected_references:
            raise ValueError(
                f"Normalized reference coverage mismatch for candidate {candidate_id}"
            )

    ir_fields = (
        ("text", "text_refs_json"),
        ("event", "event_refs_json"),
        ("item", "item_refs_json"),
        ("pokemon", "pokemon_refs_json"),
        ("movement", "movement_refs_json"),
        ("object", "object_refs_json"),
        ("battle", "battle_refs_json"),
        ("warp", "warp_refs_json"),
    )
    columns = ", ".join(column for _, column in ir_fields)
    for row in conn.execute(
        f"SELECT id, {columns} FROM script_event_ir_blocks ORDER BY id"
    ):
        ir_block_id, *encoded_fields = row
        expected = []
        for (kind, _), encoded_values in zip(ir_fields, encoded_fields):
            expected.extend(
                (kind, index, canonical_json(value))
                for index, value in enumerate(json.loads(encoded_values))
            )
        actual = conn.execute(
            """
            SELECT reference_kind, reference_index, reference_value_json
            FROM script_event_ir_references
            WHERE ir_block_id = ? ORDER BY reference_kind, reference_index
            """,
            (ir_block_id,),
        ).fetchall()
        if actual != sorted(expected):
            raise ValueError(
                f"Normalized IR reference coverage mismatch for block {ir_block_id}"
            )

    errors = []
    relationship_tables = required | {
        "script_event_candidates",
        "script_event_ir_blocks",
        "script_event_candidate_diagnostics",
    }
    for table in sorted(relationship_tables):
        errors.extend(conn.execute(f'PRAGMA foreign_key_check("{table}")').fetchall())
    if errors:
        raise ValueError(f"Normalized script foreign-key violations: {errors[:10]}")

    return {
        "actions": conn.execute(
            "SELECT COUNT(*) FROM script_event_candidate_actions"
        ).fetchone()[0],
        "conditions": conn.execute(
            "SELECT COUNT(*) FROM script_event_candidate_conditions"
        ).fetchone()[0],
        "candidateReferences": conn.execute(
            "SELECT COUNT(*) FROM script_event_candidate_references"
        ).fetchone()[0],
        "irReferences": conn.execute(
            "SELECT COUNT(*) FROM script_event_ir_references"
        ).fetchone()[0],
    }


def generated_candidate_diagnostic(candidate):
    return {
        "mapName": candidate["mapName"],
        "scriptLabel": candidate["scriptLabel"],
        "status": "generated",
        "reason": candidate.get("source", {}).get("adapter", "adapter"),
        "details": {
            "trigger": candidate.get("trigger", {}),
            "conditions": candidate.get("conditions", {}),
            "actions": [action.get("type") for action in candidate.get("actions", [])],
            "source": candidate.get("source", {}),
        },
    }


def generated_trade_diagnostic(trade):
    status = "generated" if trade.get("active") else "covered"
    reason = "in_game_trade_definition_v1" if trade.get("active") else "inactive_in_game_trade_definition_v1"
    return {
        "mapName": trade.get("mapName") or "GLOBAL",
        "scriptLabel": trade["tradeKey"],
        "status": status,
        "reason": reason,
        "details": {
            "tradeKey": trade["tradeKey"],
            "textConstant": trade.get("textConstant", ""),
            "scriptLabel": trade.get("scriptLabel", ""),
            "requestedPokemon": trade["requestedPokemon"],
            "offeredPokemon": trade["offeredPokemon"],
            "offeredNickname": trade["offeredNickname"],
            "dialogueSet": trade["dialogueSet"],
            "originalTradeIndex": trade["originalTradeIndex"],
            "active": bool(trade.get("active")),
            "sourceFile": trade["sourceFile"],
        },
    }


def generated_tile_override_diagnostic(candidate):
    return {
        "mapName": candidate["mapName"],
        "scriptLabel": candidate["scriptLabel"],
        "status": "generated",
        "reason": candidate.get("source", {}).get("adapter", "adapter"),
        "details": {
            "replacements": candidate.get("replacements", []),
            "source": candidate.get("source", {}),
        },
    }


def generated_object_visibility_diagnostic(rule):
    return {
        "mapName": rule["sourceMapName"],
        "scriptLabel": rule["scriptLabel"],
        "status": "generated",
        "reason": rule.get("source", {}).get("adapter", "object_visibility_adapter"),
        "details": {
            "mapName": rule["mapName"],
            "mapId": rule["mapId"],
            "objectName": rule["objectName"],
            "objectKey": rule["objectKey"],
            "visible": rule["visible"],
            "requiresEvent": rule["requiresEvent"],
            "label": rule["label"],
            "source": rule.get("source", {}),
        },
    }


def generated_conditional_dialogue_diagnostic(row):
    return {
        "mapName": row["mapName"],
        "scriptLabel": row["scriptLabel"],
        "status": "generated",
        "reason": row.get("source", {}).get("adapter", "conditional_dialogue_adapter"),
        "details": {
            "textConstant": row["textConstant"],
            "priority": row["priority"],
            "conditions": row.get("conditions", {}),
            "dialogueLabels": row.get("dialogueLabels", []),
            "source": row.get("source", {}),
        },
    }


def main(runtime_profile=None):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = create_tables(conn)
    map_resolver = CanonicalMapResolver.from_connection(conn)

    ir_blocks = extract_script_ir()
    trade_definitions = in_game_trade_definitions()
    candidates = []
    for adapter in ADAPTERS:
        candidates.extend(adapter())
    candidates = apply_candidate_profile(candidates, runtime_profile)
    tile_override_candidates = []
    for adapter in TILE_OVERRIDE_ADAPTERS:
        tile_override_candidates.extend(adapter())
    boulder_targets = victory_road_boulder_target_definitions()
    object_visibility_rules = object_visibility_rule_candidates(conn, ir_blocks)
    conditional_dialogue = conditional_dialogue_rows()

    for block in sorted(ir_blocks, key=lambda row: (row["mapName"], row["label"])):
        insert_ir_block(cursor, block, map_resolver)

    for candidate in sorted(candidates, key=lambda row: (row["mapName"], row["scriptLabel"])):
        insert_candidate(cursor, candidate, map_resolver)

    for trade in trade_definitions:
        insert_in_game_trade(cursor, trade)

    for candidate in sorted(tile_override_candidates, key=lambda row: (row["mapName"], row["scriptLabel"])):
        insert_tile_override_candidate(cursor, candidate)

    for row in sorted(conditional_dialogue, key=lambda row: (row["mapName"], row["textConstant"], -row["priority"], row["scriptLabel"])):
        insert_conditional_dialogue(cursor, row)

    for target in sorted(boulder_targets, key=lambda row: (row["targetFamily"], row["mapName"], row["x"], row["y"])):
        insert_boulder_target(cursor, target)

    for rule in sorted(object_visibility_rules, key=lambda row: (row["mapName"], row["objectName"], row["requiresEvent"], row["label"])):
        insert_object_visibility_rule(cursor, rule)

    diagnostics = [generated_candidate_diagnostic(candidate) for candidate in candidates]
    diagnostics.extend(generated_trade_diagnostic(trade) for trade in trade_definitions)
    diagnostics.extend(generated_tile_override_diagnostic(candidate) for candidate in tile_override_candidates)
    diagnostics.extend(generated_object_visibility_diagnostic(rule) for rule in object_visibility_rules)
    diagnostics.extend(generated_conditional_dialogue_diagnostic(row) for row in conditional_dialogue)
    boulder_diagnostics = boulder_target_runtime_diagnostics(boulder_targets)
    diagnostics.extend(boulder_diagnostics)
    spin_tile_diagnostics = spin_tile_runtime_diagnostics(conn)
    diagnostics.extend(spin_tile_diagnostics)
    trainer_flag_diagnostics = trainer_after_battle_flag_runtime_diagnostics()
    diagnostics.extend(trainer_flag_diagnostics)
    champion_hof_diagnostics = champion_hall_of_fame_runtime_diagnostics()
    diagnostics.extend(champion_hof_diagnostics)
    oak_intro_diagnostics = oak_intro_runtime_diagnostics()
    diagnostics.extend(oak_intro_diagnostics)
    authored_diagnostics = authored_runtime_diagnostics()
    diagnostics.extend(authored_diagnostics)
    pallet_daisy_diagnostics = pallet_daisy_map_load_runtime_diagnostics()
    diagnostics.extend(pallet_daisy_diagnostics)
    pokemon_tower7f_rocket_exit_diagnostics = pokemon_tower7f_rocket_exit_runtime_diagnostics()
    diagnostics.extend(pokemon_tower7f_rocket_exit_diagnostics)
    cinnabar_gym_default_diagnostics = cinnabar_gym_default_runtime_diagnostics()
    diagnostics.extend(cinnabar_gym_default_diagnostics)
    name_rater_diagnostics = name_rater_runtime_diagnostics()
    diagnostics.extend(name_rater_diagnostics)
    generated_labels = {candidate["scriptLabel"] for candidate in candidates}
    generated_labels.update(candidate.get("trigger", {}).get("sourceLabel", "") for candidate in candidates)
    for candidate in candidates:
        generated_labels.update(candidate.get("source", {}).get("coveredLabels", []))
    generated_labels.update(trade["scriptLabel"] for trade in trade_definitions if trade.get("scriptLabel"))
    generated_labels.update(candidate["scriptLabel"] for candidate in tile_override_candidates)
    for candidate in tile_override_candidates:
        generated_labels.update(candidate.get("source", {}).get("coveredLabels", []))
    generated_labels.update(rule["scriptLabel"] for rule in object_visibility_rules)
    generated_labels.update(row["scriptLabel"] for row in conditional_dialogue)
    generated_labels.update(row.get("sourceScriptLabel", "") for row in conditional_dialogue)
    for row in conditional_dialogue:
        generated_labels.update(row.get("source", {}).get("coveredLabels", []))
    generated_labels.update(
        diagnostic["scriptLabel"]
        for diagnostic in boulder_diagnostics
        if diagnostic["status"] in {"covered", "generated"}
    )
    generated_labels.update(diagnostic["scriptLabel"] for diagnostic in spin_tile_diagnostics)
    generated_labels.update(diagnostic["scriptLabel"] for diagnostic in trainer_flag_diagnostics)
    generated_labels.update(diagnostic["scriptLabel"] for diagnostic in champion_hof_diagnostics)
    generated_labels.update(diagnostic["scriptLabel"] for diagnostic in oak_intro_diagnostics)
    generated_labels.update(diagnostic["scriptLabel"] for diagnostic in authored_diagnostics)
    generated_labels.update(diagnostic["scriptLabel"] for diagnostic in pallet_daisy_diagnostics)
    generated_labels.update(diagnostic["scriptLabel"] for diagnostic in pokemon_tower7f_rocket_exit_diagnostics)
    generated_labels.update(diagnostic["scriptLabel"] for diagnostic in cinnabar_gym_default_diagnostics)
    generated_labels.update(diagnostic["scriptLabel"] for diagnostic in name_rater_diagnostics)

    text_asm_pointer_diagnostics = text_asm_text_pointer_diagnostics(generated_labels)
    diagnostics.extend(text_asm_pointer_diagnostics)
    generated_labels.update(
        diagnostic["scriptLabel"]
        for diagnostic in text_asm_pointer_diagnostics
        if diagnostic["status"] in {"covered", "generated"}
    )
    for diagnostic in text_asm_pointer_diagnostics:
        if diagnostic["status"] in {"covered", "generated"}:
            generated_labels.update(diagnostic.get("details", {}).get("source", {}).get("coveredLabels", []))

    for block in sorted(ir_blocks, key=lambda row: (row["mapName"], row["label"])):
        diagnostic = diagnostic_for_ir_block(block, generated_labels)
        if diagnostic:
            diagnostics.append(diagnostic)

    diagnostics = apply_diagnostic_profile(diagnostics, runtime_profile)
    for diagnostic in sorted(diagnostics, key=lambda row: (row["status"], row["mapName"], row["scriptLabel"])):
        insert_diagnostic(cursor, diagnostic, map_resolver)

    validate_normalized_script_tables(conn)
    conn.commit()
    conn.close()

    OUTPUT_PATH.write_text(json.dumps(candidates, indent=2, sort_keys=True) + "\n")
    IR_OUTPUT_PATH.write_text(json.dumps(ir_blocks, indent=2, sort_keys=True) + "\n")
    DIAGNOSTICS_OUTPUT_PATH.write_text(json.dumps(diagnostics, indent=2, sort_keys=True) + "\n")
    TRADE_OUTPUT_PATH.write_text(json.dumps(trade_definitions, indent=2, sort_keys=True) + "\n")
    TILE_OUTPUT_PATH.write_text(json.dumps(tile_override_candidates, indent=2, sort_keys=True) + "\n")
    BOULDER_OUTPUT_PATH.write_text(json.dumps(boulder_targets, indent=2, sort_keys=True) + "\n")
    OBJECT_VISIBILITY_OUTPUT_PATH.write_text(json.dumps(object_visibility_rules, indent=2, sort_keys=True) + "\n")
    CONDITIONAL_DIALOGUE_OUTPUT_PATH.write_text(json.dumps(conditional_dialogue, indent=2, sort_keys=True) + "\n")
    print(f"Script event candidates: {len(candidates)}")
    print(f"Script tile override candidates: {len(tile_override_candidates)}")
    print(f"Script boulder targets: {len(boulder_targets)}")
    print(f"Script object visibility rules: {len(object_visibility_rules)}")
    print(f"Script conditional dialogue rows: {len(conditional_dialogue)}")
    print(f"In-game trade definitions: {len(trade_definitions)}")
    print(f"Script IR blocks: {len(ir_blocks)}")
    print(f"Script diagnostics: {len(diagnostics)}")
    print(f"Output JSON: {OUTPUT_PATH}")
    print(f"IR JSON: {IR_OUTPUT_PATH}")
    print(f"Diagnostics JSON: {DIAGNOSTICS_OUTPUT_PATH}")
    print(f"Trade JSON: {TRADE_OUTPUT_PATH}")
    print(f"Tile override JSON: {TILE_OUTPUT_PATH}")
    print(f"Boulder target JSON: {BOULDER_OUTPUT_PATH}")
    print(f"Object visibility JSON: {OBJECT_VISIBILITY_OUTPUT_PATH}")
    print(f"Conditional dialogue JSON: {CONDITIONAL_DIALOGUE_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
