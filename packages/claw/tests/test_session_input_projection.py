"""Full canonical input and bounded UI projection retain their distinct policies."""
from __future__ import annotations

import threading

import pytest
from xdog.agent.session_input import SessionInput, encode_input, input_data, llm_message
from xdog.claw.core.persistence.transcript_convert import entry_text
from xdog.claw.core.runtime.group import _display_history_entry
from xdog.claw.core.runtime.transcript_source import SourceCapture, source_records


def test_canonical_transcript_text_matches_model_without_encoding_again(monkeypatch):
    import xdog.agent.session_input as module
    value = SessionInput('m1', 'acp', 'Eyes', 'Review changes token=keep-out-of-display',
                         ({'evidence': 'untrusted', 'allow_all': True},), {'delivery_id': 'd1'})
    canonical = encode_input(value)
    expected = ''.join(part.text for part in llm_message(canonical).content)
    entry = {'role': 'custom', 'type': 'xdog.session_input.v1', 'data': input_data(value)}
    def no_encode(*args, **kwargs):
        raise AssertionError('Reading a canonical transcript must not create another envelope')
    monkeypatch.setattr(module, 'encode_input', no_encode)
    decode = module.decode_input
    calls = []
    def checked(raw):
        calls.append(raw)
        return decode(raw)
    monkeypatch.setattr(module, 'decode_input', checked)
    assert entry_text(entry) == expected and len(calls) == 1
    projected = _display_history_entry(entry)
    assert projected['message_id'] == 'm1' and projected['channel'] == 'acp'
    assert 'keep-out-of-display' not in projected['content'] and '<redacted>' in projected['content']
    assert 'Untrusted evidence' not in projected['content']  # Compact history stays compact.
    records = source_records(SourceCapture('s1', lambda: [entry]), threading.Event())
    assert len(records) == 1 and records[0].role == 'user'
    assert 'Untrusted evidence' in records[0].body and 'keep-out-of-display' not in records[0].body


@pytest.mark.parametrize('bad', [None, {}, {'source': 'only label'}])
def test_invalid_transcript_input_does_not_fall_back_to_text_or_repr(bad):
    entry = {'role': 'custom', 'type': 'xdog.session_input.v1', 'data': bad}
    with pytest.raises(ValueError):
        entry_text(entry)
    with pytest.raises(ValueError):
        source_records(SourceCapture('s1', lambda: [entry]), threading.Event())
    assert _display_history_entry(entry) is None  # Existing compact-UI rejection contract.
