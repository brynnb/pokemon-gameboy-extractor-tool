#!/usr/bin/env python3
import os
import re
import sqlite3
from pathlib import Path
from collections import defaultdict

# Constants
# Get the project root directory (parent of the script's directory)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
POKEMON_DATA_DIR = PROJECT_ROOT / "pokemon-game-data/data/maps/objects"
CONSTANTS_DIR = PROJECT_ROOT / "pokemon-game-data/constants"
MAP_HEADERS_DIR = PROJECT_ROOT / "pokemon-game-data/data/maps/headers"
DB_PATH = PROJECT_ROOT / "pokemon.db"

# Map categories
CITIES_AND_TOWNS = [
    "PalletTown",
    "ViridianCity",
    "PewterCity",
    "CeruleanCity",
    "VermilionCity",
    "LavenderTown",
    "CeladonCity",
    "SaffronCity",
    "FuchsiaCity",
    "CinnabarIsland",
    "IndigoPlateau",
]

ROUTES = [f"Route{i}" for i in range(1, 26)]


def create_database():
    """Connect to SQLite database and create warps table if it doesn't exist"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Drop existing warps table if it exists
    cursor.execute("DROP TABLE IF EXISTS warps")

    # Create warps table
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS warps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_map TEXT NOT NULL,
        source_map_id INTEGER,
        source_x INTEGER NOT NULL,
        source_y INTEGER NOT NULL,
        x INTEGER,
        y INTEGER,
        destination_map TEXT NOT NULL,
        destination_map_id INTEGER,
        destination_x INTEGER,
        destination_y INTEGER,
        destination_warp_id INTEGER NOT NULL
    )
    """
    )

    conn.commit()
    return conn, cursor


def get_all_maps(cursor):
    """Get all maps from the database"""
    cursor.execute("SELECT id, name FROM maps")
    return {name: id for id, name in cursor.fetchall()}


def parse_map_name_from_file(file_path):
    """Extract map name from file path"""
    file_name = os.path.basename(file_path)
    map_name = os.path.splitext(file_name)[0]
    return map_name


def extract_map_id_from_header(map_name):
    """Extract map ID constant from header file"""
    header_file = Path(f"{MAP_HEADERS_DIR}/{map_name}.asm")
    if not header_file.exists():
        return None

    with open(header_file, "r") as f:
        content = f.read()

    # Look for map_header directive
    match = re.search(r"map_header\s+\w+,\s+(\w+),", content)
    if match:
        return match.group(1)  # Return the constant name, will be converted to ID later

    return None


def get_map_id_from_constant(map_constant, map_to_map_id):
    """Convert a map constant to an integer ID using the mapping"""
    if not map_constant:
        return None

    # Try direct lookup
    if map_constant in map_to_map_id:
        return map_to_map_id[map_constant]

    # Try other formats
    if map_constant.lower() in map_to_map_id:
        return map_to_map_id[map_constant.lower()]

    if map_constant.upper() in map_to_map_id:
        return map_to_map_id[map_constant.upper()]

    return None


def get_map_id_for_map(map_name, cursor):
    """Get map ID for a map from the maps table"""
    # Try exact match first
    cursor.execute("SELECT id FROM maps WHERE name = ?", (map_name,))
    result = cursor.fetchone()
    if result:
        return result[0]

    # Try case-insensitive match
    cursor.execute("SELECT id FROM maps WHERE LOWER(name) = LOWER(?)", (map_name,))
    result = cursor.fetchone()
    if result:
        return result[0]

    # Convert CamelCase to UPPER_CASE_WITH_UNDERSCORES
    upper_with_underscores = convert_camel_to_upper_underscore(map_name)
    cursor.execute("SELECT id FROM maps WHERE name = ?", (upper_with_underscores,))
    result = cursor.fetchone()
    if result:
        return result[0]

    # Convert UPPER_CASE_WITH_UNDERSCORES to CamelCase
    if "_" in map_name:
        camel_case = convert_upper_underscore_to_camel(map_name)
        cursor.execute("SELECT id FROM maps WHERE name = ?", (camel_case,))
        result = cursor.fetchone()
        if result:
            return result[0]

    return None


