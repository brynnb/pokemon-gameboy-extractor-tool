# Extractor architecture and data reference

This is the technical reference for the Pokémon Game Boy Extractor Tool. The
root [README.md](README.md) is the quick start, and
[MIGRATING.md](MIGRATING.md) describes consumer-facing changes from the older
unversioned artifacts.

## Scope and design

The extractor reads the pinned `pokemon-game-data` (`pret/pokered`) source tree
and publishes a reusable source-data release. It intentionally separates four
concerns:

1. **Source extraction** parses Red/Blue assembly, binary map data, graphics,
   and constants into normalized relational/JSON records.
2. **Catalog and provenance** records release identity, extractor/source Git
   revisions, source hashes, and source relationships already exposed by
   extracted rows.
3. **Media conversion** decodes supported graphics to PNG and optionally
   renders source-engine audio to FLAC/Ogg.
4. **Consumption** belongs to downstream games, editors, importers, and the
   bundled read-only viewer. Runtime policy is not embedded in the core data.

The repository does not contain a game ROM. The audio workflow builds a small
GBS music-player container around the source audio engine; it does not require
or distribute a retail ROM image.

## Requirements

### Canonical extraction

- Git with submodule support.
- Python 3.10 or newer, with virtual-environment support recommended.
- Pillow, installed from `requirements.txt`.
- RGBDS with `rgbgfx` for Game Boy planar graphics conversion.
- Node.js/npm for the convenience scripts and offline viewer.

The setup path is:

```bash
git clone --recurse-submodules https://github.com/brynnb/pokemon-gameboy-extractor-tool.git
cd pokemon-gameboy-extractor-tool
npm run setup
npm run generate
```

If the submodule was omitted during clone:

```bash
git submodule update --init --recursive
```

The supported Python range is 3.10 or newer. The npm workflows require Node.js
20.19 or newer and npm 10 or newer; `.nvmrc` pins the tested Node release.
`npm run setup` uses `npm ci` for the viewer's locked dependency tree.

`generate.sh` manages `.venv` by default and installs the exact Pillow version
from `requirements.txt`. Set `PYTHON_VENV` to use a different environment. A
caller-provided environment is never deleted when setup fails.

### Optional audio rendering

Audio rendering additionally needs:

- RGBDS (`rgbasm` and `rgblink`);
- `gbsplay`; and
- FFmpeg built with FLAC and `libvorbis` support.

The renderer checks for these commands before starting. It surfaces emulator
warnings; `--strict-emulator-warnings` turns otherwise non-fatal warnings into
errors.

## Canonical pipeline

`npm run export` runs `export_scripts/reprocess.py`. Its order is intentional:

1. map/tileset catalog and connections;
2. warps and stitched overworld positions;
3. final 16×16 map tiles and tile images;
4. items, objects, and object coordinates;
5. species/evolutions, moves, text, learnsets, encounters, and trainers;
6. hidden objects, map scripts, and neutral script candidates;
7. normalized audio manifest/catalog;
8. complete graphics catalog and deterministic planar decodes;
9. release/run/source/entity provenance metadata; and
10. viewer JSON/assets.

Release metadata runs after every database-producing exporter so its entity
catalog sees the finished schema. Viewer export runs last because it consumes
the completed database and generated tile images.

### Staging and publication

Every managed output is generated at a private sibling staging path. The
pipeline then validates the staged database and companion bundles. With
`--with-audio`, all 561 FLAC/Ogg pairs and their render manifest join that same
staged release. On success, companions are renamed into place and the database
is installed last as the release commit marker. If a handled publication error
occurs, already replaced outputs are rolled back and recoverable backups are
retained if rollback itself cannot finish.

Consequences for automation:

- a failed exporter or validation does not overwrite the previous successful
  release;
- configured outputs should remain on filesystems that support atomic rename;
- consumers should treat the database replacement as the indication that the
  companion bundle is current; and
