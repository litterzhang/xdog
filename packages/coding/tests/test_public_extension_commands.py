from __future__ import annotations

from xdog.coding.core.slash_commands import execute_command, list_commands

from packages.coding.tests.test_goal_extension_views import session_with_display
from packages.coding.tests.test_goal_lifecycle import dispose


async def test_public_goals_uses_actual_extension_route_help_and_keeps_goal_skill(tmp_path):
    session, calls = session_with_display(tmp_path)
    try:
        await session.send_message('Ready')
        before = len(calls)
        assert 'goals' in session.extensions.commands()
        assert session.extensions.command_owner('goals') == 'goal_ui'
        result = await execute_command('goals', '', session)
        assert 'no goals' in result.output.lower()
        assert len(calls) == before and 'goal' in list_commands()
        help_result = await execute_command('help', 'goals', session)
        assert '/goals' in help_result.output and '/ext-goal_ui-goals' not in help_result.output
    finally:
        await dispose(session)


async def test_public_name_preserves_native_readonly_source_attribution(tmp_path, monkeypatch):
    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode, _CommandState
    session, calls = session_with_display(tmp_path)
    ui = None
    try:
        await session.send_message('Ready')
        ui = InteractiveMode(session)
        assert 'goals' in ui._editor._command_provider()
        ui._command = _CommandState(1, 'goals', False)
        answers = []
        monkeypatch.setattr(ui, '_answer_extension_dialog', lambda key, value: answers.append(value))
        ui._serve_session_request({'id': 'actual-owner', 'extension': 'goal_ui', 'operation': 'snapshot'})
        ui._serve_session_request({'id': 'wrong-owner', 'extension': 'another', 'operation': 'snapshot'})
        assert answers[0]['session_id'] == session.session_id and answers[1] is None
        assert len(calls) == 1
    finally:
        if ui is not None:
            ui._request_exit()
        await dispose(session)


async def test_public_extension_cannot_shadow_builtin_native_command_or_installed_skill(tmp_path):
    from packages.coding.tests.extension_session_helpers import session as create_extension_session
    for name in ('quit', 'copy', 'goal'):
        session, calls = create_extension_session(tmp_path / name, extra=('publiccommands',))
        session.extensions.settings['publiccommands'] = {'public_name': name}
        try:
            await session.initialize_extensions()
            assert 'publiccommands' in session.extensions._failed
            assert not calls
            if name == 'goal':
                result = await execute_command('goal', 'status', session)
                assert session.extensions.is_native_command('goal')
                assert session.extensions.command_owner('goal') == 'goal'
                assert 'current' in result.output and not calls
            else:
                assert name not in session.extensions.commands()
        finally:
            await session.aclose()


async def test_new_conflicting_skill_keeps_help_snapshot_and_unload_recoverable(tmp_path, monkeypatch):
    import pytest
    from xdog.agent.skills import SkillRepo
    from xdog.coding.core import slash_commands
    manager = SkillRepo().register(directory=tmp_path / 'skills', name="shared")
    monkeypatch.setattr(slash_commands, 'skill_repo', lambda: manager)
    session, calls = session_with_display(tmp_path)
    try:
        await session.send_message('Ready')
        before = len(calls)
        skill = tmp_path / 'skills' / 'goals'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: goals\ndescription: New local skill\n---\nSKILL BODY')
        help_result = await execute_command('help', '', session)
        assert help_result.output
        assert 'goals' not in session.extensions.snapshot()['commands']
        assert session.extensions.command_conflicts() == {'goals': 'goal_ui'}
        with pytest.raises(ValueError, match='conflict|Conflict'):
            await execute_command('goals', '', session)
        await execute_command('extensions', 'unload goal_ui', session)
        assert next(row for row in session.extensions.snapshot()['extensions']
                    if row['name'] == 'goal_ui')['state'] == 'unloaded'
        assert not session.extensions.command_conflicts() and len(calls) == before
    finally:
        await dispose(session)
