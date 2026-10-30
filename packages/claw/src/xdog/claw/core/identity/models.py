"""Claw-owned identities. Labels describe actors; they never authenticate a caller."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .permissions import IdentityPermissions

Role = Literal['admin', 'user', 'external_user']


def label(value: object, *, empty: bool = False, maximum: int = 1024) -> str:
    if (not isinstance(value, str) or '\x00' in value or len(value.encode()) > maximum
            or not empty and not value.strip()):
        raise ValueError('Identity requires bounded text')
    return value


@dataclass(frozen=True)
class Principal:
    """Stable actor with a group-local role, separate from a transport recipient."""
    id: str
    kind: Literal['human', 'system']
    role: Role | None = None

    def __post_init__(self) -> None:
        label(self.id, maximum=256)
        if (not isinstance(self.kind, str) or self.kind not in {'human', 'system'}
                or self.kind == 'system' and self.role is not None
                or self.kind == 'human' and (not isinstance(self.role, str)
                    or self.role not in {'admin', 'user', 'external_user'})):
            raise ValueError('Human principals need a role; system principals have no human role')

    @property
    def display_role(self) -> str:
        return self.role or self.kind


@dataclass(frozen=True)
class MessageSource:
    """Public-safe source facts, not private tokens or a usable reply address."""
    channel: str
    account_id: str = ''
    sender_id: str = ''
    message_id: str = ''
    thread_id: str = ''
    reply_to: str = ''
    resource_id: str = ''

    def __post_init__(self) -> None:
        label(self.channel, maximum=128)
        for value in (self.account_id, self.sender_id, self.message_id, self.thread_id,
                      self.reply_to, self.resource_id):
            label(value, empty=True)


@dataclass(frozen=True)
class InputIdentity:
    principal: Principal
    source: MessageSource

    def __post_init__(self) -> None:
        if not isinstance(self.principal, Principal) or not isinstance(self.source, MessageSource):
            raise ValueError('Input identity requires typed principal and source')

    @property
    def human_authorized(self) -> bool:
        return self.principal.kind == 'human' and self.principal.role == 'admin'

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: object) -> InputIdentity:
        if not isinstance(raw, dict) or set(raw) != {'principal', 'source'}:
            raise ValueError('Invalid host identity fields')
        actor, source = raw['principal'], raw['source']
        if (not isinstance(actor, dict) or set(actor) != {'id', 'kind', 'role'}
                or not isinstance(source, dict) or set(source) != {
                    'channel', 'account_id', 'sender_id', 'message_id', 'thread_id', 'reply_to', 'resource_id'}):
            raise ValueError('Invalid principal or source fields')
        return cls(Principal(**actor), MessageSource(**source))



@dataclass(frozen=True)
class ExecutionPolicy:
    """A per-input ceiling. It does not own or modify the host tool repository."""
    manage: bool
    tools: frozenset[str] | None

    @classmethod
    def for_identity(cls, identity: InputIdentity, *,
                     permissions: IdentityPermissions | None = None) -> ExecutionPolicy:
        from .permissions import IdentityPermissions
        configured = permissions if permissions is not None else IdentityPermissions.from_config()
        return cls(identity.human_authorized, configured.tools_for(identity))

    def allows_tool(self, name: str) -> bool:
        return self.tools is None or name in self.tools
