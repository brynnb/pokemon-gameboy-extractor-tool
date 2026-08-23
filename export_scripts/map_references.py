"""Resolve source map labels to canonical relational map IDs.

Map-facing source files use CamelCase labels such as ``PalletTown`` while the
``maps`` table stores assembly constants such as ``PALLET_TOWN``.  The map
header declarations are the authority connecting those two namespaces.  A
small number of source files are split into numbered companions (currently
``CeruleanCity_2.asm``); those resolve through their declared base map rather
than through a general CamelCase heuristic.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3

from config import MAP_HEADERS_DIR


MAP_HEADER_RE = re.compile(
    r"^\s*map_header\s+([A-Za-z_][A-Za-z0-9_]*)\s*,\s*"
    r"([A-Z_][A-Z0-9_]*)\s*,"
)
NUMBERED_SOURCE_RE = re.compile(r"^(?P<base>.+)_\d+$")
GLOBAL_MAP_NAMES = frozenset({"GLOBAL"})


class MapReferenceError(ValueError):
    """A source map name cannot be mapped to one canonical database row."""


def load_map_header_constants(map_headers_dir: Path = MAP_HEADERS_DIR) -> dict[str, str]:
    """Return authoritative source-label/file-stem -> map-constant aliases."""
    aliases: dict[str, str] = {}
    for header_path in sorted(Path(map_headers_dir).glob("*.asm")):
        declaration = None
        for line in header_path.read_text(encoding="utf-8").splitlines():
            match = MAP_HEADER_RE.match(line)
            if match:
                declaration = match.groups()
                break
        if declaration is None:
            raise MapReferenceError(f"No map_header declaration in {header_path}")

        source_label, map_constant = declaration
        for alias in (header_path.stem, source_label):
            previous = aliases.get(alias)
            if previous is not None and previous != map_constant:
                raise MapReferenceError(
                    f"Ambiguous map alias {alias!r}: {previous} and {map_constant}"
                )
            aliases[alias] = map_constant
    if not aliases:
        raise MapReferenceError(f"No map headers found in {map_headers_dir}")
    return aliases


@dataclass(frozen=True)
class CanonicalMapResolver:
    """Validated lookup from source map names/constants to ``maps.id``."""

    ids_by_alias: dict[str, int]
    ids_by_casefolded_alias: dict[str, int]

    @classmethod
    def from_connection(
        cls,
        conn: sqlite3.Connection,
        map_headers_dir: Path = MAP_HEADERS_DIR,
    ) -> "CanonicalMapResolver":
        map_rows = conn.execute("SELECT id, name FROM maps ORDER BY id").fetchall()
        if not map_rows:
            raise MapReferenceError("The maps table is missing or empty")

        ids_by_constant = {name: map_id for map_id, name in map_rows}
        ids_by_alias = dict(ids_by_constant)
        for alias, map_constant in load_map_header_constants(map_headers_dir).items():
            if map_constant not in ids_by_constant:
                raise MapReferenceError(
                    f"Map header alias {alias!r} refers to missing maps.name "
                    f"{map_constant!r}"
                )
            ids_by_alias[alias] = ids_by_constant[map_constant]

        ids_by_casefolded_alias: dict[str, int] = {}
        for alias, map_id in ids_by_alias.items():
            folded = alias.casefold()
            previous = ids_by_casefolded_alias.get(folded)
            if previous is not None and previous != map_id:
                raise MapReferenceError(f"Ambiguous case-insensitive map alias: {alias!r}")
            ids_by_casefolded_alias[folded] = map_id
        return cls(ids_by_alias, ids_by_casefolded_alias)

    def resolve(self, map_name: str, *, allow_global: bool = False) -> int | None:
        """Resolve one map name, rejecting unknown names and accidental nulls."""
        if allow_global and map_name in GLOBAL_MAP_NAMES:
            return None
        if not isinstance(map_name, str) or not map_name.strip():
            raise MapReferenceError(f"Invalid map name: {map_name!r}")

        direct = self.ids_by_alias.get(map_name)
        if direct is not None:
            return direct
        folded = self.ids_by_casefolded_alias.get(map_name.casefold())
        if folded is not None:
            return folded

        numbered = NUMBERED_SOURCE_RE.fullmatch(map_name)
        if numbered:
            base = numbered.group("base")
            direct = self.ids_by_alias.get(base)
            if direct is not None:
                return direct
            folded = self.ids_by_casefolded_alias.get(base.casefold())
            if folded is not None:
                return folded

        raise MapReferenceError(f"Unknown source map name: {map_name!r}")
