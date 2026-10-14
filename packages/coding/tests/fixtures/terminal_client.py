"""Offline provider bootstrap for the real CLI terminal acceptance tests."""
from __future__ import annotations

import asyncio
import os
import shlex
import sys
import termios
from pathlib import Path

import httpx
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
    Usage,
)
from xdog.ai.utils.event_stream import EventStream

# Explicit owned desktop-helper substitution: keep production URL guards and
# process polling, but never launch a real browser from terminal acceptance tests.
if os.environ.get('XDOG_FIXTURE_URL_HELPER'):
    import subprocess
    from types import SimpleNamespace

    import xdog.tui.url_open as fixture_url_open
    _url_popen = subprocess.Popen
    def _owned_url_popen(args, **kwargs):
        if args[0] != '/usr/bin/xdg-open':
            raise ValueError('Unexpected URL fixture helper')
        return _url_popen([sys.executable, '-I', os.environ['XDOG_FIXTURE_URL_HELPER'], args[1]], **kwargs)
    fixture_url_open.subprocess = SimpleNamespace(Popen=_owned_url_popen, DEVNULL=subprocess.DEVNULL,
                                                TimeoutExpired=subprocess.TimeoutExpired)

# Owned offline cancellation evidence only. Distinguish explicit editor cancellation
# from the normal shorter callback deadline without changing production cleanup.
if os.environ.get('XDOG_FIXTURE_ARGUMENT_CANCEL_MARKER'):
    from xdog.agent.extensions.runtime import ExtensionRuntime
    _original_arguments = ExtensionRuntime.complete_command
    async def _observed_arguments(self, command, prefix, identity, *, timeout=1, argument_input=None):
        try:
            return await _original_arguments(self, command, prefix, identity, timeout=5, argument_input=argument_input)
        except asyncio.CancelledError:
            Path(os.environ['XDOG_FIXTURE_ARGUMENT_CANCEL_MARKER']).write_text('explicit-cancel-settled')
            raise
    ExtensionRuntime.complete_command = _observed_arguments

# Test-only frontend receipt barrier. Local dismissal/cancel enqueue is NOT a
# completed request: _argument_requests and the feed retain ownership until the
# gateway's original argument_completion_result is processed. A fresh nonce also
# prevents an earlier empty editor snapshot from satisfying a later test phase.
if os.environ.get('XDOG_FIXTURE_ARGUMENT_STATE'):
    import json

    from xdog.claw.channels.tui.tui_app import ChatApp
    _argument_state_path = Path(os.environ['XDOG_FIXTURE_ARGUMENT_STATE'])
    _argument_poll = ChatApp._poll
    _argument_last_state = None
    def _observed_argument_poll(self):
        global _argument_last_state
        changed = _argument_poll(self)
        request_path = _argument_state_path.with_suffix('.request')
        if request_path.exists():
            feed = self._argument_feed
            state = {'nonce': request_path.read_text(), 'connected': self._connection_ready,
                'editor': self._editor.get_text(), 'requests': list(self._argument_requests),
                'inflight': feed._inflight.serial if feed._inflight else None,
                'queued': feed._request.serial if feed._request else None,
                'canceling': feed._canceling.serial if feed._canceling else None}
            if state != _argument_last_state:
                temporary = _argument_state_path.with_suffix('.tmp')
                temporary.write_text(json.dumps(state))
                temporary.replace(_argument_state_path)
                _argument_last_state = state
        return changed
    ChatApp._poll = _observed_argument_poll

    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
    _coding_argument_poll = InteractiveMode._poll
    def _observed_coding_argument_poll(self):
        global _argument_last_state
        changed = _coding_argument_poll(self)
        request_path = _argument_state_path.with_suffix('.request')
        if request_path.exists():
            feed = self._argument_feed
            with self._argument_jobs_lock:
                requests = list(self._argument_jobs)
            client = self._session.extensions._manager._clients.get('argcomplete')
            state = {'nonce': request_path.read_text(), 'connected': client is not None and client.state == 'connected',
                'editor': self._editor.get_text(), 'requests': requests,
                'inflight': feed._inflight.serial if feed._inflight else None,
                'queued': feed._request.serial if feed._request else None,
                'canceling': feed._canceling.serial if feed._canceling else None}
            if state != _argument_last_state:
                temporary = _argument_state_path.with_suffix('.tmp')
                temporary.write_text(json.dumps(state))
                temporary.replace(_argument_state_path)
                _argument_last_state = state
        return changed
    InteractiveMode._poll = _observed_coding_argument_poll

