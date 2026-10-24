"""Tests for skills exposed as slash commands.

The wiring that matters is not that `/flow` prints something — it is that the
skill's text reaches the model, and that it can be taken back out again. The
reference clients for this format put the body into the message history, where
it stays for the rest of the session; these tests pin the alternative.
"""
from pathlib import Path
from typing import Any

import pytest
from xdog.agent.skills import SkillRepo
from xdog.coding.core import slash_commands
from xdog.coding.core.slash_commands import (
    BUILTIN_COMMANDS,
    execute_command,
    list_commands,
    parse_slash_command,
)


class FakeSession:
    """Just the surface the skill commands touch."""

    def __init__(self) -> None:
        self.runtime = None
        self._sdk_controller = None
        self.active_skills: frozenset[str] = frozenset()
        self.rebuilds = 0

    def activate_skill(self, slug: str) -> None:
        self.active_skills = self.active_skills | {slug}
        self.rebuilds += 1

    def deactivate_skill(self, slug: str) -> bool:
        if slug not in self.active_skills:
            return False
        self.active_skills = self.active_skills - {slug}
        self.rebuilds += 1
        return True


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the command dispatcher at a temporary skills directory."""
    shared = tmp_path / "skills"
    shared.mkdir(parents=True)
    manager = SkillRepo(packaged={}).register(directory=shared, name="shared")
    monkeypatch.setattr(slash_commands, "skill_repo", lambda: manager)
    return shared


def _write(shared: Path, slug: str, body: str, description: str = "does a thing") -> None:
    d = shared / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {slug}\ndescription: {description}\n---\n\n{body}", encoding="utf-8"
    )


async def test_a_skill_command_activates_it(skills: Path, session: FakeSession) -> None:
    _write(skills, "deploy", "Always run the tests before deploying.")

    result = await execute_command("deploy", "", session)  # type: ignore[arg-type]

    assert session.active_skills == {"deploy"}
    assert "deploy" in result.output
    assert "/unload" in result.output, "tell the user how to undo it"
    assert result.prompt == "", "activating alone should not start a turn"


async def test_an_argument_becomes_the_turn(skills: Path, session: FakeSession) -> None:
    """The body is in the system prompt, so only the request needs sending."""
    _write(skills, "review", "Review the diff.")

    result = await execute_command("review", "focus on error handling", session)  # type: ignore[arg-type]

    assert session.active_skills == {"review"}
    assert result.prompt == "focus on error handling"
    assert "Review the diff." not in result.prompt, "the body belongs in the system prompt"


async def test_a_skill_can_be_unloaded_again(skills: Path, session: FakeSession) -> None:
    """The whole point: the reference implementations cannot do this."""
    _write(skills, "flow", "Write a workflow.")
    await execute_command("flow", "", session)  # type: ignore[arg-type]

    result = await execute_command("unload", "flow", session)  # type: ignore[arg-type]

    assert session.active_skills == frozenset()
    assert "Unloaded" in result.output


async def test_unloading_something_inactive_says_so(skills: Path, session: FakeSession) -> None:
    result = await execute_command("unload", "nope", session)  # type: ignore[arg-type]

    assert "not active" in result.output
    assert session.rebuilds == 0, "no need to rebuild the prompt for a no-op"


async def test_unload_with_no_argument_lists_what_is_active(
    skills: Path, session: FakeSession
) -> None:
    _write(skills, "a", "x")
    _write(skills, "b", "y")
    await execute_command("a", "", session)  # type: ignore[arg-type]
    await execute_command("b", "", session)  # type: ignore[arg-type]

    result = await execute_command("unload", "", session)  # type: ignore[arg-type]

    assert "a, b" in result.output
    assert session.active_skills == {"a", "b"}, "listing must not deactivate anything"


async def test_unload_all_drops_everything(skills: Path, session: FakeSession) -> None:
    _write(skills, "a", "x")
    _write(skills, "b", "y")
    await execute_command("a", "", session)  # type: ignore[arg-type]
    await execute_command("b", "", session)  # type: ignore[arg-type]

    result = await execute_command("unload", "all", session)  # type: ignore[arg-type]

    assert session.active_skills == frozenset()
    assert "2" in result.output


async def test_activating_twice_is_idempotent(skills: Path, session: FakeSession) -> None:
    _write(skills, "flow", "x")
    await execute_command("flow", "", session)  # type: ignore[arg-type]
    await execute_command("flow", "", session)  # type: ignore[arg-type]

    assert session.active_skills == {"flow"}


async def test_skills_listing_marks_the_active_ones(skills: Path, session: FakeSession) -> None:
    _write(skills, "on", "x")
    _write(skills, "off", "y")
    await execute_command("on", "", session)  # type: ignore[arg-type]

    result = await execute_command("skills", "", session)  # type: ignore[arg-type]

    active_line = next(ln for ln in result.output.splitlines() if "/on" in ln)
    inactive_line = next(ln for ln in result.output.splitlines() if "/off" in ln)
    assert "[active]" in active_line
    assert "[active]" not in inactive_line


async def test_an_unknown_command_is_still_unknown(skills: Path, session: FakeSession) -> None:
    result = await execute_command("nope", "", session)  # type: ignore[arg-type]

    assert "Unknown command" in result.output
    assert result.prompt == ""
    assert session.active_skills == frozenset()


async def test_a_skill_cannot_shadow_a_builtin(skills: Path, session: FakeSession) -> None:
    """A skills directory is user-writable; `/quit` must keep quitting."""
    _write(skills, "quit", "Do something else entirely.")

    result = await execute_command("quit", "", session)  # type: ignore[arg-type]

    assert result.exit_requested is True
    assert session.active_skills == frozenset()
    assert list_commands()["quit"] == BUILTIN_COMMANDS["quit"]


async def test_skills_are_listed_as_commands_for_completion(skills: Path) -> None:
    _write(skills, "flow", "...", description="write a flow workflow")

    commands = list_commands()

    assert commands["flow"] == "write a flow workflow"
    assert "model" in commands, "built-ins are still there"
    assert "unload" in commands


async def test_skills_command_reports_an_empty_directory_usefully(
    skills: Path, session: FakeSession
) -> None:
    result = await execute_command("skills", "", session)  # type: ignore[arg-type]

    assert "No skills found" in result.output
    assert "pip install xdog-flow" in result.output, "tell the user how to get one"


async def test_skills_command_marks_which_skills_came_from_a_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: FakeSession
) -> None:
    shared = tmp_path / "skills"
    shared.mkdir(parents=True)
    _write(shared, "mine", "local", description="a local one")

    packaged_dir = tmp_path / "pkg"
    packaged_dir.mkdir()
    (packaged_dir / "SKILL.md").write_text(
        "---\nname: flow\ndescription: from a package\n---\n\nbody", encoding="utf-8"
    )
    manager = SkillRepo(packaged={'flow': packaged_dir}).register(directory=shared, name="shared")
    monkeypatch.setattr(slash_commands, "skill_repo", lambda: manager)

    result = await execute_command("skills", "", session)  # type: ignore[arg-type]

    assert "* /flow" in result.output
    assert "  /mine" in result.output
    assert "shipped with an installed package" in result.output


async def test_help_lists_skills_separately_from_builtins(skills: Path) -> None:
    _write(skills, "flow", "...", description="write a flow workflow")

    result = await execute_command("help", "", session=None)  # type: ignore[arg-type]

    assert "Skills:" in result.output
    assert "/flow" in result.output


async def test_a_broken_skills_directory_does_not_break_the_dispatcher(
    monkeypatch: pytest.MonkeyPatch, session: FakeSession
) -> None:
    """Slash commands must keep working when skills cannot be read."""
    def _boom() -> Any:
        raise OSError("permission denied")

    monkeypatch.setattr(slash_commands, "skill_repo", _boom)

    assert "model" in list_commands()
    result = await execute_command("nope", "", session)  # type: ignore[arg-type]
    assert "Unknown command" in result.output


def test_parse_slash_command_splits_name_from_arguments() -> None:
    assert parse_slash_command("/flow add a retry step") == ("flow", "add a retry step")
    assert parse_slash_command("/flow") == ("flow", "")
    assert parse_slash_command("not a command") is None


# -- The system-prompt side: what activation actually changes --


def test_the_system_prompt_gains_and_loses_the_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deactivation has to leave no trace, or `/unload` is theatre.

    Asserted against the Agent's system prompt rather than against the section
    coding builds: the body is placed by `Agent.set_skills` now, so the section
    is no longer where it lands. Checking what the model actually receives is
    the better test anyway — it survives the placement moving again.
    """
    from xdog.agent.agent import Agent
    from xdog.agent.core import AgentConfig
    from xdog.coding.core.agent_session import _skills_context

    shared = tmp_path / "skills"
    shared.mkdir(parents=True)
    _write(shared, "flow", "STEP ONE: validate the graph.", description="write a workflow")
    manager = SkillRepo(packaged={}).register(directory=shared, name="shared")
    monkeypatch.setattr(slash_commands, "skill_repo", lambda: manager)

    idle = _skills_context(frozenset())
    assert idle == '', 'Coding must not render a second Agent-owned skill index'
    assert "STEP ONE" not in idle, "the body must wait to be asked for"

    agent = Agent(
        lambda *a, **k: None,
        config=AgentConfig(model="m", system_prompt="BASE"),
        skills=manager,
    )
    idle_prompt = agent.state.system_prompt
    assert "write a workflow" in idle_prompt, "the index is always there"
    assert "STEP ONE" not in idle_prompt, "the body waits to be asked for"

    agent.set_active_skills(["flow"])
    assert "STEP ONE: validate the graph." in agent.state.system_prompt
    assert agent.state.system_prompt.strip().endswith("BASE"), "the caller's prompt survives"

    agent.set_active_skills([])
    assert agent.state.system_prompt == idle_prompt, "byte for byte, or /unload is theatre"


