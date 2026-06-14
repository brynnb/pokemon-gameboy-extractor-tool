# Pokemon Game Boy Extractor Tool

This repo extracts Pokemon Red/Blue data from the `pokered` disassembly into a
local SQLite database (`pokemon.db`) and companion JSON/assets. The output is
intended for games, tools, editors, and importers that need canonical Gen 1 map,
tile, object, trainer, encounter, item, move, Pokemon, dialogue, and script
source data.

The extractor is project-neutral. It does not know about any downstream game
repo, does not copy files into one, and does not ship ROM data.

![Pokemon overworld viewer demo](https://github.com/user-attachments/assets/e4602729-29bb-4ee4-94f6-446c90dd2a89)

## What It Produces

`npm run generate` produces:

- `pokemon.db`: generated SQLite source-data artifact.
- `script_event_candidates.json`: neutral generated script-event candidates.
- `script_event_conditional_dialogue.json`: neutral conditional dialogue rows.
- `script_event_in_game_trades.json`: source in-game trade definitions.
- `script_event_ir.json` and `script_event_diagnostics.json`: script inventory
  and coverage diagnostics.
- `export_scripts/tile_images/*.png`: generated 16x16 tile PNGs.
- `pokemon-phaser/public/viewer-data/**`: generated static JSON for the viewer.
- `pokemon-phaser/public/viewer-assets/**`: copied tile and sprite PNGs for the
  viewer.

Tracked source-derived reference files include:

- `script_event_boulder_targets.json`
- `script_event_tile_overrides.json`

These outputs preserve source constants and source behavior where possible.
Downstream projects are responsible for mapping them to app-specific IDs,
runtime state, persistence, UI, networking, battle mechanics, and script
execution.

## Setup

```bash
git clone https://github.com/brynnb/pokemon-gameboy-extractor-tool.git --recurse-submodules
cd pokemon-gameboy-extractor-tool
npm install
```

`npm install` initializes the `pokemon-game-data` submodule and installs the
nested offline viewer dependencies.

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

## Generate Data

For a fresh setup and full rebuild:

```bash
npm run generate
```

This command:

1. updates the `pokemon-game-data` submodule
2. creates or reuses a Python virtual environment
3. installs Python dependencies from `requirements.txt`
4. rebuilds `pokemon.db`
5. emits generated script JSON artifacts
6. writes offline viewer JSON/assets
7. validates required generated script tables

To run just the canonical extractor pipeline:

```bash
npm run export
```

That runs `export_scripts/reprocess.py`.

## Offline Viewer

The Phaser viewer inspects generated maps, tiles, items, NPCs, and warps from
static JSON/assets. It does not need a separate app server or an open SQLite
connection.

Run the viewer:

```bash
npm run viewer
```

Build the viewer:

```bash
npm run viewer:build
```

If viewer data is missing, rerun `npm run generate`.

## Data Model Notes

Pokemon Red/Blue stores maps as block grids:

1. 8x8 source tiles are stored as Game Boy 2bpp graphics.
2. One in-game square is 16x16 pixels, or a 2x2 tile group.
3. Blocksets group source tiles into 4x4-tile blocks.
4. `.blk` map files store block references.
5. The extractor expands those references into final `tiles` rows and generated
   `tile_images` PNGs.

Overworld maps are stitched into global coordinates for inspection and
downstream rendering. Local map coordinates are still preserved and should be
used for source-script behavior such as trainer sight, coordinate triggers,
object interaction, and warps.

## More Documentation

See `DOCUMENTATION.md` for the full pipeline, schema, map coordinate model,
script candidate model, viewer data, validation, and maintenance roadmap.

## Important Files

- `export_scripts/reprocess.py`: canonical full pipeline.
- `export_scripts/export_viewer_data.py`: static viewer JSON/asset exporter.
- `export_scripts/*.py`: individual extractors.
- `DOCUMENTATION.md`: full project documentation.
- `pokemon-game-data/`: source data submodule.
- `pokemon-phaser/`: offline Phaser viewer.

## Validation

For pipeline changes:

```bash
npm run export
sqlite3 pokemon.db ".tables"
sqlite3 pokemon.db "SELECT COUNT(*) FROM tiles"
```

For viewer changes:

```bash
python3 export_scripts/export_viewer_data.py
cd pokemon-phaser && npm run build
```

For focused Python changes:

```bash
python3 -m py_compile export_scripts/<script>.py
```
