import os
import re
import sqlite3
import glob

from config import BASE_STATS_DIR, DB_PATH, POKEDEX_CONSTANTS_FILE, POKEMON_DATA_DIR
from pokemon_names import SPECIAL_NAME_MAPPINGS, normalize_pokemon_name

# Regular expressions
DEX_ENTRY_PATTERN = re.compile(
    r'(\w+)DexEntry:\s*\n\s*db "([^"]+)@"\s*\n\s*db (\d+),(\d+)\s*\n\s*dw (\d+)'
)
DEX_TEXT_PATTERN = re.compile(
    r'_(\w+)DexEntry::\s*\n((?:\s*text "[^"]+"\s*\n\s*next "[^"]+"\s*\n\s*next "[^"]+"\s*\n\s*\n\s*page "[^"]+"\s*\n\s*next "[^"]+"\s*\n\s*next "[^"]+"\s*\n\s*dex\s*\n)+)'
)
EVOS_PATTERN = re.compile(
    r"(\w+)EvosMoves:\s*\n; Evolutions\s*\n((?:\s*db [^\n]+\n)+)\s*db 0"
)
EVOLVE_LEVEL_PATTERN = re.compile(r"\s*db EVOLVE_LEVEL, (\d+), (\w+)")
EVOLVE_ITEM_PATTERN = re.compile(r"\s*db EVOLVE_ITEM, [^,]+, \d+, (\w+)")
EVOLVE_TRADE_PATTERN = re.compile(r"\s*db EVOLVE_TRADE, \d+, (\w+)")
CRY_PATTERN = re.compile(r"\s*mon_cry [^,]+, \$([0-9A-F]+), \$([0-9A-F]+) ; (.+)$")

def create_database():
    """Create SQLite database and tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Drop existing pokemon table if it exists
    cursor.execute("DROP TABLE IF EXISTS pokemon")

    # Create pokemon table
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS pokemon (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        hp INTEGER NOT NULL,
        atk INTEGER NOT NULL,
        def INTEGER NOT NULL,
        spd INTEGER NOT NULL,
        spc INTEGER NOT NULL,
        type_1 TEXT NOT NULL,
        type_2 TEXT NOT NULL,
        catch_rate INTEGER NOT NULL,
        base_exp INTEGER NOT NULL,
        default_move_1_id TEXT,
        default_move_2_id TEXT,
        default_move_3_id TEXT,
        default_move_4_id TEXT,
        base_cry INTEGER,
        cry_pitch INTEGER,
        cry_length INTEGER,
        pokedex_type TEXT,
        height TEXT,
        weight INTEGER,
        pokedex_text TEXT,
        evolve_level INTEGER,
        evolve_pokemon TEXT,
        evolves_from_trade INTEGER NOT NULL DEFAULT 0,
        icon_image TEXT,
        palette_type TEXT
    )
    """
    )

    conn.commit()
    return conn, cursor


def load_pokedex_constants():
    """Load Pokémon names and their Pokédex numbers from the constants file."""
    pokemon_dex = {}
    dex_to_name = {}

    with open(POKEDEX_CONSTANTS_FILE, "r") as f:
        for line in f:
            match = re.search(r"const DEX_(\w+)\s*; (\d+)", line)
            if match:
                name = match.group(1)
                dex_num = int(match.group(2))
                pokemon_dex[name] = dex_num
                dex_to_name[dex_num] = name

    return pokemon_dex, dex_to_name


