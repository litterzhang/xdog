"""Terminal display consumes the supplied PNG; tests never request a real login."""
from pathlib import Path

import pytest
from click.testing import CliRunner
from PIL import Image
from xdog.claw.cli.qr_terminal import render_png, show_png
from xdog.tui.utils import strip_ansi, visible_width


def test_rendered_half_blocks_preserve_pixels_transparency_and_quiet_zone(tmp_path: Path) -> None:
    path = tmp_path / 'qr.png'
    image = Image.new('RGBA', (9, 9), (0, 0, 0, 0))
    expected = {(x, y) for x in range(9) for y in range(9) if (x + y) % 3 == 0}
    for xy in expected:
        image.putpixel(xy, (0, 0, 0, 255))
    image.save(path)
    lines = render_png(path, 80).splitlines()
    pixels = set()
    for y, line in enumerate(lines):
        assert line.startswith('\x1b[30;47m') and line.endswith('\x1b[0m')
        for x, cell in enumerate(strip_ansi(line)):
            if cell in {'▀', '█'}:
                pixels.add((x, 2 * y))
            if cell in {'▄', '█'}:
                pixels.add((x, 2 * y + 1))
    assert pixels == {(x + 4, y + 4) for x, y in expected}
    assert path.is_file()  # Display is not the challenge file's lifecycle owner.


def test_large_png_fits_terminal_without_color_bleed_or_cropping(tmp_path: Path) -> None:
    path = tmp_path / 'qr.png'
    Image.new('L', (400, 400), 0).save(path)
    for width in (48, 80, 120, 500):
        lines = render_png(path, width).splitlines()
        assert all(visible_width(row) <= width for row in lines)
        assert all(row.endswith('\x1b[0m') for row in lines)
        assert all(not strip_ansi(row).strip() for row in lines[:2] + lines[-2:])


def test_cli_prints_png_and_fallback_path_without_creating_another_challenge(tmp_path, monkeypatch):
    import xdog.claw.channels.xhs.login as login_module
    import yaml
    from xdog.claw.channels.accounts import bind_account
    from xdog.claw.cli.cli import cli

    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump({'data_dir': str(tmp_path / 'data')}))
    shown = []
    async def fake_login(api, data_dir, group_id, show_qr, *, confirm_scan):
        qr = tmp_path / 'challenge.png'
        Image.new('L', (29, 29), 0).save(qr)
        shown.append(qr)
        show_qr(qr)
        return bind_account(data_dir, 'xhs', group_id, 'verified', 'Profile')
    monkeypatch.setattr(login_module, 'login', fake_login)
    result = CliRunner().invoke(cli, ['channel', 'login', '--xhs', '--config', str(path)])
    assert result.exit_code == 0, result.output
    assert 'Scan this QR code with Xiaohongshu:' in result.output
    assert '█' in result.output and str(shown[0]) in result.output
    saved = yaml.safe_load(path.read_text())
    assert saved['xhs_enabled'] is True
    assert not saved.get('xhs_replies_enabled') and not saved.get('xhs_publish_enabled')


@pytest.mark.parametrize('kind', ['narrow', 'malformed', 'oversized', 'not-png'])
def test_display_failure_retains_challenge_path_and_returns_to_login(tmp_path, monkeypatch, capsys, kind):
    import os

    path = tmp_path / 'challenge.png'
    if kind == 'malformed':
        path.write_bytes(b'\x89PNG\r\n\x1a\ninvalid')
    elif kind == 'not-png':
        Image.new('L', (29, 29), 0).save(path, format='GIF')
    else:
        Image.new('L', (1025, 1) if kind == 'oversized' else (29, 29), 0).save(path)
    monkeypatch.setattr('xdog.claw.cli.qr_terminal.shutil.get_terminal_size',
                        lambda fallback: os.terminal_size((30 if kind == 'narrow' else 80, 24)))
    before = path.read_bytes()
    show_png(path)
    out = capsys.readouterr()
    assert 'Terminal QR unavailable' in out.err and str(path) in out.out
    assert path.read_bytes() == before
    assert 'PNG' in out.err and 'Traceback' not in out.err


def terminal_pixels(text):
    return {(x, 2 * y + offset)
            for y, row in enumerate(text.splitlines())
            for x, cell in enumerate(strip_ansi(row))
            for offset in (0, 1)
            if cell in (('▀', '█') if offset == 0 else ('▄', '█'))}


@pytest.mark.parametrize('version', [1, 5, 9, 13])
@pytest.mark.parametrize('pixels', [128, 400])
def test_qr_grid_is_compact_and_preserves_every_original_module(tmp_path, version, pixels):
    import qrcode
    from PIL import ImageOps

    qr = qrcode.QRCode(version=version, box_size=1, border=0)
    qr.add_data('test-login')
    qr.make(fit=False)
    original = qr.get_matrix()
    image = qr.make_image().convert('RGBA').resize((pixels, pixels), Image.Resampling.NEAREST)
    # Transparent border is presentation, not QR data.
    image = ImageOps.expand(image, border=8, fill=(0, 0, 0, 0))
    path = tmp_path / 'fixture.png'
    image.save(path)
    rendered = render_png(path, 200)
    size = len(original)
    assert len(strip_ansi(rendered.splitlines()[0])) == size + 8
    assert len(rendered.splitlines()) == (size + 9) // 2
    assert terminal_pixels(rendered) == {
        (x + 4, y + 4) for y, row in enumerate(original) for x, black in enumerate(row) if black
    }
    # A wider terminal must not inflate the same QR.
    assert render_png(path, 500) == rendered


def test_dense_qr_is_not_squeezed_into_an_unscannable_terminal(tmp_path):
    import qrcode

    qr = qrcode.QRCode(version=9, box_size=3, border=4)
    qr.add_data('test-login')
    qr.make(fit=False)
    path = tmp_path / 'dense.png'
    qr.make_image().save(path)
    with pytest.raises(ValueError, match='too narrow for this QR'):
        render_png(path, 48)
