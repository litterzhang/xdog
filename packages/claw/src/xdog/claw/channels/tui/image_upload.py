"""Claw connection image assembly with the existing terminal attachment header guard."""
from __future__ import annotations

from xdog.ai.types import GeneratedImage
from xdog.ai.utils.image_uploads import ImageUploads as _ImageUploads
from xdog.tui.attachments import attachment


def _validate(image: GeneratedImage) -> None:
    attachment(image.data, 'upload')


class ImageUploads(_ImageUploads):
    def __init__(self) -> None:
        super().__init__(_validate)
