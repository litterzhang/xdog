"""Typed child input, bounded persistence and duplicate-queue serialization budgets."""

from __future__ import annotations

import base64

import pytest
from xdog.agent.child_images import validate_images
from xdog.ai.types import ImageContent

from test_support.child_runtime.images import picture

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=")


def test_child_image_mime_mismatch_and_payload_bounds_are_not_text_fallbacks():
    with pytest.raises(ValueError, match="MIME"):
        validate_images((ImageContent(data=picture().data, mime_type="image/jpeg"),))
