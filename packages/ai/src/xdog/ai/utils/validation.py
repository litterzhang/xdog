"""Offline JSON Schema validation shared by native tool registration/execution."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, cast

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: tuple[str, ...] = ()


def _deny(uri: str) -> Resource[Any]:
    raise NoSuchResource(uri)


@lru_cache(maxsize=256)
def _compiled(encoded: str) -> Draft202012Validator:
    schema = json.loads(encoded)
    dialect = schema.get('$schema')
    if dialect not in (None, 'https://json-schema.org/draft/2020-12/schema',
                       'https://json-schema.org/draft/2020-12/schema#'):
        raise ValueError('Native tools require Draft 2020-12; no dialect was silently changed')
    Draft202012Validator.check_schema(schema)
    stack: list[Any] = [schema]
    while stack:
        value = stack.pop()
        if not isinstance(value, dict):
            continue
        for key in ('$ref', '$dynamicRef'):
            reference = value.get(key)
            if reference is not None and (not isinstance(reference, str) or not reference.startswith('#')):
                raise ValueError('Native tool schema references must be local; no remote schema was loaded')
        # Walk schema positions, not literal defaults/examples/enum values.
        for key in ('$defs', 'definitions', 'properties', 'patternProperties', 'dependentSchemas'):
            mapping = value.get(key)
            if isinstance(mapping, dict):
                stack.extend(mapping.values())
        for key in ('allOf', 'anyOf', 'oneOf', 'prefixItems'):
            sequence = value.get(key)
            if isinstance(sequence, list):
                stack.extend(sequence)
        for key in ('items', 'contains', 'additionalProperties', 'unevaluatedProperties',
                    'unevaluatedItems', 'propertyNames', 'not', 'if', 'then', 'else', 'contentSchema'):
            stack.append(value.get(key))
    return Draft202012Validator(schema, registry=cast(Any, Registry)(retrieve=_deny))


def _encoded(schema: dict[str, Any]) -> str:
    if not isinstance(schema, dict):
        raise ValueError('Tool parameters must be a JSON Schema object')
    return json.dumps(schema, allow_nan=False, sort_keys=True, separators=(',', ':'))


def validate_tool_schema(schema: dict[str, Any]) -> None:
    """Reject invalid native definitions before publication, without fetching refs.

    Compiled definitions use bounded exact-content caching, not tool identity:
    mutable host definitions cannot reuse validation for an earlier schema.
    MCP keeps its own dialect-aware advertised-document registry.
    """
    try:
        _compiled(_encoded(schema))
    except Exception:
        raise ValueError('Invalid native tool JSON Schema or unsupported reference/dialect') from None


def validate_tool_arguments(arguments: dict[str, Any], schema: dict[str, Any]) -> ValidationResult:
    """Validate nested/union/enum/additional-property rules without echoing values."""
    try:
        validator = _compiled(_encoded(schema))
        json.dumps(arguments, allow_nan=False)
        errors = tuple(f"Argument {'.'.join(map(str, error.absolute_path)) or '$'} violates {error.validator}"
                       for error in validator.iter_errors(arguments))
        return ValidationResult(not errors, errors)
    except Exception:
        return ValidationResult(False, ('Tool arguments or local schema could not be validated',))
