"""Two-phase live image return on the child runtime loop with explicit host or tool ownership.

Preparation copies bytes without removing a queued input. Commitment requires the
same owner/revision/token and the verified byte receipt, then atomically removes
only the still-unselected original input. This is application admission, not an
OS transaction, permission grant or active-crash durability guarantee.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any, cast

from xdog.agent.agent import Agent
from xdog.agent.child_queue import snapshot
from xdog.agent.child_transfer import prepare_recovery
from xdog.agent.input_queue import QueuedInput
from xdog.ai.types import ImageContent

RETURN_TIMEOUT = 30.0
MAX_RETURN_LEASES = 4


@dataclass(frozen=True)
class ReturnLease:
    token: str
    owner: str
    revision: str
    row: QueuedInput
    receipt_json: str
    deadline: float


class LiveImageReturns:
    """Only call from the child's owning loop; snapshots never borrow generic tool authority."""

    def __init__(self) -> None:
        self.leases: dict[str, ReturnLease] = {}
        self.completed: dict[str, tuple[str, str, float, dict[str, Any]]] = {}
        self.preparing = 0
        self._preparing_rows: dict[object, QueuedInput] = {}

    def retained_images(self) -> tuple[ImageContent, ...]:
        """Include copies still owned by actual byte work, even after queue removal."""
        self.prune()
        return tuple(image for row in (*self._preparing_rows.values(),
                                       *(lease.row for lease in self.leases.values())) for image in row.images)

    def clear(self) -> None:
        """Retire settled-child receipts without releasing in-flight preparation ownership."""
        self.leases.clear()
        self.completed.clear()

    def prune(self) -> None:
        now = time.monotonic()
        self.leases = {key: value for key, value in self.leases.items() if value.deadline > now}
        self.completed = {key: value for key, value in self.completed.items() if value[2] > now}

    @staticmethod
    def _owner(owner: str) -> None:
        if not isinstance(owner, str) or not 0 < len(owner) <= 128:
            raise ValueError('Live image return requires an explicit host/connection owner')

    def current(self, agent: Agent | None, child: str, status: str, token: str, owner: str) -> bool:
        self.prune()
        lease = self.leases.get(token)
        if agent is None or status != 'running' or lease is None or lease.owner != owner:
            return False
        before = snapshot(agent, child, status)
        row = next((row for row in agent.pending_inputs() if row.id == lease.row.id), None)
        return before['revision'] == lease.revision and row is lease.row and not row.selected

    async def prepare(self, agent: Agent, child: str, status: str, command: dict[str, Any],
                      owner: str) -> dict[str, Any]:
        self._owner(owner)
        self.prune()
        if self.preparing + len(self.leases) + len(self.completed) >= MAX_RETURN_LEASES:
            raise ValueError('Live image return capacity reached; cancel or wait for existing leases')
        before = snapshot(agent, child, status)
        if status != 'running' or command['revision'] != before['revision']:
            raise ValueError('Child queue changed/stopped; refresh before preparing image return')
        row = next((row for row in agent.pending_inputs() if row.id == command['id']), None)
        if row is None or row.selected or not row.images:
            raise ValueError('Live image return requires a still-queued unselected image input')
        # Reserve before yielding: concurrent requests must not all pass the
        # capacity check while validation/hash work is still in flight.
        self.preparing += 1
        reservation = object()
        self._preparing_rows[reservation] = row
        try:
            payload = await prepare_recovery(row)
            # Preparation can yield to queue consumption/edit/cancellation. No removal
            # or lease is admitted unless the exact immutable row survived that work.
            current = snapshot(agent, child, status)
            actual = next((value for value in agent.pending_inputs() if value.id == row.id), None)
            if current['revision'] != before['revision'] or actual is not row or row.selected or not agent.is_streaming:
                raise ValueError('Child queue changed during byte preparation; original not removed')
            token = uuid.uuid4().hex
            receipt_json = json.dumps(payload['receipt'], sort_keys=True)
            lease = ReturnLease(token, owner, before['revision'], row, receipt_json,
                                time.monotonic() + RETURN_TIMEOUT)
            self.leases[token] = lease
            public = next(value for value in before['inputs'] if value['id'] == row.id)
            return {'queue': {**before, 'return_prepared': public, 'return_token': token,
                              'image_receipt': json.loads(receipt_json)}, 'image_recovery': payload}
        finally:
            self._preparing_rows.pop(reservation)
            self.preparing -= 1

    def planned_commit(self, agent: Agent | None, child: str, status: str, command: dict[str, Any],
                       owner: str) -> tuple[QueuedInput, ...] | None:
        """Validate before disk work; exact completed retries need no new journal write."""
        self._owner(owner)
        self.prune()
        fingerprint = json.dumps(command, sort_keys=True)
        completed = self.completed.get(command['token'])
        if completed is not None:
            if completed[0] != owner or completed[1] != fingerprint:
                raise ValueError('Live return receipt replay owner/content mismatch')
            return None
        lease = self.leases.get(command['token'])
        if (lease is None or lease.row.id != command['id'] or lease.revision != command['revision']
                or json.dumps(command['receipt'], sort_keys=True) != lease.receipt_json
                or not self.current(agent, child, status, command['token'], owner)):
            raise ValueError('Live image return expired/changed/unverified; original not removed, no replay')
        assert agent is not None
        return tuple(replace(row, selected=False) for row in agent.pending_inputs() if row.id != lease.row.id)

    def commit(self, agent: Agent | None, child: str, status: str, command: dict[str, Any],
               owner: str) -> dict[str, Any]:
        self._owner(owner)
        self.prune()
        token = command['token']
        fingerprint = json.dumps(command, sort_keys=True)
        completed = self.completed.get(token)
        if completed is not None:
            if completed[0] != owner or completed[1] != fingerprint:
                raise ValueError('Live return receipt replay owner/content mismatch')
            # Exact retry returns a copied receipt, never removes twice.
            return cast(dict[str, Any], json.loads(json.dumps(completed[3])))
        lease = self.leases.get(token)
        if (lease is None or lease.row.id != command['id'] or lease.revision != command['revision']
                or json.dumps(command['receipt'], sort_keys=True) != lease.receipt_json
                or not self.current(agent, child, status, token, owner)):
            raise ValueError('Live image return expired/changed/unverified; original not removed, no replay')
        assert agent is not None
        before = snapshot(agent, child, status)
        public = next(row for row in before['inputs'] if row['id'] == lease.row.id)
        if not agent.change_input(lease.row.id, 'remove'):
            raise ValueError('Child input selected/consumed before image removal; no draft return')
        result = {'queue': {**snapshot(agent, child, status), 'returned': public, 'return_token': token,
                            'image_receipt': json.loads(lease.receipt_json)}}
        self.leases.pop(token)
        self.completed[token] = owner, fingerprint, lease.deadline, result
        return cast(dict[str, Any], json.loads(json.dumps(result)))

    def cancel(self, command: dict[str, Any], owner: str) -> None:
        self._owner(owner)
        self.prune()
        lease = self.leases.get(command['token'])
        if (lease is None or lease.owner != owner or lease.row.id != command['id']
                or lease.revision != command['revision']):
            raise ValueError('Unknown/foreign live image return lease or input/revision')
        self.leases.pop(command['token'])
