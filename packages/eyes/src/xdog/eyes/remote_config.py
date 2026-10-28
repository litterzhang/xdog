"""Strict external observation definitions; no network or dynamic code discovery."""

from __future__ import annotations

import math
import re
from typing import Literal, TypedDict, cast
from urllib.parse import urlsplit

from xdog.eyes.errors import ObserverValidationError
from xdog.eyes.loader import _integer, _object, _text
from xdog.eyes.models import (
    AvailabilityDetectorDef,
    ConditionDetectorDef,
    GithubSourceDef,
    HttpSourceDef,
    Predicate,
    RecordDetectorDef,
    RemoteDetectorDef,
    RemoteSourceDef,
)


class _Options(TypedDict):
    id: str
    bearer_env: str | None
    poll_interval_seconds: float
    timeout_seconds: float
    max_bytes: int
    max_records: int


def url(value: object, path: str) -> str:
    text = _text(value, path, maximum=4096)
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError as exc:
        raise ObserverValidationError("Invalid URL", path=path) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or any(ord(char) < 33 or ord(char) == 127 for char in text)
        or port is not None
        and not 1 <= port <= 65535
    ):
        raise ObserverValidationError(
            "Expected HTTP(S) URL without userinfo, fragment or control characters", path=path
        )
    if re.search(r"(?:^|[?&])(?:access_token|token|api_key|key|password|signature)=", parsed.query, re.I):
        raise ObserverValidationError("Credentials belong in bearer_env, not URL parameters", path=path)
    return text


def json_pointer(value: object, path: str) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 2048 or "\x00" in value:
        raise ObserverValidationError("Expected bounded JSON pointer", path=path)
    if value and not value.startswith("/") or re.search(r"~(?![01])", value):
        raise ObserverValidationError("Expected RFC 6901 JSON pointer", path=path)
    return value


