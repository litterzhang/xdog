"""Read-only, explicitly enabled skill tooling with real agent-loop coverage."""

from __future__ import annotations

import asyncio
import json
import os
import threading

import pytest
from xdog.agent import Agent, AgentConfig, BeforeToolCallResult, create_skill_tool
from xdog.agent.skills import SkillRepo
from xdog.ai.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage
from xdog.ai.utils.event_stream import EventStream


def write_skill(root, slug="procedure", body="SECRET_PROCEDURE_BODY"):
    directory = root / slug
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(f"---\nname: {slug}\ndescription: example\n---\n{body}\n")
    return directory


@pytest.fixture
def skills(tmp_path):
    root = tmp_path / "skills"
    write_skill(root)
    return SkillRepo(builtin=False, packaged={}).register(directory=root, name="shared")


async def call(tool, **args):
    return await tool.execute("call", args)


async def test_readonly_list_and_load_do_not_activate_or_write(skills):
    tool = create_skill_tool(skills)
    assert tool.parameters["properties"]["action"]["enum"] == ["list", "load"]
    listed = await call(tool, action="list")
    assert not listed.is_error
    data = json.loads(listed.content[0].text)
    assert data["skills"][0]["slug"] == "procedure" and not data["truncated"]
    assert "SECRET_PROCEDURE_BODY" not in listed.content[0].text
    loaded = await call(tool, action="load", slug="procedure")
    assert not loaded.is_error and "SECRET_PROCEDURE_BODY" in loaded.content[0].text
    assert str(skills._directories["shared"]) in loaded.content[0].text
    assert loaded.details["activation"] == "tool-result-only"


@pytest.mark.parametrize(
    "args",
    [
        {"action": "create", "slug": "new"},
        {"action": "remove", "slug": "procedure"},
        {"action": "patch", "slug": "procedure"},
        {"action": "load"},
        {"action": "list", "slug": "procedure"},
        {"action": "load", "slug": "../private"},
        {"action": "load", "slug": "/etc/passwd"},
        {"action": "load", "slug": "a/b"},
        {"action": "load", "slug": "a\\b"},
        {"action": "load", "slug": "unknown"},
        {"action": "load", "slug": "procedure", "path": "/tmp"},
    ],
)
async def test_rejects_unknown_unsafe_or_mutating_requests(skills, args):
    before = (skills._directories["shared"] / "procedure/SKILL.md").read_bytes()
    result = await create_skill_tool(skills).execute("call", args)
    assert result.is_error and "SECRET_PROCEDURE_BODY" not in str(result.content)
    assert (skills._directories["shared"] / "procedure/SKILL.md").read_bytes() == before


async def test_allowlist_and_limits(skills):
    tool = create_skill_tool(skills, allowed_slugs=[])
    assert json.loads((await call(tool, action="list")).content[0].text)["skills"] == []
    assert (await call(tool, action="load", slug="procedure")).is_error
    assert (await call(create_skill_tool(skills, max_skill_bytes=10), action="load", slug="procedure")).is_error
    write_skill(skills._directories["shared"], "other", "SECOND_BODY")
    limited = await call(create_skill_tool(skills, max_listed_skills=1), action="list")
    assert json.loads(limited.content[0].text)["truncated"] is True


@pytest.mark.parametrize("kind", ["file_symlink", "directory_symlink", "hardlink", "fifo"])
async def test_no_follow_or_blocking_special_files(tmp_path, kind):
    outside = write_skill(tmp_path / "outside", body="PRIVATE_OUTSIDE")
    root = tmp_path / "local"
    root.mkdir()
    if kind == "directory_symlink":
        (root / "procedure").symlink_to(outside, target_is_directory=True)
    else:
        directory = root / "procedure"
        directory.mkdir()
        path = directory / "SKILL.md"
        if kind == "file_symlink":
            path.symlink_to(outside / "SKILL.md")
        elif kind == "hardlink":
            os.link(outside / "SKILL.md", path)
        else:
            os.mkfifo(path)
    tool = create_skill_tool(SkillRepo(builtin=False, packaged={}).register(directory=root, name="shared"))
    async with asyncio.timeout(2):
        result = await call(tool, action="load", slug="procedure")
    assert result.is_error and "PRIVATE_OUTSIDE" not in str(result.content)