def extract_base_stats():
    """Extract base stats from all Pokémon base stats files."""
    pokemon_stats = {}

    for stats_file in glob.glob(f"{BASE_STATS_DIR}/*.asm"):
        pokemon_name = os.path.basename(stats_file).replace(".asm", "")
        normalized_name = normalize_pokemon_name(pokemon_name)

        with open(stats_file, "r") as f:
            content = f.read()

            # Extract Pokédex ID
            dex_id_match = re.search(r"db DEX_(\w+)", content)
            if dex_id_match:
                dex_id = dex_id_match.group(1)
                normalized_dex_id = normalize_pokemon_name(dex_id)
            else:
                continue

            # Extract base stats
            stats_match = re.search(
                r"db\s+(\d+),\s+(\d+),\s+(\d+),\s+(\d+),\s+(\d+)", content
            )
            if stats_match:
                hp, atk, def_, spd, spc = map(int, stats_match.groups())
            else:
                continue

            # Extract types
            types_match = re.search(r"db (\w+), (\w+) ; type", content)
            if types_match:
                type_1, type_2 = types_match.groups()
                # Fix for PSYCHIC_TYPE -> PSYCHIC
                if type_1 == "PSYCHIC_TYPE":
                    type_1 = "PSYCHIC"
                if type_2 == "PSYCHIC_TYPE":
                    type_2 = "PSYCHIC"
            else:
                continue

            # Extract catch rate and base exp
            catch_rate_match = re.search(r"db (\d+) ; catch rate", content)
            base_exp_match = re.search(r"db (\d+) ; base exp", content)

            catch_rate = int(catch_rate_match.group(1)) if catch_rate_match else 0
            base_exp = int(base_exp_match.group(1)) if base_exp_match else 0

            # Extract default moves
            moves_match = re.search(
                r"db ([^,\s]+), ([^,\s]+), ([^,\s]+), ([^,\s]+) ; level 1 learnset",
                content,
            )
            if moves_match:
                move_1, move_2, move_3, move_4 = moves_match.groups()
            else:
                move_1, move_2, move_3, move_4 = (
                    "NO_MOVE",
                    "NO_MOVE",
                    "NO_MOVE",
                    "NO_MOVE",
                )

            pokemon_stats[normalized_dex_id] = {
                "name": normalized_dex_id,
                "hp": hp,
                "atk": atk,
                "def": def_,
                "spd": spd,
                "spc": spc,
                "type_1": type_1,
                "type_2": type_2,
                "catch_rate": catch_rate,
                "base_exp": base_exp,
                "default_move_1_id": move_1,
                "default_move_2_id": move_2,
                "default_move_3_id": move_3,
                "default_move_4_id": move_4,
            }

    return pokemon_stats


def extract_cries():
    """Extract cry data from cries.asm."""
    cries = {}

    with open(f"{POKEMON_DATA_DIR}/cries.asm", "r") as f:
        lines = f.readlines()

        # Process each line
        for line in lines:
            if "mon_cry" in line:
                match = CRY_PATTERN.search(line)
                if match:
                    pitch, length, name = match.groups()
                    name = name.strip()  # Strip any whitespace
                    normalized_name = normalize_pokemon_name(name)

                    cries[normalized_name] = {
                        "base_cry": 0,  # Using 0 as a placeholder
                        "cry_pitch": int(pitch, 16),
                        "cry_length": int(length, 16),
                    }

    return cries


def extract_dex_entries():
    """Extract Pokédex entries from dex_entries.asm."""
    dex_entries = {}

    with open(f"{POKEMON_DATA_DIR}/dex_entries.asm", "r") as f:
        content = f.read()

        # First, create a mapping from Pokémon name to its dex entry name
        name_to_dex_entry = {}
        for line in content.split("\n"):
            match = re.search(r"\s*dw (\w+)DexEntry", line)
            if match:
                dex_entry_name = match.group(1)
                normalized_name = normalize_pokemon_name(dex_entry_name)
                name_to_dex_entry[normalized_name] = dex_entry_name

        # Now extract the dex entries
        for match in DEX_ENTRY_PATTERN.finditer(content):
            dex_entry_name, poke_type, height_ft, height_in, weight = match.groups()
            normalized_name = normalize_pokemon_name(dex_entry_name)

            dex_entries[normalized_name] = {
                "pokedex_type": poke_type,
                "height": f"{height_ft},{height_in}",
                "weight": int(weight),
            }

    return dex_entries


