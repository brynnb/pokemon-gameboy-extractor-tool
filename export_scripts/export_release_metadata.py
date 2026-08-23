#!/usr/bin/env python3
"""Export deterministic release, run, source-file, and row provenance metadata.

The game data is shared by the Red and Blue builds.  This exporter records
those releases independently while keeping source files and extracted entities
attached to the extraction run that produced them.  No wall-clock timestamp is
used: ``SOURCE_DATE_EPOCH`` (or the source commit timestamp as a fallback) is
the sole time input.
"""

from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import subprocess
from typing import Iterable

from config import DB_PATH, GAME_DATA_ROOT, PROJECT_ROOT


SCHEMA_NAME = "pokemon-gameboy-extractor"
SCHEMA_VERSION = 2
MINIMUM_READER_VERSION = 2

RELEASES = (
    (1, "red", "Pokemon Red", "red", "game-boy", "international", "en", "_RED"),
    (2, "blue", "Pokemon Blue", "blue", "game-boy", "international", "en", "_BLUE"),
)

FILE_TYPES = frozenset(
    {
        "assembly",
        "audio",
        "binary",
        "build",
        "data",
        "document",
        "image",
        "source-code",
        "symlink",
        "other",
    }
)

DIRECT_SOURCE_COLUMNS = frozenset(
    {
        "source_file",
        "source_path",
        "header_file",
        "blockset_path",
        "tileset_path",
    }
)
JSON_PATH_KEYS = frozenset(
    {
        "sourcefile",
        "sourcepath",
        "scriptpath",
        "headerfile",
        "blocksetpath",
        "tilesetpath",
    }
)
OWN_TABLES = frozenset(
    {
        "schema_metadata",
        "game_releases",
        "extraction_runs",
        "extraction_run_releases",
        "source_files",
        "extracted_tables",
        "table_provenance",
        "extracted_entities",
        "entity_provenance",
    }
)

# Canonical outputs are deliberately excluded from the extractor worktree
# fingerprint.  Otherwise a release would describe the previous generated
# files that happened to be present before its atomic staging run, creating a
# self-referential and non-reproducible identity.
GENERATED_EXTRACTOR_PATHS = frozenset(
    {
        "audio_manifest.json",
        "pokemon.db",
        "script_event_boulder_targets.json",
        "script_event_candidates.json",
        "script_event_conditional_dialogue.json",
        "script_event_diagnostics.json",
        "script_event_in_game_trades.json",
        "script_event_ir.json",
        "script_event_object_visibility.json",
        "script_event_tile_overrides.json",
    }
)
GENERATED_EXTRACTOR_PREFIXES = (
    "build/",
    "export_scripts/tile_images/",
    "pokemon-phaser/public/viewer-assets/",
    "pokemon-phaser/public/viewer-data/",
)
PIPELINE_TEMP_COMPONENT = re.compile(
    r"^\..+\.[0-9a-f]{32}\.(?:stage|backup)$"
)


def _quote_identifier(identifier: str) -> str:
    """Quote an identifier obtained from SQLite's own schema catalog."""
    return '"' + identifier.replace('"', '""') + '"'


def _run_git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def git_revision(repo: Path) -> str:
    """Return the exact Git object ID for ``repo`` or raise a useful error."""
    try:
        revision = _run_git(repo, "rev-parse", "HEAD")
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError(f"Cannot determine Git revision for {repo}") from error
    if not revision:
        raise ValueError(f"Git returned an empty revision for {repo}")
    return revision


def _excluded_extractor_path(relative: str, source_relative: str) -> bool:
    return (
        relative == source_relative
        or relative.startswith(source_relative + "/")
        or relative in GENERATED_EXTRACTOR_PATHS
        or any(relative.startswith(prefix) for prefix in GENERATED_EXTRACTOR_PREFIXES)
        # Atomic publication creates these beside each final output.  They are
        # transient generated state, and the staged database would otherwise
        # fingerprint itself while release metadata is being written.
        or any(PIPELINE_TEMP_COMPONENT.fullmatch(part) for part in PurePosixPath(relative).parts)
    )


