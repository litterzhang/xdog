"""UI-012: removed Git browser cannot intercept drafts or approvals.

Git inspection remains available to the Agent via its normal permissioned tools.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from xdog.agent.tools.tool_bash import create_bash_tool
from xdog.claw.channels.tui.tui_app import SLASH_COMMANDS, ChatApp
from xdog.coding.core.permissions import PermissionRequest
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent
from xdog.tui.prompt_keymap import PromptKeymap


@pytest.fixture(params=['coding', 'claw'])
def app(request, tmp_path: Path):
    if request.param == 'coding':
        session = MagicMock()
        session.working_dir = tmp_path
        session.agent.options.thinking = 'off'
        session.agent.options.stream_options = None
        ui = InteractiveMode(session)
    else:
        ui = ChatApp('/tmp/unused-removed-review-test.sock')
    try:
        yield ui
    finally:
        ui._request_exit()


def test_review_api_and_binding_removed(app) -> None:
    assert not hasattr(app, '_open_review')
    assert not hasattr(app, '_review_backend')
    assert PromptKeymap().resolve(KeyEvent('d', alt=True)) is None
    assert 'review' not in dict(PromptKeymap().bindings)
    assert '/diff' not in SLASH_COMMANDS
    assert 'diff' not in app._editor._command_provider()
    assert '/diff' not in app._editor._command_provider()
    with pytest.raises(ValueError, match='unknown action'):
        PromptKeymap.from_overrides({'review': 'alt+d'})


def test_alt_d_no_longer_opens_browser_or_submits(app) -> None:
    app._editor.set_text('live draft')
    app._tui._dispatch_input(KeyEvent('d', alt=True))
    assert app._editor.get_text() == 'live draft'
    assert app._tui._focused is app._editor
    assert app._layout.details is None
    if isinstance(app, InteractiveMode):
        app._session.agent.prompt.assert_not_called()
    else:
        assert app._send_queue.empty()


def test_removed_alt_d_cannot_approve_tool(tmp_path, monkeypatch) -> None:
    import xdog.tui.tui as module

    session = MagicMock()
    session.working_dir = tmp_path
    session.agent.options.thinking = 'off'
    ui = InteractiveMode(session)
    monkeypatch.setattr(module, '_terminal_width', lambda: 80)
    monkeypatch.setattr(module, '_terminal_height', lambda: 24)
    ui._tui._previous_width, ui._tui._previous_height = 80, 24
    try:
        ui._editor.set_text('preserved')
        ui._show_permission_request(PermissionRequest(id='approval', tool_name='bash', arguments={}, summary='cmd'))
        ui._tui._dispatch_input(KeyEvent('d', alt=True))
        session.permissions.resolve.assert_not_called()
        assert ui._tui._focused is ui._permission_prompt
        ui._tui._dispatch_input(KeyEvent('escape'))
        session.permissions.resolve.assert_called_once_with('approval', 'deny')
        assert ui._editor.get_text() == 'preserved'
        assert ui._tui._focused is ui._editor
    finally:
        ui._request_exit()


def test_browser_modules_and_public_exports_removed() -> None:
    import xdog.tui
    import xdog.tui.components

    for name in ('xdog.tui.repository_review', 'xdog.tui.components.review_panel'):
        assert importlib.util.find_spec(name) is None
    assert not hasattr(xdog.tui, 'RepositoryReview')
    assert not hasattr(xdog.tui.components, 'ReviewPanel')


async def test_agent_git_tool_still_reads_real_diff(tmp_path, monkeypatch) -> None:
    if not shutil.which('git'):
        pytest.skip('requires Git')
    for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(tmp_path / 'no-global'))
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    path = tmp_path / 'sample.txt'
    path.write_text('before\n')
    subprocess.run(['git', '-C', str(tmp_path), 'add', 'sample.txt'], check=True)
    path.write_text('after\n')
    tool = create_bash_tool(initial_cwd=tmp_path)
    result = await tool.execute('git-review', {'command': 'git --no-pager diff --no-ext-diff -- sample.txt'})
    text = '\n'.join(block.text for block in result.content if hasattr(block, 'text'))
    assert '-before' in text and '+after' in text
    assert path.read_text() == 'after\n'
