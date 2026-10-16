"""Historical ContextMessage images keep their byte/descriptor guards without UI request modules."""
from __future__ import annotations

from dataclasses import asdict

import pytest
from xdog.agent.extensions.context_images import MAX_PROPOSAL_BYTES, _images, _summaries
from xdog.agent.extensions.context_message import content
from xdog.ai.types import ImageContent

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='
IMAGE = ImageContent(data=PNG, mime_type='image/png')


def test_historical_typed_image_bytes_and_descriptors_remain_exact_and_separate():
    images, descriptors = _images([asdict(IMAGE)])
    assert images == (IMAGE,) and descriptors[0]['bytes'] == 68
    assert len(descriptors[0]['sha256']) == 64 and PNG not in str(descriptors)
    _summaries(descriptors)
    assert content([asdict(IMAGE)]) == [asdict(IMAGE)]


@pytest.mark.parametrize('bad', [
    None, {}, [{'uri': 'https://never-fetch.invalid/image.png'}],
    [{**asdict(IMAGE), 'path': '/never-read'}], [{**asdict(IMAGE), 'data': 'not-base64'}],
    [{**asdict(IMAGE), 'mime_type': 'image/jpeg'}], [asdict(IMAGE)] * 5,
    [{**asdict(IMAGE), 'data': 'A' * (MAX_PROPOSAL_BYTES + 1)}],
])
def test_historical_image_codec_refuses_invalid_unowned_or_oversized_bytes(bad):
    with pytest.raises(ValueError):
        _images(bad)


@pytest.mark.parametrize('change', [{'bytes': True}, {'bytes': 0}, {'mime_type': []},
                                  {'mime_type': 'image/gif'}, {'sha256': 'bad'}, {'images': []}])
def test_historical_descriptors_cannot_coerce_counts_types_or_carry_attachment_bodies(change):
    _, descriptors = _images([asdict(IMAGE)])
    with pytest.raises(ValueError):
        _summaries([{**descriptors[0], **change}])
