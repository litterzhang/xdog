"""Installed CLI identity must match the published distribution, not a legacy name."""
from importlib.metadata import version

from click.testing import CliRunner
from xdog.claw.cli.cli import cli


def test_claw_cli_version_matches_its_installed_distribution():
    result = CliRunner().invoke(cli, ['--version'])
    assert result.exit_code == 0
    assert result.output.strip() == f'xdog-claw, version {version("xdog-claw")}'
