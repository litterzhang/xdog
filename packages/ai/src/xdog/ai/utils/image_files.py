"""Small image-file helpers for CLI inputs/outputs, without a provider dependency."""
from __future__ import annotations

import base64
import binascii
import uuid
from pathlib import Path

from xdog.ai.types import GeneratedImage, ImageContent

_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
MAX_REFERENCE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_BYTES = 32 * 1024 * 1024


def image_mime_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("Only PNG, JPEG, and WebP image data are supported.")


def decode_image(data: str, mime_type: str, *, max_bytes: int = MAX_IMAGE_BYTES) -> GeneratedImage:
    if len(data) > ((max_bytes + 2) // 3) * 4:
        raise ValueError("Image data exceeds the size limit.")
    try:
        decoded = base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Invalid base64 image data.") from None
    if len(decoded) > max_bytes:
        raise ValueError("Image data exceeds the size limit.")
    if mime_type not in _EXTENSIONS or image_mime_type(decoded) != mime_type:
        raise ValueError("Image MIME type does not match PNG/JPEG/WebP data.")
    return GeneratedImage(data=decoded, mime_type=mime_type)


def read_reference(path: Path) -> ImageContent:
    path = path.expanduser()
    if not path.is_file():
        raise ValueError(f"Reference image is not a regular file: {path}")
    with path.open("rb") as source:
        data = source.read(MAX_REFERENCE_BYTES + 1)
    if len(data) > MAX_REFERENCE_BYTES:
        raise ValueError("Each reference image must be at most 10 MiB.")
    return ImageContent(data=base64.b64encode(data).decode("ascii"), mime_type=image_mime_type(data))


def save_images(images: tuple[GeneratedImage, ...], directory: Path) -> tuple[Path, ...]:
    if not images:
        raise ValueError("No images were generated.")
    for image in images:
        if image.mime_type not in _EXTENSIONS or image_mime_type(image.data) != image.mime_type:
            raise ValueError("Generated image MIME type does not match its data.")
    directory = directory.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    prefix = f"image-{uuid.uuid4().hex}"
    paths: list[Path] = []
    try:
        for index, image in enumerate(images, 1):
            path = directory / f"{prefix}-{index}{_EXTENSIONS[image.mime_type]}"
            with path.open("xb") as destination:
                paths.append(path)
                destination.write(image.data)
    except OSError:
        for path in paths:
            path.unlink(missing_ok=True)
        raise
    return tuple(paths)