- individual exporter scripts are useful for development, but bypass the
  all-artifact staging contract.

## Output layout

| Default path | Path semantics | Description |
| --- | --- | --- |
| `pokemon.db` | SQLite file | Canonical relational artifact. |
| `script_event_candidates.json` | JSON file | Neutral recognized script behaviors. |
| `script_event_ir.json` | JSON file | Source script-block inventory and references. |
| `script_event_diagnostics.json` | JSON file | Generated/covered/unsupported coverage records. |
| `script_event_in_game_trades.json` | JSON file | Source trade definitions and call sites. |
| `script_event_tile_overrides.json` | JSON file | Source-backed dynamic tile replacements. |
| `script_event_boulder_targets.json` | JSON file | Victory Road boulder target relationships. |
| `script_event_object_visibility.json` | JSON file | Event-conditioned object visibility records. |
| `script_event_conditional_dialogue.json` | JSON file | Prioritized conditional dialogue branches. |
| `audio_manifest.json` | JSON file | Versioned audio catalog and logical output paths. |
| `build/graphics/graphics-catalog.json` | Relative to graphics root | Portable hashes and paths for source graphics and decoded derivatives. |
| `build/graphics/decoded/**` | Relative to graphics root | PNGs decoded when authored `.1bpp`/`.2bpp` sources exist. |
| `export_scripts/tile_images/**` | Relative to repository | Deduplicated 16×16 map-square PNGs. |
| `pokemon-phaser/public/viewer-data/**` | Viewer-local | Static JSON split by map where appropriate. |
| `pokemon-phaser/public/viewer-assets/**` | Viewer-local | Tile and sprite assets needed by the viewer. |

Source graphics are cataloged in the database instead of being redundantly
copied. `graphic_assets.path_scope` tells consumers whether `relative_path` is
relative to the repository (`repository`) or the configured graphics output
root (`graphics_output`).

Audio manifest/DB paths such as `/sound/pokemon/music/pallet_town.ogg` are
logical web paths. When rendering, the leading slash is removed and the path is
created underneath `--out-dir`; traversal components are rejected.

## Configuration

During checkout-based execution, relative overrides are resolved against the
repository root. Installed entry points configure absolute workspace defaults;
prefer absolute output overrides when invoking an installed command. The
central pipeline stages and atomically publishes each configured managed
output.

| Environment variable | Default |
| --- | --- |
| `POKEMON_EXTRACTOR_WORKSPACE` | Current directory (installed commands only) |
| `POKEMON_EXTRACTOR_PROJECT_ROOT` | extractor checkout/workspace root |
| `POKEMON_EXTRACTOR_GAME_DATA_ROOT` | `pokemon-game-data` |
| `POKEMON_EXTRACTOR_DB` | `pokemon.db` |
| `POKEMON_EXTRACTOR_TILE_IMAGE_DIR` | `export_scripts/tile_images` |
| `POKEMON_EXTRACTOR_GRAPHICS_DIR` | `build/graphics` |
| `POKEMON_EXTRACTOR_AUDIO_DIR` | `build/audio` |
| `POKEMON_EXTRACTOR_AUDIO_MANIFEST` | `audio_manifest.json` |
| `POKEMON_EXTRACTOR_VIEWER_PUBLIC_DIR` | `pokemon-phaser/public` |
| `POKEMON_EXTRACTOR_VIEWER_DATA_DIR` | `<viewer-public>/viewer-data` |
| `POKEMON_EXTRACTOR_VIEWER_ASSET_DIR` | `<viewer-public>/viewer-assets` |
| `POKEMON_EXTRACTOR_SCRIPT_EVENT_CANDIDATES` | `script_event_candidates.json` |
| `POKEMON_EXTRACTOR_SCRIPT_EVENT_IR` | `script_event_ir.json` |
| `POKEMON_EXTRACTOR_SCRIPT_EVENT_DIAGNOSTICS` | `script_event_diagnostics.json` |
| `POKEMON_EXTRACTOR_SCRIPT_EVENT_TRADES` | `script_event_in_game_trades.json` |
| `POKEMON_EXTRACTOR_SCRIPT_EVENT_TILE_OVERRIDES` | `script_event_tile_overrides.json` |
| `POKEMON_EXTRACTOR_SCRIPT_EVENT_BOULDER_TARGETS` | `script_event_boulder_targets.json` |
| `POKEMON_EXTRACTOR_SCRIPT_EVENT_OBJECT_VISIBILITY` | `script_event_object_visibility.json` |
| `POKEMON_EXTRACTOR_SCRIPT_EVENT_CONDITIONAL_DIALOGUE` | `script_event_conditional_dialogue.json` |

