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
