"""Authoritative current context metadata must never fall back to a new owner."""
from __future__ import annotations

import json

import pytest
from xdog.claw.core.persistence.transcript_store import TranscriptStore


@pytest.mark.parametrize("damage", [
    "json", "list", "null", "row", "missing", "session", "group", "alias", "count", "time", "duplicate",
])
def test_invalid_active_index_is_rejected_before_any_original_file_is_replaced(tmp_path, damage):
    root = tmp_path / "sessions"
    store = TranscriptStore(root, owner_group="main")
    store.create_session("main")
    path = root / "sessions.json"
    value = json.loads(path.read_text())
    row = value["main"]
    if damage == "json":
        raw = b"{invalid active index\n"
    elif damage == "list":
        raw = b"[]"
    elif damage == "null":
        raw = b"null"
    elif damage == "duplicate":
        item = json.dumps(row)
        raw = ('{"main":' + item + ',"main":' + item + '}').encode()
    else:
        if damage == "row":
            value["main"] = []
        elif damage == "missing":
            del row["session_id"]
        elif damage == "session":
            row["session_id"] = "../another-owner"
        elif damage == "group":
            row["group_id"] = "different-group"
        elif damage == "alias":
            value["other"] = {**row, "group_id": "other"}
        elif damage == "count":
            row["turn_count"] = True
        elif damage == "time":
            row["last_active"] = "not a timestamp"
        raw = json.dumps(value).encode()
    path.write_bytes(raw)
    before = {item.name: item.read_bytes() for item in root.iterdir() if item.is_file()}
    with pytest.raises(ValueError, match="index"):
        TranscriptStore(root, owner_group="main")
    assert {item.name: item.read_bytes() for item in root.iterdir() if item.is_file()} == before


@pytest.mark.parametrize("dangling", [False, True])
def test_active_index_symlink_is_not_followed_or_silently_treated_as_absent(tmp_path, dangling):
    root = tmp_path / "sessions"
    root.mkdir()
    target = tmp_path / "outside-index.json"
    if not dangling:
        target.write_text("{}")
    path = root / "sessions.json"
    path.symlink_to(target)
    with pytest.raises(ValueError, match="index"):
        TranscriptStore(root, owner_group="main")
    assert path.is_symlink() and target.exists() is not dangling
    if not dangling:
        assert target.read_text() == "{}"


def test_genuinely_missing_or_valid_native_index_retains_its_actual_binding(tmp_path):
    root = tmp_path / "sessions"
    store = TranscriptStore(root, owner_group="main")
    assert store.get_active_session("main") is None
    created = store.create_session("main")
    original = (root / "sessions.json").read_bytes()
    restored = TranscriptStore(root, owner_group="main")
    assert restored.get_active_session("main") == created
    assert (root / "sessions.json").read_bytes() == original
