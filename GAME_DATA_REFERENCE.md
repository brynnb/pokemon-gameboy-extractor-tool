# Pokémon Red/Blue — Extracted Game Data Reference

All game data from the pokered disassembly has been extracted into `pokemon.db` (SQLite). This document covers every table, its schema, how the data relates to game mechanics, and how to use it when building a game engine.

The database contains **24 new tables** (plus pre-existing tile/map tables) totaling ~11,000+ rows of structured game data.

---

## Table of Contents

1. [Pokémon Data](#1-pokémon-data)
2. [Move Data](#2-move-data)
3. [Learnsets & TM/HM Compatibility](#3-learnsets--tmhm-compatibility)
4. [Item Data](#4-item-data)
5. [Trainer & Battle Data](#5-trainer--battle-data)
6. [Dialogue & Text System](#6-dialogue--text-system)
7. [Map Objects (NPCs, Signs, Items)](#7-map-objects-npcs-signs-items)
8. [Wild Encounters](#8-wild-encounters)
9. [Hidden Objects & Pickups](#9-hidden-objects--pickups)
10. [Map Connectivity & Warps](#10-map-connectivity--warps)
11. [Map Music](#11-map-music)
12. [Scripting System](#12-scripting-system)
13. [Maps & Tilesets (Pre-existing)](#13-maps--tilesets-pre-existing)
14. [Key Relationships & Join Queries](#14-key-relationships--join-queries)
15. [Game Engine Implementation Notes](#15-game-engine-implementation-notes)

---

## 1. Pokémon Data

### Table: `pokemon` (151 rows)

| Column                                          | Type       | Description                                                        |
| ----------------------------------------------- | ---------- | ------------------------------------------------------------------ |
| `id`                                            | INTEGER PK | Pokédex number (1–151)                                             |
| `name`                                          | TEXT       | Constant name (e.g. `BULBASAUR`)                                   |
| `hp`, `atk`, `def`, `spd`, `spc`                | INTEGER    | Base stats                                                         |
| `type_1`, `type_2`                              | TEXT       | Types (e.g. `GRASS`, `POISON`). Same value if single-type          |
| `catch_rate`                                    | INTEGER    | Base catch rate (0–255)                                            |
| `base_exp`                                      | INTEGER    | Base experience yield                                              |
| `default_move_1_id` through `default_move_4_id` | TEXT       | Level-1 starting moves (move constant names)                       |
| `base_cry`, `cry_pitch`, `cry_length`           | INTEGER    | Sound data for the Pokémon's cry                                   |
| `pokedex_type`                                  | TEXT       | Pokédex category (e.g. "Seed")                                     |
| `height`                                        | TEXT       | Height string (e.g. "2' 04\"")                                     |
| `weight`                                        | INTEGER    | Weight in tenths of pounds                                         |
| `pokedex_text`                                  | TEXT       | Full Pokédex flavor text                                           |
| `evolve_level`                                  | INTEGER    | Level at which this Pokémon evolves (NULL if doesn't level-evolve) |
| `evolve_pokemon`                                | TEXT       | Name of the evolution target                                       |
| `evolves_from_trade`                            | INTEGER    | 1 if trade evolution                                               |
| `icon_image`                                    | TEXT       | Menu icon identifier                                               |
| `palette_type`                                  | TEXT       | Color palette identifier                                           |

#### Game Engine Context

- **Battle system**: Use base stats + level to calculate actual stats via Gen 1 formula: `Stat = ((Base * 2 + IV + EV/4) * Level / 100) + 5` (HP adds `Level + 10` instead of `+5`)
- **Catch mechanic**: `catch_rate` feeds into the Gen 1 catch formula
- **Pokédex UI**: `pokedex_type`, `height`, `weight`, `pokedex_text` populate the Pokédex screen
- **Evolution**: Check `evolve_level` on level-up; check `evolves_from_trade` on trade. Note: stone evolutions are encoded differently (the item triggers it) — see `items` table for stone items
- **Starter moves**: `default_move_1_id` through `default_move_4_id` are the moves a Pokémon knows at level 1

---

## 2. Move Data

### Table: `moves` (154 rows)

| Column              | Type       | Description                                         |
| ------------------- | ---------- | --------------------------------------------------- |
| `id`                | INTEGER PK | Move ID (1–165, with gaps for unused)               |
| `name`              | TEXT       | Display name with spaces (e.g. `KARATE CHOP`)       |
| `short_name`        | TEXT       | Constant name with underscores (e.g. `KARATE_CHOP`) |
| `effect`            | INTEGER    | Effect constant ID (determines special behavior)    |
| `power`             | INTEGER    | Base power (0 for status moves)                     |
| `type`              | TEXT       | Move type (e.g. `ELECTRIC`)                         |
| `accuracy`          | INTEGER    | Accuracy percentage (0–100)                         |
| `pp`                | INTEGER    | Base PP (max 40)                                    |
| `battle_animation`  | INTEGER    | Animation ID                                        |
| `battle_sound`      | INTEGER    | Sound effect ID                                     |
| `is_hm`             | INTEGER    | 1 if this is an HM move (can't be forgotten)        |
| `field_move_effect` | INTEGER    | Non-zero if usable outside battle (CUT, FLY, etc.)  |

#### Game Engine Context

- **Damage calculation**: Gen 1 formula uses `power`, `type`, attacker/defender stats, and type effectiveness
- **Type effectiveness**: Not in the DB — you'll need a 15×15 type chart (Normal, Fire, Water, Electric, Grass, Ice, Fighting, Poison, Ground, Flying, Psychic, Bug, Rock, Ghost, Dragon). Gen 1 has specific quirks (Ghost doesn't affect Psychic, etc.)
- **Move effects**: The `effect` column maps to special behaviors (flinch chance, stat changes, multi-hit, recoil, etc.). These need to be implemented as game logic
- **HM moves**: `is_hm = 1` means the move can't be deleted and may have overworld effects (Cut, Fly, Surf, Strength, Flash)
- **PP tracking**: Each move in a Pokémon's moveset has current PP / max PP. Max PP can be increased with PP Up items

---

## 3. Learnsets & TM/HM Compatibility

### Table: `pokemon_learnset` (728 rows)

| Column         | Type                 | Description                        |
| -------------- | -------------------- | ---------------------------------- |
| `id`           | INTEGER PK           | Auto-increment                     |
| `pokemon_id`   | INTEGER FK → pokemon | Pokédex number                     |
| `pokemon_name` | TEXT                 | Pokémon constant name              |
| `level`        | INTEGER              | Level at which the move is learned |
| `move_name`    | TEXT                 | Move constant name                 |
| `move_id`      | INTEGER              | Move ID                            |

**Example** — Bulbasaur's level-up moves:

```
Level  7: LEECH_SEED
Level 13: VINE_WHIP
Level 20: POISONPOWDER
Level 27: RAZOR_LEAF
Level 34: GROWTH
Level 41: SLEEP_POWDER
Level 48: SOLARBEAM
```

### Table: `pokemon_tmhm` (2,980 rows)

| Column         | Type                 | Description                            |
| -------------- | -------------------- | -------------------------------------- |
| `id`           | INTEGER PK           | Auto-increment                         |
| `pokemon_id`   | INTEGER FK → pokemon | Pokédex number                         |
| `pokemon_name` | TEXT                 | Pokémon constant name                  |
| `tm_hm_name`   | TEXT                 | TM/HM identifier (e.g. `TM03`, `HM01`) |
| `move_name`    | TEXT                 | Move constant name                     |
| `move_id`      | INTEGER              | Move ID                                |
| `is_hm`        | INTEGER              | 1 if HM, 0 if TM                       |

#### Game Engine Context

- **Level-up learning**: On each level-up, query `pokemon_learnset WHERE pokemon_id = ? AND level = ?`. If the Pokémon already knows 4 moves, prompt the player to forget one
- **TM/HM usage**: When player uses a TM/HM item, query `pokemon_tmhm WHERE pokemon_id = ? AND tm_hm_name = ?` to check compatibility. TMs are consumed on use; HMs are not
- **Move Reminder**: Gen 1 doesn't have one, but if you add it, query all moves with `level <= current_level` from `pokemon_learnset`

---

## 4. Item Data

### Table: `items` (138 rows)

| Column            | Type       | Description                                       |
| ----------------- | ---------- | ------------------------------------------------- |
| `id`              | INTEGER PK | Item ID                                           |
| `name`            | TEXT       | Display name with spaces (e.g. `POKé BALL`)       |
| `short_name`      | TEXT       | Constant name with underscores (e.g. `POKE_BALL`) |
| `price`           | INTEGER    | Buy price (sell = price / 2)                      |
| `is_usable`       | INTEGER    | 1 if can be used from bag                         |
| `uses_party_menu` | INTEGER    | 1 if using it opens the party screen              |
| `vending_price`   | INTEGER    | Price at vending machines (if different)          |
| `move_id`         | INTEGER    | For TMs/HMs, the move this teaches                |
| `is_guard_drink`  | INTEGER    | 1 if this is a guard drink (Saffron gate items)   |
| `is_key_item`     | INTEGER    | 1 if key item (can't be sold/tossed)              |

#### Game Engine Context

- **Bag system**: Items go in a single bag (Gen 1 has no pockets). Max 20 unique item slots
- **Mart shopping**: Use `price` for buy cost, `price / 2` for sell
- **Key items**: `is_key_item = 1` items can't be sold or discarded (e.g. Bicycle, Silph Scope)
- **TM/HM items**: `move_id` links to the move the TM teaches. Check `pokemon_tmhm` for compatibility

---

## 5. Trainer & Battle Data

### Table: `trainer_classes` (47 rows)

| Column          | Type       | Description                                         |
| --------------- | ---------- | --------------------------------------------------- |
| `id`            | INTEGER PK | Trainer class ID (1–47)                             |
| `constant_name` | TEXT       | Constant (e.g. `YOUNGSTER`, `BROCK`, `RIVAL1`)      |
| `display_name`  | TEXT       | Display name (e.g. "YOUNGSTER", "BROCK")            |
| `base_money`    | INTEGER    | Base prize money (multiply by last Pokémon's level) |
| `is_gym_leader` | INTEGER    | 1 if gym leader                                     |
| `is_elite_four` | INTEGER    | 1 if Elite Four member                              |
| `is_rival`      | INTEGER    | 1 if rival (has multiple party variants)            |

### Table: `trainer_parties` (391 rows)

| Column              | Type                         | Description                                                  |
| ------------------- | ---------------------------- | ------------------------------------------------------------ |
| `id`                | INTEGER PK                   | Auto-increment                                               |
| `trainer_class_id`  | INTEGER FK → trainer_classes | Which trainer class                                          |
| `party_index`       | INTEGER                      | Party number within the class (1-indexed)                    |
| `location_comment`  | TEXT                         | Where this trainer appears (e.g. "Route 3", "Pewter Gym")    |
| `is_variable_level` | INTEGER                      | 1 if each Pokémon has its own level (gym leaders, E4, rival) |

### Table: `trainer_party_pokemon` (994 rows)

| Column             | Type                         | Description                   |
| ------------------ | ---------------------------- | ----------------------------- |
| `id`               | INTEGER PK                   | Auto-increment                |
| `trainer_party_id` | INTEGER FK → trainer_parties | Which party                   |
| `slot_index`       | INTEGER                      | Position in party (1-indexed) |
| `pokemon_name`     | TEXT                         | Pokémon constant name         |
| `level`            | INTEGER                      | Pokémon's level               |

**Example** — Misty's party:

```
MISTY (base_money: 9900, is_gym_leader: 1)
  Party 1: Lv.18 STARYU, Lv.21 STARMIE
  Prize money: (9900 / 100) × 21 = ¥2079
```

**Prize money formula**: `(base_money / 100) × level_of_last_pokemon_in_party`. The `base_money` values in the DB are stored in BCD (binary-coded decimal) format from the original game — divide by 100 to get the actual base ¥ amount. For example, `9900` = ¥99 base, `1500` = ¥15 base. The "last Pokémon" is the last slot in the party, not necessarily the highest level one

### Table: `trainer_headers` (322 rows)

| Column                    | Type       | Description                                                                               |
| ------------------------- | ---------- | ----------------------------------------------------------------------------------------- |
| `id`                      | INTEGER PK | Auto-increment                                                                            |
| `map_name`                | TEXT       | Map where this trainer appears                                                            |
| `header_label`            | TEXT       | ASM label for the trainer header block                                                    |
| `header_index`            | INTEGER    | Index within the header block                                                             |
| `event_flag`              | TEXT       | Event flag checked to see if already beaten (e.g. `EVENT_BEAT_VIRIDIAN_FOREST_TRAINER_0`) |
| `sight_range`             | INTEGER    | How many tiles away the trainer can see you (triggers walk-up battle)                     |
| `battle_text_label`       | TEXT       | Dialogue label shown before battle                                                        |
| `end_battle_text_label`   | TEXT       | Dialogue label shown when trainer loses                                                   |
| `after_battle_text_label` | TEXT       | Dialogue label shown when talked to after defeat                                          |

#### Game Engine Context

- **NPC → Trainer link**: The `objects` table has `trainer_class` and `trainer_party_index` columns. Join `objects.trainer_class = trainer_classes.constant_name` and `objects.trainer_party_index = trainer_parties.party_index` to get the full party
- **Sight range**: When the player enters a trainer's line of sight (`sight_range` tiles in their facing direction), the trainer walks toward the player and initiates battle. The `!` exclamation mark appears above their head
- **Event flags**: Each trainer has an event flag. Once beaten, the flag is set and the trainer won't battle again. They show `after_battle_text_label` dialogue instead
- **Prize money**: `trainer_classes.base_money × level_of_last_pokemon` (Gen 1 uses a specific BCD encoding but the formula is straightforward)
- **Rival parties**: The rival (`RIVAL1`, `RIVAL2`, `RIVAL3`) has multiple party variants based on which starter the player chose. Party indices 1/2/3 correspond to the three starter choices

---

## 6. Dialogue & Text System

### Table: `dialogue_text` (2,430 rows)

| Column        | Type        | Description                                   |
| ------------- | ----------- | --------------------------------------------- |
| `id`          | INTEGER PK  | Auto-increment                                |
| `label`       | TEXT UNIQUE | ASM label (e.g. `_PalletTownSignText`)        |
| `source_file` | TEXT        | Source file path (e.g. `text/PalletTown.asm`) |
| `dialogue`    | TEXT        | The actual dialogue string                    |

**Example entries:**

```
_PalletTownSignText       → "PALLET TOWN\nShades of your journey await!"
_PewterCityGymSignText    → "PEWTER CITY\nPOKéMON GYM LEADER: BROCK\n\nThe Rock Solid\nPOKéMON Trainer!"
_PalletTownFisherText     → "Technology is incredible!\n\nYou can now store and recall items and POKéMON as data via PC!"
```

**Text formatting conventions:**

- `\n` = new line (within same text box)
- `\n\n` = new paragraph (clears text box, player presses A)
- `{PLAYER}` = placeholder for player's name
- `{RIVAL}` = placeholder for rival's name
- `POKé` = the é character used in "POKéMON"
- `@` at end = text terminator artifact (can be stripped)

### Table: `text_pointers` (1,207 rows)

| Column           | Type       | Description                                                     |
| ---------------- | ---------- | --------------------------------------------------------------- |
| `id`             | INTEGER PK | Auto-increment                                                  |
| `map_name`       | TEXT       | Map name (e.g. `PalletTown`)                                    |
| `text_constant`  | TEXT       | TEXT\_ constant from object files (e.g. `TEXT_PALLETTOWN_SIGN`) |
| `local_label`    | TEXT       | Local script label (e.g. `PalletTownSignText`)                  |
| `dialogue_label` | TEXT       | Resolved dialogue_text label (e.g. `_PalletTownSignText`)       |
| `pointer_index`  | INTEGER    | Order in the text pointer table                                 |
| `is_trainer`     | INTEGER    | 1 if this text pointer is for a trainer NPC                     |

#### Game Engine Context

- **Resolving NPC dialogue**: An NPC object has a `text` field like `TEXT_PALLETTOWN_FISHER`. To get the actual dialogue:
  1. Query `text_pointers WHERE text_constant = 'TEXT_PALLETTOWN_FISHER'` → get `dialogue_label`
  2. Query `dialogue_text WHERE label = dialogue_label` → get `dialogue`
- **Branching dialogue**: Some NPCs have `text_asm` blocks with yes/no choices or event flag checks. These have multiple `dialogue_text` entries referenced from the same script. The `trainer_headers` table captures the three dialogue states for trainers (before battle, on defeat, after defeat)
- **Sign text**: Signs are simpler — they always have a direct `text_far` → `dialogue_text` mapping

---

## 7. Map Objects (NPCs, Signs, Items)

### Table: `objects` (997 rows)

| Column                | Type               | Description                                                             |
| --------------------- | ------------------ | ----------------------------------------------------------------------- |
| `id`                  | INTEGER PK         | Auto-increment                                                          |
| `name`                | TEXT               | Generated name (e.g. `PalletTown_NPC_1`, `ViridianForest_SIGN_3`)       |
| `map_id`              | INTEGER FK → maps  | Map this object belongs to                                              |
| `object_type`         | TEXT               | `npc`, `sign`, or `item`                                                |
| `x`, `y`              | INTEGER            | Global tile coordinates (may be NULL if not yet computed)               |
| `local_x`, `local_y`  | INTEGER            | Local tile coordinates within the map                                   |
| `sprite_name`         | TEXT               | Sprite constant (e.g. `SPRITE_YOUNGSTER`, `SPRITE_POKE_BALL`)           |
| `text`                | TEXT               | TEXT\_ constant for dialogue/interaction                                |
| `action_type`         | TEXT               | Movement behavior: `STAY`, `WALK`                                       |
| `action_direction`    | TEXT               | Facing direction or walk pattern: `UP`, `DOWN`, `LEFT`, `RIGHT`, `NONE` |
| `item_id`             | INTEGER FK → items | For item pickups, the item ID                                           |
| `movement_type`       | TEXT               | `LAND`, `WATER`, or `BOTH`                                              |
| `trainer_class`       | TEXT               | Trainer class constant (e.g. `BUG_CATCHER`) — NULL if not a trainer     |
| `trainer_party_index` | INTEGER            | Party index within the trainer class — NULL if not a trainer            |

#### Game Engine Context

- **Spawning NPCs**: On map load, query `objects WHERE map_id = ?`. Place each at `(local_x, local_y)` with the appropriate sprite
- **NPC behavior**: `action_type = 'STAY'` means stationary (faces `action_direction`). `action_type = 'WALK'` means the NPC wanders randomly within a small area
- **Interaction**: When player presses A facing an object:
  - **Signs** (`object_type = 'sign'`): Display dialogue from `text` → `text_pointers` → `dialogue_text`
  - **NPCs** (`object_type = 'npc'`): If `trainer_class` is set and the trainer hasn't been beaten, initiate battle. Otherwise show dialogue
  - **Items** (`object_type = 'item'`): Give the player the item (`item_id`) and hide the object
- **Trainer NPCs**: Join with `trainer_classes` and `trainer_parties` via `trainer_class` and `trainer_party_index` to get the battle party

---

## 8. Wild Encounters

### Table: `wild_encounters` (947 rows)

| Column           | Type              | Description                                                  |
| ---------------- | ----------------- | ------------------------------------------------------------ |
| `id`             | INTEGER PK        | Auto-increment                                               |
| `map_name`       | TEXT              | Map constant (e.g. `ROUTE_1`, `VIRIDIAN_FOREST`)             |
| `map_id`         | INTEGER FK → maps | Map ID                                                       |
| `encounter_type` | TEXT              | `grass`, `water`, `super_rod`, or `good_rod`                 |
| `encounter_rate` | INTEGER           | Encounter rate (0 = no encounters; higher = more frequent)   |
| `slot_index`     | INTEGER           | Encounter slot (1–10 for grass/water, varies for fishing)    |
| `pokemon_name`   | TEXT              | Pokémon constant name                                        |
| `level`          | INTEGER           | Pokémon level                                                |
| `version`        | TEXT              | `both`, `red`, or `blue` (some encounters differ by version) |

### Table: `encounter_slots` (10 rows)

| Column                   | Type    | Description                          |
| ------------------------ | ------- | ------------------------------------ |
| `slot_index`             | INTEGER | Slot number (1–10)                   |
| `probability`            | REAL    | Probability percentage for this slot |
| `cumulative_probability` | REAL    | Cumulative probability               |

**Slot probabilities (Gen 1):**

```
Slot  1: 19.9%    Slot  6:  9.8%
Slot  2: 19.9%    Slot  7:  5.1%
Slot  3: 15.2%    Slot  8:  5.1%
Slot  4:  9.8%    Slot  9:  4.3%
Slot  5:  9.8%    Slot 10:  1.2%
```

**Example** — Route 1 grass encounters:

```
Slot 1: Lv.3 PIDGEY   (19.9%)
Slot 2: Lv.3 RATTATA  (19.9%)
Slot 3: Lv.3 RATTATA  (15.2%)
Slot 4: Lv.2 RATTATA  (9.8%)
Slot 5: Lv.2 PIDGEY   (9.8%)
...
Slot 10: Lv.5 PIDGEY  (1.2%)
```

#### Game Engine Context

- **Encounter check**: Each step in grass/water, roll against `encounter_rate / 256`. If triggered, pick a slot using the probability table, then spawn that Pokémon at that level
- **Fishing**: Super Rod encounters are map-specific (query by map). Good Rod encounters are global (2 possible Pokémon). Old Rod always catches Magikarp (hardcoded)
- **Version differences**: Some maps (e.g. Viridian Forest) have different encounters for Red vs Blue. Filter by `version` column. If you're making one version, pick one or merge them
- **Repel**: Repel items suppress encounters with Pokémon whose level is lower than the lead party Pokémon's level

---

## 9. Hidden Objects & Pickups

### Table: `hidden_items` (54 rows)

| Column         | Type              | Description                         |
| -------------- | ----------------- | ----------------------------------- |
| `map_constant` | TEXT              | Map constant name                   |
| `map_id`       | INTEGER FK → maps | Map ID                              |
| `x`, `y`       | INTEGER           | Tile coordinates of the hidden item |

### Table: `hidden_coins` (12 rows)

Same schema as `hidden_items`. All 12 are in the Game Corner.

### Table: `hidden_objects` (198 rows)

| Column              | Type    | Description                                                                                                              |
| ------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------ |
| `map_constant`      | TEXT    | Map label (not always matching map constant exactly)                                                                     |
| `x`, `y`            | INTEGER | Tile coordinates                                                                                                         |
| `item_or_direction` | TEXT    | Facing direction required, or item/text identifier                                                                       |
| `routine`           | TEXT    | The routine that runs on interaction                                                                                     |
| `object_type`       | TEXT    | Categorized type: `pc`, `bookcase`, `gym_statue`, `poster`, `bench_guy`, `fossil`, `cable_club`, `text_predef`, `hidden` |

#### Game Engine Context

- **Hidden items**: Invisible pickups found with the Itemfinder. Player presses A on the exact tile to pick up. Each has an event flag to track if already collected (not stored in DB — you'll need to implement this)
- **Hidden coins**: Same as hidden items but give coins instead of items. All in the Game Corner
- **Hidden objects**: Interactive background elements — PCs (heal Pokémon, access storage), bookcases (flavor text), gym statues (show gym leader info), etc. The `routine` column tells you what happens on interaction
- **Itemfinder**: The Itemfinder item beeps when the player is within a certain radius of an uncollected hidden item

---

## 10. Map Connectivity & Warps

### Table: `warp_events` (805 rows)

| Column            | Type              | Description                                                       |
| ----------------- | ----------------- | ----------------------------------------------------------------- |
| `map_name`        | TEXT              | Source map (CamelCase, e.g. `PalletTown`)                         |
| `map_id`          | INTEGER FK → maps | Source map ID                                                     |
| `x`, `y`          | INTEGER           | Tile coordinates of the warp on the source map                    |
| `dest_map`        | TEXT              | Destination map constant (UPPER_SNAKE_CASE, e.g. `REDS_HOUSE_1F`) |
| `dest_warp_index` | INTEGER           | Which warp on the destination map to arrive at                    |

**Example** — Pallet Town warps:

```
PalletTown (5,5)   → REDS_HOUSE_1F, warp 1
PalletTown (13,5)  → BLUES_HOUSE, warp 1
PalletTown (12,11) → OAKS_LAB, warp 2
```

**Example** — Viridian Forest warps:

```
ViridianForest (1,0)  → VIRIDIAN_FOREST_NORTH_GATE, warp 3
ViridianForest (2,0)  → VIRIDIAN_FOREST_NORTH_GATE, warp 4
ViridianForest (15,47) → VIRIDIAN_FOREST_SOUTH_GATE, warp 2
ViridianForest (16,47) → VIRIDIAN_FOREST_SOUTH_GATE, warp 2
...
```

#### Game Engine Context

- **Door/stair transitions**: When the player steps on a warp tile, transition to `dest_map` and place the player at the corresponding `dest_warp_index` warp on that map. Warps are bidirectional — the destination map has a warp back
- **Warp index resolution**: To find where the player arrives, query `warp_events WHERE map_name = dest_map` and find the row whose order matches `dest_warp_index` (1-indexed). The `(x, y)` of that row is where the player spawns
- **Multi-tile warps**: Some entrances span multiple tiles (e.g. Viridian Forest south exit has 4 warp tiles all going to the same destination). This is normal
- **`LAST_MAP` special constant**: Some warps use `LAST_MAP` as `dest_map`, meaning "return to whatever map the player came from" (e.g. shops, Pokémon Centers). Your engine needs to track the previous map for these
- **Map connections** (overworld): The `maps` table has `north_connection`, `south_connection`, `west_connection`, `east_connection` for seamless overworld map transitions (Route 1 ↔ Pallet Town). These are different from warps — they're edge-to-edge connections

---

## 11. Map Music

### Table: `map_music` (248 rows)

| Column           | Type              | Description                                                  |
| ---------------- | ----------------- | ------------------------------------------------------------ |
| `map_constant`   | TEXT              | Map constant (UPPER_SNAKE_CASE)                              |
| `map_id`         | INTEGER FK → maps | Map ID                                                       |
| `music_constant` | TEXT              | Music track constant (e.g. `MUSIC_PALLET_TOWN`, `MUSIC_GYM`) |

#### Game Engine Context

- **Music playback**: On map load, query `map_music WHERE map_constant = ?` and start playing the corresponding track. If the track is already playing (e.g. entering a house in the same city), don't restart it
- **Music constants map to tracks**: `MUSIC_PALLET_TOWN`, `MUSIC_CITIES1`, `MUSIC_CITIES2`, `MUSIC_ROUTES1`–`MUSIC_ROUTES4`, `MUSIC_GYM`, `MUSIC_POKECENTER`, `MUSIC_POKEMON_TOWER`, `MUSIC_SS_ANNE`, `MUSIC_DUNGEON1`–`MUSIC_DUNGEON3`, `MUSIC_GAME_CORNER`, `MUSIC_CINNABAR_MANSION`, `MUSIC_SAFARI_ZONE`, `MUSIC_SILPH_CO`, `MUSIC_OAKS_LAB`, `MUSIC_INDIGO_PLATEAU`, etc.
- **Battle music** is not in this table — it's triggered by game logic (wild battle, trainer battle, gym leader, champion)

---

## 12. Scripting System

The original game uses a per-map state machine written in Z80 assembly. We've extracted the structured parts into relational tables and preserved the raw assembly for future Lua conversion.

### Table: `map_scripts` (381 rows)

| Column            | Type    | Description                                              |
| ----------------- | ------- | -------------------------------------------------------- |
| `map_name`        | TEXT    | Map name (CamelCase)                                     |
| `script_index`    | INTEGER | State index in the map's state machine (0 = default)     |
| `script_label`    | TEXT    | ASM label for this script state                          |
| `script_constant` | TEXT    | Named constant (e.g. `SCRIPT_CERULEANCITY_RIVAL_BATTLE`) |
| `raw_asm`         | TEXT    | Raw assembly source for this script block                |

**Example** — Cerulean City's script states:

```
Index 0: SCRIPT_CERULEANCITY_DEFAULT           (normal gameplay)
Index 1: SCRIPT_CERULEANCITY_RIVAL_BATTLE      (rival walks up, battle starts)
Index 2: SCRIPT_CERULEANCITY_RIVAL_DEFEATED     (post-battle dialogue)
Index 3: SCRIPT_CERULEANCITY_RIVAL_CLEANUP      (rival walks away, reset)
Index 4: SCRIPT_CERULEANCITY_ROCKET_DEFEATED    (Team Rocket thief event)
```

### Table: `npc_movement_data` (28 rows)

| Column      | Type        | Description                     |
| ----------- | ----------- | ------------------------------- |
| `map_name`  | TEXT        | Map name                        |
| `label`     | TEXT        | Movement data label             |
| `movements` | TEXT (JSON) | JSON array of movement commands |

**Example**: `["NPC_MOVEMENT_DOWN", "NPC_MOVEMENT_DOWN", "NPC_MOVEMENT_DOWN", "NPC_MOVEMENT_DOWN"]`

### Table: `event_flags` (265 rows, 136 unique flags)

| Column          | Type | Description                                                  |
| --------------- | ---- | ------------------------------------------------------------ |
| `map_name`      | TEXT | Map where this flag is referenced                            |
| `flag_name`     | TEXT | Flag constant (e.g. `EVENT_BEAT_BROCK`, `EVENT_GOT_POKEDEX`) |
| `operation`     | TEXT | `checkevent`, `setevent`, or `resetevent`                    |
| `context_label` | TEXT | Which script function references this flag                   |

### Table: `coordinate_triggers` (66 rows)

| Column     | Type    | Description                              |
| ---------- | ------- | ---------------------------------------- |
| `map_name` | TEXT    | Map name                                 |
| `label`    | TEXT    | Trigger group label                      |
| `x`, `y`   | INTEGER | Tile coordinates that trigger the script |

#### Game Engine Context — Scripting Architecture

The scripting system is the most complex part to implement. Here's the recommended approach:

**State Machine Per Map:**
Each map has a `current_script_index` (starts at 0 = default). The game engine runs the current script state every frame. Script states can:

- Check event flags and player position
- Trigger dialogue, battles, or cutscenes
- Advance to the next state (e.g. `0 → 1 → 2 → 3 → 0`)

**Event Flags (Global Game State):**
Event flags are booleans that persist across the entire game. They track:

- Which trainers have been beaten (`EVENT_BEAT_BROCK`, `EVENT_BEAT_VIRIDIAN_FOREST_TRAINER_0`)
- Story progress (`EVENT_GOT_POKEDEX`, `EVENT_BEAT_CERULEAN_ROCKET_THIEF`)
- Item collection (`EVENT_BOUGHT_MUSEUM_TICKET`)

Your game needs a global `Set<string>` of active event flags. Scripts check/set/reset these.

**Coordinate Triggers:**
When the player steps on specific tiles, scripts fire. For example, in Pewter City, stepping on tiles (35,17) or (36,17) triggers the youngster who guides you to the gym (if `EVENT_BEAT_BROCK` is not set).

**NPC Movement Sequences:**
During cutscenes, NPCs follow scripted movement paths. The `npc_movement_data` table has JSON arrays of movement commands (`NPC_MOVEMENT_UP`, `NPC_MOVEMENT_DOWN`, `NPC_MOVEMENT_LEFT`, `NPC_MOVEMENT_RIGHT`).

**Recommended Implementation:**

1. **Phase 1**: Implement event flags + coordinate triggers + trainer sight range. This covers 80% of gameplay
2. **Phase 2**: Convert the `raw_asm` in `map_scripts` to Lua scripts for cutscenes (rival encounters, story events). The state machine pattern maps naturally to Lua coroutines
3. **Phase 3**: NPC movement sequences for cutscenes

---

## 13. Maps & Tilesets (Pre-existing)

These tables were created by earlier export scripts and are not new.

### Table: `maps` (248 rows)

| Column                             | Type       | Description                             |
| ---------------------------------- | ---------- | --------------------------------------- |
| `id`                               | INTEGER PK | Map ID                                  |
| `name`                             | TEXT       | Map constant (UPPER_SNAKE_CASE)         |
| `width`, `height`                  | INTEGER    | Map dimensions in tiles                 |
| `tileset_id`                       | INTEGER    | Tileset used for rendering              |
| `blk_data`                         | BLOB       | Raw block data for the map              |
| `north/south/west/east_connection` | INTEGER    | Adjacent map IDs for seamless scrolling |
| `is_overworld`                     | INTEGER    | 1 if this is an outdoor overworld map   |

Additional pre-existing tables: `tilesets`, `tiles`, `tiles_raw`, `tile_images`, `tileset_tiles`, `blocks`, `blocksets`, `collision_tiles`, `map_connections`, `overworld_map_positions`, `warps`.

---

## 14. Key Relationships & Join Queries

### Get an NPC's full data (dialogue + trainer party)

```sql
SELECT
    o.name, o.sprite_name, o.local_x, o.local_y,
    dt.dialogue,
    tc.display_name AS trainer_name, tc.base_money,
    tpp.pokemon_name, tpp.level
FROM objects o
LEFT JOIN text_pointers tp ON tp.text_constant = o.text
LEFT JOIN dialogue_text dt ON dt.label = tp.dialogue_label
LEFT JOIN trainer_classes tc ON tc.constant_name = o.trainer_class
LEFT JOIN trainer_parties tpar ON tpar.trainer_class_id = tc.id
    AND tpar.party_index = o.trainer_party_index
LEFT JOIN trainer_party_pokemon tpp ON tpp.trainer_party_id = tpar.id
WHERE o.map_id = ?
ORDER BY o.id, tpp.slot_index;
```

### Get all data for a map (encounters + music + warps)

```sql
-- Wild encounters
SELECT * FROM wild_encounters WHERE map_name = 'ROUTE_1';

-- Music
SELECT music_constant FROM map_music WHERE map_constant = 'ROUTE_1';

-- Warps
SELECT * FROM warp_events WHERE map_name = 'Route1';

-- NPCs and signs
SELECT * FROM objects WHERE map_id = (SELECT id FROM maps WHERE name = 'ROUTE_1');

-- Hidden items
SELECT * FROM hidden_items WHERE map_constant = 'ROUTE_1';
```

### Get a Pokémon's complete moveset potential

```sql
-- Level-up moves
SELECT level, move_name FROM pokemon_learnset
WHERE pokemon_name = 'BULBASAUR' ORDER BY level;

-- TM/HM compatibility
SELECT tm_hm_name, move_name FROM pokemon_tmhm
WHERE pokemon_name = 'BULBASAUR' ORDER BY tm_hm_name;

-- Starting moves (from pokemon table)
SELECT default_move_1_id, default_move_2_id, default_move_3_id, default_move_4_id
FROM pokemon WHERE name = 'BULBASAUR';
```

### Get trainer battle text (before/during/after)

```sql
SELECT
    th.event_flag, th.sight_range,
    dt_battle.dialogue AS before_battle_text,
    dt_end.dialogue AS on_defeat_text,
    dt_after.dialogue AS after_defeat_text
FROM trainer_headers th
LEFT JOIN dialogue_text dt_battle ON dt_battle.label = th.battle_text_label
LEFT JOIN dialogue_text dt_end ON dt_end.label = th.end_battle_text_label
LEFT JOIN dialogue_text dt_after ON dt_after.label = th.after_battle_text_label
WHERE th.map_name = 'ViridianForest';
```

---

## 15. Game Engine Implementation Notes

### Map Name Conventions

There are **two naming conventions** used across the database:

- **CamelCase**: `PalletTown`, `ViridianForest` — used in `objects.name`, `warp_events.map_name`, `map_scripts.map_name`, `text_pointers.map_name`, `event_flags.map_name`
- **UPPER_SNAKE_CASE**: `PALLET_TOWN`, `VIRIDIAN_FOREST` — used in `maps.name`, `wild_encounters.map_name`, `map_music.map_constant`, `hidden_items.map_constant`, `warp_events.dest_map`

When joining across tables, you may need to convert between these formats.

### Coordinate Systems

- **Local coordinates** (`local_x`, `local_y`): Tile position within a single map. (0,0) is top-left
- **Global coordinates** (`x`, `y`): Position on the overworld. May be NULL for indoor maps
- **Warp coordinates**: Use local coordinates

### Data Not in the Database (Must Be Implemented in Code)

- **Type effectiveness chart** (15×15 matrix)
- **Damage formula** (Gen 1 specific, includes critical hit mechanics)
- **Experience curve formulas** (4 growth rates: fast, medium-fast, medium-slow, slow)
- **Stat calculation formulas** (IVs, EVs, level-based)
- **AI behavior** for trainer battles (Gen 1 AI is simple — mostly random with some type-awareness)
- **Battle state machine** (turn order, move execution, status effects, switching)
- **Inventory management** (bag slots, PC storage)
- **Pokémon storage** (Bill's PC, box system)
- **Save/load system**

### Export Script Execution Order

If you need to regenerate the database, run the scripts in this order:

```bash
cd export_scripts/
python create_zones_and_tiles.py  # Maps, tilesets, tiles (pre-existing)
python export_pokemon.py          # Pokémon base data
python export_moves.py            # Move data
python export_items.py            # Item data
python export_objects.py          # NPCs, signs, item pickups
python export_text.py             # Dialogue text + text pointers + trainer headers
python export_learnsets.py        # Level-up learnsets + TM/HM compatibility
python export_wild_encounters.py  # Wild encounters
python export_trainers.py         # Trainer classes + parties
python export_hidden_objects.py   # Hidden items/coins/objects + map music
python export_map_scripts.py      # Script state machines + event flags + warps
```
