"""flow — multi-agent workflow engine and code generator."""

from __future__ import annotations

from importlib import import_module as _import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from xdog.flow.builder.serialize import dump_workflow, workflow_to_dict
    from xdog.flow.builder.svg_doc import dump_workflow_svg, read_workflow_from_svg, workflow_to_svg_document
    from xdog.flow.codegen import generate
    from xdog.flow.executor import execute
    from xdog.flow.graph import to_ascii, to_mermaid, to_svg
    from xdog.flow.loader import load_workflow
    from xdog.flow.models import WorkflowDef
    from xdog.flow.telemetry import MetricsCollector, NodeMetrics, RunMetrics

__all__ = [
    "WorkflowDef",
    "load_workflow",
    "execute",
    "generate",
    "workflow_to_dict",
    "dump_workflow",
    "to_ascii",
    "to_mermaid",
    "to_svg",
    "workflow_to_svg_document",
    "read_workflow_from_svg",
    "dump_workflow_svg",
    "MetricsCollector",
    "NodeMetrics",
    "RunMetrics",
]


# Keep capability discovery cheap: resolve engine modules only when their public
# objects are requested. Return the original objects, not wrappers, so signatures
# and identity remain identical to imports from the defining modules.
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


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(_import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
