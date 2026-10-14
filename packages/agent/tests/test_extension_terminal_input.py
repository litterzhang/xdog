from __future__ import annotations

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.terminal_input import decision, metadata, text


def test_terminal_decisions_consume_transform_empty_and_false_are_distinct():
    assert decision(None).to_dict() == {'consume': False}
    assert decision({}).to_dict() == {'consume': False}
    assert decision({'consume': False}).to_dict() == {'consume': False}
    assert decision({'data': ''}).to_dict() == {'consume': False, 'data': ''}
    assert decision({'data': '\x1b[A', 'consume': True}).to_dict() == {'consume': True, 'data': '\x1b[A'}
    assert metadata({'kind': 'paste', 'surface': 'editor'}) == {'kind': 'paste', 'surface': 'editor'}
    assert text('界' * 5333) == '界' * 5333


def test_invalid_terminal_decisions_cannot_be_coerced_or_forwarded() -> None:

    def _check_case(value):
        with pytest.raises(ValueError):
            decision(value)

    for value in [
        True,
        "x",
        [],
        {"consume": 1},
        {"consume": None},
        {"data": None},
        {"data": "x" * 16001},
        {"data": "\ud800"},
        {"unknown": "field"},
    ]:
        _check_case(value)


def test_terminal_metadata_bounds() -> None:

    def _check_case(value):
        with pytest.raises(ValueError):
            metadata(value)

    for value in [
        None,
        {},
        {"kind": "key"},
        {"kind": "other", "surface": "editor"},
        {"kind": "key", "surface": ""},
        {"kind": "key", "surface": "x" * 129},
        {"kind": "key", "surface": "editor", "unknown": "field"},
    ]:
        _check_case(value)


def test_registration_unsubscribe_is_idempotent_bounded_and_requires_distinct_callables():
    api = ExtensionAPI({})
    def callback(*args):
        return None
    unsubscribe = api.on_terminal_input(callback)
    with pytest.raises(ValueError):
        api.on_terminal_input(callback)
    unsubscribe()
    unsubscribe()
    assert not api.terminal_hooks
    for _ in range(32):
        api.on_terminal_input(lambda *args: None)
    with pytest.raises(ValueError):
        api.on_terminal_input(lambda *args: None)
    with pytest.raises(ValueError):
        ExtensionAPI({}).on_terminal_input(None)


@pytest.mark.parametrize('value', [{'terminal_input': 1}, {'terminal_generation': True},
                                  {'terminal_generation': -1}, {'terminal_generation': 2**54}])
def test_terminal_snapshot_types_cannot_be_coerced(value):
    from xdog.agent.extensions.runtime import validate_snapshot
    with pytest.raises(ValueError):
        validate_snapshot({'extensions': [], 'commands': {}, 'ui': {}, **value})


def add_release_observer(source, log, *, behavior='continue'):
    """Install a real opt-in observer next to the fixture's unchanged legacy hook."""
    source.write_text(source.read_text() + f'''
    async def observe_release(data, metadata, context):
        import json
        from pathlib import Path
        with Path({str(log)!r}).open('a') as output:
            output.write(json.dumps({{'data': data, **metadata}}) + '\\n')
        if {behavior!r} == 'hang':
            await asyncio.Event().wait()
        if {behavior!r} == 'transform':
            return {{'data': 'forged press', 'consume': True}}
        return {{'consume': False}}
    api.on_terminal_input(observe_release, events=('release',))
''')


def test_terminal_event_registration_is_explicit_strict_and_nonempty() -> None:

    def _check_case(events):
        api = ExtensionAPI({})
        with pytest.raises(ValueError):
            api.on_terminal_input(lambda *args: None, events=events)
        assert not api.terminal_hooks and api.terminal_events() == []

    for events in [(), ("release", "release"), ("resize",), ("key", True), "release", None]:
        _check_case(events)


def test_stale_terminal_disposer_cannot_remove_reregistered_release_observer():
    api = ExtensionAPI({})
    def callback(*args):
        return None
    old = api.on_terminal_input(callback)
    old()
    current = api.on_terminal_input(callback, events=('release',))
    old()
    assert api.terminal_hooks == [callback] and api.terminal_events() == ['release']
    current()
    assert not api.terminal_hooks and not api.terminal_events()
