from __future__ import annotations

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.hooks import declaration
from xdog.agent.extensions.worker import dispatch


async def test_snapshot_dispatch_self_disposal_new_listener_next_round_and_old_disposer_identity():
    api, calls = ExtensionAPI({}), []
    async def first(context):
        calls.append('first')
        off()
        api.on('turn_start', third)
    async def second(context):
        calls.append('second')
    async def third(context):
        calls.append('third')
    off = api.on('turn_start', first)
    api.on('turn_start', second)
    await dispatch(api, 'xdog/event', {'event': 'turn_start'})
    assert calls == ['first', 'second']  # Removing first cannot skip the already admitted second.
    off()
    await dispatch(api, 'xdog/event', {'event': 'turn_start'})
    assert calls == ['first', 'second', 'second', 'third']
    revision = api._hook_revision
    off()
    assert api._hook_revision == revision
    replacement = api.on('turn_start', first)
    off()
    assert len(api.events['turn_start']) == 3
    replacement()


def test_each_hook_family_has_bounded_callable_registration_and_idempotent_disposal() -> None:

    def _check_case(kind):
        api = ExtensionAPI({})
        register = {
            "pre": api.before_tool,
            "post": api.after_tool,
            "context": api.context,
            "input": api.on_input,
            "event": lambda callback: api.on("turn_start", callback),
        }[kind]
        with pytest.raises(ValueError):
            register("not callable")
        disposers = [register(lambda *args: None) for _ in range(32)]
        with pytest.raises(ValueError):
            register(lambda *args: None)
        for dispose in disposers:
            dispose()
            dispose()
        current = api.hook_snapshot()
        assert not any((current[key] for key in ("pre", "post", "context", "input"))) and (not current["events"])

    for kind in ["pre", "post", "context", "input", "event"]:
        _check_case(kind)


def test_live_hook_metadata_does_not_coerce_or_accept_callback_objects() -> None:

    def _check_case(changes):
        with pytest.raises(ValueError):
            declaration({**ExtensionAPI({}).hook_snapshot(), **changes})

    for changes in [
        {"revision": True},
        {"events": ["unknown"]},
        {"events": ["turn_start"] * 2},
        {"pre": 1},
        {"callback": "private"},
        {"input": "true"},
    ]:
        _check_case(changes)
