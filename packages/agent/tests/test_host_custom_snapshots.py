"""Hosts explicitly bind data-only codecs; persisted type labels never load code."""
import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.core import CustomAgentMessage
from xdog.ai.utils.event_stream import EventStream


def agent(**kwargs):
    return Agent(lambda *args: EventStream.empty(), config=AgentConfig(model='test/dummy'), **kwargs)


def validate(data):
    if set(data) != {'speaker'} or not isinstance(data['speaker'], str):
        raise ValueError('invalid host fact')


def test_host_custom_records_roundtrip_only_with_explicit_host_validator():
    source = agent(custom_message_validators={'host.speaker.v1': validate})
    source.append_message(CustomAgentMessage('host.speaker.v1', {'speaker': 'A'}))
    snapshot = source.dump()
    assert 'custom_message_validators' not in snapshot
    with pytest.raises(ValueError, match='unsupported custom'):
        agent().restore(snapshot)
    target = agent(custom_message_validators={'host.speaker.v1': validate})
    target.restore(snapshot)
    assert target.messages == source.messages
    target.restore_parts(source.dump_metadata(), source.messages)
    assert target.messages == source.messages


def test_validator_cannot_rewrite_snapshot_or_infer_code_from_unknown_label():
    calls = []
    def mutate(data):
        calls.append(data.copy())
        data['speaker'] = 'rewritten'
    source = agent(custom_message_validators={'host.speaker.v1': mutate})
    source.append_message(CustomAgentMessage('host.speaker.v1', {'speaker': 'A'}))
    value = source.dump()
    assert value['messages'][0]['data']['speaker'] == 'A'
    assert source.messages[0].data['speaker'] == 'A' and calls
    value['messages'][0]['type'] = 'arbitrary.module.function'
    with pytest.raises(ValueError, match='unsupported custom'):
        source.restore(value)


def test_invalid_host_data_fails_dump_and_restore_without_replacing_live_history():
    source = agent(custom_message_validators={'host.speaker.v1': validate})
    source.append_message(CustomAgentMessage('host.speaker.v1', {'speaker': 123}))
    with pytest.raises(ValueError, match='invalid custom'):
        source.dump()


async def test_queued_context_is_detached_admitted_with_its_body_and_removed_with_input():
    from xdog.ai.types import UserMessage
    original = CustomAgentMessage('host.speaker.v1', {'speaker': 'A'})
    value = agent(custom_message_validators={'host.speaker.v1': validate})
    value.queue_input('i1', 'body', 'steer', context_messages=(original,))
    original.data['speaker'] = 'forged after queue'
    value.change_input('i1', 'edit', 'edited body')
    selected = value._dequeue_steering()
    prepared = await value._prepare_inputs(selected, 'steer')
    assert prepared[0].data == {'speaker': 'A'}
    assert isinstance(prepared[1], UserMessage) and prepared[1].content == 'edited body'
    value.drain_inputs()
    assert value._queued_input_context == {}
    value.queue_input('i2', 'never admitted', 'steer', context_messages=(original,))
    assert value.change_input('i2', 'remove')
    assert value._queued_input_context == {} and not value.messages


def test_standalone_decoder_rejects_async_or_boolean_validators_without_running_them():
    from xdog.agent.snapshots import decode_snapshot
    source = agent(custom_message_validators={'host.speaker.v1': validate})
    source.append_message(CustomAgentMessage('host.speaker.v1', {'speaker': 'A'}))
    snapshot = source.dump()
    ran = []
    async def asynchronous(data):
        ran.append(True)
    with pytest.raises(ValueError, match='invalid custom'):
        decode_snapshot(snapshot, custom_validators={'host.speaker.v1': asynchronous})
    assert ran == []
    with pytest.raises(ValueError, match='invalid custom'):
        decode_snapshot(snapshot, custom_validators={'host.speaker.v1': lambda data: False})
