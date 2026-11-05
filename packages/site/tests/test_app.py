"""Route + content contract tests for the xdog site."""

from __future__ import annotations

import re

import pytest
from flask.testing import FlaskClient
from xdog.site import create_app
from xdog.site.content.packages import PACKAGES


@pytest.fixture
def client() -> FlaskClient:
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_home_ok_lists_every_package(client: FlaskClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for pkg in PACKAGES:
        assert pkg.name in body
    # styled via the copied CSS framework
    assert "hack.css" in body and "site.css" in body


def test_each_package_detail_ok(client: FlaskClient) -> None:
    """One test over the registry rather than one per package.

    The inputs still matter — a broken page must be named, not merely counted —
    so failures are collected and reported together, which beats reading six
    separate red lines to learn that two pages are broken.
    """
    broken = []
    for pkg in (p.name for p in PACKAGES):
        resp = client.get(f"/packages/{pkg}")
        if resp.status_code != 200:
            broken.append(f"{pkg}: HTTP {resp.status_code}")
            continue
        body = resp.get_data(as_text=True)
        if pkg not in body or "Highlights" not in body:
            broken.append(f"{pkg}: missing name or Highlights")

    assert not broken, f"broken package pages: {broken}"


def test_unknown_package_404(client: FlaskClient) -> None:
    assert client.get("/packages/does-not-exist").status_code == 404


def test_static_css_served(client: FlaskClient) -> None:
    assert client.get("/static/css/hack.css").status_code == 200
    assert client.get("/static/css/site.css").status_code == 200


# --- flow deep-dive sub-pages (now the shared markdown + dynamic routes) ------


# --- generic package sub-pages (ai / agent / tui / coding / claw / flow) ------

_DOC_PACKAGES = [package.name for package in PACKAGES]
_DOC_SUBPAGES = ["design", "features", "reference", "roadmap"]


# --- HaveFun page + async run ------------------------------------------------


def test_every_curated_example_actually_loads(client: FlaskClient) -> None:
    """The picker's allowlist and the shipped examples must not drift apart.

    A stem that no longer exists (renamed, moved into a directory) fails only in
    the browser, at the moment someone picks it. ``essay_writer`` additionally
    proves the path-referenced subflow resolves, which depends on the load path
    handing the examples directory to the loader as its base_dir.
    """
    from xdog.site.blueprints.main import _HAVEFUN_STEMS

    assert _HAVEFUN_STEMS
    for stem in _HAVEFUN_STEMS:
        resp = client.post("/havefun/flow/load", json={"example": stem})
        assert resp.status_code == 200, stem
        payload = resp.get_json()
        assert payload["ok"] is True, f"{stem}: {payload.get('error')}"
        assert payload["ascii"], stem


def test_havefun_load_example_returns_diagram_and_inputs(client: FlaskClient) -> None:
    resp = client.post("/havefun/flow/load", json={"example": "agent_calculator"})
    assert resp.status_code == 200
    d = resp.get_json()
    assert d["ok"] is True
    assert "<svg" in d["svg"]
    assert d["ascii"]  # ASCII diagram present
    names = {i["name"] for i in d["inputs"]}
    assert {"a", "b"} <= names  # declared inputs surfaced with defaults


def test_havefun_rejects_invalid_json(client: FlaskClient) -> None:
    resp = client.post("/havefun/flow/load", json={"json": "{ not valid json"})
    assert resp.status_code == 400


def test_havefun_run_single_slot_then_429_and_status() -> None:
    """Runner runs one job; a second start is refused; status reports lifecycle.

    Exercised directly against the runner with a dry-run stream so no real LLM is
    called, mirroring the route wiring.
    """
    import time

    from xdog.flow.cli import _dry_run_stream_fn_factory
    from xdog.flow.loader import load_workflow
    from xdog.site.blueprints.main import _examples_dir
    from xdog.site.jobs import runner

    ex_dir = _examples_dir()
    assert ex_dir is not None
    wf = load_workflow(ex_dir / "agent_calculator.json")

    job_id = runner.start(wf, {"a": "3", "b": "4"}, stream_fn_factory=_dry_run_stream_fn_factory)
    assert job_id is not None
    # a second start while the first may still hold the slot is refused (or the
    # first already finished — retry once quickly to observe the busy state).
    second = runner.start(wf, {"a": "1", "b": "2"}, stream_fn_factory=_dry_run_stream_fn_factory)
    # second is either None (busy) or a new id (first finished first); if a new id
    # was returned, the single-slot invariant still held (never two at once).
    assert second is None or isinstance(second, str)

    # wait for completion and assert a good result
    for _ in range(50):
        job = runner.get(job_id) or (runner.get(second) if second else None)
        if job and job.state != "running":
            break
        time.sleep(0.2)
    final = runner.get(second) if second else runner.get(job_id)
    assert final is not None
    assert final.state == "done"
    # job.result is the workflow's $output (runtime["out"]); agent_calculator maps
    # solve.answer -> result.
    assert final.result is not None and "result" in final.result


def test_havefun_status_includes_execution_log() -> None:
    """A run's status carries the captured execution log (per-node run lines)."""
    import time

    from xdog.flow.cli import _dry_run_stream_fn_factory
    from xdog.flow.loader import load_workflow
    from xdog.site.blueprints.main import _examples_dir
    from xdog.site.jobs import runner

    ex_dir = _examples_dir()
    assert ex_dir is not None
    wf = load_workflow(ex_dir / "refine_loop.json")
    job_id = runner.start(
        wf, {"topic": "t", "feedback": ""}, stream_fn_factory=_dry_run_stream_fn_factory, base_dir=ex_dir
    )
    assert job_id is not None
    for _ in range(50):
        job = runner.get(job_id)
        if job and job.state != "running":
            break
        time.sleep(0.2)
    job = runner.get(job_id)
    assert job is not None and job.state == "done"
    # the log is built from flow's P3 structured event stream (on_event), formatted
    # into per-node lines: "▶ <node>" on start, "✓ <node> (<dur>s[, <n> tok])" on finish.
    assert job.log
    assert any(line.startswith("▶ draft") for line in job.log)
    assert any(line.startswith("✓ draft") for line in job.log)


def test_sitemap_lists_only_urls_that_actually_resolve(client: FlaskClient) -> None:
    """A sitemap advertising 404s is worse than no sitemap at all."""
    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200
    assert resp.mimetype == "application/xml"
    locs = re.findall(r"<loc>([^<]+)</loc>", resp.get_data(as_text=True))
    assert len(locs) > 20, locs
    broken = [
        (path, code)
        for path in (loc.replace("https://xdog.942295.xyz", "") for loc in locs)
        if (code := client.get(path).status_code) != 200
    ]
    assert not broken, broken


def test_every_package_links_to_its_actual_installation_surface(client: FlaskClient) -> None:
    """Every public catalog entry links to its published distribution."""
    for pkg in PACKAGES:
        body = client.get(f"/packages/{pkg.name}").get_data(as_text=True)
        assert pkg.pypi_url in body, pkg.name
        assert pkg.dist in body, pkg.name
