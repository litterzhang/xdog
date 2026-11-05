"""Native child editors submit actual file bytes through Coding and the owned Claw gateway."""
from __future__ import annotations

import asyncio
import base64

from xdog.ai.types import AssistantMessage, Model, StartEvent, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.tui.keys import KeyEvent

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=')


class HeldVision:
    def __init__(self):
        self.calls = []

    def resolve_model(self, name):
        return Model(id=name, context_window=128000, input=('text', 'image'))

    def __call__(self, model, context, options):
        self.calls.append(context)
        async def events():
            yield StartEvent(partial=AssistantMessage(content=(TextContent(text='CHILD-HELD'),)))
            await asyncio.sleep(60)
        return EventStream.from_async_generator(events())


async def wait(ui, predicate):
    try:
        async with asyncio.timeout(10):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(0.003)
    except TimeoutError:
        panel = ui._child_panel
        raise AssertionError(f"UI wait failed: mode={panel._mode if panel else None}, "
            f"error={panel._error if panel else None}, queue={panel.queue._error if panel and panel.queue else None}, "
            f"pending={list(getattr(ui, '_children_pending', {}))}") from None


def review(panel):
    panel.render(120)
    panel.handle_input(KeyEvent('enter'))
    assert panel._mode == 'confirm'
    panel.handle_input(KeyEvent('enter'))  # No unpainted commitment.
    assert panel._mode == 'confirm'
    for _ in range(30):
        panel.render(120)
        if panel._visible:
            panel.handle_input(KeyEvent('enter'))
            return
        panel.handle_input(KeyEvent('pagedown'))
    raise AssertionError('Child image review never became fully painted')
