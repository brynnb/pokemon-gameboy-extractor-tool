# Pokemon Game Boy Extractor Tool

This repo extracts Pokemon Red/Blue data from the `pokered` disassembly into a
SQLite database (`pokemon.db`) and provides a lightweight Phaser viewer for
inspection. The generated database is designed as a source artifact for games
and tools that need canonical Pokemon Red/Blue maps, tiles, scripts, trainers,
encounters, items, moves, and Pokemon data.

The extractor does not distribute ROM data. It reads source/data files from the
`pokemon-game-data` submodule.

## Setup

```bash
git clone https://github.com/brynnb/pokemon-gameboy-extractor-tool.git --recurse-submodules
cd pokemon-gameboy-extractor-tool
npm install
```

If the submodule is missing:

```bash
git submodule update --init --recursive
```

Install RGBDS so `rgbgfx` is available for tileset conversion:

```bash
# macOS
brew install rgbds

# Ubuntu/Debian
sudo apt-get install rgbds
```

## Full Export Pipeline

For a fresh setup and full rebuild, run:

```bash
npm run generate
```

This command updates the `pokemon-game-data` submodule, creates a local
Python virtual environment, installs extractor Python dependencies, and
rebuilds `pokemon.db`.

To run the raw canonical pipeline without the setup wrapper:

```bash
npm run export
```

This runs `export_scripts/reprocess.py`, which:

1. Rebuilds `pokemon.db` from the source data in the correct order.
2. Leaves the generated SQLite artifact at `./pokemon.db` for downstream apps
   or importers to consume.

## Viewer

The Node/Phaser viewer is useful for inspecting extracted tiles and maps:

```bash
npm run start:all
```

Or run the pieces separately:

```bash
npm run dev
cd pokemon-phaser && npm run dev
```

## Important Files

- `pokemon.db`: generated SQLite source artifact.
- `export_scripts/reprocess.py`: canonical full pipeline.
- `export_scripts/*.py`: individual extractors for maps, tiles, objects,
  warps, text, Pokemon, moves, items, trainers, encounters, hidden objects, and
  scripts.
- `GAME_DATA_REFERENCE.md`: table reference and engine notes.
- `pokemon-game-data/`: source data submodule.

## Validation

For code changes, run syntax checks on touched Python files and rerun the
smallest relevant extractor. For pipeline changes, run:

```bash
npm run export
sqlite3 pokemon.db ".tables"
```
