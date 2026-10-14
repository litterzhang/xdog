"""Workflow scheduling (docs/scheduling.md).

A top-level ``schedule`` block declares how a workflow fires on its own (timer or
hook). It is declarative config for ``xdog-flow scheduling install`` — the engine ignores it.
flow.scheduler renders the systemd units / crontab lines.
"""

from __future__ import annotations

import json as _json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import xdog.flow.scheduler.install as install_mod
from xdog.flow.builder.serialize import workflow_to_dict
from xdog.flow.errors import WorkflowValidationError
from xdog.flow.loader import parse_workflow, validate_workflow
from xdog.flow.models import ScheduleDef
from xdog.flow.scheduler.install import Installer
from xdog.flow.scheduler.systemd import (
    cron_to_oncalendar,
    render_listener_service,
    render_timer_crontab,
    render_timer_units,
)


def _wf(schedule: dict[str, Any] | None) -> dict[str, Any]:
    d: dict[str, Any] = {
        "name": "s",
        "entry": "a",
        "nodes": [{"id": "a", "type": "agent", "backend": "claude-cli", "prompt": "hi", "outputs": ["out"]}],
        "edges": [{"from": "a", "to": "$output", "map": {"out": "r"}}],
    }
    if schedule is not None:
        d["schedule"] = schedule
    return d


def test_no_schedule_is_run_once() -> None:
    wf = parse_workflow(_wf(None))
    validate_workflow(wf)
    assert wf.schedule is None


def test_timer_every_parsed() -> None:
    wf = parse_workflow(_wf({"mode": "timer", "every": "15m", "inputs": {"report": "x"}}))
    validate_workflow(wf)
    assert wf.schedule is not None
    assert wf.schedule.mode == "timer"
    assert wf.schedule.every == "15m"
    assert wf.schedule.cron is None
    assert dict(wf.schedule.inputs) == {"report": "x"}


def test_timer_cron_parsed() -> None:
    wf = parse_workflow(_wf({"mode": "timer", "cron": "*/15 * * * *"}))
    validate_workflow(wf)
    assert wf.schedule is not None
    assert wf.schedule.cron == "*/15 * * * *"
    assert wf.schedule.every is None


def test_hook_parsed() -> None:
    wf = parse_workflow(_wf({"mode": "hook", "signal": "new-ticket",
                             "listen": {"type": "http", "path": "/hooks/t", "port": 8787}}))
    validate_workflow(wf)
    assert wf.schedule is not None
    assert wf.schedule.mode == "hook"
    assert wf.schedule.signal == "new-ticket"
    assert wf.schedule.listen == {"type": "http", "path": "/hooks/t", "port": 8787}


def test_schedule_rejects() -> None:

    def _check_case(schedule: "dict[str, Any]", match: "str") -> "None":
        with pytest.raises(WorkflowValidationError, match=match):
            parse_workflow(_wf(schedule))

    for schedule, match in [
        ({"mode": "every-minute"}, "'timer' or 'hook'"),
        ({"mode": "timer", "every": "15m", "cron": "* * * * *"}, "exactly one"),
        ({"mode": "timer"}, "exactly one"),
        ({"mode": "timer", "every": "15min"}, "like '30s'"),
        ({"mode": "timer", "cron": "* * *"}, "5-field cron"),
        ({"mode": "hook", "listen": {"type": "http"}}, "non-empty"),
        ({"mode": "hook", "signal": "s"}, "'listen' object"),
        ({"mode": "hook", "signal": "s", "listen": {"type": "grpc"}}, "listen.type"),
    ]:
        _check_case(schedule, match)


def test_schedule_not_object_rejected() -> None:
    with pytest.raises(WorkflowValidationError, match="schedule must be an object"):
        parse_workflow(_wf("15m"))  # type: ignore[arg-type]


