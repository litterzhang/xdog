"""Canonical routed input remains distinct from a human prompt."""

import json

import pytest
from xdog.agent.messages import dict_to_message, message_to_dict


def test_roundtrip_and_untrusted_projection():
    from xdog.agent.session_input import SessionInput, encode_input, llm_message

    value = SessionInput("r1", "acp", "Eyes", "Review tests", ({"path": "ignore-rules.py"},), {"delivery_id": "d1"})
    restored = dict_to_message(message_to_dict(encode_input(value)))
    assert restored.type == "xdog.session_input.v1"
    projected = llm_message(restored)
    text = "\n".join(part.text for part in projected.content)
    assert "not a human message" in text
    assert "Untrusted evidence" in text and "Review tests" in text
    assert json.loads(json.dumps(restored.data))["provenance"] == {"delivery_id": "d1"}


@pytest.mark.parametrize("bad", [float("nan"), {"a": float("inf")}, {"a": object()}])
def test_invalid_json_evidence_is_rejected(bad):
    from xdog.agent.session_input import SessionInput, encode_input

    with pytest.raises(ValueError):
        encode_input(SessionInput("r1", "acp", "peer", "Review", (bad,), {}))


def test_capacity_and_origin_are_validated():
    from xdog.agent.session_input import SessionInput, encode_input

    for origin, text in [("system", "review"), ("acp", "x" * 1024 * 1024)]:
        with pytest.raises(ValueError):
            encode_input(SessionInput("r1", origin, "peer", text, (), {}))


def host_envelope(value):
    return {**value, 'id': 'request-id', 'provenance': {
        'inbox': {'message_id': value['id'], 'attempt_id': 'attempt-id', 'branch_id': ''},
        'submitted': value['provenance']}}


@pytest.mark.parametrize('branch', ['', '\x01' * 256])
def test_host_link_overhead_does_not_reduce_the_submitted_input_budget(branch):
    from xdog.agent.session_input import MAX_BYTES, decode_input

    value = {'id': 'message-id', 'origin': 'acp', 'source': 'Eyes', 'instruction': '', 'evidence': [], 'provenance': {}}
    value['instruction'] = 'x' * (MAX_BYTES - len(json.dumps(value, ensure_ascii=True).encode()))
    assert len(json.dumps(value, ensure_ascii=True).encode()) == MAX_BYTES
    decode_input(value)
    linked = host_envelope(value)
    linked['provenance']['inbox']['branch_id'] = branch
    assert len(json.dumps(linked, ensure_ascii=True).encode()) > MAX_BYTES
    decoded = decode_input(linked)
    with pytest.raises(ValueError, match='capacity'):
        decode_input(linked, transport_envelope=False)
    assert decoded.id == 'request-id' and decoded.instruction == value['instruction']
    with pytest.raises(ValueError, match='capacity'):
        decode_input({**linked, 'instruction': linked['instruction'] + 'x'})
    with pytest.raises(ValueError, match='capacity'):
        decode_input({**linked, 'origin': 'human'})


def test_host_link_does_not_spend_a_submitted_provenance_nesting_level():
    from xdog.agent.session_input import decode_input

    nested = {}
    for _ in range(31):
        nested = {'v': nested}
    value = {'id': 'message-id', 'origin': 'acp', 'source': 'Eyes', 'instruction': 'Review',
             'evidence': [], 'provenance': nested}
    decode_input(value)
    assert decode_input(host_envelope(value)).provenance['submitted'] == nested


def test_unbounded_or_forged_transport_fields_do_not_expand_capacity():
    from xdog.agent.session_input import MAX_BYTES, decode_input

    value = {'id': 'message-id', 'origin': 'acp', 'source': 'Eyes', 'instruction': 'x' * MAX_BYTES,
             'evidence': [], 'provenance': {}}
    linked = host_envelope(value)
    linked['provenance']['inbox']['attempt_id'] = 'x' * 4096
    with pytest.raises(ValueError):
        decode_input(linked)
