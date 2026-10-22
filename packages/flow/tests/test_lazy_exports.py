"""Public root exports stay compatible without eagerly loading the engine."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

# Freeze the pre-lazy public API independently of the implementation mapping.
_EXPORT_MODULES = {
    "WorkflowDef": "xdog.flow.models",
    "load_workflow": "xdog.flow.loader",
    "execute": "xdog.flow.executor",
    "generate": "xdog.flow.codegen",
    "workflow_to_dict": "xdog.flow.builder.serialize",
    "dump_workflow": "xdog.flow.builder.serialize",
    "to_ascii": "xdog.flow.graph",
    "to_mermaid": "xdog.flow.graph",
    "to_svg": "xdog.flow.graph",
    "workflow_to_svg_document": "xdog.flow.builder.svg_doc",
    "read_workflow_from_svg": "xdog.flow.builder.svg_doc",
    "dump_workflow_svg": "xdog.flow.builder.svg_doc",
    "MetricsCollector": "xdog.flow.telemetry",
    "NodeMetrics": "xdog.flow.telemetry",
    "RunMetrics": "xdog.flow.telemetry",
}


def _run_python(source: str) -> subprocess.CompletedProcess[str]:
    # A fresh interpreter is essential: other Flow tests import the engine during
    # collection, which would hide eager imports in this process.
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize("module_name", ["xdog.flow", "xdog.flow.capability"])
def test_discovery_import_does_not_load_engine(module_name: str) -> None:
    result = _run_python(f"""
        import importlib
        import importlib.abc
        import sys

        forbidden = {{"xdog.flow.executor", "xdog.flow.codegen", "xdog.flow.runners"}}

        class RejectEngine(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                assert fullname not in forbidden, f"eager engine import: {{fullname}}"
                return None

        sys.meta_path.insert(0, RejectEngine())
        importlib.import_module({module_name!r})
        assert not forbidden.intersection(sys.modules)
        if {module_name!r} == "xdog.flow":
            assert not any(name.startswith("xdog.flow.") for name in sys.modules)
    """)
    assert result.returncode == 0, result.stdout + result.stderr


def test_public_names_and_dir_do_not_resolve_exports() -> None:
    result = _run_python(f"""
        import sys
        import xdog.flow as flow

        expected = {list(_EXPORT_MODULES)!r}
        assert flow.__all__ == expected
        assert not set(expected).intersection(vars(flow))
        names = dir(flow)
        assert set(expected).issubset(names)
        assert names == sorted(set(names))
        assert "__name__" in names
        for name in ("not_a_flow_export", "__missing__"):
            try:
                getattr(flow, name)
            except AttributeError as exc:
                assert str(exc) == f"module 'xdog.flow' has no attribute {{name!r}}"
            else:
                raise AssertionError(name)
        assert not set(expected).intersection(vars(flow))
        assert not any(name.startswith("xdog.flow.") for name in sys.modules)
    """)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(("name", "module_name"), _EXPORT_MODULES.items())
def test_export_is_original_object_and_cached(name: str, module_name: str) -> None:
    result = _run_python(f"""
        import importlib
        import xdog.flow as flow

        assert {name!r} not in vars(flow)
        from xdog.flow import {name} as exported
        canonical = getattr(importlib.import_module({module_name!r}), {name!r})
        assert exported is canonical
        assert getattr(flow, {name!r}) is canonical
        assert vars(flow)[{name!r}] is canonical

        # Once resolved, future access must not need the lazy import machinery.
        def reject_import(name):
            raise AssertionError(f"uncached export: {{name}}")

        flow._import_module = reject_import
        assert getattr(flow, {name!r}) is canonical
    """)
    assert result.returncode == 0, result.stdout + result.stderr


def test_star_import_preserves_all_public_objects() -> None:
    result = _run_python(f"""
        import importlib
        import xdog.flow as flow

        namespace = {{}}
        exec("from xdog.flow import *", namespace)
        expected = {_EXPORT_MODULES!r}
        assert set(namespace) - {{"__builtins__"}} == set(expected)
        for name, module_name in expected.items():
            canonical = getattr(importlib.import_module(module_name), name)
            assert namespace[name] is canonical
            assert getattr(flow, name) is canonical
    """)
    assert result.returncode == 0, result.stdout + result.stderr
