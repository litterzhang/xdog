"""Capability preflight and error facts are explicit and credential-free."""
from dataclasses import replace

import httpx
import pytest
from xdog.ai import ModelCapabilityError, ModelFailure, model_failure, validate_model_request
from xdog.ai.core import AuthResult
from xdog.ai.protocols import anthropic_messages, openai_completions, openai_responses
from xdog.ai.types import AssistantMessage, Context, ErrorEvent, Model, StreamOptions, Tool


@pytest.mark.parametrize('protocol', [anthropic_messages, openai_completions, openai_responses])
def test_explicit_no_tools_rejects_before_auth_or_transport(protocol):
    with pytest.raises(ModelCapabilityError) as error:
        protocol.stream(Model(supports_tool_calls=False), Context(tools=(Tool(name='native'),)),
                        StreamOptions(), AuthResult())
    assert error.value.failure == ModelFailure('unsupported', code='tool_calls')


def test_missing_capability_is_not_false_or_true():
    info = Model()
    assert info.supports_tool_calls is info.supports_streaming is info.supports_structured_outputs is None
    validate_model_request(info, Context(tools=(Tool(name='native'),)), StreamOptions(parallel_tool_calls=True))
    with pytest.raises(ModelCapabilityError):
        validate_model_request(replace(info, supports_parallel_tool_calls=False), Context(),
                               StreamOptions(parallel_tool_calls=True))


@pytest.mark.parametrize(('status', 'code', 'kind'), [
    (401, None, 'authentication'), (403, None, 'permission'), (429, None, 'rate_limit'),
    (404, None, 'request'), (400, 'context_length_exceeded', 'context'), (400, None, 'request'),
    (503, None, 'upstream'), (None, 'model_not_found', 'model'), (None, None, 'unknown'),
])
def test_explicit_status_codes_classify_without_retry_policy(status, code, kind):
    failure = model_failure(status=status, code=code, partial=True)
    assert failure.kind == kind and failure.partial is True
    assert set(failure.to_dict()) == {'kind', 'status', 'code', 'partial'}


def test_transport_and_protocol_are_distinct_and_never_retain_request():
    request = httpx.Request('POST', 'https://private.invalid/secret', headers={'Authorization': 'private'})
    failure = model_failure(httpx.HTTPStatusError('private body', request=request,
                                                response=httpx.Response(429, request=request)))
    assert failure.kind == 'rate_limit' and 'private' not in repr(failure)
    assert model_failure(httpx.ConnectError('private')).kind == 'transport'
    assert model_failure(httpx.RemoteProtocolError('truncated')).kind == 'protocol'
    assert model_failure(RuntimeError('context_length_exceeded')).kind == 'unknown'
    assert model_failure(code='private body\nsecret').code is None
    assert model_failure(code=[]).kind == 'unknown'


def test_terminal_event_carries_same_facts_in_final_message():
    failure = ModelFailure('protocol', partial=True)
    original = AssistantMessage(stop_reason='error', error_message='truncated')
    event = ErrorEvent(error='truncated', message=original, failure=failure)
    assert event.failure is event.message.failure is failure
    assert original.failure is None  # Frozen original is never mutated.


@pytest.mark.parametrize('fields', [{'kind': 'retry'}, {'status': True}, {'partial': 'yes'}, {'code': 'secret/path'}])
def test_failure_facts_are_finite_typed_data(fields):
    with pytest.raises(ValueError):
        ModelFailure(**fields)


def test_http_envelope_code_is_used_without_retaining_its_prose():
    from xdog.ai.failures import http_failure

    failure = http_failure(400, b'{"error":{"code":"context_length_exceeded","message":"private prompt"}}')
    assert failure.kind == 'context' and 'private' not in repr(failure)
    assert http_failure(404, b'not JSON').kind == 'request'
    assert http_failure(400, b'{"error":{"type":[]}}').kind == 'request'


def test_sdk_and_native_http_failures_keep_explicit_machine_codes_without_credentials():
    from xdog.ai import NativeHTTPError, NativeResponse

    class SdkError(Exception):
        status_code = 400
        code = 'context_length_exceeded'

    assert model_failure(SdkError('private prompt')).kind == 'context'
    response = NativeResponse(status=400, body=b'{"error":{"code":"model_not_found","message":"private"}}')
    failure = model_failure(NativeHTTPError(response))
    assert failure.kind == 'model' and 'private' not in repr(failure)
