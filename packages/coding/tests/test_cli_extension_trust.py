"""CLI metadata/bootstrap cannot execute merely installed Python extensions."""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from xdog.ai.types import Model
from xdog.coding import main
from xdog.coding.cli.args import cli


@pytest.mark.parametrize('selected', [False, True])
def test_actual_cli_keeps_unselected_code_inert_and_selected_workers_lazy(tmp_path, monkeypatch, selected):
    data = tmp_path / 'data'
    data.mkdir()
    root = data / 'extensions'
    extension = root / 'sample'
    extension.mkdir(parents=True)
    marker = tmp_path / 'extension-imports.txt'
    (extension / 'extension.yaml').write_text('name: sample\nentry_point: main\n')
    (extension / 'main.py').write_text(
        'from pathlib import Path\n'
        f'marker = Path({str(marker)!r})\n'
        'marker.write_text((marker.read_text() if marker.exists() else "") + "X")\n'
        'def setup(api):\n'
        '    api.status("ready", "Explicitly selected worker")\n')
    (data / 'settings.json').write_text(json.dumps({
        'default_model': 'offline/model', 'thinking_level': 'off',
        'extensions': ['sample'] if selected else [], 'extension_dir': str(root)}))
    monkeypatch.setenv('CODING_DIR', str(data))

    class Provider:
        def models(self):
            return (Model(id='offline/model', context_window=100_000),)

        def model(self, identifier):
            return self.models()[0] if identifier == 'offline/model' else None

        def stream(self, *_args):
            pytest.fail('CLI construction and explicit extension setup must not call a model')

    monkeypatch.setattr('xdog.ai.load', lambda: Provider())
    # Keep real Click routing, SDK construction/configuration and startup path.
    # Replace only terminal/input boundaries; no model or real TUI is needed.
    owners = []
    worker_processes = []
    monkeypatch.setattr(main, '_build_initial_message', lambda *args, **kwargs: 'metadata only')
    def terminal(session, *args, **kwargs):
        owners.append(session)
        assert not marker.exists(), 'CLI bootstrap imported code before explicit worker activation'
        runtime = session.enable_runtime(bound=False)
        runtime.start(own_thread=True, setup_pending=True)
        for _ in range(2):
            runtime.controller.control(session.initialize_extensions, name='explicit-worker-setup').result(timeout=8)
        if selected:
            assert marker.read_text() == 'X'
            worker_processes.append(session.extensions._manager._clients['sample'].process)
        else:
            assert not marker.exists() and not session.extensions._manager._clients
    monkeypatch.setattr(main, '_run_print', terminal)
    try:
        result = CliRunner().invoke(cli, ['--print', '--prompt', 'metadata only', '--working-dir', str(tmp_path)])
        assert result.exit_code == 0, result.exception
        assert len(owners) == 1 and owners[0].agent.closed and owners[0]._lease is None
        assert all(process.returncode is not None for process in worker_processes)
    finally:
        for owner in owners:
            owner.dispose().result(timeout=10)
