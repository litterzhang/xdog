"""Gateway daemon — Unix socket server for claw runtime.

Manages the Orchestrator lifecycle and accepts TUI client connections
over a Unix domain socket using a JSON-line protocol.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import stat
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from xdog.agent import AgentConfig
from xdog.ai.types import StreamOptions, ThinkingLevel
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group, UserInput

logger = logging.getLogger(__name__)

_HISTORY_FRAME_LIMIT = 512 * 1024


@dataclass(frozen=True, slots=True)
class _ClientRun:
    group_id: str
    task: asyncio.Task[None]
    started: asyncio.Event


def _history_chunks(history: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group projected history into JSON frames below the reader limit."""
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_size = 0
    for entry in history:
        encoded_size = len(json.dumps(entry, ensure_ascii=False).encode("utf-8")) + 1
        if current and current_size + encoded_size > _HISTORY_FRAME_LIMIT:
            chunks.append(current)
            current = []
            current_size = 0
        current.append(entry)
        current_size += encoded_size
    if current:
        chunks.append(current)
    return chunks


_THINKING_LEVELS: tuple[ThinkingLevel, ...] = ("minimal", "low", "medium", "high", "xhigh")


def _as_thinking_level(raw: str | None) -> ThinkingLevel | None:
    """Narrow a configured string to a level the provider accepts.

    Group config comes from a file, so anything can be in there; an unknown
    value used to be forwarded verbatim instead of falling back."""
    for level in _THINKING_LEVELS:
        if raw == level:
            return level
    return None

# Maximum bytes per JSON-line message from client (1 MB)
_MAX_LINE_LENGTH = 1_048_576


def _build_model_and_options(config: ClawConfig) -> tuple[str, Any]:
    """Resolve the global model name and stream_fn from config.

    Returns ``(model_name, stream_fn)``.
    """
    from xdog.agent.helpers import stream_fn_from_provider
    from xdog.claw.core.runtime.group import resolve_model_name

    if not config.model and (not config.groups or any(not group.model_id for group in config.groups)):
        raise ValueError("No primary model configured. Run `xdog-claw onboard` first.")
    model_name = resolve_model_name(config.model)

    # Build stream_fn from ai runtime
    import xdog.ai as ai
    runtime = ai.load()
    sfn = stream_fn_from_provider(runtime) if runtime.active_providers() else None

    return model_name, sfn


def _groups_from_config(config: ClawConfig) -> list[Group]:
    """Convert GroupDef entries from config into Group instances."""
    groups = [
        Group(
            id=gdef.id,
            name=gdef.name,
            is_main=gdef.is_main,
            workspace=gdef.workspace,
            enabled_tools=config.enabled_tools,
            image_model=config.image_model,
            agent_config=AgentConfig(
                model=gdef.model_id,
                options=StreamOptions(
                    thinking=_as_thinking_level(gdef.thinking_level),
                    temperature=gdef.temperature,
                    max_tokens=gdef.max_tokens,
                ),
            ),
        )
        for gdef in config.groups
    ]
    return groups or [Group(
        id="main", name="Claw", is_main=True,
        enabled_tools=config.enabled_tools, image_model=config.image_model,
    )]


