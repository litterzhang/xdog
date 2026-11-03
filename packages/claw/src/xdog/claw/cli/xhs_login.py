"""Interactive channel command, separate from the gateway's resource lifetime."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import click
import httpx
from xdog.claw.config import ClawConfig


async def confirm_scan() -> None:
    """Wait for a line without a blocking input thread or browser/status polling."""
    click.echo('After confirming successful login in the XHS app, press Enter here. Ctrl+C cancels.')
    stream = click.get_text_stream('stdin')
    try:
        fd = stream.fileno()
    except (OSError, ValueError) as exc:
        raise ValueError('XHS login needs an interactive terminal for scan confirmation') from exc
    loop = asyncio.get_running_loop()
    confirmed: asyncio.Future[None] = loop.create_future()
    def readable() -> None:
        if confirmed.done():
            return
        try:
            chunk = os.read(fd, 4096)
            if not chunk:
                raise ValueError('XHS scan confirmation input closed; no account was bound')
            if b'\n' in chunk or b'\r' in chunk:
                confirmed.set_result(None)
        except (OSError, ValueError) as exc:
            confirmed.set_exception(exc)
    try:
        loop.add_reader(fd, readable)
    except (OSError, ValueError) as exc:
        raise ValueError('XHS login needs an interactive terminal for scan confirmation') from exc
    try:
        await confirmed
        # The backend's existing scan browser saves cookies after detecting the
        # app confirmation; do not race that background commit with verification.
        await asyncio.sleep(1)
    finally:
        loop.remove_reader(fd)


def run(config: ClawConfig, config_file: Path, group_id: str, base_url: str) -> None:
    import yaml
    from xdog.claw.channels.xhs.api import XhsApi
    from xdog.claw.channels.xhs.login import login
    from xdog.claw.cli.qr_terminal import show_png

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
                                  show_png, confirm_scan=confirm_scan)
            return account.account_id
        finally:
            await api.close()
    try:
        account_id = asyncio.run(perform())
    except httpx.HTTPError as exc:
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {401, 403}:
            raise click.ClickException(
                'XHS backend access denied (HTTP ' + str(exc.response.status_code) + '). '
                'Export the correct backend token in ' + config.xhs_token_env + ' before retrying. '
                'This is backend authentication, not QR expiry; existing cookies and channel '
                'configuration were retained.'
            ) from exc
        detail = f'HTTP {exc.response.status_code}' if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
        raise click.ClickException('XHS backend login request failed (' + detail + '). '
            'The scan may still be completing; keep existing cookies and retry login to recheck status. '
            'No channel configuration was enabled by this failed attempt.') from exc
    except (ValueError, OSError, TimeoutError) as exc:
        raise click.ClickException(str(exc)) from exc
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(yaml.safe_dump({**raw, 'xhs_enabled': True, 'xhs_group_id': group_id,
                                         'xhs_base_url': endpoint}, sort_keys=False), encoding='utf-8')
    click.echo(f'XHS account {account_id} bound to group {group_id}. No comment or post was sent.')
    click.echo('Replies and publishing remain independently configured; restart gateway to load this binding.')
