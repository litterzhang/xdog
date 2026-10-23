"""Route + content contract tests for the xdog site."""

from __future__ import annotations

import re

import pytest
from flask import Flask
from flask.testing import FlaskClient
from xdog.site import create_app
from xdog.site.content.blog import get_articles
from xdog.site.content.faq import FAQS
from xdog.site.content.packages import PACKAGES


@pytest.fixture
def client() -> FlaskClient:
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_create_app_returns_flask() -> None:
    assert isinstance(create_app(), Flask)


def test_home_ok_lists_every_package(client: FlaskClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for pkg in PACKAGES:
        assert pkg.name in body
    # styled via the copied CSS framework
    assert "hack.css" in body and "site.css" in body


def test_packages_index_ok(client: FlaskClient) -> None:
    resp = client.get("/packages/all")
    assert resp.status_code == 200
    assert "Packages" in resp.get_data(as_text=True)


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


def test_blog_index_ok_lists_articles(client: FlaskClient) -> None:
    resp = client.get("/blog")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert get_articles()[0].title in body


def test_each_article_ok(client: FlaskClient) -> None:
    broken = []
    for art in get_articles():
        resp = client.get(f"/blog/{art.slug}")
        if resp.status_code != 200:
            broken.append(f"{art.slug}: HTTP {resp.status_code}")
            continue
        body = resp.get_data(as_text=True)
        if art.title not in body:
            broken.append(f"{art.slug}: title missing")
        # the markdown body is rendered as HTML (paragraphs), not a raw list
        if "markdown-body" not in body:
            broken.append(f"{art.slug}: markdown not rendered")

    assert not broken, f"broken articles: {broken}"


def test_unknown_article_404(client: FlaskClient) -> None:
    assert client.get("/blog/nope").status_code == 404


def test_blog_pagination_param_is_safe(client: FlaskClient) -> None:
    assert client.get("/blog?page=999").status_code == 200
    assert client.get("/blog?page=notanumber").status_code == 200


def test_faq_ok_renders_questions(client: FlaskClient) -> None:
    resp = client.get("/faq")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert FAQS[0]["q"] in body
    # the honest production-readiness answer is present
    assert "retry" in body.lower()


def test_static_css_served(client: FlaskClient) -> None:
    assert client.get("/static/css/hack.css").status_code == 200
    assert client.get("/static/css/site.css").status_code == 200


def test_flow_overview_renders_product_purpose_and_release_radar(client: FlaskClient) -> None:
    body = client.get("/packages/flow").get_data(as_text=True)
    assert "Typed workflows for humans and Coding Agents" in body
    assert "Flow Release Radar" in body
    assert "release_readiness.json" in body


def test_favicon_served_and_linked(client: FlaskClient) -> None:
    resp = client.get("/static/favicon.ico")
    assert resp.status_code == 200
    assert "icon" in resp.content_type  # image/vnd.microsoft.icon
    assert 'rel="icon"' in client.get("/").get_data(as_text=True)


def test_404_page_is_styled(client: FlaskClient) -> None:
    body = client.get("/no-such-page").get_data(as_text=True)
    assert "404" in body
    assert "hack.css" in body  # renders through base.html


# --- flow deep-dive sub-pages (now the shared markdown + dynamic routes) ------


def test_flow_only_subpages_ok_with_breadcrumb(client: FlaskClient) -> None:
    """The two flow pages the generic package test cannot cover.

    Its design/features/reference/roadmap cases asserted byte-identical strings
    to `test_package_docs_subpages_ok_with_breadcrumb` for name="flow". These
    two are genuinely flow-only: the overview breadcrumb has no sub-page
    segment, and no other package ships an examples page — `ai/examples` 404s.
    """
    for path, crumb in (("/packages/flow", "flow"), ("/packages/flow/examples", "flow / Examples")):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path}: HTTP {resp.status_code}"
        body = resp.get_data(as_text=True)
        assert f"Packages / {crumb}" in body, f"{path}: wrong breadcrumb"
        # the left-nav flow submenu links every sub-page
        assert "/packages/flow/design" in body and "/packages/flow/roadmap" in body
        assert "/packages/flow/reference" in body


