#!/usr/bin/env python3
"""
Full reprocessing pipeline for Pokémon game data extraction.

This script runs all export steps in the correct order and writes the generated
SQLite artifact to pokemon.db in the repository root.

Pipeline order matters! Key dependencies:
  1. export_map.py              - Extracts maps, tilesets, blocksets, collision data, tiles_raw
                                  (DROPS and recreates base tables including overworld_map_positions)
  2. export_warps.py            - Extracts warp points between maps
  3. update_zone_coordinates.py - Populates overworld_map_positions with x/y offsets
                                  (MUST run after export_map.py and BEFORE create_zones_and_tiles.py,
                                   otherwise all overworld tiles stack at (0,0) and are invisible)
  4. create_zones_and_tiles.py  - Expands raw blocks into 16x16 tiles with walkability
                                  (reads overworld_map_positions for tile coordinate offsets)
  5. export_items.py            - 138 items with prices, usability, TM/HM links
                                  (MUST run before export_objects.py so visible item balls can resolve item_id)
  6. export_objects.py          - Extracts NPCs, items, signs (incl. trainer_class, trainer_party_index)
  7. update_object_coordinates.py - Applies overworld offsets to object positions
  8. export_pokemon.py          - 151 Pokémon with base stats, types, evolution, Pokédex data
  9. export_moves.py            - 154 moves with power, type, accuracy, effects
 10. export_text.py             - Dialogue text, text pointers, trainer headers
 11. export_learnsets.py        - Level-up learnsets + TM/HM compatibility
 12. export_wild_encounters.py  - Wild encounters + encounter slot probabilities
 13. export_trainers.py         - Trainer classes, parties, party Pokémon
 14. export_hidden_objects.py   - Hidden items, coins, objects, map music
  15. export_map_scripts.py      - Map scripts, NPC movement, event flags, coordinate triggers, warp events
                                   and spin/arrow tile forced movement
 16. export_script_candidates.py - Structured candidates for script behaviors
 17. export_viewer_data.py       - Static JSON/assets for the offline Phaser viewer
"""
import subprocess
import os
import sqlite3
import sys

from config import DB_PATH

scripts = [
    # Map infrastructure (order-dependent)
    "export_map.py",
    "export_warps.py",
    "update_zone_coordinates.py",
    "create_zones_and_tiles.py",
    "export_items.py",
    "export_objects.py",
    "update_object_coordinates.py",
    # Standalone data exports (no order dependency between these)
    "export_pokemon.py",
    "export_moves.py",
    "export_text.py",
    "export_learnsets.py",
    "export_wild_encounters.py",
    "export_trainers.py",
    "export_hidden_objects.py",
    "export_map_scripts.py",
    "export_script_candidates.py",
    "export_viewer_data.py",
]


def run_script(script_name):
    print(f"\n{'='*60}")
    print(f">>> Running {script_name}...")
    print(f"{'='*60}")
    try:
        subprocess.run([sys.executable, script_name], check=True)
    except subprocess.CalledProcessError as e:
        print(f"!!! Error running {script_name}: {e}")
        sys.exit(1)


def validate_generated_database(db_path):
    required_tables = [
        "script_event_ir_blocks",
        "script_event_candidates",
        "script_event_candidate_diagnostics",
        "script_event_in_game_trades",
        "script_event_tile_overrides",
        "script_event_boulder_targets",
        "script_event_conditional_dialogue",
        "spin_tiles",
    ]
    conn = sqlite3.connect(db_path)
    try:
        for table in required_tables:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            if not exists:
                print(f"!!! Missing generated table: {table}")
                sys.exit(1)
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if count == 0:
                print(f"!!! Generated table is empty: {table}")
                sys.exit(1)
            print(f"Validated {table}: {count} rows")
    finally:
        conn.close()


def main():
    # Change to the script's directory so it can find the other scripts
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    # Run all Python export scripts
    for script in scripts:
        run_script(script)

    if not DB_PATH.exists():
        print(f"!!! pokemon.db not found at {DB_PATH}")
        sys.exit(1)
    if DB_PATH.stat().st_size == 0:
        print(f"!!! pokemon.db is empty at {DB_PATH}")
        sys.exit(1)
    validate_generated_database(DB_PATH)

    print(f"\n{'='*60}")
    print("✅ All reprocessing steps completed successfully!")
    print(f"{'='*60}")
    print(f"\nOutput: {DB_PATH}")


if __name__ == "__main__":
    main()
