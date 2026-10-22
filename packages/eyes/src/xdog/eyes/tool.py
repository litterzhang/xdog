"""Thin model tool over public CLI subprocesses. No host daemon client."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from xdog.eyes.capability import EyesCapability


class EyesTool:
    name = 'eyes'
    description = (
        'Returns a JSON envelope with actual CLI exit_code/stdout/stderr; stdout is JSON on success. '
        'exit_code != 0 is command failure, never success; inspect stderr and registration status before retrying. '
        'Failure or cancellation does not prove that no registration was durably accepted. '
        'Cancellation only stops this CLI call wait, never shared daemon work or accepted registrations. '
        'Manage Eyes daemon observations from saved Eyes v2 JSON; use the eyes skill for complete examples. '
        'Tool availability ensures the shared daemon is running, without creating an observation. '
        'The caller chooses an explicit stable owner for management. '
        'Write a JSON definition with filesystem, then create with owner/definition_path. '
        'JSON handlers/routes select destinations: they may target any valid ACP endpoint/session, '
        'not only this host or owner. Eyes does not derive owner from an application, session or group. '
        'The daemon delivers directly; Eyes does not inspect Agent execution, replies or chat delivery. '
        'Prefer ACP enqueue when only durable message intake is needed. Normal host tool permissions apply.'
    )
    parameters: dict[str, Any] = {
        'type': 'object', 'required': ['action', 'owner'], 'additionalProperties': False,
        'properties': {
            'action': {
                'type': 'string',
                'enum': ['create', 'list', 'status', 'pause', 'resume', 'cancel'],
                'description': (
                    'Result is {exit_code, stdout, stderr}; exit_code != 0 is failure, not successful admission. '
                    'Every action requires owner. list requires only action/owner. '
                    'create additionally requires definition_path. '
                    'status/pause/resume/cancel additionally require observer_id. '
                    'pause stops collection reversibly; resume explicitly restores a paused source; '
                    'cancel permanently stops this registration, not already accepted messages. '
                    'Omit unrelated fields, including null/empty placeholders.'
                ),
            },
            'owner': {
                'type': 'string', 'minLength': 1,
                'description': (
                    'Caller-chosen stable identity for these JSON registrations. Required, never inferred by Eyes. '
                    'Reuse the exact owner for create/list/status/pause/resume/cancel and CLI --owner. '
                    'Persist your chosen value so another process can manage the same definitions. '
                    'An owner is a management namespace, not authentication or a destination restriction.'
                ),
            },
            'definition_path': {
                'type': 'string', 'minLength': 1,
                'description': (
                    'create only: ALREADY SAVED full Eyes v2 JSON (version/name/sources/detectors/handlers/routes). '
                    'An absolute path to a regular non-symlink file is required. '
                    'Source roots are relative to the JSON file directory. The JSON determines sources, handlers '
                    'and every destination; multiple sources/routes and different target sessions are supported. '
                    'ACP enqueue needs no cwd: the receiver resolves the target directory from the session. '
                    'Explicit handler cwd is an assertion checked against that session, not a relocation request. '
                    'Standard ACP prompt requires handler cwd for load_session. Relative cwd uses the JSON directory. '
                    'File capacity: one MiB. Later file edits do not alter an accepted normalized snapshot.'
                ),
            },
            'observer_id': {
                'type': 'string', 'minLength': 1,
                'description': (
                    'status/pause/resume/cancel only: exact registration_id returned by create/list in this '
                    'daemon owner namespace. Status covers observation/handler delivery only, '
                    'not model completion or user notification. No cancel_notifications action exists.'
                ),
            },
        },
    }

    def __init__(self, binding: EyesCapability) -> None:
        self._binding = binding

    def _check(self, cancel: asyncio.Event | None) -> None:
        if self._binding.closed:
            raise RuntimeError('Eyes tool binding is closed')
        if cancel is not None and cancel.is_set():
            raise asyncio.CancelledError

    async def execute(self, tool_call_id: str, params: dict[str, Any], cancel: asyncio.Event | None = None,
                      on_update: Any = None, ctx: dict[str, Any] | None = None) -> str:
        self._check(cancel)
        fields = {'create': {'definition_path'}, 'list': set(), 'status': {'observer_id'},
                  'pause': {'observer_id'}, 'resume': {'observer_id'}, 'cancel': {'observer_id'}}
        if not isinstance(params, dict) or any(not isinstance(key, str) for key in params):
            raise ValueError('Expected Eyes action parameters')
        action = params.get('action')
        if not isinstance(action, str) or action not in fields:
            raise ValueError('Unknown Eyes action; allowed: ' + ', '.join(fields))
        expected = {'action', 'owner'} | fields[action]
        if set(params) != expected:
            raise ValueError(f'{action} requires {sorted(expected)}; '
                             f'missing={sorted(expected - params.keys())}; '
                             f'unexpected={sorted(params.keys() - expected)}')
        owner = params['owner']
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError('Expected an explicit nonempty owner')
        arguments: tuple[str, ...] = ('--owner=' + owner,)
        if action == 'create':
            path = params['definition_path']
            if not isinstance(path, str) or not path.strip():
                raise ValueError('Expected a definition_path')
            if not Path(path).is_absolute():
                raise ValueError('Eyes definition_path must be absolute')
            arguments += ('start', path, '--delivery', 'auto', '--no-wait')
        elif action == 'list':
            arguments += ('list',)
        else:
            identifier = params['observer_id']
            if not isinstance(identifier, str) or not identifier.strip():
                raise ValueError('Expected an observer ID')
            # A leading dash is data, never another CLI option.
            arguments += (action, '--', identifier)
        result = await self._binding.control.run(arguments, cancel=cancel)
        return json.dumps(result, ensure_ascii=False, allow_nan=False)
