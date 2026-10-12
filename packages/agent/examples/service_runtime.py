"""Offline cross-process SDK example: host-owned tools, save/restore, and close.

Run from the workspace root in three separate processes::

    uv run --no-sync python packages/agent/examples/service_runtime.py init /tmp/xdog-demo.json
    uv run --no-sync python packages/agent/examples/service_runtime.py build /tmp/xdog-demo.json --compact
    uv run --no-sync python packages/agent/examples/service_runtime.py repair /tmp/xdog-demo.json

No real provider, credentials, database, or business submission protocol. The
host explicitly saves after the run. Each process supplies a fresh tool callable.
--auto-compact opts into the same built-in Coding strategy; it is off by default.
Use a disposable snapshot path. init refuses to overwrite an existing file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult, CompactionConfig
from xdog.ai.types import AssistantMessage, Context, StreamOptions, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream


async def run_demo(phase: str, path: Path, *, compact: bool = False, auto_compact: bool = False) -> dict[str, Any]:
    if phase == "init" and path.exists():
        raise ValueError("init requires a new snapshot path; existing data was not overwritten")
    source = json.loads(path.read_text()) if phase != "init" else None
    calls = 0
    summaries = 0
    tool_calls = 0

    async def record_note(call_id: str, arguments: dict[str, Any], *args: Any, **kwargs: Any) -> AgentToolResult:
        nonlocal tool_calls
        tool_calls += 1
        # Domain logic would live here. A tool name/result does not request SDK termination.
        return AgentToolResult(
            content=(TextContent(text=f"{arguments['text']}: material reference. " * 160),),
            details={"phase": phase, "recorded": True},
        )

    def offline(model: str, context: Context, options: StreamOptions) -> EventStream[AssistantMessage]:
        nonlocal calls, summaries
        if not context.tools:
            summaries += 1
            message = AssistantMessage(content=(TextContent(text="Prior goals and material references retained."),))
        else:
            calls += 1
            message = (
                AssistantMessage(
                    content=(ToolCall(id=f"{phase}-note", name="record_note", arguments={"text": phase}),),
                    stop_reason="toolUse",
                )
                if calls == 1
                else AssistantMessage(content=(TextContent(text=f"{phase} finished"),))
            )
        return EventStream.empty(message)

    tool = AgentTool(
        name="record_note",
        description="Record a host-owned example note",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        execute=record_note,
    )
    async with Agent(
        offline,
        config=AgentConfig(
            model="offline/example",
            system_prompt="Preserve goals and references.",
            compaction=CompactionConfig(auto=auto_compact, context_window=30000, output_reserve=256),
        ),
        tools=[tool],
    ) as agent:
        if source is not None:
            agent.restore(source, allowed_models=["offline/example"])
            assert calls == 0 and tool_calls == 0  # Restore only changes data.
        async for _ in await agent.prompt(f"Perform the {phase} task using the registered note tool."):
            pass
        if compact:
            result = await agent.compact("Keep goals and material references")
            # result.removed_messages is available for a host archive; no storage ACK is implied.
            assert result is not None
        snapshot = agent.dump()
        # Explicit host file persistence, deliberately outside SDK observation callbacks.
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as output:
                temporary = output.name
                json.dump(snapshot, output, ensure_ascii=False)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
    return {
        "phase": phase,
        "model_calls": calls,
        "tool_calls": tool_calls,
        "summary_calls": summaries,
        "generation": snapshot["compaction"]["generation"],
        "closed": agent.closed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("init", "build", "repair"))
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--auto-compact", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(run_demo(args.phase, args.snapshot, compact=args.compact, auto_compact=args.auto_compact))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