def extract_dex_text():
    """Extract Pokédex text from dex_text.asm."""
    dex_text = {}

    with open(f"{POKEMON_DATA_DIR}/dex_text.asm", "r") as f:
        content = f.read()

        # Extract all Pokédex entries
        for entry_match in re.finditer(r"_(\w+)DexEntry::([\s\S]*?)dex", content):
            pokemon_name = entry_match.group(1)
            normalized_name = normalize_pokemon_name(pokemon_name)
            entry_text = entry_match.group(2)

            # Extract all text and next lines
            text_parts = []
            for line in entry_text.split("\n"):
                text_match = re.search(r'text "([^"]+)"', line)
                next_match = re.search(r'next "([^"]+)"', line)
                page_match = re.search(r'page "([^"]+)"', line)

                if text_match:
                    text_parts.append(text_match.group(1))
                elif next_match:
                    text_parts.append(next_match.group(1))
                elif page_match:
                    text_parts.append(page_match.group(1))

            # Join all text parts with spaces
            dex_text[normalized_name] = " ".join(text_parts)

    return dex_text


def extract_evolutions():
    """Extract evolution data from evos_moves.asm."""
    evolutions = {}

    with open(f"{POKEMON_DATA_DIR}/evos_moves.asm", "r") as f:
        content = f.read()

        # First, create a mapping from Pokémon name to its evo_moves entry name
        name_to_evo_entry = {}
        for line in content.split("\n"):
            match = re.search(r"\s*dw (\w+)EvosMoves", line)
            if match:
                evo_entry_name = match.group(1)
                normalized_name = normalize_pokemon_name(evo_entry_name)
                name_to_evo_entry[normalized_name] = evo_entry_name

        # Now extract the evolution data
        for match in re.finditer(r"(\w+)EvosMoves:\s*\n; Evolutions", content):
            pokemon_name = match.group(1)
            normalized_name = normalize_pokemon_name(pokemon_name)

            # Find the start of the evolution block
            start_pos = match.end()

            # Extract the evolution block until 'db 0'
            evo_block = ""
            for line in content[start_pos:].split("\n"):
                evo_block += line + "\n"
                if line.strip() == "db 0":
                    break

            evolve_level = None
            evolve_pokemon = None
            evolves_from_trade = False

            # Check for level evolution
            level_match = EVOLVE_LEVEL_PATTERN.search(evo_block)
            if level_match:
                evolve_level = int(level_match.group(1))
                evolve_pokemon = normalize_pokemon_name(level_match.group(2))

            # Check for item evolution
            item_match = EVOLVE_ITEM_PATTERN.search(evo_block)
            if item_match:
                evolve_pokemon = normalize_pokemon_name(item_match.group(1))

            # Check for trade evolution
            trade_match = EVOLVE_TRADE_PATTERN.search(evo_block)
            if trade_match:
                evolve_pokemon = normalize_pokemon_name(trade_match.group(1))
                evolves_from_trade = True

            evolutions[normalized_name] = {
                "evolve_level": evolve_level,
                "evolve_pokemon": evolve_pokemon,
                "evolves_from_trade": evolves_from_trade,
            }

    return evolutions


def extract_menu_icons():
    """Extract menu icons from menu_icons.asm."""
    icons = {}
    pokemon_names = []

    # First, get the list of Pokémon names in order
    with open(POKEDEX_CONSTANTS_FILE, "r") as f:
        for line in f:
            match = re.search(r"const DEX_(\w+)\s*; (\d+)", line)
            if match:
                pokemon_names.append(match.group(1))

    with open(f"{POKEMON_DATA_DIR}/menu_icons.asm", "r") as f:
        lines = f.readlines()

        # Skip the first few lines of header
        pokemon_index = 0
        for line in lines[3:]:  # Skip the first 3 lines
            if "nybble ICON_" in line:
                icon_match = re.search(r"nybble (ICON_\w+)", line)
                if icon_match and pokemon_index < len(pokemon_names):
                    icon = icon_match.group(1)
                    pokemon_name = normalize_pokemon_name(pokemon_names[pokemon_index])
                    icons[pokemon_name] = icon
                    pokemon_index += 1

    return icons


