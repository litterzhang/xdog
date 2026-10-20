from __future__ import annotations

import pytest
from xdog.claw.channels.tui.tui_app import ChatApp

from packages.claw.tests.test_goal_extension_views import application_with_display
from packages.claw.tests.test_goal_host import activate


async def test_public_goals_runs_through_actual_claw_extension_owner_without_a_model(tmp_path):
    app, runtime, model = application_with_display(tmp_path)
    try:
        await app.start()
        await activate(app)
        session = runtime._active_session
        before = len(model.calls)
        assert 'goals' in session.extensions.commands()
        result = await app.manage_extensions('main', 'command', 'goals', '', session.meta.session_id)
        assert 'no goals' in result['output'].lower() and len(model.calls) == before
        assert result['snapshot']['command_owners']['goals'] == 'goal_ui'
    finally:
        await app.stop()


def test_native_submit_dispatches_public_catalog_name_not_a_model_prompt(monkeypatch):
    ui = ChatApp('/unused-public-commands')
    called = []
    ui._extension_snapshot = {'commands': {'goals': 'Read goals'}, 'command_owners': {'goals': 'goal_ui'}}
    monkeypatch.setattr(ui, '_extension_command', lambda name, args: called.append((name, args)))
    try:
        ui._handle_submit('/goals goal-1')
        assert called == [('goals', 'goal-1')]
        assert ui._send_queue.empty()
    finally:
        ui._request_exit()



@pytest.mark.parametrize('name', ['goals', 'inspect-state'])
def test_real_native_public_command_send_pins_declared_owner_before_pending(name):
    from xdog.agent.extensions.runtime import validate_snapshot
    ui = ChatApp('/unused-public-native-send')
    ui._connection_ready = True
    ui._extension_supported = True
    ui._gateway_session_id = 'original-session'
    ui._extension_snapshot = validate_snapshot({
        'extensions': [{'name': 'goal_ui', 'state': 'active'}],
        'commands': {name: 'Read state'}, 'command_owners': {name: 'goal_ui'}, 'ui': {}})
    try:
        ui._handle_submit('/' + name + ' selected')
        request = ui._send_queue.get_nowait()
        assert request['type'] == 'extension_control' and request['name'] == name
        assert request['session_id'] == 'original-session'
        assert ui._extension_command_owner == (request['request_id'], 'goal_ui')
        assert ui._extension_pending == (request['request_id'], 'original-session')
        assert ui._send_queue.empty()
    finally:
        ui._request_exit()
