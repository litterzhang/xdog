"""Bounded dialect-aware MCP schemas, resolving advertised documents only.

No registry retrieval opens URLs/files. Validation is in-process, not hostile-code
CPU isolation; explicitly configured servers remain a trust boundary.
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass
from typing import Any, Callable, cast

from jsonschema import Draft4Validator, Draft6Validator, Draft7Validator, Draft201909Validator, Draft202012Validator
from jsonschema.protocols import Validator
from jsonschema.validators import validator_for
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource
from referencing.jsonschema import DRAFT4, DRAFT6, DRAFT7, DRAFT201909, DRAFT202012
from xdog.agent.mcp.stdio import McpError

Schema = dict[str, Any] | bool
_DIALECTS = (Draft4Validator, Draft6Validator, Draft7Validator, Draft201909Validator, Draft202012Validator)
_SPECS = {Draft4Validator: DRAFT4, Draft6Validator: DRAFT6, Draft7Validator: DRAFT7,
          Draft201909Validator: DRAFT201909, Draft202012Validator: DRAFT202012}


def json_value(value: Any, *, maximum: int = 1024 * 1024) -> str:
    """Canonical JSON with bounded shape, no nonfinite numbers or implicit coercion."""
    stack = [(value, 0)]
    count = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if depth > 64 or count > 50000:
            raise McpError("MCP JSON shape exceeds its retention limit")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise McpError("MCP JSON object keys must be strings")
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise McpError("MCP value is not JSON data")
        elif type(item) is float and not math.isfinite(item):
            raise McpError("MCP JSON numbers must be finite")
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(text.encode()) > maximum:
            raise McpError("MCP JSON exceeds its retention limit")
        return text
    except (ValueError, UnicodeError, RecursionError):
        raise McpError("Invalid MCP JSON encoding") from None


def _deny(uri: str) -> Resource[Any]:
    raise NoSuchResource(uri)


def _dialect(schema: Schema, inherited: type[Validator]) -> type[Validator]:
    if isinstance(schema, dict) and "$schema" in schema:
        if not isinstance(schema["$schema"], str):
            raise McpError("Invalid MCP schema dialect")
        selected = cast(Callable[..., type[Validator] | None], validator_for)(schema, default=None)
        if selected not in _DIALECTS:
            raise McpError("Unsupported MCP schema dialect; it was not fetched or silently changed")
        return selected
    return inherited


@dataclass(frozen=True, slots=True)
class CompiledSchema:
    schema: Schema
    validator: Validator
    model_parameters: dict[str, Any] | None = None

    def validate(self, value: Any, *, label: str) -> None:
        json_value(value)
        try:
            self.validator.validate(value)
        except Exception:
            # Do not echo instance values/schema URLs into errors or tool history.
            raise McpError(f"MCP {label} does not satisfy its JSON Schema") from None


def compile_schema(value: object, *, input_schema: bool = False, legacy: bool = False) -> CompiledSchema:
    if not isinstance(value, (dict, bool)) or input_schema and not isinstance(value, dict):
        raise McpError("MCP schema must be a JSON Schema object (or boolean for modern outputs)")
    schema = cast(Schema, json.loads(json_value(value, maximum=256 * 1024)))
    if legacy and not isinstance(schema, dict):
        raise McpError("Legacy MCP tool schemas must be JSON Schema objects")
    selected = _dialect(schema, cast(type[Validator], Draft202012Validator))
    try:
        cast(Any, selected).check_schema(schema)
        root = Resource.from_contents(schema, default_specification=DRAFT202012)
        base = root.id() or ""
        registry: Registry[Any] = cast(Any, Registry)(retrieve=_deny).with_resource(base, root).crawl()
        stack = [(root, registry.resolver(base), selected, 0)]
        nodes = 0
        seen: set[tuple[int, type[Validator]]] = set()
        while stack:
            resource, resolver, inherited, depth = stack.pop()
            dialect = _dialect(resource.contents, inherited)
            identity = (id(resource.contents), dialect)
            if identity in seen:
                continue
            seen.add(identity)
            nodes += 1
            if depth > 64 or nodes > 4096:
                raise McpError("MCP schema exceeds its traversal limit")
            contents = resource.contents
            dialect = _dialect(contents, inherited)
            dialect.check_schema(contents)
            if isinstance(contents, dict):
                vocabularies = contents.get("$vocabulary", {})
                if dialect in (Draft201909Validator, Draft202012Validator):
                    supported = dialect.META_SCHEMA.get("$vocabulary", {})
                    if not isinstance(vocabularies, dict) or any(
                        type(required) is not bool or required and uri not in supported
                        for uri, required in vocabularies.items()
                    ):
                        raise McpError("Unsupported required MCP schema vocabulary")
                for key in ("$ref", "$dynamicRef", "$recursiveRef"):
                    if key in contents:
                        if not isinstance(contents[key], str):
                            raise McpError("Invalid MCP schema reference")
                        resolved = resolver.lookup(contents[key])
                        target = resolved.contents
                        if not isinstance(target, (dict, bool)):
                            raise McpError("MCP schema reference does not target a schema")
                        target_dialect = _dialect(target, dialect)
                        target_resource = Resource.from_contents(target,
                            default_specification=_SPECS[cast(Any, target_dialect)])
                        stack.append((target_resource, resolved.resolver, target_dialect, depth + 1))
            for child in resource.subresources():
                stack.append((child, resolver.in_subresource(child), dialect, depth + 1))
        validator = selected(schema, registry=registry)
    except McpError:
        raise
    except Exception:
        raise McpError("Invalid/unresolvable MCP schema; external documents are not fetched") from None
    model: dict[str, Any] | None = None
    if input_schema:
        assert isinstance(schema, dict)
        model = copy.deepcopy(schema)
        kind = model.get("type")
        if kind is None or kind == "object" or isinstance(kind, list) and "object" in kind:
            # MCP tool arguments are objects. This provider-facing intersection
            # preserves every applicable raw constraint and reference; validation
            # still uses the unmodified advertised schema in the owned helper.
            model["type"] = "object"
        else:
            raise McpError("MCP tool input schema cannot accept object arguments")
    return CompiledSchema(schema, validator, model)
