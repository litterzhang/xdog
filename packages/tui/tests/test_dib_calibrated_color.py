from __future__ import annotations

import ctypes
import ctypes.util
import struct
import zlib

import pytest
from xdog.tui.bitmap_dib import dib_image
from xdog.tui.dib_color_profile import calibrated_profile

ENDPOINTS = ((0.4124564, 0.2126729, 0.0193339), (0.3575761, 0.7151522, 0.1191920),
             (0.1804375, 0.0721750, 0.9503041))


def dib(*, header=124, gammas=(2.2, 2.2, 2.2), endpoints=ENDPOINTS):
    data = bytearray(header)
    struct.pack_into('<IiiHHIIIIII', data, 0, header, 1, -1, 1, 32, 3, 4, 0, 0, 0, 0)
    struct.pack_into('<4I', data, 40, 0xff0000, 0xff00, 0xff, 0xff000000)
    struct.pack_into('<9i', data, 60, *(round(value * 2**30) for column in endpoints for value in column))
    struct.pack_into('<3I', data, 96, *(round(value * 65536) for value in gammas))
    data.extend(b'\x20\x40\x80\x70')
    return bytes(data)


def chunks(png):
    position, result = 8, {}
    while position < len(png):
        size = struct.unpack_from('>I', png, position)[0]
        kind = png[position + 4:position + 8]
        data = png[position + 8:position + 8 + size]
        assert zlib.crc32(kind + data) == struct.unpack_from('>I', png, position + size + 8)[0]
        result[kind] = result.get(kind, b'') + data
        position += size + 12
    return result


def tags(profile):
    result = {}
    for index in range(struct.unpack_from('>I', profile, 128)[0]):
        offset = 132 + index * 12
        kind, start, size = struct.unpack_from('>4sII', profile, offset)
        assert start % 4 == 0 and start + size <= len(profile)
        result[kind] = profile[start:start + size]
    return result


@pytest.mark.parametrize('header', [108, 124])
def test_calibrated_rgb_preserves_rgba_samples_and_embeds_self_contained_matrix_gamma_without_file_reads(header):
    raw = dib(header=header, gammas=(1.8, 2.2, 2.4))
    data = chunks(dib_image(raw, maximum=10000))
    assert zlib.decompress(data[b'IDAT']) == b'\0\x80\x40\x20\x70'
    profile = zlib.decompress(data[b'iCCP'].split(b'\0', 2)[2])
    assert struct.unpack_from('>I', profile)[0] == len(profile) and profile[36:40] == b'acsp'
    values = tags(profile)
    assert {b'wtpt', b'chad', b'rXYZ', b'gXYZ', b'bXYZ', b'rTRC', b'gTRC', b'bTRC'} <= values.keys()
    for name, gamma in zip((b'rTRC', b'gTRC', b'bTRC'), (1.8, 2.2, 2.4)):
        assert struct.unpack_from('>I', values[name], 12)[0] == round(gamma * 65536)
    assert profile == calibrated_profile(raw[:header])  # Deterministic, no capture/user metadata.


@pytest.mark.parametrize('endpoints,gammas', [
    (((0, 0, 0),) * 3, (2.2, 2.2, 2.2)),
    ((ENDPOINTS[0],) * 3, (2.2, 2.2, 2.2)),
    (ENDPOINTS, (0, 2.2, 2.2)),
    (((-0.5, -0.5, -0.5),) * 3, (2.2, 2.2, 2.2)),
])
def test_invalid_calibration_is_not_replaced_with_guessed_srgb(endpoints, gammas):
    with pytest.raises(ValueError):
        dib_image(dib(endpoints=endpoints, gammas=gammas), maximum=10000)


def test_unsigned_gamma_outside_icc_parametric_range_is_retained_as_bounded_curve_not_clipped():
    profile = calibrated_profile(dib(gammas=(40000, 40000, 40000)))
    curve = tags(profile)[b'rTRC']
    assert curve[:4] == b'curv' and struct.unpack_from('>I', curve, 8)[0] == 65536
    assert curve[12:14] == b'\0\0' and curve[-2:] == b'\xff\xff'
    assert len(profile) < 1024 * 1024
    with pytest.raises(ValueError, match='size limit'):
        dib_image(dib(gammas=(40000, 40000, 40000)), maximum=32)


class xyY(ctypes.Structure):
    _fields_ = [('x', ctypes.c_double), ('y', ctypes.c_double), ('Y', ctypes.c_double)]


class Primaries(ctypes.Structure):
    _fields_ = [('Red', xyY), ('Green', xyY), ('Blue', xyY)]


