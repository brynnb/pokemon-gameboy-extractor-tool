#!/usr/bin/env python3
"""Catalog and losslessly render reusable Game Boy graphics assets.

Every version-controlled or non-ignored file below ``pokemon-game-data/gfx`` is
represented as a source asset in the database. Ignored build intermediates are
excluded, so catalog contents do not depend on whether the source checkout was
previously compiled. Raw ``.1bpp`` and ``.2bpp`` source streams are additionally
decoded to deterministic PNG tile sheets. Existing PNGs remain source assets;
they are never copied into the generated output directory.

The raw Game Boy formats do not contain canvas dimensions. When a same-stem PNG
source exists, its dimensions and the closest matching row/column tile order
are used. Otherwise, a deterministic fixed-width tile sheet is produced.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image, UnidentifiedImageError

from config import DB_PATH, GFX_DIR, PROJECT_ROOT
from tile_helpers import decode_2bpp_tile


DECODER_VERSION = "gameboy-planar-v1"
DEFAULT_FALLBACK_TILES_PER_ROW = 16
CATALOG_MANIFEST_NAME = "graphics-catalog.json"

# RGBDS uses palette index 0 for white and the highest index for black when
# converting the monochrome source PNGs in this repository.
GAME_BOY_1BPP_PALETTE = (
    (255, 255, 255, 255),
    (0, 0, 0, 255),
)
GAME_BOY_2BPP_PALETTE = (
    (255, 255, 255, 255),
    (170, 170, 170, 255),
    (85, 85, 85, 255),
    (0, 0, 0, 255),
)

FORMAT_METADATA = {
    ".png": ("image/png", "raster_image", None, 1),
    ".1bpp": ("application/octet-stream", "tile_graphics", 1, 1),
    ".2bpp": ("application/octet-stream", "tile_graphics", 2, 1),
    ".pic": ("application/octet-stream", "compressed_graphics", None, 0),
    ".tilemap": ("application/octet-stream", "tilemap", None, 0),
    ".bst": ("application/octet-stream", "blockset", None, 0),
    ".rle": ("application/octet-stream", "compressed_graphics", None, 0),
    ".asm": ("text/plain", "assembly", None, 0),
    ".o": ("application/octet-stream", "object", None, 0),
}


class GraphicsExportError(RuntimeError):
    """Base error for graphics catalog or rendering failures."""


class MalformedGraphicError(GraphicsExportError):
    """Raised when a supported source format cannot be decoded losslessly."""


@dataclass(frozen=True)
class SourceAsset:
    path: Path
    source_relative_path: str
    repository_relative_path: str
    extension: str
    category_path: str
    sha256: str
    byte_size: int
    width_px: int | None = None
    height_px: int | None = None
    pixel_mode: str | None = None
    tile_count: int | None = None
    palette: tuple[tuple[int, int, int, int], ...] | None = None
    metadata_basis: str = "unavailable"


@dataclass(frozen=True)
class DerivedAsset:
    source_relative_path: str
    output_relative_path: str
    png_bytes: bytes
    sha256: str
    byte_size: int
    width_px: int
    height_px: int
    tile_count: int
    tiles_per_row: int
    layout: str
    category_path: str
    palette: tuple[tuple[int, int, int, int], ...]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _portable_relative_path(path: Path, root: Path, label: str) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise GraphicsExportError(f"{label} must be contained by {root}: {path}") from exc
    return relative.as_posix()


def _default_output_root(project_root: Path) -> Path:
    configured = os.environ.get("POKEMON_EXTRACTOR_GRAPHICS_DIR")
    path = Path(configured) if configured else project_root / "build" / "graphics"
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _validate_roots(source_root: Path, output_root: Path, project_root: Path) -> None:
    if not source_root.is_dir():
        raise GraphicsExportError(f"Graphics source directory does not exist: {source_root}")
    _portable_relative_path(source_root, project_root, "Graphics source directory")
    try:
        output_root.relative_to(source_root)
    except ValueError:
        pass
    else:
        raise GraphicsExportError(
            f"Graphics output directory cannot be inside the source tree: {output_root}"
        )


def _palette_from_png(image: Image.Image) -> tuple[tuple[int, int, int, int], ...] | None:
    """Return the effective palette for palette-like PNGs, in stable order."""
    rgba = image.convert("RGBA")
    colors = rgba.getcolors(maxcolors=256)
    if colors is None:
        return None
    return tuple(
        sorted(
            (tuple(color) for _, color in colors),
            key=lambda color: (sum(color[:3]), color),
            reverse=True,
        )
    )


def _format_extension(path: Path) -> str:
    return path.suffix.lower()


def _format_metadata(extension: str) -> tuple[str, str, int | None, int]:
    return FORMAT_METADATA.get(
        extension,
        ("application/octet-stream", "other", None, 0),
    )


def _category_path(source_relative_path: str) -> str:
    parent = Path(source_relative_path).parent.as_posix()
    return "." if parent == "." else parent


def _read_png_metadata(path: Path) -> tuple[int, int, str, tuple]:
    try:
        with Image.open(path) as image:
            image.load()
            if image.format != "PNG":
                raise MalformedGraphicError(f"Expected a PNG image: {path}")
            return image.width, image.height, image.mode, _palette_from_png(image)
    except (OSError, UnidentifiedImageError) as exc:
        raise MalformedGraphicError(f"Cannot decode PNG source {path}: {exc}") from exc


def _source_file_paths(source_root: Path) -> list[Path]:
    """Return authored inputs without Git-ignored compilation byproducts."""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(source_root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                ".",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        result = None

    if result is not None and result.returncode == 0:
        paths = [source_root / relative for relative in result.stdout.splitlines()]
        files = [path for path in paths if path.is_file()]
        if files:
            return sorted(
                files,
                key=lambda path: path.relative_to(source_root).as_posix(),
            )

    # Source archives need not retain .git metadata. In that case their
    # contents are already the only available authority.
    return sorted(
        (candidate for candidate in source_root.rglob("*") if candidate.is_file()),
        key=lambda path: path.relative_to(source_root).as_posix(),
    )


def collect_source_assets(
    source_root: Path = GFX_DIR,
    project_root: Path = PROJECT_ROOT,
) -> list[SourceAsset]:
    """Read and validate metadata for every file in a graphics source tree."""
    source_root = Path(source_root).resolve()
    project_root = Path(project_root).resolve()
    if not source_root.is_dir():
        raise GraphicsExportError(f"Graphics source directory does not exist: {source_root}")

    assets: list[SourceAsset] = []
    for path in _source_file_paths(source_root):
        source_relative = _portable_relative_path(path, source_root, "Graphics source")
        repository_relative = _portable_relative_path(path, project_root, "Graphics source")
        extension = _format_extension(path)
        data = path.read_bytes()
        asset = SourceAsset(
            path=path,
            source_relative_path=source_relative,
            repository_relative_path=repository_relative,
            extension=extension,
            category_path=_category_path(source_relative),
            sha256=_sha256_bytes(data),
            byte_size=len(data),
        )

        if extension == ".png":
            width, height, pixel_mode, palette = _read_png_metadata(path)
            asset = replace(
                asset,
                width_px=width,
                height_px=height,
                pixel_mode=pixel_mode,
                palette=palette,
                metadata_basis="embedded",
            )
        elif extension in (".1bpp", ".2bpp"):
            depth = int(extension[1])
            bytes_per_tile = 8 * depth
            if not data:
                raise MalformedGraphicError(f"Raw {depth}bpp graphic is empty: {path}")
            if len(data) % bytes_per_tile:
                raise MalformedGraphicError(
                    f"Raw {depth}bpp graphic size must be a multiple of "
                    f"{bytes_per_tile} bytes, got {len(data)}: {path}"
                )
            asset = replace(
                asset,
                pixel_mode="P",
                tile_count=len(data) // bytes_per_tile,
                palette=(
                    GAME_BOY_1BPP_PALETTE if depth == 1 else GAME_BOY_2BPP_PALETTE
                ),
                metadata_basis="decoded-tile-grid",
            )
        assets.append(asset)

    if not assets:
        raise GraphicsExportError(f"Graphics source directory contains no files: {source_root}")

    # Same-stem source PNGs carry the authored canvas dimensions for formats
    # whose binary representation has no dimensions of its own.
    by_source_path = {asset.source_relative_path: asset for asset in assets}
    enriched: list[SourceAsset] = []
    for asset in assets:
        if asset.extension == ".png":
            enriched.append(asset)
            continue
        companion_key = str(Path(asset.source_relative_path).with_suffix(".png")).replace(
            os.sep, "/"
        )
        companion = by_source_path.get(companion_key)
        if companion is None:
            enriched.append(asset)
            continue
        if asset.extension in (".1bpp", ".2bpp"):
            capacity = (companion.width_px // 8) * (companion.height_px // 8)
            dimensions_are_tiles = (
                companion.width_px % 8 == 0
                and companion.height_px % 8 == 0
                and capacity >= asset.tile_count
            )
            if dimensions_are_tiles:
                asset = replace(
                    asset,
                    width_px=companion.width_px,
                    height_px=companion.height_px,
                    metadata_basis="decoded-companion-layout",
                )
        else:
            asset = replace(
                asset,
                width_px=companion.width_px,
                height_px=companion.height_px,
                pixel_mode=companion.pixel_mode,
                palette=companion.palette,
                metadata_basis="companion",
            )
        enriched.append(asset)
    return enriched


def _decode_1bpp_tile(tile_data: bytes) -> list[list[int]]:
    if len(tile_data) != 8:
        raise MalformedGraphicError(
            f"A 1bpp Game Boy tile must contain exactly 8 bytes, got {len(tile_data)}"
        )
    return [
        [((tile_data[row] >> (7 - column)) & 1) for column in range(8)]
        for row in range(8)
    ]


def _put_game_boy_palette(
    image: Image.Image, palette: Sequence[tuple[int, int, int, int]]
) -> None:
    rgb_values = [channel for color in palette for channel in color[:3]]
    image.putpalette(rgb_values + [0] * (768 - len(rgb_values)))


def _render_planar_image(
    data: bytes,
    depth: int,
    width_px: int,
    height_px: int,
    layout: str,
) -> Image.Image:
    bytes_per_tile = 8 * depth
    tile_count = len(data) // bytes_per_tile
    columns = width_px // 8
    rows = height_px // 8
    if width_px % 8 or height_px % 8 or columns * rows < tile_count:
        raise MalformedGraphicError(
            f"{width_px}x{height_px} cannot contain {tile_count} Game Boy tiles"
        )
    if layout not in ("row-major", "column-major"):
        raise GraphicsExportError(f"Unsupported Game Boy tile layout: {layout}")

    image = Image.new("P", (width_px, height_px), color=0)
    palette = GAME_BOY_1BPP_PALETTE if depth == 1 else GAME_BOY_2BPP_PALETTE
    _put_game_boy_palette(image, palette)

    for tile_index in range(tile_count):
        tile_start = tile_index * bytes_per_tile
        tile_data = data[tile_start : tile_start + bytes_per_tile]
        pixels = (
            _decode_1bpp_tile(tile_data)
            if depth == 1
            else decode_2bpp_tile(tile_data)
        )
        if layout == "column-major":
            tile_x = tile_index // rows
            tile_y = tile_index % rows
        else:
            tile_x = tile_index % columns
            tile_y = tile_index // columns
        for y, row_pixels in enumerate(pixels):
            for x, palette_index in enumerate(row_pixels):
                image.putpixel((tile_x * 8 + x, tile_y * 8 + y), palette_index)
    return image


def _encode_planar_image(
    image: Image.Image,
    depth: int,
    layout: str,
    tile_count: int,
) -> bytes:
    """Reverse a decoded sheet so validation can prove byte-for-byte fidelity."""
    if image.mode != "P":
        raise GraphicsExportError(
            f"A decoded Game Boy sheet must use indexed pixels, got {image.mode}"
        )
    columns = image.width // 8
    rows = image.height // 8
    encoded = bytearray()
    for tile_index in range(tile_count):
        if layout == "column-major":
            tile_x = tile_index // rows
            tile_y = tile_index % rows
        elif layout == "row-major":
            tile_x = tile_index % columns
            tile_y = tile_index // columns
        else:
            raise GraphicsExportError(f"Unsupported Game Boy tile layout: {layout}")

        for y in range(8):
            low_plane = 0
            high_plane = 0
            for x in range(8):
                palette_index = image.getpixel((tile_x * 8 + x, tile_y * 8 + y))
                if not 0 <= palette_index < (1 << depth):
                    raise GraphicsExportError(
                        f"Palette index {palette_index} cannot be encoded as {depth}bpp"
                    )
                shift = 7 - x
                low_plane |= (palette_index & 1) << shift
                high_plane |= ((palette_index >> 1) & 1) << shift
            encoded.append(low_plane)
            if depth == 2:
                encoded.append(high_plane)
    return bytes(encoded)


def _matching_pixel_count(candidate: Image.Image, reference_path: Path) -> int:
    with Image.open(reference_path) as reference:
        reference_rgba = reference.convert("RGBA")
    candidate_rgba = candidate.convert("RGBA")
    candidate_bytes = candidate_rgba.tobytes()
    reference_bytes = reference_rgba.tobytes()
    return sum(
        candidate_bytes[offset : offset + 4] == reference_bytes[offset : offset + 4]
        for offset in range(0, len(candidate_bytes), 4)
    )


def _render_source_asset(
    asset: SourceAsset,
    fallback_tiles_per_row: int,
) -> DerivedAsset:
    if asset.extension not in (".1bpp", ".2bpp"):
        raise GraphicsExportError(f"Cannot planar-decode {asset.extension}: {asset.path}")
    if fallback_tiles_per_row < 1:
        raise GraphicsExportError("Fallback tiles per row must be at least 1")

    depth = int(asset.extension[1])
    data = asset.path.read_bytes()
    if asset.width_px is not None and asset.height_px is not None:
        width_px = asset.width_px
        height_px = asset.height_px
    else:
        columns = min(fallback_tiles_per_row, asset.tile_count)
        rows = math.ceil(asset.tile_count / columns)
        width_px = columns * 8
        height_px = rows * 8

    row_image = _render_planar_image(data, depth, width_px, height_px, "row-major")
    layout = "row-major"
    image = row_image
    companion_path = asset.path.with_suffix(".png")
    companion_size = None
    if companion_path.is_file():
        with Image.open(companion_path) as companion:
            companion_size = companion.size
    if companion_size == (width_px, height_px):
        column_image = _render_planar_image(
            data, depth, width_px, height_px, "column-major"
        )
        row_score = _matching_pixel_count(row_image, companion_path)
        column_score = _matching_pixel_count(column_image, companion_path)
        if column_score > row_score:
            layout = "column-major"
            image = column_image

    png_buffer = io.BytesIO()
    image.save(png_buffer, format="PNG", optimize=False, compress_level=9)
    png_bytes = png_buffer.getvalue()
    output_relative = (
        Path("decoded") / Path(asset.source_relative_path + ".png")
    ).as_posix()
    return DerivedAsset(
        source_relative_path=asset.source_relative_path,
        output_relative_path=output_relative,
        png_bytes=png_bytes,
        sha256=_sha256_bytes(png_bytes),
        byte_size=len(png_bytes),
        width_px=width_px,
        height_px=height_px,
        tile_count=asset.tile_count,
        tiles_per_row=width_px // 8,
        layout=layout,
        category_path=asset.category_path,
        palette=asset.palette,
    )


def render_planar_assets(
    assets: Iterable[SourceAsset],
    fallback_tiles_per_row: int = DEFAULT_FALLBACK_TILES_PER_ROW,
) -> list[DerivedAsset]:
    """Decode all supported raw source assets into deterministic PNG bytes."""
    return [
        _render_source_asset(asset, fallback_tiles_per_row)
        for asset in assets
        if asset.extension in (".1bpp", ".2bpp")
    ]


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS graphic_derivations;
        DROP TABLE IF EXISTS graphic_source_links;
        DROP TABLE IF EXISTS graphic_assets;
        DROP TABLE IF EXISTS graphic_palette_colors;
        DROP TABLE IF EXISTS graphic_palettes;
        DROP TABLE IF EXISTS graphic_categories;
        DROP TABLE IF EXISTS graphic_formats;

        CREATE TABLE graphic_formats (
            id INTEGER PRIMARY KEY,
            extension TEXT NOT NULL UNIQUE,
            media_type TEXT NOT NULL,
            family TEXT NOT NULL CHECK (family IN (
                'raster_image', 'tile_graphics', 'compressed_graphics',
                'tilemap', 'blockset', 'assembly', 'object', 'other'
            )),
            bits_per_pixel INTEGER CHECK (bits_per_pixel IN (1, 2)),
            can_render INTEGER NOT NULL CHECK (can_render IN (0, 1))
        );

        CREATE TABLE graphic_categories (
            id INTEGER PRIMARY KEY,
            category_path TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            parent_id INTEGER,
            FOREIGN KEY (parent_id) REFERENCES graphic_categories(id)
        );

        CREATE TABLE graphic_palettes (
            id INTEGER PRIMARY KEY,
            palette_sha256 TEXT NOT NULL UNIQUE
                CHECK (length(palette_sha256) = 64
                    AND palette_sha256 NOT GLOB '*[^0-9a-f]*'),
            color_count INTEGER NOT NULL CHECK (color_count BETWEEN 1 AND 256)
        );

        CREATE TABLE graphic_palette_colors (
            palette_id INTEGER NOT NULL,
            color_index INTEGER NOT NULL CHECK (color_index BETWEEN 0 AND 255),
            red INTEGER NOT NULL CHECK (red BETWEEN 0 AND 255),
            green INTEGER NOT NULL CHECK (green BETWEEN 0 AND 255),
            blue INTEGER NOT NULL CHECK (blue BETWEEN 0 AND 255),
            alpha INTEGER NOT NULL CHECK (alpha BETWEEN 0 AND 255),
            PRIMARY KEY (palette_id, color_index),
            FOREIGN KEY (palette_id) REFERENCES graphic_palettes(id) ON DELETE CASCADE
        );

        CREATE TABLE graphic_assets (
            id INTEGER PRIMARY KEY,
            asset_role TEXT NOT NULL CHECK (asset_role IN ('source', 'derived')),
            path_scope TEXT NOT NULL CHECK (path_scope IN ('repository', 'graphics_output')),
            relative_path TEXT NOT NULL
                CHECK (relative_path <> ''
                    AND substr(relative_path, 1, 1) <> '/'
                    AND instr(relative_path, '\\') = 0
                    AND relative_path NOT LIKE '../%'
                    AND relative_path NOT LIKE '%/../%'),
            format_id INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            sha256 TEXT NOT NULL
                CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
            byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
            width_px INTEGER,
            height_px INTEGER,
            pixel_mode TEXT,
            tile_count INTEGER CHECK (tile_count > 0),
            palette_id INTEGER,
            metadata_basis TEXT NOT NULL CHECK (metadata_basis IN (
                'embedded', 'decoded', 'decoded-tile-grid',
                'decoded-companion-layout', 'companion', 'unavailable'
            )),
            UNIQUE (path_scope, relative_path),
            CHECK ((width_px IS NULL AND height_px IS NULL)
                OR (width_px > 0 AND height_px > 0)),
            CHECK ((asset_role = 'source' AND path_scope = 'repository')
                OR (asset_role = 'derived' AND path_scope = 'graphics_output')),
            FOREIGN KEY (format_id) REFERENCES graphic_formats(id),
            FOREIGN KEY (category_id) REFERENCES graphic_categories(id),
            FOREIGN KEY (palette_id) REFERENCES graphic_palettes(id)
        );

        CREATE TABLE graphic_source_links (
            source_asset_id INTEGER NOT NULL,
            companion_asset_id INTEGER NOT NULL,
            relation_type TEXT NOT NULL CHECK (relation_type = 'same_stem_preview'),
            PRIMARY KEY (source_asset_id, companion_asset_id, relation_type),
            CHECK (source_asset_id <> companion_asset_id),
            FOREIGN KEY (source_asset_id) REFERENCES graphic_assets(id) ON DELETE CASCADE,
            FOREIGN KEY (companion_asset_id) REFERENCES graphic_assets(id) ON DELETE CASCADE
        );

        CREATE TABLE graphic_derivations (
            id INTEGER PRIMARY KEY,
            source_asset_id INTEGER NOT NULL,
            derived_asset_id INTEGER NOT NULL UNIQUE,
            transformation TEXT NOT NULL CHECK (transformation = 'raw_tiles_to_png'),
            decoder_version TEXT NOT NULL,
            layout TEXT NOT NULL CHECK (layout IN ('row-major', 'column-major')),
            tiles_per_row INTEGER NOT NULL CHECK (tiles_per_row > 0),
            tile_count INTEGER NOT NULL CHECK (tile_count > 0),
            UNIQUE (source_asset_id, transformation),
            CHECK (source_asset_id <> derived_asset_id),
            FOREIGN KEY (source_asset_id) REFERENCES graphic_assets(id) ON DELETE CASCADE,
            FOREIGN KEY (derived_asset_id) REFERENCES graphic_assets(id) ON DELETE CASCADE
        );

        CREATE INDEX idx_graphic_assets_format ON graphic_assets(format_id);
        CREATE INDEX idx_graphic_assets_category ON graphic_assets(category_id);
        CREATE INDEX idx_graphic_assets_palette ON graphic_assets(palette_id);
        CREATE INDEX idx_graphic_derivations_source ON graphic_derivations(source_asset_id);
        CREATE INDEX idx_graphic_source_links_companion
            ON graphic_source_links(companion_asset_id);
        """
    )


