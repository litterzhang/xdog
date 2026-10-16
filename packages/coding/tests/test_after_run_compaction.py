"""Extension-driven after-run compaction controls are removed; native compaction stays."""
from __future__ import annotations

import importlib.util

from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.runtime import ExtensionRuntime


def test_after_run_host_control_adapters_and_public_request_are_removed():
    for name in ('compaction', 'name', 'tools'):
        assert importlib.util.find_spec('xdog.claw.core.runtime.after_run_' + name) is None
    assert not hasattr(ExtensionAPI, 'request_compaction')
    assert callable(ExtensionRuntime.before_compaction)  # Actual summary/veto hooks remain.
