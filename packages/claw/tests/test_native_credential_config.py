from __future__ import annotations

from unittest.mock import patch

import pytest
from xdog.agent.mcp.oauth_native_store import NativeCredentialStore
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef, load_config
from xdog.claw.core.runtime.gateway import GatewayServer


async def test_real_group_manager_receives_native_backend_and_distinct_group_namespaces(tmp_path):
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'native.sock'), pid_file=str(tmp_path / 'native.pid'),
        mcp_credential_backend='secret-service', groups=(GroupDef(id='main', is_main=True), GroupDef(id='other')))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
               return_value=('offline/model', lambda *args: EventStream.empty(AssistantMessage()))):
        await server.start()
    try:
        runtimes = server._orchestrator._runtimes
        stores = [runtime.mcp.credential_store for runtime in runtimes.values()]
        assert len(stores) == 2 and all(isinstance(store, NativeCredentialStore) for store in stores)
        assert len({store.namespace for store in stores}) == 2
        assert not any(store.directory.exists() for store in stores)
    finally:
        await server.stop()


@pytest.mark.parametrize('value', ['invalid', True, None, 1, []])
def test_invalid_backend_config_never_becomes_default_file_storage(tmp_path, value):
    from xdog.claw.config import ToolConfigError
    with pytest.raises(ToolConfigError, match='no plaintext fallback'):
        ClawConfig(mcp_credential_backend=value)
    path = tmp_path / 'config.yaml'
    import json
    path.write_text(json.dumps({'mcp_credential_backend': value}))
    with pytest.raises(ToolConfigError, match='no plaintext fallback'):
        load_config(path)
