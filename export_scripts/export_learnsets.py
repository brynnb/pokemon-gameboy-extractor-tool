#!/usr/bin/env python3
"""
Extract Pokemon level-up learnsets and TM/HM compatibility from the pokered disassembly.

Parses:
  - data/pokemon/evos_moves.asm for level-up move learnsets
  - data/pokemon/base_stats/*.asm for TM/HM compatibility
  - constants/item_constants.asm for TM->move mappings

Creates tables:
  - pokemon_learnset: Level-up moves per Pokemon
  - pokemon_tmhm: TM/HM compatibility per Pokemon
"""
import os
import re
import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from utils.pokemon_utils import SPECIAL_NAME_MAPPINGS, normalize_pokemon_name

# Constants
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "pokemon.db"
POKEMON_DATA_DIR = PROJECT_ROOT / "pokemon-game-data/data/pokemon"
BASE_STATS_DIR = POKEMON_DATA_DIR / "base_stats"
EVOS_MOVES_FILE = POKEMON_DATA_DIR / "evos_moves.asm"
CONSTANTS_DIR = PROJECT_ROOT / "pokemon-game-data/constants"
MOVE_CONSTANTS_FILE = CONSTANTS_DIR / "move_constants.asm"
ITEM_CONSTANTS_FILE = CONSTANTS_DIR / "item_constants.asm"
POKEDEX_CONSTANTS_FILE = CONSTANTS_DIR / "pokedex_constants.asm"


def create_tables(conn):
    """Create learnset-related tables."""
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS pokemon_learnset")
    cursor.execute("DROP TABLE IF EXISTS pokemon_tmhm")

    # Level-up learnset: which moves a Pokemon learns at which level
    cursor.execute("""
    CREATE TABLE pokemon_learnset (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pokemon_id INTEGER NOT NULL,
        pokemon_name TEXT NOT NULL,
        level INTEGER NOT NULL,
        move_name TEXT NOT NULL,
        move_id INTEGER,
        FOREIGN KEY (pokemon_id) REFERENCES pokemon (id)
    )
    """)

    # TM/HM compatibility: which TMs/HMs a Pokemon can learn
    cursor.execute("""
    CREATE TABLE pokemon_tmhm (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pokemon_id INTEGER NOT NULL,
        pokemon_name TEXT NOT NULL,
        tm_hm_name TEXT NOT NULL,
        move_name TEXT NOT NULL,
        move_id INTEGER,
        is_hm INTEGER DEFAULT 0,
        FOREIGN KEY (pokemon_id) REFERENCES pokemon (id)
    )
    """)

    conn.commit()
    return cursor


def load_pokedex_constants():
    """Load Pokemon names and their Pokedex numbers."""
    pokemon_dex = {}
    with open(POKEDEX_CONSTANTS_FILE, "r") as f:
        for line in f:
            match = re.search(r"const DEX_(\w+)\s*; (\d+)", line)
            if match:
                name = match.group(1)
                dex_num = int(match.group(2))
                pokemon_dex[name] = dex_num
    return pokemon_dex


def load_move_constants():
    """Load move name -> move ID mapping."""
    move_ids = {}
    with open(MOVE_CONSTANTS_FILE, "r") as f:
        for line in f:
            match = re.search(r"const (\w+)\s*; (\w+)", line)
            if match:
                move_name = match.group(1)
                try:
                    move_id = int(match.group(2), 16)
                    move_ids[move_name] = move_id
                except ValueError:
                    continue
    return move_ids


def load_tm_hm_moves():
    """
    Load TM/HM number -> move name mapping from item_constants.asm.
    Returns list of (tm_hm_name, move_name, is_hm) tuples in order.
    """
    tm_hm_list = []

    with open(ITEM_CONSTANTS_FILE, "r") as f:
        content = f.read()

    # Parse add_tm and add_hm macros
    # Format: add_tm MEGA_PUNCH  or  add_hm CUT
    tm_num = 1
    hm_num = 1

    for line in content.split("\n"):
        stripped = line.strip()
        tm_match = re.match(r"add_tm\s+(\w+)", stripped)
        hm_match = re.match(r"add_hm\s+(\w+)", stripped)

        if tm_match:
            move_name = tm_match.group(1)
            tm_hm_list.append((f"TM{tm_num:02d}", move_name, 0))
            tm_num += 1
        elif hm_match:
            move_name = hm_match.group(1)
            tm_hm_list.append((f"HM{hm_num:02d}", move_name, 1))
            hm_num += 1

    return tm_hm_list


