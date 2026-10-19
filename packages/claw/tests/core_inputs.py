"""Real application input fixtures; no compatibility store, mocked receipt or consumer."""
from xdog.claw.core.runtime.input_adapter import freeze_input
from xdog.claw.core.types import ExternalInput


def external_envelope(group, cwd, message):
    return freeze_input(ExternalInput(group_id=group, content='', sender='acp', channel='acp',
        metadata={'input_origin': 'external'}, canonical=message), cwd, message_id=message.id)
