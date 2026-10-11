from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.catalog import page, validate_page, validate_request
from xdog.agent.extensions.dialogs import validate_answer, validate_dialog
from xdog.agent.extensions.runtime import ExtensionRuntime

FIXTURE = Path(__file__).parent / "fixtures/extensions"


def request(**kwargs):
    return {"scope": "sessions", "query": "", "offset": 0, "session_id": "", "branch_id": "", **kwargs}


def rows(count=123):
    return [
        {
            "id": f"session-{index}",
            "title": f"Catalog item {index}",
            "detail": "read-only metadata",
            "kind": "session",
            "session_id": f"session-{index}",
            "active": index == 0,
        }
        for index in range(count)
    ]


def test_pagination_has_bounded_selected_rows_offsets_continuation_unicode_search_and_no_mutation():
    values = rows()
    first = page(iter(values), request(), "current")
    assert len(first["entries"]) == 50 and first["total"] == 123 and first["next_offset"] == 50
    second = page(iter(values), request(offset=50), "current")
    last = page(iter(values), request(offset=100), "current")
    assert len(second["entries"]) == 50 and len(last["entries"]) == 23 and not last["has_more"]
    assert len({row["id"] for batch in (first, second, last) for row in batch["entries"]}) == 123
    empty = page(iter(values), request(offset=1000), "current")
    assert empty["offset"] == 123 and not empty["entries"] and empty["next_offset"] is None
    values[0]["title"] = "中文 Match"
    assert page(values, request(query="中文 match"), "current")["total"] == 1
    first["entries"][0]["title"] = "changed"
    assert values[0]["title"] == "中文 Match"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"scope": "unknown"},
        {"offset": True},
        {"offset": -1},
        {"offset": 1000001},
        {"query": True},
        {"query": "x" * 1001},
        {"scope": "tree", "session_id": "../outside"},
        {"session_id": "foreign"},
    ],
)
def test_invalid_catalog_request_refused_without_guessing(kwargs):
    with pytest.raises(ValueError):
        validate_request(request(**kwargs))


@pytest.mark.parametrize(
    "mutate",
    [
        {"total": True},
        {"offset": False},
        {"has_more": 1},
        {"next_offset": True},
        {"query": "different"},
        {"session_id": "foreign"},
        {"entries": []},
        {"scope": "tree"},
    ],
)
def test_invalid_or_mismatched_catalog_response_refused(mutate):
    original = page(rows(), request(), "current")
    with pytest.raises(ValueError):
        validate_page(request(), {**original, **mutate})


def test_tree_target_branch_checkpoint_metadata_and_broker_codec():
    values = [
        {
            "id": "b:branch",
            "kind": "branch",
            "session_id": "target",
            "branch_id": "branch",
            "title": "Saved branch",
            "detail": "retained context",
        },
        {
            "id": "p:0",
            "kind": "checkpoint",
            "session_id": "target",
            "branch_id": "",
            "index": 0,
            "title": "1. user: preview",
            "detail": "safe checkpoint",
        },
    ]
    params = request(scope="tree", session_id="target")
    result = page(values, params, "current")
    assert result["session_id"] == "target" and result["entries"][1]["index"] == 0
    dialog = validate_dialog({"id": "catalog", "kind": "session", "title": "Catalog", "operation": "catalog", **params})
    assert validate_answer(dialog, result) == result
    bad = copy.deepcopy(result)
    bad["entries"][0]["session_id"] = "another-owner"
    with pytest.raises(ValueError):
        validate_page(params, bad)


async def test_actual_worker_catalog_requests_original_command_only_and_headless_refusal(tmp_path):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    runtime = ExtensionRuntime(["navigator"], root, tmp_path)
    seen = []

    async def catalog(extension, value):
        seen.append(value)
        return page(rows(), value, "current")

    async def dialog(extension, value):
        pytest.fail("Catalog reads must not become terminal dialogs")

    runtime.catalog_handler = catalog
    runtime.dialog_handler = dialog
    try:
        assert (
            await runtime.command("ext-navigator-catalog", '{"scope":"sessions","offset":50}') == "CATALOG-PAGE-50-123"
        )
        assert seen[0]["operation"] == "catalog" and seen[0]["offset"] == 50
        assert (await runtime.tools()[0].execute("illegal", {})).content[0].text == "TOOL-NAVIGATION-None-False"
        assert len(seen) == 1
        runtime.dialog_handler = None
        assert await runtime.command("ext-navigator-catalog", "") == "CATALOG-UNAVAILABLE"
    finally:
        await runtime.aclose()


def test_catalog_identity_order_revision_ignores_recency_but_refuses_membership_change():
    values = rows()
    first = page(values, request(), "current")
    second = page(list(reversed(values)), request(offset=50, revision=first["revision"]), "current")
    assert not {row["id"] for row in first["entries"]} & {row["id"] for row in second["entries"]}
    assert first["revision"] == second["revision"]
    values.append({"id": "new-session", "title": "Added", "detail": "", "kind": "session", "session_id": "new-session"})
    with pytest.raises(ValueError, match="membership changed"):
        page(values, request(offset=50, revision=first["revision"]), "current")
    with pytest.raises(ValueError):
        validate_request(request(revision="guessed"))


async def test_changed_membership_host_refusal_is_unavailable_without_quarantining_command(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root)
    runtime = ExtensionRuntime(['navigator'], root, tmp_path)
    async def refused(extension, request):
        raise ValueError('Catalog membership changed; restart')
    async def unused(*args):
        pytest.fail('Read refusal must not become a terminal question')
    runtime.catalog_handler = refused
    runtime.dialog_handler = unused
    try:
        assert await runtime.command('ext-navigator-catalog', '') == 'CATALOG-UNAVAILABLE'
        assert runtime.snapshot()['extensions'][0]['state'] == 'active'
    finally:
        await runtime.aclose()