def test_the_skills_section_is_empty_when_nothing_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xdog.coding.core.agent_session import _skills_context

    manager = SkillRepo(packaged={}).register(directory=tmp_path / 'empty', name="shared")
    monkeypatch.setattr(slash_commands, "skill_repo", lambda: manager)

    assert _skills_context(frozenset()) == ""


def test_an_unreadable_skills_directory_does_not_break_the_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xdog.coding.core.agent_session import _skills_context

    def _boom() -> Any:
        raise OSError("permission denied")

    monkeypatch.setattr(slash_commands, "skill_repo", _boom)
    assert 'cannot deactivate' in _skills_context(frozenset({'flow'}))
    # Rendering this product note must not consult a filesystem skill manager.


# -- Declared lifetime, and who is allowed to end it --


class FakeExpiringSession:
    """Enough of AgentSession to exercise the expiry step in isolation."""

    def __init__(self, active: set[str]) -> None:
        from types import SimpleNamespace
        self._active_skills = frozenset(active)
        self.agent = SimpleNamespace(active_skills=tuple(sorted(active)))
        self.rebuilds = 0
        self.pushed: list[list[str]] = []

    def _rebuild_system_prompt(self) -> None:
        self.rebuilds += 1

    def _push_skills(self) -> None:
        """Expiry must tell the Agent too, or the body outlives the scope."""
        self.pushed.append(sorted(self._active_skills))


