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
rebuilds `pokemon.db`. The pipeline validates that the generated script
candidate, diagnostics, in-game trade, tile override, boulder target, and
spin/arrow tile tables are present before reporting success.

To run the raw canonical pipeline without the setup wrapper:

```bash
npm run export
```

This runs `export_scripts/reprocess.py`, which:

1. Rebuilds `pokemon.db` from the source data in the correct order.
2. Emits neutral script event candidates and in-game trade definitions from
   supported ASM state machines.
3. Leaves the generated SQLite artifact at `./pokemon.db` for downstream apps
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
- `script_event_candidates.json`: generated neutral script behavior candidates
  for downstream importers. Current adapters cover the Safari Zone gate flow,
  static wild battles, the Pokemon Tower Marowak ghost special battle, Fishing
  Guru rod gifts, the Pokemon Fan Club Bike Voucher state machine, Pokemon Fan
  Club paired fan boast toggles, Fighting Dojo prize Pokemon and Karate Master
  special battle flow, Game Corner coin gifts and coin purchase, the Game
  Corner poster switch, the Silph Co. 9F nurse heal branch, side-effect-free
  Yes/No informational dialogue, Pokemon
  Mansion secret switch toggles, flag-gated and badge-or-event informational
  dialogue, Snorlax wake-up battle scripts, gym leader
  pre-battle/post-TM advice branches, Cinnabar Gym custom quiz-trainer click
  text, standard trainer after-battle object drops and progression-flag side
  effects, badge-bit Gym Guide
  branches, pure event-flag map script side effects, conditional map-load flag
  mirrors, one-shot map-load missable-object visibility scripts, Oak aides,
  simple item gifts, the Vermilion City S.S. Anne guard coordinate gate, Elite
  Four room entrance guard coordinate gates, upstairs/binocular facing-gated
  text, and simple Pokemon gifts where the ASM has one clear reward and
  completion marker.
  Day Care is marked as runtime-covered diagnostics because its source script
  is a multi-step party, storage, money, growth, and move-learning state
  machine rather than a linear script candidate.
  Generic flag-gated dialogue accepts both local and same-file global text
  labels, but deliberately leaves rival labels in diagnostics for a bespoke
  rival encounter adapter.
  Source text-pointer switches that only choose between alternate map text
  tables can be marked runtime-covered when downstream games represent them as
  explicit flag-gated dialogue branches.
  Downstream-authored flows can also be marked covered diagnostics when a source
  label is only a wrapper around behavior already owned by the runtime, such as
  Oak Lab state dispatch, Mt. Moon fossil-area encounter suppression, Pewter
  City's pre-Brock escort wrapper, or the S.S. Anne departure callback.
  Pallet Town's Daisy map-load script is marked runtime-covered because it has
  independent flag and missable-object state effects that downstream servers
  should apply authoritatively during map-load handling rather than as one
  linear cutscene.
  Pokemon Tower 7F's Rocket exit movement table is marked runtime-covered
  because it is keyed by the defeated trainer sprite index and the player's
  battle tile; downstream games should attach those source movements and object
  cleanup to standard trainer post-win handling.
  Cinnabar Gym's quiz wrong-answer trainer handoff is marked runtime-covered
  because it is part of the quiz/trainer/gate state machine. Name Rater's
  yes/no helper is marked runtime-covered because the source behavior requires
  party selection, original-trainer validation, and nickname editing UI rather
  than a linear script candidate.
  Seafoam Islands boulder holes, strong currents, surf-blocking, and Route 20
  boulder-reset scripts are also marked runtime-covered because they are
  multi-map movement/object-state systems rather than linear cutscene
  candidates.
  Source scripts that only toggle whether an NPC faces the player during
  interaction are marked as presentation runtime diagnostics.
- `script_event_in_game_trades.json`: generated neutral in-game trade
  definitions joined from `data/events/trades.asm` and map script call sites.
  Unused source-only trades are preserved as inactive diagnostics instead of
  unsupported script backlog.
- `script_event_tile_overrides.json`: generated neutral tile replacement
  candidates for map-load event state such as Elite Four exit blocks, Silph Co.
  Card Key doors, Victory Road boulder switches, Vermilion Gym's trash door,
  the Game Corner Rocket Hideout entrance, and Pokemon Mansion switch doors.
- `script_event_boulder_targets.json`: generated neutral Victory Road boulder
  switch/hole targets from source `CheckBoulderCoords` tables, including the
  original event flags and `HS_*` missable-object constants for downstream
  runtime resolution.
- `missable_objects`: generated SQLite table that resolves Red/Blue `HS_*`
  hide/show constants to map/object constants, object names, and initial
  visibility for downstream runtimes.
- `spin_tiles`: generated SQLite table of source-backed forced-movement tiles
  from `map_coord_movement` script data, suitable for downstream runtime import.
- `script_event_ir.json` and `script_event_diagnostics.json`: generated script
  block inventory and adapter coverage diagnostics. Ordinary trainer battles
  are reported as runtime-covered because they are exported through the trainer
  header and party tables rather than generated script JSON.
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
