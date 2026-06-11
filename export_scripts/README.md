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
5. `export_items.py`
6. `export_objects.py`
7. `update_object_coordinates.py`
8. `export_pokemon.py`
9. `export_moves.py`
10. `export_text.py`
11. `export_learnsets.py`
12. `export_wild_encounters.py`
13. `export_trainers.py`
14. `export_hidden_objects.py`
15. `export_map_scripts.py`
16. `export_script_candidates.py`

The order matters. Overworld offsets must exist before tile expansion, items
must exist before object extraction can resolve visible item balls, and object
coordinates must be updated after objects are extracted.

## Key Tables

- `maps`, `tilesets`, `tiles_raw`, `tiles`, `tile_images`
- `objects`, `warps`, `warp_events`
- `pokemon`, `moves`, `items`, `pokemon_learnset`, `pokemon_tmhm`
- `wild_encounters`, `encounter_slots`
- `trainer_classes`, `trainer_parties`, `trainer_party_pokemon`,
  `trainer_headers`
- `dialogue_text`, `text_pointers`
- `hidden_items`, `hidden_coins`, `hidden_objects`
- `missable_objects`
- `map_music`, `map_scripts`, `npc_movement_data`, `spin_tiles`,
  `event_flags`, `coordinate_triggers`
- `script_event_ir_blocks`, `script_event_candidates`,
  `script_event_candidate_diagnostics`, `script_event_in_game_trades`,
  `script_event_tile_overrides`, `script_event_boulder_targets`

## Runtime Integration Notes

- Trainer sight range comes from the second numeric argument in each map script's
  `trainer` macro and is exported as `trainer_headers.sight_range`.
- NPC objects link to trainer parties through `objects.trainer_class` and
  `objects.trainer_party_index`.
- Overworld maps are stitched with global coordinates, but local map coordinates
  remain important for scripts, trainer sight, object interaction, and warp
  resolution.
- Candidate adapters load all split map text files matching `text/Map*.asm`,
  not only the base `text/Map.asm`, so source branches such as the Fuchsia Gym
  Guide can be generated from their auxiliary text files.
- `spin_tiles` is generated from Red/Blue `map_coord_movement` script tables.
  The source macro uses `dbmapcoord x, y`; movement lists are stored in runtime
  order after decoding the Game Boy reverse-read RLE format.
- `script_event_candidates` uses a neutral Pokemon action vocabulary for
  supported ASM state machines. For example, generated Pokemon gifts carry
  species constants such as `LAPRAS` instead of downstream database IDs.
  Downstream tools are responsible for mapping those candidates to app-specific
  script files, handlers, and persistence.
- Static wild battle candidates are generated from `object_event` rows that
  include a Pokemon constant plus level, joined to the corresponding trainer
  header for the win flag and original battle text.
- The Pokemon Tower 6F Marowak ghost is generated from its `RESTLESS_SOUL`
  coordinate-trigger state machine. The candidate emits the source "Be
  gone..." pre-battle text, starts a L30 `MAROWAK` wild battle, and carries the
  departed-soul text as post-win actions. The original non-defeat push-right
  branch remains a documented future post-lose action.
- Snorlax wake-up battle candidates are generated from the Route 12/16
  map-script flow plus Snorlax object text. The neutral direct-interaction
  candidate requires `POKE_FLUTE`, starts the source L30 Snorlax battle, and
  hides the original `HS_*` object on win. The Red/Blue item-use fight flag
  handoff and caught-vs-calmed post-battle flavor text remain documented
  diagnostics until those branch concepts are modeled directly.
- Fishing Guru rod gifts are generated from the shared `BIT_GOT_*_ROD` +
  `YesNoChoice` + `GiveItem` state-machine shape. They emit item-name
  conditions such as `requiresItemAbsent: OLD_ROD` so downstream importers can
  resolve IDs in their own runtime data.
- The Pokemon Fan Club chairman Bike Voucher flow is generated from the
  `PokemonFanClub_CheckBikeInBag` + `YesNoChoice` + `GiveItem` state machine as
  separate already-got, has-voucher, and gift branches.
