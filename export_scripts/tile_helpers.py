"""Shared helpers for Game Boy tileset and block rendering."""

import hashlib
import io

from PIL import Image

GAMEBOY_PALETTE = (
    (255, 255, 255),
    (192, 192, 192),
    (96, 96, 96),
    (0, 0, 0),
)

BLOCK_SIZE_BYTES = 16
TILE_SIZE_BYTES = 16
TILE_PIXEL_SIZE = 8

BLOCK_QUADRANT_TILE_COORDS = (
    ((0, 0), (0, 1), (1, 0), (1, 1)),
    ((0, 2), (0, 3), (1, 2), (1, 3)),
    ((2, 0), (2, 1), (3, 0), (3, 1)),
    ((2, 2), (2, 3), (3, 2), (3, 3)),
)


def parse_fixed_size_records(file_path, record_size, label):
    """Parse a binary file into fixed-size byte records."""
    records = []
    try:
        with open(file_path, "rb") as f:
            file_data = f.read()

        record_bytes = len(file_data) - (len(file_data) % record_size)
        for start_pos in range(0, record_bytes, record_size):
            records.append(file_data[start_pos : start_pos + record_size])
        return records
    except Exception as e:
        print(f"Error parsing {label} file {file_path}: {e}")
        return []


def parse_blockset_file(blockset_path):
    """Parse a blockset (.bst) file into 4x4 tile-index block records."""
    return parse_fixed_size_records(blockset_path, BLOCK_SIZE_BYTES, "blockset")


def parse_2bpp_file(file_path):
    """Parse a 2bpp file into 8x8 tile records."""
    return parse_fixed_size_records(file_path, TILE_SIZE_BYTES, "2bpp")


def decode_2bpp_tile(tile_data):
    """Decode one 2bpp 8x8 tile into palette indices."""
    pixels = []
    for row in range(TILE_PIXEL_SIZE):
        byte1 = tile_data[row * 2]
        byte2 = tile_data[row * 2 + 1]
        row_pixels = []
        for bit in range(TILE_PIXEL_SIZE):
            bit_pos = 7 - bit
            bit1 = (byte1 >> bit_pos) & 1
            bit2 = (byte2 >> bit_pos) & 1
            row_pixels.append((bit2 << 1) | bit1)
        pixels.append(row_pixels)
    return pixels


def get_image_hash(img):
    """Hash a PIL image's PNG bytes for deduplication."""
    img_bytes = io.BytesIO()
    img.save(img_bytes, format="PNG")
    return hashlib.md5(img_bytes.getvalue()).hexdigest()


def draw_tile_to_image(img, tile_data, offset_x, offset_y, palette=GAMEBOY_PALETTE):
    """Draw one decoded 8x8 tile into a PIL image at pixel offsets."""
    tile_pixels = decode_2bpp_tile(tile_data)
    for py in range(TILE_PIXEL_SIZE):
        for px in range(TILE_PIXEL_SIZE):
            pixel_value = tile_pixels[py][px]
            img.putpixel((offset_x + px, offset_y + py), palette[pixel_value])


def draw_scaled_tile(draw, tile_data, tile_x, tile_y, scale, palette=GAMEBOY_PALETTE):
    """Draw one decoded 8x8 tile to an ImageDraw context with integer scaling."""
    tile_pixels = decode_2bpp_tile(tile_data)
    for py in range(TILE_PIXEL_SIZE):
        for px in range(TILE_PIXEL_SIZE):
            pixel_value = tile_pixels[py][px]
            pixel_color = palette[pixel_value]
            for sy in range(scale):
                for sx in range(scale):
                    draw.point(
                        (tile_x + px * scale + sx, tile_y + py * scale + sy),
                        fill=pixel_color,
                    )


def render_block_quadrant_image(block_data, tiles, position):
    """Render one 2x2 tile quadrant of a 4x4 Game Boy block as a 16x16 image."""
    img = Image.new("RGB", (16, 16), color=(255, 255, 255))
    for i, (y, x) in enumerate(BLOCK_QUADRANT_TILE_COORDS[position]):
        tile_pos = y * 4 + x
        if tile_pos >= len(block_data):
            continue
        tile_data = tiles.get(block_data[tile_pos])
        if not tile_data:
            continue
        offset_x = (i % 2) * TILE_PIXEL_SIZE
        offset_y = (i // 2) * TILE_PIXEL_SIZE
        draw_tile_to_image(img, tile_data, offset_x, offset_y)
    return img