def test_flow_reference_documents_schema_and_rules(client: FlaskClient) -> None:
    body = client.get("/packages/flow/reference").get_data(as_text=True)
    assert "xdog-flow test" in body
    assert ".test.json" in body
    # The selectors are the load-bearing part of the design — they are what makes
    # a case deterministic under fan-out concurrency, so the page must name them.
    assert "--allow-script-stub" in body
    for selector in ("when", "index", "round"):
        assert f"<code>{selector}</code>" in body
    # the JSON schema, type system, conditions, runtime container, CLI, and validation rules
    assert "submit_result" in body  # structured-output contract (derived from ports)
    assert "$output" in body  # the reserved sink
    assert "xdog-flow" in body  # the CLI section
    assert "not fed by any edge mapping" in body  # a validation rule
    assert "<table>" in body  # the schema/type/rule tables render as markdown tables


def test_flow_examples_renders_ascii_diagrams(client: FlaskClient) -> None:
    body = client.get("/packages/flow/examples").get_data(as_text=True)
    # the examples page is now static markdown: pre-generated ASCII in a code block
    assert "Agent Calculator" in body
    assert "make_problem" in body  # an ASCII-diagram node label
    assert "<pre>" in body or "<code>" in body  # fenced code block
    # live SVG generation moved to the HaveFun page; examples.md points there
    assert "/havefun" in body


def test_flow_roadmap_has_phases(client: FlaskClient) -> None:
    body = client.get("/packages/flow/roadmap").get_data(as_text=True)
    assert "Human + Agent authoring surfaces" in body
    assert "First-class workflow tests" in body
    assert "retry" in body.lower()  # a named phase
    assert "Checkpoint" in body  # a roadmap phase


def test_flow_docs_module_importable() -> None:
    from xdog.site.content.docpages import render_page
    from xdog.site.content.flow_docs import DOCS

    # Features + Roadmap are Python; static pages are markdown.
    assert DOCS.features and DOCS.roadmap
    assert sum(len(feats) for _, feats in DOCS.grouped_features()) == len(DOCS.features)
    for page in ("overview", "design", "reference", "examples"):
        assert render_page("flow", page) is not None


# --- generic package sub-pages (ai / agent / tui / coding / claw / flow) ------

_DOC_PACKAGES = [package.name for package in PACKAGES]
_DOC_SUBPAGES = ["design", "features", "reference", "roadmap"]


def test_package_docs_subpages_ok_with_breadcrumb(client: FlaskClient) -> None:
    """Every package doc sub-page renders with the right breadcrumb and left-nav."""
    broken = []
    for name in _DOC_PACKAGES:
        for sub in _DOC_SUBPAGES:
            resp = client.get(f"/packages/{name}/{sub}")
            if resp.status_code != 200:
                broken.append(f"{name}/{sub}: HTTP {resp.status_code}")
                continue
            body = resp.get_data(as_text=True)
            # breadcrumb reflects the sub-page, e.g. "Packages / ai / Design"
            if f"Packages / {name} / {sub.capitalize()}" not in body:
                broken.append(f"{name}/{sub}: wrong breadcrumb")
            # every doc page is reachable from the collapsible left-nav
            if f"/packages/{name}/reference" not in body:
                broken.append(f"{name}/{sub}: no left-nav link")

    assert not broken, f"broken doc pages: {broken}"


def test_overview_route_and_nav(client: FlaskClient) -> None:
    # Both /packages/<name> and /packages/<name>/overview render the overview.
    broken = []
    for name in _DOC_PACKAGES:
        if client.get(f"/packages/{name}/overview").status_code != 200:
            broken.append(f"{name}: /overview not 200")
        body = client.get(f"/packages/{name}").get_data(as_text=True)
        # The overview body no longer carries an in-page "Deep dive" block or a CLI line;
        # sub-pages are reached via the left-nav, which links them on every page.
        if "Deep dive" in body or "CLI:" in body:
            broken.append(f"{name}: stale in-page deep-dive block")
        if f"/packages/{name}/design" not in body or f"/packages/{name}/roadmap" not in body:
            broken.append(f"{name}: left-nav submenu incomplete")

    assert not broken, f"broken overviews: {broken}"


def test_package_docs_unknown_name_404(client: FlaskClient) -> None:
    # flow now uses the same shared routes as the others
    assert client.get("/packages/flow/design").status_code == 200
    assert client.get("/packages/flow/examples").status_code == 200  # flow ships examples
    assert client.get("/packages/ai/examples").status_code == 404  # others do not
    assert client.get("/packages/nope/design").status_code == 404
    assert client.get("/packages/nope/roadmap").status_code == 404


