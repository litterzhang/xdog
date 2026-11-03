"""Account storage and QR code login for WeChat.

Ported from openclaw-weixin src/auth/accounts.ts + src/auth/login-qr.ts.
Simplified for claw: filesystem-based credential store with QR login flow.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
DEFAULT_BOT_TYPE = "3"
QR_LONG_POLL_TIMEOUT_S = 35


# ---------------------------------------------------------------------------
# Account data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WeixinAccountData:
    token: str = ""
    base_url: str = ""
    user_id: str = ""
    saved_at: str = ""
    #: The conversation this channel delivers into. A channel is a way to reach
    #: an agent, not an agent of its own -- messages from here join the named
    #: group's session, memory and persona instead of starting a new one.
    group_id: str = "main"


def _accounts_dir(state_dir: Path) -> Path:
    return state_dir / "weixin" / "accounts"


def _account_file(state_dir: Path, account_id: str) -> Path:
    return _accounts_dir(state_dir) / f"{account_id}.json"


def load_account(state_dir: Path, account_id: str) -> WeixinAccountData | None:
    """Load account data by ID from disk."""
    path = _account_file(state_dir, account_id)
    try:
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return WeixinAccountData(
            token=raw.get("token", ""),
            base_url=raw.get("base_url", raw.get("baseUrl", "")),
            group_id=raw.get("group_id") or "main",
            user_id=raw.get("user_id", raw.get("userId", "")),
            saved_at=raw.get("saved_at", raw.get("savedAt", "")),
        )
    except Exception:
        logger.warning("Failed to load account %s", account_id)
        return None


def save_account(
    state_dir: Path, account_id: str, data: WeixinAccountData
) -> None:
    """Save account data to disk with restricted permissions."""
    dir_path = _accounts_dir(state_dir)
    dir_path.mkdir(parents=True, exist_ok=True)

    if not account_id or normalize_account_id(account_id) != account_id:
        raise ValueError('Weixin account ID must be a filesystem-safe saved ID')
    file_path = _account_file(state_dir, account_id)
    fd, temporary = tempfile.mkstemp(dir=dir_path, prefix='.account-', text=True)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(asdict(data), stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, file_path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def normalize_account_id(raw_id: str) -> str:
    """Normalize a raw ilink bot ID to a filesystem-safe key.

    e.g. ``"hex@im.bot"`` → ``"hex-im-bot"``
    """
    return re.sub(r"[^a-zA-Z0-9_-]", "-", raw_id)


# ---------------------------------------------------------------------------
# QR login flow
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QrStartResult:
    qrcode_url: str = ""
    message: str = ""
    session_key: str = ""
    qrcode: str = ""


@dataclass(frozen=True)
class QrWaitResult:
    connected: bool = False
    bot_token: str = ""
    account_id: str = ""
    base_url: str = ""
    user_id: str = ""
    message: str = ""


async def start_qr_login(
    api_base_url: str = DEFAULT_BASE_URL,
    bot_type: str = DEFAULT_BOT_TYPE,
) -> QrStartResult:
    """Fetch a QR code for login from the ilink API."""
    base = api_base_url.rstrip("/") + "/"
    url = f"{base}ilink/bot/get_bot_qrcode?bot_type={bot_type}"
    logger.info("Fetching QR code from: %s", url)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()

        qrcode = data.get("qrcode", "")
        qrcode_url = data.get("qrcode_img_content", "")
        return QrStartResult(
            qrcode_url=qrcode_url,
            qrcode=qrcode,
            message="使用微信扫描以下二维码，以完成连接。",
            session_key=qrcode,
        )
    except Exception as exc:
        logger.error("Failed to start QR login: %s", exc)
        return QrStartResult(message=f"Failed to start login: {exc}")


async def wait_qr_login(
    api_base_url: str,
    qrcode: str,
    timeout_s: float = 480,
    bot_type: str = DEFAULT_BOT_TYPE,
) -> QrWaitResult:
    """Poll the QR code status until confirmed, expired, or timed out."""
    base = api_base_url.rstrip("/") + "/"
    deadline = time.monotonic() + timeout_s

    while time.monotonic() < deadline:
        try:
            url = f"{base}ilink/bot/get_qrcode_status?qrcode={qrcode}"
            async with httpx.AsyncClient(timeout=QR_LONG_POLL_TIMEOUT_S) as client:
                resp = await client.get(
                    url, headers={"iLink-App-ClientVersion": "1"}
                )
                resp.raise_for_status()
                data = resp.json()

            status = data.get("status", "wait")

            if status == "confirmed":
                bot_id = data.get("ilink_bot_id", "")
                if not bot_id:
                    return QrWaitResult(
                        connected=False,
                        message="登录失败：服务器未返回 ilink_bot_id。",
                    )
                return QrWaitResult(
                    connected=True,
                    bot_token=data.get("bot_token", ""),
                    account_id=bot_id,
                    base_url=data.get("baseurl", ""),
                    user_id=data.get("ilink_user_id", ""),
                    message="✅ 与微信连接成功！",
                )

            if status == "expired":
                return QrWaitResult(
                    connected=False,
                    message="二维码已过期，请重新生成。",
                )

            if status == "scaned":
                logger.info("QR code scanned, waiting for confirmation...")

            # "wait" — continue polling

        except httpx.TimeoutException:
            pass  # normal for long-poll
        except Exception as exc:
            logger.error("Error polling QR status: %s", exc)
            return QrWaitResult(
                connected=False, message=f"Login failed: {exc}"
            )

        await asyncio.sleep(1)

    return QrWaitResult(connected=False, message="登录超时，请重试。")
