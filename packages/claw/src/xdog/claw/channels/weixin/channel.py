"""WeixinChannel — Channel ABC implementation for WeChat messaging.

Bridges the WeChat ilink bot API to the claw orchestrator via
long-poll monitoring (inbound) and sendMessage API (outbound).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from xdog.claw.channels.base import Channel
from xdog.claw.channels.weixin.api import WeixinApiClient
from xdog.claw.channels.weixin.context_tokens import (
    get_context_token,
    persist_context_tokens,
    restore_context_tokens,
    set_context_token,
)
from xdog.claw.channels.weixin.monitor import MonitorOpts, run_monitor
from xdog.claw.channels.weixin.types import (
    MessageItem,
    MessageItemType,
    MessageState,
    MessageType,
    SendMessageReq,
    TextItem,
    WeixinMessage,
)
from xdog.claw.core.types import UserInput

logger = logging.getLogger(__name__)

# Typing indicator constants (matching openclaw-weixin TypingStatus)
TYPING_STATUS_TYPING = 1
TYPING_STATUS_CANCEL = 2
TYPING_KEEPALIVE_INTERVAL_S = 5.0
TYPING_TICKET_CACHE_TTL_S = 24 * 60 * 60  # 24h


def _body_from_item_list(items: tuple[MessageItem, ...]) -> str:
    """Extract text content from a WeixinMessage item_list."""
    for item in items:
        if item.type == MessageItemType.TEXT and item.text_item is not None:
            text = item.text_item.text
            ref = item.ref_msg
            if not ref:
                return text
            # Quoted media — just return current text
            if ref.message_item and ref.message_item.type in (
                MessageItemType.IMAGE,
                MessageItemType.VIDEO,
                MessageItemType.FILE,
                MessageItemType.VOICE,
            ):
                return text
            # Build quoted context
            parts: list[str] = []
            if ref.title:
                parts.append(ref.title)
            if ref.message_item:
                ref_body = _body_from_item_list((ref.message_item,))
                if ref_body:
                    parts.append(ref_body)
            if not parts:
                return text
            return f"[引用: {' | '.join(parts)}]\n{text}"
        # Voice-to-text
        if item.type == MessageItemType.VOICE and item.voice_item and item.voice_item.text:
            return item.voice_item.text
    return ""


def _generate_client_id() -> str:
    return f"claw-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# User ID map persistence (survives restarts so outbound can resolve)
# ---------------------------------------------------------------------------


def _user_id_map_file(state_dir: Path, account_id: str) -> Path:
    return state_dir / "weixin" / "accounts" / f"{account_id}.user-id-map.json"


def _load_user_id_map(state_dir: Path, account_id: str) -> dict[str, str]:
    path = _user_id_map_file(state_dir, account_id)
    try:
        if path.exists():
            raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            return {k: v for k, v in raw.items() if isinstance(v, str)}
    except Exception:
        pass
    return {}


def _save_user_id_map(
    state_dir: Path, account_id: str, mapping: dict[str, str]
) -> None:
    path = _user_id_map_file(state_dir, account_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(mapping), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to save user_id_map: %s", exc)


# ---------------------------------------------------------------------------
# Typing ticket cache — fetched from getConfig, cached per user for 24h
# ---------------------------------------------------------------------------


class _TypingTicketCache:
    """Per-user typing_ticket cache with TTL and retry backoff."""

    def __init__(self, api_client: WeixinApiClient) -> None:
        self._api = api_client
        # {user_id: (ticket, fetched_at)}
        self._cache: dict[str, tuple[str, float]] = {}

    async def get_ticket(
        self, user_id: str, context_token: str = ""
    ) -> str:
        """Return a typing_ticket for the user, fetching if needed."""
        now = time.monotonic()
        cached = self._cache.get(user_id)
        if cached is not None:
            ticket, fetched_at = cached
            if (now - fetched_at) < TYPING_TICKET_CACHE_TTL_S:
                return ticket

        # Fetch fresh
        try:
            resp = await self._api.get_config(user_id, context_token)
            ticket = resp.get("typing_ticket", "")
            if resp.get("ret", -1) == 0 and ticket:
                self._cache[user_id] = (ticket, now)
                logger.debug("Cached typing_ticket for user %s", user_id)
                return str(ticket)
        except Exception:
            logger.debug("Failed to fetch typing_ticket for %s", user_id)

        # Return stale ticket if available
        if cached is not None:
            return cached[0]
        return ""


class _TypingIndicator:
    """Manages the typing indicator lifecycle for a single message processing.

    Start → keepalive every 5s → cancel on completion/error.
    """

    def __init__(
        self, api_client: WeixinApiClient, user_id: str, ticket: str
    ) -> None:
        self._api = api_client
        self._user_id = user_id
        self._ticket = ticket
        self._keepalive_task: asyncio.Task[Any] | None = None

    async def start(self) -> None:
        """Send typing=1 and begin keepalive loop."""
        if not self._ticket:
            return
        await self._send(TYPING_STATUS_TYPING)
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def stop(self) -> None:
        """Cancel keepalive and send typing=2."""
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
            self._keepalive_task = None
        if self._ticket:
            await self._send(TYPING_STATUS_CANCEL)

    async def _keepalive_loop(self) -> None:
        """Resend typing=1 every 5s to keep the indicator alive."""
        try:
            while True:
                await asyncio.sleep(TYPING_KEEPALIVE_INTERVAL_S)
                await self._send(TYPING_STATUS_TYPING)
        except asyncio.CancelledError:
            return

    async def _send(self, status: int) -> None:
        try:
            await self._api.send_typing(
                self._user_id, self._ticket, status=status
            )
        except Exception:
            pass  # non-critical


class WeixinChannel(Channel):
    """WeChat messaging channel using the ilink bot API."""

    def __init__(
        self,
        state_dir: Path,
        account_id: str,
        base_url: str,
        token: str,
        group_id: str = "main",
    ) -> None:
        self._state_dir = state_dir
        self._account_id = account_id
        # The conversation this channel delivers into. A channel is a way to
        # reach an agent, not an agent of its own: deriving the group from the
        # sender gave every peer its own session, memory and persona, which is
        # why the agent introduced itself by its routing key.
        self._group_id = group_id or "main"
        self._base_url = base_url
        self._token = token
        self._cancel_event = asyncio.Event()
        self._monitor_task: asyncio.Task[Any] | None = None
        self._api_client = WeixinApiClient(base_url, token)
        self._typing_tickets = _TypingTicketCache(self._api_client)
        # Reverse map: group_id → real weixin user_id (persisted to disk)
        self._user_id_map: dict[str, str] = {}
        # Active typing indicators: group_id → _TypingIndicator
        self._active_typing: dict[str, _TypingIndicator] = {}

    @property
    def name(self) -> str:
        return "weixin"

    async def connect(self) -> None:
        """Start the monitor long-poll loop as a background task."""
        restore_context_tokens(self._state_dir, self._account_id)
        self._user_id_map = _load_user_id_map(self._state_dir, self._account_id)

        opts = MonitorOpts(
            base_url=self._base_url,
            token=self._token,
            account_id=self._account_id,
            state_dir=self._state_dir,
            cancel_event=self._cancel_event,
            on_message=self._on_inbound,
            api_client=self._api_client,
        )
        self._monitor_task = asyncio.create_task(run_monitor(opts))
        logger.info(
            "WeixinChannel connected: account=%s base_url=%s",
            self._account_id,
            self._base_url,
        )

    async def disconnect(self) -> None:
        """Signal the monitor to stop and await completion."""
        self._cancel_event.set()
        if self._monitor_task is not None:
            try:
                self._monitor_task.cancel()
                await self._monitor_task
            except (asyncio.CancelledError, Exception):
                pass
            self._monitor_task = None
        # Stop the worker first: its handler also cleans up typing in finally.
        # Otherwise it can mutate this dictionary while disconnect iterates it.
        for indicator in self._active_typing.values():
            await indicator.stop()
        self._active_typing.clear()
        # Persist state on shutdown
        persist_context_tokens(self._state_dir, self._account_id)
        _save_user_id_map(self._state_dir, self._account_id, self._user_id_map)
        await self._api_client.close()
        logger.info("WeixinChannel disconnected: account=%s", self._account_id)

    async def send_message(self, group_id: str, text: str) -> None:
        """Send a text message to the WeChat user identified by group_id."""
        if not text:
            return

        # A message may be progress before more tool calls, not the end of the
        # turn. _on_inbound owns typing cleanup when processing actually ends.
        # Resolve the real WeChat user ID
        real_user_id = self._user_id_map.get(group_id)
        if real_user_id is None:
            logger.warning(
                "send_message: cannot resolve user for group %s (no prior inbound message)",
                group_id,
            )
            return

        context_token = get_context_token(self._account_id, real_user_id) or ""

        client_id = _generate_client_id()
        item_list = (
            MessageItem(
                type=MessageItemType.TEXT,
                text_item=TextItem(text=text),
            ),
        )

        req = SendMessageReq(
            msg=WeixinMessage(
                from_user_id="",
                to_user_id=real_user_id,
                client_id=client_id,
                message_type=MessageType.BOT,
                message_state=MessageState.FINISH,
                item_list=item_list,
                context_token=context_token,
            )
        )

        try:
            await self._api_client.send_message(req)
            logger.info(
                "Sent message to %s (group=%s) client_id=%s",
                real_user_id,
                group_id,
                client_id,
            )
        except Exception:
            logger.exception(
                "Failed to send message to %s (group=%s)", real_user_id, group_id
            )

    async def _start_typing(self, group_id: str, user_id: str, context_token: str) -> None:
        """Start the typing indicator for a user."""
        # Stop any existing indicator for this group first
        await self._stop_typing(group_id)

        ticket = await self._typing_tickets.get_ticket(user_id, context_token)
        if not ticket:
            return

        indicator = _TypingIndicator(self._api_client, user_id, ticket)
        self._active_typing[group_id] = indicator
        await indicator.start()
        logger.debug("Typing indicator started for %s", user_id)

    async def _stop_typing(self, group_id: str) -> None:
        """Stop the typing indicator for a group."""
        indicator = self._active_typing.pop(group_id, None)
        if indicator is not None:
            await indicator.stop()
            logger.debug("Typing indicator stopped for group %s", group_id)

    async def _on_inbound(self, msg: WeixinMessage) -> None:
        """Handle an inbound WeixinMessage from the monitor."""
        from_user_id = msg.from_user_id
        if not from_user_id:
            return

        # Store context token
        if msg.context_token:
            set_context_token(self._account_id, from_user_id, msg.context_token)
            # Persist periodically (every inbound message)
            persist_context_tokens(self._state_dir, self._account_id)

        # Extract text content
        body = _body_from_item_list(msg.item_list)
        if not body:
            logger.debug(
                "Skipping non-text message from %s (no extractable body)",
                from_user_id,
            )
            return

        # The bound conversation, not one derived from who is speaking.
        group_id = self._group_id
        # Remember where a reply goes. Updated on every message, not only the
        # first: with one group serving a channel, the reply belongs to whoever
        # just spoke. A change of peer is logged rather than silent — on a
        # personal bot it means someone else reached it, and that is worth
        # seeing in the journal instead of inferring from a stray answer.
        previous = self._user_id_map.get(group_id)
        if previous != from_user_id:
            if previous is not None:
                logger.info(
                    "group %s: replies now go to %s (was %s)",
                    group_id, from_user_id, previous,
                )
            self._user_id_map[group_id] = from_user_id
            _save_user_id_map(self._state_dir, self._account_id, self._user_id_map)

        # Start typing indicator before dispatching to orchestrator
        await self._start_typing(group_id, from_user_id, msg.context_token or "")

        inbound = UserInput(
            group_id=group_id,
            sender=from_user_id,
            content=body,
            timestamp=msg.create_time_ms / 1000.0 if msg.create_time_ms else time.time(),
            channel="weixin",
            metadata={
                "account_id": self._account_id,
                "weixin_user_id": from_user_id,
                "context_token": msg.context_token,
            },
        )

        if hasattr(self, "_on_message"):
            try:
                await self._on_message(inbound)
            finally:
                # Ensure typing is cancelled even if orchestrator errors
                await self._stop_typing(group_id)
