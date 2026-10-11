"""Offline actual CLI/RPC bootstrap; no authentication, model network or dependency installation."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import xdog.ai as ai
from xdog.ai.types import (
    AssistantMessage,
    DoneEvent,
    ImageContent,
    Model,
    StartEvent,
    TextContent,
    TextDeltaEvent,
    ToolCall,
)
from xdog.ai.utils.event_stream import EventStream


class Provider:
    def model(self, name):
        return Model(id='rpc-fixture', name='rpc-fixture', context_window=200000,
                     input=('text', 'image') if os.environ.get('XDOG_RPC_VISION') == '1' else ('text',))

    def models(self):
        return (self.model('rpc-fixture'),)

    def stream(self, model, context, options):
        async def generate():
            marker = Path(os.environ['XDOG_RPC_MARKER'])
            marker.write_text('entered')
            if os.environ.get('XDOG_RPC_COUNT_FILE'):
                counter = Path(os.environ['XDOG_RPC_COUNT_FILE'])
                counter.write_text(str(int(counter.read_text()) + 1 if counter.exists() else 1))
            if 'hold' in str(context.messages[-1].content):
                try:
                    yield StartEvent(partial=AssistantMessage())
                    if os.environ.get('XDOG_RPC_STREAM_PARTIAL') == '1':
                        yield TextDeltaEvent(delta='RPC-LIVE-PARTIAL',
                            partial=AssistantMessage(content=(TextContent(text='RPC-LIVE-PARTIAL'),)))
                        marker.with_suffix('.partial').write_text('partial-admitted')
                    if os.environ.get('XDOG_RPC_TRANSIENT_FLOOD') == '1':
                        for _ in range(128):
                            yield TextDeltaEvent(delta='progress', partial=AssistantMessage(content=(
                                TextContent(text='FLOOD-PROGRESS-' + 'x' * 16000),)))
                            await asyncio.sleep(0)
                        marker.with_suffix('.flood').write_text('flood-admitted')
                    await asyncio.Event().wait()
                finally:
                    marker.with_suffix('.settled').write_text('producer-settled')
            else:
                content = context.messages[-1].content
                images = sum(isinstance(part, ImageContent) for part in content) if not isinstance(content, str) else 0
                answer = f'RPC-IMAGES-{images}' if images else 'RPC-OFFLINE-ANSWER'
                message = (AssistantMessage(content=(ToolCall(id='cli-child-write', name='filesystem', arguments={
                    'action': 'write', 'path': 'cli-child-target', 'content': 'CHILD-ONLY-CONSENT'}),))
                    if (os.environ.get('XDOG_RPC_CHILD_WRITE') == '1'
                        and context.messages[-1].role == 'user' and 'child-write' in str(content)) else
                    AssistantMessage(content=(TextContent(text=answer),)))
                if (os.environ.get('XDOG_RPC_CHILD_CUSTOM') == '1'
                        and context.messages[-1].role == 'user'):
                    message = AssistantMessage(content=(ToolCall(id='cli-child-custom',
                        name=next(tool.name for tool in context.tools if tool.name.startswith('ext_custom_view')),
                        arguments={}),))
                if (os.environ.get('XDOG_RPC_CHILD_OVERLAY') == '1'
                        and context.messages[-1].role == 'user'):
                    message = AssistantMessage(content=(ToolCall(id='cli-child-overlay',
                        name=next(tool.name for tool in context.tools if tool.name.startswith('ext_overlaymock_view')),
                        arguments={'mode': 'auto'}),))
                if (os.environ.get('XDOG_RPC_CHILD_LAYOUT') == '1'
                        and context.messages[-1].role == 'user'):
                    message = AssistantMessage(content=(ToolCall(id='cli-child-layout',
                        name=next(tool.name for tool in context.tools if tool.name.startswith('ext_layout_view')),
                        arguments={'mode': 'scroll'}),))
                stream.set_result(message)
                yield DoneEvent(message=message)
        stream = EventStream(generate())
        return stream


ai.provider = lambda name: Provider()
ai.load = lambda: Provider()
if __name__ == '__main__':
    if os.environ.get('XDOG_RPC_CANCELED_SEND_MARKER'):
        from xdog.coding.modes.rpc.output import RpcOutput
        original_send = RpcOutput.send
        async def tracked_send(self, event):
            if event.get('type') == 'extension_result' and event.get('canceled') is True:
                Path(os.environ['XDOG_RPC_CANCELED_SEND_MARKER']).write_text('canceled-send-entered')
            return await original_send(self, event)
        RpcOutput.send = tracked_send
    from xdog.coding.main import main
    sys.argv = ['xdog-coding', '--rpc', '--model', 'rpc-fixture', '--permission-mode',
                os.environ.get('XDOG_RPC_PERMISSION', 'deny')]
    main()
