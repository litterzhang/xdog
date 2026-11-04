import pytest
from xdog.agent import AgentToolResult, ToolArtifact, ToolFailure


def test_outcomes_are_json_data_not_retry_or_permission_instructions():
    failure = AgentToolResult.from_failure(ToolFailure('cancelled', 'Cancelled after submission'))
    assert failure.is_error and failure.details['failure']['effect'] == 'unknown'
    assert 'retry' not in failure.details['failure']
    result = AgentToolResult.from_artifact(ToolArtifact('host-owned:result-1', size_bytes=1000000), 'short preview')
    assert len(result.content[0].text) < 100 and not result.is_error
    assert result.details['artifact'] == {
        'id': 'host-owned:result-1', 'media_type': 'text/plain', 'size_bytes': 1000000,
    }


@pytest.mark.parametrize('size', [True, -1, '100'])
def test_artifact_metadata_rejects_invalid_size(size):
    with pytest.raises(ValueError):
        ToolArtifact('reference', size_bytes=size)


def test_model_failure_survives_message_and_snapshot_round_trip():
    from xdog.agent.messages import dict_to_message, message_to_dict
    from xdog.ai import ModelFailure
    from xdog.ai.types import AssistantMessage

    message = AssistantMessage(stop_reason='error', failure=ModelFailure('rate_limit', 429, partial=True))
    encoded = message_to_dict(message)
    assert encoded['failure'] == message.failure.to_dict()
    assert dict_to_message(encoded) == message
