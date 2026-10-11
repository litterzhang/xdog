"""Represent self-contained Windows calibrated RGB as a bounded ICC matrix/TRC.

CIEXYZ endpoints use signed 2.30; gamma uses unsigned 16.16. ICC matrix/TRC
profiles use D50 PCS with an explicit chromatic-adaptation matrix. No native
color engine, external profile path or optional package is needed at runtime.
"""
from __future__ import annotations

import hashlib
import math
import struct

_D50 = (0.9642, 1.0, 0.8249)
_BRADFORD = ((0.8951, 0.2664, -0.1614), (-0.7502, 1.7135, 0.0367), (0.0389, -0.0685, 1.0296))
_INVERSE = ((0.9869929, -0.1470543, 0.1599627), (0.4323053, 0.5183603, 0.0492912),
            (-0.0085287, 0.0400428, 0.9684867))


def _fixed(value: float) -> bytes:
    if not math.isfinite(value) or not -32768 <= value < 32768:
        raise ValueError('DIB calibration exceeds ICC representation bounds')
    number = round(value * 65536)
    if not -(2**31) <= number < 2**31:
        raise ValueError('DIB calibration fixed-point overflow')
    return struct.pack('>i', number)


def _xyz(values: tuple[float, ...]) -> bytes:
    return b'XYZ \0\0\0\0' + b''.join(_fixed(value) for value in values)


def _mluc(text: str) -> bytes:
    value = text.encode('utf-16-be')
    return b'mluc\0\0\0\0' + struct.pack('>II', 1, 12) + b'enUS' + struct.pack('>II', len(value), 28) + value


def _curve(gamma: int) -> bytes:
    if not gamma:
        raise ValueError('Calibrated DIB gamma must be positive')
    if gamma < 2**31:
        # ICC parametric function 0 represents exactly the same power curve.
        return b'para\0\0\0\0' + struct.pack('>HHI', 0, 0, gamma)
    # Windows' unsigned 16.16 also admits gamma above ICC signed 15.16.
    # A 65536-entry curve is bounded and includes every output 8-bit channel
    # exactly (index=sample*257), without clipping or silently replacing gamma.
    exponent = gamma / 65536
    return b'curv\0\0\0\0' + struct.pack('>I', 65536) + b''.join(
        struct.pack('>H', round((index / 65535) ** exponent * 65535)) for index in range(65536))


def calibrated_profile(header: bytes) -> bytes:
    if len(header) < 108:
        raise ValueError('Calibrated DIB needs a complete V4/V5 header')
    raw = struct.unpack_from('<9i', header, 60)
    columns = tuple(tuple(raw[index + offset] / 2**30 for offset in range(3)) for index in (0, 3, 6))
    white = tuple(sum(column[row] for column in columns) for row in range(3))
    if white[1] <= 0:
        raise ValueError('Calibrated DIB has no positive reference white')
    columns = tuple(tuple(value / white[1] for value in column) for column in columns)
    white = tuple(value / white[1] for value in white)
    a, b, c = columns
    determinant = (a[0] * (b[1] * c[2] - b[2] * c[1]) - b[0] * (a[1] * c[2] - a[2] * c[1])
                   + c[0] * (a[1] * b[2] - a[2] * b[1]))
    if abs(determinant) < 1e-12:
        raise ValueError('Calibrated DIB has singular RGB endpoints')
    source = tuple(sum(row[index] * white[index] for index in range(3)) for row in _BRADFORD)
    target = tuple(sum(row[index] * _D50[index] for index in range(3)) for row in _BRADFORD)
    if any(value <= 1e-12 for value in source):
        raise ValueError('Calibrated DIB white cannot be chromatically adapted')
    scale = tuple(target[index] / source[index] for index in range(3))
    adaptation = tuple(tuple(sum(_INVERSE[row][index] * scale[index] * _BRADFORD[index][column]
                                 for index in range(3)) for column in range(3)) for row in range(3))
    adapted = tuple(tuple(sum(row[index] * column[index] for index in range(3)) for row in adaptation)
                    for column in columns)
    gamma = struct.unpack_from('<3I', header, 96)
    tags = [(b'desc', _mluc('XDOG clipboard calibrated RGB')), (b'cprt', _mluc('Generated from clipboard calibration')),
            (b'wtpt', _xyz(_D50)), (b'chad', b'sf32\0\0\0\0' + b''.join(_fixed(value)
              for row in adaptation for value in row))]
    for index, channel in enumerate((b'r', b'g', b'b')):
        tags.extend(((channel + b'XYZ', _xyz(adapted[index])), (channel + b'TRC', _curve(gamma[index]))))
    profile = bytearray(128)
    struct.pack_into('>I', profile, 8, 0x04300000)
    profile[12:24] = b'mntrRGB XYZ '
    struct.pack_into('>6H', profile, 24, 2000, 1, 1, 0, 0, 0)  # Deterministic valid profile date; not capture metadata.
    profile[36:44] = b'acspMSFT'
    struct.pack_into('>I', profile, 64, 1)
    profile[68:80] = b''.join(_fixed(value) for value in _D50)
    profile[80:84] = b'xdog'
    profile.extend(struct.pack('>I', len(tags)))
    offset = 132 + 12 * len(tags)
    payload = bytearray()
    for signature, data in tags:
        profile.extend(signature + struct.pack('>II', offset + len(payload), len(data)))
        payload.extend(data)
        payload.extend(b'\0' * (-len(payload) % 4))
    profile.extend(payload)
    struct.pack_into('>I', profile, 0, len(profile))
    if len(profile) > 1024 * 1024:
        raise ValueError('Calibrated DIB profile exceeds retention bounds')
    fingerprint = bytearray(profile)
    fingerprint[44:48] = b'\0' * 4
    fingerprint[64:68] = b'\0' * 4
    profile[84:100] = hashlib.md5(fingerprint, usedforsecurity=False).digest()  # ICC identifier, not security/auth.
    return bytes(profile)
