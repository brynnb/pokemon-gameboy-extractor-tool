# Migrating from the unversioned extractor output

The current artifact is the first release with explicit schema metadata. It
also corrects several data-loss and relationship bugs, so consumers of older
`pokemon.db` or JSON files should treat this as a schema migration rather than
dropping the new files into an application unnoticed.

## Consumer checklist

1. Regenerate the database and its metadata/graphics/viewer companions together
   with `npm run export`; use `npm run generate:complete` when rendered audio is
   part of the release. Do not mix generations.
2. Read `schema_metadata` and reject a schema newer than your reader supports.
3. Enable SQLite foreign keys on every connection.
4. Replace assumptions about 154 moves with the complete ID range 1–165.
5. Read evolutions from `pokemon_evolutions`, not the legacy scalar columns.
6. Filter grass/water encounters by `version` and use one-based slots 1–10.
7. Resolve map/tileset relationships through numeric IDs and foreign keys.
8. Resolve catalog paths according to their documented scope instead of
   treating build-machine paths as durable data.
9. Update audio consumers to manifest schema 2 and distinct FLAC/Ogg paths.
10. Treat `LAST_MAP` warps as runtime destinations instead of guessed static
    map links.
11. Read Pokémon default moves through integer foreign keys or the normalized
    relationship table; source constant strings now use `*_name` columns.
12. Remove CaptureQuest-specific assumptions unless that adapter is explicitly
    selected by the downstream project.

## Schema negotiation

```sql
SELECT schema_name, schema_version, minimum_reader_version
FROM schema_metadata;
```

The current values are schema `pokemon-gameboy-extractor`, version `2`, minimum
reader version `2`. This version number starts the explicit contract; it does
not imply that the preceding unversioned database had the same shape.

## Breaking and compatibility changes

### Complete move keys

Older extraction omitted 11 source moves and produced 154 rows. `moves` now
contains every ID from 1 through 165, retains each source `constant_name`, and
checks item, level/TM-HM, Pokémon default-move, and audio relationships against
that full set. Remove fixed-size arrays and allow any source move ID in that
range.

The four `pokemon.default_move_*_id` columns are now integer foreign keys.
Compatibility source strings moved to `default_move_*_name`; complete ordered
relationships are available in `pokemon_default_moves`.

### One-to-many evolutions

`pokemon_evolutions` is authoritative. It stores source/target species foreign
keys, method (`level`, `item`, or `trade`), method parameter, and source order.
This preserves all Eevee branches and any future branching source data.

The old `pokemon.evolve_level`, `evolve_pokemon`, and
`evolves_from_trade` columns are retained temporarily. They expose only the
first source relationship and are unsuitable for complete consumers.

### Release-aware encounters

Grass/water rows are now separate `red` and `blue` groups with exactly ten
one-based slots in each populated map/type/version group. Shared rod rows use
`both`. Previously flattened or misnumbered slots must not be used as stable
keys.

Recommended key:

```text
(map_id, encounter_type, version, slot_index)
```

The Good Rod list is global source data and intentionally has no `map_id`.

### Correct map relationships

Map connection fields and `map_connections` now contain numeric map IDs. All
24 tileset constants have rows; shared physical data is represented through
`tilesets.source_tileset_id`. Hidden objects now resolve to their source maps.

Warps now distinguish `destination_kind = 'fixed'` from the engine's dynamic
`destination_kind = 'last-map'`. A `last-map` row deliberately has no
`destination_map_id`; older heuristic destinations must be discarded. Script,
movement, coordinate-trigger, text-pointer, trainer-header, candidate, IR, and
diagnostic rows now expose map IDs backed by foreign keys.

Consumers that previously compared a connection column to a map name should
join it to `maps.id` instead:

```sql
SELECT source.name, destination.name
FROM maps AS source
JOIN maps AS destination ON destination.id = source.north_connection
WHERE source.north_connection IS NOT NULL;
```

### Portable paths

Source and tile-image paths no longer embed the build host's absolute path.
Treat repository paths as relative to the extraction bundle/repository.
Graphics rows state `path_scope` explicitly. Audio `/sound/...` paths are URL
paths and are materialized below the selected renderer output directory.

### Graphics catalog

The database now catalogs every version-controlled or non-ignored file in the
pinned `gfx` source tree, its format/category/hash/size, palettes where
available, authored same-stem preview links, and deterministic PNG derivations
for every authored planar source. Git-ignored compiler intermediates are
deliberately excluded so clean and previously built checkouts agree.
Use `graphic_derivations` to walk from raw bytes to a generated PNG; do not
infer a copied asset from a filename convention alone.

### Audio manifest and tables

`audio_manifest.json` now has `schemaVersion: 2`. Every entry declares a FLAC
master and Ogg Vorbis distribution path. Source files resolve for music and
SFX; moves have distinct derived paths and pitch/tempo parameters; all 190
internal cry slots remain addressable instead of being collapsed onto 151
species.

New normalized database tables are `audio_assets`, `audio_channels`,
`audio_asset_sources`, `map_music_assets`, `move_audio_assets`, and
`pokemon_cry_assets`. Prefer these for relational joins and the JSON manifest
for build/transport metadata.

The renderer's `--build-rom` flag is retained as a deprecated alias. Use
`--build-gbs`; the supported container is GBS, not a `.gb` ROM.

### Neutral script output

The canonical script candidates and diagnostics are project-neutral.
CaptureQuest-authored script names and movement overrides moved to the optional
`export_scripts/adapters/capturequest.py` profile. New consumers should not use
that profile. Existing CaptureQuest importers can apply it explicitly while
migrating.

Candidate actions, candidate conditions, candidate references, and IR
references also have normalized relationship tables. Consumers can migrate
away from JSON-only queries without losing the JSON transport representation.

CaptureQuest consumers should use the separately versioned
`pokemon-gameboy-adapt-capturequest` command rather than reading compatibility
scalar columns or applying mappings inside the canonical exporter. Its v1
contract negotiates extractor schema v2, requires an explicit Red/Blue release,
uses normalized default moves and scripts, and leaves `last-map` destinations
dynamic. It also transports the current relational world, item, evolution,
learnset, trainer, text, hidden-object, map-event, and special-rule inputs, so
new consumers do not need to fall back to compatibility scalar columns.

### Release and provenance metadata

New tables record Red/Blue release rows, one deterministic extraction run, both
Git revisions, a source file hash catalog, and exact row-to-source relationships
where exported rows expose source paths. `extracted_tables` and
`table_provenance` additionally associate every generated table with its
conservative upstream source set, including derived tables without a single
exact source path. This replaces relying on a filename or database modification
time to identify an artifact.

Run identity also includes `extractor_tree_sha256` and
`extractor_worktree_dirty`, so consumers can distinguish a release produced by
a modified generator from one produced by the same clean Git revision.

## Publication behavior

The canonical pipeline now builds every managed artifact in sibling staging
paths, validates the complete set, and installs the database last. A failed
generation leaves the previous successful release in place. Consumers that
watch files should reload only after the database changes, then reopen their
SQLite connection rather than retaining a handle to the replaced inode.

## Suggested compatibility window

For a transition period, a consumer can support both shapes by checking for
`schema_metadata`:

- if present, use normalized evolutions, releases, catalogs, and explicit
  encounter versions;
- if absent, treat the artifact as legacy and either run a one-time importer or
  require regeneration.

Avoid maintaining a silent hybrid mode. Old and new companion JSON/assets can
have valid filenames while representing different database contents.
