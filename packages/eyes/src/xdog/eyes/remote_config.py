"""Strict external observation definitions; no network or dynamic code discovery."""

from __future__ import annotations

import re
from typing import Literal, TypedDict, cast
from urllib.parse import urlsplit

from xdog.eyes.config_values import boolean, bounded_int, json_pointer, number
from xdog.eyes.errors import ObserverValidationError
from xdog.eyes.loader import _object, _text
from xdog.eyes.models import (
    FeedSourceDef,
    GithubSourceDef,
    HtmlField,
    HttpSourceDef,
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


def selector(value: object, path: str, *, empty: bool = False) -> str:
    import soupsieve

    if value == "" and empty:
        return ""
    text = _text(value, path, maximum=2048)
    try:
        soupsieve.compile(text)
    except soupsieve.SelectorSyntaxError as exc:
        raise ObserverValidationError("Invalid CSS selector", path=path) from exc
    return text


def html_fields(value: object, path: str) -> tuple[HtmlField, ...]:
    if not isinstance(value, dict) or not 1 <= len(value) <= 32:
        raise ObserverValidationError("Expected 1..32 named HTML fields", path=path)
    result = []
    for name, spec in value.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", name):
            raise ObserverValidationError("Invalid field name", path=path)
        row = _object(spec, path + "." + name, {"selector", "text", "attribute"})
        css = selector(row.get("selector", ""), path + "." + name + ".selector", empty=True)
        attribute = row.get("attribute")
        if attribute is not None:
            if (
                "text" in row
                or not isinstance(attribute, str)
                or not re.fullmatch(r"[A-Za-z_:][A-Za-z0-9_:.-]{0,63}", attribute)
            ):
                raise ObserverValidationError("Choose text or one valid attribute", path=path + "." + name)
        elif row.get("text") is not True:
            raise ObserverValidationError("Text fields require text=true", path=path + "." + name)
        result.append(HtmlField(name, css, attribute))
    return tuple(result)


def source(value: object, path: str) -> RemoteSourceDef:
    common = {
        "id",
        "type",
        "bearer_env",
        "poll_interval_seconds",
        "timeout_seconds",
        "max_bytes",
        "max_records",
    }
    is_http = isinstance(value, dict) and value.get("type") == "http"
    is_feed = isinstance(value, dict) and value.get("type") == "feed"
    data = _object(
        value,
        path,
        common
        | (
            {"url", "mode", "pointer", "key_pointer", "items_selector", "fields", "key_field", "allow_empty"}
            if is_http
            else {"url"}
            if is_feed
            else {
                "repository",
                "resource",
                "api_url",
                "max_pages",
                "lookback_seconds",
                "overlap_seconds",
                "include_prerelease",
            }
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
            data.get("poll_interval_seconds", 300 if is_feed else 60),
            path + ".poll_interval_seconds",
            minimum=1,
            maximum=86400,
        ),
        timeout_seconds=number(data.get("timeout_seconds", 10), path + ".timeout_seconds", minimum=0.1, maximum=120),
        max_bytes=bounded_int(data.get("max_bytes", 1048576), path + ".max_bytes", 16 * 1024 * 1024),
        max_records=bounded_int(data.get("max_records", 256), path + ".max_records", 4096),
    )
    if is_feed:
        return FeedSourceDef(**shared, url=url(data.get("url"), path + ".url"))
    if is_http:
        mode = data.get("mode", "json")
        if not isinstance(mode, str) or mode not in {"json", "status", "html"}:
            raise ObserverValidationError("Expected json, status or html mode", path=path + ".mode")
        if mode == "html":
            if "pointer" in data or "key_pointer" in data:
                raise ObserverValidationError("HTML does not accept JSON selection", path=path)
            fields = html_fields(data.get("fields"), path + ".fields")
            key_field = _text(data.get("key_field"), path + ".key_field", maximum=64)
            if key_field not in {item.name for item in fields}:
                raise ObserverValidationError("key_field must name an extracted field", path=path + ".key_field")
            return HttpSourceDef(
                **shared,
                url=url(data.get("url"), path + ".url"),
                mode="html",
                items_selector=selector(data.get("items_selector"), path + ".items_selector"),
                fields=fields,
                key_field=key_field,
                allow_empty=boolean(data.get("allow_empty", False), path + ".allow_empty"),
            )
        if {"items_selector", "fields", "key_field", "allow_empty"} & data.keys():
            raise ObserverValidationError("HTML fields require mode=html", path=path)
        pointer = json_pointer(data.get("pointer", ""), path + ".pointer")
        key = None if data.get("key_pointer") is None else json_pointer(data["key_pointer"], path + ".key_pointer")
        if mode == "status" and (pointer or key is not None):
            raise ObserverValidationError("status mode has no JSON selection", path=path)
        return HttpSourceDef(
            **shared,
            url=url(data.get("url"), path + ".url"),
            mode=cast(Literal["status", "json", "html"], mode),
            pointer=pointer,
            key_pointer=key,
        )
    repo = _text(data.get("repository"), path + ".repository")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or any(x in {".", ".."} for x in repo.split("/")):
        raise ObserverValidationError("Expected owner/repository", path=path + ".repository")
    resource = data.get("resource")
    if not isinstance(resource, str) or resource not in {"issues", "issue_comments", "workflow_runs", "releases"}:
        raise ObserverValidationError(
            "Expected issues, issue_comments, workflow_runs or releases", path=path + ".resource"
        )
    if "include_prerelease" in data and resource != "releases":
        raise ObserverValidationError("include_prerelease is only for releases", path=path)
    api = url(data.get("api_url", "https://api.github.com"), path + ".api_url").rstrip("/")
    if urlsplit(api).query:
        raise ObserverValidationError("API base cannot include query parameters", path=path + ".api_url")
    lookback = number(data.get("lookback_seconds", 86400), path + ".lookback_seconds", minimum=60, maximum=2592000)
    overlap = number(data.get("overlap_seconds", 120), path + ".overlap_seconds", minimum=1, maximum=lookback)
    return GithubSourceDef(
        **shared,
        repository=repo,
        resource=cast(Literal["issues", "issue_comments", "workflow_runs", "releases"], resource),
        api_url=api,
        max_pages=bounded_int(data.get("max_pages", 10), path + ".max_pages", 100),
        lookback_seconds=lookback,
        overlap_seconds=overlap,
        include_prerelease=boolean(data.get("include_prerelease", False), path + ".include_prerelease"),
    )