- The Pokemon Fan Club Pikachu/Seel fan boast toggle is generated from the
  paired `CheckEvent` + `SetEvent`/`ResetEvent` state machines. It emits
  conditioned normal and "mine is better" branches with neutral event-flag
  side effects.
- Simple flag-side-effect text scripts are generated when a text block has one
  local `PrintText` and one `SetEvent`/`ResetEvent` with no branching, rewards,
  battles, movement, warps, or object visibility. The Game Corner poster switch
  uses this adapter.
- Pure event-flag map scripts are generated when a map script block contains
  only `SetEvent`, `ResetEvent`, `ResetEvents`, optional
  `EnableAutoTextBoxDrawing`, source map-script state-register writes, and
  `ret`. These emit `map_script` candidates such as Blues House's entered flag
  Celadon City's temporary flag resets, and Cinnabar Island's Mansion/fossil
  walk-away resets. Boulder switch flags are excluded until a richer adapter
  can preserve the source object-position and cross-map reset behavior, not
  just the flag side effect.
- Conditional flag-mirror map scripts are generated for narrow map-load blocks
  that `CheckEvent` one source flag, `SetEvent` one follow-up flag, then fall
  through to normal script dispatch. Pallet Town's post-Oak Poké Ball flag is
  the current generated example.
- Source labels can be marked as downstream-authored runtime diagnostics when
  they are wrapper/state-dispatch fragments of behavior already owned by a game
  runtime or file-backed scripts. Current examples include Oak Lab's top-level
  dispatcher and rival text selector, Mt. Moon's fossil-area wrapper, Pewter
  City's pre-Brock escort wrapper, and Vermilion's S.S. Anne departure callback
  and dock state.
- One-shot object visibility map scripts are generated for narrow map-load
  blocks that check one source flag, `CheckAndSetEvent` one idempotence flag,
  then show or hide missable objects. Silph Co. 1F restoring its receptionist
  after Giovanni is beaten is the current generated example. Multi-branch
  visibility scripts such as Oak/rival state machines remain diagnostics until
  the neutral condition model can represent their full flag logic.
- Fighting Dojo prize Pokemon are generated from the two prize Poke Ball
  `YesNoChoice` + `GivePokemon` scripts. They emit the chosen species, level,
  selected ball visibility update, reward flags, and the already-got greedy
  dialogue branch.
- The Fighting Dojo Karate Master special flow is generated from the source
  default coordinate check, text routine, trainer object payload, and
  post-battle map script. Candidates cover direct click battle start, the
  automatic `(4,3)` challenge tile, post-battle reward prompt side effects, the
  pre-prize reminder click branch, and the final stay-and-train dialogue.
- Bill's House cell-separator flow is generated from the source Bill Pokemon
  text routine plus the walk-to-machine, machine-entry, machine-exit, and
  cleanup map scripts. The neutral `choice` action supports `continueOnNo` for
  this Red/Blue pattern where saying "No" prints an extra line but still
  continues the sequence.
- Route 25's Bill visibility sync is generated from the source
  `Route25ShowHideBillScript` map-load branch. It emits global
  `showObject`/`hideObject` actions for Bill's Pokemon form, post-ticket Bill,
  and the Nugget Bridge guy instead of treating that multi-branch missable
  object script as a manual override.
- The Viridian old man's catch tutorial is generated from the source inverted
  `YesNoChoice`, `BATTLE_TYPE_OLD_MAN`, Weedle level, and follow-up text. The
  neutral `choice` action supports `stopOnYes` for this prompt where saying
  "Yes" to being in a hurry exits, while saying "No" starts the tutorial.
- Mt. Moon fossil choices are generated from the fossil object `YesNoChoice` +
  `GiveItem` scripts. They emit original Red/Blue progression requirements,
  selected and alternate fossil visibility updates, source text, and reward
  flags. The original Super Nerd follow-up movement is represented as a
  collapsed hide-other-fossil action until downstream runtimes support the full
  movement state machine.
