#!/usr/bin/env python3
"""Build and transactionally publish every generated extractor artifact.

Each exporter writes to private sibling staging paths.  Nothing replaces the
last successful database, manifests, tile images, or viewer bundle until every
step and integrity check has passed.  Publication uses same-filesystem atomic
renames and rolls all previously installed artifacts back if any rename fails.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import uuid

from config import (
    AUDIO_MANIFEST_PATH,
    AUDIO_OUTPUT_DIR,
    DB_PATH,
    GRAPHICS_OUTPUT_DIR,
    SCRIPT_EVENT_BOULDER_TARGETS_PATH,
    SCRIPT_EVENT_CANDIDATES_PATH,
    SCRIPT_EVENT_CONDITIONAL_DIALOGUE_PATH,
    SCRIPT_EVENT_DIAGNOSTICS_PATH,
    SCRIPT_EVENT_IR_PATH,
    SCRIPT_EVENT_OBJECT_VISIBILITY_PATH,
    SCRIPT_EVENT_TILE_OVERRIDES_PATH,
    SCRIPT_EVENT_TRADES_PATH,
    TILE_IMAGE_OUTPUT_DIR,
    VIEWER_ASSET_DIR,
    VIEWER_DATA_DIR,
)


SCRIPT_DIR = Path(__file__).resolve().parent

SCRIPTS = [
    # Map infrastructure (order-dependent).
    "export_map.py",
    "export_warps.py",
    "update_zone_coordinates.py",
    "create_zones_and_tiles.py",
    "export_items.py",
    "export_objects.py",
    "update_object_coordinates.py",
    # Relational game data.
    "export_pokemon.py",
    "export_moves.py",
    "export_text.py",
    "export_learnsets.py",
    "export_wild_encounters.py",
    "export_trainers.py",
    "export_hidden_objects.py",
    "export_map_scripts.py",
    "export_script_candidates.py",
    "export_audio_manifest.py",
    "export_graphics.py",
    # Release/provenance metadata must see every DB-producing exporter.
    "export_release_metadata.py",
    "export_viewer_data.py",
]

# Environment variable, final path, and whether the output is a directory.
OUTPUTS = [
    ("POKEMON_EXTRACTOR_DB", DB_PATH, False),
    ("POKEMON_EXTRACTOR_SCRIPT_EVENT_CANDIDATES", SCRIPT_EVENT_CANDIDATES_PATH, False),
    ("POKEMON_EXTRACTOR_SCRIPT_EVENT_IR", SCRIPT_EVENT_IR_PATH, False),
    ("POKEMON_EXTRACTOR_SCRIPT_EVENT_DIAGNOSTICS", SCRIPT_EVENT_DIAGNOSTICS_PATH, False),
    ("POKEMON_EXTRACTOR_SCRIPT_EVENT_TRADES", SCRIPT_EVENT_TRADES_PATH, False),
    ("POKEMON_EXTRACTOR_SCRIPT_EVENT_TILE_OVERRIDES", SCRIPT_EVENT_TILE_OVERRIDES_PATH, False),
    ("POKEMON_EXTRACTOR_SCRIPT_EVENT_BOULDER_TARGETS", SCRIPT_EVENT_BOULDER_TARGETS_PATH, False),
    ("POKEMON_EXTRACTOR_SCRIPT_EVENT_OBJECT_VISIBILITY", SCRIPT_EVENT_OBJECT_VISIBILITY_PATH, False),
    (
        "POKEMON_EXTRACTOR_SCRIPT_EVENT_CONDITIONAL_DIALOGUE",
        SCRIPT_EVENT_CONDITIONAL_DIALOGUE_PATH,
        False,
    ),
    ("POKEMON_EXTRACTOR_AUDIO_MANIFEST", AUDIO_MANIFEST_PATH, False),
    ("POKEMON_EXTRACTOR_GRAPHICS_DIR", GRAPHICS_OUTPUT_DIR, True),
    ("POKEMON_EXTRACTOR_TILE_IMAGE_DIR", TILE_IMAGE_OUTPUT_DIR, True),
    ("POKEMON_EXTRACTOR_VIEWER_DATA_DIR", VIEWER_DATA_DIR, True),
    ("POKEMON_EXTRACTOR_VIEWER_ASSET_DIR", VIEWER_ASSET_DIR, True),
]


class PipelineError(RuntimeError):
    """A generation or release validation step failed."""


def path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def remove_path(path: Path) -> None:
    """Remove one exact staging/backup path without following symlinks."""
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def make_staging_outputs(token: str, *, include_audio: bool = False):
    outputs = []
    configured_outputs = list(OUTPUTS)
    if include_audio:
        configured_outputs.append(
            ("POKEMON_EXTRACTOR_AUDIO_DIR", AUDIO_OUTPUT_DIR, True)
        )
    for environment_name, final_path, is_directory in configured_outputs:
        final_path = Path(final_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        staging_path = final_path.with_name(f".{final_path.name}.{token}.stage")
        backup_path = final_path.with_name(f".{final_path.name}.{token}.backup")
        if path_exists(staging_path) or path_exists(backup_path):
            raise PipelineError(f"Staging path collision: {staging_path}")
        outputs.append(
            {
                "environment_name": environment_name,
                "final": final_path,
                "staging": staging_path,
                "backup": backup_path,
                "is_directory": is_directory,
            }
        )
    return outputs


def run_script(script_name: str, environment: dict[str, str]) -> None:
    print(f"\n{'=' * 60}")
    print(f">>> Running {script_name}...")
    print(f"{'=' * 60}")
    try:
        subprocess.run(
            [sys.executable, script_name],
            check=True,
            cwd=SCRIPT_DIR,
            env=environment,
        )
    except subprocess.CalledProcessError as error:
        raise PipelineError(f"Error running {script_name}: {error}") from error


def render_complete_audio(outputs, environment: dict[str, str]) -> None:
    """Render every normalized audio asset into the current release staging set."""
    manifest_path = next(
        output["staging"]
        for output in outputs
        if output["environment_name"] == "POKEMON_EXTRACTOR_AUDIO_MANIFEST"
    )
    output_path = next(
        output["staging"]
        for output in outputs
        if output["environment_name"] == "POKEMON_EXTRACTOR_AUDIO_DIR"
    )
    print(f"\n{'=' * 60}")
    print(">>> Rendering complete lossless/distribution audio bundle...")
    print(f"{'=' * 60}")
    try:
        subprocess.run(
            [
                sys.executable,
                "render_audio_assets.py",
                "--build-gbs",
                "--kind",
                "all",
                "--manifest",
                str(manifest_path),
                "--out-dir",
                str(output_path),
            ],
            check=True,
            cwd=SCRIPT_DIR,
            env=environment,
        )
    except subprocess.CalledProcessError as error:
        raise PipelineError(f"Error rendering complete audio bundle: {error}") from error


def require_table(conn: sqlite3.Connection, table: str, *, expected_count=None) -> int:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    if not exists:
        raise PipelineError(f"Missing generated table: {table}")
    count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    if count == 0:
        raise PipelineError(f"Generated table is empty: {table}")
    if expected_count is not None and count != expected_count:
        raise PipelineError(
            f"Unexpected {table} row count: expected {expected_count}, found {count}"
        )
    print(f"Validated {table}: {count} rows")
    return count


def validate_generated_database(
    db_path: Path, *, graphics_output_root: Path | None = None
) -> None:
    """Reject incomplete, corrupt, dangling, or host-specific releases."""
    conn = sqlite3.connect(db_path)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise PipelineError(f"SQLite integrity check failed: {integrity}")

        foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            preview = ", ".join(map(str, foreign_key_errors[:10]))
            raise PipelineError(
                f"SQLite foreign-key check found {len(foreign_key_errors)} errors: {preview}"
            )

        for table, expected_count in {
            "maps": 248,
            "tilesets": 24,
            "pokemon": 151,
            "pokemon_evolutions": 72,
            "pokemon_default_moves": 362,
            "moves": 165,
            "wild_encounters": 1271,
            "hidden_objects": 198,
            "warps": 805,
            "warp_sources": 805,
            "warp_events": 805,
            "audio_assets": 561,
            "move_audio_assets": 165,
            "graphic_assets": 2086,
            "graphic_derivations": 509,
            "graphic_source_links": 870,
            "schema_metadata": 1,
            "game_releases": 2,
            "extraction_runs": 1,
        }.items():
            require_table(conn, table, expected_count=expected_count)

        for table in [
            "script_event_ir_blocks",
            "script_event_candidates",
            "script_event_candidate_diagnostics",
            "script_event_in_game_trades",
            "script_event_tile_overrides",
            "script_event_boulder_targets",
            "script_event_object_visibility",
            "script_event_conditional_dialogue",
            "script_event_candidate_actions",
            "script_event_candidate_conditions",
            "script_event_candidate_references",
            "script_event_ir_references",
            "extracted_tables",
            "table_provenance",
            "extracted_entities",
            "entity_provenance",
            "spin_tiles",
            "map_scripts",
            "npc_movement_data",
            "event_flags",
            "coordinate_triggers",
            "text_pointers",
            "trainer_headers",
        ]:
            require_table(conn, table)

        from export_moves import (  # Imported late so focused tests stay isolated.
            validate_dependent_move_references,
            validate_moves_table,
        )

        validate_moves_table(conn)
        validate_dependent_move_references(conn, require_tables=True)

        from export_pokemon import validate_pokemon_default_moves

        validate_pokemon_default_moves(conn)

        from export_audio_manifest import validate_audio_tables
        from export_graphics import validate_graphics_catalog
        from export_release_metadata import validate_release_metadata
        from export_map_scripts import validate_map_script_relationships
        from export_script_candidates import validate_normalized_script_tables
        from export_warps import validate_warps

        validate_audio_tables(conn)
        validate_warps(conn)
        validate_map_script_relationships(conn)
        validate_normalized_script_tables(conn)
        validate_release_metadata(conn)
        if graphics_output_root is None:
            raise PipelineError("Graphics staging path is required for validation")
        validate_graphics_catalog(conn, output_root=graphics_output_root)

        bad_encounter_groups = conn.execute(
            """
            SELECT map_id, encounter_type, version
            FROM wild_encounters
            WHERE encounter_type IN ('grass', 'water')
            GROUP BY map_id, encounter_type, version
            HAVING COUNT(*) != 10
                OR MIN(slot_index) != 1
                OR MAX(slot_index) != 10
                OR COUNT(DISTINCT slot_index) != 10
            """
        ).fetchall()
        if bad_encounter_groups:
            raise PipelineError(
                f"Incomplete release-specific encounter groups: {bad_encounter_groups[:10]}"
            )

        grass_water_count = conn.execute(
            """
            SELECT COUNT(*) FROM wild_encounters
            WHERE encounter_type IN ('grass', 'water')
            """
        ).fetchone()[0]
        if grass_water_count != 1160:
            raise PipelineError(
                "Unexpected release-specific grass/water encounter count: "
                f"expected 1160, found {grass_water_count}"
            )

        unresolved_maps = conn.execute(
            """
            SELECT COUNT(*) FROM wild_encounters
            WHERE encounter_type IN ('grass', 'water') AND map_id IS NULL
            """
        ).fetchone()[0]
        if unresolved_maps:
            raise PipelineError(f"Wild encounters have {unresolved_maps} unresolved maps")

        unresolved_hidden = conn.execute(
            "SELECT COUNT(*) FROM hidden_objects WHERE map_id IS NULL"
        ).fetchone()[0]
        if unresolved_hidden:
            raise PipelineError(f"Hidden objects have {unresolved_hidden} unresolved maps")

        absolute_paths = conn.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT blockset_path AS path FROM tilesets
                UNION ALL SELECT tileset_path FROM tilesets
                UNION ALL SELECT image_path FROM tile_images
            )
            WHERE path LIKE '/%'
               OR path GLOB '[A-Za-z]:*'
               OR path LIKE '\\\\%'
            """
        ).fetchone()[0]
        if absolute_paths:
            raise PipelineError(
                f"Generated database contains {absolute_paths} host-specific absolute paths"
            )
    finally:
        conn.close()