async def test_unsafe_shadow_does_not_fall_back_to_lower_tier(tmp_path):
    shared = tmp_path / "shared"
    write_skill(shared, body="LOWER_BODY")
    group = tmp_path / "group"
    group.mkdir()
    repo = SkillRepo(builtin=False).register(directory=shared).register(directory=group)
    (group / "procedure").symlink_to(shared / "procedure", target_is_directory=True)
    result = await call(create_skill_tool(repo), action="load", slug="procedure")
    assert result.is_error and "LOWER_BODY" not in str(result.content)


@pytest.mark.parametrize("cancel_via", ["task", "event"])
async def test_cancellation_waits_for_original_reader(skills, monkeypatch, cancel_via):
    from xdog.agent.skills.tool import _Reader

    started, released = threading.Event(), threading.Event()
    original = _Reader.load

    def held(self, slug, stopped):
        started.set()
        assert released.wait(3)
        return original(self, slug, stopped)

    monkeypatch.setattr(_Reader, "load", held)
    cancel = asyncio.Event()
    tool = create_skill_tool(skills)
    task = asyncio.create_task(tool.execute("call", {"action": "load", "slug": "procedure"}, cancel))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        if cancel_via == "task":
            task.cancel()
        else:
            cancel.set()
        await asyncio.sleep(0.01)
        assert not task.done()
    finally:
        released.set()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("deny", [False, True])
async def test_actual_model_tool_roundtrip_obeys_permission_hook(skills, deny):
    calls = []

    def stream(model, context, options):
        calls.append(context)
        msg = (
            AssistantMessage(
                content=(ToolCall(id="load", name="skill", arguments={"action": "load", "slug": "procedure"}),),
                stop_reason="toolUse",
            )
            if len(calls) == 1
            else AssistantMessage()
        )
        return EventStream.empty(msg)

    async def permission(ctx, cancel):
        return BeforeToolCallResult(block=deny, reason="host denied")

    agent = Agent(
        stream,
        config=AgentConfig(model="offline/test"),
        skills=skills,
        tools=[create_skill_tool(skills)],
        before_tool_call=permission,
    )
    async for _ in await agent.prompt("Use an available skill"):
        pass
    assert len(calls) == 2
    result = next(m for m in calls[1].messages if isinstance(m, ToolResultMessage))
    text = "".join(p.text for p in result.content if isinstance(p, TextContent))
    assert ("SECRET_PROCEDURE_BODY" in text) is not deny
    assert result.is_error is deny
    assert agent.active_skills == ()
    assert "SECRET_PROCEDURE_BODY" not in str(agent.state.system_prompt)


def test_index_never_promises_unconfigured_tool(skills):
    agent = Agent(lambda *a: EventStream.empty(AssistantMessage()), skills=skills)
    assert "Use the skill tool" not in agent.state.system_prompt
    assert not agent.state.tools


@pytest.mark.parametrize(
    "kwargs",
    [
        {"allowed_slugs": "procedure"},
        {"allowed_slugs": ["../secret"]},
        {"max_skill_bytes": True},
        {"max_skill_bytes": 0},
        {"max_listed_skills": 0},
    ],
)
def test_invalid_configuration_fails_explicitly(skills, kwargs):
    with pytest.raises(ValueError):
        create_skill_tool(skills, **kwargs)


@pytest.mark.parametrize("slug", ["procedure\n", "procedure\r", "procedure\x00", "x" * 65, "a--b"])
async def test_invalid_slug_never_starts_reader(skills, monkeypatch, slug):
    from xdog.agent.skills.tool import _Reader

    def forbidden(*args):
        pytest.fail("Invalid names must fail before filesystem reads")

    monkeypatch.setattr(_Reader, "load", forbidden)
    result = await call(create_skill_tool(skills), action="load", slug=slug)
    assert result.is_error
    with pytest.raises(ValueError, match="valid skill names"):
        create_skill_tool(skills, allowed_slugs=[slug])


async def test_precancelled_tool_does_not_start_reader(skills, monkeypatch):
    from xdog.agent.skills.tool import _Reader

    def forbidden(*args):
        pytest.fail("Pre-cancelled reads must not start a worker")

    monkeypatch.setattr(_Reader, "load", forbidden)
    monkeypatch.setattr(_Reader, "listing", forbidden)
    cancel = asyncio.Event()
    cancel.set()
    tool = create_skill_tool(skills)
    for args in ({"action": "list"}, {"action": "load", "slug": "procedure"}):
        with pytest.raises(asyncio.CancelledError):
            await tool.execute("cancelled", args, cancel)