`SOURCE_DATE_EPOCH` controls the recorded schema/run epoch. If unset, the
source submodule commit timestamp is used. The run ID is a SHA-256 identity
derived from schema inputs, exact extractor/source revisions, the epoch, and
the cataloged source-tree hash; wall-clock time is never used.

### Installed command-line entry points

The project is installable with `python3 -m pip install .` and exposes:

- `pokemon-gameboy-extract`;
- `pokemon-gameboy-catalogue-graphics`; and
- `pokemon-gameboy-render-audio`; and
- `pokemon-gameboy-adapt-capturequest`.

Installed commands treat the current directory as the checkout/workspace root.
Set `POKEMON_EXTRACTOR_WORKSPACE` to point them at a different checkout. Normal
`POKEMON_EXTRACTOR_*` output overrides still take precedence.

## Relational database

Consumers should enable SQLite foreign-key enforcement when opening the file:

```sql
PRAGMA foreign_keys = ON;
```

### Schema and release identity

- `schema_metadata` declares `pokemon-gameboy-extractor`, its schema version,
  minimum reader version, and deterministic application epoch.
- `game_releases` has distinct Red and Blue rows with build defines.
- `extraction_runs` records the canonical run ID, revisions, epoch, portable
  source root, source-tree hash, file count, byte count, and a SHA-256 of the
  exact generator worktree inputs plus an explicit dirty-state bit. Canonical
  outputs and the upstream source submodule are excluded from that generator
  hash because their content is recorded separately.
- `extraction_run_releases` links the run to both releases.
- `source_files` catalogs submodule source files using repository-relative
  paths, SHA-256, size, and a format-neutral file type.
- `extracted_entities` and `entity_provenance` link rows that expose direct or
  JSON source paths to exact cataloged files.
- `extracted_tables` and `table_provenance` cover every generated table with
  its conservative upstream source set, including derived rows that do not
  carry a single exact source path.

Always feature-detect using `schema_metadata`, not a guessed table count:

```sql
SELECT schema_name, schema_version, minimum_reader_version
FROM schema_metadata;
```

Example provenance lookup:

```sql
SELECT ep.entity_table, ep.entity_key, ep.source_path,
       ep.source_column, ep.relationship
FROM entity_provenance AS ep
WHERE ep.entity_table = 'dialogue_text'
ORDER BY ep.entity_key, ep.source_path;
```

### Maps, tiles, and objects

The map model includes `maps`, `tilesets`, `map_connections`, `blocksets`,
`tileset_tiles`, `collision_tiles`, `tiles_raw`, `overworld_map_positions`,
`tile_images`, and `tiles`.

Red/Blue graphics have three spatial layers:

1. a planar source tile is 8×8 pixels;
2. a rendered map square is a 2×2 group of source tiles (16×16 pixels); and
3. a map block is a 4×4 group of source tiles (32×32 pixels).

`.blk` maps contain block indices. The extractor retains the raw block layer
and expands it into final squares. `tiles.local_x/local_y` preserve source map
space; stitched coordinates place the connected overworld maps in one global
space. Use local coordinates for source events and global coordinates for an
overworld view.

