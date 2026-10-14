from __future__ import annotations

import pytest
from xdog.claw.config import ClawConfig, load_config


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
