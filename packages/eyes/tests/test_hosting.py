"""Managed hosting is an independent feature on stdlib-only, injected ports."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from xdog.eyes.hosting.journal import ObserverJournal
from xdog.eyes.hosting.manager import ManagedObservers
from xdog.eyes.hosting.runtime import provider


class Database:
    """Injected transaction IO for feature tests, not a production lease owner."""
    def __init__(self, path, schema, validate, recover):
        self.path, self.closed = path, False
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript(schema)
        validate(self.db)
        recover(self.db)

    async def disk(self, operation):
        with self.db:
            return operation(self.db)

    async def close(self):
        if not self.closed:
            self.db.close()
            self.closed = True


class Host:
    def __init__(self, root):
        self.data_dir, self.endpoint = root, root / 'private.sock'
        self.opened, self.routes, self.listeners = [], {}, []
        self.resumed = 0
        self.receipts, self.attempts, self.outputs = {}, {}, []

    def workspace(self, owner):
        return self.data_dir if owner == 'owner' else None

    def validate_message(self, value):
        assert isinstance(value, dict) and set(value) == {
            'id', 'origin', 'source', 'instruction', 'evidence', 'provenance'}
        return json.loads(json.dumps(value, allow_nan=False))

    def validate_reply(self, owner, workspace, value):
        # Eyes must not interpret this host's private opaque reply representation.
        assert owner == 'owner' and workspace == self.data_dir
        if value != 'opaque!host-private-recipient':
            raise ValueError('Wrong original reply')
        return value

    def tool_scope(self, context):
        if context.get('group_id') != 'owner':
            raise ValueError('Unknown actual tool owner')
        return {'group_id': 'owner', 'workspace': str(self.data_dir)}

    def capture_reply(self, context):
        self.tool_scope(context)
        return 'opaque!host-private-recipient'

    async def open_database(self, path, **kwargs):
        assert kwargs['format'] == 'xdog.claw.observers.v3'
        database = Database(path, kwargs['schema'], kwargs['validate'], kwargs['recover'])
        self.opened.append(database)
        return database

    async def input_namespace(self, owner, workspace):
        return 'a' * 32

    async def input_get(self, owner, workspace, identifier):
        return self.receipts.get(identifier)

    async def input_attempts(self, owner, workspace, identifier):
        return self.attempts.get(identifier, ())

    async def output_records(self, owner, workspace):
        return tuple(self.outputs)

    async def cancel_outputs(self, owner, workspace, identifiers):
        for row in self.outputs:
            if row['envelope']['message']['id'] in identifiers and row['status'] == 'pending':
                row['status'] = 'cancelled'

    async def resume_io(self):
        self.resumed += 1

    async def subscribe_inputs(self, callback):
        self.listeners.append(callback)
        return lambda: self.listeners.remove(callback)

    async def subscribe_outputs(self, callback):
        self.listeners.append(callback)
        return lambda: self.listeners.remove(callback)

    def register_output_source(self, name, resolve, policy):
        self.routes[name] = resolve, policy
        return lambda: self.routes.pop(name)


def message(identifier='original'):
    return {'id': identifier, 'origin': 'acp', 'source': 'Eyes', 'instruction': 'Inspect original work',
            'evidence': [{'original': 1}], 'provenance': {}}


async def test_provider_on_stdlib_host_owns_only_feature_resources(tmp_path):
    host = Host(tmp_path)
    assert (provider.name, provider.scopes, provider.tool_names, provider.requirements) == (
        'eyes', ('application',), ('eyes',), ('acp',))
    binding = await provider.open(host)
    try:
        assert isinstance(binding, ManagedObservers) and len(binding.tools) == 1
        assert host.opened[0].path == tmp_path / 'observers' / 'journal.sqlite3'
        assert host.resumed == 0 and 'observations' in host.routes
        tool = binding.tools[0]
        assert tool.name == 'eyes' and json.loads(json.dumps(tool.parameters)) == tool.parameters
        assert json.loads(await tool.execute('list', {'action': 'list'}, ctx={'group_id': 'owner'})) == []
        with pytest.raises(ValueError, match='owner'):
            await tool.execute('wrong', {'action': 'list'}, ctx={'group_id': 'external'})
        assert not binding._tasks and not binding._files
        await binding.flush()
    finally:
        await binding.aclose()
    assert not host.listeners and not host.routes and host.opened[0].closed
    assert host.resumed == 0  # Closing the feature does not close/restart host execution.
    await binding.aclose()


async def test_opaque_reply_and_json_outbox_remain_original_and_atomic(tmp_path):
    host = Host(tmp_path)
    journal = await ObserverJournal.open(tmp_path / 'observers' / 'journal.sqlite3', host)
    try:
        record = await journal.create('owner', tmp_path, 'original', {},
                                      reply=host.capture_reply({'group_id': 'owner'}))
        await journal._disk(lambda db: db.execute(
            "CREATE TRIGGER fail_cursor BEFORE UPDATE ON observers BEGIN SELECT RAISE(ABORT,'disk gate'); END"))
        with pytest.raises(sqlite3.IntegrityError, match='disk gate'):
            await journal.save(record, {'cursor': 1}, (message(),))
        assert await journal.message(record, 'original') is None
        await journal._disk(lambda db: db.execute('DROP TRIGGER fail_cursor'))
        payload = message()
        await journal.save(record, {'cursor': 1}, (payload,))
        payload['evidence'][0]['original'] = 2
        assert await journal.message(record, 'original') == message()
        envelope = {'group_id': 'owner', 'workspace': str(tmp_path), 'message': message(), 'reply': record.reply}
        assert await journal.resolve_reply(envelope) == 'opaque!host-private-recipient'
        assert await journal.output_policy({'envelope': envelope}) == 'ready'
        with pytest.raises(ValueError, match='recipient'):
            await journal.output_policy({'envelope': {**envelope, 'reply': 'another recipient'}})
        await journal.cancel_notifications('owner', tmp_path, record.id)
        assert await journal.output_policy({'envelope': envelope}) == 'cancelled'
        assert (await journal.get('owner', tmp_path, record.id)).enabled
        await journal.cancel('owner', tmp_path, record.id)
        assert not host.receipts  # Source/notification cancellation never manipulates Core input state.
    finally:
        await journal.close()


async def test_provider_open_failure_settles_journal_and_registrations(tmp_path):
    host = Host(tmp_path)

    async def fail():
        raise OSError('output service unavailable')

    async def fail_subscription(callback):
        await fail()
    host.subscribe_outputs = fail_subscription
    with pytest.raises(OSError, match='unavailable'):
        await provider.open(host)
    assert host.opened[0].closed and not host.listeners and not host.routes


def test_import_provider_without_any_harness_or_agent_packages(tmp_path):
    # A clean process is important: other workspace test modules import Agent.
    source = Path(importlib.util.find_spec('xdog.eyes').origin).parent.parent.parent
    script = r'''
import asyncio, importlib.abc, pathlib, sys
class NoHarness(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[:2] in [
            ['xdog','agent'], ['xdog','ai'], ['xdog','claw'], ['xdog','coding'],
            ['xdog','skills'], ['xdog','flow'], ['xdog','tui']]:
            raise AssertionError('Forbidden dependency: ' + fullname)
sys.meta_path.insert(0, NoHarness())
from xdog.eyes.hosting.runtime import provider
assert provider.scopes == ('application',)
assert 'xdog.eyes.hosting.manager' not in sys.modules
assert 'xdog.eyes.acp_client' not in sys.modules
assert 'acp' not in sys.modules
assert not pathlib.Path.cwd().joinpath('observers').exists()
assert not any(name.startswith(('xdog.agent', 'xdog.claw', 'xdog.ai')) for name in sys.modules)
'''
    result = subprocess.run([sys.executable, '-I', '-c', f'import sys; sys.path.insert(0, {str(source)!r});' + script],
                            cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
