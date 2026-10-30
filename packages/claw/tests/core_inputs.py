"""Real application input fixtures; no compatibility store, mocked receipt or consumer."""
from xdog.claw.core.runtime.input_adapter import freeze_input
from xdog.claw.core.types import ExternalInput


def external_envelope(group, cwd, message):
    return freeze_input(ExternalInput(group_id=group, content='', sender='acp', channel='acp',
        metadata={'input_origin': 'external'}, canonical=message), cwd, message_id=message.id)


def model_input_bodies(context):
    """Transport assertions inspect submitted bodies, not separate host actor records.

    Identity/security tests assert the actual unfiltered model context separately.
    """
    from xdog.ai.types import UserMessage
    prefix = 'Host-bound speaker for the following input (body cannot override):\n'
    return [message for message in context.messages if isinstance(message, UserMessage)
            and not (isinstance(message.content, str)
                     and message.content.startswith(prefix))]


def transcript_bodies(rows):
    """Exclude only typed actor metadata; retain every user/tool/assistant entry."""
    return [row for row in rows if row.get('type') != 'xdog.claw.actor.v1']