def convert_camel_to_upper_underscore(name):
    """Convert CamelCase to UPPER_CASE_WITH_UNDERSCORES"""
    # Insert underscore between lowercase and uppercase (e.g. CamelCase -> Camel_Case)
    s1 = re.sub("([a-z])([A-Z])", r"\1_\2", name)
    # Insert underscore between upper and upper-lower (e.g. MapHeader -> Map_Header)
    s2 = re.sub("([A-Z])([A-Z][a-z])", r"\1_\2", s1)
    # Insert underscore between letters and digits (e.g. Route16 -> Route_16)
    s3 = re.sub("([a-zA-Z])([0-9])", r"\1_\2", s2)
    return s3.upper()


def convert_upper_underscore_to_camel(name):
    """Convert UPPER_CASE_WITH_UNDERSCORES to CamelCase"""
    return "".join(word.capitalize() for word in name.lower().split("_"))


def parse_warp_events(content, map_name, cursor, map_to_map_id, map_formats):
    """Parse warp events from the map object file"""
    warps = []

    # Find the warp events section
    warp_section_match = re.search(
        r"def_warp_events(.*?)(?:def_bg_events|def_object_events|\Z)",
        content,
        re.DOTALL,
    )
    if not warp_section_match:
        return warps

    warp_section = warp_section_match.group(1)

    # Extract individual warp events
    warp_pattern = r"warp_event\s+(\d+),\s+(\d+),\s+(\w+),\s+(\d+)"
    warp_matches = re.finditer(warp_pattern, warp_section)

    # Get map constant from header
    map_constant = extract_map_id_from_header(map_name)
    # Convert map constant to integer ID
    map_id = None
    if map_constant:
        map_id = get_map_id_from_constant(map_constant, map_to_map_id)

    # If not found, try direct lookup
    if not map_id:
        map_id = get_map_id_from_mapping(map_name, map_to_map_id)

    for i, match in enumerate(warp_matches):
        x = int(match.group(1))
        y = int(match.group(2))
        destination = match.group(3)
        destination_warp_id = int(match.group(4))

        # Extract destination map ID if it's not LAST_MAP
        destination_map_id = None
        if destination != "LAST_MAP":
            # Get destination map constant from header
            destination_constant = extract_map_id_from_header(destination)
            # Convert destination constant to integer ID
            if destination_constant:
                destination_map_id = get_map_id_from_constant(
                    destination_constant, map_to_map_id
                )

            # If not found, try direct lookup
            if not destination_map_id:
                destination_map_id = get_map_id_from_mapping(destination, map_to_map_id)

        warps.append(
            {
                "source_map": map_name,
                "source_map_id": map_id,
                "source_x": x,
                "source_y": y,
                "destination_map": destination,
                "destination_map_id": destination_map_id,
                "destination_warp_id": destination_warp_id,
                "destination_x": None,  # Will be filled in later
                "destination_y": None,  # Will be filled in later
                "warp_index": i + 1,  # 1-based index
                "is_last_map": 1 if destination == "LAST_MAP" else 0,
            }
        )

    return warps


def find_incoming_warps(all_warps, target_map):
    """Find all warps that lead to the target map"""
    return [warp for warp in all_warps if warp["destination_map"] == target_map]


