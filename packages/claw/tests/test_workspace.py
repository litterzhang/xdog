"""Tests for workspace management."""
from xdog.claw.core.prompt import init_workspace, run_bootstrap, set_identity_name


def test_init_workspace_does_not_overwrite_existing(tmp_path):
    ws = tmp_path / "workspace"
    init_workspace(ws)
    (ws / "AGENTS.md").write_text("Custom instructions")
    init_workspace(ws)
    assert (ws / "AGENTS.md").read_text() == "Custom instructions"


def test_set_identity_name_creates_when_missing(tmp_path):
    ws = tmp_path / "workspace"
    set_identity_name(ws, "Claw")
    assert "Name: Claw" in (ws / "IDENTITY.md").read_text()
    assert "{{agent_name}}" not in (ws / "IDENTITY.md").read_text()


def test_set_identity_name_overwrites_existing_name(tmp_path):
    """Force-write replaces the Name line but preserves other content.

    Regression for the onboard rename bug: init_workspace seeds IDENTITY.md with
    the default "Assistant" and won't overwrite it, so onboard must force the
    chosen name onto the existing file.
    """
    ws = tmp_path / "workspace"
    init_workspace(ws)  # writes "Name: Assistant"
    (ws / "IDENTITY.md").write_text("# Identity\n\nName: Assistant\n\nExtra: keep me\n")
    set_identity_name(ws, "Claw")
    text = (ws / "IDENTITY.md").read_text()
    assert "Name: Claw" in text
    assert "Name: Assistant" not in text
    assert "Extra: keep me" in text  # unrelated content preserved


def test_set_identity_name_appends_when_no_name_line(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)
    (ws / "IDENTITY.md").write_text("# Identity\n\nSome notes only\n")
    set_identity_name(ws, "Rex")
    text = (ws / "IDENTITY.md").read_text()
    assert "Some notes only" in text
    assert "Name: Rex" in text


def test_run_bootstrap_executes_and_deletes(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)
    (ws / "BOOTSTRAP.md").write_text("First run setup instructions")
    assert run_bootstrap(ws) == "First run setup instructions"
    assert not (ws / "BOOTSTRAP.md").exists()
    assert run_bootstrap(ws) is None


def test_all_workspace_defaults_are_packaged_and_seeded(tmp_path):
    from importlib.resources import files

    from xdog.claw.core.prompt.workspace import WORKSPACE_FILES

    init_workspace(tmp_path, agent_name="Maotao")
    for filename in WORKSPACE_FILES:
        default = files("xdog.claw").joinpath("resources", filename).read_text(encoding="utf-8")
        expected = default.replace("{{agent_name}}", "Maotao") if filename == "IDENTITY.md" else default
        assert (tmp_path / filename).read_text() == expected
    assert "Three similar lines of code" not in (tmp_path / "AGENTS.md").read_text()


def test_explicit_generated_content_is_used_only_for_missing_files(tmp_path):
    contents = {"IDENTITY.md": "# Identity\nName: Generated cat\n", "SOUL.md": "Playful generated persona",
                "USER.md": "Alice prefers Chinese", "AGENTS.md": "Only discuss cats"}
    init_workspace(tmp_path, identity_content=contents["IDENTITY.md"], soul_content=contents["SOUL.md"],
                   user_content=contents["USER.md"], agents_content=contents["AGENTS.md"])
    for filename, content in contents.items():
        assert (tmp_path / filename).read_text() == content
    # An empty file may be an intentional user edit; initialization must not fill it.
    (tmp_path / "SOUL.md").write_text("")
    init_workspace(tmp_path, agent_name="New name", soul_content="Do not overwrite")
    assert (tmp_path / "SOUL.md").read_text() == ""
    assert (tmp_path / "IDENTITY.md").read_text() == contents["IDENTITY.md"]
    (tmp_path / "USER.md").unlink()
    init_workspace(tmp_path, user_content="Updated generated profile")
    assert (tmp_path / "USER.md").read_text() == "Updated generated profile"
    assert (tmp_path / "AGENTS.md").read_text() == contents["AGENTS.md"]


async def test_group_creation_initializes_all_templates_without_model_calls(tmp_path):
    from xdog.claw.config import ClawConfig
    from xdog.claw.core.runtime.orchestrator import Orchestrator
    from xdog.claw.core.types import Group

    def no_model(*_args):
        raise AssertionError("Group initialization must not call a model")
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), stream_fn=no_model)
    try:
        app.register_group(Group(id="maotao", name="毛桃", workspace=str(tmp_path / "cat"), enabled_tools=()))
        assert "Name: 毛桃" in (tmp_path / "cat/IDENTITY.md").read_text()
        for name in ("IDENTITY.md", "SOUL.md", "USER.md", "AGENTS.md"):
            assert (tmp_path / "cat" / name).read_text()
        app.auto_register_group("new-group")
        workspace = app.get_group_workspace("new-group")
        assert "Name: new-group" in (workspace / "IDENTITY.md").read_text()
        assert all((workspace / name).is_file() for name in ("SOUL.md", "USER.md", "AGENTS.md"))
    finally:
        await app.stop()
