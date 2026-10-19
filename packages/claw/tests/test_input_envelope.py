"""Protocol-neutral input preserves independent content and host routing facts."""
from __future__ import annotations

import base64
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from xdog.agent.session_input import SessionInput
from xdog.ai.types import ImageContent


def message(identifier='one', **changes):
    return SessionInput(**{'id': identifier, 'origin': 'human', 'source': 'TUI',
                          'instruction': 'Check deployment deploy-123', 'evidence': (),
                          'provenance': {'source': 'original'}, **changes})


def image():
    return ImageContent(data=base64.b64encode(b'\x89PNG\r\n\x1a\nexample').decode(), mime_type='image/png')


def test_envelope_freezes_content_and_roundtrips_explicit_routing_and_images(tmp_path):
    from xdog.claw.core.inputs.models import InputEnvelope, InputScope, ReplyTarget, decode_envelope, encode_envelope

    submitted = message()
    value = InputEnvelope(InputScope('main', tmp_path), submitted, ReplyTarget('weixin', 'user-123'),
                          images=(image(),), human_authorized=True)
    submitted.provenance['source'] = 'forged'
    value.message.provenance['source'] = 'also forged'
    restored = decode_envelope(encode_envelope(value))
    assert restored.message.provenance == {'source': 'original'}
    assert restored.message.instruction == 'Check deployment deploy-123'
    assert restored.reply == ReplyTarget('weixin', 'user-123')
    assert restored.images == (image(),)
    assert restored.human_authorized is True
    with pytest.raises(FrozenInstanceError):
        value.human_authorized = False


def test_external_labels_do_not_create_human_authority_or_admission_binding(tmp_path):
    from xdog.claw.core.inputs.models import InputEnvelope, InputScope

    value = InputEnvelope(InputScope('main', tmp_path), message(origin='acp', source='human',
        provenance={'human_authorized': True, 'admission_key': 'goal:forged'}))
    assert value.human_authorized is False
    assert value.admission_key == ''
    with pytest.raises(ValueError, match='human'):
        InputEnvelope(value.scope, value.message, human_authorized=True)


@pytest.mark.parametrize('group,workspace', [('', '/tmp'), ('a\x00b', '/tmp'), ('x' * 257, '/tmp'),
                                           ('main', 'relative'), ('main', '/tmp/../elsewhere')])
def test_scope_rejects_invalid_or_ambiguous_identity(group, workspace):
    from xdog.claw.core.inputs.models import InputScope
    with pytest.raises(ValueError):
        InputScope(group, Path(workspace))


@pytest.mark.parametrize('field,value', [('unexpected', True), ('human_authorized', 1),
                                       ('images', [{'data': 'invalid', 'mime_type': 'image/png'}]),
                                       ('images', [{'data': '', 'mime_type': 'image/svg+xml'}]),
                                       ('reply', {'channel': 'weixin', 'recipient': ''}),
                                       ('admission_key', 'x' * 257)])
def test_decode_rejects_invalid_envelope_without_ignoring_fields(tmp_path, field, value):
    from xdog.claw.core.inputs.models import InputEnvelope, InputScope, decode_envelope, encode_envelope
    payload = json.loads(encode_envelope(InputEnvelope(InputScope('main', tmp_path), message())))
    payload[field] = value
    with pytest.raises(ValueError):
        decode_envelope(json.dumps(payload))


def test_original_content_limit_is_not_spent_on_host_routing(tmp_path):
    from xdog.agent.session_input import MAX_BYTES, input_data
    from xdog.claw.core.inputs.models import InputEnvelope, InputScope, ReplyTarget, decode_envelope, encode_envelope
    original = message(instruction='x')
    overhead = len(json.dumps(input_data(original), ensure_ascii=True).encode()) - 1
    content = message(instruction='x' * (MAX_BYTES - overhead))
    encoded = encode_envelope(InputEnvelope(InputScope('main', tmp_path), content, ReplyTarget('weixin', 'u' * 256)))
    assert decode_envelope(encoded).message.instruction == content.instruction
    with pytest.raises(ValueError, match='capacity'):
        InputEnvelope(InputScope('main', tmp_path), message(instruction='x' * MAX_BYTES))


def test_image_order_and_repeated_images_are_not_collapsed(tmp_path):
    from xdog.claw.core.inputs.models import InputEnvelope, InputScope, decode_envelope, encode_envelope
    value = InputEnvelope(InputScope('main', tmp_path), message(), images=(image(), image()))
    assert decode_envelope(encode_envelope(value)).images == (image(), image())
    with pytest.raises(ValueError):
        InputEnvelope(value.scope, value.message, images=(image(),) * 5)


def test_unknown_format_and_duplicate_json_keys_are_rejected(tmp_path):
    from xdog.claw.core.inputs.models import InputEnvelope, InputScope, decode_envelope, encode_envelope
    raw = encode_envelope(InputEnvelope(InputScope('main', tmp_path), message()))
    value = json.loads(raw)
    value['format'] = 'unknown'
    with pytest.raises(ValueError):
        decode_envelope(json.dumps(value))
    with pytest.raises(ValueError):
        decode_envelope(raw[:-1] + ', "human_authorized": true}')
