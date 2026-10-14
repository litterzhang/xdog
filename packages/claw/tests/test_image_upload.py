from __future__ import annotations

import base64
import hashlib

import pytest
from xdog.claw.channels.tui.image_upload import ImageUploads

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=")


def chunk(key="image", data=PNG, index=0, final=True, checksum=None):
    return dict(id=key, data=base64.b64encode(data).decode(), index=index, final=final, mime_type="image/png",
                sha256=checksum or hashlib.sha256(PNG).hexdigest())


def test_chunked_images_are_scoped_complete_validated_and_bounded():
    uploads = ImageUploads()
    assert uploads.chunk(chunk(data=PNG[:20], final=False)) == "partial"
    with pytest.raises(ValueError, match="completely"):
        uploads.resolve(["image"])
    assert uploads.chunk(chunk(data=PNG[20:], index=1)) == "ready"
    image = uploads.resolve(["image"])[0]
    assert base64.b64decode(image.data) == PNG
    other = ImageUploads()
    with pytest.raises(ValueError):
        other.resolve(["image"])
    with pytest.raises(ValueError):
        uploads.resolve(["image", "image"])
    uploads.discard("image")
    assert uploads._bytes == 0


def test_invalid_uploads_never_become_ready() -> None:

    def _check_case(bad):
        uploads = ImageUploads()
        with pytest.raises((ValueError, TypeError)):
            uploads.chunk({**chunk(), **bad})
        with pytest.raises(ValueError):
            uploads.resolve(["image"])

    for bad in [
        {"id": "", "index": 0},
        {"index": True},
        {"mime_type": []},
        {"data": "notbase64"},
        {"index": 1},
        {"sha256": "wrong"},
        {"data": "A" * 100001},
    ]:
        _check_case(bad)
