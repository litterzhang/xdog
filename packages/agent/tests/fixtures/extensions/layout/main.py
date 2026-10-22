import json

from xdog.tui.components.custom_layout import (
    CustomBox,
    CustomChild,
    CustomColumn,
    CustomComponent,
    CustomLayer,
    CustomRow,
    CustomStack,
    CustomText,
    CustomViewport,
    custom_frame,
)
from xdog.tui.components.input_panel import InputPanel
from xdog.tui.keys import KeyEvent, KeyEventType


class Theme:
    accent = bold = dim = border = staticmethod(lambda value: value)


def setup(api):
    calls = 0

    async def command(args, context):
        nonlocal calls
        calls += 1
        editor = InputPanel(Theme())
        editor.focused = True
        value, nested = None, ''
        layered = False
        frames = 0
        scrolling = 'scroll' in args.split()
        top, left = 0, 0
        document = '\n'.join(f'ROW-{row:04d} ' + '.' * 40 + f'PAN-{row:04d} 界👩\u200d💻'
                             for row in range(320))
        def submitted(text):
            nonlocal value
            value = text
        editor.on_submit = submitted

        def render(width, height):
            nonlocal frames
            frames += 1
            editor.set_render_budget(max(1, height - 4), show_borders=False)
            layout = CustomColumn((
                CustomChild(CustomText('\x1b[36mSTRUCTURED-LAYOUT\x1b[0m\x1b]52;c;unsafe\a'), size=1),
                CustomChild(CustomRow((
                    CustomChild(CustomBox(CustomComponent(editor), title='Draft · 编辑')),
                    CustomChild(CustomBox(CustomViewport(CustomText(document), 128, 320, left=left, top=top)
                              if scrolling else CustomText('Side status\n' + nested, wrap=True), title='Info'), size=16)
                ), gap=1)),
            ), gap=1)
            if layered:
                popup = CustomBox(CustomText(f'LAYER-POPUP W{width} R{frames}'), title='Menu')
                layout = CustomStack(layout, (CustomLayer(popup, top=2, width=min(32, width), height=3),))
            return custom_frame(layout, width, height)

        async def on_input(event):
            nonlocal nested, top, left, layered
            if event['kind'] == 'paste':
                if not layered:
                    editor.handle_paste(event['text'])
            elif event['key'] == 'f5':
                layered = not layered
                editor.focused = not layered
            elif scrolling and event['key'] in ('pagedown', 'pageup', 'f3', 'f4'):
                if event['key'] == 'pagedown':
                    top = min(319, top + 20)
                elif event['key'] == 'pageup':
                    top = max(0, top - 20)
                elif event['key'] == 'f3':
                    left = 49
                else:
                    top, left = 0, 0
            elif event['key'] == 'f2':
                nested = await api.input('Nested layout question', default='nested draft') or 'canceled'
            elif not layered:
                editor.handle_input(KeyEvent(event['key'], ctrl=event['ctrl'], alt=event['alt'],
                    shift=event['shift'], event_type=KeyEventType[event.get('event_type', 'press').upper()]))
            if value is not None:
                return {'done': True, 'value': {'text': value, 'nested': nested}}
            return None

        placement = ({'width': '75%', 'max_height': 12, 'anchor': 'top-right', 'margin': 1}
                     if 'overlay' in args.split() else None)
        result = await api.custom('Layout fixture', render, on_input, fullscreen='full' in args.split(),
                                  overlay=placement, timeout=300)
        return json.dumps({'value': result, 'calls': calls})

    api.command('view', 'Structured worker-owned layout', command)
    async def tool(params, context):
        return await command(params.get('mode', ''), context)
