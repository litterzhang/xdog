"""Goal-owned notice reconciliation over the host's original-ID enqueue port."""

from __future__ import annotations

from typing import TYPE_CHECKING

from xdog.goal.host import EnqueueReceipt
from xdog.goal.models import Notice

if TYPE_CHECKING:
    from xdog.goal.service import GoalService


def validate(service: GoalService, notice: Notice, receipt: EnqueueReceipt) -> None:
    service._owner()
    if (
        not isinstance(receipt, EnqueueReceipt)
        or receipt.owner_key != service.store.owner.key
        or receipt.notice_id != notice.id
    ):
        raise ValueError("Goal notice receipt belongs to another owner or identity")


async def drain(service: GoalService) -> None:
    async with service._notices_lock:
        for row in await service.store.notices():
            goal = await service._get(row.notice.goal_id)
            if row.status not in {"pending", "unknown"}:
                continue
            if row.status == "unknown":
                try:
                    receipt = await service.host.receipt(row.notice)
                    if receipt is not None:
                        validate(service, row.notice, receipt)
                        if receipt.status in {"accepted", "duplicate"}:
                            await service.store.notice_settled(row.notice.id, "accepted", send_id=row.send_id)
                            continue
                except Exception:
                    # A failed read cannot authorize another transport attempt.
                    continue
            if not goal.notifications_enabled:
                continue
            try:
                sending = await service.store.notice_started(row.notice.id)
            except ValueError:
                current = next(item for item in await service.store.notices() if item.notice.id == row.notice.id)
                if current.status == "cancelled":
                    continue
                raise
            try:
                receipt = await service.host.enqueue(row.notice)
                validate(service, row.notice, receipt)
                state = (
                    "accepted"
                    if receipt.status in {"accepted", "duplicate"}
                    else "pending"
                    if receipt.status == "not-sent"
                    else "unknown"
                )
                await service.store.notice_settled(row.notice.id, state, send_id=sending.send_id)
            except Exception as exc:
                await service.store.notice_settled(row.notice.id, "unknown", str(exc), send_id=sending.send_id)
