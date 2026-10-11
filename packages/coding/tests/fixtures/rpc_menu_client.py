"""Actual Coding CLI with a private model fixture that invokes only the menu reader."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import xdog.ai as ai
from xdog.ai.types import AssistantMessage, Model, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream


class Provider:
    def model(self, name):
        return Model(id='menu-fixture', context_window=200000)

    def models(self):
        return (self.model('menu-fixture'),)

    def stream(self, model, context, options):
        with Path(os.environ['MENU_CALLS']).open('a') as output:
            output.write('owned-model-step\n')
        if context.messages[-1].role == 'user':
            tool = next(tool for tool in context.tools if 'menureader' in tool.name)
            message = AssistantMessage(content=(ToolCall(id='owned-menu-tool', name=tool.name, arguments={}),),
                                       stop_reason='toolUse')
        else:
            message = AssistantMessage(content=(TextContent(text='MENU-CLI-DONE'),))
        return EventStream.empty(message)


ai.provider = lambda name: Provider()
ai.load = lambda: Provider()
if __name__ == '__main__':
    from xdog.coding.main import main
    sys.argv = ['xdog-coding', '--rpc', '--model', 'menu-fixture', '--permission-mode', 'allow-all']
    main()
