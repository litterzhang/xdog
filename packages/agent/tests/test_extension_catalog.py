from __future__ import annotations

import copy
from pathlib import Path

import pytest
from xdog.agent.extensions.catalog import page, validate_page, validate_request
from xdog.agent.extensions.dialogs import validate_answer, validate_dialog

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


def test_invalid_catalog_request_refused_without_guessing() -> None:

    def _check_case(kwargs):
        with pytest.raises(ValueError):
            validate_request(request(**kwargs))

    for kwargs in [
        {"scope": "unknown"},
        {"offset": True},
        {"offset": -1},
        {"offset": 1000001},
        {"query": True},
        {"query": "x" * 1001},
        {"scope": "tree", "session_id": "../outside"},
        {"session_id": "foreign"},
    ]:
        _check_case(kwargs)


def test_invalid_or_mismatched_catalog_response_refused() -> None:

    def _check_case(mutate):
        original = page(rows(), request(), "current")
        with pytest.raises(ValueError):
            validate_page(request(), {**original, **mutate})

    for mutate in [
        {"total": True},
        {"offset": False},
        {"has_more": 1},
        {"next_offset": True},
        {"query": "different"},
        {"session_id": "foreign"},
        {"entries": []},
        {"scope": "tree"},
    ]:
        _check_case(mutate)


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
