"""Read-only message labels from host identities and existing output receipts.

These are display facts, never routing instructions or authorization. In particular,
a reply target is not delivery evidence and tool sends do not replace that target.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from xdog.claw.core.identity import InputIdentity
from xdog.claw.core.inputs.models import InputEnvelope
from xdog.claw.core.inputs.store import InputRecord
from xdog.claw.core.outputs.store import OutputRecord
from xdog.claw.core.runtime.display_events import display_result


def identity_metadata(identity: InputIdentity, *, group: str = '', input_id: str = '') -> dict[str, Any]:
    return {'role': identity.principal.display_role, 'principal': identity.principal.id,
            'source': identity.source.channel, 'sender': identity.source.sender_id,
            'account': identity.source.account_id, 'group': group, 'input_id': input_id}


def envelope_metadata(envelope: InputEnvelope) -> dict[str, Any]:
    raw = envelope.context.get('identity')
    result: dict[str, Any] = (identity_metadata(InputIdentity.from_dict(raw), group=envelope.scope.group_id,
                                input_id=envelope.message.id) if raw is not None else
              {'group': envelope.scope.group_id, 'input_id': envelope.message.id})
    reply = envelope.reply
    # ACP has no implicit chat recipient. Never invent a destination from source.
    result['destinations'] = ([{'channel': reply.channel, 'status': 'disabled' if
        envelope.context.get('reply_mode') == 'none' else 'not_recorded'}] if reply is not None else [])
    return result


def output_metadata(rows: Sequence[OutputRecord]) -> list[dict[str, str]]:
    return [{'channel': row.envelope.reply.channel, 'status': row.status, 'output_id': row.id}
            for row in rows if row.envelope.reply is not None]


def enrich_history(history: list[dict[str, Any]], records: Sequence[InputRecord],
                   outputs: Sequence[OutputRecord], session_id: str) -> None:
    """Join only exact original input/tool IDs; older unlabelled rows stay unknown."""
    inputs = {record.envelope.message.id: record for record in records if record.session_id == session_id}
    by_input: dict[str, list[OutputRecord]] = {}
    by_call: dict[tuple[str, str], list[OutputRecord]] = {}
    for row in outputs:
        provenance = row.envelope.message.provenance
        if row.envelope.context.get('outbound_tool') == 'message':
            record = inputs.get(str(provenance.get('input_id', '')))
            if record is None or row.attempt_id != record.attempt_id or provenance.get('worker'):
                continue
            key = (str(provenance.get('input_id', '')), str(provenance.get('tool_call_id', '')))
            by_call.setdefault(key, []).append(row)
        else:
            record = inputs.get(row.envelope.message.id)
            if record is not None and row.attempt_id == record.attempt_id:
                by_input.setdefault(row.envelope.message.id, []).append(row)
    for entry in history:
        metadata = entry.get('message_metadata', {})
        identifier = metadata.get('input_id', '')
        record = inputs.get(identifier)
        if record is None:
            continue
        metadata = envelope_metadata(record.envelope)
        if entry['role'] == 'assistant':
            content = entry.get('content', '')
            text = content if isinstance(content, str) else ''.join(
                part.get('text', '') for part in content if part.get('type') == 'text')
            # Progress prose must not borrow a later final reply's receipt.
            rows = [row for row in by_input.get(identifier, ()) if display_result(row.text) == text]
            if rows:
                metadata['destinations'] = output_metadata(rows)
        elif entry['role'] == 'toolResult':
            rows = by_call.get((identifier, entry.get('tool_call_id', '')), [])
            if rows:
                metadata['destinations'] = output_metadata(rows)
        entry['message_metadata'] = metadata