def determine_parent_location(map_name):
    """Determine the parent location (city, town, route) for a building or area"""
    # Check if this is already a city, town, or route
    if map_name in CITIES_AND_TOWNS or map_name in ROUTES:
        return None

    # Try to match with a city or town
    for location in CITIES_AND_TOWNS:
        # Remove "City" or "Town" suffix for matching
        base_name = (
            location.replace("City", "")
            .replace("Town", "")
            .replace("Island", "")
            .replace("Plateau", "")
        )
        if base_name in map_name:
            return location

    # Try to match with a route
    for route in ROUTES:
        if route in map_name:
            return route

    # Special cases for maps that don't follow the naming convention
    special_cases = {
        "OaksLab": "PalletTown",
        "RedsHouse1F": "PalletTown",
        "RedsHouse2F": "PalletTown",
        "BluesHouse": "PalletTown",
        "Museum1F": "PewterCity",
        "Museum2F": "PewterCity",
        "MtMoon1F": "Route4",
        "MtMoon3F": "Route4",
        "MtMoonB1F": "Route4",
        "RockTunnel1F": "Route10",
        "RockTunnel2F": "Route10",
        "PowerPlant": "Route10",
        "SeafoamIslands1F": "Route20",
        "VictoryRoad1F": "Route23",
        "VictoryRoad2F": "Route23",
        "VictoryRoad3F": "Route23",
        "DiglettsCave": "Route2",
        "ViridianForest": "Route2",
        "PokemonTower1F": "LavenderTown",
        "PokemonTower2F": "LavenderTown",
        "PokemonTower3F": "LavenderTown",
        "PokemonTower4F": "LavenderTown",
        "PokemonTower5F": "LavenderTown",
        "PokemonTower6F": "LavenderTown",
        "PokemonTower7F": "LavenderTown",
        "SilphCo1F": "SaffronCity",
        "SilphCo2F": "SaffronCity",
        "SilphCo3F": "SaffronCity",
        "SilphCo4F": "SaffronCity",
        "SilphCo5F": "SaffronCity",
        "SilphCo6F": "SaffronCity",
        "SilphCo7F": "SaffronCity",
        "SilphCo8F": "SaffronCity",
        "SilphCo9F": "SaffronCity",
        "SilphCo10F": "SaffronCity",
        "SilphCo11F": "SaffronCity",
        "PokemonMansion1F": "CinnabarIsland",
        "PokemonMansion2F": "CinnabarIsland",
        "PokemonMansion3F": "CinnabarIsland",
        "PokemonMansionB1F": "CinnabarIsland",
        "SafariZoneCenter": "FuchsiaCity",
        "SafariZoneEast": "FuchsiaCity",
        "SafariZoneNorth": "FuchsiaCity",
        "SafariZoneWest": "FuchsiaCity",
        "CeruleanCave1F": "CeruleanCity",
        "CeruleanCave2F": "CeruleanCity",
        "CeruleanCaveB1F": "CeruleanCity",
        "UndergroundPathRoute5": "Route5",
        "UndergroundPathRoute6": "Route6",
        "UndergroundPathRoute7": "Route7",
        "UndergroundPathRoute7Copy": "Route7",
        "UndergroundPathRoute8": "Route8",
        "RocketHideoutB1F": "CeladonCity",
        "RocketHideoutB2F": "CeladonCity",
        "RocketHideoutB3F": "CeladonCity",
        "RocketHideoutB4F": "CeladonCity",
        # Additional special cases for remaining unresolved warps
        "BikeShop": "CeruleanCity",
        "MtMoonPokecenter": "Route4",
        "FightingDojo": "SaffronCity",
        "PokemonFanClub": "VermilionCity",
        "SafariZoneGate": "FuchsiaCity",
        "WardensHouse": "FuchsiaCity",
        "CopycatsHouse1F": "SaffronCity",
        "CopycatsHouse2F": "SaffronCity",
        "GameCorner": "CeladonCity",
        "BillsHouse": "Route25",
        "MrFujisHouse": "LavenderTown",
        "MrPsychicsHouse": "SaffronCity",
        "GameCornerPrizeRoom": "CeladonCity",
        "RockTunnelPokecenter": "Route10",
        "NameRatersHouse": "LavenderTown",
        "Daycare": "Route5",
        "Route2TradeHouse": "Route2",
        "CeruleanTradeHouse": "CeruleanCity",
        "ViridianNicknameHouse": "ViridianCity",
        "LavenderCuboneHouse": "LavenderTown",
        "FuchsiaGoodRodHouse": "FuchsiaCity",
        "PewterNidoranHouse": "PewterCity",
        "FuchsiaBillsGrandpasHouse": "FuchsiaCity",
        "FuchsiaMeetingRoom": "FuchsiaCity",
        "CeladonChiefHouse": "CeladonCity",
        "VermilionPidgeyHouse": "VermilionCity",
        "VermilionOldRodHouse": "VermilionCity",
        "SaffronPidgeyHouse": "SaffronCity",
        "ViridianSchoolHouse": "ViridianCity",
        "Route12SuperRodHouse": "Route12",
        "Route16FlyHouse": "Route16",
    }

    return special_cases.get(map_name)


