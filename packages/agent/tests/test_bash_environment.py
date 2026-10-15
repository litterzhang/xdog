import os

from xdog.agent.tools.tool_bash import create_bash_tool


async def test_caller_environment_is_per_tool_call_not_process_global(tmp_path, monkeypatch):
    monkeypatch.setenv("XDOG_EVENT_CONTEXT", "parent")
    tool = create_bash_tool(initial_cwd=tmp_path)
    result = await tool.execute("call", {"command": 'printf "%s" "$XDOG_EVENT_CONTEXT"'},
                                subprocess_env={"XDOG_EVENT_CONTEXT": "child"})
    assert result.content[0].text == "child"
    assert os.environ["XDOG_EVENT_CONTEXT"] == "parent"
    inherited = await tool.execute("next", {"command": 'printf "%s" "$XDOG_EVENT_CONTEXT"'})
    assert inherited.content[0].text == "parent"


async def test_model_arguments_cannot_supply_caller_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("XDOG_EVENT_CONTEXT", "parent")
    result = await create_bash_tool(initial_cwd=tmp_path).execute("call", {
        "command": 'printf "%s" "$XDOG_EVENT_CONTEXT"', "subprocess_env": {"XDOG_EVENT_CONTEXT": "injected"},
    })
    assert result.content[0].text == "parent"


async def test_invalid_environment_does_not_launch_process(tmp_path):
    result = await create_bash_tool(initial_cwd=tmp_path).execute(
        "call", {"command": "printf done > marker"}, subprocess_env={"invalid": 123})
    assert not (tmp_path / "marker").exists()
    assert "Error:" in result.content[0].text
