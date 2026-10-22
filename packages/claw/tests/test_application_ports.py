"""Application provider context is inert and independent from conversation owners."""
from dataclasses import fields

from xdog.agent.capabilities.runtime import ApplicationPort


def test_application_context_does_not_expose_session_or_group_business_ports():
    first, other = ApplicationPort(), ApplicationPort()
    assert first.scope == 'application' and first.identity != other.identity
    assert {item.name for item in fields(first)} == {'scope', 'identity'}
    for name in ('tool_scope', 'data_dir', 'open_database', 'input_get', 'output_records',
                 'register_output_source', 'capture_reply', 'authorize_target'):
        assert not hasattr(first, name)
