"""Actual native metadata/tool/extension preparation precedes owner-loop Agent construction."""
from __future__ import annotations

import io
import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, Model, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.claw.core.types import UserInput


@pytest.mark.parametrize('kind', ['extension-root', 'builtin-tools', 'native-model'])
async def test_real_constructor_metadata_io_is_prepared_without_moving_agent_loop(tmp_path, monkeypatch, kind):
    workspace = tmp_path / 'workspace'
    extensions = tmp_path / 'real-extensions'
    extensions.mkdir()
    alias = tmp_path / 'extension-alias'
    alias.symlink_to(extensions, target_is_directory=True)
    catalog = tmp_path / 'native-catalog.json'
    catalog.write_text(json.dumps({'id': 'test/metadata', 'context_window': 32000}))
    owner, observed, calls = threading.get_ident(), [], []
    original_resolve, original_open = Path.resolve, io.open

    class NativeStream:
        def __call__(self, *args):
            assert threading.get_ident() == owner
            calls.append(args)
            return EventStream.empty(AssistantMessage(content=(TextContent(text='Original owner model'),)))

        def resolve_model(self, name):
            row = json.loads(catalog.read_text())
            return Model(id=row['id'], context_window=row['context_window'])

    stream = NativeStream()

    def resolve(path, *args, **kwargs):
        if path == (alias if kind == 'extension-root' else workspace) and kind != 'native-model':
            observed.append(threading.get_ident())
        return original_resolve(path, *args, **kwargs)

    def open_file(file, *args, **kwargs):
        if file == catalog and kind == 'native-model':
            observed.append(threading.get_ident())
        return original_open(file, *args, **kwargs)

    config = ClawConfig(model='test/metadata', data_dir=str(tmp_path / 'data'),
        extension_dir=str(alias), enabled_tools=('bash',) if kind == 'builtin-tools' else (),
        socket_path=str(tmp_path / 'gateway.sock'), pid_file=str(tmp_path / 'pid'),
        groups=(GroupDef(id='main', workspace=str(workspace)),))
    server = GatewayServer(config)
    try:
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/metadata', stream)):
            with monkeypatch.context() as trace:
                trace.setattr(Path, 'resolve', resolve)
                trace.setattr(io, 'open', open_file)
                await server.start()
                result = await server._orchestrator.route_message(UserInput(group_id='main', content='Actual input'))
        assert result.error is None and len(calls) == 1
        session = server._orchestrator._runtimes['main']._active_session
        assert session._host_loop is not None and session.extensions.root == extensions
        assert observed and all(thread != owner for thread in observed), (
            'Constructor blocked Agent owner', kind, observed)
    finally:
        await server.stop()


@pytest.mark.parametrize('action', ['reasoning', 'models', 'select', 'inspection'])
async def test_existing_native_model_controls_read_catalog_off_loop(tmp_path, monkeypatch, action):
    from xdog.claw.core.runtime.orchestrator import Orchestrator
    from xdog.claw.core.types import Group

    catalog = tmp_path / 'control-catalog.json'
    catalog.write_text(json.dumps({'id': 'test/metadata', 'context_window': 32000}))
    owner, calls, reads = threading.get_ident(), [], []

    class Catalog:
        def __call__(self, *args):
            assert threading.get_ident() == owner
            calls.append(args)
            return EventStream.empty(AssistantMessage(content=(TextContent(text='Actual original input'),)))

        def resolve_model(self, name):
            row = json.loads(catalog.read_text())
            return Model(id=row['id'], context_window=row['context_window'])

        def models(self):
            return (self.resolve_model('test/metadata'),)

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/metadata', stream_fn=Catalog())
    app.register_group(Group(id='main', workspace=str(tmp_path / 'workspace'), enabled_tools=()))
    original = io.open

    def open_file(file, *args, **kwargs):
        if file == catalog:
            reads.append(threading.get_ident())
        return original(file, *args, **kwargs)

    try:
        await app.route_message(UserInput(group_id='main', content='Actual parent input'))
        identifier = app.get_new_session_id('main')
        with monkeypatch.context() as trace:
            trace.setattr(io, 'open', open_file)
            if action == 'inspection':
                assert app._runtimes['main']._active_session._extension_inspection() is not None
            elif action == 'reasoning':
                await app.reasoning('main', identifier)
            else:
                await app.extension_models('main', identifier, 'test/metadata' if action == 'select' else '')
        assert len(calls) == 1 and app.get_new_session_id('main') == identifier
        assert (not reads if action == 'inspection' else reads and all(thread != owner for thread in reads)), (
            'Model control/inspection lookup blocked its Agent owner')
    finally:
        await app.stop()


async def test_default_provider_resource_is_prepared_per_context_without_changing_configuration(tmp_path, monkeypatch):
    from xdog.ai.providers.runtime import Runtime
    from xdog.claw.core.runtime.orchestrator import Orchestrator
    from xdog.claw.core.types import Group

    monkeypatch.setattr('xdog.ai.load', Runtime)  # No configured providers/authentication or model calls.
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/no-provider')
    app.register_group(Group(id='main', workspace=str(tmp_path / 'workspace'), enabled_tools=()))
    runtime = app._runtimes['main']
    try:
        first = await runtime.prepare_session(allow_reset=False)
        first_stream = first.agent.stream_fn.base
        assert runtime.stream_fn is None, 'Preparation must not replace the configured default-provider policy'
        await app.navigate_session('main', 'new', '', '', -1, first.meta.session_id)
        second = await runtime.prepare_session(allow_reset=False)
        assert second.agent.stream_fn.base is not first_stream and runtime.stream_fn is None
    finally:
        await app.stop()
