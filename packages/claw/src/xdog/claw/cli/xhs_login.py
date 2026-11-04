"""Explicit optional-SDK login; no separately configured HTTP backend."""
from __future__ import annotations

import asyncio
from pathlib import Path

import click
from xdog.claw.config import ClawConfig


def run(config: ClawConfig, config_file: Path, group_id: str) -> None:
    import yaml
    from xdog.claw.channels.xhs.login import login

    raw = yaml.safe_load(config_file.read_text()) if config_file.exists() else {}
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise click.ClickException('Invalid config; no account or configuration was changed')
    if (config.groups and group_id not in {group.id for group in config.groups}
            or not config.groups and group_id != 'main'):
        raise click.ClickException('Configure the target group before binding its channel')
    try:
        account = asyncio.run(login(Path(config.data_dir), group_id))
    except (ValueError, OSError, TimeoutError) as exc:
        raise click.ClickException(str(exc)) from exc
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(yaml.safe_dump({**raw, 'xhs_enabled': True, 'xhs_group_id': group_id}, sort_keys=False),
                           encoding='utf-8')
    click.echo(f'XHS account {account.account_id} bound to group {group_id}. No comment or post was sent.')
    click.echo('Replies and publishing remain independently configured; restart gateway to load this binding.')
