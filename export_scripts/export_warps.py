#!/usr/bin/env python3
"""Export source-faithful, FK-backed map warp relationships."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import sqlite3

from config import DB_PATH, MAP_HEADERS_DIR, MAP_OBJECTS_DIR, PROJECT_ROOT
from map_references import CanonicalMapResolver


WARP_SECTION_RE = re.compile(
    r"def_warp_events(.*?)(?:def_bg_events|def_object_events|\Z)", re.DOTALL
)
WARP_EVENT_RE = re.compile(
    r"warp_event\s+(\d+),\s+(\d+),\s+([A-Za-z_][A-Za-z0-9_]*),\s+(\d+)"
)


class WarpExportError(ValueError):
    """Warp source data cannot be represented without ambiguity."""


@dataclass(frozen=True)
class SourceWarp:
    source_map: str
    source_map_id: int
    source_warp_index: int
    source_x: int
    source_y: int
    destination_map: str
    destination_kind: str
    destination_map_id: int | None
    destination_warp_id: int
    source_file: str


def portable_source_path(path: Path, project_root: Path = PROJECT_ROOT) -> str:
    try:
        relative = path.resolve().relative_to(Path(project_root).resolve()).as_posix()
    except ValueError as error:
        raise WarpExportError(f"Warp source is outside the project root: {path}") from error
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise WarpExportError(f"Non-portable warp source path: {relative}")
    return relative


def create_table(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS warp_sources")
    conn.execute("DROP TABLE IF EXISTS warps")
    conn.executescript(
        """
        CREATE TABLE warps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_map TEXT NOT NULL,
            source_map_id INTEGER NOT NULL,
            source_warp_index INTEGER NOT NULL CHECK(source_warp_index >= 1),
            source_x INTEGER NOT NULL CHECK(source_x >= 0),
            source_y INTEGER NOT NULL CHECK(source_y >= 0),
            x INTEGER NOT NULL,
            y INTEGER NOT NULL,
            destination_map TEXT NOT NULL,
            destination_kind TEXT NOT NULL
                CHECK(destination_kind IN ('fixed', 'last-map')),
            destination_map_id INTEGER,
            destination_x INTEGER,
            destination_y INTEGER,
            destination_warp_id INTEGER NOT NULL CHECK(destination_warp_id >= 0),
            source_file TEXT NOT NULL,
            UNIQUE(source_map, source_warp_index),
            FOREIGN KEY(source_map_id) REFERENCES maps(id),
            FOREIGN KEY(destination_map_id) REFERENCES maps(id),
            CHECK(
                (destination_kind = 'fixed' AND destination_map <> 'LAST_MAP'
                    AND destination_map_id IS NOT NULL)
                OR
                (destination_kind = 'last-map' AND destination_map = 'LAST_MAP'
                    AND destination_map_id IS NULL
                    AND destination_x IS NULL AND destination_y IS NULL)
            ),
            CHECK((destination_x IS NULL) = (destination_y IS NULL))
        );
        CREATE INDEX idx_warps_destination_map ON warps(destination_map_id);
        CREATE TABLE warp_sources (
            warp_id INTEGER NOT NULL,
            source_file TEXT NOT NULL,
            source_map_label TEXT NOT NULL,
            relationship TEXT NOT NULL
                CHECK(relationship IN ('canonical', 'alias-copy')),
            PRIMARY KEY(warp_id, source_file),
            FOREIGN KEY(warp_id) REFERENCES warps(id) ON DELETE CASCADE
        ) WITHOUT ROWID;
        CREATE INDEX idx_warp_sources_file ON warp_sources(source_file);
        """
    )


def parse_source_warps(
    source_path: Path,
    resolver: CanonicalMapResolver,
    *,
    project_root: Path = PROJECT_ROOT,
) -> list[SourceWarp]:
    source_path = Path(source_path)
    content = source_path.read_text(encoding="utf-8")
    section = WARP_SECTION_RE.search(content)
    if section is None:
        return []

    source_map = source_path.stem
    source_map_id = resolver.resolve(source_map)
    source_file = portable_source_path(source_path, project_root)
    rows = []
    for source_warp_index, match in enumerate(
        WARP_EVENT_RE.finditer(section.group(1)), start=1
    ):
        source_x, source_y = int(match.group(1)), int(match.group(2))
        destination_map = match.group(3)
        destination_warp_id = int(match.group(4))
        is_last_map = destination_map == "LAST_MAP"
        rows.append(
            SourceWarp(
                source_map=source_map,
                source_map_id=source_map_id,
                source_warp_index=source_warp_index,
                source_x=source_x,
                source_y=source_y,
                destination_map=destination_map,
                destination_kind="last-map" if is_last_map else "fixed",
                destination_map_id=(
                    None if is_last_map else resolver.resolve(destination_map)
                ),
                destination_warp_id=destination_warp_id,
                source_file=source_file,
            )
        )
    return rows


def collect_source_warps(
    resolver: CanonicalMapResolver,
    *,
    objects_dir: Path = MAP_OBJECTS_DIR,
    project_root: Path = PROJECT_ROOT,
) -> list[SourceWarp]:
    rows = []
    for source_path in sorted(Path(objects_dir).glob("*.asm")):
        rows.extend(parse_source_warps(source_path, resolver, project_root=project_root))
    return rows


def destination_coordinates(
    warp: SourceWarp, coordinates_by_map: dict[int, dict[int, tuple[int, int]]]
) -> tuple[int | None, int | None]:
    """Resolve the destination warp number to its source coordinates."""
    if warp.destination_kind != "fixed" or warp.destination_warp_id == 0:
        return None, None
    destination_rows = coordinates_by_map.get(warp.destination_map_id, {})
    # Some retained/unused map constants have no object file in the source;
    # the map relationship remains exact even though no target coordinates
    # exist to dereference.
    if not destination_rows:
        return None, None
    coordinates = destination_rows.get(warp.destination_warp_id)
    if coordinates is None:
        raise WarpExportError(
            f"{warp.source_file} warp {warp.source_warp_index} points to missing "
            f"warp {warp.destination_warp_id} in {warp.destination_map}"
        )
    return coordinates


def insert_warps(conn: sqlite3.Connection, rows: list[SourceWarp]) -> None:
    coordinates_by_map: dict[int, dict[int, tuple[int, int]]] = {}
    for row in rows:
        map_coordinates = coordinates_by_map.setdefault(row.source_map_id, {})
        coordinates = (row.source_x, row.source_y)
        previous = map_coordinates.get(row.source_warp_index)
        if previous is not None and previous != coordinates:
            raise WarpExportError(
                f"Conflicting coordinates for map {row.source_map_id}, "
                f"warp {row.source_warp_index}: {previous} and {coordinates}"
            )
        map_coordinates[row.source_warp_index] = coordinates

    for row in rows:
        destination_x, destination_y = destination_coordinates(
            row, coordinates_by_map
        )
        cursor = conn.execute(
            """
            INSERT INTO warps (
                source_map, source_map_id, source_warp_index, source_x, source_y,
                x, y, destination_map, destination_kind, destination_map_id,
                destination_x, destination_y, destination_warp_id, source_file
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.source_map,
                row.source_map_id,
                row.source_warp_index,
                row.source_x,
                row.source_y,
                row.source_x,
                row.source_y,
                row.destination_map,
                row.destination_kind,
                row.destination_map_id,
                destination_x,
                destination_y,
                row.destination_warp_id,
                row.source_file,
            ),
        )
        warp_id = cursor.lastrowid
        conn.execute(
            """
            INSERT INTO warp_sources
                (warp_id, source_file, source_map_label, relationship)
            VALUES (?, ?, ?, 'canonical')
            """,
            (warp_id, row.source_file, row.source_map),
        )


