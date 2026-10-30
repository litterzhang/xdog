"""Public channel account bindings, separate from personas and backend credentials."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from xdog.claw.core.identity.models import label


@dataclass(frozen=True)
class ChannelAccount:
    channel: str
    group_id: str
    account_id: str
    username: str
    revision: str

    def __post_init__(self) -> None:
        for value in (self.channel, self.group_id, self.account_id, self.revision):
            label(value, maximum=256)
        label(self.username, empty=True)

    def public(self) -> dict[str, str]:
        return {'channel': self.channel, 'account_id': self.account_id, 'username': self.username}


def account_path(data_dir: Path, channel: str, group_id: str) -> Path:
    label(channel, maximum=128)
    label(group_id, maximum=256)
    key = hashlib.sha256(json.dumps([channel, group_id]).encode()).hexdigest()
    return data_dir / 'channel-accounts' / (key + '.json')


def load_account(data_dir: Path, channel: str, group_id: str) -> ChannelAccount | None:
    path = account_path(data_dir, channel, group_id)
    try:
        if path.is_symlink() or path.stat().st_size > 8192:
            raise ValueError('Invalid account binding file')
        raw = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    if not isinstance(raw, dict) or set(raw) != {'channel', 'group_id', 'account_id', 'username', 'revision'}:
        raise ValueError('Invalid saved channel account')
    account = ChannelAccount(**raw)
    if account.channel != channel or account.group_id != group_id:
        raise ValueError('Channel account binding scope changed')
    return account


def bind_account(data_dir: Path, channel: str, group_id: str, account_id: str, username: str) -> ChannelAccount:
    """Explicit host login only. A new login rotates the binding even for the same ID."""
    account = ChannelAccount(channel, group_id, account_id, username, uuid.uuid4().hex)
    path = account_path(data_dir, channel, group_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.account-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(asdict(account), stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return account