def _extractor_catalog_paths(project_root: Path, source_relative: str) -> list[str]:
    """Return deterministic generator-input paths, including non-ignored additions."""
    try:
        top_level = Path(_run_git(project_root, "rev-parse", "--show-toplevel")).resolve()
        if top_level != project_root:
            raise ValueError("project root is not the Git root")
        output = subprocess.run(
            [
                "git",
                "-C",
                str(project_root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
        paths = [
            value.decode("utf-8", "surrogateescape")
            for value in output.split(b"\0")
            if value
        ]
    except (OSError, subprocess.CalledProcessError, ValueError):
        paths = [
            path.relative_to(project_root).as_posix()
            for path in project_root.rglob("*")
            if not any(part == ".git" for part in path.relative_to(project_root).parts)
        ]
    return sorted(
        set(
            relative
            for relative in paths
            if not _excluded_extractor_path(relative, source_relative)
        )
    )


def _extractor_worktree_dirty(project_root: Path, source_relative: str) -> bool:
    """Return whether a Git worktree has relevant tracked or untracked changes."""
    try:
        output = subprocess.run(
            [
                "git",
                "-C",
                str(project_root),
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return False

    records = output.split(b"\0")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4:
            return True
        status_code = record[:2]
        relative = record[3:].decode("utf-8", "surrogateescape")
        old_relative = None
        if b"R" in status_code or b"C" in status_code:
            if index < len(records):
                old_relative = records[index].decode("utf-8", "surrogateescape")
                index += 1
        if not _excluded_extractor_path(relative, source_relative):
            return True
        if old_relative and not _excluded_extractor_path(old_relative, source_relative):
            return True
    return False


def extractor_worktree_state(project_root: Path, source_root: Path) -> tuple[str, bool]:
    """Hash exact generator inputs and report whether their Git state is dirty."""
    source_relative = source_root.relative_to(project_root).as_posix()
    digest = hashlib.sha256()
    for relative in _extractor_catalog_paths(project_root, source_relative):
        path = project_root / relative
        if path.is_symlink():
            payload = os.fsencode(os.readlink(path))
            kind = "symlink"
            mode = "symlink"
        elif path.is_file():
            payload = path.read_bytes()
            kind = "file"
            mode = "executable" if path.stat().st_mode & stat.S_IXUSR else "regular"
        elif path.exists():
            # A Gitlink is a directory in the checkout.  Its exact revision is
            # already recorded separately for the source tree; other Gitlinks
            # are represented by their checked-out revision when available.
            try:
                payload = git_revision(path).encode("ascii")
            except ValueError:
                payload = b""
            kind = "gitlink"
            mode = "directory"
        else:
            # A tracked deletion must change the fingerprint rather than
            # silently disappearing from the catalog.
            payload = b""
            kind = "missing"
            mode = "missing"
        record = {
            "kind": kind,
            "mode": mode,
            "path": relative,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
        }
        digest.update(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest(), _extractor_worktree_dirty(
        project_root, source_relative
    )


def resolve_source_date_epoch(source_root: Path, explicit_epoch=None) -> int:
    """Resolve the reproducible build epoch without consulting wall-clock time."""
    value = explicit_epoch
    if value is None:
        value = os.environ.get("SOURCE_DATE_EPOCH")
    if value is None:
        try:
            value = _run_git(source_root, "show", "-s", "--format=%ct", "HEAD")
        except (OSError, subprocess.CalledProcessError) as error:
            raise ValueError(
                "SOURCE_DATE_EPOCH is required when the source tree has no Git commit"
            ) from error
    try:
        epoch = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid SOURCE_DATE_EPOCH: {value!r}") from error
    if epoch < 0:
        raise ValueError("SOURCE_DATE_EPOCH must be non-negative")
    return epoch


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _portable_relative_path(path: Path, project_root: Path) -> str:
    relative = path.relative_to(project_root).as_posix()
    if not is_portable_relative_path(relative):
        raise ValueError(f"Non-portable repository path: {relative!r}")
    return relative


def is_portable_relative_path(value: str) -> bool:
    """Return whether a string is a normalized, repository-relative POSIX path."""
    if not value or "\\" in value or value.startswith("/"):
        return False
    if len(value) >= 2 and value[0].isalpha() and value[1] == ":":
        return False
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    return path.as_posix() == value


def classify_source_file(path: Path, *, is_symlink: bool = False) -> str:
    """Classify a source file using a small format-neutral vocabulary."""
    if is_symlink:
        return "symlink"
    name = path.name.lower()
    suffix = path.suffix.lower()
    if suffix in {".asm", ".inc"}:
        return "assembly"
    if suffix in {".ogg", ".mp3", ".wav", ".flac", ".aiff", ".mid"}:
        return "audio"
    if suffix in {".png", ".gif", ".jpg", ".jpeg", ".webp", ".svg"}:
        return "image"
    if suffix in {
        ".1bpp",
        ".2bpp",
        ".bin",
        ".blk",
        ".bst",
        ".gb",
        ".gbc",
        ".o",
        ".pic",
        ".rle",
        ".tilecoll",
        ".tilemap",
    }:
        return "binary"
    if suffix in {".json", ".csv", ".tsv", ".toml", ".yaml", ".yml"}:
        return "data"
    if suffix in {".md", ".rst", ".txt"}:
        return "document"
    if suffix in {".c", ".h", ".js", ".mjs", ".py", ".sh", ".ts", ".tsx"}:
        return "source-code"
    if name in {"makefile", "dockerfile"} or suffix in {".mk", ".link", ".template"}:
        return "build"
    return "other"


def _git_catalog_paths(source_root: Path) -> list[Path] | None:
    """Return tracked/non-ignored files when ``source_root`` is a Git root."""
    try:
        top_level = Path(_run_git(source_root, "rev-parse", "--show-toplevel")).resolve()
        if top_level != source_root:
            return None
        output = subprocess.run(
            [
                "git",
                "-C",
                str(source_root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None

    relative_paths = sorted(
        (Path(os.fsdecode(value)) for value in output.split(b"\0") if value),
        key=lambda value: value.as_posix(),
    )
    paths = []
    for relative in relative_paths:
        path = source_root / relative
        if not path.exists() and not path.is_symlink():
            raise ValueError(f"Git source file is missing from the worktree: {relative}")
        paths.append(path)
    return paths


def _filesystem_catalog_paths(source_root: Path) -> list[Path]:
    paths = []
    for path in source_root.rglob("*"):
        relative = path.relative_to(source_root)
        if ".git" in relative.parts:
            continue
        if path.is_file() or path.is_symlink():
            paths.append(path)
    return sorted(paths, key=lambda value: value.relative_to(source_root).as_posix())


def collect_source_files(project_root: Path, source_root: Path) -> list[dict]:
    """Hash source files in deterministic repository-relative path order."""
    project_root = Path(project_root).resolve(strict=True)
    source_root = Path(source_root).resolve(strict=True)
    if not _is_within(source_root, project_root):
        raise ValueError(f"Source root must be inside project root: {source_root}")

    paths = _git_catalog_paths(source_root)
    if paths is None:
        paths = _filesystem_catalog_paths(source_root)

    rows = []
    seen = set()
    for path in paths:
        portable_path = _portable_relative_path(path, project_root)
        if portable_path in seen:
            raise ValueError(f"Duplicate source path: {portable_path}")
        seen.add(portable_path)

        is_symlink = path.is_symlink()
        if is_symlink:
            payload = os.fsencode(os.readlink(path))
        else:
            payload = path.read_bytes()
        rows.append(
            {
                "path": portable_path,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
                "file_type": classify_source_file(path, is_symlink=is_symlink),
            }
        )
    if not rows:
        raise ValueError(f"Source tree contains no files: {source_root}")
    return rows


def source_tree_sha256(source_files: Iterable[dict]) -> str:
    """Hash the canonical source catalog rather than filesystem metadata."""
    digest = hashlib.sha256()
    for row in sorted(source_files, key=lambda value: value["path"]):
        record = {
            "file_type": row["file_type"],
            "path": row["path"],
            "sha256": row["sha256"],
            "size_bytes": row["size_bytes"],
        }
        digest.update(
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        digest.update(b"\n")
    return digest.hexdigest()


def extraction_run_id(
    *,
    extractor_revision: str,
    extractor_tree_hash: str,
    extractor_worktree_dirty: bool,
    source_revision: str,
    source_date_epoch: int,
    source_root: str,
    source_tree_hash: str,
) -> str:
    payload = {
        "extractor_revision": extractor_revision,
        "extractor_tree_sha256": extractor_tree_hash,
        "extractor_worktree_dirty": bool(extractor_worktree_dirty),
        "releases": [row[1] for row in RELEASES],
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "source_date_epoch": source_date_epoch,
        "source_revision": source_revision,
        "source_root": source_root,
        "source_tree_sha256": source_tree_hash,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def create_tables(conn: sqlite3.Connection) -> None:
    """Recreate metadata tables in dependency order."""
    for table in (
        "entity_provenance",
        "extracted_entities",
        "table_provenance",
        "extracted_tables",
        "source_files",
        "extraction_run_releases",
        "extraction_runs",
        "game_releases",
        "schema_metadata",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {_quote_identifier(table)}")

    conn.executescript(
        """
        CREATE TABLE schema_metadata (
            schema_name TEXT NOT NULL,
            schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
            minimum_reader_version INTEGER NOT NULL
                CHECK (minimum_reader_version >= 1
                       AND minimum_reader_version <= schema_version),
            applied_epoch INTEGER NOT NULL CHECK (applied_epoch >= 0),
            PRIMARY KEY (schema_name, schema_version),
            CHECK (length(trim(schema_name)) > 0)
        );

        CREATE TABLE game_releases (
            source_order INTEGER PRIMARY KEY CHECK (source_order >= 1),
            release_code TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL UNIQUE,
            variant TEXT NOT NULL UNIQUE,
            platform TEXT NOT NULL CHECK (platform = 'game-boy'),
            region TEXT NOT NULL,
            language TEXT NOT NULL,
            build_define TEXT NOT NULL UNIQUE,
            CHECK (release_code = lower(release_code)),
            CHECK (length(trim(title)) > 0),
            CHECK (length(trim(region)) > 0),
            CHECK (length(trim(language)) > 0)
        );

        CREATE TABLE extraction_runs (
            run_id TEXT PRIMARY KEY
                CHECK (length(run_id) = 64
                       AND run_id = lower(run_id)
                       AND run_id NOT GLOB '*[^0-9a-f]*'),
            schema_name TEXT NOT NULL,
            schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
            extractor_revision TEXT NOT NULL CHECK (length(trim(extractor_revision)) > 0),
            extractor_tree_sha256 TEXT NOT NULL
                CHECK (length(extractor_tree_sha256) = 64
                       AND extractor_tree_sha256 = lower(extractor_tree_sha256)
                       AND extractor_tree_sha256 NOT GLOB '*[^0-9a-f]*'),
            extractor_worktree_dirty INTEGER NOT NULL
                CHECK (extractor_worktree_dirty IN (0, 1)),
            source_revision TEXT NOT NULL CHECK (length(trim(source_revision)) > 0),
            source_date_epoch INTEGER NOT NULL CHECK (source_date_epoch >= 0),
            source_root TEXT NOT NULL
                CHECK (source_root NOT LIKE '/%'
                       AND source_root NOT GLOB '[A-Za-z]:*'
                       AND instr(source_root, '\\') = 0
                       AND instr('/' || source_root || '/', '/../') = 0),
            source_tree_sha256 TEXT NOT NULL
                CHECK (length(source_tree_sha256) = 64
                       AND source_tree_sha256 = lower(source_tree_sha256)
                       AND source_tree_sha256 NOT GLOB '*[^0-9a-f]*'),
            source_file_count INTEGER NOT NULL CHECK (source_file_count > 0),
            source_total_bytes INTEGER NOT NULL CHECK (source_total_bytes >= 0),
            UNIQUE (
                extractor_revision, extractor_tree_sha256,
                source_revision, source_date_epoch, source_tree_sha256
            ),
            FOREIGN KEY (schema_name, schema_version)
                REFERENCES schema_metadata (schema_name, schema_version)
        );

        CREATE TABLE extraction_run_releases (
            run_id TEXT NOT NULL,
            release_code TEXT NOT NULL,
            PRIMARY KEY (run_id, release_code),
            FOREIGN KEY (run_id) REFERENCES extraction_runs (run_id) ON DELETE CASCADE,
            FOREIGN KEY (release_code) REFERENCES game_releases (release_code)
        );

        CREATE TABLE source_files (
            run_id TEXT NOT NULL,
            path TEXT NOT NULL
                CHECK (path NOT LIKE '/%'
                       AND path NOT GLOB '[A-Za-z]:*'
                       AND instr(path, '\\') = 0
                       AND instr('/' || path || '/', '/../') = 0),
            sha256 TEXT NOT NULL
                CHECK (length(sha256) = 64
                       AND sha256 = lower(sha256)
                       AND sha256 NOT GLOB '*[^0-9a-f]*'),
            size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
            file_type TEXT NOT NULL
                CHECK (file_type IN ('assembly', 'audio', 'binary', 'build', 'data',
                                     'document', 'image', 'source-code', 'symlink', 'other')),
            PRIMARY KEY (run_id, path),
            FOREIGN KEY (run_id) REFERENCES extraction_runs (run_id) ON DELETE CASCADE
        ) WITHOUT ROWID;

        CREATE TABLE extracted_entities (
            run_id TEXT NOT NULL,
            entity_table TEXT NOT NULL CHECK (length(trim(entity_table)) > 0),
            entity_key TEXT NOT NULL CHECK (length(entity_key) > 1),
            PRIMARY KEY (run_id, entity_table, entity_key),
            FOREIGN KEY (run_id) REFERENCES extraction_runs (run_id) ON DELETE CASCADE
        ) WITHOUT ROWID;

        CREATE TABLE extracted_tables (
            run_id TEXT NOT NULL,
            entity_table TEXT NOT NULL CHECK (length(trim(entity_table)) > 0),
            primary_key_json TEXT NOT NULL CHECK (json_valid(primary_key_json)),
            row_count INTEGER NOT NULL CHECK (row_count >= 0),
            PRIMARY KEY (run_id, entity_table),
            FOREIGN KEY (run_id) REFERENCES extraction_runs (run_id) ON DELETE CASCADE
        ) WITHOUT ROWID;

        CREATE TABLE table_provenance (
            run_id TEXT NOT NULL,
            entity_table TEXT NOT NULL,
            source_path TEXT NOT NULL,
            relationship TEXT NOT NULL CHECK (relationship = 'source-set'),
            PRIMARY KEY (run_id, entity_table, source_path, relationship),
            FOREIGN KEY (run_id, entity_table)
                REFERENCES extracted_tables (run_id, entity_table) ON DELETE CASCADE,
            FOREIGN KEY (run_id, source_path)
                REFERENCES source_files (run_id, path) ON DELETE CASCADE
        ) WITHOUT ROWID;

        CREATE TABLE entity_provenance (
            run_id TEXT NOT NULL,
            entity_table TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            source_path TEXT NOT NULL,
            source_column TEXT NOT NULL CHECK (length(trim(source_column)) > 0),
            relationship TEXT NOT NULL
                CHECK (relationship IN ('direct-column', 'json-column')),
            PRIMARY KEY (
                run_id, entity_table, entity_key, source_path, source_column, relationship
            ),
            FOREIGN KEY (run_id, entity_table, entity_key)
                REFERENCES extracted_entities (run_id, entity_table, entity_key)
                ON DELETE CASCADE,
            FOREIGN KEY (run_id, source_path)
                REFERENCES source_files (run_id, path)
                ON DELETE CASCADE
        ) WITHOUT ROWID;

        CREATE INDEX entity_provenance_source_idx
            ON entity_provenance (run_id, source_path);
        CREATE INDEX entity_provenance_entity_idx
            ON entity_provenance (entity_table, entity_key);
        CREATE INDEX table_provenance_source_idx
            ON table_provenance (run_id, source_path);
        """
    )


def _table_primary_key(conn: sqlite3.Connection, table: str) -> list[str]:
    columns = conn.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
    return [
        row[1]
        for row in sorted((row for row in columns if row[5]), key=lambda row: row[5])
    ]


def _json_source_paths(value) -> Iterable[str]:
    if isinstance(value, dict):
        for key in sorted(value):
            child = value[key]
            normalized_key = "".join(character for character in key.lower() if character.isalnum())
            if normalized_key in JSON_PATH_KEYS and isinstance(child, str):
                yield child
            yield from _json_source_paths(child)
    elif isinstance(value, list):
        for child in value:
            yield from _json_source_paths(child)


def _resolve_source_reference(
    raw_path: str,
    *,
    source_root: str,
    available_paths: set[str],
) -> str | None:
    raw_path = raw_path.strip()
    if not raw_path or raw_path.endswith("/") or not is_portable_relative_path(raw_path):
        return None
    candidates = [raw_path]
    if raw_path != source_root and not raw_path.startswith(source_root + "/"):
        candidates.append(f"{source_root}/{raw_path}")
    return next((candidate for candidate in candidates if candidate in available_paths), None)


def discover_entity_provenance(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    source_root: str,
    available_paths: set[str],
) -> tuple[list[tuple], list[tuple]]:
    """Find exact source paths already exposed by extracted entity rows."""
    tables = [
        row[0]
        for row in conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )
        if row[0] not in OWN_TABLES
    ]
    entities = set()
    provenance = set()

    for table in tables:
        column_rows = conn.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
        column_names = [row[1] for row in column_rows]
        primary_key = _table_primary_key(conn, table)
        if not primary_key:
            continue
        direct_columns = sorted(DIRECT_SOURCE_COLUMNS.intersection(column_names))
        json_columns = sorted(column for column in column_names if column.endswith("_json"))
        source_columns = direct_columns + json_columns
        if not source_columns:
            continue

        selected_columns = primary_key + source_columns
        query = "SELECT " + ", ".join(map(_quote_identifier, selected_columns))
        query += " FROM " + _quote_identifier(table)
        query += " ORDER BY " + ", ".join(map(_quote_identifier, primary_key))

        for values in conn.execute(query):
            key_values = values[: len(primary_key)]
            if any(value is None for value in key_values):
                continue
            entity_key = json.dumps(
                dict(zip(primary_key, key_values)),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            row_paths = []
            for column, raw_value in zip(source_columns, values[len(primary_key) :]):
                if not isinstance(raw_value, str) or not raw_value.strip():
                    continue
                relationship = "direct-column" if column in direct_columns else "json-column"
                if relationship == "direct-column":
                    raw_paths = (raw_value,)
                else:
                    try:
                        decoded = json.loads(raw_value)
                    except json.JSONDecodeError:
                        continue
                    raw_paths = _json_source_paths(decoded)
                for raw_path in raw_paths:
                    resolved = _resolve_source_reference(
                        raw_path,
                        source_root=source_root,
                        available_paths=available_paths,
                    )
                    if resolved is not None:
                        row_paths.append((resolved, column, relationship))

            if not row_paths:
                continue
            entities.add((run_id, table, entity_key))
            for path, column, relationship in row_paths:
                provenance.add((run_id, table, entity_key, path, column, relationship))

    return sorted(entities), sorted(provenance)


TABLE_SOURCE_AREAS = (
    (("graphic",), ("gfx/",)),
    (("audio", "map_music", "pokemon_cry"), ("audio/", "constants/music_constants.asm")),
    (("move",), ("data/moves/", "constants/move_constants.asm")),
    (("pokemon", "learnset", "tmhm", "evolution"),
     ("data/pokemon/", "constants/pokemon_constants.asm", "constants/pokedex_constants.asm")),
    (("item",), ("data/items/", "constants/item_constants.asm")),
    (("trainer",), ("data/trainers/", "constants/trainer_constants.asm")),
    (("wild", "encounter"), ("data/wild/",)),
    (("dialogue", "text_pointer"), ("text/", "data/text/")),
    (("map", "warp", "zone", "tile", "block", "object", "spin", "coordinate",
      "missable", "hidden", "script", "event"),
     ("data/maps/", "maps/", "scripts/", "text/", "gfx/blocksets/", "gfx/tilesets/")),
)


def _table_source_paths(
    table: str, *, source_root: str, available_paths: set[str]
) -> list[str]:
    """Return a conservative, deterministic upstream source set for a table."""
    normalized = table.lower()
    relative_prefixes = set()
    for name_fragments, prefixes in TABLE_SOURCE_AREAS:
        if any(fragment in normalized for fragment in name_fragments):
            relative_prefixes.update(prefixes)
    if not relative_prefixes:
        relative_prefixes.add("")

    qualified = tuple(
        f"{source_root}/{prefix}" if prefix else f"{source_root}/"
        for prefix in sorted(relative_prefixes)
    )
    paths = sorted(
        path
        for path in available_paths
        if any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in qualified)
    )
    # Constants or uncommon derived tables may not match a known directory;
    # catalog the complete upstream tree rather than inventing row-level origin.
    return paths or sorted(
        path for path in available_paths if path.startswith(f"{source_root}/")
    )


def discover_table_provenance(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    source_root: str,
    available_paths: set[str],
) -> tuple[list[tuple], list[tuple]]:
    """Catalog every generated table and the upstream source set it derives from."""
    tables = [
        row[0]
        for row in conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )
        if row[0] not in OWN_TABLES
    ]
    table_rows = []
    provenance_rows = []
    for table in tables:
        primary_key = _table_primary_key(conn, table)
        row_count = conn.execute(
            f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
        ).fetchone()[0]
        table_rows.append(
            (
                run_id,
                table,
                json.dumps(primary_key, separators=(",", ":")),
                row_count,
            )
        )
        provenance_rows.extend(
            (run_id, table, path, "source-set")
            for path in _table_source_paths(
                table, source_root=source_root, available_paths=available_paths
            )
        )
    return table_rows, provenance_rows


def export_release_metadata(
    conn: sqlite3.Connection,
    *,
    project_root: Path = PROJECT_ROOT,
    source_root: Path = GAME_DATA_ROOT,
    extractor_revision: str | None = None,
    source_revision: str | None = None,
    source_date_epoch=None,
) -> str:
    """Populate all release metadata tables and return the deterministic run ID."""
    project_root = Path(project_root).resolve(strict=True)
    source_root = Path(source_root).resolve(strict=True)
    source_root_relative = _portable_relative_path(source_root, project_root)
    extractor_revision = extractor_revision or git_revision(project_root)
    extractor_tree_hash, worktree_dirty = extractor_worktree_state(
        project_root, source_root
    )
    source_revision = source_revision or git_revision(source_root)
    epoch = resolve_source_date_epoch(source_root, source_date_epoch)
    files = collect_source_files(project_root, source_root)
    tree_hash = source_tree_sha256(files)
    run_id = extraction_run_id(
        extractor_revision=extractor_revision,
        extractor_tree_hash=extractor_tree_hash,
        extractor_worktree_dirty=worktree_dirty,
        source_revision=source_revision,
        source_date_epoch=epoch,
        source_root=source_root_relative,
        source_tree_hash=tree_hash,
    )

    create_tables(conn)
    conn.execute(
        "INSERT INTO schema_metadata VALUES (?, ?, ?, ?)",
        (SCHEMA_NAME, SCHEMA_VERSION, MINIMUM_READER_VERSION, epoch),
    )
    conn.executemany(
        """
        INSERT INTO game_releases (
            source_order, release_code, title, variant, platform,
            region, language, build_define
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        RELEASES,
    )
    conn.execute(
        """
        INSERT INTO extraction_runs (
            run_id, schema_name, schema_version, extractor_revision,
            extractor_tree_sha256, extractor_worktree_dirty, source_revision,
            source_date_epoch, source_root, source_tree_sha256,
            source_file_count, source_total_bytes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            SCHEMA_NAME,
            SCHEMA_VERSION,
            extractor_revision,
            extractor_tree_hash,
            int(worktree_dirty),
            source_revision,
            epoch,
            source_root_relative,
            tree_hash,
            len(files),
            sum(row["size_bytes"] for row in files),
        ),
    )
    conn.executemany(
        "INSERT INTO extraction_run_releases VALUES (?, ?)",
        ((run_id, release[1]) for release in RELEASES),
    )
    conn.executemany(
        "INSERT INTO source_files VALUES (?, ?, ?, ?, ?)",
        (
            (run_id, row["path"], row["sha256"], row["size_bytes"], row["file_type"])
            for row in files
        ),
    )

    available_paths = {row["path"] for row in files}
    extracted_tables, table_provenance = discover_table_provenance(
        conn,
        run_id=run_id,
        source_root=source_root_relative,
        available_paths=available_paths,
    )
    conn.executemany("INSERT INTO extracted_tables VALUES (?, ?, ?, ?)", extracted_tables)
    conn.executemany(
        "INSERT INTO table_provenance VALUES (?, ?, ?, ?)", table_provenance
    )
    entities, provenance = discover_entity_provenance(
        conn,
        run_id=run_id,
        source_root=source_root_relative,
        available_paths=available_paths,
    )
    conn.executemany("INSERT INTO extracted_entities VALUES (?, ?, ?)", entities)
    conn.executemany(
        "INSERT INTO entity_provenance VALUES (?, ?, ?, ?, ?, ?)", provenance
    )
    validate_release_metadata(conn)
    return run_id


def _require_table(conn: sqlite3.Connection, table: str) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone():
        raise ValueError(f"Missing release metadata table: {table}")


def validate_release_metadata(conn: sqlite3.Connection) -> None:
    """Validate referential, canonical, and deterministic metadata invariants."""
    for table in OWN_TABLES:
        _require_table(conn, table)

    foreign_key_errors = []
    for table in sorted(OWN_TABLES):
        foreign_key_errors.extend(
            conn.execute(f"PRAGMA foreign_key_check({_quote_identifier(table)})")
        )
    if foreign_key_errors:
        raise ValueError(f"Release metadata foreign-key violations: {foreign_key_errors[:10]}")

    schema_rows = conn.execute(
        "SELECT schema_name, schema_version, minimum_reader_version, applied_epoch "
        "FROM schema_metadata"
    ).fetchall()
    if len(schema_rows) != 1 or schema_rows[0][:3] != (
        SCHEMA_NAME,
        SCHEMA_VERSION,
        MINIMUM_READER_VERSION,
    ):
        raise ValueError(f"Unexpected schema metadata: {schema_rows}")

    release_rows = conn.execute(
        "SELECT source_order, release_code, title, variant, platform, region, language, "
        "build_define FROM game_releases ORDER BY source_order"
    ).fetchall()
    if release_rows != list(RELEASES):
        raise ValueError(f"Unexpected release rows: {release_rows}")

    runs = conn.execute(
        """
        SELECT run_id, extractor_revision, extractor_tree_sha256,
               extractor_worktree_dirty, source_revision, source_date_epoch,
               source_root, source_tree_sha256, source_file_count, source_total_bytes
        FROM extraction_runs
        """
    ).fetchall()
    if len(runs) != 1:
        raise ValueError(f"Expected one extraction run, found {len(runs)}")
    (
        run_id,
        extractor_revision,
        extractor_tree_hash,
        extractor_worktree_dirty,
        source_revision,
        epoch,
        source_root,
        recorded_tree_hash,
        recorded_file_count,
        recorded_total_bytes,
    ) = runs[0]
    if (
        len(extractor_tree_hash) != 64
        or any(character not in "0123456789abcdef" for character in extractor_tree_hash)
        or extractor_worktree_dirty not in (0, 1)
    ):
        raise ValueError("Extraction run has invalid generator worktree metadata")
    if not is_portable_relative_path(source_root):
        raise ValueError(f"Non-portable source root: {source_root}")
    if schema_rows[0][3] != epoch:
        raise ValueError("Schema application epoch does not match the extraction run")

    release_links = conn.execute(
        "SELECT release_code FROM extraction_run_releases WHERE run_id = ? ORDER BY release_code",
        (run_id,),
    ).fetchall()
    if release_links != [("blue",), ("red",)]:
        raise ValueError(f"Extraction run is not linked to both releases: {release_links}")

    file_rows = [
        {
            "path": path,
            "sha256": sha256,
            "size_bytes": size_bytes,
            "file_type": file_type,
        }
        for path, sha256, size_bytes, file_type in conn.execute(
            """
            SELECT path, sha256, size_bytes, file_type
            FROM source_files WHERE run_id = ? ORDER BY path
            """,
            (run_id,),
        )
    ]
    if len(file_rows) != recorded_file_count:
        raise ValueError("Source file count does not match extraction run metadata")
    if sum(row["size_bytes"] for row in file_rows) != recorded_total_bytes:
        raise ValueError("Source byte count does not match extraction run metadata")
    if any(not is_portable_relative_path(row["path"]) for row in file_rows):
        raise ValueError("Source catalog contains a non-portable path")
    if any(row["file_type"] not in FILE_TYPES for row in file_rows):
        raise ValueError("Source catalog contains an unsupported file type")
    calculated_tree_hash = source_tree_sha256(file_rows)
    if calculated_tree_hash != recorded_tree_hash:
        raise ValueError("Source tree hash does not match its file catalog")
    calculated_run_id = extraction_run_id(
        extractor_revision=extractor_revision,
        extractor_tree_hash=extractor_tree_hash,
        extractor_worktree_dirty=bool(extractor_worktree_dirty),
        source_revision=source_revision,
        source_date_epoch=epoch,
        source_root=source_root,
        source_tree_hash=recorded_tree_hash,
    )
    if calculated_run_id != run_id:
        raise ValueError("Extraction run ID is not canonical")

    available_paths = {row["path"] for row in file_rows}
    expected_tables, expected_table_provenance = discover_table_provenance(
        conn,
        run_id=run_id,
        source_root=source_root,
        available_paths=available_paths,
    )
    actual_tables = conn.execute(
        """
        SELECT run_id, entity_table, primary_key_json, row_count
        FROM extracted_tables ORDER BY run_id, entity_table
        """
    ).fetchall()
    actual_table_provenance = conn.execute(
        """
        SELECT run_id, entity_table, source_path, relationship
        FROM table_provenance
        ORDER BY run_id, entity_table, source_path, relationship
        """
    ).fetchall()
    if actual_tables != expected_tables:
        raise ValueError("Generated table catalog is incomplete or non-canonical")
    if actual_table_provenance != expected_table_provenance:
        raise ValueError("Table source-set provenance is incomplete or non-canonical")

    expected_entities, expected_provenance = discover_entity_provenance(
        conn,
        run_id=run_id,
        source_root=source_root,
        available_paths=available_paths,
    )
    actual_entities = conn.execute(
        """
        SELECT run_id, entity_table, entity_key
        FROM extracted_entities ORDER BY run_id, entity_table, entity_key
        """
    ).fetchall()
    actual_provenance = conn.execute(
        """
        SELECT run_id, entity_table, entity_key, source_path, source_column, relationship
        FROM entity_provenance
        ORDER BY run_id, entity_table, entity_key, source_path, source_column, relationship
        """
    ).fetchall()
    if actual_entities != expected_entities:
        raise ValueError("Extracted entity catalog is incomplete or non-canonical")
    if actual_provenance != expected_provenance:
        raise ValueError("Entity provenance is incomplete or non-canonical")


def main(
    db_path: Path = DB_PATH,
    *,
    project_root: Path = PROJECT_ROOT,
    source_root: Path = GAME_DATA_ROOT,
    extractor_revision: str | None = None,
    source_revision: str | None = None,
    source_date_epoch=None,
) -> str:
    """Export metadata into the configured SQLite database."""
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("PRAGMA foreign_keys = ON")
        run_id = export_release_metadata(
            conn,
            project_root=project_root,
            source_root=source_root,
            extractor_revision=extractor_revision,
            source_revision=source_revision,
            source_date_epoch=source_date_epoch,
        )
    print(f"Exported deterministic release metadata for run {run_id}")
    return run_id


if __name__ == "__main__":
    main()
