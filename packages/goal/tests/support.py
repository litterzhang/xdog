"""Only the external host boundary is controlled; Goal storage/state are real."""

from __future__ import annotations

import asyncio


class Host:
    def __init__(self, owner):
        self.owner = owner
        self.started = []
        self.reviewed = []
        self.cancelled = []
        self.children = {}
        self.accepted = {}
        self.enqueued = []
        self.start_entered = asyncio.Event()
        self.start_release = None
        self.before_start = None
        self.lose_enqueue_ack = False
        self.enqueue_entered = asyncio.Event()
        self.enqueue_release = None

    async def start_worker(self, spec):
        from xdog.goal.host import ChildStatus
        from xdog.goal.models import WorkerRef

        if self.before_start is not None:
            await self.before_start(spec)
        self.start_entered.set()
        if self.start_release is not None:
            await self.start_release.wait()
        ref = WorkerRef("child-" + spec.attempt_id, "execution-" + spec.attempt_id)
        self.started.append((spec, ref))
        self.children[ref] = ChildStatus(ref, "running")
        return ref

    async def start_reviewer(self, spec):
        self.reviewed.append(spec)
        raise AssertionError("This non-review test must never start a reviewer")

    async def child_status(self, ref):
        from xdog.goal.host import ChildStatus

        return self.children.get(ref, ChildStatus(ref, "unknown"))

    async def cancel_child(self, ref):
        from xdog.goal.host import ChildStatus

        self.cancelled.append(ref)
        self.children[ref] = ChildStatus(ref, "cancelled")

    def finish(self, ref, state="succeeded"):
        from xdog.goal.host import ChildStatus

        self.children[ref] = ChildStatus(ref, state)

    async def enqueue(self, notice):
        from xdog.goal.host import EnqueueReceipt

        self.enqueue_entered.set()
        if self.enqueue_release is not None:
            await self.enqueue_release.wait()
        previous = self.accepted.get(notice.id)
        if previous is not None and previous != notice:
            raise ValueError("Host canonical notice conflict")
        self.enqueued.append(notice)
        self.accepted[notice.id] = notice
        if self.lose_enqueue_ack:
            self.lose_enqueue_ack = False
            raise ConnectionError("Host committed but ACK lost")
        return EnqueueReceipt(self.owner.key, notice.id, "duplicate" if previous else "accepted")

    async def receipt(self, notice):
        from xdog.goal.host import EnqueueReceipt

        current = self.accepted.get(notice.id)
        if current is None:
            return None
        if current != notice:
            raise ValueError("Stored host notice content differs")
        return EnqueueReceipt(self.owner.key, notice.id, "accepted")
