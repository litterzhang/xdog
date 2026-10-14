"""Actual native /extensions auth and RPC ownership over real offline OAuth worker."""
from __future__ import annotations

import asyncio

import pytest
from xdog.coding.modes.rpc.extensions import RpcExtensions

from packages.agent.tests.test_extension_provider_oauth import PROVIDER
from packages.coding.tests.test_rpc_extensions import close, packet, session


async def until(predicate, ui=None):
    async with asyncio.timeout(8):
        while not predicate():
            if ui is not None:
                ui._poll()
            await asyncio.sleep(.003)


def test_login_command_discovery_is_explicit_extension_provider_auth_not_native_provider_cli():
    from xdog.coding.core.slash_commands import BUILTIN_COMMANDS
    assert 'Extension-provider' in BUILTIN_COMMANDS['login']
    assert 'xdog-ai' in BUILTIN_COMMANDS['login']
    assert 'auth menu' in BUILTIN_COMMANDS['extensions']


@pytest.mark.parametrize('extra', [{'name': 'ext-other-provider'}, {'storage': {'backend': 'secret-service'}}])
async def test_rpc_auth_rejects_ambiguous_provider_name_or_extra_storage_fields_before_admission(tmp_path, extra):
    owner, calls = session(tmp_path, extra=('provideroauth',))
    events = []
    rpc = RpcExtensions(owner, events.append)
    try:
        rpc.negotiate({'extension_provider_auth': 1})
        with pytest.raises(ValueError, match='extra fields'):
            rpc.start({**packet(owner, 'bad', '', action='auth'), 'args': 'login ' + PROVIDER, **extra},
                      turn_running=False)
        assert rpc.task is None and not calls and not owner.extension_dialogs.has_pending
    finally:
        await rpc.aclose()
        await close(owner)