def validate_warps(
    conn: sqlite3.Connection,
    *,
    objects_dir: Path = MAP_OBJECTS_DIR,
    map_headers_dir: Path = MAP_HEADERS_DIR,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, int]:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='warps'"
    ).fetchone():
        raise WarpExportError("Missing warps table")
    resolver = CanonicalMapResolver.from_connection(conn, map_headers_dir)
    expected = collect_source_warps(
        resolver, objects_dir=objects_dir, project_root=project_root
    )
    actual_count = conn.execute("SELECT COUNT(*) FROM warps").fetchone()[0]
    if actual_count != len(expected):
        raise WarpExportError(
            f"Warp coverage mismatch: expected {len(expected)}, found {actual_count}"
        )
    source_count = conn.execute("SELECT COUNT(*) FROM warp_sources").fetchone()[0]
    if source_count != len(expected):
        raise WarpExportError(
            f"Warp source coverage mismatch: expected {len(expected)}, found {source_count}"
        )
    invalid = conn.execute(
        """
        SELECT COUNT(*) FROM warps
        WHERE source_map_id IS NULL
           OR (destination_kind = 'fixed' AND destination_map_id IS NULL)
           OR (destination_kind = 'last-map' AND destination_map_id IS NOT NULL)
           OR source_file LIKE '/%' OR source_file GLOB '[A-Za-z]:*'
           OR instr(source_file, '\\') > 0
        """
    ).fetchone()[0]
    if invalid:
        raise WarpExportError(f"Warp table contains {invalid} invalid relationships")
    errors = conn.execute("PRAGMA foreign_key_check(warps)").fetchall()
    errors.extend(conn.execute("PRAGMA foreign_key_check(warp_sources)").fetchall())
    if errors:
        raise WarpExportError(f"Warp foreign-key violations: {errors[:10]}")
    fixed = conn.execute(
        "SELECT COUNT(*) FROM warps WHERE destination_kind='fixed'"
    ).fetchone()[0]
    dynamic = conn.execute(
        "SELECT COUNT(*) FROM warps WHERE destination_kind='last-map'"
    ).fetchone()[0]
    return {
        "warps": actual_count,
        "sourceRows": source_count,
        "fixed": fixed,
        "lastMap": dynamic,
    }


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        resolver = CanonicalMapResolver.from_connection(conn)
        rows = collect_source_warps(resolver)
        with conn:
            create_table(conn)
            insert_warps(conn, rows)
            result = validate_warps(conn)
    finally:
        conn.close()
    print(
        f"Exported {result['warps']} warps: {result['fixed']} fixed, "
        f"{result['lastMap']} runtime LAST_MAP relationships"
    )


if __name__ == "__main__":
    main()