- The Fuchsia zoo fossil sign is generated from the two-flag
  `FuchsiaCityFossilSignText` branch. Candidates preserve the original branch
  priority for Dome Fossil, Helix Fossil, and unknown fossil text. The source
  `DisplayPokedex` helper call is recorded in metadata/diagnostics, but the
  neutral action vocabulary does not yet model it as a runtime action.
- Badge-or-event dialogue branches are generated when a Red/Blue text script
  treats a badge bit or an event flag as equivalent story progress. Viridian
  City's gambler uses this shape for the gym returned/closed dialogue: the
  extractor emits separate neutral candidates with `requiresBadge`,
  `requiresEvent`, `requiresBadgesAbsent`, and `requiresEventsAbsent`
  conditions so downstream runtimes can model the original OR without relying
  on branch order.
- Gym leader post-battle TM rewards are generated from the shared
  `DisplayTextID` + `GiveItem` + badge-bit state-machine shape. They emit the
  source beat flag, downstream-friendly `EVENT_GOT_*BADGE` compatibility flag,
  TM reward flag, item constant, and source object visibility/reset side effects
  where the ASM includes them.
- Gym leader pre-battle and post-TM advice text is generated from each
  leader's `CheckEvent`/`CheckEventReuseA` text branch plus `object_event`
  trainer payload. Candidates emit neutral `startTrainerBattle` actions for
  the before-beat branch and source advice dialogue once the TM flag is set.
- Cinnabar Gym's custom quiz-trainer click text is generated from the
  `CinnabarGymSetTrainerHeader` + `CinnabarGymStartBattleScript` flow. These
  candidates emit source trainer payload battles, post-win gate flags, and
  after-battle dialogue while leaving quiz sign branching as a separate
  runtime/source-data system.
- Cinnabar Gym's map-load helper emits the source temporary `EVENT_2A7` reset
  as a narrow map-script candidate. The gate tile replacements remain in the
  event-tile override pipeline, because those are tile-state data rather than
  dialogue/battle script actions.
- Cerulean City's bridge rival encounter is generated from the source
  coordinate trigger, Rival1 battle branch, and cleanup scripts. The neutral
  candidate preserves starter-specific party selection and post-win departure
  cleanup while leaving the exact cutscene presentation to downstream runtime
  actions.
- Route 22's first and second rival encounters are generated from the source
  coordinate trigger, Rival1/Rival2 starter-specific battle tables, and exit
  cleanup scripts. The source has trigger-index-specific exit movement
  variants; the neutral candidate records the current common movement path
  until downstream runtimes model branch-on-trigger-index cutscenes.
- Silph Co. 7F's rival encounter is generated from its two source coordinate
  triggers, Rival2 starter-specific battle table, and post-win hide-object
  cleanup. It emits separate upper/lower candidates so downstream runtimes can
  preserve the distinct movement paths.
- Standard trainer after-battle item drops are generated when the after-battle
  text prints dialogue, `CheckAndSetEvent`s a drop flag, and `ShowObject`s a
  `HS_*` missable object. This covers the Rocket Hideout B4F Rocket who drops
  the Lift Key after defeat while keeping the pre-battle trainer runtime
  authoritative.
- Trainer end/after-battle text blocks that set an extra progression flag are
  generated as post-battle map-script candidates. Lance's room uses this to
  bridge the source trainer-header win flag into `EVENT_BEAT_LANCE`; text
  blocks that only set the same flag as their trainer header are marked
  runtime-covered instead of becoming duplicate script JSON.
- Fixed story item rewards such as the Bike Shop voucher exchange, Bill's S.S.
  Ticket, Oak's Parcel, the Warden's HM04, Old Amber, the S.S. Anne Captain's
  HM01, Daisy's Town Map, and Copycat's TM31 exchange are generated from their
  Red/Blue ASM state-machine labels. They emit source item constants, flag
  requirements, item gates, object visibility side effects, and hydrated
  received-item text.
