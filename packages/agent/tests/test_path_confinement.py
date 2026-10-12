"""Path confinement — the allowlist mode of `validate_path`.

Two modes with opposite failure behaviour, which is the whole point. The
denylist names a few bad places and allows anything unlisted; the allowlist
names the one good place and *denies* anything unlisted. Getting that backwards
produces a check that passes everything and looks like it works.
"""
from __future__ import annotations

from pathlib import Path

from xdog.agent.tools._utils import validate_path


def test_without_a_bound_an_ordinary_path_is_allowed(tmp_path: Path) -> None:
    """The historical behaviour, which xdog-coding and xdog-claw rely on."""
    assert validate_path(str(tmp_path / "notes.txt")) is None


def test_inside_the_bound_is_allowed(tmp_path: Path) -> None:
    workspace = tmp_path / "runtime"
    workspace.mkdir()

    assert validate_path(str(workspace / "out.json"), confine_to=[workspace]) is None
    assert validate_path(str(workspace / "deep" / "nested.txt"), confine_to=[workspace]) is None
    assert validate_path(str(workspace), confine_to=[workspace]) is None, "the root itself"


def test_outside_the_bound_is_denied(tmp_path: Path) -> None:
    workspace = tmp_path / "runtime"
    workspace.mkdir()
    sibling = tmp_path / "secrets"
    sibling.mkdir()

    error = validate_path(str(sibling / "keys.txt"), confine_to=[workspace])

    assert error is not None
    assert "outside this run's workspace" in error
    assert str(workspace) in error, "say what *is* allowed, not just what is not"


def test_an_unlisted_path_is_denied_rather_than_allowed(tmp_path: Path) -> None:
    """The inversion. A denylist would let `/home/someone/anything` through
    because it is not one of the few named bad places."""
    workspace = tmp_path / "runtime"
    workspace.mkdir()

    assert validate_path("/home/someone/anything", confine_to=[workspace]) is not None
    assert validate_path("/home/someone/anything") is None, "unbounded still allows it"


def test_a_symlink_out_of_the_workspace_is_caught_by_where_it_lands(tmp_path: Path) -> None:
    """Comparing spelled paths would let this through; comparing resolved ones
    does not. A workspace an agent can escape by making one symlink is not a
    workspace."""
    workspace = tmp_path / "runtime"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "target.txt").write_text("secret", encoding="utf-8")
    (workspace / "escape").symlink_to(outside)

    error = validate_path(str(workspace / "escape" / "target.txt"), confine_to=[workspace])

    assert error is not None, "a symlink walked out of the workspace"


def test_several_roots_are_allowed_together(tmp_path: Path) -> None:
    """--allow-read adds a tree; the workspace plus the grants are the bound."""
    workspace = tmp_path / "runtime"
    granted = tmp_path / "data"
    other = tmp_path / "elsewhere"
    for d in (workspace, granted, other):
        d.mkdir()

    roots = [workspace, granted]
    assert validate_path(str(workspace / "a"), confine_to=roots) is None
    assert validate_path(str(granted / "b"), confine_to=roots) is None
    assert validate_path(str(other / "c"), confine_to=roots) is not None


def test_an_empty_allowlist_denies_everything(tmp_path: Path) -> None:
    """Distinct from `None`, which means unbounded.

    An empty list is falsy, so the natural `if not roots` reads it as "no bound
    given" and opens the door — which is exactly backwards. A bug upstream that
    computes no roots must close the door, not grant everything.
    """
    assert validate_path(str(tmp_path / "x"), confine_to=[]) is not None


def test_the_tool_helper_also_treats_absent_and_empty_differently() -> None:
    """The same distinction one layer up, where it is easy to lose."""
    from xdog.agent.tools.tool_filesystem import CONFINE_CTX_KEY, _confinement

    assert _confinement({}) is None, "absent means unbounded"
    assert _confinement({CONFINE_CTX_KEY: []}) == [], "empty means deny everything"


