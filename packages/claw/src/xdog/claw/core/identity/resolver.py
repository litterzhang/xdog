"""Exact host-configured bindings. No inference from display names or message text."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .models import InputIdentity, MessageSource, Principal, Role, label


@dataclass(frozen=True)
class IdentityBinding:
    group_id: str
    channel: str
    account_id: str
    sender_id: str
    principal_id: str
    role: Role

    def __post_init__(self) -> None:
        for value in (self.group_id, self.channel, self.sender_id, self.principal_id):
            label(value, maximum=256)
        label(self.account_id, empty=True)
        Principal(self.principal_id, 'human', self.role)

    @property
    def key(self) -> tuple[str, str, str, str]:
        return self.group_id, self.channel, self.account_id, self.sender_id


class IdentityResolver:
    """Owned by one Claw application. Channel facts must come from its actual adapter."""
    def __init__(self, bindings: tuple[IdentityBinding, ...] = ()) -> None:
        if any(not isinstance(binding, IdentityBinding) for binding in bindings):
            raise ValueError('Identity bindings must be typed')
        self._bindings = {binding.key: binding for binding in bindings}
        if len(self._bindings) != len(bindings):
            raise ValueError('Duplicate channel identity binding')

    @classmethod
    def from_config(cls, raw: object) -> IdentityResolver:
        if not isinstance(raw, (tuple, list)):
            raise ValueError('identity_bindings must be a list')
        bindings = []
        for item in raw:
            if not isinstance(item, dict) or set(item) != {
                    'group_id', 'channel', 'account_id', 'sender_id', 'principal_id', 'role'}:
                raise ValueError('Identity binding requires exact group/channel/account/sender/principal/role fields')
            bindings.append(IdentityBinding(**item))
        return cls(tuple(bindings))

    def channel(self, group_id: str, source: MessageSource) -> InputIdentity:
        label(group_id, maximum=256)
        if not source.sender_id:
            raise ValueError('Channel input requires a stable platform sender')
        binding = self._bindings.get((group_id, source.channel, source.account_id, source.sender_id))
        if binding is not None:
            return InputIdentity(Principal(binding.principal_id, 'human', binding.role), source)
        # Never merge accounts/platforms or grant management to an unknown sender.
        key = json.dumps([source.channel, source.account_id, source.sender_id], ensure_ascii=True)
        identifier = 'external-' + hashlib.sha256(key.encode()).hexdigest()
        return InputIdentity(Principal(identifier, 'human', 'external_user'), source)

    @staticmethod
    def local(source: MessageSource) -> InputIdentity:
        """Only a host-authenticated local control connection may call this entry."""
        return InputIdentity(Principal('owner', 'human', 'admin'), source)

    @staticmethod
    def system(source: MessageSource) -> InputIdentity:
        # Do not let a submitted ACP source label impersonate an installed component.
        return InputIdentity(Principal('system:' + source.channel, 'system'), source)

    def current(self, group_id: str, identity: InputIdentity) -> bool:
        """Revalidate saved channel grants at executor admission, including revocation."""
        if identity.principal.kind == 'system':
            return identity == self.system(identity.source)
        return identity == self.channel(group_id, identity.source)

    def with_default(self, binding: IdentityBinding) -> IdentityResolver:
        """Add a verified login-owner binding without overriding explicit config."""
        return (self if binding.key in self._bindings
                else IdentityResolver((*self._bindings.values(), binding)))

    def owner_recipient(self, group_id: str, channel: str, account_id: str) -> str | None:
        """One verified owner destination, not the last person who spoke."""
        candidates = [binding.sender_id for binding in self._bindings.values()
                      if binding.group_id == group_id and binding.channel == channel
                      and binding.account_id == account_id and binding.principal_id == 'owner'
                      and binding.role == 'admin']
        return candidates[0] if len(candidates) == 1 else None

    def describe(self) -> tuple[dict[str, Any], ...]:
        from dataclasses import asdict
        return tuple(asdict(binding) for binding in self._bindings.values())
