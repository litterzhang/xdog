"""Bounded typed extension form codec; submission is data, never host approval."""

from __future__ import annotations

import json
import math
import re
from typing import Any, cast

KINDS = ("text", "integer", "number", "boolean", "choice", "multi_choice")


def _text(value: Any, maximum: int, *, empty: bool = True) -> str:
    if not isinstance(value, str) or not empty and not value.strip():
        raise ValueError("Invalid extension form text")
    try:
        if len(value.encode()) > maximum:
            raise ValueError("Extension form text exceeds its limit")
    except UnicodeError:
        raise ValueError("Invalid extension form text encoding") from None
    return value


def _number(value: Any) -> int | float:
    if type(value) not in (int, float) or type(value) is float and not math.isfinite(value):
        raise ValueError("Extension form number must be finite, not boolean/coerced")
    if type(value) is int and abs(value) > 2**53 - 1:
        raise ValueError("Extension form integer exceeds the interoperable range")
    return cast(int | float, value)


def same(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def validate_field_value(field: dict[str, Any], value: Any) -> Any:
    kind = field["kind"]
    if kind == "text":
        _text(value, 16000)
        if not field.get("min_length", 0) <= len(value) <= field.get("max_length", 16000):
            raise ValueError("Extension form text length is outside its limits")
    elif kind == "integer":
        if type(value) is not int:
            raise ValueError("Extension form integer must not be coerced")
        _number(value)
    elif kind == "number":
        _number(value)
    elif kind == "boolean":
        if type(value) is not bool:
            raise ValueError("Extension form boolean must not be coerced")
    elif kind == "multi_choice":
        if (not isinstance(value, list) or not field.get("min_items", 0) <= len(value) <= field.get(
                "max_items", len(field["choices"]))):
            raise ValueError("Extension multi-choice count is outside its limits")
        for index, item in enumerate(value):
            if (not any(same(item, choice["value"]) for choice in field["choices"])
                    or any(same(item, previous) for previous in value[:index])):
                raise ValueError("Extension multi-choice contains an unoffered/duplicate typed value")
    elif kind == "choice":
        if not any(same(value, choice["value"]) for choice in field["choices"]):
            raise ValueError("Extension form value is not an offered choice")
    else:
        raise ValueError("Invalid extension form field kind")
    if kind in ("integer", "number"):
        if "minimum" in field and value < field["minimum"] or "maximum" in field and value > field["maximum"]:
            raise ValueError("Extension form number is outside its limits")
    return value


def validate_fields(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        raise ValueError("Extension form requires 1-32 fields")
    fields, seen = [], set()
    for row in value:
        if not isinstance(row, dict) or row.get("kind") not in KINDS:
            raise ValueError("Invalid extension form field")
        if set(row) - {
            "name",
            "kind",
            "label",
            "description",
            "required",
            "default",
            "multiline",
            "min_length",
            "max_length",
            "minimum",
            "maximum",
            "choices",
            "min_items",
            "max_items",
        }:
            raise ValueError("Unsupported extension form field constraint")
        name = row.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name) or name in seen:
            raise ValueError("Invalid/duplicate extension form field name")
        seen.add(name)
        field = dict(row)
        field["label"] = _text(row.get("label", name), 256, empty=False)
        field["description"] = _text(row.get("description", ""), 2000)
        if type(row.get("required", False)) is not bool or type(row.get("multiline", False)) is not bool:
            raise ValueError("Invalid extension form field flags")
        kind = row["kind"]
        if kind != "text" and ("min_length" in row or "max_length" in row or row.get("multiline", False)):
            raise ValueError("Text-only extension form constraint on another field")
        minimum, maximum = row.get("min_length", 0), row.get("max_length", 16000)
        if type(minimum) is not int or type(maximum) is not int or not 0 <= minimum <= maximum <= 16000:
            raise ValueError("Invalid extension form text length limits")
        if kind not in ("integer", "number") and ("minimum" in row or "maximum" in row):
            raise ValueError("Numeric extension form constraint on another field")
        for key in ("minimum", "maximum"):
            if key in row:
                _number(row[key])
        if "minimum" in row and "maximum" in row and row["minimum"] > row["maximum"]:
            raise ValueError("Invalid extension form numeric range")
        if kind in ("choice", "multi_choice"):
            choices = row.get("choices")
            if not isinstance(choices, list) or not 1 <= len(choices) <= 32:
                raise ValueError("Extension form choice requires 1-32 entries")
            normalized: list[dict[str, Any]] = []
            for choice in choices:
                if not isinstance(choice, dict) or set(choice) != {"label", "value"}:
                    raise ValueError("Invalid extension form choice")
                item = choice["value"]
                if type(item) is str:
                    _text(item, 1000)
                elif type(item) in (int, float):
                    _number(item)
                elif item is not None and type(item) is not bool:
                    raise ValueError("Extension form choices must be JSON scalars")
                if any(same(item, previous["value"]) for previous in normalized):
                    raise ValueError("Duplicate typed extension form choice")
                normalized.append({"label": _text(choice["label"], 256, empty=False), "value": item})
            field["choices"] = normalized
        elif "choices" in row:
            raise ValueError("Choices on a non-choice extension form field")
        if kind == "multi_choice":
            lower, upper = row.get("min_items", 0), row.get("max_items", len(field["choices"]))
            if (type(lower) is not int or type(upper) is not int
                    or not 0 <= lower <= upper <= len(field["choices"])):
                raise ValueError("Invalid extension multi-choice count limits")
        elif "min_items" in row or "max_items" in row:
            raise ValueError("Multi-choice count constraints on another field")
        if "default" in field:
            validate_field_value(field, field["default"])
        fields.append(field)
    encoded = json.dumps(fields, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode()) > 128 * 1024:
        raise ValueError("Extension form declaration exceeds its retention limit")
    return cast(list[dict[str, Any]], json.loads(encoded))  # Owned nested snapshot.


def validate_form_answer(dialog: dict[str, Any], answer: Any) -> dict[str, Any]:
    if not isinstance(answer, dict):
        raise ValueError("Extension form answer must be a named object")
    fields = {field["name"]: field for field in dialog["fields"]}
    if answer.keys() - fields.keys() or any(
        field.get("required", False) and name not in answer for name, field in fields.items()
    ):
        raise ValueError("Missing/unknown extension form answer field")
    for name, value in answer.items():
        validate_field_value(fields[name], value)
    encoded = json.dumps(answer, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode()) > 65536:
        raise ValueError("Extension form answer exceeds its retention limit")
    return cast(dict[str, Any], json.loads(encoded))


def multi_choice_form(value: Any) -> bool:
    """Negotiation discriminator, not an approval or a substitute for schema validation."""
    return (isinstance(value, dict) and value.get("kind") == "form"
            and any(isinstance(field, dict) and field.get("kind") == "multi_choice"
                    for field in value.get("fields", [])))