def test_the_denylist_still_applies_inside_a_workspace(tmp_path: Path) -> None:
    """Confinement narrows; it does not re-permit."""
    assert validate_path("/etc/passwd", confine_to=[Path("/etc")]) is not None


# -- the workspace, which is not the same thing as a bound --------------------


def test_a_relative_path_resolves_inside_the_workspace(tmp_path: Path) -> None:
    """The half of the design that is on by default. A model reasons in relative
    paths — "write it to report.md" — and without a workspace those either error
    or land in whatever directory the process started in, which differs between
    running a workflow by hand and running it from a timer."""
    from xdog.agent.tools.tool_filesystem import WORKSPACE_CTX_KEY, _resolve

    ctx = {WORKSPACE_CTX_KEY: str(tmp_path / "runtime")}

    assert _resolve("report.md", ctx) == str(tmp_path / "runtime" / "report.md")
    assert _resolve("a/b.txt", ctx) == str(tmp_path / "runtime" / "a" / "b.txt")


def test_an_absolute_path_is_left_alone(tmp_path: Path) -> None:
    """A workspace says where "here" is; it does not rewrite what the caller
    spelled out in full. Silently relocating an absolute path would make the
    confinement error message describe a path the model never asked for."""
    from xdog.agent.tools.tool_filesystem import WORKSPACE_CTX_KEY, _resolve

    ctx = {WORKSPACE_CTX_KEY: str(tmp_path / "runtime")}

    assert _resolve("/etc/hosts", ctx) == "/etc/hosts"


def test_without_a_workspace_a_relative_path_is_untouched() -> None:
    """xdog-coding and xdog-claw set no workspace, and must keep the behaviour
    they have: a relative path reaches validate_path and is rejected there."""
    from xdog.agent.tools.tool_filesystem import _resolve

    assert _resolve("report.md", {}) == "report.md"
    assert validate_path("report.md") == "Error: file path must be absolute."


def test_a_workspace_alone_does_not_confine(tmp_path: Path) -> None:
    """The distinction the whole redesign turns on. Having a workspace is the
    default; being unable to leave it is opt-in. A workspace that quietly
    confined would break every unconfined workflow that writes to a real path."""
    from xdog.agent.tools.tool_filesystem import WORKSPACE_CTX_KEY, _confinement

    ctx = {WORKSPACE_CTX_KEY: str(tmp_path / "runtime")}

    assert _confinement(ctx) is None
    assert validate_path("/tmp/elsewhere.txt", confine_to=_confinement(ctx)) is None


# -- skill placement belongs to the Agent, not to each caller ----------------


def _manager(tmp_path: Path, *skills: tuple[str, str, bool]) -> object:
    """A SkillManager over a temp directory — the same loader every product uses."""
    from xdog.agent.skills import SkillManager

    for name, body, expires in skills:
        d = tmp_path / "skills" / name
        d.mkdir(parents=True, exist_ok=True)
        meta = "\nscope: turn" if expires else ""
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: desc-{name}{meta}\n---\n\n{body}\n",
            encoding="utf-8",
        )
    return SkillManager(shared_dir=tmp_path / "skills", packaged={})


def _agent(**kw: object) -> object:
    from xdog.agent.agent import Agent
    from xdog.agent.core import AgentConfig

    return Agent(
        lambda *a, **k: None,  # type: ignore[arg-type]
        config=AgentConfig(model="m", system_prompt="THE TASK"),
        **kw,  # type: ignore[arg-type]
    )


def test_a_fixed_skill_goes_in_the_cacheable_prefix(tmp_path: Path) -> None:
    """It is the same for every request of the session, so it belongs where the
    prompt cache can keep it: the front of the system prompt."""
    agent = _agent(skills=_manager(tmp_path, ("house", "USE TABS", False)),
                   active_skills=["house"])

    assert "USE TABS" in agent.state.system_prompt  # type: ignore[attr-defined]
    assert agent.state.system_prompt.strip().endswith("THE TASK")  # type: ignore[attr-defined]
    assert agent.state.messages == ()  # type: ignore[attr-defined]


