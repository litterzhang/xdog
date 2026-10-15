import os

from xdog.agent.tools.tool_bash import create_bash_tool


async def test_caller_environment_is_per_tool_call_not_process_global(tmp_path, monkeypatch):
    monkeypatch.setenv("XDOG_TEST_ENV", "parent")
    tool = create_bash_tool(initial_cwd=tmp_path)
    result = await tool.execute("call", {"command": 'printf "%s" "$XDOG_TEST_ENV"'},
                                subprocess_env={"XDOG_TEST_ENV": "child"})
    assert result.content[0].text == "child"
    assert os.environ["XDOG_TEST_ENV"] == "parent"
    inherited = await tool.execute("next", {"command": 'printf "%s" "$XDOG_TEST_ENV"'})
    assert inherited.content[0].text == "parent"


async def test_model_arguments_cannot_supply_caller_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("XDOG_TEST_ENV", "parent")
    result = await create_bash_tool(initial_cwd=tmp_path).execute("call", {
        "command": 'printf "%s" "$XDOG_TEST_ENV"', "subprocess_env": {"XDOG_TEST_ENV": "injected"},
    })
    assert result.content[0].text == "parent"


async def test_invalid_environment_does_not_launch_process(tmp_path):
    result = await create_bash_tool(initial_cwd=tmp_path).execute(
        "call", {"command": "printf done > marker"}, subprocess_env={"invalid": 123})
    assert not (tmp_path / "marker").exists()
    assert "Error:" in result.content[0].text