class GatewayServer:
    """Unix socket server that bridges TUI clients to the Orchestrator."""

    _SCHEDULER_POLL_INTERVAL = 30  # seconds between tick() calls

    def __init__(self, config: ClawConfig) -> None:
        self._config = config
        self._socket_path = Path(config.socket_path)
        self._pid_path = Path(config.pid_file)
        self._server: asyncio.Server | None = None
        self._orchestrator: Orchestrator | None = None
        self._scheduler_task: asyncio.Task[Any] | None = None
        self._client_tasks: set[asyncio.Task[Any]] = set()
        self._client_counter = 0
        self._group_runs: dict[str, str] = {}
        self._group_runs_lock = asyncio.Lock()
        self._shutdown_event = asyncio.Event()

    async def start(self) -> None:
        """Start the gateway: orchestrator + socket server."""
        logger.info("Starting claw gateway...")

        # Initialize orchestrator
        data_dir = Path(self._config.data_dir)
        model, stream_fn = _build_model_and_options(self._config)
        self._orchestrator = Orchestrator(
            self._config,
            model=model, stream_fn=stream_fn,
            data_dir=data_dir,
        )

        # Register groups
        for group in _groups_from_config(self._config):
            self._orchestrator.register_group(group)

        await self._orchestrator.start()

        # Wire WeChat channel if enabled
        if self._config.weixin_enabled:
            try:
                self._start_weixin_channel()
            except Exception:
                logger.exception(
                    "Failed to start WeChat channel — gateway continues without it"
                )

        # Clean up stale socket
        if self._socket_path.exists():
            self._socket_path.unlink()

        # Ensure parent directory exists
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)

        # Start Unix socket server
        self._server = await asyncio.start_unix_server(
            self._on_client_connect, path=str(self._socket_path)
        )

        # Restrict socket permissions to owner only (0600)
        os.chmod(str(self._socket_path), stat.S_IRUSR | stat.S_IWUSR)

        # Write PID file
        self._write_pid()

        # Start scheduler polling loop
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

        logger.info("Gateway listening on %s (PID: %d)", self._socket_path, os.getpid())

    def _on_client_connect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Wrap client handler in a tracked task for clean shutdown."""
        self._client_counter += 1
        client_id = self._client_counter
        task = asyncio.create_task(self._handle_client(reader, writer, client_id))
        self._client_tasks.add(task)
        task.add_done_callback(self._client_tasks.discard)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
        client_id: int = 0,
    ) -> None:
        """Handle a single TUI client connection."""
        peer = f"client-{client_id}"
        logger.info("Client connected: %s", peer)

        write_lock = asyncio.Lock()
        runs: dict[str, _ClientRun] = {}

        async def write(data: dict[str, Any]) -> None:
            async with write_lock:
                await self._write_response(writer, data)

        try:
            while not reader.at_eof():
                try:
                    line = await reader.readuntil(b"\n")
                except asyncio.LimitOverrunError:
                    await self._write_response(writer, {
                        "type": "error",
                        "message": "Message too large",
                    })
                    # Drain the oversized line
                    await reader.readline()
                    continue
                except asyncio.IncompleteReadError:
                    break

                if not line:
                    break

                try:
                    request = json.loads(line.decode("utf-8").strip())
                except (json.JSONDecodeError, UnicodeDecodeError):
                    await self._write_response(writer, {
                        "type": "error",
                        "message": "Invalid JSON",
                    })
                    continue

                msg_type = request.get("type", "") if isinstance(request, dict) else ""
                if msg_type == "message":
                    group_id = str(request.get("group_id", "main"))
                    if self._orchestrator and group_id not in self._orchestrator.get_group_ids():
                        await write({
                            "type": "error",
                            "message": f"Unknown group: {group_id}",
                        })
                        continue
                    run_id = str(request.get("run_id") or f"run-{time.time_ns()}")
                    request = {**request, "run_id": run_id}
                    content = request.get("content")
                    if not isinstance(content, str) or not content.strip():
                        await write({
                            "type": "error",
                            "message": "Empty message content",
                            "run_id": run_id,
                        })
                        continue
                    if len(run_id) > 128:
                        await write({"type": "error", "message": "Invalid run_id"})
                        continue
                    if runs:
                        await write({
                            "type": "error",
                            "message": "Client already has an active run",
                            "run_id": run_id,
                        })
                        continue
                    async with self._group_runs_lock:
                        busy = (
                            group_id in self._group_runs
                            or not bool(
                                self._orchestrator
                                and self._orchestrator.try_reserve_group(group_id)
                            )
                        )
                        if not busy:
                            self._group_runs[group_id] = run_id
                    if busy:
                        await write({
                            "type": "busy",
                            "group_id": group_id,
                            "run_id": run_id,
                        })
                        continue
                    assert self._orchestrator is not None
                    started = asyncio.Event()
                    task = asyncio.create_task(
                        self._handle_chat_message(
                            request,
                            writer,
                            write=write,
                            started=started,
                        )
                    )
                    group_id = str(request.get("group_id", "main"))
                    runs[run_id] = _ClientRun(
                        group_id=group_id,
                        task=task,
                        started=started,
                    )
                    await started.wait()
                    await write({
                        "type": "run_ack",
                        "group_id": request.get("group_id", "main"),
                        "run_id": run_id,
                    })

                    def _remove_run(_task: asyncio.Task[None], rid: str = run_id, gid: str = group_id) -> None:
                        runs.pop(rid, None)

                        async def _release() -> None:
                            if self._orchestrator:
                                self._orchestrator.release_group_reservation(gid)
                            async with self._group_runs_lock:
                                if self._group_runs.get(gid) == rid:
                                    self._group_runs.pop(gid, None)

                        asyncio.create_task(_release())

                    task.add_done_callback(_remove_run)
                    continue
                if msg_type == "reset":
                    reset_group = str(request.get("group_id", "main"))
                    async with self._group_runs_lock:
                        reset_busy = reset_group in self._group_runs
                    if reset_busy or bool(
                        self._orchestrator
                        and self._orchestrator.is_group_running(reset_group)
                    ):
                        await write({
                            "type": "error",
                            "message": "Cannot reset while a run is active",
                        })
                        continue
                if msg_type == "abort":
                    run_id = str(request.get("run_id", ""))
                    group_id = str(request.get("group_id", "main"))
                    run = runs.get(run_id)
                    requested = bool(
                        run
                        and run.group_id == group_id
                        and self._orchestrator
                        and self._orchestrator.abort_group(run.group_id)
                    )
                    await write({
                        "type": "abort_ack",
                        "group_id": group_id,
                        "run_id": run_id,
                        "status": "requested" if requested else "not_active",
                    })
                    continue
                await self._dispatch(request, writer)

        except (ConnectionResetError, BrokenPipeError):
            logger.info("Client disconnected: %s", peer)
        except Exception:
            logger.exception("Error handling client %s", peer)
        finally:
            for run in tuple(runs.values()):
                if self._orchestrator:
                    self._orchestrator.abort_group(run.group_id)
            if self._orchestrator:
                await asyncio.gather(
                    *(self._orchestrator.wait_group_idle(run.group_id) for run in runs.values()),
                    return_exceptions=True,
                )
            for run in tuple(runs.values()):
                run.task.cancel()
            if runs:
                await asyncio.gather(
                    *(run.task for run in runs.values()),
                    return_exceptions=True,
                )
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.info("Client session ended: %s", peer)

    async def _dispatch(
        self, request: dict[str, Any], writer: asyncio.StreamWriter
    ) -> None:
        """Route a client request to the appropriate handler."""
        msg_type = request.get("type", "")

        if msg_type == "message":
            await self._handle_chat_message(request, writer)
        elif msg_type == "reset":
            await self._handle_reset(request, writer)
        elif msg_type == "status":
            await self._handle_status(writer)
        elif msg_type == "ping":
            group_id = request.get("group_id", "main")
            session_info: dict[str, Any] = {"type": "pong"}
            history: list[dict[str, Any]] = []
            if self._orchestrator:
                session_info.update(self._orchestrator.get_session_info(group_id))
                raw_history = session_info.pop("history", [])
                if isinstance(raw_history, list):
                    history = raw_history
                notifications = self._orchestrator.pop_goal_notifications(group_id)
                if notifications:
                    session_info["goal_notifications"] = [
                        asdict(n) for n in notifications
                    ]
            session_info["history_count"] = len(history)
            await self._write_response(writer, session_info)
            for entries in _history_chunks(history):
                await self._write_response(writer, {
                    "type": "history_chunk",
                    "entries": entries,
                })
            if history:
                await self._write_response(writer, {"type": "history_end"})
        else:
            await self._write_response(writer, {
                "type": "error",
                "message": f"Unknown message type: {msg_type}",
            })

    async def _handle_chat_message(
        self,
        request: dict[str, Any],
        writer: asyncio.StreamWriter,
        *,
        write: Any = None,
        started: asyncio.Event | None = None,
    ) -> None:
        """Process a chat message from the TUI client with streaming deltas."""
        group_id = request.get("group_id", "main")
        content = request.get("content", "")
        run_id = str(request.get("run_id", f"run-{time.time_ns()}"))
        write_response = write or (lambda data: self._write_response(writer, data))

        if not content:
            await write_response({
                "type": "error",
                "message": "Empty message content",
                "run_id": run_id,
            })
            return

        if not self._orchestrator:
            await write_response({
                "type": "error",
                "message": "Orchestrator not initialized",
                "run_id": run_id,
            })
            return

        message = UserInput(
            group_id=group_id,
            content=content,
            sender="user",
            channel="tui",
        )
        if started is not None:
            started.set()

        # Stream presentation-neutral turn events to the TUI client.
        accumulated_text = ""
        streamed_any_text = False
        pending_writes: list[asyncio.Task[None]] = []

        def on_display_event(event: Any) -> None:
            nonlocal accumulated_text, streamed_any_text
            try:
                payload = event.to_wire()
                event_type = payload.get("type")
                if event_type == "assistant_delta":
                    streamed_any_text = True
                    accumulated_text += str(payload.pop("delta", ""))
                    payload.update({
                        "type": "delta",
                        "content": accumulated_text,
                    })
                elif event_type == "tool_call":
                    accumulated_text = ""
                payload.update({"group_id": group_id, "run_id": run_id})
                pending_writes.append(asyncio.create_task(write_response(payload)))
            except (AttributeError, TypeError, ValueError):
                logger.exception("Failed to serialize TUI display event")

        result = await self._orchestrator.route_message(
            message,
            on_display_event=on_display_event,
            reserved=True,
        )
        if pending_writes:
            await asyncio.gather(*pending_writes)

        if result is None:
            await write_response({
                "type": "queued",
                "group_id": group_id,
                "run_id": run_id,
            })
        elif result.error:
            if result.error == "aborted":
                await write_response({
                    "type": "aborted",
                    "group_id": group_id,
                    "run_id": run_id,
                })
            else:
                await write_response({
                    "type": "error",
                    "group_id": group_id,
                    "message": result.error,
                    "run_id": run_id,
                })
        else:
            if streamed_any_text:
                # Deltas already delivered content — send completion
                # signal only to avoid the TUI rendering the text twice.
                resp: dict[str, Any] = {
                    "type": "final",
                    "group_id": group_id,
                    "run_id": run_id,
                }
            else:
                # No streaming happened — send the full content
                resp = {
                    "type": "response",
                    "group_id": group_id,
                    "content": result.response_text or "(no response)",
                    "run_id": run_id,
                }
            if result.usage:
                resp["usage"] = result.usage
            await write_response(resp)

    async def _handle_reset(
        self, request: dict[str, Any], writer: asyncio.StreamWriter
    ) -> None:
        """Reset a group's session."""
        group_id = request.get("group_id", "main")

        if not self._orchestrator:
            await self._write_response(writer, {
                "type": "error",
                "message": "Orchestrator not initialized",
            })
            return

        if self._orchestrator.reset_group(group_id):
            new_session_info: dict[str, Any] = {
                "type": "reset_ack",
                "group_id": group_id,
            }
            sid = self._orchestrator.get_new_session_id(group_id)
            if sid:
                new_session_info["session_id"] = sid
            await self._write_response(writer, new_session_info)
        else:
            await self._write_response(writer, {
                "type": "error",
                "message": f"Unknown group: {group_id}",
            })

    async def _handle_status(self, writer: asyncio.StreamWriter) -> None:
        """Return gateway status."""
        groups = self._orchestrator.get_group_ids() if self._orchestrator else []

        await self._write_response(writer, {
            "type": "status",
            "running": True,
            "pid": os.getpid(),
            "groups": groups,
        })

    async def _write_response(
        self, writer: asyncio.StreamWriter, data: dict[str, Any]
    ) -> None:
        """Write a JSON-line response to the client."""
        try:
            line = json.dumps(data) + "\n"
            writer.write(line.encode("utf-8"))
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass

    def _write_pid(self) -> None:
        """Write current PID to file."""
        self._pid_path.parent.mkdir(parents=True, exist_ok=True)
        self._pid_path.write_text(str(os.getpid()), encoding="utf-8")

    def _remove_pid(self) -> None:
        """Remove PID file."""
        try:
            if self._pid_path.exists():
                self._pid_path.unlink()
        except OSError:
            pass

    async def _scheduler_loop(self) -> None:
        """Periodically call orchestrator.tick() for scheduling and goal runner."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(self._SCHEDULER_POLL_INTERVAL)
                if self._orchestrator is not None and not self._shutdown_event.is_set():
                    await self._orchestrator.tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Scheduler loop error")

    def _start_weixin_channel(self) -> None:
        """Initialize and add the WeChat channel to the orchestrator."""
        from xdog.claw.channels.weixin import WeixinChannel
        from xdog.claw.channels.weixin.auth import (
            DEFAULT_BASE_URL,
            load_account,
        )

        config = self._config
        state_dir = Path(config.data_dir)

        account_id = config.weixin_account_id
        token = config.weixin_token
        base_url = config.weixin_base_url
        group_id = "main"

        # Try loading token from account file if not in config
        if account_id and not token:
            account_data = load_account(state_dir, account_id)
            if account_data:
                token = account_data.token
                base_url = base_url or account_data.base_url
                group_id = account_data.group_id or "main"

        if not token:
            logger.warning(
                "WeChat channel enabled but no token configured. "
                "Run 'xdog-claw channel login --weixin' first."
            )
            return

        base_url = base_url or DEFAULT_BASE_URL

        weixin = WeixinChannel(
            state_dir=state_dir,
            account_id=account_id,
            base_url=base_url,
            token=token,
            group_id=group_id,
        )
        if self._orchestrator is None:
            # Every other use in this file guards; this one did not, so adding
            # the channel before `start()` raised AttributeError instead of
            # saying what was wrong.
            logger.error("Cannot add the WeChat channel: the orchestrator is not running")
            return

        self._orchestrator.add_channel(weixin)
        # Channel was added after orchestrator.start(), so connect manually
        asyncio.get_event_loop().create_task(weixin.connect())
        logger.info(
            "WeChat channel added: account=%s group=%s base_url=%s",
            account_id,
            group_id,
            base_url,
        )

    async def stop(self) -> None:
        """Gracefully shut down the gateway."""
        logger.info("Shutting down gateway...")

        # Signal shutdown first so loops can exit
        self._shutdown_event.set()

        # Cancel scheduler loop
        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass

        # Stop accepting new connections
        if self._server:
            self._server.close()
            await self._server.wait_closed()

        # Cancel all active client handler tasks
        for task in list(self._client_tasks):
            task.cancel()
        if self._client_tasks:
            await asyncio.gather(*self._client_tasks, return_exceptions=True)
            self._client_tasks.clear()

        if self._orchestrator:
            await self._orchestrator.stop()

        # Clean up socket and PID files
        try:
            if self._socket_path.exists():
                self._socket_path.unlink()
        except OSError:
            pass

        self._remove_pid()

        logger.info("Gateway stopped.")

    async def run_forever(self) -> None:
        """Run the gateway until interrupted."""
        await self.start()

        loop = asyncio.get_running_loop()

        def _signal_handler() -> None:
            logger.info("Received shutdown signal")
            asyncio.ensure_future(self.stop())

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _signal_handler)

        await self._shutdown_event.wait()


def read_pid(pid_path: Path) -> int | None:
    """Read PID from file, returning None if invalid or missing."""
    try:
        if not pid_path.exists():
            return None
        pid = int(pid_path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, OSError):
        return None


def run_gateway(config: ClawConfig) -> None:
    """Entry point to run the gateway (blocking)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    server = GatewayServer(config)
    asyncio.run(server.run_forever())
