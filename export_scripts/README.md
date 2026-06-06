# Export Scripts

These Python scripts extract Pokemon Red/Blue data from the
`pokemon-game-data` submodule into `pokemon.db` (SQLite).

## Canonical Pipeline

Run the full environment setup and pipeline from the repo root:

```bash
npm run generate
```

If dependencies and submodules are already ready, run just the extractor
pipeline:

```bash
npm run export
```

That invokes `reprocess.py`, which runs the scripts in this order:

1. `export_map.py`
2. `export_warps.py`
3. `update_zone_coordinates.py`
4. `create_zones_and_tiles.py`
5. `export_objects.py`
6. `update_object_coordinates.py`
7. `export_pokemon.py`
8. `export_moves.py`
9. `export_items.py`
10. `export_text.py`
11. `export_learnsets.py`
12. `export_wild_encounters.py`
13. `export_trainers.py`
14. `export_hidden_objects.py`
15. `export_map_scripts.py`

The order matters. Overworld offsets must exist before tile expansion, and
object coordinates must be updated after objects are extracted.

## Key Tables

- `maps`, `tilesets`, `tiles_raw`, `tiles`, `tile_images`
- `objects`, `warps`, `warp_events`
- `pokemon`, `moves`, `items`, `pokemon_learnset`, `pokemon_tmhm`
- `wild_encounters`, `encounter_slots`
- `trainer_classes`, `trainer_parties`, `trainer_party_pokemon`,
  `trainer_headers`
- `dialogue_text`, `text_pointers`
- `hidden_items`, `hidden_coins`, `hidden_objects`
- `map_music`, `map_scripts`, `npc_movement_data`, `event_flags`,
  `coordinate_triggers`

## Runtime Integration Notes

- Trainer sight range comes from the second numeric argument in each map script's
  `trainer` macro and is exported as `trainer_headers.sight_range`.
- NPC objects link to trainer parties through `objects.trainer_class` and
  `objects.trainer_party_index`.
- Overworld maps are stitched with global coordinates, but local map coordinates
  remain important for scripts, trainer sight, object interaction, and warp
  resolution.
- Runtime behavior belongs in the downstream game or tool; extractor tables
  should preserve original source facts rather than encode app-specific policy.

## Troubleshooting

```bash
sqlite3 ../pokemon.db ".tables"
sqlite3 ../pokemon.db "SELECT COUNT(*) FROM tiles"
sqlite3 ../pokemon.db "SELECT map_name, sight_range FROM trainer_headers LIMIT 10"
```

If tilesets fail to convert, confirm `rgbgfx` is installed through RGBDS.