@pytest.mark.parametrize("schedule", [
    {"mode": "timer", "every": "30s"},
    {"mode": "timer", "cron": "0 9 * * 1-5", "inputs": {"n": 3}},
    {"mode": "hook", "signal": "sig", "listen": {"type": "file", "dir": "/tmp/drop"}},
    {"mode": "hook", "signal": "sig", "listen": {"type": "socket", "path": "/run/x.sock"}},
])
def test_schedule_roundtrips(schedule: dict[str, Any]) -> None:
    wf = parse_workflow(_wf(schedule))
    assert parse_workflow(workflow_to_dict(wf)) == wf


# --- Phase 2: systemd / crontab unit rendering -----------------------------

_B = Path("/home/u/.local/share/xdog-flow/triage")


def test_timer_every_units() -> None:
    r = render_timer_units("triage", _B, ScheduleDef(mode="timer", every="15m", inputs=(("report", "x"),)))
    assert set(r.files) == {"triage.service", "triage.timer"}
    assert r.enable == ("triage.timer",)
    tmr = r.files["triage.timer"]
    assert "OnUnitActiveSec=15min" in tmr and "Persistent=true" in tmr
    svc = r.files["triage.service"]
    assert "Type=oneshot" in svc
    assert "Environment=FLOW_RUN_ID=triage" in svc
    assert 'Environment=FLOW_INPUTS={"report": "x"}' in svc
    assert f"ExecStart=/usr/bin/python3 {_B}" in svc


def test_timer_cron_units() -> None:
    r = render_timer_units("triage", _B, ScheduleDef(mode="timer", cron="*/15 * * * *"))
    assert "OnCalendar=*-*-* *:0/15:00" in r.files["triage.timer"]


def test_timer_no_inputs_omits_env() -> None:
    r = render_timer_units("t", _B, ScheduleDef(mode="timer", every="1h"))
    assert "FLOW_INPUTS" not in r.files["t.service"]


@pytest.mark.parametrize(("cron", "oncal"), [
    ("*/15 * * * *", "*-*-* *:0/15:00"),
    ("0 9 * * 1-5", "Mon..Fri *-*-* 9:0:00"),
    ("30 2 1 * *", "*-*-1 2:30:00"),
    ("0 0 * * 0", "Sun *-*-* 0:0:00"),
])
def test_cron_to_oncalendar(cron: str, oncal: str) -> None:
    assert cron_to_oncalendar(cron) == oncal


def test_cron_bad_dow_raises() -> None:
    with pytest.raises(ValueError, match="day-of-week"):
        cron_to_oncalendar("0 0 * * xyz")


def test_crontab_fallback_cron() -> None:
    line = render_timer_crontab("triage", _B, ScheduleDef(mode="timer", cron="*/15 * * * *"))
    assert line.startswith("*/15 * * * * FLOW_RUN_ID=triage /usr/bin/python3 ")


def test_crontab_fallback_every_minutes() -> None:
    line = render_timer_crontab("t", _B, ScheduleDef(mode="timer", every="30m"))
    assert line.startswith("*/30 * * * *")


def test_crontab_fallback_subminute_raises() -> None:
    with pytest.raises(ValueError, match="sub-minute"):
        render_timer_crontab("t", _B, ScheduleDef(mode="timer", every="30s"))


def test_listener_service_render() -> None:
    svc = render_listener_service()
    assert "Description=xdog-flow shared hook listener" in svc
    assert "flow.scheduler.listener --registry" in svc
    assert "Restart=on-failure" in svc
    assert "WantedBy=default.target" in svc


# --- Phase 3: scheduling CLI + registry ------------------------------------


