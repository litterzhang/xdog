"""Historical v2 fixtures: production APIs must never create a new wait(host)."""
import hashlib
import time
from dataclasses import replace

from xdog.skills.goal.codec import encode_goal, json_text
from xdog.skills.goal.host import OperationContext
from xdog.skills.goal.transitions import transition_data


async def wait_goal(service, identifier='', *, until='user', **values):
    if until != 'host':
        return await service.change('wait', identifier, until=until, **values)
    before = await service.current()
    assert before is not None and (not identifier or identifier == before.id)
    context = values.pop('context', OperationContext(origin='runtime'))
    version = values.pop('version', before.version)
    assert version == before.version
    after = replace(before, version=before.version + 1, status='waiting', wait_for='host',
                    updated_at=time.time(), reason=values.pop('reason', ''), question='', impact='', resume_when='')
    assert not values
    payload = encode_goal(after)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    provenance = json_text(transition_data('wait', context, before, after))
    def write(db):
        db.execute('UPDATE goals SET version=?,payload=?,digest=? WHERE id=?',
                   (after.version, payload, digest, after.id))
        db.execute('INSERT INTO revisions VALUES(?,?,?,?)', (after.id, after.version, payload, digest))
        db.execute('INSERT INTO transitions VALUES(?,?,?,?)',
                   (after.id, after.version, provenance, hashlib.sha256(provenance.encode()).hexdigest()))
        db.execute("UPDATE reminders SET status='cancelled' WHERE goal_id=? AND status IN ('pending','unavailable')",
                   (after.id,))
    await service.store.database.disk(write)
    service.on_change()
    return after
