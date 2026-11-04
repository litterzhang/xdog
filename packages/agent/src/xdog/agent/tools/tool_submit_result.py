"""submit_result tool — an agent submits its final structured result.

The agent calls ``submit_result`` with a JSON value. The caller supplies an
optional ``result_schema`` through the shared ``tool_ctx`` dict. On success the
tool writes the validated value under ``"result"`` in a caller-owned
``result_sink`` dict and returns an acceptance message. On failure it returns
an ``Error:`` string so the agent can fix the value and submit again.

Why a sink instead of writing a new ctx key: ToolDef dispatch passes the handler
a **shallow copy** of ``tool_ctx``. Mutating the shared sink value remains visible
to the caller after the agent turn drains; adding a context key would not.

The schema is an offline Draft 2020-12 JSON Schema object. With no schema, any
JSON value is accepted; old flat field/type maps are not converted.
The retired Flow-specific context keys are explicitly rejected, not interpreted
as an unconstrained submission or translated into the generic contract.
"""

from __future__ import annotations

from typing import Any

from xdog.agent.core import AgentTool
from xdog.agent.tool_def import Param, ToolDef


def _validate_against_schema(result: Any, schema: Any) -> str | None:
    """Validate only an explicit offline JSON Schema object, with no shorthand."""
    if schema is None:
        return None
    if not isinstance(schema, dict):
        return 'invalid schema (expected a JSON Schema object)'
    from jsonschema import Draft202012Validator
    from xdog.ai.utils.validation import validate_tool_schema

    keywords = set(Draft202012Validator.VALIDATORS) | {
        '$schema', '$id', '$defs', '$anchor', '$dynamicAnchor', '$dynamicRef', '$comment',
        'title', 'description', 'default', 'examples', 'deprecated', 'readOnly', 'writeOnly',
        'contentEncoding', 'contentMediaType', 'contentSchema',
    }
    if schema.keys() - keywords:
        return 'invalid schema: use JSON Schema keywords, not a flat field/type mapping'
    try:
        validate_tool_schema(schema)
        for error in Draft202012Validator(schema).iter_errors(result):
            path = '.'.join(map(str, error.absolute_path)) or '$'
            if error.validator == 'required' and isinstance(error.instance, dict):
                missing = [name for name in error.validator_value if name not in error.instance]
                return f'result {path} missing required fields: {", ".join(missing)}'
            return f'result {path} violates {error.validator}'
    except (ValueError, TypeError, RecursionError):
        return 'invalid result schema'
    return None


class SubmitResultTool(ToolDef):
    """Single-action tool for submitting a schema-validated structured result."""

    name = "submit_result"
    description = (
        "Submit your final structured result. It is validated against the "
        "required schema; if validation fails you receive an error and must "
        "fix the value and call submit_result again."
    )
    params = {
        "result": Param(
            # The caller's result_schema is the validator. A concrete type here
            # would reject valid arrays/scalars before that schema is consulted.
            "",
            required=True,
            description=(
                "The result to submit: an object when several fields were "
                "asked for, otherwise the value itself (array, string, number)."
            ),
        ),
    }

    async def execute(self, ctx: dict[str, Any] | None, result: Any) -> str:
        ctx = ctx or {}
        for old_key, new_key in (
            ("flow_output_schema", "result_schema"),
            ("flow_result_sink", "result_sink"),
        ):
            if old_key in ctx:
                return f"Error: unsupported context key {old_key!r}; use {new_key!r} instead"
        schema = ctx.get("result_schema")
        error = _validate_against_schema(result, schema)
        if error is not None:
            return f"Error: {error}"
        sink = ctx.get("result_sink")
        if isinstance(sink, dict):
            sink["result"] = result
        return "Result accepted."


def create_submit_result_tool() -> AgentTool:
    """Build the ``submit_result`` :class:`AgentTool`."""
    return SubmitResultTool().build()
