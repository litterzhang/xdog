"""The actual native Goal route shows text by default; JSON is explicitly requested."""
import json

import pytest
from xdog.skills.goal.host import OperationContext

from packages.skills.tests.goal.test_runtime import close, setup


async def test_native_waiting_status_is_compact_unicode_and_does_not_resume_or_send(tmp_path, monkeypatch):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    try:
        await runtime.service.create('无人值守续跑测试', '真实 Eyes 事件验证', context=OperationContext(True))
        await runtime.service.change('wait', until='user', reason='交付目标未定；等待用户选择',
            question='Which delivery target should be used?', impact='The final target cannot be selected',
            resume_when='User selects the delivery target')
        await runtime.flush()
        before = await runtime.service.summary()
        authorized[0] = False
        for args in ['', 'status']:
            text = await parent.extensions.command('goal', args)
            assert 'Goal: 无人值守续跑测试' in text
            assert 'Status: waiting | wait_for: user' in text
            assert '交付目标未定；等待用户选择' in text
            assert 'waiting for user input; no automatic retry' in text
            assert '/goal status --json' in text
            assert r'\u' not in text and '"criteria"' not in text
        for args in ['status --json', '--json']:
            raw = await parent.extensions.command('goal', args)
            assert '无人值守续跑测试' in raw and r'\u' not in raw
            assert json.loads(raw) == json.loads(json.dumps(before))
        assert await runtime.service.summary() == before
        assert not sent and not parent.extensions.enabled
    finally:
        await close(runtime, parent)


async def test_native_history_text_and_json_keep_original_pagination_and_records(tmp_path, monkeypatch):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    try:
        for i in range(17):
            await runtime.service.create(f'历史目标 {i}', '不启动模型', context=OperationContext(True))
            await runtime.service.change('clear', context=OperationContext(True))
        text = await parent.extensions.command('goal', 'history')
        assert 'Goal history: 17 total' in text and '/goal history 16' in text
        assert text.count('  [cleared]') == 16 and r'\u' not in text
        raw = json.loads(await parent.extensions.command('goal', 'history --json'))
        assert len(raw['goals']) == 16 and raw['next_offset'] == 16
        for args in ['history 16 --json', 'history --json 16']:
            raw = await parent.extensions.command('goal', args)
            page = json.loads(raw)
            assert '历史目标' in raw and r'\u' not in raw
            assert len(page['goals']) == 1 and page['next_offset'] is None
        assert await runtime.service.current() is None
        assert len(await runtime.service.list()) == 17
    finally:
        await close(runtime, parent)


@pytest.mark.parametrize('args', ['status nonsense', 'status --json extra', '--json extra',
                                  'history -1', 'history --json --json', 'history 1 2',
                                  'history 9999999999999', 'history --other'])
async def test_invalid_display_options_cannot_change_goal(tmp_path, monkeypatch, args):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    try:
        with pytest.raises(ValueError, match='Usage: /goal'):
            await parent.extensions.command('goal', args)
        assert await runtime.service.current() is None and not sent
    finally:
        await close(runtime, parent)
