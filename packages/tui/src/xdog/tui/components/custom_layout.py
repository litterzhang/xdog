"""Bounded worker-side custom layouts. Only ordinary frame data reaches a host.

These helpers live in the standalone TUI package; agent never imports TUI or
executes plugin components in its UI thread. Use custom_frame() from an owned
extension render callback and return its existing lines/cursor frame protocol.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from xdog.tui.components.extension_custom import _SGR, _clip, _plain_offsets, safe_custom_line
from xdog.tui.editor_layout import grapheme_clusters
from xdog.tui.tui import CURSOR_MARKER, Component
from xdog.tui.utils import pad_right, string_width, wrap_text_with_ansi

_RESET = '\x1b[0m'


@dataclass(frozen=True, slots=True)
class CustomText:
    text: str
    wrap: bool = False
    align: Literal['left', 'center', 'right'] = 'left'


@dataclass(frozen=True, slots=True)
class CustomSpacer:
    pass


@dataclass(frozen=True, slots=True)
class CustomComponent:
    """Adapt a worker-owned Component, extracting its ONE cursor as frame data.

    The caller retains state/focus and handles input in the worker. No component
    object, callback or private host cursor marker is serialized to a client.
    """
    component: Component


@dataclass(frozen=True, slots=True)
class CustomChild:
    content: CustomLayout
    size: int | None = None
    weight: int = 1


@dataclass(frozen=True, slots=True)
class CustomRow:
    children: tuple[CustomChild, ...]
    gap: int = 0


@dataclass(frozen=True, slots=True)
class CustomColumn:
    children: tuple[CustomChild, ...]
    gap: int = 0


@dataclass(frozen=True, slots=True)
class CustomPadding:
    content: CustomLayout
    top: int = 0
    right: int = 0
    bottom: int = 0
    left: int = 0


@dataclass(frozen=True, slots=True)
class CustomBox:
    content: CustomLayout
    title: str = ''


@dataclass(frozen=True, slots=True)
class CustomViewport:
    """Scroll a bounded virtual canvas using worker-owned display-cell offsets.

    Rebuild with new offsets after input; no host scroll state or callbacks cross
    the frame protocol. Resize clamps offsets to the last full visible canvas.
    """
    content: CustomLayout
    width: int
    height: int
    left: int = 0
    top: int = 0


@dataclass(frozen=True, slots=True)
class CustomLayer:
    """Opaque worker-owned rectangle; coordinates are display cells/rows."""
    content: CustomLayout
    left: int = 0
    top: int = 0
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True, slots=True)
class CustomStack:
    """Compose in-view popups without borrowing a host overlay/focus handle."""
    background: CustomLayout
    layers: tuple[CustomLayer, ...] = ()


CustomLayout: TypeAlias = (CustomText | CustomSpacer | CustomComponent | CustomRow
                          | CustomColumn | CustomPadding | CustomBox | CustomViewport | CustomStack)


@dataclass(frozen=True, slots=True)
class _Frame:
    lines: tuple[str, ...] = ()
    cursor: tuple[int, int] | None = None


def _integer(value: int, maximum: int) -> bool:
    return type(value) is int and 0 <= value <= maximum


def _validate(root: CustomLayout) -> None:
    count, size, canvas = 0, 0, 0
    def visit(node: CustomLayout, path: frozenset[int]) -> None:
        nonlocal count, size, canvas
        count += 1
        if count > 256 or len(path) >= 12 or id(node) in path:
            raise ValueError('Custom layout exceeds node/depth bounds or contains a cycle')
        path = path | {id(node)}
        if isinstance(node, CustomText):
            if (not isinstance(node.text, str) or type(node.wrap) is not bool
                    or node.align not in ('left', 'center', 'right')):
                raise ValueError('Invalid custom layout text')
            size += len(node.text.encode())
        elif isinstance(node, CustomSpacer):
            pass
        elif isinstance(node, CustomComponent):
            if not isinstance(node.component, Component):
                raise ValueError('Custom adapter requires a worker-owned Component')
        elif isinstance(node, (CustomRow, CustomColumn)):
            if (not isinstance(node.children, tuple) or len(node.children) > 32
                    or not _integer(node.gap, 64)):
                raise ValueError('Invalid custom layout children/gap')
            for child in node.children:
                if (not isinstance(child, CustomChild) or child.size is not None and not _integer(child.size, 10000)
                        or not _integer(child.weight, 256) or child.weight == 0):
                    raise ValueError('Invalid fixed/flexible layout slot')
                visit(child.content, path)
        elif isinstance(node, CustomPadding):
            if any(not _integer(value, 64) for value in (node.top, node.right, node.bottom, node.left)):
                raise ValueError('Invalid custom layout padding')
            visit(node.content, path)
        elif isinstance(node, CustomViewport):
            if (not _integer(node.width, 8192) or not node.width
                    or not _integer(node.height, 4096) or not node.height
                    or not _integer(node.left, node.width) or not _integer(node.top, node.height)
                    or node.width * node.height > 128 * 1024):
                raise ValueError('Invalid/excessive custom viewport canvas or offset')
            canvas += node.width * node.height
            if canvas > 512 * 1024:
                raise ValueError('Custom viewports exceed aggregate canvas budget')
            visit(node.content, path)
        elif isinstance(node, CustomStack):
            if not isinstance(node.layers, tuple) or len(node.layers) > 32:
                raise ValueError('Invalid/excessive custom stack layers')
            visit(node.background, path)
            for layer in node.layers:
                if (not isinstance(layer, CustomLayer) or not _integer(layer.left, 10000)
                        or not _integer(layer.top, 4096)
                        or layer.width is not None and not _integer(layer.width, 10000)
                        or layer.height is not None and not _integer(layer.height, 4096)):
                    raise ValueError('Invalid custom layer rectangle')
                visit(layer.content, path)
        elif isinstance(node, CustomBox):
            if not isinstance(node.title, str):
                raise ValueError('Invalid custom layout title')
            size += len(node.title.encode())
            visit(node.content, path)
        else:
            raise ValueError('Unknown custom layout node')
        if size > 128 * 1024:
            raise ValueError('Custom layout text exceeds retention budget')
    visit(root, frozenset())


def _allocate(children: tuple[CustomChild, ...], space: int) -> list[int]:
    slots, remaining = [0] * len(children), space
    for index, child in enumerate(children):
        if child.size is not None:
            slots[index] = min(remaining, child.size)
            remaining -= slots[index]
    flexible = [index for index, child in enumerate(children) if child.size is None]
    total = sum(children[index].weight for index in flexible)
    if total:
        for index in flexible:
            slots[index] = remaining * children[index].weight // total
        left = remaining - sum(slots[index] for index in flexible)
        # Stable largest-remainder allocation; no floating point or starvation.
        for index in sorted(flexible, key=lambda i: -(remaining * children[i].weight % total))[:left]:
            slots[index] += 1
    return slots


def _merge_cursor(old: tuple[int, int] | None, new: tuple[int, int] | None) -> tuple[int, int] | None:
    if old is not None and new is not None:
        raise ValueError('Custom layout has multiple focused cursors')
    return new if new is not None else old


def _line(text: str, width: int) -> str:
    return _clip(safe_custom_line(text), width) + _RESET


def _slice_cells(line: str, left: int, width: int) -> str:
    # Treat SGR embedded between an emoji/base/accent as styling, not a glyph
    # boundary. Carry prior styles and replace cut clusters with their cells.
    plain, offsets = _plain_offsets(line)
    output: list[str] = []
    column, started = 0, False
    for start, end, cluster in grapheme_clusters(plain):
        following = column + string_width(cluster)
        if column >= left + width:
            break
        if following > left:
            begin = offsets[start]
            finish = offsets[end] if end < len(offsets) else len(line)
            if not started:
                output.extend(match[0] for match in _SGR.finditer(line[:begin]))
                started = True
            span = line[begin:finish]
            if column < left or following > left + width:
                output.extend(match[0] for match in _SGR.finditer(span))
                output.append(' ' * (min(left + width, following) - max(left, column)))
            else:
                output.append(span)
        column = following
    return ''.join(output)


def _render(node: CustomLayout, width: int, height: int) -> _Frame:
    if not width or not height:
        return _Frame()
    if isinstance(node, CustomSpacer):
        return _Frame(tuple(' ' * width for _ in range(height)))
    if isinstance(node, CustomText):
        text = '\n'.join(safe_custom_line(line) for line in node.text.split('\n'))
        lines = wrap_text_with_ansi(text, width) if node.wrap else text.split('\n')
        output = []
        for line in lines[:height]:
            line = _line(line, width)
            spare = max(0, width - string_width(line))
            start = spare if node.align == 'right' else spare // 2 if node.align == 'center' else 0
            output.append(' ' * start + line)
        return _Frame(tuple(output))
    if isinstance(node, CustomComponent):
        node.component.set_height(height)
        lines = node.component.render(width)
        if (not isinstance(lines, list) or len(lines) > max(200, height)
                or any(not isinstance(line, str) for line in lines)):
            raise ValueError('Invalid/excessive worker component output')
        if sum(len(line.encode()) for line in lines) > 512 * 1024:
            raise ValueError('Worker component exceeds retained canvas budget')
        cursor = None
        markers = 0
        output = []
        for row, line in enumerate(lines):
            if len(line.encode()) > 8192 or '\n' in line or '\r' in line:
                raise ValueError('Invalid worker component line')
            if CURSOR_MARKER in line:
                markers += line.count(CURSOR_MARKER)
                if markers != 1:
                    raise ValueError('Worker component has multiple cursor markers')
                # Markers are source boundaries, not independently measurable
                # prefixes: a marker inside e+accent/ZWJ/keycap must round to the
                # glyph's left edge before viewport/box/layer translation.
                prefix, _ = _plain_offsets(safe_custom_line(line.split(CURSOR_MARKER)[0]))
                plain, _ = _plain_offsets(safe_custom_line(line.replace(CURSOR_MARKER, '')))
                point = 0
                for _start, end, cluster in grapheme_clusters(plain):
                    if len(prefix) < end:
                        break
                    point += string_width(cluster)
                if row < height and point < width:
                    cursor = (row, point)
            if row < height:
                output.append(_line(line.replace(CURSOR_MARKER, ''), width))
        return _Frame(tuple(output), cursor)
    if isinstance(node, CustomViewport):
        frame = _render(node.content, node.width, node.height)
        if (len(frame.lines) > node.height or any(len(line.encode()) > 8192 for line in frame.lines)
                or sum(len(line.encode()) for line in frame.lines) > 512 * 1024):
            raise ValueError('Custom viewport exceeds retained canvas budget')
        left = min(node.left, max(0, node.width - width))
        top = min(node.top, max(0, node.height - height))
        visible = tuple(_line(_slice_cells(line, left, width), width)
                       for line in frame.lines[top:top + height])
        cursor = None
        if frame.cursor is not None:
            row, column = frame.cursor[0] - top, frame.cursor[1] - left
            if 0 <= row < height and 0 <= column < width:
                cursor = (row, column)
        return _Frame(visible, cursor)
    if isinstance(node, CustomStack):
        background = _render(node.background, width, height)
        composed = [pad_right(background.lines[row] if row < len(background.lines) else '', width)
                    for row in range(height)]
        cursor = background.cursor
        for layer in node.layers:
            if layer.left >= width or layer.top >= height:
                continue  # Hidden layers cannot invoke components or acquire a cursor.
            layer_w = min(width - layer.left, layer.width if layer.width is not None else width - layer.left)
            layer_h = min(height - layer.top, layer.height if layer.height is not None else height - layer.top)
            if not layer_w or not layer_h:
                continue
            front = _render(layer.content, layer_w, layer_h)
            if cursor is not None and (layer.top <= cursor[0] < layer.top + layer_h
                                       and layer.left <= cursor[1] < layer.left + layer_w):
                cursor = None  # An opaque rectangle hides the underlying cursor too.
            if front.cursor is not None:
                cursor = _merge_cursor(cursor, (front.cursor[0] + layer.top, front.cursor[1] + layer.left))
            for row in range(layer_h):
                base = composed[layer.top + row]
                line = front.lines[row] if row < len(front.lines) else ''
                composed[layer.top + row] = (_slice_cells(base, 0, layer.left) + _RESET
                    + pad_right(line, layer_w) + _RESET
                    + _slice_cells(base, layer.left + layer_w, width - layer.left - layer_w) + _RESET)
        return _Frame(tuple(composed), cursor)
    if isinstance(node, CustomPadding):
        left, top = min(width, node.left), min(height, node.top)
        inner_w, inner_h = max(0, width - left - node.right), max(0, height - top - node.bottom)
        frame = _render(node.content, inner_w, inner_h)
        output = [' ' * width] * top
        output.extend(pad_right(' ' * left + line, width) for line in frame.lines)
        output.extend(' ' * width for _ in range(max(0, height - len(output))))
        return _Frame(tuple(output), (frame.cursor[0] + top, frame.cursor[1] + left) if frame.cursor else None)
    if isinstance(node, CustomBox):
        if width < 2 or height < 2:
            return _Frame(tuple('─' * width for _ in range(height)))
        frame = _render(node.content, width - 2, height - 2)
        title = _clip(safe_custom_line(node.title).replace('\n', ' '), max(0, width - 4))
        caption = (' ' + title + ' ') if title and width >= 4 else ''
        output = ['┌' + caption + _RESET + '─' * max(0, width - 2 - string_width(caption)) + '┐']
        output.extend('│' + pad_right(frame.lines[row] if row < len(frame.lines) else '', width - 2) + '│'
                      for row in range(height - 2))
        output.append('└' + '─' * (width - 2) + '┘')
        return _Frame(tuple(output), (frame.cursor[0] + 1, frame.cursor[1] + 1) if frame.cursor else None)
    if isinstance(node, (CustomRow, CustomColumn)):
        horizontal = isinstance(node, CustomRow)
        space = width if horizontal else height
        gap = min(node.gap, space // max(1, len(node.children) - 1))
        sizes = _allocate(node.children, max(0, space - gap * max(0, len(node.children) - 1)))
        output = [] if not horizontal else [''] * height
        cursor, offset = None, 0
        for index, (child, size) in enumerate(zip(node.children, sizes, strict=True)):
            frame = _render(child.content, size if horizontal else width, height if horizontal else size)
            if frame.cursor:
                position = ((frame.cursor[0], frame.cursor[1] + offset) if horizontal
                         else (frame.cursor[0] + offset, frame.cursor[1]))
                cursor = _merge_cursor(cursor, position)
            if horizontal:
                for row in range(height):
                    output[row] += pad_right(frame.lines[row] if row < len(frame.lines) else '', size)
                    if index < len(node.children) - 1:
                        output[row] += ' ' * gap
            else:
                output.extend(frame.lines)
                output.extend(' ' * width for _ in range(size - len(frame.lines)))
                if index < len(node.children) - 1:
                    output.extend(' ' * width for _ in range(gap))
            offset += size + gap
        return _Frame(tuple(output), cursor)
    raise ValueError('Unknown custom layout')


def custom_frame(layout: CustomLayout, width: int, height: int) -> dict[str, object]:
    """Render bounded immutable composition into the existing custom-view wire frame.

    Dimensions/callback deadlines are host-owned. Empty/hidden viewports don't
    call component renderers. All emitted controls are sanitized; only a single
    cursor coordinate may cross the wire, never a component or host marker.
    """
    if not _integer(width, 10000) or not _integer(height, 200):
        raise ValueError('Invalid custom layout viewport')
    _validate(layout)
    frame = _render(layout, width, height)
    lines = [_line(line, width) for line in frame.lines]
    if (len(lines) > height or any(len(line.encode()) > 8192 for line in lines)
            or sum(len(line.encode()) for line in lines) > 128 * 1024):
        raise ValueError('Custom layout exceeds frame output limits')
    return {'lines': lines, 'cursor': list(frame.cursor) if frame.cursor is not None else None}