async def _assert_turn_skill_at_model_boundary(
    tmp_path: Path, *, turn_body: str, fixed_body: str | None = None,
) -> None:
    """Observe both requests, including canonical state *during* adapter entry."""
    from typing import cast

    from xdog.agent import Agent, AgentConfig
    from xdog.agent.skills import SkillManager
    from xdog.ai.types import AssistantMessage, Context, StreamOptions, SystemPromptBlock, TextContent, UserMessage
    from xdog.ai.utils.event_stream import EventStream

    skills = [("temp", turn_body, True)]
    if fixed_body is not None:
        skills.insert(0, ("fixed", fixed_body, False))
    manager = cast(SkillManager, _manager(tmp_path, *skills))
    seen: list[Context] = []

    def user_text(message: UserMessage) -> str:
        if isinstance(message.content, str):
            return message.content
        return "".join(part.text for part in message.content if isinstance(part, TextContent))

    def stream(model: str, context: Context, options: StreamOptions) -> EventStream[AssistantMessage]:
        # A request-local projection must never first pass through canonical history.
        assert all(turn_body not in str(message.content) for message in agent.messages)
        assert agent.state.system_prompt == prefix
        seen.append(context)
        return EventStream.empty(AssistantMessage())

    base = (SystemPromptBlock(text="THE TASK", cache=True),)
    agent = Agent(
        stream, config=AgentConfig(model="offline/skill-placement", system_prompt=base),
        skills=manager, active_skills=[name for name, _, _ in skills], tools=[],
    )
    prefix = agent.state.system_prompt
    assert isinstance(prefix, tuple) and prefix[-1:] == base
    assert turn_body not in str(prefix)
    assert agent.messages == (), "construction must not seed transient canonical history"
    if fixed_body is not None:
        assert fixed_body in prefix[0].text

    for index in range(2):
        task = f"Independent request {index + 1}"
        async for _ in await agent.prompt(task):
            pass
        await agent.wait_for_idle()
        assert len(seen) == index + 1, "assert against an actual adapter request, not only local state"
        context = seen[index]
        assert context.system_prompt == prefix, "turn activation/retirement must not churn prefix bytes or cache flags"
        projected = [message for message in context.messages if turn_body in str(message.content)]
        if index == 0:
            assert len(projected) == 1, "the owning request must see exactly one transient body"
            assert isinstance(projected[0], UserMessage)
            assert context.messages[0] is projected[0], "the turn instructions precede the current task"
        else:
            assert not projected, "the next independent request must not inherit a retired turn skill"
        assert any(isinstance(message, UserMessage) and user_text(message) == task for message in context.messages)
        assert any(isinstance(message, UserMessage) and user_text(message) == task for message in agent.messages)
        assert all(turn_body not in str(message.content) for message in agent.messages)
        assert agent.state.system_prompt == prefix
        assert agent.active_skills == (("fixed",) if fixed_body is not None else ())
        if fixed_body is not None:
            assert fixed_body in str(context.system_prompt), "session instructions survive turn-skill retirement"
            assert all(fixed_body not in str(message.content) for message in context.messages)


async def test_a_turn_scoped_skill_goes_after_the_prefix(tmp_path: Path) -> None:
    """A transient body reaches the model once, without canonical history or cache churn."""
    await _assert_turn_skill_at_model_boundary(tmp_path, turn_body="JUST THIS TURN")


async def test_both_kinds_can_be_given_at_once(tmp_path: Path) -> None:
    """Session instructions stay in the prefix as request-local turn instructions retire."""
    await _assert_turn_skill_at_model_boundary(tmp_path, turn_body="FOR NOW", fixed_body="ALWAYS")


