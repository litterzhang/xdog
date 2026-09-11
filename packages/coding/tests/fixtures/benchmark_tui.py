"""Offline component+ANSI benchmark. Run with the workspace Python interpreter."""
from __future__ import annotations

import json
from statistics import median
from time import perf_counter

from xdog.coding.modes.interactive.components.chat_log import ChatLog
from xdog.coding.modes.interactive.components.custom_editor import CustomEditorComponent
from xdog.coding.modes.interactive.components.footer import FooterComponent
from xdog.coding.modes.interactive.theme import create_default_theme
from xdog.tui.components.bounded_details import BoundedDetails
from xdog.tui.components.inline_layout import InlineLayout
from xdog.tui.main_buffer_renderer import MainBufferRenderer
from xdog.tui.tui import CURSOR_MARKER
from xdog.tui.utils import string_width


def benchmark(messages: int = 1000, frames: int = 300) -> dict[str, object]:
    theme = create_default_theme()
    chat = ChatLog(theme)
    for index in range(messages):
        chat.add_assistant(f"Message {index}: result and explanation.", thinking="Short reasoning.")
    tool = chat.add_tool("bash", {"command": "stream log"})
    footer = FooterComponent(theme)
    footer.set_activity("running bash")
    editor = CustomEditorComponent(theme)
    editor.set_focus(True)
    editor.set_text("persistent draft")
    layout = InlineLayout(chat, footer, editor, details=BoundedDetails(chat.detail_records))
    painter = MainBufferRenderer()
    log: list[str] = []
    samples: dict[str, list[float]] = {"stream": [], "resize": []}
    byte_counts: dict[str, list[int]] = {"stream": [], "resize": []}
    width, height = 100, 30
    clean: list[str] = []
    cursor = None
    for frame in range(frames):
        resized = frame % 25 == 0
        if resized:
            width, height = (60, 12) if width == 100 else (100, 30)
            painter.reanchor(min(height - 1, painter.state.cursor_row), painter.state.cursor_column)
        log.append(f"output line {frame}")
        started = perf_counter()
        tool.set_streaming("\n".join(log))
        layout.set_height(height)
        rows = layout.render(width)
        clean = []
        cursor = None
        for index, row in enumerate(rows):
            if CURSOR_MARKER in row:
                cursor = (index, string_width(row.split(CURSOR_MARKER)[0]))
            clean.append(row.replace(CURSOR_MARKER, ""))
        output = painter.render(clean, width, height, cursor, layout.transient_start)
        elapsed = (perf_counter() - started) * 1000
        kind = "resize" if resized else "stream"
        samples[kind].append(elapsed)
        byte_counts[kind].append(len(output.encode()))
        assert all(string_width(row) <= width for row in clean)
        assert cursor is not None
        assert 0 <= painter.state.origin + cursor[0] < height
    idle = painter.render(clean, width, height, cursor, layout.transient_start)
    assert idle == ""
    report: dict[str, object] = {"messages": messages, "frames": frames, "idle_bytes": len(idle)}
    for kind, timings in samples.items():
        ordered = sorted(timings)
        report[kind] = {
            "median_ms": round(median(timings), 3),
            "p95_ms": round(ordered[int((len(ordered) - 1) * 0.95)], 3),
            "max_ms": round(max(timings), 3),
            "median_output_bytes": median(byte_counts[kind]),
            "max_output_bytes": max(byte_counts[kind]),
        }
    return report


if __name__ == "__main__":
    print(json.dumps(benchmark(), indent=2))
