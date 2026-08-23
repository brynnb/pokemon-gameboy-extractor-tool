import os
import re
import sqlite3
import glob

from config import BASE_STATS_DIR, DB_PATH, POKEDEX_CONSTANTS_FILE, POKEMON_DATA_DIR
from export_moves import parse_move_constants
from pokemon_names import normalize_pokemon_name

# Regular expressions
DEX_ENTRY_PATTERN = re.compile(
    r'(\w+)DexEntry:\s*\n\s*db "([^"]+)@"\s*\n\s*db (\d+),(\d+)\s*\n\s*dw (\d+)'
)
DEX_TEXT_PATTERN = re.compile(
    r'_(\w+)DexEntry::\s*\n((?:\s*text "[^"]+"\s*\n\s*next "[^"]+"\s*\n\s*next "[^"]+"\s*\n\s*\n\s*page "[^"]+"\s*\n\s*next "[^"]+"\s*\n\s*next "[^"]+"\s*\n\s*dex\s*\n)+)'
)
CRY_PATTERN = re.compile(
    r"\s*mon_cry\s+SFX_CRY_([0-9A-F]{2}),\s+\$([0-9A-F]+),\s+\$([0-9A-F]+)\s*;\s*(.+)$",
    re.IGNORECASE,
)

def create_tables(conn):
    """Create the Pokemon species and normalized evolution tables."""
    cursor = conn.cursor()

    # The relationship table references pokemon, so it must be dropped first.
    cursor.execute("DROP TABLE IF EXISTS pokemon_default_moves")
    cursor.execute("DROP TABLE IF EXISTS pokemon_evolutions")
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
        default_move_1_id INTEGER,
        default_move_2_id INTEGER,
        default_move_3_id INTEGER,
        default_move_4_id INTEGER,
        default_move_1_name TEXT NOT NULL DEFAULT 'NO_MOVE',
        default_move_2_name TEXT NOT NULL DEFAULT 'NO_MOVE',
        default_move_3_name TEXT NOT NULL DEFAULT 'NO_MOVE',
        default_move_4_name TEXT NOT NULL DEFAULT 'NO_MOVE',
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
        palette_type TEXT,
        FOREIGN KEY (default_move_1_id) REFERENCES moves (id),
        FOREIGN KEY (default_move_2_id) REFERENCES moves (id),
        FOREIGN KEY (default_move_3_id) REFERENCES moves (id),
        FOREIGN KEY (default_move_4_id) REFERENCES moves (id),
        CHECK ((default_move_1_id IS NULL) = (default_move_1_name = 'NO_MOVE')),
        CHECK ((default_move_2_id IS NULL) = (default_move_2_name = 'NO_MOVE')),
        CHECK ((default_move_3_id IS NULL) = (default_move_3_name = 'NO_MOVE')),
        CHECK ((default_move_4_id IS NULL) = (default_move_4_name = 'NO_MOVE'))
    )
    """
    )

    cursor.execute(
        """
        CREATE TABLE pokemon_evolutions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_pokemon_id INTEGER NOT NULL,
            target_pokemon_id INTEGER NOT NULL,
            method TEXT NOT NULL CHECK (method IN ('level', 'item', 'trade')),
            level INTEGER NOT NULL CHECK (level >= 1),
            item_id INTEGER,
            source_order INTEGER NOT NULL CHECK (source_order >= 1),
            FOREIGN KEY (source_pokemon_id) REFERENCES pokemon (id),
            FOREIGN KEY (target_pokemon_id) REFERENCES pokemon (id),
            FOREIGN KEY (item_id) REFERENCES items (id),
            UNIQUE (source_pokemon_id, source_order),
            CHECK (
                (method = 'item' AND item_id IS NOT NULL)
                OR (method IN ('level', 'trade') AND item_id IS NULL)
            )
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE pokemon_default_moves (
            pokemon_id INTEGER NOT NULL,
            slot_index INTEGER NOT NULL CHECK(slot_index BETWEEN 1 AND 4),
            move_id INTEGER NOT NULL,
            source_move_name TEXT NOT NULL CHECK(source_move_name <> 'NO_MOVE'),
            PRIMARY KEY(pokemon_id, slot_index),
            FOREIGN KEY(pokemon_id) REFERENCES pokemon(id) ON DELETE CASCADE,
            FOREIGN KEY(move_id) REFERENCES moves(id),
            UNIQUE(pokemon_id, move_id)
        ) WITHOUT ROWID
        """
    )
    cursor.execute(
        """
        CREATE INDEX idx_pokemon_evolutions_target
        ON pokemon_evolutions (target_pokemon_id)
        """
    )

    conn.commit()
    return cursor


def create_database():
    """Open the generated database and recreate Pokemon-related tables."""
    conn = sqlite3.connect(DB_PATH)
    cursor = create_tables(conn)
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
                "default_move_1_name": move_1,
                "default_move_2_name": move_2,
                "default_move_3_name": move_3,
                "default_move_4_name": move_4,
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
                    base_cry, pitch, length, name = match.groups()
                    name = name.strip()  # Strip any whitespace
                    normalized_name = normalize_pokemon_name(name)

                    cries[normalized_name] = {
                        "base_cry": int(base_cry, 16),
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


def parse_evolutions(content):
    """Parse every source evolution row, preserving its order within each species."""
    evolutions = []
    block_pattern = re.compile(
        r"^(\w+)EvosMoves:\s*\n"
        r"\s*;\s*Evolutions\s*\n"
        r"(.*?)"
        r"^\s*db\s+0(?:\s*;[^\n]*)?$",
        re.MULTILINE | re.DOTALL,
    )

    row_patterns = (
        (
            "level",
            re.compile(r"^db\s+EVOLVE_LEVEL,\s*(\d+),\s*(\w+)(?:\s*;.*)?$"),
        ),
        (
            "item",
            re.compile(
                r"^db\s+EVOLVE_ITEM,\s*(\w+),\s*(\d+),\s*(\w+)(?:\s*;.*)?$"
            ),
        ),
        (
            "trade",
            re.compile(r"^db\s+EVOLVE_TRADE,\s*(\d+),\s*(\w+)(?:\s*;.*)?$"),
        ),
    )

    for block_match in block_pattern.finditer(content):
        source_name = normalize_pokemon_name(block_match.group(1))
        source_order = 0
        for raw_line in block_match.group(2).splitlines():
            line = raw_line.strip()
            if not line or line.startswith(";"):
                continue
            if not re.match(r"^db\s+EVOLVE_", line):
                continue

            parsed = None
            for method, pattern in row_patterns:
                row_match = pattern.fullmatch(line)
                if not row_match:
                    continue
                source_order += 1
                if method == "item":
                    item_constant, level, target_name = row_match.groups()
                else:
                    level, target_name = row_match.groups()
                    item_constant = None
                parsed = {
                    "source_name": source_name,
                    "target_name": normalize_pokemon_name(target_name),
                    "method": method,
                    "level": int(level),
                    "item_constant": item_constant,
                    "source_order": source_order,
                }
                evolutions.append(parsed)
                break

            if parsed is None:
                raise ValueError(f"Unsupported evolution row for {source_name}: {line}")

    source_row_count = sum(
        bool(re.match(r"^db\s+EVOLVE_", raw_line.strip()))
        for raw_line in content.splitlines()
    )
    if len(evolutions) != source_row_count:
        raise ValueError(
            "Evolution parser coverage mismatch: "
            f"parsed {len(evolutions)} of {source_row_count} source rows"
        )

    return evolutions


def extract_evolutions():
    """Extract all normalized evolution rows from evos_moves.asm."""
    with open(f"{POKEMON_DATA_DIR}/evos_moves.asm", "r") as f:
        return parse_evolutions(f.read())


def evolution_rows_by_source(evolutions):
    """Group parsed evolution rows by normalized source species name."""
    grouped = {}
    for evolution in evolutions:
        grouped.setdefault(evolution["source_name"], []).append(evolution)
    return grouped


def insert_evolutions(cursor, evolutions, pokemon_ids, item_ids):
    """Resolve relationship IDs and insert every parsed evolution row."""
    for evolution in evolutions:
        source_name = evolution["source_name"]
        target_name = evolution["target_name"]
        if source_name not in pokemon_ids:
            raise ValueError(f"Unknown evolution source Pokemon: {source_name}")
        if target_name not in pokemon_ids:
            raise ValueError(f"Unknown evolution target Pokemon: {target_name}")

        item_constant = evolution["item_constant"]
        item_id = None
        if item_constant is not None:
            item_id = item_ids.get(item_constant)
            if item_id is None:
                raise ValueError(f"Unknown evolution item: {item_constant}")

        cursor.execute(
            """
            INSERT INTO pokemon_evolutions (
                source_pokemon_id, target_pokemon_id, method, level, item_id, source_order
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                pokemon_ids[source_name],
                pokemon_ids[target_name],
                evolution["method"],
                evolution["level"],
                item_id,
                evolution["source_order"],
            ),
        )