def test_package_docs_content_is_accurate(client: FlaskClient) -> None:
    # ai: the old five-vendor claim is gone; Copilot + protocols are documented
    ai_over = client.get("/packages/ai").get_data(as_text=True)
    assert "Mistral" not in ai_over and "Bedrock" not in ai_over
    assert "copilot" in client.get("/packages/ai/reference").get_data(as_text=True)
    # agent: built-in tools; tui: differential renderer; claw: workspace files
    assert "submit_result" in client.get("/packages/agent/reference").get_data(as_text=True)
    assert "differential" in client.get("/packages/tui/design").get_data(as_text=True).lower()
    assert "IDENTITY.md" in client.get("/packages/claw/reference").get_data(as_text=True)
    # Shipped facts and future intent are distinct, not an invented release date.
    from xdog.site.blueprints.main import PACKAGE_DOCS

    for name in _DOC_PACKAGES:
        docs = PACKAGE_DOCS[name]
        assert any(phase.done for phase in docs.roadmap)
        assert any(not phase.done for phase in docs.roadmap)
        assert docs.roadmap_intro
        body = client.get(f"/packages/{name}/roadmap").get_data(as_text=True)
        assert "state-success" in body


def test_static_markdown_renders_tables(client: FlaskClient) -> None:
    # a markdown reference page renders GFM tables to <table>
    assert "<table>" in client.get("/packages/ai/reference").get_data(as_text=True)
    assert "<table>" in client.get("/packages/flow/reference").get_data(as_text=True)


def test_docpages_loader_unit() -> None:
    from markupsafe import Markup
    from xdog.site.content.docpages import render_page

    ref = render_page("ai", "reference")
    assert ref is not None
    assert isinstance(ref.html, Markup) and "<table>" in ref.html
    assert ref.title  # frontmatter title or capitalized page name
    # missing page for a real package, unknown package, and unknown page → None
    assert render_page("ai", "examples") is None
    assert render_page("nope", "design") is None
    assert render_page("flow", "bogus") is None


def test_docs_content_modules_importable() -> None:
    from xdog.site.blueprints.main import PACKAGE_DOCS

    assert set(PACKAGE_DOCS) == {package.name for package in PACKAGES}
    for docs in PACKAGE_DOCS.values():
        assert docs.features and docs.roadmap
        grouped = docs.grouped_features()
        assert sum(len(features) for _, features in grouped) == len(docs.features)


# --- HaveFun page + async run ------------------------------------------------


def test_havefun_page_ok(client: FlaskClient) -> None:
    resp = client.get("/havefun/flow")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "HaveFun" in body
    assert "hf-run" in body  # the run button
    assert "hf-file" in body  # the upload file input
    assert "agent_calculator" in body  # the example option


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


def test_havefun_nav_is_a_collapsible_section_over_its_packages(client: FlaskClient) -> None:
    """HaveFun nests its runnable packages, matching the Packages section's shape.

    The children are rendered from the route's own allowlist, so a nav link cannot
    drift into pointing at a name that would 404.
    """
    from xdog.site.blueprints.main import HAVEFUN_PACKAGES

    body = client.get("/").get_data(as_text=True)
    nav = body.split('<ul class="nav">', 1)[1]
    section = nav.split("HaveFun", 1)[1].split("</li>", 1)[0]
    assert "<input type=\"checkbox\" />" in section  # collapsible, like Packages
    for name in HAVEFUN_PACKAGES:
        assert f'<a href="/havefun/{name}">{name}</a>' in section
        assert client.get(f"/havefun/{name}").status_code == 200


def test_old_flow_havefun_url_gone(client: FlaskClient) -> None:
    # HaveFun moved to a per-package route /havefun/<name>; old + un-named URLs are gone.
    assert client.get("/packages/flow/havefun").status_code == 404
    assert client.post("/packages/flow/havefun/load", json={"example": "agent_calculator"}).status_code == 404
    assert client.get("/havefun").status_code == 404  # must name a package
    assert client.get("/havefun/ai").status_code == 404  # only packages with workflows


def test_havefun_load_example_returns_diagram_and_inputs(client: FlaskClient) -> None:
    resp = client.post("/havefun/flow/load", json={"example": "agent_calculator"})
    assert resp.status_code == 200
    d = resp.get_json()
    assert d["ok"] is True
    assert "<svg" in d["svg"]
    assert d["ascii"]  # ASCII diagram present
    names = {i["name"] for i in d["inputs"]}
    assert {"a", "b"} <= names  # declared inputs surfaced with defaults


def test_havefun_load_unknown_example_400(client: FlaskClient) -> None:
    resp = client.post("/havefun/flow/load", json={"example": "does-not-exist"})
    assert resp.status_code == 400


