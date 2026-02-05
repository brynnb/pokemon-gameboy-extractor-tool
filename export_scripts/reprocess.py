#!/usr/bin/env python3
import subprocess
import os
import sys

scripts = [
    "export_map.py",
    "update_zone_coordinates.py",
    "create_zones_and_tiles.py",
    "export_objects.py",
    "update_object_coordinates.py"
]

def run_script(script_name):
    print(f"\n>>> Running {script_name}...")
    try:
        # Use sys.executable to ensure we use the same python interpreter
        subprocess.run([sys.executable, script_name], check=True)
    except subprocess.CalledProcessError as e:
        print(f"!!! Error running {script_name}: {e}")
        sys.exit(1)

def main():
    # Change to the script's directory so it can find the other scripts
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    for script in scripts:
        run_script(script)
        
    print("\n✅ All reprocessing steps completed successfully!")

if __name__ == "__main__":
    main()