`tilesets.grass_tile_id` preserves the native 8×8 grass sample declared by
each tileset header. Each expanded square also exposes
`tiles.raw_foot_tile_id` (the bottom-left collision sample) and
`tiles.raw_encounter_tile_id` (the bottom-right wild-encounter sample). These
source identities are stable across deduplicated PNG catalog rebuilds; consumers
must not infer gameplay terrain from `tile_image_id`.

Connections are normalized in `map_connections` with numeric map foreign keys,
direction, and block offset. The convenience connection columns on `maps` also
contain numeric map IDs. All 24 tileset constants are present; aliases point to
their physical source through `tilesets.source_tileset_id`.

Object/runtime tables include `objects`, `warps`, `warp_events`,
`hidden_items`, `hidden_coins`, `hidden_objects`, `missable_objects`,
`map_music`, `map_scripts`, `npc_movement_data`, `coordinate_triggers`,
`event_flags`, and `spin_tiles`. Hidden-object rows resolve to map IDs; the two
global Good Rod encounter rows are intentionally not map-specific.

Every source warp has a `warp_sources` row and a map foreign key. Fixed
destinations expose `destination_map_id` and destination coordinates. The
source engine's `LAST_MAP` sentinel is preserved as
`destination_kind = 'last-map'` with a null destination map: it is runtime
state, not a static map relationship, and the extractor does not invent one.

### Species, evolutions, moves, and encounters

`pokemon` contains the 151 Pokédex species. Evolution is a one-to-many
relationship in `pokemon_evolutions`:

```sql
SELECT source.name, evolution.method, evolution.level,
       item.name AS item_name, target.name AS target_name
FROM pokemon_evolutions AS evolution
JOIN pokemon AS source ON source.id = evolution.source_pokemon_id
JOIN pokemon AS target ON target.id = evolution.target_pokemon_id
LEFT JOIN items AS item ON item.id = evolution.item_id
WHERE source.name = 'EEVEE'
ORDER BY evolution.source_order;
```

The old scalar evolution columns remain as a compatibility hint containing
only the first source relationship. New consumers must use
`pokemon_evolutions`, especially for branching species.

`moves` contains exactly IDs 1 through 165 and retains the source constant in
`constant_name`. `pokemon_learnset`, `pokemon_tmhm`, item TM/HM references,
Pokémon default moves, and audio move relationships are validated against that
complete key set. Default moves use integer `default_move_*_id` foreign keys;
the corresponding source constants remain available in compatibility
`default_move_*_name` columns and in ordered form through
`pokemon_default_moves`.

`wild_encounters` uses one-based `slot_index`. Grass and water groups are
materialized separately for `version = 'red'` and `version = 'blue'`, with ten
complete slots per populated map/type/release group. Shared fishing data uses
`version = 'both'`. Query the version explicitly:

```sql
SELECT slot_index, pokemon_name, level
FROM wild_encounters
WHERE map_id = ? AND encounter_type = 'grass' AND version = 'red'
ORDER BY slot_index;
```

Related gameplay tables include `items`, `trainer_classes`, `trainer_parties`,
`trainer_party_pokemon`, `trainer_headers`, `dialogue_text`, and
`text_pointers`.

### Neutral script data

`script_event_ir_blocks` inventories source blocks and detected references.
`script_event_candidates` represents recognized behavior with neutral trigger,
condition, and action JSON. The JSON remains convenient transport data, while
`script_event_candidate_actions`, `script_event_candidate_conditions`,
`script_event_candidate_references`, and `script_event_ir_references` provide
normalized, queryable projections with map foreign keys. Companion relationship
tables cover trades, tile overrides, boulder targets, object visibility, and
conditional dialogue. Map-facing script, movement, trigger, text-pointer,
trainer-header, candidate, IR, and diagnostic rows all resolve through
`maps.id` rather than relying on spelling-compatible names.

Diagnostics have three statuses:

- `generated`: a neutral candidate/relationship was emitted;
- `covered`: another exported table or an explicitly identified runtime system
  owns the behavior; and
