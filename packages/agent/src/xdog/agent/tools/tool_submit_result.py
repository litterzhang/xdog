"""submit_result tool — an agent submits its final structured result.

The agent calls ``submit_result`` with a JSON value. The caller supplies an
optional ``result_schema`` through the shared ``tool_ctx`` dict. On success the
tool writes the validated value under ``"result"`` in a caller-owned
``result_sink`` dict and returns an acceptance message. On failure it returns
an ``Error:`` string so the agent can fix the value and submit again.

Why a sink instead of writing a new ctx key: ToolDef dispatch passes the handler
a **shallow copy** of ``tool_ctx``. Mutating the shared sink value remains visible
to the caller after the agent turn drains; adding a context key would not.

The schema may be a JSON Schema (validated with fastjsonschema), or a flat
``{field_name: json_type}`` mapping requiring each declared field with a matching
type while allowing extra fields. With no schema, any JSON value is accepted.
The retired Flow-specific context keys are explicitly rejected, not interpreted
as an unconstrained submission or translated into the generic contract.
"""

from __future__ import annotations

from typing import Any

from xdog.agent.core import AgentTool
from xdog.agent.tool_def import Param, ToolDef

# JSON-schema type name -> the Python type(s) that satisfy it.
_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def _looks_like_json_schema(schema: dict[str, Any]) -> bool:
    """True if *schema* is a JSON Schema (has ``type``/``properties``) vs the
    legacy flat ``{field: json_type}`` mapping."""
    return "type" in schema or "properties" in schema


def _validate_against_schema(result: Any, schema: Any) -> str | None:
    """Return an error string if *result* violates *schema*, else ``None``.

    *schema* is either a real JSON Schema (validated with fastjsonschema) or the
    legacy flat ``{field: json_type}`` mapping (presence + type of declared
    fields; extra fields pass).  A falsy schema accepts anything.
    """
    if not schema:
        return None
    if not isinstance(schema, dict):
        return "invalid schema (expected an object)"

    if _looks_like_json_schema(schema):
        import fastjsonschema

        try:
            fastjsonschema.compile(schema)(result)
        except fastjsonschema.JsonSchemaException as exc:
            return str(exc)
        except ValueError as exc:  # invalid schema — don't block the agent on our bug
            return f"invalid schema: {exc}"
        return None

    # Legacy flat {field: json_type} mapping.
    if not isinstance(result, dict):
        return f"result must be an object, got {type(result).__name__}"
    for field, json_type in schema.items():
        if field not in result:
            return f"missing required field {field!r}"
        expected = _JSON_TYPES.get(json_type)
        if expected is None:
            # Unknown type in the schema — don't block the agent on our own bug.
            continue
        value = result[field]
        # bool is a subclass of int; reject it where a number/integer is wanted.
        if json_type in ("integer", "number") and isinstance(value, bool):
            return f"field {field!r} must be {json_type}, got boolean"
        if not isinstance(value, expected):
            return f"field {field!r} must be {json_type}, got {type(value).__name__}"
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
