"""Read-only, bounded JSON projections shared by terminal and CLI clients."""

from __future__ import annotations

import json
from typing import Any

from xdog.coding.core.sessions.inbox_models import RESOLVED, EnqueueReceipt, InboxRecord
from xdog.coding.core.sessions.models import InputRecord


def receipt_data(receipt: EnqueueReceipt) -> dict[str, Any]:
    row = receipt.record
    return {'session_id': row.message.session_id, 'message_id': row.message.message_id,
            'status': 'duplicate' if receipt.duplicate else 'queued',
            'message_state': row.status, 'branch_id': row.branch_id}


def summary(row: InboxRecord, *, current_branch: str | None = None) -> dict[str, Any]:
    evidence = json.dumps(row.message.evidence[:3], ensure_ascii=True, allow_nan=False)
    return {'message_id': row.message.message_id, 'status': row.status, 'branch_id': row.branch_id,
            'attempt_id': row.attempt_id, 'request_id': row.request_id,
            'batch_id': row.batch_id, 'sequence': row.sequence,
            'blocked_reason': ('branch_changed' if current_branch is not None and row.branch_id != current_branch
                               else 'explicit_retry_required' if row.status == 'interrupted' else None),
            'source': row.message.source, 'instruction': row.message.instruction[:2048],
            'evidence_count': len(row.message.evidence),
            'evidence_preview': evidence[:3072],
            'truncated': len(row.message.instruction) > 2048 or len(row.message.evidence) > 3 or len(evidence) > 3072,
            'untrusted': True}


def visible(records: tuple[InboxRecord, ...], inputs: tuple[InputRecord, ...] = ()) -> tuple[InboxRecord, ...]:
    """Every unresolved message and at most 16 completed entries; input history supplies recency."""
    order = {row.input.id: index for index, row in enumerate(inputs)}
    pending = [row for row in records if row.status not in RESOLVED]
    terminal = [row for row in records if row.status in RESOLVED]
    terminal.sort(key=lambda row: (order.get(row.request_id, -1), row.message.message_id), reverse=True)
    return tuple(pending + terminal[:16])
