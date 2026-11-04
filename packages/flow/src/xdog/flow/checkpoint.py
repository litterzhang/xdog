"""flow.checkpoint — checkpoint store protocol and JSON-file implementation."""

from __future__ import annotations

import inspect
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol


class CheckpointStore(Protocol):
    """Persists and restores a run's progress snapshot, keyed by run_id."""

    def save(self, run_id: str, snapshot: dict[str, Any]) -> None: ...

    def load(self, run_id: str) -> dict[str, Any] | None: ...  # None if absent


class CheckpointInterceptor:
    """Persist only after a coherent state mutation completes successfully."""

    def __init__(
        self,
        snapshot_factory: Callable[[], dict[str, object]],
        persist: Callable[[dict[str, object]], None],
    ) -> None:
        self._snapshot_factory = snapshot_factory
        self._persist = persist

    def intercept(self, reason: str, mutation: Callable[[], Any]) -> Any:
        """Run *mutation*, then persist exactly once if it returns normally."""
        _ = reason  # diagnostic boundary label; deliberately not serialized
        result = mutation()
        self.commit(reason)
        return result

    def commit(self, reason: str) -> None:
        """Persist the current state without a preceding mutation (e.g. pause)."""
        _ = reason
        self._persist(self._snapshot_factory())


def render_checkpoint_interceptor() -> str:
    """Return the exact standalone interceptor implementation for codegen."""
    return inspect.getsource(CheckpointInterceptor)


class JSONFileCheckpointStore:
    """Checkpoint store backed by JSON files in a directory.

    Each run_id maps to ``<dir>/<run_id>.json``.  Writes are atomic: the
    snapshot is first written to a temp file in the same directory and then
    renamed over the target so a crash during the write never leaves a
    partially-written file.
    """

    def __init__(self, dir: str | Path) -> None:
        self._dir = Path(dir)

    def save(self, run_id: str, snapshot: dict[str, Any]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        target = self._dir / f"{run_id}.json"
        fd, tmp_path = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(snapshot, fh, ensure_ascii=False)
            os.replace(tmp_path, target)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def load(self, run_id: str) -> dict[str, Any] | None:
        target = self._dir / f"{run_id}.json"
        if not target.exists():
            return None
        with target.open(encoding="utf-8") as fh:
            data: dict[str, Any] = json.load(fh)
        return data


def _contract_hash(value: object) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def checkpoint_contract(definition: dict[str, Any]) -> dict[str, object]:
    """Pin graph/defaults and each node body, without saving executable definitions.

    An uncomputed node's body may be explicitly repaired before resume. Completed
    computations, wiring, port types and defaults cannot change silently. External
    module contents still require host-owned artifact/version pinning.
    """
    nodes = definition['nodes']
    graph = {key: value for key, value in definition.items() if key != 'nodes'}
    graph['nodes'] = [{key: node[key] for key in ('id', 'type', 'input_ports', 'output_ports')}
                      for node in nodes]
    return {'format': 'xdog.flow.checkpoint.v1', 'graph': _contract_hash(graph),
            'nodes': {node['id']: _contract_hash(node) for node in nodes}}


def validate_checkpoint_contract(snapshot: dict[str, Any], expected: dict[str, object]) -> None:
    actual = snapshot.get('contract')
    if (not isinstance(actual, dict) or actual.get('format') != expected['format']
            or actual.get('graph') != expected['graph'] or not isinstance(actual.get('nodes'), dict)):
        raise ValueError('Checkpoint definition/format changed; no old work was resumed')
    completed = snapshot.get('completed')
    versions = expected['nodes']
    if not isinstance(completed, list) or not isinstance(versions, dict):
        raise ValueError('Invalid checkpoint completed nodes')
    outputs, frames = snapshot.get('outputs'), snapshot.get('stack')
    if not isinstance(outputs, dict) or not isinstance(frames, list):
        raise ValueError('Invalid checkpoint outputs/stack')
    # Loop transitions reopen completed nodes. Their previous outputs/history and
    # deterministic memo entries still belong to the old definition, not new code.
    computed = [*completed, *(node for node in outputs if node != '$in'),
                *(frame.get('node') for frame in frames if isinstance(frame, dict))]
    for node in computed:
        if not isinstance(node, str) or node not in versions or actual['nodes'].get(node) != versions[node]:
            raise ValueError('Completed checkpoint node changed; no old work was resumed')


def render_checkpoint_contract() -> str:
    return '\n\n'.join(inspect.getsource(item) for item in
                       (_contract_hash, checkpoint_contract, validate_checkpoint_contract))