- Celadon rooftop drink-for-TM exchanges are generated from the Little Girl's
  drink menu state machine as one conditional branch per drink. The neutral
  action vocabulary does not yet model item-selection menus, so generated
  candidates gate on `requiresItem` and emit the selected drink/TM reward path.
- Paid Yes/No state machines are generated for the Museum 1F entry ticket and
  the Mt. Moon Pokecenter Magikarp salesman. Candidates emit `requiresMoney`,
  `requiresMoneyBelow`, and `takeMoney` so downstream runtimes can select
  success/insufficient-money branches server-side before playing dialogue.
  Day Care and the broader Game Corner/Safari Zone encounter runtimes remain
  diagnostics/runtime-owned because they involve broader state machines than a
  simple paid cutscene. Safari Gate entry/exit itself is generated by the
  dedicated `safari_zone_gate_v1` adapter.
- Day Care's gentleman script is marked as `daycare_runtime_v1` coverage
  instead of a generated candidate. The source script combines party
  selection, storage transfer, HM-move rejection, fee calculation, withdrawal,
  step growth, and level-up move learning; downstream runtimes should execute
  those mechanics server-side.
- Game Corner NPC coin gifts are generated from the shared `COIN_CASE` +
  `Has9990Coins` + `AddBCDPredef` + `EVENT_GOT_*_COINS` state-machine shape.
  Candidates emit neutral `requiresCoins`, `requiresCoinsBelow`, and
  `giveCoins` actions, preserving the original full-Coin-Case threshold while
  leaving slot machines, prize exchange, and hidden coins to downstream runtime
  systems.
- Game Corner Clerk 1's paid coin purchase is generated from the `COIN_CASE` +
  `Has9990Coins` + `HasEnoughMoney` + BCD add/subtract state machine.
  Candidates preserve the source Yes/No prompt and emit neutral
  `requiresMoney`, `requiresMoneyBelow`, `takeMoney`, and `giveCoins` actions
  for downstream server-authoritative execution.
- The Silph Co. 9F nurse is generated from her `EVENT_BEAT_SILPH_CO_GIOVANNI`
  branch and `HealParty` helper call. The candidate collapses fade/music
  details into a neutral `healParty` action plus the source dialogue before and
  after Giovanni is beaten.
- Pokemon Mansion secret switch prompts are generated from the
  `YesNoChoice` + `CheckAndSetEvent`/`ResetEventReuseHL` state-machine shape.
  Candidates emit a neutral `toggleEvent` action for the shared
  `EVENT_MANSION_SWITCH_ON` flag; door tile replacements remain a separate
  event-tile override data source for downstream runtimes.
- Pokemon Mansion switch door tile override candidates are generated from the
  map-load `ReplaceTileBlock` helpers on 1F, 2F, 3F, and B1F. They preserve the
  source block coordinates, closed/open block IDs, and shared
  `EVENT_MANSION_SWITCH_ON` gate without assuming a downstream tileset schema.
- Route 23 badge-gate candidates are generated from `Route23DefaultScript`,
  `Route23GuardsYCoords`, the Route 23 `object_event` rows, and
  `Route23CheckForBadgeScript`. They emit neutral `requiresBadge` /
  `requiresBadgeAbsent` conditions plus the original
  `EVENT_PASSED_*BADGE_CHECK` pass flags.
- Vermilion City's S.S. Anne guard candidate branches are generated from
  `VermilionCityDefaultScript`, `SSAnneTicketCheckCoords`, and
  `VermilionCitySailor1Text`. They preserve the source facing-down coordinate
  gate, S.S. Ticket requirement, ship-departed flag branch, and one-step
  push-back movement with neutral `requiresItem`, `requiresItemAbsent`,
  `requiresEvent`, and `requiresPlayerFacing` conditions.
