import asyncio
from pathlib import Path


def setup(api):
    for event in ('session_start', 'turn_start', 'message_end', 'tool_end', 'turn_end'):
        async def record(context, phase=event):
            api.append_entry('phase', {'event': phase})
            if phase == 'turn_start' and api.settings.get('hold'):
                Path(api.settings['marker']).write_text('entered')
                await asyncio.Event().wait()
        api.on(event, record)