# Exercise the production macOS helper protocol on Linux/private tmux; this is
# not native AppKit/desktop validation. Replace only this module's sys reference.
if os.environ.get("XDOG_FIXTURE_MAC_CLIPBOARD") == "1":
    from types import SimpleNamespace

    import xdog.tui.attachments as clipboard_attachments
    clipboard_attachments.sys = SimpleNamespace(platform="darwin")

# Offline Win32 helper transport only; native Windows desktop/terminal validation
# remains separate. Do not replace process-global sys.platform on this Linux peer.
if os.environ.get("XDOG_FIXTURE_WIN_CLIPBOARD"):
    from types import SimpleNamespace

    import xdog.tui.attachments as clipboard_attachments
    clipboard_attachments.sys = SimpleNamespace(platform="win32",
        executable=os.environ["XDOG_FIXTURE_WIN_CLIPBOARD"])

# Exercise only the owned Windows copy protocol on Linux/private tmux.
if os.environ.get("XDOG_FIXTURE_WIN_COPY"):
    from types import SimpleNamespace

    import xdog.tui.transcript_transfer as clipboard_transfer
    clipboard_transfer.sys = SimpleNamespace(platform="win32", executable=os.environ["XDOG_FIXTURE_WIN_COPY"])


class FixtureProvider:
    def model(self, _name):
        if _name == "fixture/reviewer":
            return Model(id="fixture/reviewer", name="reviewer", context_window=200000, max_tokens=2000,
                         reasoning=True, supported_efforts=("low", "medium"))
        return Model(id="terminal-fixture", name="terminal-fixture", context_window=200000, input=("text", "image"))

    def models(self):
        return (self.model(""),)

    def stream(self, _model, context, _options):
        if os.environ.get("XDOG_FIXTURE_COUNT_MODEL") == "1":
            counter = Path("fixture-model-count")
            value = int(counter.read_text()) if counter.exists() else 0
            counter.write_text(str(value + 1))
        async def generate():
            last = context.messages[-1]
            text = str(last.content)
            yield StartEvent(partial=AssistantMessage())
            from xdog.ai.types import ImageContent
            count = (sum(isinstance(part, ImageContent) for part in last.content)
                     if not isinstance(last.content, str) else 0)
            if count:
                answer = f"IMAGE-RECEIVED-{count}"
                yield TextStartEvent()
                yield TextDeltaEvent(delta=answer)
                yield TextDoneEvent(text=answer)
                yield DoneEvent(message=AssistantMessage(content=(TextContent(text=answer),)))
                return
            if "live-dynamic-tool" in text and last.role == "user":
                external = next(tool for tool in context.tools if tool.name.startswith("ext_toolmock_target_"))
                value = 7 if external.parameters["properties"]["value"]["type"] == "integer" else "first"
                call = ToolCall(id="dynamic", name=external.name, arguments={"value": value})
                yield DoneEvent(stop_reason="toolUse", message=AssistantMessage(content=(call,), stop_reason="toolUse"))
                return
            if "callback-inspection" in text and last.role == "user":
                external = next(tool for tool in context.tools if tool.name.startswith("ext_inspector_"))
                call = ToolCall(id="callback-inspection", name=external.name, arguments={})
                yield DoneEvent(stop_reason="toolUse", message=AssistantMessage(model=_model, content=(call,),
                    usage=Usage(input=11, output=2), stop_reason="toolUse"))
                return
            if "INPUT-HOOK-" in text and last.role == "user":
                answer = "INPUT-FILTERED-BY-FIXTURE"
                yield TextStartEvent()
                yield TextDeltaEvent(delta=answer)
                yield TextDoneEvent(text=answer)
                yield DoneEvent(message=AssistantMessage(content=(TextContent(text=answer),)))
                return
            if "child-nested" in text and last.role == "user":
                external = next(tool for tool in context.tools if tool.name.startswith("ext_nested_"))
                call = ToolCall(id="child-nested", name=external.name, arguments={"mode": "full"})
                yield DoneEvent(stop_reason="toolUse", message=AssistantMessage(content=(call,), stop_reason="toolUse"))
                return
            if "child-custom" in text and last.role == "user":
                external = next(tool for tool in context.tools if tool.name.startswith("ext_custom_"))
                call = ToolCall(id="child-custom", name=external.name,
                                arguments={"mode": "rich" if "child-custom-rich" in text else ""})
                yield DoneEvent(stop_reason="toolUse", message=AssistantMessage(content=(call,), stop_reason="toolUse"))
                return
            if "child-dialog" in text and last.role == "user":
                external = next(tool for tool in context.tools if tool.name.startswith("ext_dialogue_"))
                call = ToolCall(id="child-dialog", name=external.name, arguments={})
                yield DoneEvent(stop_reason="toolUse", message=AssistantMessage(content=(call,), stop_reason="toolUse"))
                return
            if "extension-rich" in text and last.role == "user":
                external = next(tool for tool in context.tools if tool.name.startswith("ext_rich_"))
                arguments = {"value": "error"} if os.environ.get("XDOG_FIXTURE_RICH_ERROR") else {}
                call = ToolCall(id="extension-rich", name=external.name, arguments=arguments)
                yield ToolCallStartEvent(id=call.id, name=call.name)
                yield ToolCallDoneEvent(id=call.id, name=call.name, arguments=call.arguments)
                yield DoneEvent(stop_reason="toolUse", message=AssistantMessage(content=(call,), stop_reason="toolUse"))
                return
            if "extension-call" in text and last.role == "user":
                external = next(tool for tool in context.tools if tool.name.startswith("ext_sample_"))
                call = ToolCall(id="extension-call", name=external.name, arguments={"value": "fixture"})
                yield ToolCallStartEvent(id=call.id, name=call.name)
                yield ToolCallDoneEvent(id=call.id, name=call.name, arguments=call.arguments)
                yield DoneEvent(stop_reason="toolUse", message=AssistantMessage(content=(call,), stop_reason="toolUse"))
                return
            if "plan-build" in text and last.role == "user":
                call = ToolCall(id="plan-build", name="update_plan", arguments={"expected_revision": 1,
                                "steps": [{"description": "Inspect files", "status": "completed"},
                                          {"description": "Implement change", "status": "pending"}]})
                yield ToolCallStartEvent(id=call.id, name=call.name)
                yield ToolCallDoneEvent(id=call.id, name=call.name, arguments=call.arguments)
                yield DoneEvent(stop_reason="toolUse", message=AssistantMessage(content=(call,), stop_reason="toolUse"))
                return
            if "mcp-rich" in text and last.role == "user":
                external = next(tool for tool in context.tools if tool.name.startswith("mcp_peer_"))
                call = ToolCall(id="mcp-rich", name=external.name, arguments={"value": None})
                yield ToolCallStartEvent(id=call.id, name=call.name)
                yield ToolCallDoneEvent(id=call.id, name=call.name, arguments=call.arguments)
                yield DoneEvent(stop_reason="toolUse", message=AssistantMessage(content=(call,), stop_reason="toolUse"))
                return
            if "mcp-call" in text and last.role == "user":
                external = next(tool for tool in context.tools if tool.name.startswith("mcp_fixture_"))
                call = ToolCall(id="mcp-call", name=external.name, arguments={"value": "TERMINAL-MCP-RESULT"})
                yield ToolCallStartEvent(id=call.id, name=call.name)
                yield ToolCallDoneEvent(id=call.id, name=call.name, arguments=call.arguments)
                yield DoneEvent(stop_reason="toolUse", message=AssistantMessage(content=(call,), stop_reason="toolUse"))
                return
            if "review-permission" in text and last.role == "user":
                yield TextStartEvent()
                yield TextDeltaEvent(delta="REVIEW-HOLD", partial=AssistantMessage(
                    content=(TextContent(text="REVIEW-HOLD"),),
                ))
                while not Path("release-review").exists():
                    await asyncio.sleep(0.01)
            if "queue-hold" in text and last.role == "user":
                yield TextStartEvent()
                yield TextDeltaEvent(delta="QUEUE-HOLD", partial=AssistantMessage(
                    content=(TextContent(text="QUEUE-HOLD"),),
                ))
                while not Path("release-queue").exists():
                    await asyncio.sleep(0.01)
                yield DoneEvent(message=AssistantMessage(content=(TextContent(text="BEFORE-INPUT"),)))
                return
            if "hold" in text and last.role == "user":
                yield TextStartEvent()
                yield TextDeltaEvent(delta="FIXTURE-HOLD", partial=AssistantMessage(
                    content=(TextContent(text="FIXTURE-HOLD"),),
                ))
                await asyncio.sleep(60)
            if ("permission" in text or "stress" in text) and last.role == "user":
                command = "printf fixture"
                if "stress" in text:
                    script = (
                        "import time\nfor i in range(120):\n"
                        "    print(f'STRESS-{i}', flush=True)\n    time.sleep(0.03)"
                    )
                    command = shlex.join([sys.executable, "-c", script])
                call = ToolCall(id="fixture-call", name="bash", arguments={"command": command})
                yield ToolCallStartEvent(id=call.id, name=call.name)
                yield ToolCallDoneEvent(id=call.id, name=call.name, arguments=call.arguments)
                yield DoneEvent(stop_reason="toolUse", message=AssistantMessage(content=(call,), stop_reason="toolUse"))
            else:
                answer = (f"FIXTURE-REVIEWER-{_options.thinking}" if _model == "fixture/reviewer" else "FIXTURE-ANSWER")
                if "CALLBACK-INSPECTION" in text:
                    answer = "INSPECTION-TURN-DONE"
                if "EXTENSION-CONTEXT-" in text:
                    answer = "FIXTURE-" + ("CONTEXT-first" if "first" in text else "CONTEXT-second")
                for needle, response in (("steer-new", "STEER-RESPONSE"), ("follow-new", "FOLLOW-RESPONSE"),
                                         ("local-new", "LOCAL-RESPONSE")):
                    if needle in text:
                        answer = response
                if "long-history" in text:
                    answer = "\n".join(f"LONG-HISTORY-{i}" for i in range(100))
                yield TextStartEvent()
                yield TextDeltaEvent(delta=answer)
                yield TextDoneEvent(text=answer)
                content = (TextContent(text=answer),)
                if "reasoning" in text and last.role == "user":
                    content = (ThinkingContent(thinking="FIXTURE-REASONING-DETAIL"), *content)
                yield DoneEvent(message=AssistantMessage(content=content, usage=Usage(input=1234, cache_read=1000)))
        async def events():
            async for event in generate():
                if isinstance(event, DoneEvent):
                    stream.set_result(event.message)
                yield event
        stream = EventStream.from_async_generator(events())
        return stream