- Lorelei, Bruno, and Agatha room entrance guard candidates are generated from
  their shared Elite Four coordinate-gate state machine. They preserve the
  source entrance coordinate array, first-entry six-step auto-walk,
  `EVENT_AUTOWALKED_INTO_*` flag, and pre-battle "Don't run away!" one-step
  push-back branch. Lance's room uses a separate tile-override adapter for its
  two-block entrance lock and a separate default-script adapter because its
  source room logic combines battle, door-lock, and forced-walk branches.
- Elite Four exit-block tile override candidates are generated from the
  `CheckEvent` + `ReplaceTileBlock` map-load scripts for Lorelei, Bruno, and
  Agatha. They emit original map names, event flags, block coordinates, and
  Game Boy block IDs; downstream importers resolve those into runtime tile
  images and collision rows.
- Lance's room entrance blocks are generated as event tile override candidates
  from `LanceShowOrHideEntranceBlocks`. They emit open `$31/$32` and closed
  `$72/$73` block replacements gated by `EVENT_LANCES_ROOM_LOCK_DOOR`.
- Lance's room default script is generated as three coordinate candidates from
  `LanceTriggerMovementCoords`: the two near-Lance tiles start Lance's source
  Elite Four battle, the two hallway tiles set `EVENT_LANCES_ROOM_LOCK_DOOR`,
  and the far entrance tile emits the source RLE forced-walk path toward Lance.
- Pokemon Tower 7F's Rocket exit movement table is marked runtime-covered with
  `pokemon_tower7f_rocket_exit_runtime_v1`. The source table is keyed by Rocket
  sprite index and player battle coordinate, so downstream runtimes should
  attach it to standard Rocket trainer post-win actions rather than generate a
  separate NPC-click script.
- Cinnabar Gym's default quiz wrong-answer handoff is marked runtime-covered
  with `cinnabar_gym_quiz_trainer_runtime_v1`; generated trainer scripts and
  event tile overrides own the persistent battle/gate effects. Name Rater's
  shared yes/no helper is marked runtime-covered with `name_rater_runtime_v1`
  because downstream runtimes need a party picker, original-trainer validation,
  and nickname editor instead of linear cutscene JSON.
- Silph Co. Card Key door tile override candidates are generated from each
  floor's gate callback and the shared Card Key open-block behavior. They emit
  one closed/open replacement pair per source door flag while staying neutral
  about downstream tile image IDs.
- Victory Road boulder-switch tile override candidates are generated from the
  map-load checks that call `ReplaceTileBlock` after boulder switch flags are
  set. These candidates cover tile state only; boulder movement, switch
  detection, hole drops, and route/map reset behavior remain separate runtime
  systems for downstream projects.
- Victory Road boulder switch and hole target rows are generated from
  `CheckBoulderCoords` coordinate tables plus source event flags into
  `script_event_boulder_targets`. The 3F hole target preserves the original
  `HS_*` missable-object constants for downstream runtimes to resolve through
  their object/import data. Route 23 and Victory Road 2F boulder map-load reset
  labels are marked as runtime-covered diagnostics.
- Vermilion Gym trash-puzzle door tile override candidates are generated from
  `VermilionGymSetDoorTile`, preserving the original second-lock flag, closed
  double-door block, and open floor block as a neutral block replacement pair.
- The Game Corner Rocket Hideout entrance tile override is generated from
  `GameCornerSetRocketHideoutDoorTile` and `GameCornerPosterText`, preserving
  the source closed/open block IDs behind `EVENT_FOUND_ROCKET_HIDEOUT`.
- Cinnabar Lab fossil revival candidates are generated from the fossil
  scientist script, `FossilsList`, and `engine/events/cinnabar_lab.asm`. The
  original item-selection menu is emitted as one item-gated branch per fossil,
  with a source Yes/No confirmation and item-specific revival flags for
  downstream persistent state.
- Rocket reward-battle scripts are generated for source state machines that
  chain dialogue/reward actions into `EngageMapTrainer`/`InitBattleEnemyParameters`.
  They emit source trainer class/party indexes from object events, battle win
  flags, and post-win reward/object-visibility actions.
