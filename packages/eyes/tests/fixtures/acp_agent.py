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
        if options.mode == 'inbox-disconnect-once-slow' and options.record.with_suffix('.dropped').exists():
            while not options.gate.exists():
                await asyncio.sleep(0.01)
        if options.mode == "stderr":
            os.write(2, b"noise\n" * 32768)
        extensions = ({"enqueue": 1, "inbox_status": 1} if options.mode.startswith("inbox") else
                      {"session_info": 1, "resume": 1} if options.xdog else {})
        if options.mode == 'inbox-bool-version':
            extensions['enqueue'] = True
        return InitializeResponse.model_validate(
            {
                "protocolVersion": protocol_version,
                "agentCapabilities": AgentCapabilities(load_session=not options.mode.startswith("inbox")
                                                       and options.mode != "no-load"),
                **({"_meta": {"xdog": {"extensions": extensions}}} if extensions else {}),
            }
        )

    async def load_session(self, session_id, cwd, mcp_servers=None, **kwargs):
        self.record("load", session_id=session_id, cwd=cwd, mcp_servers=mcp_servers)
        if options.mode.startswith('inbox'):
            raise RequestError(-32601, 'Offline intake never loads a model/session owner')
        if session_id != "target":
            raise RequestError(-32602, "Unknown session")
        return LoadSessionResponse()

    async def ext_method(self, method, params):
        self.record("extension", method=method, params=params)
        if options.mode.startswith('inbox'):
            return self.inbox(method, params)
        if not options.xdog:
            raise RequestError(-32601, "No XDOG extensions")
        if method == "xdog/session_info":
            self.info_queries += 1
            return {
                "session_id": "target",
                "cwd": str(Path.cwd()),
                "branch_id": "main",
                "generation": "g2" if options.mode == "changed-owner" and self.info_queries > 1 else "g1",
                **({"automatic_budget": options.mode != "headless"} if options.mode != "no-budget-hint" else {}),
            }
        if method == "xdog/resume":
            approved = options.mode != "deny-resume"
            if approved:
                self.budget = 1
                self.wake.set()
            return {"resumed": approved}
        raise RequestError(-32601, "Unknown extension")

    def inbox(self, method, params):
        store = options.record.with_suffix('.inbox-store')
        existing = json.loads(store.read_text()) if store.exists() else {}
        changed = options.gate is not None and options.gate.exists()
        branch = 'other' if options.mode == 'inbox-changed-branch' and changed else 'main'
        namespace = 'store-other' if options.mode == 'inbox-changed-store' and changed else 'store-main'
        if method == 'xdog/inbox_status':
            result = {'session_id': 'wrong' if options.mode == 'inbox-wrong-scope' else 'target',
                      'cwd': '/other' if options.mode == 'inbox-wrong-cwd' else str(Path.cwd()),
                      'branch_id': branch, 'inbox_id': namespace, 'messages': [], 'next_cursor': None}
            if options.mode == 'inbox-no-store':
                result.pop('inbox_id')
            return result
        if method != 'xdog/enqueue':
            raise RequestError(-32601, 'No execution/resume in offline inbox')
        if options.mode == 'inbox-quota':
            raise RequestError(-32000, 'Inbox capacity exceeded')
        key = params['message_id']
        duplicate = key in existing
        if duplicate and existing[key] != params:
            raise RequestError(-32602, 'Inbox message content conflict')
        if not duplicate:
            existing[key] = params
            store.write_text(json.dumps(existing))
            self.record('stored', message_id=key)
        dropped = options.record.with_suffix('.dropped')
        if options.mode.startswith('inbox-disconnect-once') and not dropped.exists():
            dropped.touch()
            os._exit(0)
        return {'session_id': 'wrong' if options.mode == 'inbox-bad-session' else 'target',
                'message_id': 'wrong' if options.mode == 'inbox-bad-id' else key,
                'status': 'duplicate' if duplicate else 'queued',
                'message_state': 'unknown' if options.mode == 'inbox-bad-state' else 'pending_review',
                'branch_id': 'wrong' if options.mode == 'inbox-bad-branch' else branch}

    async def prompt(self, session_id, prompt, **kwargs):
        self.record("received", session_id=session_id)
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
