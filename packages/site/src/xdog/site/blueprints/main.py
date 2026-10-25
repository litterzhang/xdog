"""Main blueprint: home, packages, package sub-pages, and FAQ."""

from __future__ import annotations

import functools
import json as jsonlib
from pathlib import Path
from typing import Any

from flask import Blueprint, abort, jsonify, render_template, request
from xdog.site.content.docpages import render_page
from xdog.site.content.docs import PackageDocs
from xdog.site.content.faq import FAQS
from xdog.site.content.packages import LAYERS, PACKAGES, PACKAGES_BY_NAME
from xdog.site.content.product import OPTIONAL_INSTALL, QUICKSTART_COMMANDS
from xdog.site.content.registry import PACKAGE_DOCS as PACKAGE_DOCS

bp = Blueprint("main", __name__)

_MAX_UPLOAD_BYTES = 256 * 1024  # generous cap for a workflow JSON


@bp.route("/")
def home() -> str:
    layers = [(title, [PACKAGES_BY_NAME[name] for name in names]) for title, names in LAYERS]
    return render_template("index.html", packages=PACKAGES, layers=layers,
                           quickstart_commands=QUICKSTART_COMMANDS, optional_install=OPTIONAL_INSTALL)


@bp.route("/packages/all")
def packages() -> str:
    return render_template("packages/index.html", packages=PACKAGES)


# -- HaveFun: load a workflow, view diagrams, fill inputs, run async ----------

# Curated built-in workflows offered in the HaveFun example picker (also the
# allowlist for the ``example`` load path).  Kept small and cheap to run.
# ``essay_writer`` reaches ``essay_compose`` through a path-referenced subflow,
# which resolves because the example load path hands the examples directory to
# the loader as its base_dir; the child is listed too so it can be viewed and run
# on its own.
_HAVEFUN_STEMS: tuple[str, ...] = (
    "agent_calculator",
    "refine_loop",
    "essay_writer",
    "essay_compose",
)

# Packages that expose a runnable HaveFun page. Today only flow ships workflows.
# The nav renders its HaveFun children from this same tuple (see app.template_global),
# so a link can never point at a name the route would 404.
HAVEFUN_PACKAGES: tuple[str, ...] = ("flow",)


@bp.route("/havefun/<name>")
def havefun(name: str) -> str:
    if name not in HAVEFUN_PACKAGES:
        abort(404)
    return render_template("havefun.html", name=name, example_stems=list(_HAVEFUN_STEMS))


def _load_workflow_from_request(data: dict[str, Any]) -> tuple[Any, Path | None, str | None]:
    """Resolve the request body to a (workflow, base_dir, error) tuple.

    A built-in ``example`` stem or an uploaded ``json`` string.  Returns
    ``(None, None, message)`` on any problem.
    """
    from xdog.flow.loader import parse_workflow, validate_workflow
    from xdog.flow.models import WorkflowDef

    ex_dir = _examples_dir()
    stem = data.get("example")
    if stem:
        if stem not in _HAVEFUN_STEMS or ex_dir is None:
            return None, None, f"Unknown example {stem!r}."
        from xdog.flow.loader import load_workflow

        try:
            return load_workflow(ex_dir / f"{stem}.json"), ex_dir, None
        except Exception as exc:  # noqa: BLE001
            return None, None, f"Failed to load example: {exc}"

    raw = data.get("json")
    if not isinstance(raw, str) or not raw.strip():
        return None, None, "Provide an example or a workflow JSON."
    if len(raw.encode("utf-8")) > _MAX_UPLOAD_BYTES:
        return None, None, "Workflow JSON is too large."
    try:
        parsed = jsonlib.loads(raw)
        wf: WorkflowDef = parse_workflow(parsed)
        validate_workflow(wf)
    except Exception as exc:  # noqa: BLE001
        return None, None, f"Invalid workflow: {exc}"
    return wf, None, None


@bp.route("/havefun/<name>/load", methods=["POST"])
def havefun_load(name: str) -> Any:
    if name not in HAVEFUN_PACKAGES:
        abort(404)
    from xdog.flow.graph import to_ascii_diagram, to_svg

    data = request.get_json(silent=True) or {}
    wf, _base, err = _load_workflow_from_request(data)
    if err is not None:
        return jsonify({"ok": False, "error": err}), 400
    try:
        svg = to_svg(wf)
    except Exception:  # noqa: BLE001
        svg = ""
    ascii_diagram = to_ascii_diagram(wf)
    inputs = [{"name": k, "default": v} for k, v in wf.initial_state]
    return jsonify({"ok": True, "name": wf.name, "svg": svg, "ascii": ascii_diagram, "inputs": inputs})


