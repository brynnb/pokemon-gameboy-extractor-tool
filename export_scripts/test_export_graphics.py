import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_graphics import (
    GraphicsExportError,
    MalformedGraphicError,
    export_graphics,
    validate_graphics_catalog,
)


def encode_uniform_2bpp_tile(palette_index):
    low_plane = 0xFF if palette_index & 1 else 0x00
    high_plane = 0xFF if palette_index & 2 else 0x00
    return bytes([low_plane, high_plane] * 8)


def make_1bpp_checkerboard():
    return bytes(0xAA if row % 2 == 0 else 0x55 for row in range(8))


class GraphicsExportTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temporary_directory.name) / "repository"
        self.source_root = self.project_root / "gfx"
        self.output_root = Path(self.temporary_directory.name) / "published-graphics"
        self.db_path = Path(self.temporary_directory.name) / "catalog.sqlite3"
        (self.source_root / "sprites").mkdir(parents=True)

        checker_data = make_1bpp_checkerboard()
        (self.source_root / "glyph.1bpp").write_bytes(checker_data)
        checker = Image.new("1", (8, 8), color=1)
        for y, byte in enumerate(checker_data):
            for x in range(8):
                checker.putpixel((x, y), 0 if byte & (1 << (7 - x)) else 1)
        checker.save(self.source_root / "glyph.png", format="PNG")

        # The stream is column-major: light gray is below white, while dark
        # gray is to the right. The companion lets the exporter detect that.
        tile_data = b"".join(encode_uniform_2bpp_tile(index) for index in range(4))
        (self.source_root / "sprites" / "column.2bpp").write_bytes(tile_data)
        companion = Image.new("L", (16, 16), color=255)
        values = (255, 170, 85, 0)
        positions = ((0, 0), (0, 8), (8, 0), (8, 8))
        for value, (x, y) in zip(values, positions):
            companion.paste(value, (x, y, x + 8, y + 8))
        companion.save(self.source_root / "sprites" / "column.png", format="PNG")

        (self.source_root / "orphan.1bpp").write_bytes(checker_data * 2)
        (self.source_root / "notes.asm").write_text(
            'INCBIN "gfx/glyph.1bpp"\n', encoding="utf-8"
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def export(self):
        return export_graphics(
            self.db_path,
            source_root=self.source_root,
            output_root=self.output_root,
            project_root=self.project_root,
        )

    def test_catalogs_every_source_and_decodes_planar_assets(self):
        result = self.export()
        self.assertEqual(
            result,
            {"source_assets": 6, "derived_assets": 3, "source_links": 2},
        )

        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            source_paths = {
                row[0]
                for row in conn.execute(
                    "SELECT relative_path FROM graphic_assets WHERE asset_role = 'source'"
                )
            }
            self.assertEqual(
                source_paths,
                {
                    "gfx/glyph.1bpp",
                    "gfx/glyph.png",
                    "gfx/notes.asm",
                    "gfx/orphan.1bpp",
                    "gfx/sprites/column.2bpp",
                    "gfx/sprites/column.png",
                },
            )
            self.assertFalse(any(Path(path).is_absolute() for path in source_paths))

            column = conn.execute(
                """
                SELECT derivation.layout, asset.width_px, asset.height_px,
                       derivation.tile_count, format.bits_per_pixel
                FROM graphic_derivations AS derivation
                JOIN graphic_assets AS source ON source.id = derivation.source_asset_id
                JOIN graphic_assets AS asset ON asset.id = derivation.derived_asset_id
                JOIN graphic_formats AS format ON format.id = source.format_id
                WHERE source.relative_path = 'gfx/sprites/column.2bpp'
                """
            ).fetchone()
            self.assertEqual(column, ("column-major", 16, 16, 4, 2))

            orphan = conn.execute(
                """
                SELECT width_px, height_px, metadata_basis
                FROM graphic_assets
                WHERE relative_path = 'gfx/orphan.1bpp'
                """
            ).fetchone()
            self.assertEqual(orphan, (None, None, "decoded-tile-grid"))
            orphan_derived = conn.execute(
                """
                SELECT derived.width_px, derived.height_px
                FROM graphic_derivations AS derivation
                JOIN graphic_assets AS source ON source.id = derivation.source_asset_id
                JOIN graphic_assets AS derived ON derived.id = derivation.derived_asset_id
                WHERE source.relative_path = 'gfx/orphan.1bpp'
                """
            ).fetchone()
            self.assertEqual(orphan_derived, (16, 8))

            palette = conn.execute(
                """
                SELECT palette.color_count
                FROM graphic_assets AS asset
                JOIN graphic_palettes AS palette ON palette.id = asset.palette_id
                WHERE asset.relative_path = 'gfx/sprites/column.2bpp'
                """
            ).fetchone()
            self.assertEqual(palette, (4,))
            category_parent = conn.execute(
                """
                SELECT parent.category_path
                FROM graphic_categories AS child
                JOIN graphic_categories AS parent ON parent.id = child.parent_id
                WHERE child.category_path = 'sprites'
                """
            ).fetchone()
            self.assertEqual(category_parent, (".",))

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE graphic_assets SET relative_path = '/absolute.png' WHERE id = 1"
                )

        decoded_column = self.output_root / "decoded" / "sprites" / "column.2bpp.png"
        with Image.open(decoded_column) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (16, 16))
            self.assertEqual(
                [image.convert("L").getpixel(point) for point in ((0, 0), (0, 8), (8, 0), (8, 8))],
                [255, 170, 85, 0],
            )

        with Image.open(self.output_root / "decoded" / "glyph.1bpp.png") as decoded:
            with Image.open(self.source_root / "glyph.png") as authored:
                self.assertEqual(
                    decoded.convert("RGBA").tobytes(),
                    authored.convert("RGBA").tobytes(),
                )

        # Authored source PNGs are linked in the catalog, not copied.
        self.assertFalse((self.output_root / "decoded" / "glyph.png").exists())
        manifest = json.loads(
            (self.output_root / "graphics-catalog.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["sourceAssetCount"], 6)
        self.assertEqual(manifest["derivedAssetCount"], 3)

    def test_output_is_deterministic_and_revalidates(self):
        self.export()
        output_file = self.output_root / "decoded" / "sprites" / "column.2bpp.png"
        first_hash = hashlib.sha256(output_file.read_bytes()).hexdigest()

        self.export()
        second_hash = hashlib.sha256(output_file.read_bytes()).hexdigest()
        self.assertEqual(first_hash, second_hash)
        self.assertEqual(
            validate_graphics_catalog(
                self.db_path,
                source_root=self.source_root,
                output_root=self.output_root,
                project_root=self.project_root,
            ),
            {"source_assets": 6, "derived_assets": 3, "source_links": 2},
        )

        (self.source_root / "notes.asm").write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(GraphicsExportError, "Source graphic hash mismatch"):
            validate_graphics_catalog(
                self.db_path,
                source_root=self.source_root,
                output_root=self.output_root,
                project_root=self.project_root,
            )

    def test_rejects_malformed_supported_graphics_before_writing(self):
        (self.source_root / "broken.2bpp").write_bytes(b"not-sixteen!!!")
        with self.assertRaisesRegex(MalformedGraphicError, "multiple of 16 bytes"):
            self.export()
        self.assertFalse(self.db_path.exists())
        self.assertFalse(self.output_root.exists())

    def test_rejects_an_output_root_inside_the_source_tree(self):
        with self.assertRaisesRegex(GraphicsExportError, "cannot be inside"):
            export_graphics(
                self.db_path,
                source_root=self.source_root,
                output_root=self.source_root / "generated",
                project_root=self.project_root,
            )

    def test_git_ignored_build_intermediates_do_not_change_the_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory) / "repository"
            source_root = project_root / "gfx"
            output_root = Path(directory) / "graphics"
            db_path = Path(directory) / "catalog.sqlite3"
            source_root.mkdir(parents=True)
            (project_root / ".gitignore").write_text("*.2bpp\n", encoding="utf-8")
            Image.new("L", (8, 8), color=255).save(source_root / "authored.png")
            (source_root / "generated.2bpp").write_bytes(
                encode_uniform_2bpp_tile(0)
            )
            subprocess.run(
                ["git", "init", "-q", str(project_root)],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(project_root), "add", ".gitignore", "gfx/authored.png"],
                check=True,
            )

            result = export_graphics(
                db_path,
                source_root=source_root,
                output_root=output_root,
                project_root=project_root,
            )

            self.assertEqual(
                result,
                {"source_assets": 1, "derived_assets": 0, "source_links": 0},
            )
            with closing(sqlite3.connect(db_path)) as conn:
                paths = conn.execute(
                    "SELECT relative_path FROM graphic_assets ORDER BY relative_path"
                ).fetchall()
            self.assertEqual(paths, [("gfx/authored.png",)])
            self.assertTrue((output_root / "graphics-catalog.json").is_file())


if __name__ == "__main__":
    unittest.main()
