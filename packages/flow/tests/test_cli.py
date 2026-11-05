"""Tests for flow.cli — validate, graph, run --dry-run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from xdog.flow.cli import main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).parent / "fixtures"
_LINEAR = str(_FIXTURES / "linear.json")
_BAD = str(_FIXTURES / "bad_missing_entry.json")


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_ok(capsys: pytest.CaptureFixture[str]) -> None:
    main(["validate", _LINEAR])
    out = capsys.readouterr().out
    assert "OK:" in out
    assert "linear-workflow" in out


def test_validate_bad_exits(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["validate", _BAD])
    assert exc_info.value.code == 1


def test_run_failure_prints_structured_envelope(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["run", "/nonexistent/path/workflow.json"])
    assert exc_info.value.code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["success"] is False
    assert data["output"] == {}
    assert data["message"]
    assert data["context"]["workflow"] == "workflow"


# ---------------------------------------------------------------------------
# graph
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# run --dry-run
# ---------------------------------------------------------------------------


async def test_run_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    """dry-run should complete without hitting any LLM and print JSON state."""

    from xdog.flow.cli import _cmd_run

    await _cmd_run(_LINEAR, provider=None, dry_run=True)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["success"] is True
    assert data["message"] == "Workflow completed"
    assert data["output"] == {}
    assert data["context"]["workflow"] == "linear-workflow"
    assert data["context"]["startTime"].endswith("Z")
    assert data["context"]["durationMs"] >= 0


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


def test_generate_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out_file = tmp_path / "workflow.py"
    main(["generate", _LINEAR, "-o", str(out_file)])
    assert out_file.exists()
    content = out_file.read_text()
    assert "async def main" in content


# ---------------------------------------------------------------------------
# --input K=V (override $in seed at run time)
# ---------------------------------------------------------------------------

_AGENT_CALC = str(Path(__file__).parent.parent / "examples" / "agent_calculator.json")


def test_run_input_overrides_seed(capsys: pytest.CaptureFixture[str]) -> None:
    """`run --input a=2 --input b=40 --dry-run` prints the workflow's $output."""
    main(["run", _AGENT_CALC, "--input", "a=2", "--input", "b=40", "--dry-run"])
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is True
    assert "result" in out["output"]
    assert out["output"]["result"].startswith("DRYRUN:")


def test_validate_json_reports_every_problem_at_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One round trip per mistake is the cost an authoring Agent cannot afford."""
    wf = json.loads((Path(__file__).parent.parent / "examples" / "refine_loop.json").read_text())
    wf["edges"][1]["map"] = {"nonexistent_port": "answer"}
    wf["nodes"][0]["inputs"] = ["topic", "never_fed"]
    wf["nodes"][1]["tools"] = ["no_such_tool"]
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(wf), encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        main(["validate", str(broken), "--json"])
    assert excinfo.value.code == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["workflow"] == "refine-loop"
    assert len(payload["errors"]) >= 4, payload["errors"]
    # every error says where it belongs, so a repair can be applied without parsing prose
    assert all("node" in e or "edge" in e for e in payload["errors"]), payload["errors"]
    located = {e.get("node") for e in payload["errors"]} | {
        (e["edge"]["from"], e["edge"]["to"]) for e in payload["errors"] if "edge" in e
    }
    assert {"draft", "critic", ("draft", "critic")} <= located
