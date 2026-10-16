from __future__ import annotations

import copy
import json

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.inspection import snapshot, validate
from xdog.ai.types import AssistantMessage, Model, StreamOptions, Usage, UserMessage, VisionLimits
from xdog.ai.utils.event_stream import EventStream


def agent():
    def stream(*args):
        pytest.fail('Inspection cannot call a model')
        return EventStream.empty(AssistantMessage())
    return Agent(stream, config=AgentConfig(model='private/model', context_window=64000, max_prompt_tokens=48000,
                                          options=StreamOptions(thinking='high')))


def test_actual_public_selected_model_excludes_credentials_endpoints_and_stale_caller_metadata():
    owner = agent()
    model = Model(id='private/model', name='Selected', provider='private', context_window=64000,
                  max_prompt_tokens=48000, max_tokens=8000, reasoning=True, input=('text', 'image'),
                  headers={'Authorization': 'PRIVATE-SECRET'}, base_url='https://PRIVATE-ENDPOINT.invalid',
                  supported_efforts=('low', 'high'),
                  vision_limits=VisionLimits(max_prompt_images=2, max_prompt_image_size=4096,
                                             supported_media_types=('image/png',)))
    value = snapshot(owner, 'session', 'model', model)
    assert value['id'] == 'private/model' and value['thinking'] == 'high'
    assert value['input'] == ['text', 'image'] and value['supported_efforts'] == ['low', 'high']
    assert value['context_window'] == 64000 and value['max_prompt_tokens'] == 48000
    assert value['vision_limits'] == {'max_prompt_images': 2, 'max_prompt_image_size': 4096,
                                      'supported_media_types': ['image/png']}
    assert 'PRIVATE-' not in json.dumps(value) and 'Authorization' not in json.dumps(value)
    value['input'].clear()
    assert model.input == ('text', 'image')
    stale = snapshot(owner, 'session', 'model', Model(id='old/model', reasoning=True, max_tokens=9999))
    assert stale['reasoning'] is None and stale['max_tokens'] == 0 and stale['provider'] == ''


def test_context_usage_is_actual_last_matching_response_with_cache_output_age_and_prompt_ceiling():
    owner = agent()
    owner.append_message(UserMessage(content='history'))
    owner.append_message(AssistantMessage(model='private/model', usage=Usage(input=100, output=20,
                                                                          cache_read=30, cache_write=40)))
    owner.append_message(UserMessage(content='not included in the prior measured prompt'))
    owner.append_message(AssistantMessage(model='other/model', usage=Usage(input=9000)))
    value = snapshot(owner, 'session', 'usage')
    assert value['tokens'] == 190 and value['context_limit'] == 48000
    assert value['percent'] == 100 * 190 / 48000
    assert value['source'] == 'last-response' and value['messages_since_usage'] == 2 and value['message_count'] == 4
    assert value['components'] == {'input': 100, 'output': 20, 'cache_read': 30, 'cache_write': 40}
    assert 'history' not in json.dumps(value)


def test_missing_usage_and_model_switch_never_turn_estimates_or_old_model_counts_into_measurements():
    owner = agent()
    owner.append_message(UserMessage(content='x' * 1000))
    value = snapshot(owner, 'session', 'usage')
    assert value['source'] == 'unavailable' and value['tokens'] is None and value['percent'] is None
    owner.append_message(AssistantMessage(model='old/model', usage=Usage(input=999)))
    assert snapshot(owner, 'session', 'usage')['source'] == 'unavailable'


@pytest.mark.parametrize('changes', [{'tokens': True}, {'tokens': 3}, {'percent': float('nan')},
    {'percent': 1}, {'source': 'estimated'}, {'history': []}, {'messages_since_usage': 100},
    {'context_limit': 100000}, {'components': {'input': True, 'output': 20, 'cache_read': 30, 'cache_write': 40}}])
def test_inspection_reply_does_not_coerce_counts_claim_fake_measurements_or_accept_transcript(changes):
    owner = agent()
    owner.append_message(AssistantMessage(model='private/model', usage=Usage(input=100, output=20,
                                                                          cache_read=30, cache_write=40)))
    value = snapshot(owner, 'session', 'usage')
    with pytest.raises(ValueError):
        validate('usage', {**value, **changes})


def test_public_model_snapshot_strictly_limits_fields_types_and_capability_bounds() -> None:

    def _check_case(changes):
        value = snapshot(agent(), "session", "model")
        with pytest.raises(ValueError):
            validate("model", {**value, **changes})
        unchanged = copy.deepcopy(value)
        assert validate("model", value) == unchanged

    for changes in [
        {"headers": {}},
        {"base_url": "private"},
        {"reasoning": 1},
        {"context_window": True},
        {"max_prompt_tokens": 999999},
        {"thinking": "invalid"},
        {"input": ["text", "text"]},
        {"supported_efforts": ["arbitrary"]},
        {"id": "x" * 513},
        {"vision_limits": {"headers": {}}},
    ]:
        _check_case(changes)


async def test_headless_api_never_sends_host_inspection_or_creates_model_work():
    api = ExtensionAPI({})
    assert await api.get_model() is None and await api.get_context_usage() is None
    assert not api._dialogs


@pytest.mark.parametrize('field,changed', [('session_id', 'foreign'), ('owner_id', 'foreign-child')])
def test_invocation_envelope_cannot_adopt_foreign_session_or_child_identity(field, changed):
    from xdog.agent.extensions.inspection import capture, envelope
    value = capture(agent(), 'session', 'child')
    with pytest.raises(ValueError, match='owner'):
        envelope({**value, field: changed}, session_id='session', owner_id='child')
    value['usage']['model'] = 'foreign-model'
    with pytest.raises(ValueError, match='inconsistent'):
        envelope(value, session_id='session', owner_id='child')


def test_child_context_counters_cannot_borrow_measured_parent_inherited_history():
    from xdog.agent.extensions.inspection import capture
    owner = agent()
    owner.append_message(AssistantMessage(model='private/model', usage=Usage(input=9999)))
    assert capture(owner, 'session', 'child', usage_floor=1)['usage']['source'] == 'unavailable'
    owner.append_message(AssistantMessage(model='private/model', usage=Usage(input=11, output=2)))
    assert capture(owner, 'session', 'child', usage_floor=1)['usage']['tokens'] == 13


async def test_read_only_snapshot_getter_returns_a_fresh_copy_without_command_authority():
    from xdog.agent.extensions.inspection import capture
    api = ExtensionAPI({})
    api._inspection.set(capture(agent(), 'session', 'session'))
    first = await api.get_model()
    first['name'] = 'mutated'
    assert (await api.get_model())['name'] == 'private/model'
    assert (await api.get_context_usage())['source'] == 'unavailable'
    assert await api.get_session() is None and not hasattr(api, 'open_sessions')
    assert not api._dialogs