def _insert_formats(conn: sqlite3.Connection, extensions: Iterable[str]) -> dict[str, int]:
    for extension in sorted(set(extensions) | {".png"}):
        media_type, family, bits_per_pixel, can_render = _format_metadata(extension)
        conn.execute(
            """
            INSERT INTO graphic_formats (
                extension, media_type, family, bits_per_pixel, can_render
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (extension, media_type, family, bits_per_pixel, can_render),
        )
    return {
        extension: format_id
        for format_id, extension in conn.execute("SELECT id, extension FROM graphic_formats")
    }


def _insert_categories(
    conn: sqlite3.Connection, category_paths: Iterable[str]
) -> dict[str, int]:
    all_paths = {"."}
    for category_path in category_paths:
        current = Path(category_path)
        while current.as_posix() not in (".", ""):
            all_paths.add(current.as_posix())
            current = current.parent
    ordered = sorted(
        all_paths,
        key=lambda value: (0 if value == "." else len(Path(value).parts), value),
    )
    ids: dict[str, int] = {}
    for category_path in ordered:
        if category_path == ".":
            name = "root"
            parent_id = None
        else:
            path = Path(category_path)
            name = path.name
            parent_path = path.parent.as_posix()
            parent_id = ids["." if parent_path == "." else parent_path]
        cursor = conn.execute(
            "INSERT INTO graphic_categories (category_path, name, parent_id) VALUES (?, ?, ?)",
            (category_path, name, parent_id),
        )
        ids[category_path] = cursor.lastrowid
    return ids


def _palette_digest(palette: Sequence[tuple[int, int, int, int]]) -> str:
    return _sha256_bytes(bytes(channel for color in palette for channel in color))


def _ensure_palette(
    conn: sqlite3.Connection,
    cache: dict[tuple, int],
    palette: tuple[tuple[int, int, int, int], ...] | None,
) -> int | None:
    if palette is None:
        return None
    if palette in cache:
        return cache[palette]
    cursor = conn.execute(
        "INSERT INTO graphic_palettes (palette_sha256, color_count) VALUES (?, ?)",
        (_palette_digest(palette), len(palette)),
    )
    palette_id = cursor.lastrowid
    conn.executemany(
        """
        INSERT INTO graphic_palette_colors (
            palette_id, color_index, red, green, blue, alpha
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (palette_id, index, *color)
            for index, color in enumerate(palette)
        ],
    )
    cache[palette] = palette_id
    return palette_id


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _graphics_manifest_bytes(conn: sqlite3.Connection) -> bytes:
    """Serialize the portable graphics bundle index from normalized rows."""
    sources = [
        {
            "path": path,
            "format": extension,
            "sha256": digest,
            "byteSize": byte_size,
        }
        for path, extension, digest, byte_size in conn.execute(
            """
            SELECT asset.relative_path, format.extension, asset.sha256,
                   asset.byte_size
            FROM graphic_assets AS asset
            JOIN graphic_formats AS format ON format.id = asset.format_id
            WHERE asset.asset_role = 'source'
            ORDER BY asset.relative_path
            """
        )
    ]
    derivations = [
        {
            "sourcePath": source_path,
            "path": derived_path,
            "sha256": digest,
            "byteSize": byte_size,
            "transformation": transformation,
            "decoderVersion": decoder_version,
            "layout": layout,
            "tilesPerRow": tiles_per_row,
            "tileCount": tile_count,
        }
        for (
            source_path,
            derived_path,
            digest,
            byte_size,
            transformation,
            decoder_version,
            layout,
            tiles_per_row,
            tile_count,
        ) in conn.execute(
            """
            SELECT source.relative_path, derived.relative_path,
                   derived.sha256, derived.byte_size,
                   relation.transformation, relation.decoder_version,
                   relation.layout, relation.tiles_per_row, relation.tile_count
            FROM graphic_derivations AS relation
            JOIN graphic_assets AS source ON source.id = relation.source_asset_id
            JOIN graphic_assets AS derived ON derived.id = relation.derived_asset_id
            ORDER BY source.relative_path, derived.relative_path
            """
        )
    ]
    payload = {
        "schemaVersion": 1,
        "sourceAssetCount": len(sources),
        "derivedAssetCount": len(derivations),
        "sources": sources,
        "derivations": derivations,
    }
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _connect(db_or_connection: Path | str | sqlite3.Connection):
    if isinstance(db_or_connection, sqlite3.Connection):
        return db_or_connection, False
    db_path = Path(db_or_connection)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(db_path), True