def convert_map_name_to_file_name(map_name):
    """Convert a map name in UPPER_CASE_WITH_UNDERSCORES format to CamelCase file name"""
    if "_" in map_name:
        parts = map_name.split("_")
        return "".join(part.capitalize() for part in parts)
    return map_name


def find_destination_coordinates(source_map, destination_map, destination_warp_id):
    """Find the coordinates of a destination warp by directly parsing the destination map file"""
    # Convert the destination map name to file name format
    destination_file_name = convert_map_name_to_file_name(destination_map)

    # Construct the path to the destination map file
    destination_file = POKEMON_DATA_DIR / f"{destination_file_name}.asm"

    # If the file doesn't exist, try the original name
    if not destination_file.exists():
        destination_file = POKEMON_DATA_DIR / f"{destination_map}.asm"

    # If the file still doesn't exist, return None
    if not destination_file.exists():
        return None, None

    # Read the destination map file
    with open(destination_file, "r") as f:
        content = f.read()

    # Find the warp events section
    warp_section_match = re.search(
        r"def_warp_events(.*?)(?:def_bg_events|def_object_events|\Z)",
        content,
        re.DOTALL,
    )
    if not warp_section_match:
        return None, None

    warp_section = warp_section_match.group(1)

    # Extract individual warp events
    warp_pattern = r"warp_event\s+(\d+),\s+(\d+),\s+(\w+),\s+(\d+)"
    warp_matches = list(re.finditer(warp_pattern, warp_section))

    # Find the warp with the matching ID
    for match in warp_matches:
        warp_id = int(match.group(4))
        if warp_id == destination_warp_id:
            x = int(match.group(1))
            y = int(match.group(2))
            return x, y

    return None, None


