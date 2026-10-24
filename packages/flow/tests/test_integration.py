"""Integration test: load examples/agent_calculator.json and execute dry-run.

The shipped example set is intentionally minimal (just ``agent_calculator``),
which exercises both node kinds in one workflow: an inline **script** node
(``make_problem`` turns the typed integer inputs into an arithmetic string) and an
**agent** node with the ``bash`` tool (``solve``).  These tests cover dry-run
execution and interpreter/codegen parity against that single example; the
feature-specific behaviours that used to ride on other examples (bounded loop
back-edges, ``submit_result`` structured output) are covered by unit tests in
``test_svg.py`` and ``test_executor.py``.
"""

from __future__ import annotations

import asyncio
import pathlib
import subprocess
import sys
import tempfile
import types
from typing import Any

from xdog.ai.types import AssistantMessage, DoneEvent, TextContent
from xdog.ai.utils.event_stream import EventStream as AiEventStream
from xdog.flow.codegen import generate
from xdog.flow.executor import execute
from xdog.flow.loader import load_workflow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXAMPLES_DIR = pathlib.Path(__file__).parent.parent / "examples"
_CALC = _EXAMPLES_DIR / "agent_calculator.json"


def _make_stub_factory(responses: dict[str, str], default: str = "") -> Any:
    """Return a stream_fn_factory keyed by *model* that returns deterministic text."""

    def _factory(model: str) -> Any:
        text = responses.get(model, default)

        def _stream_fn(
            model_id: Any,
            context: Any,
            options: Any = None,
        ) -> AiEventStream[AssistantMessage]:
            msg = AssistantMessage(content=(TextContent(text=text),))
            stream: AiEventStream[AssistantMessage] = AiEventStream()

            async def _push() -> None:
                await asyncio.sleep(0)
                await stream.send(DoneEvent(stop_reason="stop", message=msg))
                stream.set_result(msg)
                await stream.close()

            asyncio.ensure_future(_push())
            return stream

        return _stream_fn

    return _factory


def _dryrun_factory(m: str) -> Any:
    """Stream factory whose agent nodes echo ``DRYRUN:<model>`` (no real LLM)."""

    def _stream_fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
        msg = AssistantMessage(content=(TextContent(text=f"DRYRUN:{m}"),))
        stream: AiEventStream[AssistantMessage] = AiEventStream()

        async def _push() -> None:
            await asyncio.sleep(0)
            await stream.send(DoneEvent(stop_reason="stop", message=msg))
            stream.set_result(msg)
            await stream.close()

        asyncio.ensure_future(_push())
        return stream

    return _stream_fn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_agent_calculator_dryrun() -> None:
    """Load agent_calculator.json; execute with a dry-run stub.

    The script node (make_problem) runs its inline ctx-first function for real,
    turning typed ints a=347, b=895 into the string "347 + 895".  The agent node
    (solve) declares the ``bash`` tool and — under the stub stream_fn — stores
    some text under ``answer``.  (A real ``--provider`` run has the agent shell
    out via bash to compute 1242; the stub only exercises the wiring.)
    """
    wf = load_workflow(_CALC)

    from xdog.flow.tools import default_repo

    result = await execute(wf, stream_fn_factory=_dryrun_factory, tool_repo=default_repo())

    # script node built the arithmetic problem from typed integer inputs
    assert result.runtime["state"]["make_problem"].get("problem") == "347 + 895"
    # agent node ran and stored an answer (real content requires a live provider)
    assert "answer" in result.runtime["state"].get("solve", {})
    # the workflow declares a $output ($in -> solve.answer -> result)
    assert "result" in result.runtime["out"]


async def test_generate_parity() -> None:
    """Generate agent_calculator.json; ruff-check; compile; assert node functions.

    - generated module compiles and is ruff-clean
    - one async node function per workflow node, plus async main()
    - the inline script node emits a self-contained ctx-first function + wrapper,
      and the agent node resolves its tools via _REGISTRY.select
    - running the generated module yields the same nested outputs as the
      interpreter (parity), using a stub so no real LLM is called
    """
    wf = load_workflow(_CALC)
    src = generate(wf)

    # --- structural: compiles ---
    compile(src, "<generated>", "exec")

    # --- structural: ruff-clean ---
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as tf:
        tf.write(src)
        tmp_path = pathlib.Path(tf.name)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--line-length", "120", str(tmp_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"ruff failed:\n{result.stdout}\n{result.stderr}"
    finally:
        tmp_path.unlink(missing_ok=True)

    # --- structural: node functions, main(), inline script + tool registry ---
    for node in wf.nodes:
        assert f"async def node_{node.id}" in src, f"missing node function for {node.id}"
    assert "async def main()" in src
    assert "def _script_make_problem(ctx" in src, "inline script must be a top-level _script_make_problem"
    assert "RuntimeContext(" in src, "script wrapper must build a RuntimeContext"
    assert "_REGISTRY.select(" in src, "agent node must resolve its tools via _REGISTRY.select("

    # --- parity: generated module output == interpreter output ---
    model = wf.default_model
    stub_text = "DRYRUN"

    def _stub_stream_fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
        msg = AssistantMessage(content=(TextContent(text=stub_text),))
        stream: AiEventStream[AssistantMessage] = AiEventStream()

        async def _push() -> None:
            await asyncio.sleep(0)
            await stream.send(DoneEvent(stop_reason="stop", message=msg))
            stream.set_result(msg)
            await stream.close()

        asyncio.ensure_future(_push())
        return stream

    import xdog.agent.helpers as _agent_helpers
    import xdog.ai as _ai

    original_sfp = _agent_helpers.stream_fn_from_provider
    _agent_helpers.stream_fn_from_provider = lambda _provider: _stub_stream_fn  # type: ignore[assignment]
    _sentinel_provider = object()
    original_ai_provider = _ai.provider
    _ai.provider = lambda _name: _sentinel_provider  # type: ignore[assignment]
    gen_module = types.ModuleType("_generated_workflow")
    try:
        exec(compile(src, "<generated_workflow>", "exec"), gen_module.__dict__)  # noqa: S102
        await gen_module.main()  # type: ignore[attr-defined]
        gen_runtime: dict[str, Any] = gen_module._RUNTIME  # type: ignore[attr-defined]

        stub_factory = _make_stub_factory(responses={model: stub_text}, default=stub_text)
        interp_result = await execute(wf, stream_fn_factory=stub_factory)
        # Deterministic parts of the container agree (stack ORDER is best-effort
        # under parallelism, so compare state + out, not the trace).
        assert gen_runtime["state"] == dict(interp_result.runtime["state"])
        assert gen_runtime["out"] == dict(interp_result.runtime["out"])
    finally:
        _agent_helpers.stream_fn_from_provider = original_sfp  # type: ignore[assignment]
        _ai.provider = original_ai_provider  # type: ignore[assignment]
