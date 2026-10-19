"""Captured host routing is immutable and independent from request/evidence data."""

from __future__ import annotations

import json

import pytest
from xdog.ai.types import ImageContent
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.inputs.store import InputStore
from xdog.claw.core.observers.journal import ObserverJournal
from xdog.claw.core.types import UserInput


def user():
    return UserInput(
        group_id="main",
        channel="weixin",
        sender="alice",
        content="PRIVATE original request",
        metadata={"account_id": "account", "weixin_user_id": "alice", "context_token": "original-context"},
        images=(ImageContent(data="PRIVATE image bytes never enter routing", mime_type="image/png"),),
    )


def test_reference_detaches_routing_metadata_and_never_copies_prompt_images_or_authority(tmp_path):
    from xdog.claw.core.outputs.targets import ReplyReference, decode_reply, encode_reply

    original = user()
    reference = ReplyReference(InputScope("main", tmp_path), "live-channel", original)
    original.metadata["context_token"] = "mutated"
    data = encode_reply(reference)
    assert reference.original.metadata["context_token"] == "original-context"
    assert reference.original.content == "" and reference.original.images == ()
    assert "PRIVATE" not in data and "human_authorized" not in data
    projected = reference.original
    projected.metadata.clear()
    assert decode_reply(data) == reference and reference.original.metadata["account_id"] == "account"


@pytest.mark.parametrize(
    "field,value",
    [
        ("instruction", "cannot persist input"),
        ("images", []),
        ("human_authorized", True),
        ("format", "unknown-reply.v9"),
    ],
)
def test_codec_rejects_unregistered_fields_and_versions(tmp_path, field, value):
    from xdog.claw.core.outputs.targets import ReplyReference, decode_reply, encode_reply

    data = json.loads(encode_reply(ReplyReference(InputScope("main", tmp_path), "endpoint", user())))
    data[field] = value
    with pytest.raises(ValueError):
        decode_reply(json.dumps(data))


async def test_observer_roundtrip_rejects_recipient_replacement_and_wrong_scope(tmp_path):
    from xdog.claw.core.outputs.targets import ReplyReference

    core = await InputStore.open(tmp_path / "core" / "inputs.sqlite3")
    journal = await ObserverJournal.open(tmp_path / "observer" / "journal.sqlite3", core)
    await core.bind_scope(InputScope("main", tmp_path))
    reference = ReplyReference(InputScope("main", tmp_path), "original-endpoint", user())
    try:
        saved = await journal.create("main", tmp_path, "watch", {"version": 2}, reply=reference)
        assert (await journal.get("main", tmp_path, saved.id)).reply == reference
        assert (await journal.create("main", tmp_path, "watch", {"version": 2}, reply=reference)).id == saved.id
        other = ReplyReference(InputScope("main", tmp_path), "different-endpoint", user())
        with pytest.raises(ValueError, match="different content"):
            await journal.create("main", tmp_path, "watch", {"version": 2}, reply=other)
        with pytest.raises(ValueError, match="scope"):
            await journal.create("different", tmp_path, "unsafe", {"version": 2}, reply=reference)
    finally:
        await journal.close()
    journal = await ObserverJournal.open(journal.path, core)
    try:
        assert (await journal.get("main", tmp_path, saved.id)).reply == reference
    finally:
        await journal.close()
        await core.close()


async def test_old_observer_format_is_rejected_without_touching_original_bytes(tmp_path):
    import sqlite3

    core = await InputStore.open(tmp_path / "core" / "inputs.sqlite3")
    journal = await ObserverJournal.open(tmp_path / "observer" / "journal.sqlite3", core)
    await journal.close()
    with sqlite3.connect(journal.path) as db:
        db.execute("UPDATE metadata SET format='xdog.claw.observers.v1'")
    before = journal.path.read_bytes()
    try:
        with pytest.raises(ValueError, match="Unsupported"):
            await ObserverJournal.open(journal.path, core)
        assert journal.path.read_bytes() == before
    finally:
        await core.close()