def number(value: object, path: str, *, minimum: float, maximum: float) -> float:
    if (
        not isinstance(value, (float, int))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise ObserverValidationError(f"Expected finite number {minimum}..{maximum}", path=path)
    return float(value)


def bounded_int(value: object, path: str, maximum: int) -> int:
    result = _integer(value, path)
    if result > maximum:
        raise ObserverValidationError(f"Expected integer at most {maximum}", path=path)
    return result


def boolean(value: object, path: str) -> bool:
    if type(value) is not bool:
        raise ObserverValidationError("Expected boolean", path=path)
    return value


def source(value: object, path: str) -> RemoteSourceDef:
    common = {"id", "type", "bearer_env", "poll_interval_seconds", "timeout_seconds", "max_bytes", "max_records"}
    is_http = isinstance(value, dict) and value.get("type") == "http"
    data = _object(
        value,
        path,
        common
        | (
            {"url", "mode", "pointer", "key_pointer"}
            if is_http
            else {"repository", "resource", "api_url", "max_pages", "lookback_seconds", "overlap_seconds"}
        ),
    )
    env = data.get("bearer_env")
    if env is not None and (not isinstance(env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", env)):
        raise ObserverValidationError(
            "Expected environment variable name, never a credential", path=path + ".bearer_env"
        )
    shared: _Options = dict(
        id=_text(data.get("id"), path + ".id"),
        bearer_env=env,
        poll_interval_seconds=number(
            data.get("poll_interval_seconds", 60), path + ".poll_interval_seconds", minimum=1, maximum=86400
        ),
        timeout_seconds=number(data.get("timeout_seconds", 10), path + ".timeout_seconds", minimum=0.1, maximum=120),
        max_bytes=bounded_int(data.get("max_bytes", 1048576), path + ".max_bytes", 16 * 1024 * 1024),
        max_records=bounded_int(data.get("max_records", 256), path + ".max_records", 4096),
    )
    if is_http:
        mode = data.get("mode", "json")
        if not isinstance(mode, str) or mode not in {"json", "status"}:
            raise ObserverValidationError("Expected json or status mode", path=path + ".mode")
        pointer = json_pointer(data.get("pointer", ""), path + ".pointer")
        key = None if data.get("key_pointer") is None else json_pointer(data["key_pointer"], path + ".key_pointer")
        if mode == "status" and (pointer or key is not None):
            raise ObserverValidationError("status mode has no JSON selection", path=path)
        return HttpSourceDef(
            **shared,
            url=url(data.get("url"), path + ".url"),
            mode=cast(Literal["status", "json"], mode),
            pointer=pointer,
            key_pointer=key,
        )
    repo = _text(data.get("repository"), path + ".repository")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or any(x in {".", ".."} for x in repo.split("/")):
        raise ObserverValidationError("Expected owner/repository", path=path + ".repository")
    resource = data.get("resource")
    if not isinstance(resource, str) or resource not in {"issues", "issue_comments", "workflow_runs"}:
        raise ObserverValidationError("Expected issues, issue_comments or workflow_runs", path=path + ".resource")
    api = url(data.get("api_url", "https://api.github.com"), path + ".api_url").rstrip("/")
    if urlsplit(api).query:
        raise ObserverValidationError("API base cannot include query parameters", path=path + ".api_url")
    lookback = number(data.get("lookback_seconds", 86400), path + ".lookback_seconds", minimum=60, maximum=2592000)
    overlap = number(data.get("overlap_seconds", 120), path + ".overlap_seconds", minimum=1, maximum=lookback)
    return GithubSourceDef(
        **shared,
        repository=repo,
        resource=cast(Literal["issues", "issue_comments", "workflow_runs"], resource),
        api_url=api,
        max_pages=bounded_int(data.get("max_pages", 10), path + ".max_pages", 100),
        lookback_seconds=lookback,
        overlap_seconds=overlap,
    )


def detector(value: object, path: str) -> RemoteDetectorDef:
    kind = value.get("type") if isinstance(value, dict) else None
    fields = {
        "record.change": {"removals"},
        "value.condition": {"all"},
        "availability": {"failures", "for_seconds", "initial_abnormal"},
    }
    data = _object(value, path, {"id", "type", "source"} | fields.get(str(kind), set()))
    identifier, source_id = _text(data.get("id"), path + ".id"), _text(data.get("source"), path + ".source")
    if kind == "record.change":
        return RecordDetectorDef(
            id=identifier, source=source_id, removals=boolean(data.get("removals", False), path + ".removals")
        )
    if kind == "availability":
        return AvailabilityDetectorDef(
            id=identifier,
            source=source_id,
            failures=bounded_int(data.get("failures", 3), path + ".failures", 10000),
            for_seconds=number(data.get("for_seconds", 0), path + ".for_seconds", minimum=0, maximum=2592000),
            initial_abnormal=boolean(data.get("initial_abnormal", True), path + ".initial_abnormal"),
        )
    if kind == "value.condition":
        conditions = data.get("all")
        if not isinstance(conditions, list) or not 1 <= len(conditions) <= 16:
            raise ObserverValidationError("Expected 1..16 scalar predicates", path=path + ".all")
        predicates = []
        for index, condition in enumerate(conditions):
            loc = f"{path}.all[{index}]"
            row = _object(condition, loc, {"pointer", "op", "value"})
            op, expected = row.get("op"), row.get("value")
            if not isinstance(op, str) or op not in {"eq", "ne", "gt", "ge", "lt", "le"} or "value" not in row:
                raise ObserverValidationError("Expected supported op and scalar value", path=loc)
            if (
                not isinstance(expected, (str, int, float, bool, type(None)))
                or isinstance(expected, float)
                and not math.isfinite(expected)
                or isinstance(expected, str)
                and len(expected.encode()) > 4096
            ):
                raise ObserverValidationError("Expected bounded finite scalar", path=loc + ".value")
            if op not in {"eq", "ne"} and (type(expected) not in {int, float}):
                raise ObserverValidationError("Ordered comparisons require numeric values", path=loc)
            predicates.append(
                Predicate(
                    json_pointer(row.get("pointer", ""), loc + ".pointer"),
                    cast(Literal["eq", "ne", "gt", "ge", "lt", "le"], op),
                    expected,
                )
            )
        return ConditionDetectorDef(id=identifier, source=source_id, all=tuple(predicates))
    raise ObserverValidationError("Unknown detector", path=path)
