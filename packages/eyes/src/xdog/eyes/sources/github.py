"""Finite GitHub resource reads with stable identity, explicit windows and no writes."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode, urlsplit

from xdog.eyes.errors import SourceError
from xdog.eyes.models import GithubSourceDef
from xdog.eyes.sources.http import HttpTransport, Response, backoff, credential, origin, redact, within_path
from xdog.eyes.sources.records import PollResult, Record, RecordSnapshot, canonical, decode


def utc(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def timestamp(value: object) -> float:
    if not isinstance(value, str):
        raise ValueError("Missing resource timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("GitHub timestamp requires a timezone")
    return parsed.timestamp()


def project(row: Any, source: GithubSourceDef, secret: str | None) -> Record | None:
    if not isinstance(row, dict) or type(row.get("id")) is not int:
        raise ValueError("Expected GitHub resource object with integer ID")
    if source.resource == "issues" and "pull_request" in row:
        return None  # The issues API also lists PRs; this source deliberately does not.
    fields = (
        "id",
        "number",
        "title",
        "body",
        "state",
        "updated_at",
        "created_at",
        "html_url",
        "status",
        "conclusion",
        "run_attempt",
        "head_sha",
        "workflow_id",
        "name",
        "issue_url",
    )
    data = {field: row[field] for field in fields if field in row}
    if isinstance(row.get("user"), dict):
        data["author"] = row["user"].get("login")
    if "labels" in row:
        data["labels"] = sorted(
            label["name"] for label in row["labels"] if isinstance(label, dict) and isinstance(label.get("name"), str)
        )
    url = row.get("html_url")
    if (
        not isinstance(url, str)
        or urlsplit(url).username
        or urlsplit(url).password
        or origin(url) not in {origin(source.api_url), ("https", "github.com", 443)}
    ):
        raise ValueError("Missing/invalid GitHub evidence URL")
    attempt = row.get("run_attempt", 1)
    if source.resource == "workflow_runs" and (type(attempt) is not int or attempt < 1):
        raise ValueError("Invalid workflow run attempt")
    key = f"{row['id']}:{attempt}" if source.resource == "workflow_runs" else str(row["id"])
    return Record(key, redact(canonical(data), secret), redact(url, secret), timestamp(row.get("updated_at")))


async def poll(source: GithubSourceDef, state: dict[str, Any], transport: HttpTransport, *, now: float) -> PollResult:
    secret = credential(source)
    last = state.get("last_success")
    if isinstance(last, (float, int)) and (now - last > source.lookback_seconds or last > now):
        return PollResult(
            RecordSnapshot(
                (),
                now,
                healthy=False,
                complete=False,
                valid=False,
                gap=True,
                error="Observation gap/clock regression exceeds reliable window; explicitly rebaseline",
            ),
            {**state, "gap": True},
            source.poll_interval_seconds,
        )
    headers = {"accept": "application/vnd.github+json", "x-github-api-version": "2022-11-28", "user-agent": "xdog-eyes"}
    if secret:
        headers["authorization"] = "Bearer " + secret
    root = source.api_url + "/repos/" + source.repository
    paths = {"issues": "/issues", "issue_comments": "/issues/comments", "workflow_runs": "/actions/runs"}
    floor = (
        max(now - source.lookback_seconds, float(last) - source.overlap_seconds)
        if last is not None
        else (now - source.lookback_seconds)
    )
    # Actions has no updated-since query. Monitor a rolling created window and
    # keep polling known unfinished runs even after they leave that window.
    params: dict[str, str] = {"per_page": "100"}
    if source.resource == "workflow_runs":
        params["created"] = ">=" + utc(now - source.lookback_seconds)
    else:
        params.update(since=utc(floor), sort="updated", direction="asc")
        if source.resource == "issues":
            params["state"] = "all"
    url = root + paths[source.resource] + "?" + urlencode(params)
    records: dict[str, Record] = {}
    active: set[int] = set()
    seen_runs: set[int] = set()
    visited: set[str] = set()
    remaining = source.max_bytes
    pages = 0

    async def read(target: str) -> tuple[Any, dict[str, str], int]:
        nonlocal remaining, pages
        if pages >= source.max_pages or target in visited:
            raise SourceError("GitHub pagination exceeds max_pages or cycles; cursor retained")
        if origin(target) != origin(source.api_url) or not within_path(target, root_path + "/"):
            raise SourceError("GitHub pagination escaped the configured repository")
        if urlsplit(target).username or urlsplit(target).password or urlsplit(target).fragment:
            raise SourceError("Unsafe GitHub pagination URL")
        visited.add(target)
        pages += 1
        response = await transport.get(target, headers, max_bytes=remaining, path_prefix=root_path + "/")
        remaining -= len(response.body)
        if response.status != 200:
            return None, response.headers, response.status
        return decode(response.body), response.headers, response.status

    root_path = urlsplit(root).path

    def failed(status: int, response_headers: dict[str, str]) -> PollResult:
        delay = backoff(Response(status, response_headers, b""), now=now, attempt=int(state.get("failures", 0)))
        return PollResult(
            RecordSnapshot(
                (),
                now,
                healthy=False,
                complete=False,
                valid=False,
                status_code=status,
                error=f"GitHub HTTP status {status}",
            ),
            {**state, "failures": int(state.get("failures", 0)) + 1, "last_status": status},
            max(source.poll_interval_seconds, delay),
        )

    async with asyncio.timeout(source.timeout_seconds):
        while url:
            value, response_headers, status = await read(url)
            if status != 200:
                return failed(status, response_headers)
            if source.resource == "workflow_runs" and isinstance(value, dict) and value.get("total_count", 0) > 1000:
                raise SourceError("GitHub search cap prevents complete collection; narrow the lookback window")
            items = (
                value.get("workflow_runs") if source.resource == "workflow_runs" and isinstance(value, dict) else value
            )
            if not isinstance(items, list):
                raise SourceError("Invalid GitHub collection; cursor retained")
            for row in items:
                record = project(row, source, secret)
                if record is None:
                    continue
                if source.resource == "workflow_runs":
                    seen_runs.add(row["id"])
                    if row.get("status") != "completed":
                        active.add(row["id"])
                records[record.key] = record
                if len(records) > source.max_records:
                    raise SourceError("GitHub max_records exceeded; no partial snapshot committed")
            links = response_headers.get("link", "")
            candidates = re.findall(r'<([^>]+)>\s*;\s*rel=["\']?next["\']?(?:\s*(?:,|;|$))', links)
            if links and not candidates and re.search(r"\bnext\b", links):
                raise SourceError("Malformed GitHub pagination link; cursor retained")
            if len(candidates) > 1:
                raise SourceError("Ambiguous GitHub next-page link")
            url = candidates[0] if candidates else ""
        if source.resource == "workflow_runs":
            for identifier in state.get("active_runs", []):
                if identifier in seen_runs:
                    continue
                value, response_headers, status = await read(root + "/actions/runs/" + str(identifier))
                if status != 200:
                    return failed(status, response_headers)
                record = project(value, source, secret)
                if record is None:
                    raise SourceError("Invalid active run")
                records[record.key] = record
                if value.get("status") != "completed":
                    active.add(identifier)
            if len(records) > source.max_records:
                raise SourceError("GitHub max_records exceeded; cursor retained")
    # Incremental results are not authoritative membership sets. No inferred deletion.
    return PollResult(
        RecordSnapshot(tuple(records.values()), now, complete=False, status_code=200, window_start=floor),
        {"last_success": now, "failures": 0, "active_runs": sorted(active), "last_status": 200},
        source.poll_interval_seconds,
    )
