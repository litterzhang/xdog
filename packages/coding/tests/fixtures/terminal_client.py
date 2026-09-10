"""Offline provider bootstrap for the real CLI terminal acceptance tests."""
from __future__ import annotations

import asyncio
import sys
import termios
from pathlib import Path

import xdog.ai as ai
from xdog.ai.types import (
    AssistantMessage,
    DoneEvent,
    Model,
    StartEvent,
    TextContent,
    TextDeltaEvent,
    TextDoneEvent,
    TextStartEvent,
    ThinkingContent,
    ToolCall,
    ToolCallDoneEvent,
    ToolCallStartEvent,
)
from xdog.ai.utils.event_stream import EventStream


class FixtureProvider:
    def model(self, _name):
        return Model(id="terminal-fixture", name="terminal-fixture", context_window=200000)

    def models(self):
        return (self.model(""),)

    def stream(self, _model, context, _options):
        async def generate():
            last = context.messages[-1]
            text = str(last.content)
            yield StartEvent(partial=AssistantMessage())
            if "hold" in text and last.role == "user":
                yield TextStartEvent()
                yield TextDeltaEvent(delta="FIXTURE-HOLD", partial=AssistantMessage(
                    content=(TextContent(text="FIXTURE-HOLD"),),
                ))
                await asyncio.sleep(60)
            if "permission" in text and last.role == "user":
                call = ToolCall(id="fixture-call", name="bash", arguments={"command": "printf fixture"})
                yield ToolCallStartEvent(id=call.id, name=call.name)
                yield ToolCallDoneEvent(id=call.id, name=call.name, arguments=call.arguments)
                yield DoneEvent(stop_reason="toolUse", message=AssistantMessage(content=(call,), stop_reason="toolUse"))
            else:
                answer = "FIXTURE-ANSWER"
                yield TextStartEvent()
                yield TextDeltaEvent(delta=answer)
                yield TextDoneEvent(text=answer)
                content = (TextContent(text=answer),)
                if "reasoning" in text and last.role == "user":
                    content = (ThinkingContent(thinking="FIXTURE-REASONING-DETAIL"), *content)
                yield DoneEvent(message=AssistantMessage(content=content))
        async def events():
            async for event in generate():
                if isinstance(event, DoneEvent):
                    stream.set_result(event.message)
                yield event
        stream = EventStream.from_async_generator(events())
        return stream


if __name__ == "__main__":
    before = termios.tcgetattr(0)
    kind, *args = sys.argv[1:]
    try:
        if kind == "coding":
            ai.provider = lambda _name: FixtureProvider()
            from xdog.coding.main import main
            sys.argv = ["xdog-coding", "--model", "terminal-fixture", "--permission-mode", "ask-all"]
            main()
        else:
            from xdog.claw.channels.tui.tui_client import run_tui
            run_tui(args[0])
    finally:
        restored = before == termios.tcgetattr(0)
        Path("termios-result.txt").write_text(str(restored))
        print(f"TERMIOS-RESTORED={restored}", flush=True)
