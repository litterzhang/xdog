"""Observation-owned remote polling resources and candidate state. No daemon control API."""

from __future__ import annotations

import math
import time
from typing import Any

import httpx
from xdog.eyes.errors import SourceError
from xdog.eyes.models import HttpSourceDef, RemoteSourceDef
from xdog.eyes.sources import github, http
from xdog.eyes.sources.records import PollResult, RecordSnapshot


class RemoteSampler:
    def __init__(self, transport: http.HttpTransport | None = None) -> None:
        self.transport = transport if transport is not None else http.HttpTransport()

    async def poll(self, source: RemoteSourceDef, state: dict[str, Any], *, now: float) -> PollResult:
        started = time.monotonic()
        try:
            result = (
                await http.poll(source, state, self.transport, now=now)
                if isinstance(source, HttpSourceDef)
                else await github.poll(source, state, self.transport, now=now)
            )
        except (
            SourceError,
            httpx.HTTPError,
            TimeoutError,
            ValueError,
            KeyError,
            TypeError,
            IndexError,
            UnicodeError,
            RecursionError,
        ) as exc:
            # Never include URLs, response bodies, headers or library exceptions:
            # transport exceptions may contain credentials or attacker-controlled text.
            error = str(exc) if isinstance(exc, SourceError) else "Remote read/parse failed; previous cursor retained"
            failures = int(state.get("failures", 0)) + 1
            delay = max(source.poll_interval_seconds, min(3600.0, 5 * 2 ** min(failures - 1, 10)))
            result = PollResult(
                RecordSnapshot((), now, healthy=False, complete=False, valid=False, error=error),
                {**state, "failures": failures, "last_status": None},
                delay,
            )
        finished_at = now + math.ceil(time.monotonic() - started)
        state = {
            **result.state,
            "next_poll_at": finished_at + result.delay,
            "error": result.snapshot.error,
            "gap": result.snapshot.gap,
        }
        return PollResult(result.snapshot, state, result.delay)

    async def close(self) -> None:
        await self.transport.close()
