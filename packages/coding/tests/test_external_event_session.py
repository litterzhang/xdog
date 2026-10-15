import json

from xdog.agent import AgentConfig
from xdog.agent.agent import Agent
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager


def session(tmp_path):
    manager = SessionManager(sessions_dir=tmp_path / "sessions")
    data = manager.create_session(model="test", working_dir=tmp_path)
    agent = Agent(lambda *_: EventStream.empty(AssistantMessage(content=())),
                  config=AgentConfig(model="test"), tool_ctx={"fs_workspace": str(tmp_path)})
    return AgentSession(agent=agent, session_data=data, session_manager=manager, settings=SettingsManager(),
                        tool_registry=None, bash=None, working_dir=tmp_path)


def test_generic_binding_preserves_context_and_revokes_on_navigation(tmp_path):
    value = session(tmp_path)
    try:
        binding = value.enable_external_events()
        context = value.agent.tool_context
        assert context["fs_workspace"] == str(tmp_path)
        descriptor = binding.host.descriptor
        assert context["subprocess_env"]["XDOG_EVENT_CONTEXT"] == str(descriptor)
        old = json.loads(descriptor.read_text())
        value.new_session()
        assert not descriptor.exists()
        descriptor = binding.host.descriptor
        new = json.loads(descriptor.read_text())
        assert old["owner"]["generation"] != new["owner"]["generation"]
        assert old["token"] != new["token"]
    finally:
        value.dispose()
    assert not descriptor.exists()


def test_binding_is_opt_in_and_does_not_require_observer_package(tmp_path):
    value = session(tmp_path)
    try:
        assert value.external_events is None
        assert value.agent.tool_context["subprocess_env"]["XDOG_EVENT_CONTEXT"] == ""
    finally:
        value.dispose()


def test_unbound_session_masks_inherited_parent_capability(tmp_path, monkeypatch):
    monkeypatch.setenv("XDOG_EVENT_CONTEXT", "/tmp/parent-session-capability")
    value = session(tmp_path)
    try:
        assert value.agent.tool_context["subprocess_env"]["XDOG_EVENT_CONTEXT"] == ""
    finally:
        value.dispose()


async def test_external_input_uses_normal_sdk_and_persists_custom_provenance(tmp_path):
    from xdog.agent.core import CustomAgentMessage

    value = session(tmp_path)
    observed = []

    def stream(model, context, options):
        observed.extend(context.messages)
        return EventStream.empty(AssistantMessage(content=()))

    value.agent.set_stream_fn(stream)
    record = {"id": "job", "registration_id": "observer", "route_id": "route",
              "instruction": "Review tests", "delivery_ids": ["delivery"],
              "events": [{"id": "event", "source_id": "source", "detector_id": "detector",
                          "type": "file.modified", "observed_at": 1,
                          "evidence": [{"path": "src/a.py"}]}]}
    try:
        await value.send_message(json.dumps(record), input_origin="external_event")
        assert any(isinstance(row, CustomAgentMessage) and row.type == "xdog.external_event.v1"
                   for row in value.messages)
        saved = value.session_manager.load_session(value.session_id)
        assert any(isinstance(row, CustomAgentMessage) for row in saved.messages)
        text = " ".join(part.text for row in observed for part in row.content if hasattr(part, "text"))
        assert "not a human message" in text and "Review tests" in text
    finally:
        value.dispose()


async def test_resume_policy_is_fail_closed_unless_explicitly_allowed(tmp_path):
    value = session(tmp_path)
    binding = value.enable_external_events()
    try:
        value.permissions.set_mode("ask")
        assert await binding._approve("observer") is False
        value.permissions.set_mode("deny")
        assert await binding._approve("observer") is False
        value.permissions.set_mode("allow-all")
        assert await binding._approve("observer") is True
    finally:
        value.dispose()