def _expire(session: Any) -> None:
    from xdog.coding.core.agent_session import AgentSession

    AgentSession._expire_turn_scoped_skills(session)


def _write_scoped(shared: Path, slug: str, scope: str) -> None:
    d = shared / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {slug}\ndescription: d\nmetadata:\n  scope: {scope}\n---\n\nbody",
        encoding="utf-8",
    )


def test_a_turn_scoped_skill_retires_itself(skills: Path) -> None:
    _write_scoped(skills, "once", "turn")
    session = FakeExpiringSession({"once"})
    session.agent.active_skills = ()  # SDK completed the owning model run.

    _expire(session)

    assert session._active_skills == frozenset()
    assert session.rebuilds == 1


def test_a_session_scoped_skill_stays(skills: Path) -> None:
    """The guardrail case: it must survive turns nobody used it in."""
    _write_scoped(skills, "guard", "session")
    session = FakeExpiringSession({"guard"})

    _expire(session)

    assert session._active_skills == {"guard"}
    assert session.rebuilds == 0, "no prompt rebuild when nothing changed"


def test_expiry_only_touches_the_skills_that_declared_it(skills: Path) -> None:
    _write_scoped(skills, "once", "turn")
    _write_scoped(skills, "guard", "session")
    session = FakeExpiringSession({"once", "guard"})
    session.agent.active_skills = ("guard",)

    _expire(session)

    assert session._active_skills == {"guard"}


def test_expiry_survives_a_skill_that_vanished_mid_session(skills: Path) -> None:
    """The user may delete a skill file while it is active."""
    session = FakeExpiringSession({"deleted-since"})

    _expire(session)

    assert session._active_skills == {"deleted-since"}, "unknown means keep, not drop"


def test_expiry_survives_an_unreadable_skills_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> Any:
        raise OSError("permission denied")

    monkeypatch.setattr(slash_commands, "skill_repo", _boom)
    session = FakeExpiringSession({"flow"})

    _expire(session)

    assert session._active_skills == {"flow"}