@pytest.mark.parametrize('gammas', [(2.2, 2.2, 2.2), (1.8, 2.2, 2.4)])
def test_generated_profile_color_transform_matches_independent_lcms_generated_calibrated_rgb(gammas):
    library = ctypes.util.find_library('lcms2')
    if not library:
        pytest.skip('independent system LittleCMS unavailable; not color-conformance evidence')
    lcms = ctypes.CDLL(library)
    def bind(name, args, result):
        function = getattr(lcms, name)
        function.argtypes, function.restype = args, result
        return function
    pointer = ctypes.c_void_p
    open_profile = bind('cmsOpenProfileFromMem', [pointer, ctypes.c_uint32], pointer)
    close_profile = bind('cmsCloseProfile', [pointer], ctypes.c_int)
    build_gamma = bind('cmsBuildGamma', [pointer, ctypes.c_double], pointer)
    free_curve = bind('cmsFreeToneCurve', [pointer], None)
    create_rgb = bind('cmsCreateRGBProfile', [ctypes.POINTER(xyY), ctypes.POINTER(Primaries),
                                            ctypes.POINTER(pointer)], pointer)
    create_srgb = bind('cmsCreate_sRGBProfile', [], pointer)
    create_transform = bind('cmsCreateTransform', [pointer, ctypes.c_uint32, pointer, ctypes.c_uint32,
                                                  ctypes.c_uint32, ctypes.c_uint32], pointer)
    do_transform = bind('cmsDoTransform', [pointer, pointer, pointer, ctypes.c_uint32], None)
    delete_transform = bind('cmsDeleteTransform', [pointer], None)
    raw = dib(gammas=gammas)
    profile = calibrated_profile(raw)
    memory = ctypes.create_string_buffer(profile)
    generated = open_profile(memory, len(profile))
    assert generated
    # Build the reference from the independently parsed source coordinates/gamma,
    # not our ICC bytes or adaptation/matrix-generation implementation.
    coordinates = tuple(tuple(value / 2**30 for value in struct.unpack_from('<3i', raw, 60 + index * 12))
                        for index in range(3))
    white_xyz = tuple(sum(column[index] for column in coordinates) for index in range(3))
    def chromaticity(xyz):
        return xyY(xyz[0] / sum(xyz), xyz[1] / sum(xyz), 1.0)
    white, primaries = chromaticity(white_xyz), Primaries(*(chromaticity(column) for column in coordinates))
    curves = [build_gamma(None, gamma / 65536) for gamma in struct.unpack_from('<3I', raw, 96)]
    reference = create_rgb(ctypes.byref(white), ctypes.byref(primaries), (pointer * 3)(*curves))
    output = create_srgb()
    assert reference and output
    for curve in curves:
        free_curve(curve)
    transforms = []
    try:
        samples = bytes((128, 64, 32, 255, 255, 255, 1, 2, 3, 230, 145, 80, 10, 200, 50))
        source = ctypes.create_string_buffer(samples)
        results = []
        for input_profile in (generated, reference):
            # RGB_8, relative-colorimetric intent, independent evaluation without LUT optimization.
            transform = create_transform(input_profile, 0x40019, output, 0x40019, 1, 0x100)
            assert transform
            transforms.append(transform)
            target = ctypes.create_string_buffer(len(samples))
            do_transform(transform, source, target, len(samples) // 3)
            results.append(target.raw)
        assert max(abs(left - right) for left, right in zip(*results)) <= 1
    finally:
        for transform in transforms:
            delete_transform(transform)
        for handle in (generated, reference, output):
            close_profile(handle)


def test_clipboard_calibrated_snapshot_converts_after_close_retains_profile_and_rejects_changed_revision():
    from xdog.tui.clipboard_windows import snapshot
    class Backend:
        def __init__(self, changed=False):
            self.opened, self.changed, self.checks, self.reads = False, changed, 0, []
        def open(self):
            self.opened = True
        def close(self):
            self.opened = False
        def sequence(self):
            self.checks += 1
            return 11 if self.changed and self.checks == 3 else 10
        def format(self, name):
            return name
        def available(self, format):
            return format == 17
        def read(self, format, maximum):
            self.reads.append(format)
            return dib()
    backend = Backend()
    kind, png = snapshot(backend, image_limit=10000)
    assert kind == 'image' and b'iCCP' in png and not backend.opened and backend.reads == [17]
    with pytest.raises(ValueError, match='changed during conversion'):
        snapshot(Backend(changed=True), image_limit=10000)