- `unsupported`: source behavior was detected but is not yet represented.

Unsupported is intentionally visible; consumers should not silently substitute
an unrelated dialogue or guessed behavior.

The generic hook in `runtime_profiles.py` deep-copies neutral records before
applying an explicitly supplied profile. CaptureQuest compatibility is isolated
in `adapters/capturequest.py` and is never imported by the canonical pipeline.
A downstream migration can opt in without changing the neutral source:

```python
from adapters.capturequest import PROFILE
from runtime_profiles import apply_candidate_profile, apply_diagnostic_profile

capturequest_candidates = apply_candidate_profile(neutral_candidates, PROFILE)
capturequest_diagnostics = apply_diagnostic_profile(neutral_diagnostics, PROFILE)
```

With the repository checkout as the working directory, add `export_scripts` to
`PYTHONPATH` before using those imports. New projects should define their own
duck-typed profile or consume the neutral vocabulary directly.

### CaptureQuest schema-v2 consumer

`adapters/capturequest_v2.py` is a complete downstream import boundary rather
than a canonical exporter. Its contract is separately versioned as
`capturequest-pokemon-import` v1 and deliberately supports only extractor
schema `pokemon-gameboy-extractor` v2 with reader version 2. It rejects unknown
schemas before producing output.

```bash
pokemon-gameboy-adapt-capturequest pokemon.db --release red \
  --output capturequest-red.json
```

The consumer selects `red` or `blue` encounter rows, retains numeric map IDs
including ID 0, reconstructs scripts from normalized action, condition, and
reference tables, applies the opt-in CaptureQuest runtime profile, reads
ordered Pokémon defaults from `pokemon_default_moves`, and preserves
`destination_kind = 'last-map'` with a null destination. It also converts
repository, graphics-output, and logical audio paths to explicit scoped
references. Items, all ordered evolution branches, level/TM/HM learnsets,
tilesets/tiles/objects, trainers, dialogue pointers, hidden and missable
objects, map events, and every specialized neutral script-rule table used by
the downstream importer are included in the same versioned boundary. Output
JSON is canonically ordered and written atomically, and the CLI rejects an
output path that aliases its input database.

CaptureQuest runtime mappings are drawn from diagnostic/source-state-machine
coverage as well as generated candidates. The production integration check
requires all 34 legacy mapping keys to remain represented. None of these names
or mappings are written into the neutral SQLite or JSON artifacts.

## Graphics catalog and PNG output

The graphics exporter catalogs every version-controlled or non-ignored file
under `pokemon-game-data/gfx`. Git-ignored compiler intermediates are excluded,
so a previously built source checkout produces the same catalog as a fresh
clone. It then populates:

- `graphic_formats` and `graphic_categories`;
- `graphic_palettes` and ordered `graphic_palette_colors`;
- `graphic_assets` for every source plus each generated image;
- `graphic_source_links` for same-stem authored previews; and
- `graphic_derivations` for raw-planar-to-PNG transformations.

Every authored `.1bpp` and `.2bpp` stream is decoded. When a same-stem PNG
provides dimensions, it also helps preserve sheet layout; otherwise the
exporter uses a deterministic sixteen-tile-wide fallback. The catalog records
dimensions, pixel mode, tile count, layout, palette, hashes, sizes, and the
metadata basis. Validation re-encodes decoded images to prove byte-for-byte raw
tile fidelity and compares applicable authored companions visually.

Source PNGs and non-planar graphic formats remain directly addressable through
repository-relative catalog rows. They are not needlessly transcoded or copied.
`graphics-catalog.json` mirrors the portable source/derivation paths and hashes
needed by consumers that do not query SQLite.

## Audio catalog and rendering

`audio_manifest.json` has `schemaVersion: 2` and two declared output formats:

- FLAC (`masterPath`) as the lossless master; and
- Ogg Vorbis (`path`) as the distribution/browser form.

