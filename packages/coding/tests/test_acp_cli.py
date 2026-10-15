from click.testing import CliRunner


def test_attach_conflicts_are_checked_before_sdk(monkeypatch):
    from xdog.coding.cli.args import cli
    from xdog.coding.core import sdk

    def forbidden(*args, **kwargs):
        raise AssertionError("attach must not construct another session")

    monkeypatch.setattr(sdk, "create_agent_session", forbidden)
    result = CliRunner().invoke(cli, ["--session", "s1", "--resume-id", "s1", "--prompt", "Review"])
    assert result.exit_code == 2
    assert "--session cannot be combined with --resume-id" in result.output


def test_attach_requires_prompt_or_acp():
    from xdog.coding.cli.args import cli

    result = CliRunner().invoke(cli, ["--session", "s1"])
    assert result.exit_code == 2
    assert "--prompt or --acp" in result.output
