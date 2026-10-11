"""Bounded command session/tree catalogs sourced by the host, not extension state."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from typing import Any, cast


def _text(value: Any, maximum: int, *, empty: bool = True) -> str:
    if not isinstance(value, str) or not empty and not value:
        raise ValueError("Invalid command catalog string")
    try:
        if len(value.encode()) > maximum:
            raise ValueError("Command catalog string exceeds its limit")
    except UnicodeError:
        raise ValueError("Invalid command catalog encoding") from None
    return value


def validate_request(request: dict[str, Any]) -> None:
    if request.get("scope") not in ("sessions", "tree"):
        raise ValueError("Command catalog scope must be sessions/tree")
    _text(request.get("query"), 1000)
    revision = _text(request.get("revision", ""), 64)
    if revision and not re.fullmatch("[a-f0-9]{64}", revision):
        raise ValueError("Invalid catalog membership revision")

    if type(request.get("offset")) is not int or not 0 <= request["offset"] <= 1000000:
        raise ValueError("Invalid command catalog offset")
    for key in ("session_id", "branch_id"):
        value = _text(request.get(key), 128)
        if value and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
            raise ValueError("Invalid command catalog target")
    if request["scope"] == "sessions" and (request["session_id"] or request["branch_id"]):
        raise ValueError("Session catalog cannot silently substitute a tree target")


def _entry(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != {"id", "title", "detail", "kind", "session_id", "branch_id", "index", "depth", "active"}
        or value.get("kind") not in ("session", "branch", "checkpoint")
    ):
        raise ValueError("Invalid command catalog entry")
    for key, maximum in (("id", 512), ("title", 2048), ("detail", 2048), ("session_id", 128), ("branch_id", 128)):
        _text(value[key], maximum, empty=key != "id")
    if (
        type(value["index"]) is not int
        or not -1 <= value["index"] <= 1000000
        or type(value["depth"]) is not int
        or not 0 <= value["depth"] <= 1000000
        or type(value["active"]) is not bool
    ):
        raise ValueError("Invalid command catalog entry metadata")
    return dict(value)


def page(rows: Iterable[dict[str, Any]], request: dict[str, Any], owner_session: str) -> dict[str, Any]:
    validate_request(request)
    _text(owner_session, 128, empty=False)
    # Native browsers retain recent-first behavior. Command pages use stable IDs
    # so the command's own durable entries cannot reorder later session pages.
    source: list[dict[str, Any]] = []
    for row in rows:
        if len(source) >= 1000000:
            raise ValueError("Host catalog exceeds its traversal limit")
        source.append(row)
    if request["scope"] == "sessions":
        source.sort(key=lambda row: row["id"])
    revision = hashlib.sha256(
        json.dumps(
            [owner_session, request["scope"], request["session_id"], request["branch_id"], request["query"]],
            ensure_ascii=True,
        ).encode()
    )
    entries: list[dict[str, Any]] = []
    count = total = 0
    query = request["query"].casefold()
    for row in source:
        count += 1
        if count > 1000000:
            raise ValueError("Host catalog exceeds its traversal limit")
        if query in f"{row['id']} {row['title']} {row['detail']}".casefold():
            if request["offset"] <= total < request["offset"] + 50:
                defaults = {"detail": "", "branch_id": "", "index": -1, "depth": 0, "active": False}
                entries.append(_entry({**defaults, **row}))
            identity = [row["id"], row["kind"], row["session_id"], row.get("branch_id", ""), row.get("index", -1)]
            revision.update(json.dumps(identity, ensure_ascii=True, separators=(",", ":")).encode() + b"\n")
            total += 1
    fingerprint = revision.hexdigest()
    if request.get("revision") and request["revision"] != fingerprint:
        raise ValueError("Catalog membership changed; restart at offset zero")
    offset = min(request["offset"], total)
    more = offset + len(entries) < total
    result = {
        "scope": request["scope"],
        "revision": fingerprint,
        "owner_session": owner_session,
        "session_id": (request["session_id"] or owner_session) if request["scope"] == "tree" else "",
        "branch_id": request["branch_id"],
        "query": request["query"],
        "offset": offset,
        "total": total,
        "entries": entries,
        "has_more": more,
        "next_offset": offset + len(entries) if more else None,
    }
    return validate_page(request, result)


def validate_page(request: dict[str, Any], result: Any) -> dict[str, Any]:
    validate_request(request)
    if (
        not isinstance(result, dict)
        or set(result)
        != {
            "scope",
            "revision",
            "owner_session",
            "session_id",
            "branch_id",
            "query",
            "offset",
            "total",
            "entries",
            "has_more",
            "next_offset",
        }
        or result["scope"] != request["scope"]
        or result["query"] != request["query"]
        or result["branch_id"] != request["branch_id"]
    ):
        raise ValueError("Invalid command catalog scope/response")
    fingerprint = _text(result["revision"], 64, empty=False)
    if not re.fullmatch("[a-f0-9]{64}", fingerprint) or request.get("revision") and request["revision"] != fingerprint:
        raise ValueError("Catalog membership revision changed")
    owner = _text(result["owner_session"], 128, empty=False)
    target = _text(result["session_id"], 128)
    expected = (request["session_id"] or owner) if request["scope"] == "tree" else ""
    if target != expected:
        raise ValueError("Command catalog target changed")
    if (
        type(result["total"]) is not int
        or not 0 <= result["total"] <= 1000000
        or type(result["offset"]) is not int
        or result["offset"] != min(request["offset"], result["total"])
        or type(result["has_more"]) is not bool
        or not isinstance(result["entries"], list)
        or len(result["entries"]) > 50
        or result["offset"] + len(result["entries"]) > result["total"]
    ):
        raise ValueError("Invalid command catalog count/offset")
    entries = [_entry(row) for row in result["entries"]]
    if len({row["id"] for row in entries}) != len(entries):
        raise ValueError("Duplicate command catalog entries")
    for row in entries:
        if (
            request["scope"] == "sessions"
            and row["kind"] != "session"
            or request["scope"] == "tree"
            and row["session_id"] != target
        ):
            raise ValueError("Command catalog entry belongs to another scope")
    more = result["offset"] + len(entries) < result["total"]
    next_offset = result["offset"] + len(entries) if more else None
    if (
        result["has_more"] != more
        or result["next_offset"] != next_offset
        or more
        and (not entries or type(result["next_offset"]) is not int)
    ):
        raise ValueError("Invalid command catalog continuation")
    encoded = json.dumps({**result, "entries": entries}, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode()) > 256 * 1024:
        raise ValueError("Command catalog page exceeds its retention limit")
    return cast(dict[str, Any], json.loads(encoded))