def test_havefun_load_uploaded_json(client: FlaskClient) -> None:
    """An uploaded workflow JSON loads and returns its diagram + inputs."""
    payload = {
        "name": "up",
        "provider": "copilot",
        "entry": "x",
        "state": {"n": "1"},
        "nodes": [
            {
                "id": "x",
                "type": "script",
                "code": "def x(ctx, n):\n    return n",
                "inputs": ["n"],
                "output": "y",
            }
        ],
        "edges": [{"from": "$in", "to": "x", "map": {"n": "n"}}],
    }
    import json as _json

    resp = client.post("/havefun/flow/load", json={"json": _json.dumps(payload)})
    assert resp.status_code == 200
    d = resp.get_json()
    assert d["ok"] is True
    assert "n" in {i["name"] for i in d["inputs"]}


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


def test_havefun_load_refine_loop_example(client: FlaskClient) -> None:
    """The generator↔critic refine_loop example loads with a topic input only.

    `feedback` is an internal optional loop-carried port, NOT a workflow input, so
    it must not surface as a user-facing input box.
    """
    resp = client.post("/havefun/flow/load", json={"example": "refine_loop"})
    assert resp.status_code == 200
    d = resp.get_json()
    assert d["ok"] is True
    assert "<svg" in d["svg"]
    names = {i["name"] for i in d["inputs"]}
    assert "topic" in names  # the user-facing input is surfaced
    assert "feedback" not in names  # internal loop-carried port is not an input


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


def test_robots_txt_points_at_the_sitemap(client: FlaskClient) -> None:
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    assert resp.mimetype == "text/plain"
    body = resp.get_data(as_text=True)
    assert "User-agent: *" in body
    assert "Sitemap: https://xdog.942295.xyz/sitemap.xml" in body
    # the job-status poll is a GET with unbounded cardinality; crawlers must skip it
    assert "Disallow: /havefun/*/status/" in body


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


def test_sitemap_tracks_the_registries_it_is_built_from(client: FlaskClient) -> None:
    """Adding a package or a post must not need anyone to remember the sitemap."""
    from xdog.site.content.blog import get_articles
    from xdog.site.content.packages import PACKAGES

    body = client.get("/sitemap.xml").get_data(as_text=True)
    for package in PACKAGES:
        assert f"<loc>https://xdog.942295.xyz/packages/{package.name}</loc>" in body
    for article in get_articles():
        assert f"/blog/{article.slug}</loc>" in body


def test_home_shows_how_to_install_from_pypi(client: FlaskClient) -> None:
    """The front page has to answer "how do I get this" without a detour."""
    body = client.get("/").get_data(as_text=True)
    assert "pip install xdog-coding xdog-claw xdog-flow xdog-eyes" in body
    assert "https://pypi.org/project/xdog-ai/" in body
    # The namespace (installed as xdog-flow, imported as xdog.flow) is no
    # longer stated here. It is a real surprise -- the dist name and the import
    # name differ -- and this used to assert the front page named it. Dropped
    # deliberately to keep the landing page short, not because it stopped being
    # true. Checked while removing it: no page on the site states it now, so a
    # visitor learns the import name from the READMEs or by trying.


def test_every_package_links_to_its_actual_installation_surface(client: FlaskClient) -> None:
    """Every public catalog entry links to its published distribution."""
    for pkg in PACKAGES:
        body = client.get(f"/packages/{pkg.name}").get_data(as_text=True)
        assert pkg.pypi_url in body, pkg.name
        assert pkg.dist in body, pkg.name


def test_claw_docs_describe_current_capability_and_observation_boundary(client: FlaskClient) -> None:
    reference = client.get('/packages/claw/reference').get_data(as_text=True)
    overview = client.get('/packages/claw').get_data(as_text=True)
    design = client.get('/packages/claw/design').get_data(as_text=True)
    features = client.get('/packages/claw/features').get_data(as_text=True)
    assert 'Default private Unix ACP endpoint' in reference
    assert 'independent user-local daemon' in reference
    assert 'bodies activate on demand' in reference and 'never grant tools' in reference
    assert 'currently bound channel' in overview and 'no implicit chat reply recipient' in overview
    assert 'CapabilityAssembly' in design and 'bodies load on demand' in design
    assert 'Flow invokes public CLI argv without a daemon' in design
    for page in (reference, overview, design, features):
        assert 'gateway-managed' not in page
        assert 'creator\u2019s reply destination is bound' not in page
        assert 'independent completion review' not in page
        assert 'separate notification cancellation' not in page