async def test_repeated_cancellation_settles_worker_before_return(skills, monkeypatch):
    from xdog.agent.skills.tool import _Reader

    started, released, finished = threading.Event(), threading.Event(), threading.Event()
    original = _Reader.listing

    def held(self, stopped):
        started.set()
        try:
            assert released.wait(3)
            return original(self, stopped)
        finally:
            finished.set()

    monkeypatch.setattr(_Reader, "listing", held)
    task = asyncio.create_task(call(create_skill_tool(skills), action="list"))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done() and not finished.is_set()
    finally:
        released.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert finished.is_set()


@pytest.mark.parametrize("invalid", ["utf8", "oversize"])
async def test_bad_shadow_not_read_from_lower_tier_and_list_reports_unavailable(tmp_path, invalid):
    shared, group = tmp_path / "shared", tmp_path / "group"
    write_skill(shared, body="LOWER_BODY")
    group.mkdir()
    repo = SkillRepo(builtin=False).register(directory=shared).register(directory=group)
    directory = write_skill(group)
    (directory / "SKILL.md").write_bytes(b"\xff" if invalid == "utf8" else b"X" * 1025)
    tool = create_skill_tool(repo, max_skill_bytes=1024)
    loaded = await call(tool, action="load", slug="procedure")
    listed = json.loads((await call(tool, action="list")).content[0].text)
    assert loaded.is_error and "LOWER_BODY" not in str(loaded.content)
    assert listed == {"skills": [], "unavailable": ["procedure"], "truncated": False}


async def test_missing_tier_falls_back_to_packaged_without_creating_roots(tmp_path):
    directory = write_skill(tmp_path / "package", body="PACKAGED_BODY")
    shared, group = tmp_path / "shared", tmp_path / "group"
    manager = (
        SkillRepo(builtin=False, packaged={"procedure": directory})
        .register(directory=shared, name="shared")
        .register(directory=group, name="group")
    )
    result = await call(create_skill_tool(manager), action="load", slug="procedure")
    assert not result.is_error and result.details["packaged"]
    assert "PACKAGED_BODY" in str(result.content)
    assert not shared.exists() and not group.exists()


async def test_loading_prose_never_executes_commands_or_reads_references(tmp_path):
    marker = tmp_path / "should-not-exist"
    directory = write_skill(tmp_path / "skills", body=f"Run: touch {marker}\nRead: ${{SKILL_DIR}}/private.txt")
    reference = directory / "private.txt"
    reference.write_text("REFERENCE_MUST_NOT_BE_LOADED")
    script = directory / "run.sh"
    script.write_text(f"touch {marker}\n")
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    result = await call(
        create_skill_tool(SkillRepo(builtin=False, packaged={}).register(directory=directory.parent, name="shared")),
        action="load",
        slug="procedure",
    )
    assert not result.is_error and "touch" in str(result.content)
    assert "REFERENCE_MUST_NOT_BE_LOADED" not in str(result.content)
    assert not marker.exists()
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}


async def test_output_expansion_is_bounded(skills):
    (skills._directories["shared"] / "procedure/SKILL.md").write_text("${SKILL_DIR}" * 12000)
    result = await call(create_skill_tool(skills), action="load", slug="procedure")
    assert result.is_error and "output limit" in result.content[0].text


async def test_directory_scan_is_bounded_without_loading_files(skills, monkeypatch):
    from xdog.agent.skills.tool import _Reader

    class Entry:
        name = "ignored"

        def is_dir(self, *, follow_symlinks):
            assert follow_symlinks is False
            return False

    class Scan:
        def __enter__(self):
            return iter([Entry()] * 4097)

        def __exit__(self, *args):
            pass

    def forbidden(*args):
        pytest.fail("Oversized catalogs must be rejected before any skill body read")

    monkeypatch.setattr(os, "scandir", lambda *args: Scan())
    monkeypatch.setattr(_Reader, "load", forbidden)
    result = await call(create_skill_tool(skills), action="list")
    assert result.is_error and "bounded catalog scan" in result.content[0].text
