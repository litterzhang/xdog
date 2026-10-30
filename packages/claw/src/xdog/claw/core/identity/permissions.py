"""Immutable, group-local identity tool ceilings; context remains shared.

Profiles select from actual host tools; they never instantiate tools, authenticate
speakers, grant approvals or replace an executor's existing restrictions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import InputIdentity, label


@dataclass(frozen=True)
class IdentityPermissions:
    profiles: tuple[tuple[str, frozenset[str] | None], ...]
    principals: tuple[tuple[str, str], ...]
    defaults: tuple[tuple[str, str], ...]

    @classmethod
    def from_config(cls, profiles: object = None, principals: object = (),
                    defaults: object = None) -> IdentityPermissions:
        if profiles is None:
            profiles = {'owner': {'tools': ['*']}, 'automation': {'tools': ['*']},
                        'reader': {'tools': []}, 'public': {'tools': []}}
        if defaults is None:
            defaults = {'admin': 'owner', 'system': 'automation', 'user': 'reader', 'external_user': 'public'}
        if not isinstance(profiles, dict) or not profiles:
            raise ValueError('permission_profiles must be a nonempty mapping')
        parsed = []
        for name, value in profiles.items():
            label(name, maximum=128)
            if not isinstance(value, dict) or set(value) != {'tools'}:
                raise ValueError(f'Permission profile {name} requires exactly tools')
            tools = value['tools']
            if not isinstance(tools, (list, tuple)):
                raise ValueError(f'Permission profile {name}.tools must be a list')
            for tool in tools:
                label(tool, maximum=256)
                if tool.strip() != tool:
                    raise ValueError('Tool names cannot contain surrounding whitespace')
            if len(set(tools)) != len(tools) or '*' in tools and len(tools) != 1:
                raise ValueError('Use unique tool names, or ["*"] alone to inherit group tools')
            parsed.append((name, None if tools == ['*'] or tools == ('*',) else frozenset(tools)))
        names = {name for name, _ in parsed}
        if not isinstance(defaults, dict) or set(defaults) != {'admin', 'system', 'user', 'external_user'}:
            raise ValueError('defaults requires admin, system, user and external_user permission profiles')
        if any(not isinstance(value, str) or value not in names for value in defaults.values()):
            raise ValueError('defaults references an unknown permission profile')
        if not isinstance(principals, (list, tuple)):
            raise ValueError('principals must be a list of id/permissions mappings; roles belong to identity bindings')
        grants = []
        for principal in principals:
            if not isinstance(principal, dict) or set(principal) != {'id', 'permissions'}:
                raise ValueError('Each principal requires exactly id and permissions')
            identifier, profile = principal['id'], principal['permissions']
            label(identifier, maximum=256)
            if not isinstance(profile, str) or profile not in names:
                raise ValueError(f'Principal {identifier} references an unknown permission profile')
            grants.append((identifier, profile))
        if len({identifier for identifier, _ in grants}) != len(grants):
            raise ValueError('Duplicate principal permission assignment')
        return cls(tuple(parsed), tuple(grants), tuple(defaults.items()))

    def tools_for(self, identity: InputIdentity) -> frozenset[str] | None:
        name = dict(self.principals).get(identity.principal.id, dict(self.defaults)[identity.principal.display_role])
        return dict(self.profiles)[name]

    def config(self) -> dict[str, Any]:
        return {'permission_profiles': {name: {'tools': ['*'] if tools is None else sorted(tools)}
                                        for name, tools in self.profiles},
                'principals': [{'id': identifier, 'permissions': profile} for identifier, profile in self.principals],
                'defaults': dict(self.defaults)}
