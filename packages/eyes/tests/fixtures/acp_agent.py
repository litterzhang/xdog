"""Deterministic third-party ACP peer: official SDK only, no XDOG imports."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from acp import Agent, RequestError, run_agent
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    InitializeResponse,
    LoadSessionResponse,
    PermissionOption,
    PromptResponse,
    TextContentBlock,
    ToolCallUpdate,
)

parser = argparse.ArgumentParser()
parser.add_argument("--record", type=Path, required=True)
parser.add_argument("--mode", default="success")
parser.add_argument("--xdog", action="store_true")
parser.add_argument("--gate", type=Path)
options = parser.parse_args()


class Peer(Agent):
    def on_connect(self, conn):
        self.conn = conn
        self.running = 0
        self.info_queries = 0
        self.cancelled = asyncio.Event()
        self.budget = 1
        self.wake = asyncio.Event()

    def record(self, kind, **data):
        with options.record.open("a") as stream:
            stream.write(
                json.dumps(
                    {"kind": kind, **data}, ensure_ascii=True, default=lambda item: item.model_dump(by_alias=True)
                )
                + "\n"
            )

    async def initialize(self, protocol_version, **kwargs):
        self.record("initialize", kwargs=kwargs, pid=os.getpid())
        if options.mode == "stderr":
            os.write(2, b"noise\n" * 32768)
        return InitializeResponse.model_validate(
            {
                "protocolVersion": protocol_version,
                "agentCapabilities": AgentCapabilities(load_session=options.mode != "no-load"),
                **({"_meta": {"xdog": {"extensions": {"session_info": 1, "resume": 1}}}} if options.xdog else {}),
            }
        )

    async def load_session(self, session_id, cwd, mcp_servers=None, **kwargs):
        self.record("load", session_id=session_id, cwd=cwd, mcp_servers=mcp_servers)
        if session_id != "target":
            raise RequestError(-32602, "Unknown session")
        return LoadSessionResponse()

    async def ext_method(self, method, params):
        self.record("extension", method=method, params=params)
        if not options.xdog:
            raise RequestError(-32601, "No XDOG extensions")
        if method == "xdog/session_info":
            self.info_queries += 1
            return {
                "session_id": "target",
                "cwd": str(Path.cwd()),
                "branch_id": "main",
                "generation": "g2" if options.mode == "changed-owner" and self.info_queries > 1 else "g1",
            }
        if method == "xdog/resume":
            approved = options.mode != "deny-resume"
            if approved:
                self.budget = 1
                self.wake.set()
            return {"resumed": approved}
        raise RequestError(-32601, "Unknown extension")

    async def prompt(self, session_id, prompt, **kwargs):
        self.record("received", session_id=session_id)
        if options.xdog:
            while self.budget <= 0 and not self.cancelled.is_set():
                if options.mode == "human-credit" and options.gate.exists():
                    self.budget = 1  # Explicit external human actor, not a model result.
                    break
                try:
                    await asyncio.wait_for(self.wake.wait(), 0.01)
                except TimeoutError:
                    pass
                self.wake.clear()
            if self.cancelled.is_set():
                return PromptResponse(stop_reason="cancelled")
            self.budget -= 1
        self.running += 1
        self.record(
            "prompt",
            session_id=session_id,
            prompt=[item.model_dump(by_alias=True) for item in prompt],
            kwargs=kwargs,
            concurrent=self.running,
        )
        try:
            if options.mode == "disconnect":
                os._exit(0)
            if options.mode == "error":
                raise RequestError(-32000, "Task execution failed")
            if options.mode == "permission":
                response = await self.conn.request_permission(
                    session_id=session_id,
                    tool_call=ToolCallUpdate(tool_call_id="call", title="Write project", kind="edit"),
                    options=[PermissionOption(option_id="allow", name="Allow all", kind="allow_always")],
                )
                self.record("permission", outcome=response.outcome.model_dump(by_alias=True))
                return PromptResponse(stop_reason="cancelled")
            await self.conn.session_update(
                session_id=session_id,
                update=AgentMessageChunk(
                    session_update="agent_message_chunk",
                    content=TextContentBlock(type="text", text="streamed, not finished"),
                ),
            )
            self.record("update")
            if options.mode == "wait":
                while not self.cancelled.is_set() and not options.gate.exists():
                    await asyncio.sleep(0.01)
                if self.cancelled.is_set():
                    return PromptResponse(stop_reason="cancelled")
            await asyncio.sleep(0.02)
            self.record("completed")
            return PromptResponse(stop_reason="refusal" if options.mode == "refusal" else "end_turn")
        finally:
            self.running -= 1

    async def cancel(self, session_id, **kwargs):
        self.record("cancel", session_id=session_id)
        self.cancelled.set()
        self.wake.set()


asyncio.run(run_agent(Peer()))