def _no_http(*args, **kwargs):
    raise AssertionError("Terminal acceptance fixtures must never make HTTP requests")


if __name__ == "__main__":
    # Fail closed if a future routing change bypasses one of the fake factories.
    if os.environ.get("XDOG_FIXTURE_MCP_HTTP") == "1":
        from types import SimpleNamespace

        import xdog.agent.mcp.http as mcp_http
        # Only MCP gets the real client factory; model bootstrap still fails closed.
        mcp_http.httpx = SimpleNamespace(AsyncClient=httpx.AsyncClient, Limits=httpx.Limits,
                                       HTTPError=httpx.HTTPError, Response=httpx.Response,
                                       TransportError=httpx.TransportError, Headers=httpx.Headers)
    if os.environ.get("XDOG_FIXTURE_OAUTH") == "1":
        import importlib.util
        import json
        from types import SimpleNamespace

        import xdog.agent.mcp.http as mcp_http
        from xdog.agent.mcp.oauth import OAuthOwner
        spec = importlib.util.spec_from_file_location("oauth_peer", Path(__file__).parents[3]
                                                    / "agent/tests/fixtures/oauth_ui_peer.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        peer = module.Peer()
        peer.issue_refresh = os.environ.get("XDOG_FIXTURE_REFRESH") == "1"
        if os.environ.get("XDOG_FIXTURE_CLIENT_AUTH"):
            peer.method = os.environ["XDOG_FIXTURE_CLIENT_AUTH"]
            peer.secret = Path("oauth-client-secret.txt").read_text()
            os.environ["XDOG_TERMINAL_OAUTH_SECRET"] = peer.secret
            if peer.method == 'private_key_jwt':
                import re
                import subprocess
                public = subprocess.run(['openssl', 'pkey', '-text_pub', '-noout'], input=peer.secret.encode(),
                                        capture_output=True, check=True).stdout.decode()
                modulus = int(re.search(r'Modulus:\s*([0-9a-f:\s]+)Exponent:', public)[1].replace(':', '')
                              .replace('\n', '').replace(' ', ''), 16)
                peer.public_rsa = modulus, int(re.search(r'Exponent: ([0-9]+)', public)[1])
        handler = peer.handle
        async def record_registration(request):
            if request.url.path == "/register":
                Path("oauth-registration.json").write_text(json.dumps({"effect": "registration POST"}))
            if request.url.path == "/token" and b"grant_type=refresh_token" in request.content:
                Path("oauth-refresh.json").write_text(json.dumps({"effect": "refresh POST"}))
            return await handler(request)
        peer.transport = httpx.MockTransport(record_registration)
        factory = httpx.AsyncClient
        OAuthOwner.client = lambda self: factory(transport=peer.transport, trust_env=False, follow_redirects=False)
        original_start = OAuthOwner.start
        async def start_oauth(self, receipt):
            result = await original_start(self, receipt)
            peer.launch(result["authorization_url"])
            Path("oauth-launch.json").write_text(json.dumps(result))
            return result
        OAuthOwner.start = start_oauth
        mcp_http.httpx = SimpleNamespace(AsyncClient=lambda **kwargs: factory(transport=peer.transport, **kwargs),
            Limits=httpx.Limits, HTTPError=httpx.HTTPError, Headers=httpx.Headers)
    httpx.AsyncClient = _no_http
    before = termios.tcgetattr(0)
    kind, *args = sys.argv[1:]
    try:
        if kind == "coding":
            ai.provider = lambda _name: FixtureProvider()
            ai.load = lambda: FixtureProvider()
            from xdog.coding.main import main
            from xdog.coding.modes.interactive import interactive_mode
            if os.environ.get("XDOG_FIXTURE_SESSIONS") == "1":
                from xdog.ai.types import UserMessage
                from xdog.coding.core.sdk import CreateSessionOptions, create_agent_session
                target = create_agent_session(CreateSessionOptions(
                    working_dir=Path.cwd(), model_name="terminal-fixture",
                )).session
                target.agent.replace_messages([UserMessage(content="NAV-CONTEXT")])
                target.session_data.summary = "NAV-TARGET"
                target._persist()
                target.create_branch(at_index=0, label="NAV-BRANCH")
                target.agent.append_message(UserMessage(content="NAV-LATER"))
                target._persist()
                target.dispose()
            original_command = interactive_mode.execute_command

            async def command(cmd, args, session):
                if cmd == "fixture-command":
                    import logging
                    logging.getLogger("xdog.fixture").warning("FIXTURE-COMMAND-WARNING")
                    await asyncio.sleep(60)
                return await original_command(cmd, args, session)

            interactive_mode.execute_command = command
            sys.argv = ["xdog-coding", *(args or ["--model", "terminal-fixture", "--permission-mode", "ask-all"])]
            main()
        else:
            from xdog.claw.channels.tui.tui_client import run_tui
            run_tui(args[0])
    finally:
        restored = before == termios.tcgetattr(0)
        Path("termios-result.txt").write_text(str(restored))
        print(f"TERMIOS-RESTORED={restored}", flush=True)
