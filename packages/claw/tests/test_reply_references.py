"""Captured host routing is immutable and independent from request/evidence data."""

from __future__ import annotations

import json

import pytest
from xdog.ai.types import ImageContent
from xdog.claw.core.inputs.models import InputScope
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






@pytest.mark.parametrize("reject_raises", [False, True])
async def test_unadmitted_input_cannot_leave_its_reply_capability_on_an_idle_agent(tmp_path, reject_raises):
    from xdog.claw.config import ClawConfig
    from xdog.claw.core.outputs.targets import ReplyReference
    from xdog.claw.core.runtime.orchestrator import Orchestrator
    from xdog.claw.core.types import Group

    async def no_model(*args):
        raise AssertionError("Unadmitted input must not run a model")

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/dummy", stream_fn=no_model)
    app.register_group(Group(id="main", workspace=str(tmp_path), enabled_tools=()))
    reference = ReplyReference(InputScope("main", tmp_path), "original-route", user())

    async def reject(session):
        if reject_raises:
            raise ValueError("admission failed before model")
        return False

    try:
        if reject_raises:
            with pytest.raises(ValueError):
                await app._execute_owned_input(
                    UserInput(group_id="main", content="rejected"), admit=reject, reply_reference=reference
                )
        else:
            assert (
                await app._execute_owned_input(
                    UserInput(group_id="main", content="rejected"), admit=reject, reply_reference=reference
                )
                is None
            )
        assert app._runtimes["main"]._active_session.agent.tool_context.get("_reply_reference") is None
    finally:
        await app.stop()
