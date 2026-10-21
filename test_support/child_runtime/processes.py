"""Real isolated-process imports for the selected production host, never test code."""

from pathlib import Path

AGENT_TEST_FIXTURES = Path(__file__).resolve().parents[2] / "packages" / "agent" / "tests" / "fixtures"


def child_process_imports(runtime_type, child_store_type, *, borrowed_loop=False):
    source = (
        f"from {runtime_type.__module__} import {runtime_type.__name__} as ChildRuntime\n"
        f"from {child_store_type.__module__} import {child_store_type.__name__} as ChildStore\n"
    )
    if borrowed_loop:
        source += (
            "from functools import partial\nChildRuntime = partial(ChildRuntime, owner_loop=asyncio.get_running_loop)\n"
        )
    return source


def write_host_script(path, template, imports):
    source = (Path(__file__).parent / "fixtures" / template).read_text()
    path.write_text(source.replace("# HOST_RUNTIME_IMPORTS", imports))
