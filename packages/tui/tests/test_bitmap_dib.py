from __future__ import annotations

import struct
import zlib

import pytest
from xdog.tui.bitmap_dib import dib_image


def dib(width, height, bits, pixels, *, header=40, compression=0, masks=(), palette=b'', profile=b''):
    if header == 12:
        data = bytearray(struct.pack('<IHHHH', 12, width, height, 1, bits))
    else:
        data = bytearray(header)
        struct.pack_into('<IiiHHIIIIII', data, 0, header, width, height, 1, bits, compression, len(pixels),
                         0, 0, len(palette) // 4, 0)
    if masks:
        if header == 40:
            data.extend(struct.pack('<' + 'I' * len(masks), *masks))
        else:
            struct.pack_into('<' + 'I' * len(masks), data, 40, *masks)
    data.extend(palette)
    data.extend(pixels)
    if profile:
        struct.pack_into('<I', data, 56, 0x4d424544)
        struct.pack_into('<II', data, 112, len(data), len(profile))
        data.extend(profile)
    return bytes(data)


def png_pixels(png):
    position, compressed, chunks = 8, b'', {}
    while position < len(png):
        size = struct.unpack_from('>I', png, position)[0]
        name, data = png[position + 4:position + 8], png[position + 8:position + 8 + size]
        assert zlib.crc32(name + data) == struct.unpack_from('>I', png, position + size + 8)[0]
        if name == b'IDAT':
            compressed += data
        chunks[name] = data
        position += size + 12
    return zlib.decompress(compressed), chunks


@pytest.mark.parametrize('height', [2, -2])
@pytest.mark.parametrize('header', [40, 52, 56, 108, 124])
@pytest.mark.parametrize('bits', [24, 32])
def test_rgb_rows_padding_top_down_bottom_up_and_opaque_unused_high_byte(height, header, bits):
    row = b'\0\0\xff' if bits == 24 else b'\0\0\xff\0'
    raw = dib(1, height, bits, (row + b'\0' * (4 - len(row))) * 2, header=header)
    pixels, _ = png_pixels(dib_image(raw, maximum=10000))
    assert pixels == b'\0\xff\0\0\xff' * 2


def test_orientation_and_core_header_rgb():
    red, blue = b'\0\0\xff\0', b'\xff\0\0\0'
    pixels, _ = png_pixels(dib_image(dib(1, 2, 24, blue + red), maximum=10000))
    assert pixels == b'\0\xff\0\0\xff\0\0\0\xff\xff'
    pixels, _ = png_pixels(dib_image(dib(1, 1, 24, red, header=12), maximum=10000))
    assert pixels == b'\0\xff\0\0\xff'


@pytest.mark.parametrize('header, compression', [(40, 6), (56, 3), (108, 3), (124, 3), (124, 0)])
def test_declared_alpha_is_preserved_without_guessing_alpha_from_rgb_padding(header, compression):
    raw = dib(1, -1, 32, b'\x10\x20\x30\x80', header=header, compression=compression,
              masks=(0xff0000, 0xff00, 0xff, 0xff000000))
    pixels, _ = png_pixels(dib_image(raw, maximum=10000))
    assert pixels == b'\0\x30\x20\x10\x80'


@pytest.mark.parametrize('compression, masks, value', [(0, (), 0x7c00), (3, (0xf800, 0x7e0, 0x1f), 0xf800)])
def test_16_bit_rgb_and_bitfields(compression, masks, value):
    raw = dib(1, 1, 16, struct.pack('<HH', value, 0), compression=compression, masks=masks)
    pixels, _ = png_pixels(dib_image(raw, maximum=10000))
    assert pixels == b'\0\xff\0\0\xff'


@pytest.mark.parametrize('bits, indexes', [(1, b'\x40\0\0\0'), (4, b'\x01\0\0\0'), (8, b'\0\1\0\0')])
def test_palette_bit_order_padding_and_bounds(bits, indexes):
    raw = dib(2, 1, bits, indexes, palette=b'\0\0\xff\0\xff\0\0\0')
    pixels, _ = png_pixels(dib_image(raw, maximum=10000))
    assert pixels == b'\0\xff\0\0\xff\0\0\xff\xff'


@pytest.mark.parametrize('bits, compression, raw', [
    (8, 1, b'\x03\x01\0\x01'),
    (8, 1, b'\0\x03\0\1\0\0\0\x01'),
    (4, 2, b'\x03\x10\0\x01'),
    (4, 2, b'\0\x03\x01\0\0\x01'),
])
def test_rle_encoded_absolute_and_word_alignment(bits, compression, raw):
    pixels, _ = png_pixels(dib_image(dib(3, 1, bits, raw, compression=compression,
        palette=b'\0\0\xff\0\xff\0\0\0'), maximum=10000))
    assert len(pixels) == 13


def test_rle_delta_rows_and_completion():
    raw = b'\0\x02\x01\0\x01\x01\0\0\x02\x01\0\x01'
    pixels, _ = png_pixels(dib_image(dib(2, 2, 8, raw, compression=1,
        palette=b'\0\0\xff\0\xff\0\0\0'), maximum=10000))
    assert pixels == b'\0\0\0\xff\xff\0\0\xff\xff\0\xff\0\0\xff\0\0\xff\xff'


def test_embedded_profile_is_retained_in_png_without_reading_a_linked_file():
    profile = bytearray(128)
    struct.pack_into('>I', profile, 0, 128)
    profile[36:40] = b'acsp'
    raw = dib(1, 1, 24, b'\0\0\xff\0', header=124, profile=profile)
    _, chunks = png_pixels(dib_image(raw, maximum=10000))
    assert zlib.decompress(chunks[b'iCCP'].split(b'\0', 2)[2]) == profile
    linked = bytearray(raw)
    struct.pack_into('<I', linked, 56, 0x4c494e4b)
    with pytest.raises(ValueError, match='Linked'):
        dib_image(bytes(linked), maximum=10000)


@pytest.mark.parametrize('raw', [b'', b'\0' * 40,
    dib(0, 1, 24, b'\0' * 4), dib(100001, 1000, 24, b'\0' * 4),
    dib(2, 1, 24, b'\0' * 4), dib(1, 1, 32, b'\0' * 4, compression=3, masks=(1, 1, 1)),
    dib(1, 1, 32, b'\0' * 4, compression=3, masks=(5, 0xff00, 0xff0000)),
    dib(1, 1, 8, b'\x02\0\0\0', palette=b'\0' * 8),
    dib(1, 1, 8, b'\x02\x01\0\x01', compression=1, palette=b'\0' * 8),
    dib(1, -1, 8, b'\0\x01', compression=1, palette=b'\0' * 8),
    dib(1, 1, 8, b'\x01\x01', compression=1, palette=b'\0' * 8),
])
def test_bad_headers_masks_palettes_rasters_and_rle_fail_closed(raw):
    with pytest.raises(ValueError):
        dib_image(raw, maximum=10000)


def test_converted_output_limit_is_not_just_an_input_byte_limit():
    with pytest.raises(ValueError, match='size limit'):
        dib_image(dib(1, 1, 24, b'\0' * 4), maximum=32)