def extract_palettes():
    """Extract palette types from palettes.asm."""
    palettes = {}
    pokemon_names = []

    # First, get the list of Pokémon names in order
    with open(POKEDEX_CONSTANTS_FILE, "r") as f:
        for line in f:
            match = re.search(r"const DEX_(\w+)\s*; (\d+)", line)
            if match:
                pokemon_names.append(match.group(1))

    with open(f"{POKEMON_DATA_DIR}/palettes.asm", "r") as f:
        lines = f.readlines()

        # Skip the first few lines of header
        pokemon_index = 0
        for line in lines[2:]:  # Skip the first 2 lines
            if "db PAL_" in line:
                palette_match = re.search(r"db (PAL_\w+)", line)
                if palette_match and pokemon_index < len(pokemon_names):
                    palette = palette_match.group(1)
                    pokemon_name = normalize_pokemon_name(pokemon_names[pokemon_index])
                    palettes[pokemon_name] = palette
                    pokemon_index += 1

    return palettes


def main():
    # Create database
    conn, cursor = create_database()

    # Load Pokédex constants
    pokemon_dex, dex_to_name = load_pokedex_constants()

    # Extract data from various files
    base_stats = extract_base_stats()
    cries = extract_cries()
    dex_entries = extract_dex_entries()
    dex_text = extract_dex_text()
    evolutions = extract_evolutions()
    menu_icons = extract_menu_icons()
    palettes = extract_palettes()

    # Insert data into database
    for name, dex_num in pokemon_dex.items():
        if name in base_stats:
            # Prepare data for insertion
            pokemon_data = {
                "id": dex_num,
                "name": name,
                "hp": base_stats[name]["hp"],
                "atk": base_stats[name]["atk"],
                "def": base_stats[name]["def"],
                "spd": base_stats[name]["spd"],
                "spc": base_stats[name]["spc"],
                "type_1": base_stats[name]["type_1"],
                "type_2": base_stats[name]["type_2"],
                "catch_rate": base_stats[name]["catch_rate"],
                "base_exp": base_stats[name]["base_exp"],
                "default_move_1_id": base_stats[name]["default_move_1_id"],
                "default_move_2_id": base_stats[name]["default_move_2_id"],
                "default_move_3_id": base_stats[name]["default_move_3_id"],
                "default_move_4_id": base_stats[name]["default_move_4_id"],
                "base_cry": cries.get(name, {}).get("base_cry"),
                "cry_pitch": cries.get(name, {}).get("cry_pitch"),
                "cry_length": cries.get(name, {}).get("cry_length"),
                "pokedex_type": dex_entries.get(name, {}).get("pokedex_type"),
                "height": dex_entries.get(name, {}).get("height"),
                "weight": dex_entries.get(name, {}).get("weight"),
                "pokedex_text": dex_text.get(name),
                "evolve_level": evolutions.get(name, {}).get("evolve_level"),
                "evolve_pokemon": evolutions.get(name, {}).get("evolve_pokemon"),
                "evolves_from_trade": (
                    1 if evolutions.get(name, {}).get("evolves_from_trade") else 0
                ),
                "icon_image": menu_icons.get(name),
                "palette_type": palettes.get(name),
            }

            # Insert into database
            cursor.execute(
                """
            INSERT INTO pokemon (
                id, name, hp, atk, def, spd, spc, type_1, type_2, catch_rate, base_exp,
                default_move_1_id, default_move_2_id, default_move_3_id, default_move_4_id,
                base_cry, cry_pitch, cry_length, pokedex_type, height, weight, pokedex_text,
                evolve_level, evolve_pokemon, evolves_from_trade, icon_image, palette_type
            ) VALUES (
                :id, :name, :hp, :atk, :def, :spd, :spc, :type_1, :type_2, :catch_rate, :base_exp,
                :default_move_1_id, :default_move_2_id, :default_move_3_id, :default_move_4_id,
                :base_cry, :cry_pitch, :cry_length, :pokedex_type, :height, :weight, :pokedex_text,
                :evolve_level, :evolve_pokemon, :evolves_from_trade, :icon_image, :palette_type
            )
            """,
                pokemon_data,
            )

    # Commit changes and close connection
    conn.commit()
    conn.close()

    print(f"Exported data for {len(pokemon_dex)} Pokémon to pokemon.db")


if __name__ == "__main__":
    main()
