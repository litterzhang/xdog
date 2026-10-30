"""Display an existing login PNG, without decoding its payload or creating a QR."""
from __future__ import annotations

import shutil
from pathlib import Path

import click
from PIL import Image, ImageOps


def render_png(path: Path, columns: int) -> str:
    """Use two square black/white pixels per cell; keep a white quiet border."""
    if columns < 48:
        raise ValueError('Terminal is too narrow; widen it or open the PNG')
    with Image.open(path, formats=['PNG']) as source:
        if source.width > 1024 or source.height > 1024:
            raise ValueError('QR image exceeds the terminal rendering limit')
        # Composite transparency on white rather than inventing black modules.
        image = Image.new('RGBA', source.size, 'white')
        image.alpha_composite(source.convert('RGBA'))
    width = min(image.width, columns - 10, 160)
    height = max(1, round(image.height * width / image.width))
    image = image.convert('L').resize((width, height), Image.Resampling.NEAREST)
    image = ImageOps.expand(image, border=4, fill=255)
    pixels = image.tobytes()
    rows = []
    for y in range(0, image.height, 2):
        cells = []
        for x in range(image.width):
            top = int(pixels[y * image.width + x] < 128)
            bottom = int(y + 1 < image.height and pixels[(y + 1) * image.width + x] < 128)
            cells.append((' ', '▀', '▄', '█')[top + 2 * bottom])
        # Explicit colors keep the QR usable on both light and dark terminals.
        rows.append('\x1b[30;47m' + ''.join(cells) + '\x1b[0m')
    return '\n'.join(rows)


def show_png(path: Path) -> None:
    """A display failure must not discard the saved challenge or restart login."""
    click.echo('Scan this QR code with Xiaohongshu:')
    try:
        click.echo(render_png(path, shutil.get_terminal_size((80, 24)).columns), color=True)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        # Do not echo arbitrary decoder/backend exceptions or QR payloads.
        click.echo('Terminal QR unavailable; open the saved PNG instead.', err=True)
        if isinstance(exc, ValueError) and str(exc).startswith('Terminal is too narrow'):
            click.echo('Widen the terminal to at least 48 columns.', err=True)
    click.echo(f'Scan the XHS QR image: {path}')
