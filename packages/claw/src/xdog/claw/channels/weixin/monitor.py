"""Long-poll monitor loop for WeChat getUpdates.

Ported from openclaw-weixin src/monitor/monitor.ts.
Polls independently of a serial inbound-message worker, using a bounded FIFO.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from xdog.claw.channels.weixin.api import WeixinApiClient
from xdog.claw.channels.weixin.types import (
    MessageType,
    WeixinMessage,
)

logger = logging.getLogger(__name__)

DEFAULT_LONG_POLL_TIMEOUT_MS = 35_000
MAX_CONSECUTIVE_FAILURES = 3
BACKOFF_DELAY_S = 30.0
RETRY_DELAY_S = 2.0
SESSION_EXPIRED_ERRCODE = -14
SESSION_PAUSE_S = 300.0  # 5 minutes
DEFAULT_MAX_PENDING_MESSAGES = 50


@dataclass(frozen=True)
class MonitorOpts:
    base_url: str
    token: str
    account_id: str
    state_dir: Path
    cancel_event: asyncio.Event
    on_message: Callable[[WeixinMessage], Awaitable[None]]
    api_client: WeixinApiClient | None = None
    long_poll_timeout_ms: int = DEFAULT_LONG_POLL_TIMEOUT_MS
    max_pending_messages: int = DEFAULT_MAX_PENDING_MESSAGES


def _sync_buf_file(state_dir: Path, account_id: str) -> Path:
    return state_dir / "weixin" / "accounts" / f"{account_id}.sync.json"


def _load_get_updates_buf(state_dir: Path, account_id: str) -> str:
    path = _sync_buf_file(state_dir, account_id)
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return str(data.get("get_updates_buf", ""))
    except Exception:
        pass
    return ""


def _save_get_updates_buf(state_dir: Path, account_id: str, buf: str) -> None:
    path = _sync_buf_file(state_dir, account_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"get_updates_buf": buf}), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to save sync buf: %s", exc)


def _is_user_message(msg: WeixinMessage) -> bool:
    """Filter to only process user-sent messages (not bot echoes)."""
    return msg.message_type == MessageType.USER


async def run_monitor(opts: MonitorOpts) -> None:
    """Poll and dispatch independently until cancelled.

    One worker processes messages in order so the channel's active reply
    recipient and typing indicator are not overwritten by later arrivals.
    A full inbox applies backpressure rather than dropping older messages.
    The inbox is in-memory; shutdown cancels processing without draining it.
    """
    if opts.max_pending_messages <= 0:
        raise ValueError("max_pending_messages must be positive")
    if opts.cancel_event.is_set():
        return

    owns_client = opts.api_client is None
    api_client = opts.api_client or WeixinApiClient(opts.base_url, opts.token)
    inbox: asyncio.Queue[WeixinMessage] = asyncio.Queue(maxsize=opts.max_pending_messages)
    tasks = [
        asyncio.create_task(_poll_updates(opts, api_client, inbox), name="weixin-poll"),
        asyncio.create_task(_dispatch_messages(opts, inbox), name="weixin-dispatch"),
        asyncio.create_task(opts.cancel_event.wait(), name="weixin-stop"),
    ]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        # Surface unexpected worker failures instead of leaving a live poller
        # with nobody consuming its inbox.
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if not inbox.empty():
            logger.warning("Monitor stopped with %d pending in-memory messages", inbox.qsize())
        if owns_client:
            await api_client.close()
        logger.info("Monitor stopped")


async def _dispatch_messages(
    opts: MonitorOpts,
    inbox: asyncio.Queue[WeixinMessage],
) -> None:
    """Keep handlers serial while the poller continues receiving messages."""
    while True:
        msg = await inbox.get()
        try:
            await opts.on_message(msg)
        except Exception:
            logger.exception("Error processing message from %s", msg.from_user_id)
        finally:
            inbox.task_done()


async def _poll_updates(
    opts: MonitorOpts,
    api_client: WeixinApiClient,
    inbox: asyncio.Queue[WeixinMessage],
) -> None:
    """Fetch updates and enqueue them, preserving API order and retry behavior."""
    get_updates_buf = _load_get_updates_buf(opts.state_dir, opts.account_id)
    if get_updates_buf:
        logger.info(
            "Monitor resuming from previous sync buf (%d bytes)",
            len(get_updates_buf),
        )
    else:
        logger.info("Monitor starting fresh (no previous sync buf)")

    next_timeout_ms = opts.long_poll_timeout_ms
    consecutive_failures = 0

    try:
        while not opts.cancel_event.is_set():
            try:
                resp = await api_client.get_updates(
                    get_updates_buf=get_updates_buf,
                    timeout_ms=next_timeout_ms,
                )

                # Update poll timeout if server suggests one
                if resp.longpolling_timeout_ms > 0:
                    next_timeout_ms = resp.longpolling_timeout_ms

                # Check for API errors
                is_api_error = (resp.ret != 0) or (resp.errcode != 0)
                if is_api_error:
                    if resp.errcode == SESSION_EXPIRED_ERRCODE or resp.ret == SESSION_EXPIRED_ERRCODE:
                        logger.error(
                            "getUpdates: session expired (errcode=%d), pausing %ds",
                            resp.errcode,
                            SESSION_PAUSE_S,
                        )
                        consecutive_failures = 0
                        await _cancellable_sleep(SESSION_PAUSE_S, opts.cancel_event)
                        continue

                    consecutive_failures += 1
                    logger.error(
                        "getUpdates failed: ret=%d errcode=%d errmsg=%s (%d/%d)",
                        resp.ret,
                        resp.errcode,
                        resp.errmsg,
                        consecutive_failures,
                        MAX_CONSECUTIVE_FAILURES,
                    )
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        logger.error(
                            "getUpdates: %d consecutive failures, backing off %ds",
                            MAX_CONSECUTIVE_FAILURES,
                            BACKOFF_DELAY_S,
                        )
                        consecutive_failures = 0
                        await _cancellable_sleep(BACKOFF_DELAY_S, opts.cancel_event)
                    else:
                        await _cancellable_sleep(RETRY_DELAY_S, opts.cancel_event)
                    continue

                # Success — reset failure counter
                consecutive_failures = 0

                # Enqueue without waiting for agent execution. Only a full
                # inbox pauses polling, bounding memory during message bursts.
                for msg in resp.msgs:
                    if _is_user_message(msg):
                        logger.info(
                            "Inbound message: from=%s types=%s",
                            msg.from_user_id,
                            ",".join(str(i.type) for i in msg.item_list) or "none",
                        )
                        if inbox.full():
                            logger.warning("WeChat inbox full; pausing polling until space is available")
                        await inbox.put(msg)
                        logger.info("Queued WeChat message (pending=%d)", inbox.qsize())

                # Do not advance the cursor past a partially enqueued batch.
                if resp.get_updates_buf:
                    _save_get_updates_buf(
                        opts.state_dir, opts.account_id, resp.get_updates_buf
                    )
                    get_updates_buf = resp.get_updates_buf

            except asyncio.CancelledError:
                logger.info("Monitor cancelled")
                return
            except Exception:
                if opts.cancel_event.is_set():
                    logger.info("Monitor stopped (cancelled)")
                    return

                consecutive_failures += 1
                logger.exception(
                    "getUpdates error (%d/%d)",
                    consecutive_failures,
                    MAX_CONSECUTIVE_FAILURES,
                )
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    consecutive_failures = 0
                    await _cancellable_sleep(BACKOFF_DELAY_S, opts.cancel_event)
                else:
                    await _cancellable_sleep(RETRY_DELAY_S, opts.cancel_event)

        logger.info("Monitor ended")
    finally:
        logger.debug("WeChat poller stopped")


async def _cancellable_sleep(seconds: float, cancel_event: asyncio.Event) -> None:
    """Sleep that can be interrupted by the cancel event."""
    try:
        await asyncio.wait_for(cancel_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass  # normal — sleep completed without cancellation
