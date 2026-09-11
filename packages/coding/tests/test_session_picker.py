"""Directory-scoped resume selection and CLI handoff."""
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner
from xdog.coding.cli.args import cli
from xdog.coding.cli.session_picker import SessionPicker
from xdog.coding.core.session_manager import SessionManager
from xdog.tui.keys import KeyEvent
from xdog.tui.utils import visible_width


def test_sessions_filter_before_limit_and_preserve_directory(tmp_path):
    manager = SessionManager(tmp_path / "sessions")
    here, elsewhere = tmp_path / "here", tmp_path / "elsewhere"
    expected = [manager.create_session(model="m", working_dir=here).session_id for _ in range(25)]
    manager.create_session(model="other", working_dir=elsewhere)
    legacy = manager.create_session(model="legacy")
    found = manager.list_sessions(limit=None, working_dir=here)
    assert {meta.session_id for meta in found} == set(expected)
    assert len(manager.list_sessions(limit=1, working_dir=here)) == 1
    restored = manager.load_session(expected[0])
    assert restored is not None and restored.working_dir == str(here.resolve())
    assert legacy.session_id not in {meta.session_id for meta in found}


def test_selector_navigation_cancel_and_height(tmp_path):
    manager = SessionManager(tmp_path / "sessions")
    for i in range(30):
        manager.create_session(model="model", summary=f"summary-{i}", working_dir=tmp_path)
    picker = SessionPicker(manager.list_sessions(limit=None), tmp_path)
    selected = []
    picker.on_select_cb = lambda item: selected.append(item.value)
    picker.set_height(6)
    rows = picker.render(35)
    assert len(rows) <= 6
    assert all(visible_width(row) <= 35 for row in rows)
    picker.handle_input(KeyEvent(key="end"))
    assert picker.selected_item == picker.items[-1]
    picker.handle_input(KeyEvent(key="enter"))
    assert selected == [picker.items[-1].value]
    canceled = []
    picker.on_cancel_cb = lambda: canceled.append(True)
    picker.handle_input(KeyEvent(key="escape"))
    assert canceled == [True]


def test_resume_cli_passes_selected_id_and_directory(tmp_path):
    with patch("xdog.coding.cli.args.pick_session_command", return_value="chosen") as picker:
        with patch("xdog.coding.main.run_agent") as run:
            result = CliRunner().invoke(cli, ["-r", "--working-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    picker.assert_called_once_with(working_dir=Path(tmp_path))
    assert run.call_args.kwargs["resume_id"] == "chosen"
    assert run.call_args.kwargs["resume"] is False


def test_canceled_resume_never_starts_agent():
    with patch("xdog.coding.cli.args.pick_session_command", return_value=None):
        with patch("xdog.coding.main.run_agent") as run:
            assert CliRunner().invoke(cli, ["-r"]).exit_code == 0
    run.assert_not_called()


def test_conflicting_resume_flags_rejected():
    result = CliRunner().invoke(cli, ["-r", "--resume-id", "chosen"])
    assert result.exit_code != 0


def test_unknown_sessions_are_listed_but_other_directories_are_excluded(tmp_path):
    manager = SessionManager(tmp_path / "sessions")
    current = manager.create_session(model="m", working_dir=tmp_path)
    legacy = manager.create_session(model="m")
    manager.create_session(model="other", working_dir=tmp_path / "elsewhere")
    sessions = manager.list_sessions(limit=None, working_dir=tmp_path, include_unknown=True)
    picker = SessionPicker(sessions, tmp_path)
    assert [item.value for item in picker.items] == [current.session_id, legacy.session_id]
    assert "[Current dir]" in picker.items[0].label
    assert "[Unknown dir]" in picker.items[1].label
    assert manager.load_session(legacy.session_id).working_dir == ""


def test_canceling_legacy_picker_does_not_assign_directory(tmp_path, monkeypatch):
    from xdog.coding.cli.session_picker import pick_session_command

    monkeypatch.setenv("CODING_DIR", str(tmp_path))
    manager = SessionManager(tmp_path / "sessions")
    legacy = manager.create_session(model="m")
    path = next((tmp_path / "sessions").glob("*.json"))
    before = path.read_bytes()
    with patch("xdog.coding.cli.session_picker.sys.stdin.isatty", return_value=True):
        with patch("xdog.coding.cli.session_picker.sys.stdout.isatty", return_value=True):
            with patch("xdog.coding.cli.session_picker.TUI.start") as start:
                assert pick_session_command(tmp_path) is None
    start.assert_called_once()
    assert path.read_bytes() == before
    assert manager.load_session(legacy.session_id).working_dir == ""
