"""Abstract channel interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Awaitable, Callable

from xdog.claw.core.identity import MessageSource
from xdog.claw.core.types import GroupInput, UserInput

OnMessageCallback = Callable[[GroupInput], Awaitable[None]]

class Channel(ABC):
    """Abstract base for messaging channels."""

    _on_accept: OnMessageCallback | None = None

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def send_message(self, group_id: str, text: str) -> None: ...

    def accepts_group(self, group_id: str) -> bool:
        """Whether this channel can route reports to this group."""
        return True

    def bound_reply(self, group_id: str) -> UserInput | None:
        """Capture a currently bound proactive destination for the host only.

        Broadcast eligibility is not a recipient binding. Hosts must freeze the
        returned routing data in a ReplyReference, not expose it in channel lists
        (it may contain private transport context). No input is submitted here.
        """
        return None

    @property
    def reply_mode(self) -> str:
        """Ordinary chats stream complete messages; public posts may require one final reply."""
        return 'stream'

    @property
    def input_roles(self) -> frozenset[str]:
        """The actual adapter's role ceiling, independent of configured mappings."""
        return frozenset({'admin', 'user', 'external_user'})

    def input_source(self, original: UserInput) -> MessageSource:
        """Facts from this actual adapter, never a role claimed by remote content.

        Platform adapters should override for verified thread/reply identifiers.
        Private reply tokens stay in reply_context, not this public projection.
        """
        if original.channel != self.name:
            raise ValueError('Input does not belong to this channel binding')
        return MessageSource(self.name, original.metadata.get('account_id', ''),
                             original.sender, original.metadata.get('channel_message_id', ''))

    def account_profile(self, group_id: str) -> dict[str, str] | None:
        """Public login-account information, never persona or credential state."""
        return None

    def bound_publication(self, group_id: str) -> UserInput | None:
        """Optional account-bound publication destination, never a chat recipient."""
        return None

    async def prepare_publication(self, original: UserInput, payload: dict[str, Any], workspace: Path) -> str:
        raise ValueError('This channel does not support publishing')

    async def send_chunk(self, group_id: str, chunk: str) -> None:
        """Send a single chunk of a longer response.

        Default implementation delegates to :meth:`send_message`.
        Channels that support incremental delivery can override this.
        """
        await self.send_message(group_id, chunk)

    def set_on_message(self, callback: OnMessageCallback) -> None:
        self._on_message = callback

    def set_on_accept(self, callback: OnMessageCallback) -> None:
        """Bind lightweight application commit, independently of result display."""
        self._on_accept = callback

    async def send_reply(self, original: UserInput, text: str) -> None:
        """Reply through the original live channel, not all group channels."""
        await self.send_message(original.group_id, text)

    async def send_output(self, original: UserInput, output_id: str, text: str) -> None:
        """Stable transport correlation where the channel supports it."""
        await self.send_reply(original, text)

    def reply_context(self, original: UserInput) -> dict[str, object]:
        """Select routing metadata, not prompt/images/permission data."""
        return {}

    def accepts_saved_reply(self, original: UserInput) -> bool:
        """Opt in to restoring a persisted destination without a live connection.

        A newly connected TUI must never inherit another connection's replies.
        """
        return False
