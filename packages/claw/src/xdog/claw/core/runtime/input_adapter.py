"""Host-owned durable projection; payload provenance never selects authority or input kind."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from xdog.agent.session_input import SessionInput, render_input
from xdog.claw.core.identity import IdentityResolver, InputIdentity, MessageSource
from xdog.claw.core.inputs.models import InputEnvelope, InputScope, ReplyTarget
from xdog.claw.core.types import (
    ContextContinuation,
    ExternalInput,
    GroupInput,
    SystemInput,
    SystemInputKind,
    UserInput,
)


def freeze_input(value: GroupInput, workspace: Path, *, message_id: str,
                 admission_key: str = '', reply: ReplyTarget | None = None,
                 identity: InputIdentity | None = None, identity_binding: str = 'local',
                 reply_mode: str = 'stream') -> InputEnvelope:
    # This is a trusted host API. Actual channel callbacks pass their resolved
    # identity explicitly; metadata and submitted canonical provenance never do.
    if identity is None:
        if isinstance(value, ExternalInput):
            identity = IdentityResolver.system(MessageSource('acp'))
            identity_binding = 'system'
        elif isinstance(value, UserInput) and value.metadata.get('input_origin', 'user') == 'user':
            identity = IdentityResolver.local(MessageSource(value.channel or 'local'))
        else:
            identity = IdentityResolver.system(MessageSource('internal'))
            identity_binding = 'system'
    if identity_binding not in {'local', 'channel', 'system'}:
        raise ValueError('Unknown host identity binding')
    if reply_mode not in {'stream', 'final', 'none'}:
        raise ValueError('Unknown host channel reply mode')
    human = identity.human_authorized
    if isinstance(value, ExternalInput) and human:
        raise ValueError('External input cannot carry management authority')
    kind = ('external' if isinstance(value, ExternalInput) else 'continuation' if isinstance(value, ContextContinuation)
            else 'system' if isinstance(value, SystemInput) else 'user')
    context: dict[str, Any] = {'kind': kind, 'metadata': value.metadata,
                               'empty_text': not bool(value.content),
                               'identity': identity.to_dict(), 'identity_binding': identity_binding,
                               'reply_mode': reply_mode}
    if isinstance(value, UserInput):
        context.update(sender=value.sender, channel=value.channel, timestamp=value.timestamp)
        # External origin/channel labels never select a chat destination. Only
        # an explicit host reply binding may route an automatic input result.
        if reply is None and not isinstance(value, ExternalInput) and value.channel and value.sender:
            reply = ReplyTarget(value.channel, value.sender)
    else:
        context['system_kind'] = value.kind.value
    if isinstance(value, ContextContinuation):
        context['record_id'] = value.record_id
    blank_image = (isinstance(value, UserInput) and bool(value.images)
                   and bool(value.content) and not value.content.strip())
    instruction = '[Blank image input]\n' + value.content if blank_image else value.content
    if blank_image:
        context['blank_image_text'] = True
    message = (replace(value.canonical, id=message_id) if isinstance(value, ExternalInput) else
        SessionInput(message_id, 'human' if identity.principal.kind == 'human' else 'automatic',
                     (value.channel or 'User') if isinstance(value, UserInput) else value.kind.value,
                     instruction or '[Saved input continuation or image]', (), {}))
    return InputEnvelope(InputScope(value.group_id, workspace), message, reply,
        value.images if isinstance(value, UserInput) else (), human_authorized=human,
        admission_key=admission_key, context=context)


def restore_input(value: InputEnvelope) -> GroupInput:
    context = value.context
    kind, metadata = context.get('kind'), context.get('metadata')
    if not isinstance(kind, str) or not isinstance(metadata, dict):
        raise ValueError('Input is missing its host metadata projection')
    content = '' if context.get('empty_text') is True else value.message.instruction
    if context.get('blank_image_text') is True:
        if kind != 'user' or not value.images or not content.startswith('[Blank image input]\n'):
            raise ValueError('Invalid saved image-only text projection')
        content = content[len('[Blank image input]\n'):]
    raw_identity = context.get('identity')
    identity = InputIdentity.from_dict(raw_identity) if raw_identity is not None else None
    if identity is not None and value.human_authorized != identity.human_authorized:
        raise ValueError('Saved identity does not match host authorization')
    common: dict[str, Any] = {'group_id': value.scope.group_id, 'content': content, 'metadata': metadata,
                              'identity': identity}
    if kind in {'user', 'external'}:
        # Routed external messages reconstruct an original ReplyReference,
        # whose routing contract has no timestamp. Omit that redundant zero in
        # the stored context to preserve the existing64KiB host metadata bound.
        default_timestamp = 0 if kind == 'external' and 'output' in context else None
        sender, channel = context.get('sender'), context.get('channel')
        timestamp = context.get('timestamp', default_timestamp)
        if (not isinstance(sender, str) or not isinstance(channel, str)
                or not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool)):
            raise ValueError('Invalid saved input channel projection')
        common.update(sender=sender, channel=channel, timestamp=timestamp, images=value.images)
        if kind == 'external':
            if value.human_authorized:
                raise ValueError('External input cannot acquire human control authority')
            common['content'] = render_input(value.message)
            return ExternalInput(**common, canonical=value.message)
        if identity is None and value.human_authorized != (metadata.get('input_origin', 'user') == 'user'):
            raise ValueError('Saved input authority does not match the host binding')
        return UserInput(**common)
    if value.human_authorized:
        raise ValueError('Automatic input cannot acquire human control authority')
    if kind == 'continuation':
        record_id = context.get('record_id')
        if not isinstance(record_id, str) or not record_id or len(record_id.encode()) > 256 or '\x00' in record_id:
            raise ValueError('Invalid saved context continuation binding')
        return ContextContinuation(**common, record_id=record_id)
    if kind == 'system':
        system_kind = context.get('system_kind')
        if not isinstance(system_kind, str):
            raise ValueError('Invalid automatic input kind')
        return SystemInput(**common, kind=SystemInputKind(system_kind))
    raise ValueError('Unknown host input projection; no input kind inferred from payload')


def same_channel_submission(original: InputEnvelope, offered: InputEnvelope) -> bool:
    """ACK-loss retries keep original host policy, never recapture broader grants.

    Platform text/images/sender/account/context and the original admission owner
    still match exactly. Role/reply policy are host snapshots, not a new
    submitted message; the executor revalidates the original grant independently.
    """
    if (original.scope != offered.scope or original.message != offered.message or original.reply != offered.reply
            or original.images != offered.images or original.admission_key != offered.admission_key):
        return False
    old, new = original.context, offered.context
    policy = {'identity', 'identity_binding', 'reply_mode'}
    if ({key: value for key, value in old.items() if key not in policy}
            != {key: value for key, value in new.items() if key not in policy}):
        return False
    old_identity, new_identity = old.get('identity'), new.get('identity')
    if old_identity is not None and new_identity is not None:
        return InputIdentity.from_dict(old_identity).source == InputIdentity.from_dict(new_identity).source
    return old_identity is None  # An accepted legacy row stays legacy/unexecutable, never upgraded.