def resolve_last_map_warps(all_warps, cursor, map_to_map_id, map_formats):
    """Resolve LAST_MAP references in warps"""
    resolved_warps = []
    resolved_count = 0

    for warp in all_warps:
        if warp["destination_map"] == "LAST_MAP":
            # Try to find incoming warps to this map
            incoming_warps = find_incoming_warps(all_warps, warp["source_map"])
            if incoming_warps:
                # Use the first incoming warp as the destination
                incoming_warp = incoming_warps[0]
                warp["destination_map"] = incoming_warp["source_map"]
                warp["destination_map_id"] = incoming_warp["source_map_id"]
                warp["destination_x"] = incoming_warp["source_x"]
                warp["destination_y"] = incoming_warp["source_y"]
                warp["destination_warp_id"] = 0  # Default warp ID
                resolved_count += 1
                resolved_warps.append(warp)
            else:
                # Try to determine parent location
                parent_location = determine_parent_location(warp["source_map"])
                if parent_location:
                    # Convert parent location to map name format
                    parent_map = convert_map_name_to_constant(parent_location)
                    parent_file = convert_map_name_to_file_name(parent_map)

                    # Check if parent file exists
                    parent_file_path = POKEMON_DATA_DIR / f"{parent_file}.asm"
                    if parent_file_path.exists():
                        # Use parent location as destination
                        warp["destination_map"] = parent_map

                        # Get map ID from map_formats or map_to_map_id
                        parent_map_id = map_formats.get(parent_map)
                        if not parent_map_id:
                            # Try to get map ID from constant
                            parent_map_id = get_map_id_from_constant(
                                parent_map, map_to_map_id
                            )
                            if not parent_map_id:
                                # Try direct lookup
                                parent_map_id = get_map_id_from_mapping(
                                    parent_map, map_to_map_id
                                )

                        warp["destination_map_id"] = parent_map_id
                        warp["destination_x"] = 0  # Default coordinates
                        warp["destination_y"] = 0
                        warp["destination_warp_id"] = 0  # Default warp ID
                        resolved_count += 1
                        resolved_warps.append(warp)
                    else:
                        # If parent file doesn't exist, keep the original with a note
                        resolved_warps.append(warp)
                else:
                    # If no parent location found, keep the original with a note
                    resolved_warps.append(warp)
        else:
            # Not a LAST_MAP reference
            resolved_warps.append(warp)

    print(f"Resolved {resolved_count} LAST_MAP warps")
    return resolved_warps


