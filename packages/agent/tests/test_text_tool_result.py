"""Consumers can return typed tool text through Agent, without importing a provider layer."""
from __future__ import annotations

import pytest
from xdog.agent import AgentToolResult


def test_text_factory_constructs_actual_typed_content_and_error_metadata():
    result = AgentToolResult.from_text('structured response', is_error=True, details={'id': 'one'})
    assert len(result.content) == 1 and result.content[0].type == 'text'
    assert result.content[0].text == 'structured response'
    assert result.is_error is True and result.details == {'id': 'one'}


@pytest.mark.parametrize('value', [None, 1, {}, ['not text']])
def test_text_factory_rejects_non_text_values(value):
    with pytest.raises(ValueError):
        AgentToolResult.from_text(value)