def validate_pokemon_default_moves(conn):
    """Validate typed compatibility columns and the normalized slot relation."""
    for table in ("pokemon", "pokemon_default_moves", "moves"):
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone():
            raise ValueError(f"Missing default-move relationship table: {table}")

    column_types = {
        row[1]: row[2].upper() for row in conn.execute("PRAGMA table_info(pokemon)")
    }
    for slot in range(1, 5):
        if column_types.get(f"default_move_{slot}_id") != "INTEGER":
            raise ValueError(f"pokemon.default_move_{slot}_id is not INTEGER")

    expected = []
    columns = ", ".join(
        ["id"]
        + [
            value
            for slot in range(1, 5)
            for value in (
                f"default_move_{slot}_id",
                f"default_move_{slot}_name",
            )
        ]
    )
    for row in conn.execute(f"SELECT {columns} FROM pokemon ORDER BY id"):
        pokemon_id, *values = row
        for slot in range(1, 5):
            move_id, move_name = values[(slot - 1) * 2 : slot * 2]
            if move_id is None:
                if move_name != "NO_MOVE":
                    raise ValueError(
                        f"Pokemon {pokemon_id} slot {slot} has a name without a move ID"
                    )
            else:
                if move_name == "NO_MOVE":
                    raise ValueError(
                        f"Pokemon {pokemon_id} slot {slot} has a move ID without a name"
                    )
                expected.append((pokemon_id, slot, move_id, move_name))

    actual = conn.execute(
        """
        SELECT pokemon_id, slot_index, move_id, source_move_name
        FROM pokemon_default_moves ORDER BY pokemon_id, slot_index
        """
    ).fetchall()
    if actual != expected:
        raise ValueError("pokemon_default_moves does not match the four compatibility slots")
    name_mismatches = conn.execute(
        """
        SELECT COUNT(*)
        FROM pokemon_default_moves AS default_move
        JOIN moves ON moves.id = default_move.move_id
        WHERE moves.constant_name <> default_move.source_move_name
        """
    ).fetchone()[0]
    if name_mismatches:
        raise ValueError(
            f"pokemon_default_moves has {name_mismatches} move-name mismatches"
        )
    errors = conn.execute("PRAGMA foreign_key_check(pokemon_default_moves)").fetchall()
    errors.extend(conn.execute("PRAGMA foreign_key_check(pokemon)").fetchall())
    if errors:
        raise ValueError(f"Pokemon default-move foreign-key violations: {errors[:10]}")
    return len(actual)


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
    evolutions_by_source = evolution_rows_by_source(evolutions)
    menu_icons = extract_menu_icons()
    palettes = extract_palettes()
    move_ids = parse_move_constants()

    # Insert data into database
    for name, dex_num in pokemon_dex.items():
        if name in base_stats:
            # Preserve the first source row in the legacy single-evolution columns.
            # Consumers that need complete branching/method data should use
            # pokemon_evolutions instead.
            primary_evolution = next(iter(evolutions_by_source.get(name, [])), None)

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
                **{
                    f"default_move_{slot}_name": base_stats[name][
                        f"default_move_{slot}_name"
                    ]
                    for slot in range(1, 5)
                },
                **{
                    f"default_move_{slot}_id": (
                        None
                        if base_stats[name][f"default_move_{slot}_name"] == "NO_MOVE"
                        else move_ids[
                            base_stats[name][f"default_move_{slot}_name"]
                        ]
                    )
                    for slot in range(1, 5)
                },
                "base_cry": cries.get(name, {}).get("base_cry"),
                "cry_pitch": cries.get(name, {}).get("cry_pitch"),
                "cry_length": cries.get(name, {}).get("cry_length"),
                "pokedex_type": dex_entries.get(name, {}).get("pokedex_type"),
                "height": dex_entries.get(name, {}).get("height"),
                "weight": dex_entries.get(name, {}).get("weight"),
                "pokedex_text": dex_text.get(name),
                "evolve_level": (
                    primary_evolution["level"]
                    if primary_evolution
                    and primary_evolution["method"] == "level"
                    else None
                ),
                "evolve_pokemon": (
                    primary_evolution["target_name"] if primary_evolution else None
                ),
                "evolves_from_trade": (
                    1
                    if primary_evolution
                    and primary_evolution["method"] == "trade"
                    else 0
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
                default_move_1_name, default_move_2_name, default_move_3_name, default_move_4_name,
                base_cry, cry_pitch, cry_length, pokedex_type, height, weight, pokedex_text,
                evolve_level, evolve_pokemon, evolves_from_trade, icon_image, palette_type
            ) VALUES (
                :id, :name, :hp, :atk, :def, :spd, :spc, :type_1, :type_2, :catch_rate, :base_exp,
                :default_move_1_id, :default_move_2_id, :default_move_3_id, :default_move_4_id,
                :default_move_1_name, :default_move_2_name, :default_move_3_name, :default_move_4_name,
                :base_cry, :cry_pitch, :cry_length, :pokedex_type, :height, :weight, :pokedex_text,
                :evolve_level, :evolve_pokemon, :evolves_from_trade, :icon_image, :palette_type
            )
            """,
                pokemon_data,
            )
            for slot_index in range(1, 5):
                move_id = pokemon_data[f"default_move_{slot_index}_id"]
                if move_id is not None:
                    cursor.execute(
                        """
                        INSERT INTO pokemon_default_moves
                            (pokemon_id, slot_index, move_id, source_move_name)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            dex_num,
                            slot_index,
                            move_id,
                            pokemon_data[f"default_move_{slot_index}_name"],
                        ),
                    )

    pokemon_ids = {
        name: pokemon_id
        for pokemon_id, name in cursor.execute("SELECT id, name FROM pokemon")
    }
    item_ids = {
        short_name: item_id
        for item_id, short_name in cursor.execute("SELECT id, short_name FROM items")
    }
    insert_evolutions(cursor, evolutions, pokemon_ids, item_ids)

    # Commit changes and close connection
    conn.commit()
    conn.close()

    print(
        f"Exported data for {len(pokemon_dex)} Pokémon and "
        f"{len(evolutions)} evolution relationships to pokemon.db"
    )


if __name__ == "__main__":
    main()