def parse_evos_moves():
    """
    Parse evos_moves.asm to extract level-up learnsets.
    Returns dict of {pokemon_name: [(level, move_name), ...]}.
    """
    learnsets = {}

    with open(EVOS_MOVES_FILE, "r") as f:
        content = f.read()

    # Find each Pokemon's EvosMoves block
    # Format:
    # PokemonNameEvosMoves:
    # ; Evolutions
    # ... evolution data ...
    # db 0
    # ; Learnset
    # db level, MOVE_NAME
    # ...
    # db 0

    pattern = re.compile(
        r"(\w+)EvosMoves:\s*\n"
        r"; Evolutions\s*\n"
        r"(.*?)"  # evolution block
        r"db 0\s*\n"
        r"(?:; Learnset\s*\n)?"
        r"(.*?)"  # learnset block
        r"db 0",
        re.DOTALL
    )

    for match in pattern.finditer(content):
        pokemon_name = match.group(1)
        learnset_block = match.group(3)

        # Skip MissingNo entries
        if "MissingNo" in pokemon_name or "Fossil" in pokemon_name or "MonGhost" in pokemon_name:
            continue

        normalized = normalize_pokemon_name(pokemon_name)
        moves = []

        for line in learnset_block.strip().split("\n"):
            move_match = re.match(r"\s*db\s+(\d+),\s+(\w+)", line.strip())
            if move_match:
                level = int(move_match.group(1))
                move_name = move_match.group(2)
                moves.append((level, move_name))

        if moves:
            learnsets[normalized] = moves

    return learnsets


def parse_tmhm_compatibility(base_stats_file, tm_hm_list):
    """
    Parse a base_stats/*.asm file for TM/HM compatibility.
    The tmhm macro encodes compatibility as a bitmask.
    Returns list of compatible move names.
    """
    with open(base_stats_file, "r") as f:
        content = f.read()

    # Find the tmhm line(s) - may span multiple lines with backslash continuation
    # Format: tmhm MOVE1, MOVE2, MOVE3, ...
    tmhm_match = re.search(r"tmhm\s+(.*?)(?:\n\s*; end)", content, re.DOTALL)
    if not tmhm_match:
        return []

    tmhm_block = tmhm_match.group(1)
    # Remove line continuations and extra whitespace
    tmhm_block = tmhm_block.replace("\\\n", " ")
    tmhm_block = re.sub(r"\s+", " ", tmhm_block).strip()

    # Extract move names
    compatible_moves = [m.strip() for m in tmhm_block.split(",") if m.strip()]

    return compatible_moves


def main():
    conn = sqlite3.connect(DB_PATH)
    cursor = create_tables(conn)

    # Load reference data
    pokemon_dex = load_pokedex_constants()
    move_ids = load_move_constants()
    tm_hm_list = load_tm_hm_moves()

    print(f"Loaded {len(pokemon_dex)} Pokemon, {len(move_ids)} moves, {len(tm_hm_list)} TMs/HMs")

    # =========================================================================
    # Phase 1: Level-up learnsets
    # =========================================================================
    print("\nPhase 1: Extracting level-up learnsets...")
    learnsets = parse_evos_moves()

    learnset_count = 0
    for pokemon_name, moves in learnsets.items():
        dex_num = pokemon_dex.get(pokemon_name)
        if not dex_num:
            # Try case variations
            for pname, pnum in pokemon_dex.items():
                if pname.upper() == pokemon_name.upper():
                    dex_num = pnum
                    break

        if not dex_num:
            print(f"  Warning: Could not find dex number for {pokemon_name}")
            continue

        for level, move_name in moves:
            move_id = move_ids.get(move_name)
            cursor.execute(
                """INSERT INTO pokemon_learnset 
                   (pokemon_id, pokemon_name, level, move_name, move_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (dex_num, pokemon_name, level, move_name, move_id),
            )
            learnset_count += 1

    print(f"  Extracted {learnset_count} level-up moves for {len(learnsets)} Pokemon")

    # =========================================================================
    # Phase 2: TM/HM compatibility
    # =========================================================================
    print("\nPhase 2: Extracting TM/HM compatibility...")

    # Build move_name -> (tm_hm_name, is_hm) lookup
    move_to_tmhm = {}
    for tm_hm_name, move_name, is_hm in tm_hm_list:
        move_to_tmhm[move_name] = (tm_hm_name, is_hm)

    tmhm_count = 0
    pokemon_with_tmhm = 0

    for stats_file in sorted(BASE_STATS_DIR.glob("*.asm")):
        pokemon_file_name = stats_file.stem
        normalized = normalize_pokemon_name(pokemon_file_name)

        dex_num = pokemon_dex.get(normalized)
        if not dex_num:
            for pname, pnum in pokemon_dex.items():
                if pname.upper() == normalized.upper():
                    dex_num = pnum
                    break

        if not dex_num:
            continue

        compatible_moves = parse_tmhm_compatibility(stats_file, tm_hm_list)
        if compatible_moves:
            pokemon_with_tmhm += 1

        for move_name in compatible_moves:
            tm_hm_info = move_to_tmhm.get(move_name)
            if tm_hm_info:
                tm_hm_name, is_hm = tm_hm_info
            else:
                tm_hm_name = move_name
                is_hm = 0

            move_id = move_ids.get(move_name)
            cursor.execute(
                """INSERT INTO pokemon_tmhm 
                   (pokemon_id, pokemon_name, tm_hm_name, move_name, move_id, is_hm)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (dex_num, normalized, tm_hm_name, move_name, move_id, is_hm),
            )
            tmhm_count += 1

    print(f"  Extracted {tmhm_count} TM/HM entries for {pokemon_with_tmhm} Pokemon")

    conn.commit()
    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    main()