def test_the_model_is_told_it_may_suggest_but_not_unload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Layer three. A skill is often a constraint, and the constrained party
    should not hold the release — so there is no tool, and the prompt says so."""
    from xdog.coding.core.agent_session import _skills_context

    shared = tmp_path / "skills"
    shared.mkdir(parents=True)
    _write(shared, "guard", "Always run the tests before deploying.")
    manager = SkillRepo(packaged={}).register(directory=shared, name="shared")
    monkeypatch.setattr(slash_commands, "skill_repo", lambda: manager)

    prompt = _skills_context(frozenset({"guard"}))

    assert "cannot deactivate" in prompt
    assert "/unload" in prompt
    assert "the user decides" in prompt.lower()


# -- tool definitions were being sent twice ---------------------------------


def _cfg(model: str = "copilot/x"):
    from xdog.coding.config import PlatformInfo, RuntimeConfig

    return RuntimeConfig(
        model=model, thinking_level="normal", permission_mode="ask", allowed_tools=("read",),
        custom_instructions="", extensions=(), working_dir="/tmp",
        platform_info=PlatformInfo.detect(),
    )


_TOOLS = [{"name": "read", "description": "Read a file", "parameters": {"path": "string"}}]


def test_a_native_tool_calling_model_is_not_told_its_tools_twice() -> None:
    """They already arrive as structured tool definitions through the API.
    Describing them again sends every name, description and parameter schema a
    second time, in the cached prefix, for a model that did not need it."""
    from xdog.coding.core.system_prompt import build_system_prompt

    described = build_system_prompt(_cfg(), _TOOLS, native_tool_calls=False)
    native = build_system_prompt(_cfg(), _TOOLS, native_tool_calls=True)

    assert "Read a file" in described
    assert "Read a file" not in native
    assert len(native) < len(described)


def test_the_default_keeps_tools_visible() -> None:
    """The two mistakes are not symmetric. A duplicate costs tokens; a model
    that cannot see its tools cannot act, and that reads as the model being
    useless rather than as a missing prompt section — so unknown means describe.
    """
    from xdog.coding.core.system_prompt import build_system_prompt

    assert "Read a file" in build_system_prompt(_cfg(), _TOOLS)


@pytest.mark.parametrize('capability', [None, False, True])
def test_an_unresolvable_model_still_gets_its_tools_described(tmp_path: Path, capability) -> None:
    """Use the actual owning Agent capability; unknown/false never hide tool instructions."""
    import asyncio

    from xdog.agent import Agent, AgentConfig
    from xdog.agent.helpers import model_supports_tool_calls
    from xdog.coding.core.agent_session import AgentSession
    from xdog.coding.core.permissions import PermissionManager
    from xdog.coding.core.session_manager import SessionManager
    from xdog.coding.core.settings_manager import SettingsManager
    from xdog.coding.core.system_prompt import build_system_prompt

    def unexpected(*args):
        pytest.fail('Capability inspection must not submit model work')
    name = 'no-such-provider/no-such-model'
    manager = SessionManager(tmp_path / 'sessions')
    session = AgentSession(agent=Agent(unexpected, config=AgentConfig(model=name, supports_tool_calls=capability)),
        session_data=manager.create_session(model=name, working_dir=tmp_path), session_manager=manager,
        settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
        tool_repo=None, bash=None, permissions=PermissionManager('deny'))
    try:
        assert model_supports_tool_calls(name) is None
        native = session._native_tool_calls()
        assert native is (capability is True)
        prompt = build_system_prompt(_cfg(), _TOOLS, native_tool_calls=native)
        assert ('Read a file' in prompt) is (capability is not True)
    finally:
        asyncio.run(session.children.runtime.aclose())
        asyncio.run(session.extensions.aclose())
        session.dispose()


def test_completion_offers_skills_not_only_builtins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A skill has always been runnable — the dispatcher falls through to
    `_load_skill`. It just could not be *found*: completion read
    BUILTIN_COMMANDS, so you had to already know the name. `list_commands()`
    exists for exactly this and says so in its docstring.
    """
    from xdog.coding.core import slash_commands
    from xdog.coding.core.slash_commands import list_commands

    shared = tmp_path / "skills"
    shared.mkdir(parents=True)
    _write(shared, "flow", "body", description="write a workflow")
    manager = SkillRepo(packaged={}).register(directory=shared, name="shared")
    monkeypatch.setattr(slash_commands, "skill_repo", lambda: manager)

    commands = list_commands()

    assert "flow" in commands, "a skill is a command"
    assert commands["flow"] == "write a workflow"
    assert "quit" in commands, "and the built-ins are still there"


def test_a_builtin_wins_a_name_clash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Otherwise a skill named `quit` could take over leaving the session."""
    from xdog.coding.core import slash_commands
    from xdog.coding.core.slash_commands import BUILTIN_COMMANDS, list_commands

    shared = tmp_path / "skills"
    shared.mkdir(parents=True)
    _write(shared, "quit", "body", description="a skill pretending to be quit")
    manager = SkillRepo(packaged={}).register(directory=shared, name="shared")
    monkeypatch.setattr(slash_commands, "skill_repo", lambda: manager)

    assert list_commands()["quit"] == BUILTIN_COMMANDS["quit"]
