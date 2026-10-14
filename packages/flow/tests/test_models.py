"""Tests for flow.models — construction and immutability checks."""

from __future__ import annotations

import dataclasses

import pytest
from xdog.flow.models import Condition, EdgeDef, NodeDef, Port, WorkflowDef


def test_agent_output_schema_derived_from_multi_ports() -> None:
    from xdog.flow.models import Port, agent_is_structured, agent_output_schema

    node = NodeDef(
        id="n1",
        type="agent",
        output_ports=(Port("summary", schema={"type": "string"}), Port("count", schema={"type": "integer"})),
    )
    assert agent_is_structured(node) is True
    assert agent_output_schema(node) == {
        "type": "object",
        "properties": {"summary": {"type": "string"}, "count": {"type": "integer"}},
        "required": ["summary", "count"],
    }


def test_agent_single_string_port_is_plain_text() -> None:
    from xdog.flow.models import Port, agent_is_structured

    node = NodeDef(id="n1", type="agent", output_ports=(Port("answer"),))
    assert agent_is_structured(node) is False


def test_agent_single_structured_port_uses_its_schema() -> None:
    from xdog.flow.models import Port, agent_is_structured, agent_output_schema

    plan = {"type": "object", "properties": {"x": {"type": "integer"}}}
    node = NodeDef(id="n1", type="agent", output_ports=(Port("plan", schema=plan),))
    assert agent_is_structured(node) is True
    assert agent_output_schema(node) == plan


def test_the_model_types_are_actually_frozen() -> None:
    """What the five deleted "immutability" tests never checked.

    They each called `dataclasses.replace` and asserted the copy differed from
    the original — which is the documented behaviour of `dataclasses.replace`,
    not of anything here. Removing `frozen=True` from every model would have
    left all five green. This one fails.
    """
    subjects = (
        (NodeDef(id="n1"), "id", "n2"),
        (Condition(op="equals", value="a"), "op", "contains"),
        (EdgeDef(src="a", dst="b"), "dst", "c"),
        (WorkflowDef(name="w", provider="p", entry="n1", nodes=(), edges=()), "name", "other"),
        (Port(name="p"), "name", "q"),
    )
    for obj, field, value in subjects:
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, field, value)
