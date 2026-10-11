"""Native macOS clipboard image/text snapshot, executed only on explicit paste.

The JXA helper imports AppKit/Foundation, not an application automation target.
Its stdout is bounded by the caller's existing owned subprocess runner.
"""
from __future__ import annotations

import base64
import binascii
import json
from typing import Protocol

MAX_TEXT_BYTES = 1024 * 1024

# Objective-C selectors correspond to Apple's NSPasteboard/NSBitmapImageRep APIs.
# https://developer.apple.com/documentation/appkit/nspasteboard
# https://developer.apple.com/documentation/appkit/nsbitmapimagerep
MACOS_SCRIPT = r"""
ObjC.import('AppKit');
ObjC.import('Foundation');
function run(argv) {
    var imageLimit = Number(argv[0]), textLimit = Number(argv[1]);
    var board = $.NSPasteboard.generalPasteboard;
    var revision = Number(board.changeCount);
    function exists(value) { return value && !value.isNil(); }
    function bounded(data, maximum) {
        if (!exists(data) || Number(data.length) <= 0) throw new Error('Clipboard data unavailable');
        if (Number(data.length) > maximum) throw new Error('Clipboard size limit');
        return data;
    }
    function finish(kind, data) {
        if (Number(board.changeCount) !== revision) throw new Error('Clipboard changed during read');
        return JSON.stringify({kind: kind, data: data ? ObjC.unwrap(data.base64EncodedStringWithOptions(0)) : ''});
    }
    var types = ObjC.deepUnwrap(board.types) || [];
    var direct = ['public.png', 'public.jpeg', 'org.webmproject.webp'];
    for (var i = 0; i < direct.length; i++) {
        if (types.indexOf(direct[i]) !== -1) {
            return finish('image', bounded(board.dataForType(direct[i]), imageLimit));
        }
    }
    if (types.indexOf('public.tiff') !== -1) {
        var source = bounded(board.dataForType('public.tiff'), imageLimit);
        var bitmap = $.NSBitmapImageRep.imageRepWithData(source);
        if (!exists(bitmap)) throw new Error('Clipboard image conversion failed');
        var width = Number(bitmap.pixelsWide), height = Number(bitmap.pixelsHigh);
        if (!(width > 0 && height > 0) || width * height > 100000000) throw new Error('Clipboard pixel limit');
        var png = bitmap.representationUsingTypeProperties($.NSBitmapImageFileTypePNG, $({}));
        return finish('image', bounded(png, imageLimit));
    }
    var text = board.stringForType('public.utf8-plain-text');
    if (!exists(text) || Number(text.length) === 0) return finish('text', null);
    return finish('text', bounded(text.dataUsingEncoding($.NSUTF8StringEncoding), textLimit));
}
"""


class Command(Protocol):
    def __call__(self, args: list[str], *, maximum: int) -> bytes: ...


def read_macos_clipboard(command: Command, helper: str, *, image_limit: int) -> tuple[str, bytes]:
    maximum = 4 * ((image_limit + 2) // 3) + 128
    try:
        output = command([helper, '-l', 'JavaScript', '-e', MACOS_SCRIPT, '--',
                          str(image_limit), str(MAX_TEXT_BYTES)], maximum=maximum)
        value = json.loads(output)
        if (not isinstance(value, dict) or set(value) != {'kind', 'data'}
                or value['kind'] not in ('image', 'text') or not isinstance(value['data'], str)):
            raise ValueError
        data = base64.b64decode(value['data'], validate=True)
        limit = image_limit if value['kind'] == 'image' else MAX_TEXT_BYTES
        if len(data) > limit or value['kind'] == 'image' and not data:
            raise ValueError
        return value['kind'], data
    except (ValueError, TypeError, binascii.Error, UnicodeError):
        # Never echo pasteboard content or an invalid helper response into the transcript.
        message = 'macOS clipboard snapshot failed or exceeded its limit; try copying again or /attach'
        raise ValueError(message) from None
