"""Controlled CI schedules: host acknowledgement is not UI settlement."""
from __future__ import annotations

import asyncio
import json

from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.core.runtime.sampling_protocol import SamplingConnection

from packages.coding.tests.test_mcp_task_hosts import (
    test_native_required_tools_not_selected_refuses_before_model_or_output_review as run_required_tools,
)


async def test_required_tool_refusal_waits_for_delayed_socket_close_event(tmp_path, monkeypatch):
    """Drive the old GitHub failure without sleeps or changing runtime policy.

    Deliver the terminal error to the peer but withhold only the UI close event.
    The input form is already gone; busy must stay true until the actual close
    observation is delivered. The host integration test must wait for that fact.
    """
    held = []
    panels = []
    published = SamplingConnection._publish
    opened = ChatApp._open_mcp

    def defer_closed(connection, binding, view, event, models):
        if event['type'] == 'sampling_closed':
            held.append((connection, binding, view, event, models))
        else:
            published(connection, binding, view, event, models)

    def capture_panel(ui):
        opened(ui)
        panels.append(ui._mcp_panel)

    monkeypatch.setattr(SamplingConnection, '_publish', defer_closed)
    monkeypatch.setattr(ChatApp, '_open_mcp', capture_panel)
    operation = asyncio.create_task(run_required_tools(tmp_path, 'claw'))

    def release():
        while held:
            published(*held.pop(0))

    try:
        async with asyncio.timeout(6):
            while not held:
                if operation.done():
                    operation.result()
                    raise AssertionError('No terminal close event was produced')
                await asyncio.sleep(0)
            # This is the original test's failure window: the MCP result is
            # available while the socket UI still has not received its close.
            log = tmp_path / 'task-frames'
            while not log.exists() or not any(
                row.get('id') == 'retrieve' for row in (json.loads(line) for line in log.read_text().splitlines())
            ):
                await asyncio.sleep(0)
        assert panels[-1].sampling_review is None
        assert panels[-1]._busy
        assert not operation.done(), 'The host test mistook form removal for a settled request'
        release()
        await asyncio.wait_for(operation, 6)
        assert not panels[-1]._busy
    finally:
        release()
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
