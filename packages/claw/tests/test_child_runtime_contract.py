"""Run the unchanged shared acceptance contract against the real Claw runtime."""

from functools import partial
from importlib import import_module

import pytest

from test_support.child_runtime.admission import TestChildAdmission as TestChildAdmission
from test_support.child_runtime.compaction import TestChildCompaction as TestChildCompaction
from test_support.child_runtime.config import TestChildConfig as TestChildConfig
from test_support.child_runtime.cross_loop import TestChildCrossLoop as TestChildCrossLoop
from test_support.child_runtime.cross_loop import TestChildCrossLoopShutdown as TestChildCrossLoopShutdown
from test_support.child_runtime.dispatch import harness as harness
from test_support.child_runtime.extension_journal import TestChildExtensionJournal as TestChildExtensionJournal
from test_support.child_runtime.extension_readonly import TestChildExtensionReadonly as TestChildExtensionReadonly
from test_support.child_runtime.images import TestChildImages as TestChildImages
from test_support.child_runtime.initial_recovery import TestChildInitialRecovery as TestChildInitialRecovery
from test_support.child_runtime.journal import TestChildJournal as TestChildJournal
from test_support.child_runtime.lifecycle import TestChildLifecycle as TestChildLifecycle
from test_support.child_runtime.main_recovery import TestChildMainRecovery as TestChildMainRecovery
from test_support.child_runtime.presets import TestChildPresets as TestChildPresets
from test_support.child_runtime.protocol import TestChildProtocol as TestChildProtocol
from test_support.child_runtime.queue import TestChildQueue as TestChildQueue
from test_support.child_runtime.recovery import TestChildRecovery as TestChildRecovery
from test_support.child_runtime.returns import TestChildReturns as TestChildReturns
from test_support.child_runtime.store import TestChildStore as TestChildStore
from test_support.child_runtime.subscriptions import TestChildSubscriptions as TestChildSubscriptions
from test_support.child_runtime.tool import TestChildTool as TestChildTool


@pytest.fixture
def runtime_type():
    from xdog.claw.core.runtime.child_runtime import ClawChildRuntime

    return ClawChildRuntime


@pytest.fixture
def child_store_type(child_store_module):
    return child_store_module.ChildStore


@pytest.fixture
def child_store_module():
    from xdog.claw.core.runtime import child_store

    return child_store


@pytest.fixture
def runtime_module(runtime_type):
    module = import_module(runtime_type.__module__)
    return import_module(module.LiveImageReturns.__module__)


@pytest.fixture
def application_loop():
    from test_support.child_runtime.ownership import host_loop

    with host_loop() as loop:
        yield loop


@pytest.fixture
def runtime_factory(runtime_type, application_loop):
    # Construct the real class, injecting only the actual application's loop port.
    return partial(runtime_type, owner_loop=lambda: application_loop)


@pytest.fixture
def execution_owner(application_loop):
    from test_support.child_runtime.ownership import RuntimeOwnership

    return RuntimeOwnership(application_loop)


@pytest.fixture
def child_extensions_type():
    from xdog.claw.core.runtime.child_extensions import ChildExtensions

    return ChildExtensions


@pytest.fixture
def child_process_imports(runtime_type, child_store_type):
    from test_support.child_runtime.processes import child_process_imports

    return child_process_imports(runtime_type, child_store_type, borrowed_loop=True)
