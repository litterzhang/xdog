"""Display an existing login PNG, without decoding its payload or creating a QR."""
from __future__ import annotations

import shutil
from pathlib import Path

import click
from PIL import Image, ImageOps


def _module_grid(image: Image.Image) -> Image.Image | None:
    """Recover an axis-aligned QR grid from its three finder/timing patterns.

    This samples the supplied symbol, never decodes/replaces its challenge.
    One module per terminal pixel is compact without fractional module widths.
    """
    ink = image.point(lambda value: 255 if value < 128 else 0)
    bounds = ink.getbbox()
    if bounds is None:
        return None
    cropped = image.crop(bounds)
    if cropped.width != cropped.height:
        return None
    run = 0
    first_row = cropped.tobytes()[:cropped.width]
    while run < cropped.width and first_row[run] < 128:
        run += 1
    for size in range(21, min(177, cropped.width) + 1, 4):
        if abs(7 * cropped.width / size - run) > 1:
            continue
        grid = cropped.resize((size, size), Image.Resampling.NEAREST)
        pixels = grid.tobytes()
        corners = ((0, 0), (size - 7, 0), (0, size - 7))
        if not all(
            (pixels[(top + y) * size + left + x] < 128)
            == (x in (0, 6) or y in (0, 6) or 2 <= x <= 4 and 2 <= y <= 4)
            for left, top in corners for y in range(7) for x in range(7)
        ):
            continue
        if all((pixels[6 * size + i] < 128) == (i % 2 == 0)
               and (pixels[i * size + 6] < 128) == (i % 2 == 0) for i in range(8, size - 8)):
            return grid
    return None


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
    image = image.convert('L')
    grid = _module_grid(image)
    if grid is not None:
        if grid.width + 8 > columns:
            raise ValueError('Terminal is too narrow for this QR; open the saved PNG')
        image = grid
    else:
        width = min(image.width, columns - 10, 72)
        height = max(1, round(image.height * width / image.width))
        image = image.resize((width, height), Image.Resampling.NEAREST)
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
    click.echo('Use the latest QR promptly; starting another login replaces the previous scan session.')
    try:
        click.echo(render_png(path, shutil.get_terminal_size((80, 24)).columns), color=True)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        # Do not echo arbitrary decoder/backend exceptions or QR payloads.
        click.echo('Terminal QR unavailable; open the saved PNG instead.', err=True)
        if isinstance(exc, ValueError) and str(exc).startswith('Terminal is too narrow'):
            click.echo('Widen the terminal or open the saved PNG; QR modules cannot be squeezed safely.', err=True)
    click.echo(f'Scan the XHS QR image: {path}')