@bp.route("/havefun/<name>/run", methods=["POST"])
def havefun_run(name: str) -> Any:
    if name not in HAVEFUN_PACKAGES:
        abort(404)
    from xdog.site.jobs import runner

    data = request.get_json(silent=True) or {}
    wf, base_dir, err = _load_workflow_from_request(data)
    if err is not None:
        return jsonify({"ok": False, "error": err}), 400

    raw_inputs = data.get("inputs") or {}
    inputs = {str(k): str(v) for k, v in raw_inputs.items()} if isinstance(raw_inputs, dict) else {}

    try:
        import xdog.ai as ai
        from xdog.agent.helpers import stream_fn_from_provider, web_search_fn_from_provider

        provider = ai.provider(wf.provider or "copilot")
        base_stream = stream_fn_from_provider(provider)

        def _sf(_model: str) -> Any:
            return base_stream

        def _wsf(model: str) -> Any:
            return web_search_fn_from_provider(provider, model)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"Runner unavailable: {exc}"}), 503

    job_id = runner.start(wf, inputs, stream_fn_factory=_sf, base_dir=base_dir, web_search_fn_factory=_wsf)
    if job_id is None:
        return jsonify({"ok": False, "error": "A run is already in progress. Try again shortly."}), 429
    return jsonify({"ok": True, "job_id": job_id})


@bp.route("/havefun/<name>/status/<job_id>")
def havefun_status(name: str, job_id: str) -> Any:
    if name not in HAVEFUN_PACKAGES:
        abort(404)
    from xdog.site.jobs import runner

    job = runner.get(job_id)
    if job is None:
        return jsonify({"ok": False, "error": "Unknown job."}), 404
    payload: dict[str, Any] = {"ok": True, "state": job.state, "elapsed": job.elapsed, "log": job.log}
    if job.state == "done":
        payload["result"] = job.result
    elif job.state == "error":
        payload["error"] = job.error
    return jsonify(payload)


# -- per-package sub-pages ----------------------------------------------------
# Static pages (Design / Reference / Examples) are markdown rendered by one view;
# dynamic pages (Features / Roadmap) come from PACKAGE_DOCS. Both are registered
# before the generic <name> route so /packages/ai/design resolves here.


def _docs_or_404(name: str) -> tuple[Any, PackageDocs]:
    pkg = PACKAGES_BY_NAME.get(name)
    docs = PACKAGE_DOCS.get(name)
    if pkg is None or docs is None:
        abort(404)
    return pkg, docs


@bp.route("/packages/<name>/design", defaults={"page": "design"})
@bp.route("/packages/<name>/reference", defaults={"page": "reference"})
@bp.route("/packages/<name>/examples", defaults={"page": "examples"})
def package_static(name: str, page: str) -> str:
    pkg = PACKAGES_BY_NAME.get(name)
    rendered = render_page(name, page)
    if pkg is None or rendered is None:
        abort(404)
    return render_template(
        "packages/docs/static.html",
        pkg=pkg,
        page=page,
        page_label=rendered.title,
        content=rendered.html,
        has_docs=name in PACKAGE_DOCS,
    )


@bp.route("/packages/<name>/features")
def package_features(name: str) -> str:
    pkg, docs = _docs_or_404(name)
    return render_template("packages/docs/features.html", pkg=pkg, docs=docs, groups=docs.grouped_features())


@bp.route("/packages/<name>/roadmap")
def package_roadmap(name: str) -> str:
    pkg, docs = _docs_or_404(name)
    return render_template("packages/docs/roadmap.html", pkg=pkg, docs=docs)


@bp.route("/packages/<name>")
@bp.route("/packages/<name>/overview")
def package_detail(name: str) -> str:
    pkg = PACKAGES_BY_NAME.get(name)
    rendered = render_page(name, "overview")
    if pkg is None or rendered is None:
        abort(404)
    # Rendered with the same static-page template as design/reference so the
    # overview is one markdown block with an "Overview" breadcrumb.
    return render_template(
        "packages/docs/static.html",
        pkg=pkg,
        page="overview",
        page_label=rendered.title,
        content=rendered.html,
        has_docs=name in PACKAGE_DOCS,
    )


@bp.route("/faq")
def faq() -> str:
    return render_template("faq.html", faqs=FAQS)


# -- flow example rendering ---------------------------------------------------


@functools.lru_cache(maxsize=1)
def _examples_dir() -> Path | None:
    """Locate the installed flow package's examples directory, or None."""
    try:
        import xdog.flow as flow

        assert flow.__file__ is not None
        # Walk up rather than index a fixed depth: `src/flow` and `src/xdog/flow`
        # put the package a different number of levels below the project root,
        # and a hardcoded parents[n] silently resolves to the wrong directory
        # instead of failing.
        for parent in Path(flow.__file__).resolve().parents:
            candidate = parent / "examples"
            if candidate.is_dir():
                return candidate
        return None
    except Exception:
        return None
