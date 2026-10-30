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
from collections.abc import Awaitable, Callable
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xdog.agent import AgentConfig
from xdog.agent.async_work import run_owned, settle_cleanup, wait_owned
from xdog.agent.capabilities import CapabilitySet
from xdog.ai.types import StreamOptions, ThinkingLevel
from xdog.claw.config import ClawConfig
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.runtime.input_queries import NativeInputReceipts
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.runtime.unix_endpoint import BoundEndpoint
from xdog.claw.core.types import Group

if TYPE_CHECKING:
    from xdog.claw.acp.server import ClawAcpServer
    from xdog.claw.channels.base import Channel
    from xdog.claw.core.inputs.store import InputRecord

logger = logging.getLogger(__name__)

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
            permissions=gdef.permissions,
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


    def __init__(self, config: ClawConfig) -> None:
        self._config = config
        self._socket_path = Path(config.socket_path)
        self._pid_path = Path(config.pid_file)
        self._server: asyncio.Server | None = None
        self._bound_endpoint: BoundEndpoint | None = None
        self._acp_server: ClawAcpServer | None = None
        self._acp_viewers: dict[int, tuple[str, Callable[[dict[str, Any]], Awaitable[None]]]] = {}
        self._orchestrator: Orchestrator | None = None
        self._native_inputs: NativeInputReceipts | None = None
        self._input_unsubscribe: Callable[[], None] | None = None
        self._input_notices: set[asyncio.Task[None]] = set()
        self._application_capabilities: CapabilitySet | None = None
        self._client_tasks: set[asyncio.Task[Any]] = set()
        self._client_counter = 0
        self._shutdown_event = asyncio.Event()
        self._stop_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the gateway: orchestrator + socket server."""
        # Installed providers declare generic host requirements before services open.
        capabilities = CapabilitySet("application")
        group_capabilities = CapabilitySet("group")
        requirements = capabilities.requirements | group_capabilities.requirements
        unsupported = requirements - {"acp"}
        if unsupported:
            raise RuntimeError("Unsupported installed capability requirements: " + ", ".join(sorted(unsupported)))
        if find_spec("acp") is None:
            raise RuntimeError("Claw gateway requires the ACP SDK: install xdog-claw[acp]")
        logger.info("Starting claw gateway...")
        if self._stop_task is not None:
            if not self._stop_task.done():
                raise RuntimeError("Gateway shutdown is still in progress")
            self._stop_task.result()
            self._stop_task = None
            self._shutdown_event.clear()

        # Acquire still-unpublished IO resources on a worker. Actual Agent,
        # consumer and capability owner loops start only after adoption here.
        from xdog.claw.core.runtime.bootstrap import build
        building = asyncio.create_task(asyncio.to_thread(build, self._config))
        try:
            built = await wait_owned(building)
        except BaseException:
            if building.done() and not building.cancelled() and building.exception() is None:
                orphan = building.result().application
                await wait_owned(asyncio.create_task(orphan.stop()))
            raise
        self._orchestrator = built.application
        self._application_capabilities = self._orchestrator.application_capabilities
        if built.error is not None:
            await wait_owned(asyncio.create_task(self._orchestrator.stop()))
            raise built.error
        data_dir = Path(self._config.data_dir)

        try:
            for runtime in self._orchestrator._runtimes.values():
                current = runtime.get_new_session_id() or ""
                selected = await run_owned(runtime.transcript_store.get_extension_model, current) if current else ""
                if runtime.model.startswith("ext-") or selected:
                    session = await runtime.prepare_session(allow_reset=False)
                    await session.initialize_extensions(require_model=True)
            await self._orchestrator.start()
            inputs = await self._orchestrator.input_service()
            application = self._orchestrator
            def native_scope(group_id: str) -> InputScope | None:
                workspace = application.get_group_workspace(group_id)
                return InputScope(group_id, workspace) if workspace is not None else None
            self._native_inputs = NativeInputReceipts(inputs, native_scope)
            def completed(record: InputRecord) -> None:
                if record.envelope.message.origin == 'acp' and self._acp_viewers:
                    task = asyncio.create_task(self._acp_completed(record))
                    self._input_notices.add(task)
                    task.add_done_callback(self._input_notices.discard)
            self._input_unsubscribe = inputs.subscribe_completed(completed)
            # Await this owned connection before restoring overdue managed work.
            # Keep the existing ability to use TUI when WeChat is unavailable.
            if self._config.weixin_enabled:
                channel = None
                try:
                    channel = await self._start_weixin_channel()
                    if channel is not None:
                        await channel.connect()
                        self._orchestrator.channels_ready()
                except Exception:
                    if channel is not None:
                        await channel.disconnect()
                        self._orchestrator._channels.remove(channel)
                    logger.exception("Failed to connect WeChat channel — gateway continues without it")
            if self._config.xhs_enabled:
                from xdog.claw.channels.xhs import XhsChannel
                from xdog.claw.channels.xhs.api import XhsApi
                cfg = self._config
                if cfg.xhs_group_id not in self._orchestrator.get_group_ids():
                    raise ValueError('XHS target group must be explicitly configured')
                xhs = XhsChannel(Path(cfg.data_dir), cfg.xhs_group_id,
                    XhsApi(cfg.xhs_base_url, token_env=cfg.xhs_token_env), poll_seconds=cfg.xhs_poll_seconds,
                    replies_enabled=cfg.xhs_replies_enabled, publish_enabled=cfg.xhs_publish_enabled)
                self._orchestrator.add_channel(xhs)
                await xhs.connect()
                self._orchestrator.channels_ready()
            from xdog.claw.acp.server import ClawAcpServer
            endpoint = (Path(self._config.acp_socket_path) if self._config.acp_socket_path else
                        data_dir / 'acp' / 'agent.sock')
            self._acp_server = await ClawAcpServer.start(
                                         self._orchestrator,
                                         endpoint,
                                         recover_stale=not bool(self._config.acp_socket_path),
                                     )
            if self._application_capabilities.providers:
                from xdog.agent.capabilities.runtime import ApplicationPort
                await self._application_capabilities.start(ApplicationPort())
                await self._orchestrator.refresh_application_capabilities()
            # Bootstrap holds accepted inputs until installed tools are ready.
            inputs.resume()
        except BaseException as error:
            try:
                await self.stop()
            except BaseException as cleanup:
                raise BaseExceptionGroup("Gateway startup and cleanup failed", [error, cleanup]) from None
            raise

        try:
            from xdog.claw.core.runtime.unix_endpoint import bind, close, listen
            binding = asyncio.create_task(asyncio.to_thread(bind, self._socket_path))
            try:
                bound_endpoint = await wait_owned(binding)
            except BaseException:
                if binding.done() and not binding.cancelled() and binding.exception() is None:
                    await run_owned(close, binding.result())
                raise
            self._bound_endpoint = bound_endpoint
            self._server = await listen(self._on_client_connect, bound_endpoint, limit=_MAX_LINE_LENGTH)

            # Write PID file
            await run_owned(self._write_pid)


            logger.info("Gateway listening on %s (PID: %d)", self._socket_path, os.getpid())
        except BaseException:
            await self.stop()
            raise

    async def _acp_completed(self, record: InputRecord) -> None:
        from xdog.claw.core.runtime.display_events import display_result
        payload = {"type": "acp_input_completed", "group_id": record.envelope.scope.group_id,
                   "session_id": record.session_id,
                   "message_id": record.envelope.message.id, "source": display_result(record.envelope.message.source),
                   "instruction": display_result(record.envelope.message.instruction), "status": record.status,
                   "response": display_result(record.response), "error": display_result(record.error)}
        async def notify(write: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
            try:
                async with asyncio.timeout(2):
                    await write(payload)
            except (OSError, TimeoutError):
                pass  # Display failure cannot change the durable execution result.
        await asyncio.gather(*(notify(write) for group, write in tuple(self._acp_viewers.values())
                               if group == record.envelope.scope.group_id))

    def _on_client_connect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Wrap client handler in a tracked task for clean shutdown."""
        if self._shutdown_event.is_set():
            writer.close()  # An already accepted callback cannot create a new owner during shutdown.
            return
        self._client_counter += 1
        client_id = self._client_counter
        task = asyncio.create_task(self._handle_client(reader, writer, client_id))
        self._client_tasks.add(task)
        task.add_done_callback(self._client_tasks.discard)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                             client_id: int = 0) -> None:
        from xdog.claw.core.runtime.native_connection import NativeConnection
        connection = NativeConnection(self._orchestrator, self._native_inputs,
                                      self._acp_viewers, self._write_response)
        await connection.handle(reader, writer, client_id)


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

    async def _start_weixin_channel(self) -> Channel | None:
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

        # The locally saved QR login binds its verified owner, not every Weixin
        # sender. Explicit per-sender config takes precedence over this default.
        account_data = await run_owned(load_account, state_dir, account_id) if account_id else None
        if account_data is not None:
            group_id = account_data.group_id or 'main'
            if not token:
                token = account_data.token
                base_url = base_url or account_data.base_url

        if not token:
            logger.warning(
                "WeChat channel enabled but no token configured. "
                "Run 'xdog-claw channel login --weixin' first."
            )
            return None

        base_url = base_url or DEFAULT_BASE_URL

        application = self._orchestrator
        if application is None:
            logger.error("Cannot add the WeChat channel: the orchestrator is not running")
            return None
        # Validate pure identity descriptors before acquiring an HTTP client.
        if account_data is not None and account_data.user_id and token == account_data.token:
            from xdog.claw.core.identity import IdentityBinding
            application.identities = application.identities.with_default(IdentityBinding(
                group_id, 'weixin', account_id, account_data.user_id, 'owner', 'admin'))
        weixin = WeixinChannel(
            state_dir=state_dir, account_id=account_id, base_url=base_url,
            token=token, group_id=group_id,
            proactive_recipient=lambda: application.identities.owner_recipient(group_id, 'weixin', account_id),
        )
        application.add_channel(weixin)
        logger.info(
            "WeChat channel added: account=%s group=%s base_url=%s",
            account_id,
            group_id,
            base_url,
        )
        return weixin

    async def stop(self) -> None:
        """Join one cleanup owner; caller cancellation is not resource settlement."""
        self._shutdown_event.set()
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(self._stop(), name="claw-gateway-stop")
        task = self._stop_task
        await wait_owned(task)

    async def _stop(self) -> None:
        if self._orchestrator is not None:
            self._orchestrator.request_stop()
        if self._input_unsubscribe is not None:
            self._input_unsubscribe()
            self._input_unsubscribe = None
        for notice in tuple(self._input_notices):
            notice.cancel()
        await asyncio.gather(*tuple(self._input_notices), return_exceptions=True)
        logger.info("Shutting down gateway...")
        server = self._server
        if server is not None:
            server.close()

        async def capabilities() -> None:
            if self._application_capabilities is not None:
                await self._application_capabilities.aclose()

        async def ingress() -> None:
            if self._acp_server is not None:
                await self._acp_server.close()
                self._acp_server = None

        async def clients() -> None:
            # Retire peers before server.wait_closed, which can await transports.
            while self._client_tasks:
                pending = tuple(self._client_tasks)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                self._client_tasks.difference_update(pending)
            if server is not None:
                await server.wait_closed()

        async def application() -> None:
            if self._orchestrator is not None:
                await self._orchestrator.stop()

        async def endpoint() -> None:
            # Remove only the inode acquired by this startup, never a replacement.
            if self._bound_endpoint is not None:
                from xdog.claw.core.runtime.unix_endpoint import close
                owned, self._bound_endpoint = self._bound_endpoint, None
                await run_owned(close, owned)

        async def pid() -> None:
            await run_owned(self._remove_pid)

        await settle_cleanup(capabilities, ingress, clients, application, endpoint, pid,
                             message="Gateway resource cleanup failed")
        logger.info("Gateway stopped.")

    async def run_forever(self) -> None:
        """Run the gateway until interrupted."""
        await self.start()

        loop = asyncio.get_running_loop()

        def _signal_handler() -> None:
            logger.info("Received shutdown signal")
            self._shutdown_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _signal_handler)

        try:
            await self._shutdown_event.wait()
        finally:
            try:
                # asyncio.run must not cancel the cleanup task while it owns resources.
                await self.stop()
            finally:
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.remove_signal_handler(sig)


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
