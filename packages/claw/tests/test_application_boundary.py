"""Keep Claw an application at its root, with explicit internal runtime imports."""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest


def _run_python(tmp_path: Path, code: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Use the installed workspace, not pytest's imports or source-path injection."""
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "NO_COLOR": "1",
        "TERM": "dumb",
    }
    result = subprocess.run(
        [sys.executable, "-I", "-c", textwrap.dedent(code), *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"Child exited {result.returncode}:\n{result.stdout}\n{result.stderr}"
    return result


def test_root_initializer_is_only_an_application_docstring() -> None:
    initializer = Path(__file__).resolve().parents[1] / "src/xdog/claw/__init__.py"
    module = ast.parse(initializer.read_text(encoding="utf-8"))
    assert ast.get_docstring(module), "The application root should describe its purpose"
    assert len(module.body) == 1, "Keep SDK exports, lazy exports and runtime setup out of the root"


def test_package_metadata_describes_a_chat_application() -> None:
    project_file = Path(__file__).resolve().parents[1] / "pyproject.toml"
    project = tomllib.loads(project_file.read_text(encoding="utf-8"))["project"]
    classifiers = project["classifiers"]
    assert "Topic :: Communications :: Chat" in classifiers
    assert "Topic :: Software Development :: Libraries" not in classifiers


def test_root_import_does_not_load_runtime_providers_or_tool_registration(tmp_path: Path) -> None:
    _run_python(tmp_path, """
        import importlib.abc
        import sys

        # Namespace boundaries, not a snapshot of every transitive implementation module.
        forbidden = (
            'xdog.agent', 'xdog.ai', 'xdog.eyes', 'xdog.tui',
            'xdog.claw.core', 'xdog.claw.channels', 'xdog.claw.acp',
        )

        def is_forbidden(name):
            return any(name == prefix or name.startswith(prefix + '.') for prefix in forbidden)

        assert not any(is_forbidden(name) for name in sys.modules)

        class NoRuntimeImports(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                assert not is_forbidden(fullname), 'Root import attempted to load ' + fullname

        sys.meta_path.insert(0, NoRuntimeImports())
        import xdog.claw

        assert not any(is_forbidden(name) for name in sys.modules)
        # In a fresh interpreter even an accidental module/function re-export is visible.
        public_names = {name for name in vars(xdog.claw) if not name.startswith('_')}
        assert not public_names, public_names
    """)


def test_root_import_does_not_start_background_work(tmp_path: Path) -> None:
    _run_python(tmp_path, """
        import asyncio
        import importlib
        import subprocess
        import threading
        from unittest.mock import patch

        def forbidden_start(*args, **kwargs):
            raise AssertionError('Importing the application root started background work')

        async def check_import():
            loop = asyncio.get_running_loop()
            tasks_before = asyncio.all_tasks()
            threads_before = set(threading.enumerate())
            old_factory = loop.get_task_factory()
            loop.set_task_factory(forbidden_start)
            try:
                with patch.object(threading.Thread, 'start', forbidden_start), \\
                     patch.object(subprocess, 'Popen', forbidden_start):
                    importlib.import_module('xdog.claw')
                assert asyncio.all_tasks() == tasks_before
                assert set(threading.enumerate()) == threads_before
            finally:
                loop.set_task_factory(old_factory)

        asyncio.run(check_import())
    """)


@pytest.mark.parametrize("name", ["Orchestrator", "AgentSession", "GroupRuntime", "ClawConfig", "create_tools"])
def test_representative_sdk_names_are_not_available_from_root(tmp_path: Path, name: str) -> None:
    _run_python(tmp_path, """
        import sys
        import xdog.claw

        name = sys.argv[1]
        assert not hasattr(xdog.claw, name), 'Legacy root export: ' + name
        try:
            exec('from xdog.claw import ' + name, {})
        except ImportError:
            pass
        else:
            raise AssertionError('Legacy from-import still works: ' + name)
    """, name)


@pytest.mark.parametrize(
    ("args", "markers"),
    [
        (("--help",), ("gateway", "tui")),
        (("gateway", "--help"), ("start", "stop", "status")),
        (("gateway", "start", "--help"), ("--foreground", "--acp-socket")),
        (("tui", "--help"), ("--group", "--config")),
    ],
)
def test_cli_help_works_in_a_fresh_process(tmp_path: Path, args: tuple[str, ...], markers: tuple[str, ...]) -> None:
    result = _run_python(tmp_path, """
        import sys
        from importlib.metadata import distribution

        # Exercise the real console entry point in this interpreter's environment.
        entrypoint = next(
            entry for entry in distribution('xdog-claw').entry_points
            if entry.group == 'console_scripts' and entry.name == 'xdog-claw'
        )
        sys.argv[0] = 'xdog-claw'
        entrypoint.load()()
    """, *args)
    assert "Usage:" in result.stdout
    for marker in markers:
        assert marker in result.stdout
    assert not result.stderr, result.stderr


def test_explicit_core_import_still_registers_domain_tools(tmp_path: Path) -> None:
    _run_python(tmp_path, """
        import sys
        import xdog.claw

        # Inspecting the registry first would itself import core and hide a root regression.
        assert 'xdog.claw.core.tools.registry' not in sys.modules
        import xdog.claw.core
        from xdog.claw.core.tools.registry import create_tools, registered_names

        # Representative built-in and domain registrations; new tools may be added freely.
        required = {'filesystem', 'group_message', 'eyes', 'memory', 'skill'}
        assert required <= registered_names(), required - registered_names()
        tools = create_tools(enabled=tuple(sorted(required)))
        assert {tool.name for tool in tools} == required
    """)


def test_internal_application_entry_points_remain_importable(tmp_path: Path) -> None:
    _run_python(tmp_path, """
        from xdog.claw.acp.backend import ClawBackend
        from xdog.claw.channels.tui.tui_app import ChatApp
        from xdog.claw.core.runtime.gateway import GatewayServer, run_gateway
        from xdog.claw.core.runtime.group import GroupRuntime
        from xdog.claw.core.runtime.orchestrator import Orchestrator
        from xdog.claw.core.runtime.session import AgentSession

        # Import smoke only: no model, gateway, TUI, socket or ACP session is started.
        for entrypoint in (
            ClawBackend, ChatApp, GatewayServer, run_gateway, GroupRuntime, Orchestrator, AgentSession,
        ):
            assert callable(entrypoint)
    """)