def test_no_skills_changes_nothing(tmp_path: Path) -> None:
    """Every existing caller passes none, and must be unaffected."""
    assert _agent().state.system_prompt == "THE TASK"  # type: ignore[attr-defined]  # noqa: E501
    assert _agent().state.messages == ()  # type: ignore[attr-defined]


def test_the_caller_prompt_and_the_skill_preamble_are_independent(tmp_path: Path) -> None:
    """coding rewrites its whole system prompt before every turn.

    If the Agent stored one merged string, that rewrite would drop the skills —
    or coding would have to re-render them itself, which is exactly the
    duplication that had three packages answering the same question three ways.
    """
    agent = _agent(skills=_manager(tmp_path, ("house", "USE TABS", False)),
                   active_skills=["house"])

    agent.set_system_prompt("A DIFFERENT TASK")  # type: ignore[attr-defined]

    assert "USE TABS" in agent.state.system_prompt  # type: ignore[attr-defined]
    assert agent.state.system_prompt.strip().endswith("A DIFFERENT TASK")  # type: ignore[attr-defined]


def test_dropping_a_skill_leaves_no_trace(tmp_path: Path) -> None:
    """`/unload` is why the body is in the prompt rather than in a message. If
    removal left residue it would be theatre, and a constraint the user was told
    they had lifted would still be in force."""
    agent = _agent(skills=_manager(tmp_path, ("house", "USE TABS", False)),
                   active_skills=["house"])

    agent.set_active_skills([])  # type: ignore[attr-defined]

    assert "USE TABS" not in agent.state.system_prompt  # type: ignore[attr-defined]
    assert agent.state.system_prompt.strip().endswith("THE TASK")  # type: ignore[attr-defined]


# -- a model that cannot take tools through the API must not be sent any -----


def _captured_context(**cfg_kw: object) -> object:
    """Run one turn against a stub stream and return the context it received."""
    import asyncio

    from xdog.agent.agent import Agent
    from xdog.agent.core import AgentConfig, AgentTool
    from xdog.ai.types import AssistantMessage, DoneEvent, TextContent
    from xdog.ai.utils.event_stream import EventStream

    seen: list[object] = []

    def stream_fn(model: object, context: object, options: object = None) -> object:
        seen.append(context)
        stream: EventStream = EventStream()
        msg = AssistantMessage(
            content=(TextContent(text="ok"),), api="openai-completions",
            provider="p", model="m", stop_reason="stop", timestamp=0,
        )

        async def push() -> None:
            await asyncio.sleep(0)
            await stream.send(DoneEvent(stop_reason="stop", message=msg))
            stream.set_result(msg)
            await stream.close()

        asyncio.ensure_future(push())
        return stream

    async def _drain() -> None:
        agent = Agent(
            stream_fn,  # type: ignore[arg-type]
            config=AgentConfig(model="m", system_prompt="x", **cfg_kw),  # type: ignore[arg-type]
            tools=(AgentTool(name="read", description="d", parameters={}, execute=None),),  # type: ignore[arg-type]
        )
        async for _ in await agent.prompt("go"):
            pass

    asyncio.run(_drain())
    return seen[0]


def test_tools_are_withheld_from_a_model_that_cannot_take_them() -> None:
    """`body["tools"]` is written whenever the context carries tools, with no
    check of its own — so the request would carry definitions the model cannot
    use and may reject. Its tools reach it as prompt text instead."""
    assert _captured_context(supports_tool_calls=False).tools is None  # type: ignore[attr-defined]


def test_tools_are_sent_when_the_model_takes_them() -> None:
    ctx = _captured_context(supports_tool_calls=True)
    assert ctx.tools and ctx.tools[0].name == "read"  # type: ignore[attr-defined]


def test_not_determined_behaves_like_supported() -> None:
    """None is "nobody looked", and must not silently remove every tool. It is
    also the default, so a caller that says nothing gets what it always got."""
    ctx = _captured_context()
    assert ctx.tools and ctx.tools[0].name == "read"  # type: ignore[attr-defined]
