"""Current interfaces expose no removed application/child compatibility APIs."""
import importlib

import pytest
from xdog.agent.access import AccessState
from xdog.agent.child_config import ChildConfig
from xdog.agent.children import ChildSpec


def test_removed_child_and_access_interfaces_are_not_exposed():
    children = importlib.import_module('xdog.agent.children')
    assert not hasattr(children, 'child_task')
    assert not hasattr(ChildConfig, 'from_record')
    assert not hasattr(AccessState, 'legacy')
    for value in ({'instructions': 'old'}, {'inherit_context': True}, {'timeout': 1}, {'max_tokens': 10}):
        with pytest.raises(ValueError):
            ChildConfig.from_dict(value)
    with pytest.raises(TypeError):
        ChildSpec('id', 'task', instructions='old')
