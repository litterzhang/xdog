"""Replay merges display sidecars with canonical input chronology, without execution."""

from xdog.agent import CustomAgentMessage
from xdog.agent.session_input import SessionInput, encode_input
from xdog.ai.types import AssistantMessage, TextContent, UserMessage
from xdog.coding.core.sessions.models import InputRecord
from xdog.coding.modes.interactive.components.chat_log import ChatLog
from xdog.coding.modes.interactive.theme import create_default_theme
from xdog.tui.utils import strip_ansi


def test_replay_shows_canonical_input_once_and_keeps_assistant_chronology():
    from xdog.coding.modes.interactive.session_events import SessionPresenter

    item = SessionInput("r1", "acp", "watcher", "Inspect src/a.py", ({"path": "src/a.py"},), {})
    log = ChatLog(create_default_theme())
    presenter = SessionPresenter(log)
    presenter.replay(
        (InputRecord(item, "branch", "g1", "succeeded"),),
        (
            UserMessage(content="Human first"),
            AssistantMessage(content=(TextContent(text="Earlier answer"),)),
            encode_input(item),
            AssistantMessage(content=(TextContent(text="External answer"),)),
        ),
    )
    output = "\n".join(strip_ansi(line) for line in log.render(100))
    assert output.count("External input") == 1 and output.count("Inspect src/a.py") == 1
    assert output.index("Earlier answer") < output.index("External input") < output.index("External answer")
    assert "Status: succeeded" in output


def test_replay_interrupted_sidecar_does_not_enter_history_or_invent_a_model_answer(coding_session):
    import asyncio

    from xdog.coding.core.sessions.store import InputStore
    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

    item = SessionInput("pending", "acp", "offline producer", "Review unexecuted change", ({"path": "late.py"},), {})
    asyncio.run(
        InputStore(coding_session).accept(
            InputRecord(item, coding_session.session_data.active_branch, "old-generation", "queued")
        )
    )
    ui = InteractiveMode(coding_session)
    try:
        ui._replay_history()
        output = "\n".join(strip_ansi(line) for line in ui._chat_log.render(100))
        assert "Review unexecuted change" in output and "Status: interrupted" in output
        assert "not model context" in output
        assert coding_session.messages == [] and coding_session.runtime is None
        assert not ui._editor._history
    finally:
        ui._request_exit()


def test_legacy_event_replay_retains_handling_instruction_and_evidence_without_host_startup(coding_session):
    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

    legacy = {
        "id": "old",
        "registration_id": "reg",
        "route_id": "route",
        "delivery_ids": ["delivery"],
        "instruction": "Legacy handling requirement",
        "events": [
            {
                "id": "event",
                "source_id": "files",
                "type": "file_changed",
                "observed_at": 1,
                "evidence": [{"path": "old.py", "before": "a", "after": "b"}],
            }
        ],
    }
    coding_session.agent.replace_messages([CustomAgentMessage(type="xdog.external_event.v1", data=legacy)])
    ui = InteractiveMode(coding_session)
    try:
        ui._replay_history()
        output = "\n".join(strip_ansi(line) for line in ui._chat_log.render(100))
        assert "Legacy handling requirement" in output and "old.py" in output
        assert "External input" in output and "not a human" in output
        assert coding_session.runtime is None
    finally:
        ui._request_exit()


def test_initial_replay_and_unpainted_completed_live_events_do_not_duplicate_answer(coding_session):
    import asyncio

    from xdog.ai.types import DoneEvent
    from xdog.ai.utils.event_stream import EventStream
    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

    answer = AssistantMessage(content=(TextContent(text="ONE-EXTERNAL-ANSWER"),))

    def stream(*args):
        async def generate():
            yield DoneEvent(message=answer)

        completed = asyncio.get_running_loop().create_future()
        completed.set_result(answer)
        return EventStream.from_async_generator(generate(), result_future=completed)

    coding_session.agent.set_stream_fn(stream)
    ui = InteractiveMode(coding_session)
    ui._session_events.start()
    controller = coding_session.runtime.controller
    try:
        controller.human_input()
        item = SessionInput("r1", "acp", "watcher", "Inspect live file", ({"path": "live.py"},), {})
        controller.submit(item, peer_id="acp").result.result(timeout=3)
        ui._replay_history()
        ui._poll()
        output = "\n".join(strip_ansi(line) for line in ui._chat_log.render(100))
        assert output.count("ONE-EXTERNAL-ANSWER") == 1
        assert output.count("External input") == 1
        assert "Status: succeeded" in output and "live.py" in output
        assert not ui._editor._history
    finally:
        ui._request_exit()
        ui._session_events.close()


def test_command_view_replays_its_settled_snapshot_not_a_successor_history(coding_session, monkeypatch):
    from xdog.coding.core.slash_commands import CommandResult
    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

    ui = InteractiveMode(coding_session)
    ui._session_events.start()

    async def command(*args):
        coding_session.agent.replace_messages([UserMessage(content="Captured navigation history")])
        return CommandResult("cleared")

    monkeypatch.setattr("xdog.coding.modes.interactive.interactive_mode.execute_command", command)
    try:
        ui._handle_slash_command("clear", "")
        ui._command_thread.join(3)
        assert not ui._command_thread.is_alive()
        coding_session.agent.append_message(AssistantMessage(content=(TextContent(text="SUCCESSOR-ALREADY-FINISHED"),)))
        ui._poll()
        output = "\n".join(strip_ansi(line) for line in ui._chat_log.render(100))
        assert "Captured navigation history" in output
        assert "SUCCESSOR-ALREADY-FINISHED" not in output
    finally:
        ui._request_exit()
        ui._session_events.close()


def test_repeated_replay_is_idempotent_and_does_not_erase_unrelated_notices():
    from xdog.coding.modes.interactive.session_events import SessionPresenter

    item = SessionInput("r1", "acp", "watcher", "Inspect once", (), {})
    record = InputRecord(item, "branch", "g1", "succeeded")
    log = ChatLog(create_default_theme())
    log.add_system("startup banner")
    presenter = SessionPresenter(log)
    messages = (encode_input(item), AssistantMessage(content=(TextContent(text="ONE-REPLAYED-ANSWER"),)))
    presenter.replay((record,), messages)
    presenter.replay((record,), messages)
    output = "\n".join(strip_ansi(line) for line in log.render(100))
    assert output.count("External input") == 1
    assert output.count("ONE-REPLAYED-ANSWER") == 1
    assert output.count("startup banner") == 1


def test_pending_record_from_another_branch_is_not_presented_as_current_work(coding_session):
    import asyncio

    from xdog.coding.core.sessions.store import InputStore
    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

    item = SessionInput("other-branch", "acp", "watcher", "Do not retarget this request", (), {})
    asyncio.run(InputStore(coding_session).accept(InputRecord(item, "different-branch", "old", "queued")))
    ui = InteractiveMode(coding_session)
    try:
        ui._replay_history()
        output = "\n".join(strip_ansi(line) for line in ui._chat_log.render(100))
        assert "Do not retarget" not in output
        assert coding_session.runtime is None
    finally:
        ui._request_exit()
