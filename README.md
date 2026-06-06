# Pokemon Game Boy Extractor Tool

This repo extracts Pokemon Red/Blue data from the `pokered` disassembly into a
SQLite database (`pokemon.db`) and provides a lightweight Phaser viewer for
inspection. CaptureQuest consumes the generated SQLite artifact through its Go
importer and writes runtime data to Postgres.

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

Run the canonical pipeline:

```bash
npm run export
```

This runs `export_scripts/reprocess.py`, which:

1. Rebuilds `pokemon.db` from the source data in the correct order.
2. Copies it to `../capture-quest/public/phaser/pokemon.db` when that sibling
   repo exists.
3. Runs CaptureQuest's `server/cmd/import-phaser` importer so the copied SQLite
   artifact syncs into Postgres.

Useful environment overrides:

```bash
# Point at a non-sibling CaptureQuest checkout
CAPTURE_QUEST_ROOT=/path/to/capture-quest npm run export

# Rebuild pokemon.db but skip the CaptureQuest import
RUN_CAPTUREQUEST_IMPORT=0 npm run export
```

CaptureQuest's importer reads `DATABASE_URL` for Postgres when it is set.

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
RUN_CAPTUREQUEST_IMPORT=0 npm run export
sqlite3 pokemon.db ".tables"
```