def validate_graphics_catalog(
    db_or_connection: Path | str | sqlite3.Connection = DB_PATH,
    source_root: Path = GFX_DIR,
    output_root: Path | None = None,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, int]:
    """Validate coverage, relationships, portability, files, and DB integrity."""
    source_root = Path(source_root).resolve()
    project_root = Path(project_root).resolve()
    output_root = (
        _default_output_root(project_root)
        if output_root is None
        else Path(output_root).resolve()
    )
    conn, owns_connection = _connect(db_or_connection)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        required_tables = {
            "graphic_formats",
            "graphic_categories",
            "graphic_palettes",
            "graphic_palette_colors",
            "graphic_assets",
            "graphic_source_links",
            "graphic_derivations",
        }
        present_tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        missing_tables = required_tables - present_tables
        if missing_tables:
            raise GraphicsExportError(
                f"Graphics catalog is missing tables: {', '.join(sorted(missing_tables))}"
            )

        source_files = _source_file_paths(source_root)
        expected_sources = {
            _portable_relative_path(path, project_root, "Graphics source")
            for path in source_files
        }
        actual_sources = {
            row[0]
            for row in conn.execute(
                """
                SELECT relative_path FROM graphic_assets
                WHERE asset_role = 'source' AND path_scope = 'repository'
                """
            )
        }
        if actual_sources != expected_sources:
            missing = sorted(expected_sources - actual_sources)[:5]
            extra = sorted(actual_sources - expected_sources)[:5]
            raise GraphicsExportError(
                f"Graphics source coverage mismatch; missing={missing}, extra={extra}"
            )

        for relative_path, expected_hash in conn.execute(
            """
            SELECT relative_path, sha256
            FROM graphic_assets
            WHERE asset_role = 'source'
            ORDER BY relative_path
            """
        ):
            source_path = project_root / relative_path
            if not source_path.is_file():
                raise GraphicsExportError(f"Missing cataloged source graphic: {source_path}")
            actual_hash = _sha256_bytes(source_path.read_bytes())
            if actual_hash != expected_hash:
                raise GraphicsExportError(f"Source graphic hash mismatch: {source_path}")

        raw_source_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM graphic_assets AS asset
            JOIN graphic_formats AS format ON format.id = asset.format_id
            WHERE asset.asset_role = 'source' AND format.extension IN ('.1bpp', '.2bpp')
            """
        ).fetchone()[0]
        derivation_count = conn.execute(
            "SELECT COUNT(*) FROM graphic_derivations"
        ).fetchone()[0]
        if derivation_count != raw_source_count:
            raise GraphicsExportError(
                f"Expected one derivation per raw planar source; "
                f"raw={raw_source_count}, derivations={derivation_count}"
            )
        invalid_derivations = conn.execute(
            """
            SELECT COUNT(*)
            FROM graphic_derivations AS derivation
            JOIN graphic_assets AS source ON source.id = derivation.source_asset_id
            JOIN graphic_formats AS source_format ON source_format.id = source.format_id
            JOIN graphic_assets AS derived ON derived.id = derivation.derived_asset_id
            JOIN graphic_formats AS derived_format ON derived_format.id = derived.format_id
            WHERE source.asset_role <> 'source'
               OR source_format.extension NOT IN ('.1bpp', '.2bpp')
               OR derived.asset_role <> 'derived'
               OR derived_format.extension <> '.png'
               OR source.tile_count <> derivation.tile_count
               OR derived.tile_count <> derivation.tile_count
            """
        ).fetchone()[0]
        missing_derivations = conn.execute(
            """
            SELECT COUNT(*)
            FROM graphic_assets AS source
            JOIN graphic_formats AS format ON format.id = source.format_id
            LEFT JOIN graphic_derivations AS derivation
                ON derivation.source_asset_id = source.id
            WHERE source.asset_role = 'source'
              AND format.extension IN ('.1bpp', '.2bpp')
              AND derivation.id IS NULL
            """
        ).fetchone()[0]
        if invalid_derivations or missing_derivations:
            raise GraphicsExportError(
                "Graphics derivation relationships are invalid; "
                f"invalid={invalid_derivations}, missing={missing_derivations}"
            )

        source_path_by_relative = {
            path.relative_to(source_root).as_posix(): _portable_relative_path(
                path, project_root, "Graphics source"
            )
            for path in source_files
        }
        expected_source_links = set()
        for source_relative, repository_relative in source_path_by_relative.items():
            if Path(source_relative).suffix.lower() == ".png":
                continue
            companion_relative = Path(source_relative).with_suffix(".png").as_posix()
            companion_repository_path = source_path_by_relative.get(companion_relative)
            if companion_repository_path is not None:
                expected_source_links.add(
                    (repository_relative, companion_repository_path, "same_stem_preview")
                )
        actual_source_links = {
            row
            for row in conn.execute(
                """
                SELECT source.relative_path, companion.relative_path, link.relation_type
                FROM graphic_source_links AS link
                JOIN graphic_assets AS source ON source.id = link.source_asset_id
                JOIN graphic_assets AS companion ON companion.id = link.companion_asset_id
                """
            )
        }
        if actual_source_links != expected_source_links:
            raise GraphicsExportError(
                "Graphics source companion relationships do not match the source tree"
            )

        invalid_palette_counts = conn.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT palette.id
                FROM graphic_palettes AS palette
                LEFT JOIN graphic_palette_colors AS color
                    ON color.palette_id = palette.id
                GROUP BY palette.id
                HAVING COUNT(color.color_index) <> palette.color_count
            )
            """
        ).fetchone()[0]
        if invalid_palette_counts:
            raise GraphicsExportError(
                f"Graphics catalog has {invalid_palette_counts} incomplete palettes"
            )

        portable_path_errors = conn.execute(
            """
            SELECT COUNT(*) FROM graphic_assets
            WHERE relative_path LIKE '/%'
               OR instr(relative_path, '\\') > 0
               OR relative_path LIKE '../%'
               OR relative_path LIKE '%/../%'
            """
        ).fetchone()[0]
        if portable_path_errors:
            raise GraphicsExportError(
                f"Graphics catalog contains {portable_path_errors} non-portable paths"
            )

        foreign_key_errors = [
            row
            for row in conn.execute("PRAGMA foreign_key_check")
            if str(row[0]).startswith("graphic_")
        ]
        if foreign_key_errors:
            raise GraphicsExportError(
                f"Graphics catalog has foreign-key violations: {foreign_key_errors[:5]}"
            )

        for relative_path, expected_hash, width, height in conn.execute(
            """
            SELECT asset.relative_path, asset.sha256, asset.width_px, asset.height_px
            FROM graphic_assets AS asset
            WHERE asset.asset_role = 'derived'
            ORDER BY asset.relative_path
            """
        ):
            output_path = output_root / relative_path
            if not output_path.is_file():
                raise GraphicsExportError(f"Missing derived graphic: {output_path}")
            data = output_path.read_bytes()
            if _sha256_bytes(data) != expected_hash:
                raise GraphicsExportError(f"Derived graphic hash mismatch: {output_path}")
            try:
                with Image.open(output_path) as image:
                    image.load()
                    if image.format != "PNG" or image.size != (width, height):
                        raise GraphicsExportError(
                            f"Derived graphic metadata mismatch: {output_path}"
                        )
            except (OSError, UnidentifiedImageError) as exc:
                raise GraphicsExportError(
                    f"Cannot validate derived graphic {output_path}: {exc}"
                ) from exc

        manifest_path = output_root / CATALOG_MANIFEST_NAME
        if not manifest_path.is_file():
            raise GraphicsExportError(f"Missing graphics catalog manifest: {manifest_path}")
        if manifest_path.read_bytes() != _graphics_manifest_bytes(conn):
            raise GraphicsExportError(
                f"Graphics catalog manifest does not match the database: {manifest_path}"
            )

        for source_path, derived_path, depth, layout, tile_count in conn.execute(
            """
            SELECT source.relative_path, derived.relative_path,
                   source_format.bits_per_pixel, derivation.layout,
                   derivation.tile_count
            FROM graphic_derivations AS derivation
            JOIN graphic_assets AS source ON source.id = derivation.source_asset_id
            JOIN graphic_formats AS source_format ON source_format.id = source.format_id
            JOIN graphic_assets AS derived ON derived.id = derivation.derived_asset_id
            ORDER BY source.relative_path
            """
        ):
            with Image.open(output_root / derived_path) as decoded_image:
                decoded_image.load()
                round_trip_bytes = _encode_planar_image(
                    decoded_image, depth, layout, tile_count
                )
            original_bytes = (project_root / source_path).read_bytes()
            if round_trip_bytes != original_bytes:
                raise GraphicsExportError(
                    f"Derived graphic is not a lossless representation of {source_path}"
                )

        source_links = conn.execute(
            "SELECT COUNT(*) FROM graphic_source_links"
        ).fetchone()[0]
        return {
            "source_assets": len(actual_sources),
            "derived_assets": derivation_count,
            "source_links": source_links,
        }
    finally:
        if owns_connection:
            conn.close()


