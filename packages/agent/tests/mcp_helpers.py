"""Shared inert model adapter, owned peer fixture and bounded async test waits."""
from __future__ import annotations

import asyncio
import sys

from xdog.ai.types import AssistantMessage, DoneEvent, Model, TextContent
from xdog.ai.utils.event_stream import EventStream


class Adapter:
    def __init__(self, message=None):
        self.calls = []
        self.message = message or AssistantMessage(content=(TextContent(text='MODEL OUTPUT'),))

    def resolve_model(self, name):
        return Model(id=name, context_window=32000, input=('text', 'image'))

    def __call__(self, model, context, options):
        self.calls.append((model, context, options))
        message = self.message
        async def events():
            yield DoneEvent(message=message)
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(events(), result_future=future)


def reverse_peer_config(tmp_path):
    peer, log = tmp_path / 'peer.py', tmp_path / 'frames'
    peer.write_text('''import json,sys
from pathlib import Path
log=Path(sys.argv[1])
def send(row): print(json.dumps(row),flush=True)
for raw in sys.stdin:
    row=json.loads(raw)
    with log.open('a') as f:f.write(json.dumps(row)+'\\n')
    method=row.get('method')
    if method=='server/discover':
        send({'jsonrpc':'2.0','id':row['id'],'error':{'code':-32601,'message':'legacy'}})
    elif method=='initialize':
        send({'jsonrpc':'2.0','id':row['id'],'result':{
            'protocolVersion':'2025-11-25','capabilities':{'tools':{}}}})
    elif method=='tools/list':
        send({'jsonrpc':'2.0','id':'sample','method':'sampling/createMessage','params':{
            'messages':[{'role':'user','content':{'type':'text','text':'SERVER INPUT'}}],'maxTokens':20}})
        send({'jsonrpc':'2.0','id':row['id'],'result':{'tools':[]}})
    elif method=='ping':
        send({'jsonrpc':'2.0','id':row['id'],'result':{}})
''')
    return {'owned': {'command': sys.executable, 'args': [str(peer), str(log)], 'timeout': 5}}, log


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.002)