def main():
    # Create database
    conn, cursor = create_database()

    # Get map formats from maps table
    map_formats = {}
    try:
        cursor.execute("SELECT id, name FROM maps")
        for map_id, map_name in cursor.fetchall():
            # Store the UPPER_CASE format
            map_formats[map_name] = map_id
    except sqlite3.OperationalError:
        # If maps table doesn't exist, continue without it
        print("Warning: 'maps' table not found, continuing without map formats")

    # Create a map name to map ID mapping
    map_to_map_id = {}
    try:
        cursor.execute("SELECT id, name FROM maps")
        for map_id, map_name in cursor.fetchall():
            # Store the original format (ALL_UPPER_CASE_WITH_UNDERSCORES)
            map_to_map_id[map_name] = map_id

            # Store lowercase version
            map_to_map_id[map_name.lower()] = map_id

            # Store CamelCase version
            camel_case = convert_upper_underscore_to_camel(map_name)
            map_to_map_id[camel_case] = map_id

            # Handle floor number variations if present
            if "F" in map_name:
                map_to_map_id[map_name.replace("F", "f")] = map_id
            if "f" in map_name:
                map_to_map_id[map_name.replace("f", "F")] = map_id
    except sqlite3.OperationalError:
        # If maps table doesn't exist, continue without it
        print("Warning: 'maps' table not found, continuing without map ID mapping")

    # Get all map files
    map_files = list(POKEMON_DATA_DIR.glob("*.asm"))
    print(f"Found {len(map_files)} map files")

    # Process each map file
    all_warps = []
    processed_count = 0

    for file_path in map_files:
        map_name = parse_map_name_from_file(file_path)
        if not map_name:
            continue

        # Get map ID from map name
        map_id = map_formats.get(map_name)
        if not map_id:
            # Try to get map ID from the mapping
            map_id = get_map_id_from_mapping(map_name, map_to_map_id)
            if not map_id:
                # If still not found, try to extract from header
                map_constant = extract_map_id_from_header(map_name)
                if map_constant:
                    map_id = get_map_id_from_constant(map_constant, map_to_map_id)

        # Read the map file
        with open(file_path, "r") as f:
            content = f.read()

        # Parse warp events
        warps = parse_warp_events(content, map_name, cursor, map_to_map_id, map_formats)
        all_warps.extend(warps)
        processed_count += 1

    print(f"Processed {processed_count} map files, found {len(all_warps)} warps")

    # Resolve LAST_MAP references
    resolved_warps = resolve_last_map_warps(
        all_warps, cursor, map_to_map_id, map_formats
    )

    # Insert warps into database
    inserted_count = 0
    for warp in resolved_warps:
        # Calculate global coordinates for overworld warps
        x = None
        y = None
        if warp["source_map_id"]:
            try:
                # Get the global coordinates of the map (top-left corner)
                map_x, map_y = get_map_global_coordinates(cursor, warp["source_map_id"])

                if map_x is not None and map_y is not None:
                    # Apply the map offset to the warp coordinates
                    x = map_x + warp["source_x"]
                    y = map_y + warp["source_y"]

                    # Ensure x and y are integers, not None
                    if x is not None and y is not None:
                        x = int(x)
                        y = int(y)
                else:
                    # For maps without coordinates, use local coordinates as global coordinates
                    # This ensures at least some coordinates are set
                    x = warp["source_x"]
                    y = warp["source_y"]
            except sqlite3.OperationalError as e:
                # If tiles table doesn't exist, continue without global coordinates
                # Use local coordinates as fallback
                x = warp["source_x"]
                y = warp["source_y"]
        else:
            # If no map_id, still use local coordinates as global coordinates
            x = warp["source_x"]
            y = warp["source_y"]

        # Use a parameterized query with explicit column names
        cursor.execute(
            """
            INSERT INTO warps (
                source_map, source_map_id, source_x, source_y,
                x, y, destination_map, destination_map_id, 
                destination_x, destination_y, destination_warp_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                warp["source_map"],
                warp["source_map_id"],
                warp["source_x"],
                warp["source_y"],
                x,  # Explicitly pass x
                y,  # Explicitly pass y
                warp["destination_map"],
                warp["destination_map_id"],
                warp["destination_x"],
                warp["destination_y"],
                warp["destination_warp_id"],
            ),
        )
        inserted_count += 1

        # Commit more frequently to ensure data is saved
        if inserted_count % 50 == 0:
            conn.commit()
            print(f"Committed {inserted_count} warps so far")

    # Final commit
    conn.commit()
    print(f"Final commit: Successfully exported {inserted_count} warps to pokemon.db")

    # Close the database connection
    conn.close()


def get_map_id_from_mapping(map_name, map_to_map_id):
    """Get map ID for a map from the mapping"""
    # Try exact match
    if map_name in map_to_map_id:
        return map_to_map_id[map_name]

    # Try lowercase
    if map_name.lower() in map_to_map_id:
        return map_to_map_id[map_name.lower()]

    # Try uppercase
    if map_name.upper() in map_to_map_id:
        return map_to_map_id[map_name.upper()]

    # Try converting CamelCase to UPPER_CASE_WITH_UNDERSCORES
    upper_with_underscores = convert_camel_to_upper_underscore(map_name)
    if upper_with_underscores in map_to_map_id:
        return map_to_map_id[upper_with_underscores]

    # Try converting UPPER_CASE_WITH_UNDERSCORES to CamelCase
    if "_" in map_name:
        camel_case = convert_upper_underscore_to_camel(map_name)
        if camel_case in map_to_map_id:
            return map_to_map_id[camel_case]

    return None


def convert_map_name_to_constant(map_name):
    """Convert a map name in CamelCase format to UPPER_CASE_WITH_UNDERSCORES constant"""
    # Add underscore before each uppercase letter except the first one
    constant = "".join(
        ["_" + c if c.isupper() and i > 0 else c for i, c in enumerate(map_name)]
    )
    return constant.upper()


def get_map_global_coordinates(cursor, map_id):
    """Get the global coordinates of a map (top-left corner)"""
    if not map_id:
        return None, None

    try:
        cursor.execute(
            """
            SELECT MIN(x), MIN(y) 
            FROM tiles 
            WHERE map_id = ?
            """,
            (map_id,),
        )
        result = cursor.fetchone()
        if result:
            return result[0], result[1]
    except sqlite3.OperationalError:
        # If tiles table doesn't exist, return None
        pass

    return None, None


if __name__ == "__main__":
    main()