def test_scheduling_cli_subcommands(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import xdog.flow.cli as cli

    calls: list[tuple[str, object]] = []

    class FakeInstaller:
        def install(
            self,
            wf: object,
            *,
            name: str | None,
            dry_run: bool,
            base_dir: Path | None = None,
            venv: bool = True,
            confine: object = None,
            start: bool = True,
        ) -> str:
            # base_dir carries the workflow's sibling modules into the bundle.
            calls.append(("install", (wf, name, dry_run, base_dir is not None)))
            return name or "demo"

        def delete(self, name: str, *, dry_run: bool) -> None:
            calls.append(("uninstall", (name, dry_run)))

        def list_installed(self) -> list[dict[str, object]]:
            calls.append(("list", None))
            return [{"name": "demo", "mode": "timer", "bundle": "/tmp/demo"}]

    workflow = object()
    monkeypatch.setattr(cli, "_scheduling_installer", lambda python=None: FakeInstaller())
    monkeypatch.setattr(cli, "load_any", lambda path: workflow)

    cli.main(["scheduling", "install", "workflow.json", "--name", "custom", "--dry-run"])
    cli.main(["scheduling", "uninstall", "custom", "--dry-run"])
    cli.main(["scheduling", "list"])

    assert calls == [
        ("install", (workflow, "custom", True, True)),
        ("uninstall", ("custom", True)),
        ("list", None),
    ]
    output = capsys.readouterr().out
    assert "would install custom" in output
    assert "would uninstall custom" in output
    assert "demo" in output


def test_legacy_install_command_is_removed() -> None:
    import xdog.flow.cli as cli

    with pytest.raises(SystemExit):
        cli.main(["install", "workflow.json"])


# --- Installer internals ---------------------------------------------------


def _installer(tmp_path: Path) -> tuple[Installer, Path]:
    """An Installer wired to tmp dirs with a stub systemctl that logs calls."""
    import stat as _stat

    stub = tmp_path / "systemctl"
    log = tmp_path / "calls.log"
    stub.write_text(f'#!/usr/bin/env bash\necho "systemctl $@" >> "{log}"\n', encoding="utf-8")
    stub.chmod(stub.stat().st_mode | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH)
    # Registry/unit tests do not need a real uv environment for every install.
    # The dedicated provisioning test below checks that boundary separately.
    class RegistryInstaller(Installer):
        def _provision_venv(self, bundle_dir: Path) -> Path:
            return bundle_dir / ".venv" / "bin" / "python"

    inst = RegistryInstaller(unit_dir=tmp_path / "units", data_dir=tmp_path / "data", systemctl=(str(stub),))
    return inst, log


def _timer_wf(name: str = "triage") -> Any:
    return parse_workflow({
        "name": name, "entry": "a",
        "schedule": {"mode": "timer", "every": "15m", "inputs": {"report": "x"}},
        "nodes": [{"id": "a", "type": "agent", "backend": "claude-cli", "prompt": "hi", "outputs": ["out"]}],
        "edges": [{"from": "a", "to": "$output", "map": {"out": "r"}}],
    })


def _hook_wf(name: str = "deploy") -> Any:
    return parse_workflow({
        "name": name, "entry": "a",
        "schedule": {"mode": "hook", "signal": "go",
                     "listen": {"type": "http", "path": f"/hooks/{name}", "port": 8787}},
        "nodes": [{"id": "a", "type": "agent", "backend": "claude-cli", "prompt": "hi", "outputs": ["out"]}],
        "edges": [{"from": "a", "to": "$output", "map": {"out": "r"}}],
    })


def test_install_timer_writes_units_and_registry(tmp_path: Path) -> None:
    inst, log = _installer(tmp_path)
    inst.install(_timer_wf())
    units = {p.name for p in (tmp_path / "units").iterdir()}
    assert units == {"triage.service", "triage.timer"}
    reg = _json.loads(inst.registry_path.read_text())
    assert reg["triage"]["mode"] == "timer"
    calls = log.read_text()
    assert "daemon-reload" in calls
    assert "enable --now triage.timer" in calls


def test_install_hook_creates_shared_listener_once(tmp_path: Path) -> None:
    inst, _ = _installer(tmp_path)
    inst.install(_hook_wf("deploy"))
    inst.install(_hook_wf("triage2"))
    units = {p.name for p in (tmp_path / "units").iterdir()}
    # ONE shared listener, no per-workflow hook unit
    assert units == {"xdog-flow-listener.service"}
    reg = _json.loads(inst.registry_path.read_text())
    assert set(reg) == {"deploy", "triage2"}
    assert reg["deploy"]["signal"] == "go"


def test_list_installed(tmp_path: Path) -> None:
    inst, _ = _installer(tmp_path)
    inst.install(_timer_wf("t1"))
    inst.install(_hook_wf("h1"))
    names = {e["name"]: e["mode"] for e in inst.list_installed()}
    assert names == {"t1": "timer", "h1": "hook"}


def test_delete_timer_round_trips(tmp_path: Path) -> None:
    inst, _ = _installer(tmp_path)
    inst.install(_timer_wf())
    inst.delete("triage")
    assert not any((tmp_path / "units").iterdir())
    assert inst.list_installed() == []


def test_delete_last_hook_tears_down_listener(tmp_path: Path) -> None:
    inst, log = _installer(tmp_path)
    inst.install(_hook_wf("h1"))
    inst.install(_hook_wf("h2"))
    inst.delete("h1")
    # listener stays while h2 remains
    assert (tmp_path / "units" / "xdog-flow-listener.service").exists()
    inst.delete("h2")
    # last hook gone -> listener torn down
    assert not (tmp_path / "units" / "xdog-flow-listener.service").exists()
    assert "disable --now xdog-flow-listener.service" in log.read_text()


def test_delete_unknown_raises(tmp_path: Path) -> None:
    inst, _ = _installer(tmp_path)
    with pytest.raises(ValueError, match="no installed workflow named"):
        inst.delete("nope")


def test_install_no_schedule_raises(tmp_path: Path) -> None:
    inst, _ = _installer(tmp_path)
    wf = parse_workflow({
        "name": "x", "entry": "a",
        "nodes": [{"id": "a", "type": "agent", "backend": "claude-cli", "prompt": "hi", "outputs": ["o"]}],
        "edges": [{"from": "a", "to": "$output", "map": {"o": "r"}}],
    })
    with pytest.raises(ValueError, match="no 'schedule' block"):
        inst.install(wf)


def test_install_dry_run_touches_nothing(tmp_path: Path) -> None:
    inst, log = _installer(tmp_path)
    inst.install(_timer_wf(), dry_run=True)
    assert not (tmp_path / "units").exists()
    assert not inst.registry_path.exists()
    assert not log.exists()  # no systemctl actually invoked


# --- Phase 4: shared hook listener (routing) -------------------------------


def _routes_two_http() -> list[Any]:
    from xdog.flow.scheduler.listener import HookRoute

    return [
        HookRoute("triage", Path("/b/triage"), "new-ticket",
                  {"type": "http", "path": "/hooks/triage", "port": 8787}),
        HookRoute("deploy", Path("/b/deploy"), "go",
                  {"type": "http", "path": "/hooks/deploy", "port": 8787}),
    ]


def test_listener_routes_http_by_path_with_env() -> None:
    from xdog.flow.scheduler.listener import Router

    spawned: list[tuple[str, dict[str, str]]] = []
    r = Router(routes=_routes_two_http(), spawn=lambda b, e: spawned.append((str(b), dict(e))))
    r.deliver_http("/hooks/triage", b'{"report": "boom"}')
    bundle, env = spawned[-1]
    assert bundle == "/b/triage"
    assert env["FLOW_SIGNALS"] == "new-ticket"
    assert env["FLOW_RUN_ID"] == "triage"
    assert '"report": "boom"' in env["FLOW_INPUTS"]


def test_listener_two_workflows_share_one_router() -> None:
    from xdog.flow.scheduler.listener import Router

    spawned: list[str] = []
    r = Router(routes=_routes_two_http(), spawn=lambda b, e: spawned.append(str(b)))
    r.deliver_http("/hooks/triage", b"")
    r.deliver_http("/hooks/deploy", b"")
    assert spawned == ["/b/triage", "/b/deploy"]
    assert r.http_paths() == ["/hooks/deploy", "/hooks/triage"]


def test_listener_empty_body_omits_inputs() -> None:
    from xdog.flow.scheduler.listener import Router

    seen: list[dict[str, str]] = []
    r = Router(routes=_routes_two_http(), spawn=lambda b, e: seen.append(dict(e)))
    r.deliver_http("/hooks/deploy", b"")
    assert "FLOW_INPUTS" not in seen[-1]


def test_listener_unknown_path_raises() -> None:
    from xdog.flow.scheduler.listener import Router

    r = Router(routes=_routes_two_http(), spawn=lambda b, e: None)
    with pytest.raises(KeyError, match="no hook workflow bound"):
        r.deliver_http("/nope", b"{}")


def test_listener_file_routing() -> None:
    from xdog.flow.scheduler.listener import HookRoute, Router

    seen: list[tuple[str, dict[str, str]]] = []
    routes = [HookRoute("ingest", Path("/b/ingest"), "file-in", {"type": "file", "dir": "/tmp/drop"})]
    r = Router(routes=routes, spawn=lambda b, e: seen.append((str(b), dict(e))))
    r.deliver_file("/tmp/drop", b'{"path": "/data"}')
    assert seen[-1][0] == "/b/ingest"
    assert seen[-1][1]["FLOW_SIGNALS"] == "file-in"
    assert '"path": "/data"' in seen[-1][1]["FLOW_INPUTS"]


def test_listener_from_registry_integration(tmp_path: Path) -> None:
    """The listener reads exactly the hook routes an install wrote (Phase 3 -> 4)."""
    from xdog.flow.scheduler.listener import Router

    inst, _ = _installer(tmp_path)
    inst.install(_hook_wf("deploy"))
    inst.install(_timer_wf("t1"))  # a timer must NOT become a route
    spawned: list[str] = []
    r = Router.from_registry(inst.registry_path, spawn=lambda b, e: spawned.append(str(b)))
    assert r.http_paths() == ["/hooks/deploy"]  # only the hook, not the timer
    r.deliver_http("/hooks/deploy", b'{"x": 1}')
    assert spawned and spawned[-1].endswith("/deploy")


async def test_listener_signal_reaches_human_node(tmp_path: Path) -> None:
    """The delivered signal makes a human node proceed instead of pausing."""
    from xdog.flow.executor import execute

    # A workflow that pauses at a human node unless its signal is delivered.
    wf = parse_workflow({
        "name": "gated", "entry": "gate", "state": {},
        "nodes": [{"id": "gate", "type": "human", "signal": "go", "outputs": ["ok"]}],
        "edges": [{"from": "gate", "to": "$output", "map": {"ok": "result"}}],
    })
    # Signal delivered (as the listener would via FLOW_SIGNALS) -> proceeds.
    r = await execute(wf, signals={"go"})
    assert r.runtime["out"]["result"] == "approved"


# --- regression: human-node signal round-trips (found via a hook example) ---


def test_human_signal_roundtrips() -> None:
    wf = parse_workflow({
        "name": "g", "entry": "gate",
        "nodes": [{"id": "gate", "type": "human", "signal": "go", "outputs": ["ok"]}],
        "edges": [{"from": "gate", "to": "$output", "map": {"ok": "r"}}],
    })
    assert wf.nodes[0].signal == "go"
    rt = parse_workflow(workflow_to_dict(wf))
    assert rt == wf
    assert rt.nodes[0].signal == "go"


# --- start timeout + jitter --------------------------------------------------


def test_timer_service_always_bounds_its_start_timeout() -> None:
    """A Type=oneshot unit inherits DefaultTimeoutStartSec (90s on most distros).

    That would kill essentially any workflow that talks to a model, so an unset
    schedule.timeout must still render an explicit, generous bound rather than
    leaving the unit to inherit the default.
    """
    wf = parse_workflow(_wf({"mode": "timer", "cron": "0 */4 * * *"}))
    units = render_timer_units("nightly", Path("/srv/bundle"), wf.schedule)
    service = units.files["nightly.service"]
    assert "TimeoutStartSec=1h" in service


def test_schedule_timeout_and_jitter_reach_the_units() -> None:
    wf = parse_workflow(_wf({"mode": "timer", "cron": "0 */4 * * *", "timeout": "40m", "jitter": "15m"}))
    units = render_timer_units("enrich", Path("/srv/bundle"), wf.schedule)
    assert "TimeoutStartSec=40min" in units.files["enrich.service"]
    assert "RandomizedDelaySec=15min" in units.files["enrich.timer"]


def test_jitter_is_absent_unless_asked_for() -> None:
    wf = parse_workflow(_wf({"mode": "timer", "every": "30m"}))
    assert "RandomizedDelaySec" not in render_timer_units("t", Path("/b"), wf.schedule).files["t.timer"]


def test_schedule_durations_round_trip_through_the_serializer() -> None:
    from xdog.flow.builder.serialize import workflow_to_dict

    wf = parse_workflow(_wf({"mode": "timer", "cron": "0 */4 * * *", "timeout": "40m", "jitter": "15m"}))
    sched = workflow_to_dict(wf)["schedule"]
    assert sched["timeout"] == "40m"
    assert sched["jitter"] == "15m"


@pytest.mark.parametrize("bad", ["40", "40mins", "-5m", "m"])
def test_bad_duration_is_rejected_at_load(bad: str) -> None:
    with pytest.raises(WorkflowValidationError, match="schedule.timeout"):
        parse_workflow(_wf({"mode": "timer", "cron": "0 */4 * * *", "timeout": bad}))


def test_install_python_is_selectable(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """The unit's ExecStart must be able to name an interpreter that has the deps.

    A bundle needs jsonpath-ng (and httpx/pydantic/fastjsonschema for SDK agents).
    /usr/bin/python3 rarely has them, so a unit hard-wired to it fails on its very
    first firing — with nothing but a systemd status to explain why.
    """
    from xdog.flow import cli

    wf_path = "wf.json"
    monkeypatch.setattr(cli, "load_any", lambda _p: parse_workflow(_wf({"mode": "timer", "every": "15m"})))
    cli.main(
        ["scheduling", "install", wf_path, "--dry-run", "--no-venv", "--python", "/opt/venv/bin/python"]
    )
    assert "/opt/venv/bin/python" in capsys.readouterr().out


# --- uv provisioning ---------------------------------------------------------


def test_install_provisions_the_bundle_with_uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """install detects uv, then has uv build the bundle's environment.

    The bundle ships a pyproject, so one `uv sync` picks the interpreter (fetching
    a CPython when the host has none), makes .venv, and installs the deps. The unit
    then runs that interpreter instead of /usr/bin/python3, which on a typical host
    has none of them.
    """
    ran: list[list[str]] = []

    def _fake_run(cmd: list[str], **kw: object) -> object:
        ran.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(install_mod.subprocess, "run", _fake_run)
    inst = Installer(
        unit_dir=tmp_path / "units",
        data_dir=tmp_path / "data",
        systemctl=("true",),
        uv="/fake/uv",
    )
    wf = parse_workflow(_wf({"mode": "timer", "cron": "0 */4 * * *"}))
    inst.install(wf, name="demo")

    sync = next(c for c in ran if c[:2] == ["/fake/uv", "sync"])
    assert "--project" in sync
    service = (tmp_path / "units" / "demo.service").read_text(encoding="utf-8")
    assert str(tmp_path / "data" / "demo" / ".venv" / "bin" / "python") in service


def test_install_falls_back_to_a_named_interpreter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """venv=False keeps the old behaviour for an environment you already maintain."""
    monkeypatch.setattr(
        install_mod.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", "")
    )
    inst = Installer(
        unit_dir=tmp_path / "units",
        data_dir=tmp_path / "data",
        systemctl=("true",),
        python="/opt/env/bin/python",
    )
    wf = parse_workflow(_wf({"mode": "timer", "cron": "0 */4 * * *"}))
    inst.install(wf, name="demo", venv=False)
    assert "/opt/env/bin/python" in (tmp_path / "units" / "demo.service").read_text(encoding="utf-8")


def _installed(tmp_path: Path, mode: str = "timer") -> tuple[Any, list[list[str]]]:
    """An Installer against a temp dir with a recording systemctl."""
    from xdog.flow.scheduler.install import Installer

    calls: list[list[str]] = []

    class _Recording(Installer):
        def _run(self, *args: str, dry_run: bool = False) -> None:  # type: ignore[override]
            calls.append(list(args))

    inst = _Recording(unit_dir=tmp_path / "units", data_dir=tmp_path / "data")
    entry: dict[str, object] = {"mode": mode, "bundle": str(tmp_path / "b")}
    if mode == "timer":
        entry["units"] = ["wf.timer", "wf.service"]
    else:
        entry["units"] = []
        entry["listen"] = {"type": "http", "path": "/h"}
    inst._register("wf", entry)
    return inst, calls


def test_stop_disarms_the_timer_and_ends_a_run_in_progress(tmp_path: Path) -> None:
    """Stopping only the timer leaves a build already running to finish — which
    is how uninstall used to delete a bundle out from under the interpreter
    executing it."""
    inst, calls = _installed(tmp_path)

    inst.stop("wf")

    assert ["stop", "wf.timer"] in calls
    assert ["stop", "wf.service"] in calls
    assert calls.index(["stop", "wf.timer"]) < calls.index(["stop", "wf.service"]), (
        "the timer first, or it could re-trigger the service between the two"
    )


def test_uninstall_stops_before_it_deletes(tmp_path: Path) -> None:
    bundle = tmp_path / "b"
    bundle.mkdir(parents=True)
    inst, calls = _installed(tmp_path)

    inst.delete("wf")

    assert ["stop", "wf.service"] in calls
    stopped = calls.index(["stop", "wf.service"])
    assert all(c[0] != "disable" or calls.index(c) > stopped for c in calls if c[0] == "disable")
    assert not bundle.exists()


def test_install_can_hold_off_arming(tmp_path: Path) -> None:
    """Installing and arming are separate decisions. `enable` still happens, so
    it survives a reboot and runs at its next scheduled time."""
    from xdog.flow.models import ScheduleDef

    inst, calls = _installed(tmp_path)
    sched = ScheduleDef(mode="timer", every="1h")

    inst._install_timer("wf", tmp_path / "b", sched, dry_run=False, start=False)
    assert ["enable", "wf.timer"] in calls
    assert ["enable", "--now", "wf.timer"] not in calls

    calls.clear()
    inst._install_timer("wf", tmp_path / "b", sched, dry_run=False, start=True)
    assert ["enable", "--now", "wf.timer"] in calls


def test_stopping_a_hook_workflow_actually_stops_it(tmp_path: Path) -> None:
    """A hook workflow has no unit of its own, so the registry is the only place
    it can be disarmed — and the listener has to honour that, or stopping one is
    cosmetic while events keep firing it."""
    from xdog.flow.scheduler.listener import Router

    inst, calls = _installed(tmp_path, mode="hook")

    inst.stop("wf")

    assert Router.from_registry(inst.registry_path).routes == []
    assert any(c[0] == "reload-or-restart" for c in calls), "the listener must be told"

    inst.start("wf")
    assert [r.name for r in Router.from_registry(inst.registry_path).routes] == ["wf"]
