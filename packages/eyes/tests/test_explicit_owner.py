"""Caller-selected owner is registration metadata, never inferred from a host."""
import json

import pytest
from xdog.eyes.capability import provider
from xdog.eyes.local_control import daemon_location

from packages.eyes.tests.test_tool import raw_definition


async def test_identical_json_is_owned_by_exact_caller_identity_and_host_is_not_used(tmp_path):
    class NoHostAccess:
        def __getattr__(self, name):
            raise AssertionError('Eyes must not inspect a session/group/application host: ' + name)

    binding = await provider.open(NoHostAccess())
    path = tmp_path / 'eyes.json'
    path.write_text(json.dumps(raw_definition(tmp_path)))

    async def invoke(owner, action, **fields):
        result = json.loads(await binding.tools[0].execute('call', {
            'action': action, 'owner': owner, **fields}))
        if result['exit_code'] != 0:
            raise ValueError(result['stderr'])
        return json.loads(result['stdout'])

    try:
        first = await invoke('any:caller:格式', 'create', definition_path=str(path))
        other = await invoke('independent-owner', 'create', definition_path=str(path))
        assert first['owner'] == 'any:caller:格式'
        assert first['registration_id'] != other['registration_id']
        assert [row['registration_id'] for row in (await invoke(first['owner'], 'list'))['registrations']] == [
            first['registration_id']]
        with pytest.raises(ValueError, match='owner'):
            await invoke(other['owner'], 'cancel', observer_id=first['registration_id'])
        await invoke(first['owner'], 'cancel', observer_id=first['registration_id'])
        await invoke(other['owner'], 'cancel', observer_id=other['registration_id'])
        for params in ({'action': 'list', 'workspace': str(tmp_path)},
                       {'action': 'list', 'owner': ' '}):
            with pytest.raises(ValueError):
                await binding.tools[0].execute('invalid', params)
    finally:
        await binding.aclose()


def test_default_directory_is_xdg_data_home_not_old_state_home(tmp_path, monkeypatch):
    monkeypatch.delenv('XDOG_EYES_RUNTIME_DIR', raising=False)
    monkeypatch.delenv('XDG_DATA_HOME', raising=False)
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'old-state'))
    expected = tmp_path / '.local/share/xdog/eyes'
    assert daemon_location().directory == expected
    assert not (tmp_path / 'old-state').exists()
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'custom-data'))
    assert daemon_location().directory == tmp_path / 'custom-data/xdog/eyes'
