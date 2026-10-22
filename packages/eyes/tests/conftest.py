import pytest


@pytest.fixture
def observer_data():
    return {
        "version": 2,
        "name": "review",
        "sources": [{"id": "files", "type": "file", "root": "."}],
        "detectors": [{"id": "changes", "type": "file.change", "source": "files"}],
        "handlers": [
            {
                "id": "notify",
                "type": "acp",
                "connection": {"transport": "stdio", "command": ["third-party-acp"]},
                "session": "session-1",
                "instruction": "Review tests",
                "cwd": ".",
            }
        ],
        "routes": [
            {
                "id": "review",
                "detector": "changes",
                "handler": "notify",
                "event_types": ["file.created", "file.modified", "file.deleted"],
            }
        ],
    }


@pytest.fixture(autouse=True)
async def isolated_daemon_directory(monkeypatch):
    """No test may start, stop or attach to a user's default Eyes daemon."""
    import asyncio
    import tempfile
    from pathlib import Path

    from xdog.eyes.control import request
    from xdog.eyes.local_control import daemon_location
    from xdog.eyes.process import reap_runtime

    with tempfile.TemporaryDirectory(prefix='eyes-suite-') as directory:
        monkeypatch.setenv('XDOG_EYES_RUNTIME_DIR', directory)
        location = daemon_location(Path(directory))
        try:
            yield Path(directory)
        finally:
            try:
                await request(location, 'daemon-stop', {})
            except (FileNotFoundError, ConnectionRefusedError):
                pass
            async with asyncio.timeout(10):
                while location.socket.exists():
                    await asyncio.sleep(.01)
            await asyncio.to_thread(reap_runtime, location, timeout=2)


@pytest.fixture
def host_skill_manager(tmp_path):
    """Manual host Agents still need the SDK's real skill placement inputs.

    Limit discovery to the real installed Goal skill. Explicit placement can
    resolve its body, but discovering/reading it does not activate it on an Agent.
    """
    from xdog.agent.skills import SkillManager, SkillPlacement, packaged_skills

    manager = SkillManager(tmp_path / 'shared-skills', group_dir=tmp_path / 'project-skills',
                           packaged={'goal': packaged_skills()['goal']})
    placement = SkillPlacement.read(manager, ('goal',))
    goal, = placement.resolved
    assert goal.packaged and goal.path.is_file() and goal.content.strip()
    assert goal.content in placement.fixed
    return manager


@pytest.fixture
def assert_host_goal_discoverable(host_skill_manager):
    """Goal remains discoverable without default body activation on automatic input."""
    from xdog.agent.skills import render_skill_body
    from xdog.ai.types import system_prompt_text

    goal = host_skill_manager.load_skill('goal')
    assert goal is not None
    body = render_skill_body(goal)

    def check(context):
        prompt = system_prompt_text(context.system_prompt) or ''
        assert body not in prompt
        assert '- `goal`' in prompt

    return check
