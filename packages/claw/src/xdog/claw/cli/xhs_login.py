"""Interactive channel command, separate from the gateway's resource lifetime."""
from __future__ import annotations

import asyncio
from pathlib import Path

import click
from xdog.claw.config import ClawConfig


def run(config: ClawConfig, config_file: Path, group_id: str, base_url: str) -> None:
    import yaml
    from xdog.claw.channels.xhs.api import XhsApi
    from xdog.claw.channels.xhs.login import login

    # Refuse to overwrite invalid config. Existing unrelated keys are preserved.
    raw = yaml.safe_load(config_file.read_text()) if config_file.exists() else {}
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise click.ClickException('Invalid config; no account or configuration was changed')
    if (config.groups and group_id not in {group.id for group in config.groups}
            or not config.groups and group_id != 'main'):
        raise click.ClickException('Configure the target group before binding its channel')
    endpoint = base_url or config.xhs_base_url

    async def perform() -> str:
        api = XhsApi(endpoint, token_env=config.xhs_token_env)
        try:
            account = await login(api, Path(config.data_dir), group_id,
                                  lambda path: click.echo(f'Scan the XHS QR image: {path}'))
            return account.account_id
        finally:
            await api.close()
    try:
        account_id = asyncio.run(perform())
    except (ValueError, OSError, TimeoutError) as exc:
        raise click.ClickException(str(exc)) from exc
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(yaml.safe_dump({**raw, 'xhs_enabled': True, 'xhs_group_id': group_id,
                                         'xhs_base_url': endpoint}, sort_keys=False), encoding='utf-8')
    click.echo(f'XHS account {account_id} bound to group {group_id}. No comment or post was sent.')
    click.echo('Replies and publishing remain independently configured; restart gateway to load this binding.')