def validate_staged_outputs(outputs) -> None:
    for output in outputs:
        path = output["staging"]
        if not path_exists(path):
            raise PipelineError(f"Exporter did not create required output: {path}")
        if output["is_directory"]:
            if not any(path.iterdir()):
                raise PipelineError(f"Generated directory is empty: {path}")
        elif path.stat().st_size == 0:
            raise PipelineError(f"Generated file is empty: {path}")


def publish_outputs(outputs) -> None:
    """Install all staged outputs, keeping the database as the commit marker."""
    database_output = next(
        output for output in outputs if output["environment_name"] == "POKEMON_EXTRACTOR_DB"
    )
    ordered = [output for output in outputs if output is not database_output]
    ordered.append(database_output)
    installed = []

    try:
        for output in ordered:
            final_path = output["final"]
            backup_path = output["backup"]
            had_previous = path_exists(final_path)
            if had_previous:
                os.replace(final_path, backup_path)
            try:
                os.replace(output["staging"], final_path)
            except Exception:
                if had_previous:
                    os.replace(backup_path, final_path)
                raise
            installed.append((output, had_previous))
    except Exception as error:
        for output, had_previous in reversed(installed):
            remove_path(output["final"])
            if had_previous and path_exists(output["backup"]):
                os.replace(output["backup"], output["final"])
        raise PipelineError(f"Could not publish generated release: {error}") from error

    for output, had_previous in installed:
        if had_previous:
            remove_path(output["backup"])


