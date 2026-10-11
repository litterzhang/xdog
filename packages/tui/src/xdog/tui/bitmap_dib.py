"""Bounded packed Windows DIB conversion; no external codecs, profiles or file reads.

BITMAPINFO/V4/V5 layouts and RLE follow Microsoft's wingdi/bitmap documentation.
Clipboard HGLOBAL padding is not interpreted as another image or format.
"""
from __future__ import annotations

import struct
import zlib
from collections.abc import Iterator

MAX_PIXELS = 100_000_000
MAX_PROFILE_BYTES = 1024 * 1024
MAX_DIB_BYTES = MAX_PIXELS * 4 + MAX_PROFILE_BYTES + 2048


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))


def _rle(data: memoryview, width: int, height: int, bits: int) -> bytearray:
    output = bytearray(width * height)
    x = y = position = 0
    while position + 2 <= len(data):
        count, value = data[position], data[position + 1]
        position += 2
        if not count and value == 1:
            return output
        if not count and value == 0:
            x, y = 0, y + 1
            if y > height:
                raise ValueError('DIB RLE row exceeds bounds')
            continue
        if not count and value == 2:
            if position + 2 > len(data):
                raise ValueError('Incomplete DIB RLE delta')
            x, y = x + data[position], y + data[position + 1]
            position += 2
            if x > width or y >= height:
                raise ValueError('DIB RLE delta exceeds bounds')
            continue
        length = count or value
        if y >= height or x + length > width:
            raise ValueError('DIB RLE run exceeds bounds')
        if count:
            values = bytes([value]) * count if bits == 8 else bytes(
                value >> 4 if index % 2 == 0 else value & 15 for index in range(count))
        else:
            size = length if bits == 8 else (length + 1) // 2
            if position + size + (size % 2) > len(data):
                raise ValueError('Incomplete DIB RLE absolute run')
            raw = data[position:position + size]
            values = bytes(raw) if bits == 8 else bytes(
                raw[index // 2] >> 4 if index % 2 == 0 else raw[index // 2] & 15 for index in range(length))
            position += size + size % 2
        output[y * width + x:y * width + x + length] = values
        x += length
    raise ValueError('DIB RLE ended without its completion marker')


def _mask(mask: int, bits: int) -> tuple[int, int]:
    if not mask or mask >= 1 << bits:
        raise ValueError('Invalid DIB channel mask')
    shift = (mask & -mask).bit_length() - 1
    value = mask >> shift
    if value & (value + 1):
        raise ValueError('Noncontiguous DIB channel mask')
    return shift, value


def dib_image(data: bytes, *, maximum: int) -> bytes:
    """Return PNG (or the original embedded JPEG/PNG) after full raster bounds checks."""
    if not 12 <= len(data) <= MAX_DIB_BYTES:
        raise ValueError('Invalid DIB length')
    header = struct.unpack_from('<I', data)[0]
    if header == 12:
        width, signed_height, planes, bits = struct.unpack_from('<HHHH', data, 4)
        compression = declared = colors = 0
    elif header in (40, 52, 56, 108, 124) and len(data) >= header:
        width, signed_height, planes, bits, compression, declared = struct.unpack_from('<iiHHII', data, 4)
        colors = struct.unpack_from('<I', data, 32)[0]
    else:
        raise ValueError('Unsupported or incomplete DIB header')
    height = abs(signed_height)
    if planes != 1 or width <= 0 or height <= 0 or width * height > MAX_PIXELS:
        raise ValueError('DIB dimensions exceed image bounds')
    if compression not in (0, 1, 2, 3, 4, 5, 6) or signed_height < 0 and compression not in (0, 3, 6):
        raise ValueError('Invalid DIB compression/orientation')
    offset, profile = header, b''
    masks = (0x7c00, 0x03e0, 0x001f, 0) if bits == 16 else (0xff0000, 0xff00, 0xff, 0)
    if compression in (3, 6):
        if bits not in (16, 32):
            raise ValueError('Bitfields require 16/32-bit DIB pixels')
        count = 4 if compression == 6 else 3
        source = 40 if header >= 52 else header
        if source + count * 4 > len(data):
            raise ValueError('Incomplete DIB masks')
        values = struct.unpack_from('<' + 'I' * count, data, source)
        masks = (*values[:3], values[3] if count == 4 else struct.unpack_from('<I', data, 52)[0] if header >= 56 else 0)
        if header == 40:
            offset += count * 4
    if compression == 0 and header >= 56 and bits in (16, 32):
        masks = (*masks[:3], struct.unpack_from('<I', data, 52)[0])
    palette: tuple[bytes, ...] = ()
    if bits in (1, 4, 8) or colors:
        count = colors or (1 << bits)
        unit = 3 if header == 12 else 4
        if (count > (1 << bits if bits < 16 else MAX_DIB_BYTES // 4)
                or offset + count * unit > len(data)):
            raise ValueError('Invalid DIB palette')
        if bits < 16:
            palette = tuple(bytes((data[index + 2], data[index + 1], data[index], 255))
                            for index in range(offset, offset + count * unit, unit))
        offset += count * unit
    if header >= 108:
        space = struct.unpack_from('<I', data, 56)[0]
        if space == 0x4c494e4b:
            raise ValueError('Linked DIB profiles are not read')
        if space == 0 and any(data[60:108]):
            from xdog.tui.dib_color_profile import calibrated_profile
            profile = calibrated_profile(data[:header])
        if space not in (0, 0x73524742, 0x57696e20, 0x4d424544):
            raise ValueError('Unsupported DIB color space')
        if space == 0x4d424544:
            if header != 124:
                raise ValueError('DIB embedded profile needs a V5 header')
            start, size = struct.unpack_from('<II', data, 112)
            if (size < 128 or size > MAX_PROFILE_BYTES or start < offset or start + size > len(data)
                    or struct.unpack_from('>I', data, start)[0] != size or data[start + 36:start + 40] != b'acsp'):
                raise ValueError('Invalid DIB embedded profile')
            profile = data[start:start + size]
            if start == offset:
                offset = (start + size + 3) & ~3
    if compression in (4, 5):
        if not 0 < declared <= maximum or offset + declared > len(data):
            raise ValueError('Invalid embedded DIB image bounds')
        embedded = data[offset:offset + declared]
        expected = b'\xff\xd8\xff' if compression == 4 else b'\x89PNG\r\n\x1a\n'
        if not embedded.startswith(expected):
            raise ValueError('Invalid embedded DIB image signature')
        from xdog.tui.terminal_image import get_image_dimensions
        dimensions = get_image_dimensions(embedded)
        if dimensions is None or (dimensions.width_px, dimensions.height_px) != (width, height):
            raise ValueError('Embedded DIB image dimensions disagree')
        return embedded
    if bits not in (1, 4, 8, 16, 24, 32) or compression == 1 and bits != 8 or compression == 2 and bits != 4:
        raise ValueError('Unsupported DIB pixel format')
    stride = ((width * bits + 31) // 32) * 4
    raster_size = declared if compression in (1, 2) else stride * height
    if raster_size <= 0 or offset + raster_size > len(data):
        raise ValueError('Incomplete DIB raster')
    if profile and space == 0x4d424544 and start < offset + raster_size and start + size > offset:
        raise ValueError('DIB profile overlaps its pixels')
    raster = memoryview(data)[offset:offset + raster_size]
    indexes = _rle(raster, width, height, bits) if compression in (1, 2) else None
    channels = [_mask(mask, bits) for mask in masks if mask] if bits in (16, 32) else []
    used = 0
    for mask in masks if channels else ():
        if mask & used:
            raise ValueError('Overlapping DIB channel masks')
        used |= mask

    def rows() -> Iterator[bytes]:
        for row in range(height):
            source = row if signed_height < 0 else height - row - 1
            raw = raster[source * stride:(source + 1) * stride]
            output = bytearray(width * 4)
            if bits in (24, 32) and masks[:3] == (0xff0000, 0xff00, 0xff) and masks[3] in (0, 0xff000000):
                unit = bits // 8
                raw = raw[:width * unit]
                output[0::4], output[1::4], output[2::4] = raw[2::unit], raw[1::unit], raw[0::unit]
                output[3::4] = raw[3::unit] if unit == 4 and masks[3] else b'\xff' * width
            elif bits in (1, 4, 8):
                for column in range(width):
                    index = (indexes[source * width + column] if indexes is not None else
                             (raw[column * bits // 8] >> (8 - bits - column * bits % 8)) & ((1 << bits) - 1))
                    if index >= len(palette):
                        raise ValueError('DIB palette index out of bounds')
                    output[column * 4:column * 4 + 4] = palette[index]
            else:
                for column in range(width):
                    value = int.from_bytes(raw[column * (bits // 8):(column + 1) * (bits // 8)], 'little')
                    values = [((value >> shift) & mask) * 255 // mask for shift, mask in channels]
                    output[column * 4:column * 4 + 4] = bytes((*values[:3], values[3] if len(values) == 4 else 255))
            yield b'\0' + output

    output = bytearray(b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)))
    if profile:
        output.extend(_chunk(b'iCCP', b'clipboard\0\0' + zlib.compress(profile)))
    compressor = zlib.compressobj()
    for row in rows():
        part = compressor.compress(row)
        if part:
            output.extend(_chunk(b'IDAT', part))
        if len(output) > maximum:
            raise ValueError('Converted DIB exceeds image size limit')
    output.extend(_chunk(b'IDAT', compressor.flush()))
    output.extend(_chunk(b'IEND', b''))
    if len(output) > maximum:
        raise ValueError('Converted DIB exceeds image size limit')
    return bytes(output)