It contains 45 music entries, 161 SFX/base-cry entries, 151 canonical species
cry views, all 190 internal cry slots (including distinct glitch slots), 248 map
music relationships, and all 165 move-sound relationships. The database
normalizes this as `audio_assets`, `audio_channels`, `audio_asset_sources`,
`map_music_assets`, `move_audio_assets`, and `pokemon_cry_assets`.

Render the complete catalog:

```bash
npm run render:audio -- --build-gbs --kind all --out-dir build/audio
```

Or generate and validate the database, manifests, graphics, viewer, and all
rendered audio as one staged release:

```bash
npm run generate:complete
```

`--kind` may be repeated and accepts `music`, `sfx`, `base-cries`, `cries`,
`moves`, or `all`. `--constant` and `--move-id` render selected entries;
`--limit` is useful for smoke tests. Music and effect capture lengths are
controlled independently with `--music-seconds` and `--effect-seconds`.

Species/internal cries and moves are derived assets. Their source pitch and
tempo/length modifiers are applied while building the per-asset GBS player, so
they cannot be faithfully rendered from a generic external `--gbs` file. Use
`--build-gbs` for them.

The renderer validates non-silent stereo 16-bit PCM, trims trailing silence
from non-music effects, writes both formats with deterministic FFmpeg settings,
and records hashes/duration/sample metadata in
`audio-render-manifest.json`. Looping music is captured for the selected
duration and tagged with a loop spanning that capture window; source loop labels
and the `source-runtime-capture` mode remain in the source manifest. This is
loop-aware capture metadata, not a claim that assembly loop labels map to an
exact PCM sample without emulation.

The audio output directory is itself staged and renamed. A failed render keeps
the previous complete bundle.

## Viewer

The offline Phaser viewer reads generated files under
`pokemon-phaser/public`. Run it with:

```bash
npm run viewer
```

Build its static bundle with:

```bash
npm run viewer:build
```

The viewer is deliberately downstream of the reusable database and manifests;
its layout is not part of the relational schema contract.

## Validation and reproducibility

Run focused/unit/integration tests:

```bash
npm test
```

Use `npm run test:python` or `npm run test:viewer` for one side only.

Run a complete staged database/metadata/graphics/viewer release:

```bash
npm run export
```

Useful independent checks:

```bash
sqlite3 pokemon.db 'PRAGMA integrity_check;'
sqlite3 pokemon.db 'PRAGMA foreign_key_check;'
sqlite3 pokemon.db 'SELECT schema_name, schema_version FROM schema_metadata;'
npm run viewer:build
```

The repository's full local CI equivalent runs Python/viewer tests, the viewer
build, and a moderate-or-higher dependency audit:

```bash
npm run ci
```

GitHub Actions also compiles/tests Python 3.10 and 3.14, builds an installable
wheel, type-checks/builds the viewer, and audits its locked dependencies.

The central release gate verifies:

- required tables and exact source-baseline cardinalities;
- all move keys and dependent references;
- complete release-specific encounter slot groups;
- resolved encounter and hidden-object maps;
- zero SQLite foreign-key violations and no host-specific database paths;
- complete audio relationships/source paths;
- complete graphics coverage, hashes, and round trips; and
- canonical release/run/source/entity provenance.

Deterministic exporters sort inputs and serialize stable JSON/PNG/audio output.
For reproducible run identity across environments, pin both Git revisions and
set the same `SOURCE_DATE_EPOCH`. Tool versions used for rendered audio are
recorded in its render manifest; use the same RGBDS, `gbsplay`, and FFmpeg
versions when byte-identical media is required.

## Legal boundary

The root MIT license covers the repository's original extractor code to the
extent its contributors can license it. It does not relicense the Pokémon
source material or generated content. See
[DATA_AND_ASSET_NOTICE.md](DATA_AND_ASSET_NOTICE.md) for redistribution and
third-party ownership cautions.
