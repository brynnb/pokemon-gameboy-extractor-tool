#!/usr/bin/env python3
import sqlite3
import os
from pathlib import Path

# Constants
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "pokemon.db"
SEED_PATH = PROJECT_ROOT / "reseed_all.sql"

def generate_seed():
    if not os.path.exists(DB_PATH):
        print(f"Error: {DB_PATH} not found.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print("Generating MySQL seed file...")

    with open(SEED_PATH, "w") as f:
        f.write("SET FOREIGN_KEY_CHECKS = 0;\n")
        f.write("TRUNCATE TABLE phaser_tiles;\n")
        f.write("TRUNCATE TABLE phaser_objects;\n")
        f.write("TRUNCATE TABLE phaser_warps;\n")
        f.write("SET FOREIGN_KEY_CHECKS = 1;\n\n")

        # Seed Tiles
        print("Processing tiles...")
        cursor.execute("SELECT x, y, local_x, local_y, map_id, tile_image_id, is_walkable, collision_type FROM tiles")
        tiles = cursor.fetchall()
        
        if tiles:
            f.write("INSERT INTO phaser_tiles (x, y, local_x, local_y, map_id, tile_image_id, is_walkable, collision_type) VALUES\n")
            batch_size = 1000
            for i in range(0, len(tiles), batch_size):
                batch = tiles[i:i+batch_size]
                values = []
                for t in batch:
                    values.append(f"({t[0]}, {t[1]}, {t[2]}, {t[3]}, {t[4]}, {t[5]}, {t[6]}, {t[7]})")
                
                line = ",\n".join(values)
                if i + batch_size >= len(tiles):
                    f.write(line + ";\n\n")
                else:
                    f.write(line + ";\n")
                    f.write("INSERT INTO phaser_tiles (x, y, local_x, local_y, map_id, tile_image_id, is_walkable, collision_type) VALUES\n")

        # Seed Objects
        print("Processing objects...")
        cursor.execute("SELECT x, y, map_id, object_type, sprite_name, name, item_id, action_type, action_direction, local_x, local_y, movement_type FROM objects")
        objects = cursor.fetchall()
        
        if objects:
            f.write("INSERT INTO phaser_objects (x, y, map_id, object_type, sprite_name, name, item_id, action_type, action_direction, local_x, local_y, movement_type) VALUES\n")
            values = []
            for o in objects:
                x = o[0] if o[0] is not None else "NULL"
                y = o[1] if o[1] is not None else "NULL"
                map_id = o[2] if o[2] is not None else "NULL"
                obj_type = f"'{o[3]}'" if o[3] is not None else "NULL"
                sprite = f"'{o[4]}'" if o[4] is not None else "NULL"
                name = f"'{o[5]}'" if o[5] is not None else "NULL"
                item_id = o[6] if o[6] is not None else "NULL"
                action_type = f"'{o[7]}'" if o[7] is not None else "NULL"
                action_dir = f"'{o[8]}'" if o[8] is not None else "NULL"
                local_x = o[9] if o[9] is not None else "NULL"
                local_y = o[10] if o[10] is not None else "NULL"
                move_type = f"'{o[11]}'" if o[11] is not None else "'LAND'"
                
                values.append(f"({x}, {y}, {map_id}, {obj_type}, {sprite}, {name}, {item_id}, {action_type}, {action_dir}, {local_x}, {local_y}, {move_type})")
            
            f.write(",\n".join(values) + ";\n\n")

        # Seed Warps
        print("Processing warps...")
        cursor.execute("SELECT source_map_id, x, y, destination_map_id, destination_map, destination_x, destination_y FROM warps")
        warps = cursor.fetchall()

        if warps:
            f.write("INSERT INTO phaser_warps (source_map_id, x, y, destination_map_id, destination_map, destination_x, destination_y) VALUES\n")
            values = []
            for w in warps:
                source_id = w[0] if w[0] is not None else "NULL"
                x = w[1] if w[1] is not None else "NULL"
                y = w[2] if w[2] is not None else "NULL"
                dest_id = w[3] if w[3] is not None else "NULL"
                dest_map = f"'{w[4]}'" if w[4] is not None else "NULL"
                dest_x = w[5] if w[5] is not None else "NULL"
                dest_y = w[6] if w[6] is not None else "NULL"

                values.append(f"({source_id}, {x}, {y}, {dest_id}, {dest_map}, {dest_x}, {dest_y})")

            f.write(",\n".join(values) + ";\n")

    conn.close()
    print(f"✅ Seed file generated at {SEED_PATH}")

if __name__ == "__main__":
    generate_seed()