- Side-effect-free Yes/No informational dialogue is generated only when the ASM
  has no rewards, flags, movement, object visibility, warps, trainer battles,
  money checks, or stateful helper calls. Stateful choices such as Oak starters
  and the Name Rater stay in diagnostics for bespoke adapters.
- Flag-gated informational dialogue is generated only when one `CheckEvent`
  selects between two text branches and the block has no rewards, choices,
  movement, object visibility, warps, trainer battles, map tile changes, or
  stateful helper calls. Generated branches emit `requiresEvent` and
  `requiresEventAbsent` conditions. Text branches may point to local labels
  such as `.BeatMistyText` or same-file global labels such as
  `GameCornerGymGuideTheyOfferRarePokemonText`. Rival labels stay unsupported
  here because their text is part of broader battle/movement state machines.
- Map text-pointer switch helpers are marked runtime-covered when the source
  script only chooses between alternate text tables and downstream behavior is
  already represented by explicit flag-gated dialogue branches. Viridian Mart's
  `EVENT_OAK_GOT_PARCEL` pointer switch is the current example.
- Seafoam Islands boulder-hole, strong-current, surf-blocking, and Route 20
  reset scripts are marked runtime-covered diagnostics. They combine
  multi-map object visibility, dungeon hole warps, forced surf movement, and
  map-load cleanup, so downstream games should model them as authoritative
  movement/map-state systems instead of generated linear cutscene JSON.
- Source scripts that only toggle `BIT_NO_NPC_FACE_PLAYER` are marked
  presentation runtime-covered. The S.S. Anne Captain map-load helper is the
  current example; its HM01 reward remains a separate generated/preserved
  script candidate.
- Badge-bit Gym Guide dialogue is generated from `wBeatGymFlags` +
  `BIT_*BADGE` checks. Candidates emit neutral `requiresBadge` and
  `requiresBadgeAbsent` conditions so downstream importers can map badges to
  their own progression flags while preserving Red/Blue text branches and
  simple Yes/No advice prompts.
- Gate upstairs/binocular dialogue that routes through
  `GateUpstairsScript_PrintIfFacingUp` is generated with a neutral
  `requiresPlayerFacing: UP` condition. This preserves the original Red/Blue
  facing gate while leaving downstream runtimes to check their authoritative
  player-facing state.
- Item-received text that uses Game Boy `wStringBuffer` item placeholders is
  hydrated with the awarded item constant in neutral candidates, for example
  `a BIKE VOUCHER!`.
- Standard trainer battles are intentionally covered through
  `trainer_headers`, `trainer_parties`, and `trainer_party_pokemon` instead of
  generated script candidates. `script_event_candidate_diagnostics` marks those
  blocks as `covered` with `trainer_battle_runtime_v1`.
- Spin/arrow tile source labels that feed `spin_tiles` are similarly marked as
  `covered` with `spin_tile_runtime_v1`; they are runtime forced-movement data,
  not cutscene JSON.
- `script_event_in_game_trades` joins the TradeMons table with map script
  `DoInGameTradeDialogue` call sites. It includes inactive rows, such as the
  unused `TRADE_FOR_CHIKUCHIKU`, as covered diagnostics rather than unsupported
  script backlog so downstream importers can decide whether to seed only active
  NPC trades or expose inactive source data.
- `script_event_ir_blocks` inventories every top-level script label with
  detected text refs, event flags, movement refs, item/Pokemon reward hints,
  object visibility hints, battle hints, and warp hints. Diagnostics mark
  generated candidates, runtime-covered blocks, unsupported interesting blocks,
  and ambiguous blocks that need adapter logic.

## Troubleshooting

```bash
sqlite3 ../pokemon.db ".tables"
sqlite3 ../pokemon.db "SELECT COUNT(*) FROM tiles"
sqlite3 ../pokemon.db "SELECT map_name, sight_range FROM trainer_headers LIMIT 10"
```

If tilesets fail to convert, confirm `rgbgfx` is installed through RGBDS.
