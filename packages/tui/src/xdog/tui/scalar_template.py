"""Inert RFC 6570 scalar-string expansion; never resolves/fetches a URI."""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import quote

_VARIABLE = re.compile(
    r"((?:[A-Za-z0-9_]|%[0-9A-Fa-f]{2})+(?:\.(?:[A-Za-z0-9_]|%[0-9A-Fa-f]{2})+)*)"
    r"(\*|:[1-9][0-9]{0,3})?"
)
_RESERVED = ":/?#[]@!$&'()*+,;="


def _encode(value: str, reserved: bool = False) -> str:
    if not reserved:
        return quote(value, safe="-._~")
    return "".join(
        piece if re.fullmatch(r"%[0-9A-Fa-f]{2}", piece) else quote(piece, safe=_RESERVED)
        for piece in re.split(r"(%[0-9A-Fa-f]{2})", value)
    )


def expand(template: str, values: Mapping[str, str]) -> str:
    if (
        len(template.encode()) > 8192
        or len(values) > 128
        or any(not isinstance(value, str) or len(value.encode()) > 8192 for value in values.values())
    ):
        raise ValueError("URI template input exceeds scalar limits")

    def expression(match: re.Match[str]) -> str:
        text = match[1]
        if text is None:
            raise ValueError("Invalid URI template expression")
        operator = text[0] if text[0] in "+#./;?&" else ""
        text = text[1:] if operator else text
        start, separator = {
            "": ("", ","),
            "+": ("", ","),
            "#": ("#", ","),
            ".": (".", "."),
            "/": ("/", "/"),
            ";": (";", ";"),
            "?": ("?", "&"),
            "&": ("&", "&"),
        }[operator]
        parts = []
        for token in text.split(","):
            variable = _VARIABLE.fullmatch(token)
            if variable is None:
                raise ValueError("Invalid URI template variable")
            name, modifier = variable[1], variable[2] or ""
            if name not in values:
                continue
            value = values[name]
            if modifier.startswith(":"):
                value = value[: int(modifier[1:])]
            encoded = _encode(value, operator in ("+", "#"))
            if operator in (";", "?", "&"):
                encoded = name + ("=" + encoded if encoded or operator != ";" else "")
            parts.append(encoded)
        return start + separator.join(parts) if parts else ""

    result = re.sub(r"\{([^{}]+)\}|[{}]", expression, template)
    if len(result.encode()) > 8192:
        raise ValueError("Expanded URI exceeds its limit")
    return result