def export_graphics(
    db_path: Path | str = DB_PATH,
    source_root: Path = GFX_DIR,
    output_root: Path | None = None,
    project_root: Path = PROJECT_ROOT,
    fallback_tiles_per_row: int = DEFAULT_FALLBACK_TILES_PER_ROW,
) -> dict[str, int]:
    """Build the complete graphics catalog and decoded PNG output."""
    source_root = Path(source_root).resolve()
    project_root = Path(project_root).resolve()
    output_root = (
        _default_output_root(project_root)
        if output_root is None
        else Path(output_root).resolve()
    )
    _validate_roots(source_root, output_root, project_root)
    if fallback_tiles_per_row < 1:
        raise GraphicsExportError("Fallback tiles per row must be at least 1")

    # Validation and rendering happen before touching the database so a
    # malformed supported source cannot erase a previously valid catalog.
    sources = collect_source_assets(source_root, project_root)
    derived = render_planar_assets(sources, fallback_tiles_per_row)

    conn, owns_connection = _connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:
            _create_schema(conn)
            format_ids = _insert_formats(conn, (asset.extension for asset in sources))
            category_ids = _insert_categories(
                conn, (asset.category_path for asset in sources)
            )
            palette_ids: dict[tuple, int] = {}
            source_ids: dict[str, int] = {}

            for asset in sources:
                palette_id = _ensure_palette(conn, palette_ids, asset.palette)
                cursor = conn.execute(
                    """
                    INSERT INTO graphic_assets (
                        asset_role, path_scope, relative_path, format_id, category_id,
                        sha256, byte_size, width_px, height_px, pixel_mode, tile_count,
                        palette_id, metadata_basis
                    ) VALUES ('source', 'repository', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        asset.repository_relative_path,
                        format_ids[asset.extension],
                        category_ids[asset.category_path],
                        asset.sha256,
                        asset.byte_size,
                        asset.width_px,
                        asset.height_px,
                        asset.pixel_mode,
                        asset.tile_count,
                        palette_id,
                        asset.metadata_basis,
                    ),
                )
                source_ids[asset.source_relative_path] = cursor.lastrowid

            # Existing authored PNGs are represented as source companions, not
            # duplicated into generated output.
            for asset in sources:
                if asset.extension == ".png":
                    continue
                companion_relative = Path(asset.source_relative_path).with_suffix(
                    ".png"
                ).as_posix()
                companion_id = source_ids.get(companion_relative)
                if companion_id is not None:
                    conn.execute(
                        """
                        INSERT INTO graphic_source_links (
                            source_asset_id, companion_asset_id, relation_type
                        ) VALUES (?, ?, 'same_stem_preview')
                        """,
                        (source_ids[asset.source_relative_path], companion_id),
                    )

            for asset in derived:
                palette_id = _ensure_palette(conn, palette_ids, asset.palette)
                cursor = conn.execute(
                    """
                    INSERT INTO graphic_assets (
                        asset_role, path_scope, relative_path, format_id, category_id,
                        sha256, byte_size, width_px, height_px, pixel_mode, tile_count,
                        palette_id, metadata_basis
                    ) VALUES (
                        'derived', 'graphics_output', ?, ?, ?, ?, ?, ?, ?, 'P', ?, ?, 'decoded'
                    )
                    """,
                    (
                        asset.output_relative_path,
                        format_ids[".png"],
                        category_ids[asset.category_path],
                        asset.sha256,
                        asset.byte_size,
                        asset.width_px,
                        asset.height_px,
                        asset.tile_count,
                        palette_id,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO graphic_derivations (
                        source_asset_id, derived_asset_id, transformation,
                        decoder_version, layout, tiles_per_row, tile_count
                    ) VALUES (?, ?, 'raw_tiles_to_png', ?, ?, ?, ?)
                    """,
                    (
                        source_ids[asset.source_relative_path],
                        cursor.lastrowid,
                        DECODER_VERSION,
                        asset.layout,
                        asset.tiles_per_row,
                        asset.tile_count,
                    ),
                )

            for asset in derived:
                _write_bytes_atomic(output_root / asset.output_relative_path, asset.png_bytes)

            _write_bytes_atomic(
                output_root / CATALOG_MANIFEST_NAME,
                _graphics_manifest_bytes(conn),
            )

            result = validate_graphics_catalog(
                conn,
                source_root=source_root,
                output_root=output_root,
                project_root=project_root,
            )
        return result
    finally:
        if owns_connection:
            conn.close()


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Catalog all source graphics and decode raw Game Boy tiles to PNG."
    )
    parser.add_argument("--db", type=Path, default=DB_PATH, help="SQLite database path")
    parser.add_argument(
        "--source-root",
        type=Path,
        default=GFX_DIR,
        help="Root graphics source directory",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help=(
            "Generated graphics root (default: POKEMON_EXTRACTOR_GRAPHICS_DIR "
            "or build/graphics)"
        ),
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=PROJECT_ROOT,
        help="Repository root used to create portable provenance paths",
    )
    parser.add_argument(
        "--fallback-tiles-per-row",
        type=int,
        default=DEFAULT_FALLBACK_TILES_PER_ROW,
        help="Sheet width for raw files without an authored PNG companion",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate an existing catalog and its generated files",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> dict[str, int]:
    args = build_argument_parser().parse_args(argv)
    operation = validate_graphics_catalog if args.validate_only else export_graphics
    result = operation(
        args.db,
        source_root=args.source_root,
        output_root=args.output_root,
        project_root=args.project_root,
        **(
            {"fallback_tiles_per_row": args.fallback_tiles_per_row}
            if not args.validate_only
            else {}
        ),
    )
    print(
        "Graphics catalog ready: "
        f"{result['source_assets']} sources, "
        f"{result['derived_assets']} decoded PNGs, "
        f"{result['source_links']} source companion links"
    )
    return result


if __name__ == "__main__":
    main()
