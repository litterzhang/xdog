from __future__ import annotations

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.tool_inventory import capture, page, validate
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream


def agent(capability=None, count=1):
    async def execute(*args):
        pytest.fail('Reading a tool inventory cannot execute a tool')
        return AgentToolResult()
    def stream(*args):
        pytest.fail('Reading a tool inventory cannot submit a model turn')
        return EventStream.empty(AssistantMessage())
    tools = [AgentTool(name='native_' + str(index), description='Public definition',
        parameters={'type': 'object', 'properties': {'values': {'enum': [False, 0, None]}}}, execute=execute)
        for index in range(count)]
    return Agent(stream, config=AgentConfig(model='private/model', supports_tool_calls=capability), tools=tools)


@pytest.mark.parametrize('capability', [False, True, None])
def test_actual_inventory_snapshots_definitions_without_executor_context_or_permission_claims(capability):
    owner = agent(capability)
    captured = capture(owner, 'session', 'child')
    assert captured['api_enabled'] is (capability is not False)
    assert captured['tools'][0]['parameters']['properties']['values']['enum'] == [False, 0, None]
    assert set(captured['tools'][0]) == {'name', 'description', 'parameters'}
    captured['tools'][0]['parameters']['properties'].clear()
    assert owner.state.tools[0].parameters['properties']
    assert not owner.state.messages


def test_paged_search_stable_revision_and_no_silent_catalog_truncation():
    captured = capture(agent(count=80), 'session', 'owner')
    result = page(captured)
    assert result['total'] == 80 and len(result['tools']) == 32 and result['next_offset'] == 32
    second = page(captured, offset=32, revision=result['revision'])
    third = page(captured, offset=64, revision=result['revision'])
    assert len(second['tools']) == 32 and len(third['tools']) == 16 and third['next_offset'] is None
    assert len({tool['name'] for chunk in (result, second, third) for tool in chunk['tools']}) == 80
    filtered = page(captured, query='NATIVE_79')
    assert filtered['total'] == 1 and filtered['tools'][0]['name'] == 'native_79'
    captured['tools'][0]['description'] = 'Changed public schema metadata'
    stale = page(captured, offset=32, revision=result['revision'])
    assert stale['status'] == 'stale' and stale['tools'] == [] and stale['next_offset'] is None
    assert page(None) is None
    with pytest.raises(ValueError):
        page(1)


def test_queries_do_not_coerce_or_overrun_catalog() -> None:

    def _check_case(kwargs):
        with pytest.raises(ValueError):
            page(capture(agent(), "session", "owner"), **kwargs)

    for kwargs in [
        {"offset": True},
        {"offset": -1},
        {"offset": 2049},
        {"offset": 2},
        {"query": "\n"},
        {"query": "x" * 257},
        {"revision": "foreign"},
        {"revision": "z" * 64},
    ]:
        _check_case(kwargs)


def test_invalid_or_foreign_inventory_envelopes() -> None:

    def _check_case(changes):
        captured = capture(agent(), "session", "owner")
        if changes == {"tools": []}:
            changed = {**captured, "tools": [captured["tools"][0], captured["tools"][0]]}
        else:
            changed = {**captured, **changes}
        with pytest.raises(ValueError):
            validate(changed, session_id="session", owner_id="owner")

    for changes in [
        {"session_id": "foreign"},
        {"owner_id": "foreign"},
        {"api_enabled": 1},
        {"permission": "allow-all"},
        {"tools": [1]},
        {"tools": []},
    ]:
        _check_case(changes)


def test_nonfinite_schema_excessive_bytes_and_owner_change_fail_instead_of_claiming_empty(monkeypatch):
    owner = agent()
    owner.state.tools[0].parameters['bad'] = float('nan')
    with pytest.raises(ValueError):
        capture(owner, 'session', 'owner')
    owner.state.tools[0].parameters['bad'] = 'x' * (512 * 1024)
    with pytest.raises(ValueError):
        capture(owner, 'session', 'owner')
    owner = agent()
    from xdog.agent.extensions import tool_inventory
    original = tool_inventory.validate
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        owner.set_tools(())
        return result
    monkeypatch.setattr(tool_inventory, 'validate', changed)
    with pytest.raises(ValueError, match='changed'):
        capture(owner, 'session', 'owner')


async def test_api_is_headless_read_only_and_copies_every_page():
    api = ExtensionAPI({})
    assert await api.get_tool_inventory() is None
    api._tool_inventory.set(capture(agent(), 'session', 'owner'))
    first = await api.get_tool_inventory()
    first['tools'][0]['parameters'].clear()
    assert (await api.get_tool_inventory())['tools'][0]['parameters']
    assert await api.get_session() is None and not hasattr(api, 'open_sessions')


@pytest.mark.parametrize('selection', [None, [], ['native_1'], ['native_0', 'native_1']])
def test_inventory_distinguishes_selection_defaults_explicit_all_and_api_gate(selection):
    owner = agent(False, count=2)
    before = page(capture(owner, 'session', 'owner'))
    owner.set_active_tools(selection, expected_revision=owner.tool_selection_revision)
    value = capture(owner, 'session', 'owner')
    assert value['active_tools'] == (['native_0', 'native_1'] if selection is None else selection)
    assert value['selection_mode'] == ('default' if selection is None else 'explicit')
    assert not value['api_enabled'] and len(value['tools']) == 2
    current = page(value)
    assert current['active_total'] == len(value['active_tools'])
    assert current['active_tools'] == value['active_tools']
    if selection is not None:
        assert page(value, revision=before['revision'])['status'] == 'stale'
    filtered = page(value, query='native_0')
    assert filtered['active_tools'] == (['native_0'] if selection is None or 'native_0' in selection else [])


@pytest.mark.parametrize('bad', [None, ['absent'], ['native_0', 'native_0'], False])
def test_invalid_active_inventory_is_not_coerced_to_enabled(bad):
    captured = capture(agent(), 'session', 'owner')
    with pytest.raises(ValueError):
        validate({**captured, 'active_tools': bad}, session_id='session', owner_id='owner')


@pytest.mark.parametrize('changes', [{'selection_revision': True}, {'selection_revision': -1},
                                    {'selection_mode': 'unknown'}, {'active_tools': []}])
def test_selection_metadata_must_be_complete_and_match_defaults(changes):
    captured = capture(agent(), 'session', 'owner')
    with pytest.raises(ValueError):
        validate({**captured, **changes}, session_id='session', owner_id='owner')
    captured.pop('selection_revision')
    with pytest.raises(ValueError):
        validate(captured, session_id='session', owner_id='owner')


def test_legacy_inventory_stays_unknown_and_selection_races_reject_capture(monkeypatch):
    from xdog.agent.extensions import tool_inventory
    owner = agent()
    value = capture(owner, 'session', 'owner')
    legacy = {key: data for key, data in value.items() if key not in tool_inventory.SELECTION_FIELDS}
    assert 'active_tools' not in validate(legacy, session_id='session', owner_id='owner')
    assert 'active_tools' not in page(legacy)
    original = tool_inventory.validate
    def change_selection(*args, **kwargs):
        copied = original(*args, **kwargs)
        owner.set_active_tools([], expected_revision=owner.tool_selection_revision)
        return copied
    monkeypatch.setattr(tool_inventory, 'validate', change_selection)
    with pytest.raises(ValueError, match='changed'):
        capture(owner, 'session', 'owner')
