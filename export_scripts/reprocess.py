#!/usr/bin/env python3
"""
Full reprocessing pipeline for Pokémon game data extraction.

This script runs all export steps in the correct order, then copies the
resulting SQLite database to the CaptureQuest project and imports it into MySQL.

Pipeline order matters! Key dependencies:
  1. export_map.py              - Extracts maps, tilesets, blocksets, collision data, tiles_raw
                                  (DROPS and recreates base tables including overworld_map_positions)
  2. export_warps.py            - Extracts warp points between maps
  3. update_zone_coordinates.py - Populates overworld_map_positions with x/y offsets
                                  (MUST run after export_map.py and BEFORE create_zones_and_tiles.py,
                                   otherwise all overworld tiles stack at (0,0) and are invisible)
  4. create_zones_and_tiles.py  - Expands raw blocks into 16x16 tiles with walkability
                                  (reads overworld_map_positions for tile coordinate offsets)
  5. export_objects.py          - Extracts NPCs, items, signs (incl. trainer_class, trainer_party_index)
  6. update_object_coordinates.py - Applies overworld offsets to object positions
  7. export_pokemon.py          - 151 Pokémon with base stats, types, evolution, Pokédex data
  8. export_moves.py            - 154 moves with power, type, accuracy, effects
  9. export_items.py            - 138 items with prices, usability, TM/HM links
 10. export_text.py             - Dialogue text, text pointers, trainer headers
 11. export_learnsets.py        - Level-up learnsets + TM/HM compatibility
 12. export_wild_encounters.py  - Wild encounters + encounter slot probabilities
 13. export_trainers.py         - Trainer classes, parties, party Pokémon
 14. export_hidden_objects.py   - Hidden items, coins, objects, map music
 15. export_map_scripts.py      - Map scripts, NPC movement, event flags, coordinate triggers, warp events
 16. generate_mysql_seed.py     - Generates a SQL seed file (optional, for non-Go imports)

After the Python scripts, this also:
 17. Copies pokemon.db to the CaptureQuest public folder
 18. Runs the Go import-phaser tool to sync SQLite → MySQL
"""
import subprocess
import os
import sys
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Sibling repo path (adjust if your layout differs)
CAPTURE_QUEST_ROOT = PROJECT_ROOT.parent / "capture-quest"
CAPTURE_QUEST_DB_DEST = CAPTURE_QUEST_ROOT / "public" / "phaser" / "pokemon.db"
CAPTURE_QUEST_SERVER_DIR = CAPTURE_QUEST_ROOT / "server"

scripts = [
    # Map infrastructure (order-dependent)
    "export_map.py",
    "export_warps.py",
    "update_zone_coordinates.py",
    "create_zones_and_tiles.py",
    "export_objects.py",
    "update_object_coordinates.py",
    # Standalone data exports (no order dependency between these)
    "export_pokemon.py",
    "export_moves.py",
    "export_items.py",
    "export_text.py",
    "export_learnsets.py",
    "export_wild_encounters.py",
    "export_trainers.py",
    "export_hidden_objects.py",
    "export_map_scripts.py",
    # Seed generation (must be last)
    "generate_mysql_seed.py",
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


def copy_db():
    src = PROJECT_ROOT / "pokemon.db"
    if not src.exists():
        print(f"!!! pokemon.db not found at {src}")
        sys.exit(1)
    if not CAPTURE_QUEST_DB_DEST.parent.exists():
        print(
            f"!!! CaptureQuest phaser directory not found: {CAPTURE_QUEST_DB_DEST.parent}"
        )
        print("    Skipping DB copy and MySQL import.")
        return False
    print(f"\n{'='*60}")
    print(f">>> Copying pokemon.db to CaptureQuest...")
    print(f"{'='*60}")
    shutil.copy2(src, CAPTURE_QUEST_DB_DEST)
    print(f"    {src} → {CAPTURE_QUEST_DB_DEST}")
    return True


def run_mysql_import():
    if not CAPTURE_QUEST_SERVER_DIR.exists():
        print(
            f"!!! CaptureQuest server directory not found: {CAPTURE_QUEST_SERVER_DIR}"
        )
        print("    Skipping MySQL import.")
        return
    print(f"\n{'='*60}")
    print(f">>> Importing into MySQL (go run ./cmd/import-phaser)...")
    print(f"{'='*60}")
    try:
        subprocess.run(
            ["go", "run", "./cmd/import-phaser"],
            cwd=str(CAPTURE_QUEST_SERVER_DIR),
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"!!! Error running import-phaser: {e}")
        sys.exit(1)
    except FileNotFoundError:
        print("!!! 'go' command not found. Skipping MySQL import.")
        print("    Run manually: cd server && go run ./cmd/import-phaser")


def main():
    # Change to the script's directory so it can find the other scripts
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    # Run all Python export scripts
    for script in scripts:
        run_script(script)

    # Copy DB and import into MySQL
    if copy_db():
        run_mysql_import()

    print(f"\n{'='*60}")
    print("✅ All reprocessing steps completed successfully!")
    print(f"{'='*60}")
    print("\nNote: Restart the CaptureQuest server to pick up the new data.")


if __name__ == "__main__":
    main()
