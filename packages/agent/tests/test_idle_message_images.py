"""Idle image messages are explicit, bounded, private and separate from queue/context."""
from __future__ import annotations

import json

import pytest
from xdog.agent.extensions.message_request import prepare, proposal, request, text
from xdog.ai.types import ImageContent

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='


@pytest.mark.parametrize('content', ['', '  /quit\n'])
def test_image_only_or_literal_text_exact_private_bytes_and_descriptor_review(content):
    source = proposal(content, (ImageContent(data=PNG, mime_type='image/png'),))
    prepared = prepare('sample', source)
    assert prepared.images[0].data == PNG and PNG not in json.dumps(prepared.review)
    assert prepared.review['images'] == [] and prepared.review['image_summaries'][0]['bytes'] > 0
    assert prepared.content.endswith(content) and prepared.matches(prepared.review)
    request(prepared.review)
    prepared.review['image_summaries'][0]['sha256'] = '0' * 64
    assert not prepared.matches(prepared.review)
    with pytest.raises(ValueError):
        text('')  # Busy-queue/context nonempty text contract did not change.


@pytest.mark.parametrize('bad', [None, [], ('uri://borrowed',), (ImageContent(data='bad', mime_type='image/png'),)])
def test_no_composer_upload_reference_or_invalid_binary_can_become_explicit_image(bad):
    with pytest.raises(ValueError):
        proposal('text', bad)


def test_worker_cannot_supply_host_descriptors_or_review_tokens():
    with pytest.raises(ValueError, match='own message review'):
        prepare('sample', {'text': 'pretend', 'images': [], 'image_summaries': []})
    with pytest.raises(ValueError):
        proposal('', ())


@pytest.mark.parametrize('field', ['message_generation', 'message_images_generation'])
def test_only_host_projection_accepts_exact_transport_generation_metadata(field):
    prepared = prepare('sample', proposal('', (ImageContent(data=PNG, mime_type='image/png'),)))
    projected = {**prepared.review, field: 1}
    request(projected)
    assert prepared.matches(projected)
    with pytest.raises(ValueError):
        prepare('sample', {**proposal('text'), field: 1})
    with pytest.raises(ValueError):
        request({**prepared.review, field: True})
