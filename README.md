# Pokémon Game Boy Extractor Tool

This repository turns the source data in the
[`pret/pokered`](https://github.com/pret/pokered) disassembly into reusable,
project-neutral artifacts:

- a relational SQLite database with foreign keys, release metadata, and source
  provenance;
- portable JSON manifests for script/event and audio data;
- a complete graphics catalog plus deterministic PNG decodes of Game Boy
  1bpp/2bpp sources;
- optional lossless FLAC masters and Ogg Vorbis distribution audio rendered
  through the original Red/Blue audio engine; and
- static data/assets for the included offline map viewer.

The extractor does not include a Pokémon ROM and does not copy artifacts into
any downstream game. The canonical output is neutral: application-specific
IDs, persistence, networking, UI, battle behavior, and script execution remain
the consumer's responsibility.

![Pokémon overworld viewer demo](https://github.com/user-attachments/assets/e4602729-29bb-4ee4-94f6-446c90dd2a89)

## Quick start

```bash
git clone --recurse-submodules https://github.com/brynnb/pokemon-gameboy-extractor-tool.git
cd pokemon-gameboy-extractor-tool
npm run setup
npm run generate
```

`npm run setup` initializes the `pokemon-game-data` submodule and installs the
locked offline-viewer dependencies. `npm run generate` prepares Python
dependencies and runs the validated database/metadata/graphics/viewer pipeline.
To run the pipeline with an already prepared Python environment:

```bash
npm run export
```

Extraction requires Python 3.10 or newer, Pillow, and RGBDS (`rgbgfx`). The npm
workflows require Node.js 20.19 or newer and npm 10 or newer. Install RGBDS
with Homebrew (`brew install rgbds`) or your operating system's package
manager. See [DOCUMENTATION.md](DOCUMENTATION.md#requirements) for the complete
tool matrix, including optional audio requirements.

## Outputs

The default extraction publishes these artifacts only after every exporter and
validation check succeeds:

| Output | Contents |
| --- | --- |
| `pokemon.db` | Relational Red/Blue data, release/run metadata, source-file and entity provenance, graphics/audio catalogs. |
| `script_event_*.json` | Neutral script IR, candidates, diagnostics, trades, conditional dialogue, visibility, tile, and boulder data. |
| `audio_manifest.json` | Versioned music, SFX, map-music, move-sound, and all 190 internal cry-slot metadata. |
| `build/graphics/decoded/**` | Deterministic PNG decodes for every supported source `.1bpp` and `.2bpp` asset. |
| `export_scripts/tile_images/**` | Deduplicated 16×16 map tile PNGs. |
| `pokemon-phaser/public/viewer-{data,assets}/**` | Static bundle consumed by the offline viewer. |

Database paths are portable POSIX paths. Graphics source rows point back to
the repository/submodule; derived graphics rows use paths relative to the
graphics output root. Audio paths beginning with `/sound/` are logical web
paths, which the renderer safely materializes below its selected output root.

The pinned Red/Blue source baseline is validated for, among other invariants,
151 species, all move IDs 1–165, all 72 evolution relationships, release-aware
grass/water slot groups, 198 map-linked hidden objects, 561 normalized audio
assets, and a complete source/derived graphics catalog. Exact checks live in
`export_scripts/reprocess.py`, so incomplete releases are rejected rather than
published.

## Render audio

Audio rendering is optional because it requires additional native tools and is
substantially slower than metadata extraction. Install RGBDS, `gbsplay`, and
FFmpeg. To generate and publish the database, manifests, graphics, viewer, and
all audio as one validated release:

```bash
npm run generate:complete
```

To add or refresh audio directly from an existing `audio_manifest.json`:

```bash
npm run render:audio -- --build-gbs --kind all --out-dir build/audio
```

The renderer builds a source-derived GBS player per asset, captures the source
engine through `gbsplay`, writes deterministic FLAC masters and Ogg Vorbis
derivatives, and publishes an `audio-render-manifest.json` with hashes, sample
metadata, source modifiers, and loop-capture metadata. Useful focused forms:

```bash
npm run render:audio -- --build-gbs --kind music --out-dir build/audio
npm run render:audio -- --build-gbs --kind sfx --kind base-cries --out-dir build/audio
npm run render:audio -- --build-gbs --kind cries --kind moves --out-dir build/audio
npm run render:audio -- --build-gbs --constant MUSIC_PALLET_TOWN --out-dir build/audio
npm run render:audio -- --build-gbs --move-id 1 --out-dir build/audio
```

`--build-rom` remains a deprecated command-line alias for `--build-gbs`; it
does not produce or send a `.gb` ROM to the player.

## Viewer

```bash
npm run viewer
npm run viewer:build
```

The Phaser viewer is an inspection aid, not a game runtime. It displays the
generated maps, tiles, objects, items, NPCs, and warps from static local data.

## Validation

```bash
npm test
npm run ci
sqlite3 pokemon.db 'PRAGMA foreign_key_check;'
```

`npm test` runs the Python suite and viewer checks. `npm run ci` additionally
builds and audits the locked viewer dependency tree. The complete extractor
can still be exercised independently with `npm run export`.

The pipeline also runs SQLite integrity/foreign-key checks, exact source
coverage checks, portable-path checks, graphics round-trip/hash validation,
audio relationship validation, and release/provenance validation before
publishing.

## Project-neutral core and adapters

The default exporter contains no CaptureQuest runtime assumptions. Generic
profile hooks live in `export_scripts/runtime_profiles.py`; the historical
CaptureQuest mappings are isolated in the optional
`export_scripts/adapters/capturequest.py` adapter. Consumers opt in explicitly,
and the neutral artifacts remain the default.

## Installable Python commands

The checkout can also be installed as a Python package:

```bash
python3 -m pip install .
pokemon-gameboy-extract
pokemon-gameboy-catalogue-graphics
pokemon-gameboy-render-audio --build-gbs --kind all
```

Installed commands use the current directory as the checkout/workspace root.
Set `POKEMON_EXTRACTOR_WORKSPACE` to select another checkout containing the
`pokemon-game-data` source tree.

## Documentation and migration

- [DOCUMENTATION.md](DOCUMENTATION.md) — architecture, output layout, schema,
  provenance, configuration, graphics/audio workflows, and validation.
- [MIGRATING.md](MIGRATING.md) — consumer changes from the earlier unversioned
  output.
- [DATA_AND_ASSET_NOTICE.md](DATA_AND_ASSET_NOTICE.md) — the important legal
  distinction between this extractor's code and third-party Pokémon material.

## License and third-party material

Original extractor code in this repository is offered under the
[MIT License](LICENSE). That license does **not** grant rights to Pokémon game
data, graphics, audio, text, trademarks, the `pret/pokered` submodule, or other
third-party material. Generated artifacts can contain or describe that
material. Read [DATA_AND_ASSET_NOTICE.md](DATA_AND_ASSET_NOTICE.md) before
redistributing generated data or assets.