def cleanup_outputs(outputs) -> None:
    for output in outputs:
        remove_path(output["staging"])
        # Backups are deliberately not cleaned here. publish_outputs removes them
        # after success; if rollback itself fails, retaining one is the safest
        # recoverable state.


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--with-audio",
        action="store_true",
        help=(
            "Render all 561 source-faithful FLAC/Ogg assets and publish them in "
            "the same transaction (requires RGBDS, gbsplay, and ffmpeg)."
        ),
    )
    return parser


def main(argv=None) -> None:
    args = build_argument_parser().parse_args(argv)
    token = uuid.uuid4().hex
    outputs = make_staging_outputs(token, include_audio=args.with_audio)
    environment = os.environ.copy()
    for output in outputs:
        environment[output["environment_name"]] = str(output["staging"])

    try:
        for script in SCRIPTS:
            run_script(script, environment)

        if args.with_audio:
            render_complete_audio(outputs, environment)

        staged_db = next(
            output["staging"]
            for output in outputs
            if output["environment_name"] == "POKEMON_EXTRACTOR_DB"
        )
        staged_graphics = next(
            output["staging"]
            for output in outputs
            if output["environment_name"] == "POKEMON_EXTRACTOR_GRAPHICS_DIR"
        )
        validate_generated_database(
            staged_db, graphics_output_root=staged_graphics
        )
        if args.with_audio:
            from render_audio_assets import validate_render_bundle

            staged_manifest = next(
                output["staging"]
                for output in outputs
                if output["environment_name"]
                == "POKEMON_EXTRACTOR_AUDIO_MANIFEST"
            )
            staged_audio = next(
                output["staging"]
                for output in outputs
                if output["environment_name"] == "POKEMON_EXTRACTOR_AUDIO_DIR"
            )
            validate_render_bundle(
                staged_audio, staged_manifest, require_complete=True
            )
        validate_staged_outputs(outputs)
        publish_outputs(outputs)
    finally:
        cleanup_outputs(outputs)

    print(f"\n{'=' * 60}")
    print("All reprocessing steps completed and were published successfully.")
    print(f"{'=' * 60}")
    print(f"\nDatabase: {DB_PATH}")
    print(f"Viewer data: {VIEWER_DATA_DIR}")
    print(f"Viewer assets: {VIEWER_ASSET_DIR}")


if __name__ == "__main__":
    try:
        main()
    except PipelineError as error:
        print(f"!!! {error}", file=sys.stderr)
        raise SystemExit(1) from error
