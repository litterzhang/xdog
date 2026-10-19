"""A staged native input is neither an editable composer nor a new execution."""

from __future__ import annotations

import queue

from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.tui.attachments import Attachment

NAMESPACE = "1" * 32
IMAGE = Attachment("image-one", "original.png", "image/png", b"captured immutable image bytes", 1, 1)


def drain(app):
    requests = []
    while True:
        try:
            requests.append(app._send_queue.get_nowait())
        except queue.Empty:
            return requests


def pong(namespace=NAMESPACE):
    return {
        "type": "pong",
        "group_id": "main",
        "session_id": "session-one",
        "history_count": 0,
        "input_receipts": 1,
        "inbox_namespace": namespace,
    }


def active():
    app = ChatApp("/tmp/unused-native-receipt-test.sock")
    app._handle_response(pong())
    app._start_message("original", (IMAGE,))
    original = drain(app)[0]
    return app, original


def disconnect(app):
    app._handle_response({"type": "internal_error", "message": "connection lost"})


def reconnect(app, namespace=NAMESPACE):
    app._handle_response({"type": "internal_reconnected"})
    app._handle_response(pong(namespace))
    return drain(app)


def result(query, message, **changes):
    return {
        "type": "input_status_result",
        "request_id": query["request_id"],
        "success": True,
        "namespace": NAMESPACE,
        "message": message,
        **changes,
    }


def test_lost_ack_retains_original_input_separately_from_queue_and_current_edit():
    app, original = active()
    app._pending_messages.append("queued independently")
    app._pending_ids.sync(1)
    app._editor.set_text("current unsent edit")
    disconnect(app)
    assert list(app._pending_messages) == ["queued independently"]
    assert app._editor.get_text() == "current unsent edit"
    assert app._native_submission.run_id == original["run_id"]
    assert app._native_submission.images == (IMAGE,)
    assert app._retained_image_bytes() >= len(IMAGE.data)
    app._dispatch_next_pending()
    assert drain(app) == []


def test_reconnect_queries_same_id_before_any_resubmission_and_only_absent_can_reoffer():
    app, original = active()
    disconnect(app)
    requests = reconnect(app)
    (query,) = [row for row in requests if row["type"] == "input_status"]
    assert query["message_id"] == "native:" + original["run_id"] and query["wait"] is True
    assert not any(row["type"] == "message" for row in requests)
    app._handle_response(result(query, None))
    (retry,) = [row for row in drain(app) if row["type"] == "message"]
    assert retry["run_id"] == original["run_id"] and retry["content"] == "original"
    assert retry["_images"] == (IMAGE,) and retry["group_id"] == original["group_id"]


def test_already_accepted_input_is_not_restored_as_a_new_draft_or_resubmitted():
    app, original = active()
    app._handle_response(
        {
            "type": "run_ack",
            "run_id": original["run_id"],
            "session_id": "session-one",
            "message_id": "native:" + original["run_id"],
            "inbox_namespace": NAMESPACE,
        }
    )
    app._editor.set_text("new edit")
    disconnect(app)
    (query,) = [row for row in reconnect(app) if row["type"] == "input_status"]
    app._handle_response(
        result(
            query,
            {
                "id": query["message_id"],
                "status": "succeeded",
                "attempt_id": "attempt-one",
                "session_id": "session-one",
                "response": "already done",
                "error": "",
            },
        )
    )
    assert not any(row["type"] == "message" for row in drain(app))
    assert app._editor.get_text() == "new edit" and app._native_submission is None
    text = "\n".join(app._chat_log.render(100))
    assert "already done" in text


def test_a_missing_previously_acknowledged_input_is_a_contradiction_not_permission_to_replay():
    app, original = active()
    app._handle_response(
        {
            "type": "run_ack",
            "run_id": original["run_id"],
            "session_id": "session-one",
            "message_id": "native:" + original["run_id"],
            "inbox_namespace": NAMESPACE,
        }
    )
    disconnect(app)
    (query,) = [row for row in reconnect(app) if row["type"] == "input_status"]
    app._handle_response(result(query, None))
    assert app._native_submission is not None
    assert not any(row["type"] == "message" for row in drain(app))


def test_namespace_change_cannot_transfer_or_resend_an_uncertain_submission():
    app, original = active()
    disconnect(app)
    assert not any(row["type"] in {"message", "input_status"} for row in reconnect(app, "2" * 32))
    assert app._native_submission.run_id == original["run_id"]


def test_stale_read_or_wrong_input_cannot_retire_an_uncertain_submission():
    app, original = active()
    disconnect(app)
    (query,) = [row for row in reconnect(app) if row["type"] == "input_status"]
    terminal = {
        "id": "native:some-other-run",
        "status": "succeeded",
        "attempt_id": "other-attempt",
        "session_id": "session-one",
        "response": "other response",
        "error": "",
    }
    app._handle_response(result(query, terminal, request_id="stale-request"))
    app._handle_response(result(query, terminal))
    assert app._native_submission.run_id == original["run_id"]
    assert drain(app) == []


def acknowledge(app, original):
    app._handle_response(
        {
            "type": "run_ack",
            "run_id": original["run_id"],
            "session_id": "session-one",
            "message_id": "native:" + original["run_id"],
            "inbox_namespace": NAMESPACE,
        }
    )


def test_enter_during_reconciliation_queues_independently_without_replacing_the_original():
    app, original = active()
    disconnect(app)
    reconnect(app)
    app._handle_submit("new intent while receipt pending")
    assert app._native_submission.run_id == original["run_id"]
    assert list(app._pending_messages) == ["new intent while receipt pending"]
    assert not any(row["type"] == "message" for row in drain(app))


def test_a_late_ack_after_disconnect_is_knowledge_not_a_live_execution_binding():
    app, original = active()
    disconnect(app)
    acknowledge(app, original)
    queries = [row for row in reconnect(app) if row["type"] == "input_status"]
    assert len(queries) == 1 and queries[0]["message_id"] == "native:" + original["run_id"]
    assert app._native_submission.acknowledged is True


def test_connection_generation_change_detaches_old_presentation_but_retains_identity():
    app, original = active()
    app._handle_response({"type": "_connection_opened", "generation": 1})
    app._handle_response(pong())
    requests = drain(app)
    (query,) = [row for row in requests if row["type"] == "input_status"]
    assert query["message_id"] == "native:" + original["run_id"]
    assert app._active_run_id is None and app._native_submission.images == (IMAGE,)


def test_terminal_failure_does_not_replay_accepted_text_and_consumes_only_the_next_queue_entry():
    app, original = active()
    acknowledge(app, original)
    app._pending_messages.append("next independent message")
    app._pending_ids.sync(1)
    app._editor.set_text("current draft")
    app._handle_response({"type": "error", "run_id": original["run_id"], "message": "model failed"})
    (sent,) = [row for row in drain(app) if row["type"] == "message"]
    assert sent["content"] == "next independent message" and sent["run_id"] != original["run_id"]
    assert app._editor.get_text() == "current draft"


def test_explicit_abort_does_not_merge_or_replay_the_cancelled_input_or_drop_other_queued_inputs():
    app, original = active()
    acknowledge(app, original)
    app._pending_messages.append("next independent message")
    app._pending_ids.sync(1)
    app._editor.set_text("current draft")
    app._handle_escape()
    (abort,) = drain(app)
    assert abort["type"] == "abort" and abort["run_id"] == original["run_id"]
    assert app._editor.get_text() == "current draft"
    app._handle_response({"type": "aborted", "run_id": original["run_id"]})
    (sent,) = [row for row in drain(app) if row["type"] == "message"]
    assert sent["content"] == "next independent message" and sent["run_id"] != original["run_id"]


def test_an_online_queued_or_running_receipt_never_causes_another_ordinary_submission():
    app, original = active()
    disconnect(app)
    (query,) = [row for row in reconnect(app) if row["type"] == "input_status"]
    app._handle_response(
        result(
            query,
            {
                "id": query["message_id"],
                "status": "running",
                "attempt_id": "attempt-one",
                "session_id": "session-one",
                "response": "",
                "error": "",
            },
        )
    )
    assert app._native_submission.run_id == original["run_id"]
    assert app._native_submission.acknowledged and drain(app) == []


def test_terminal_failure_returns_uncertain_agent_lane_as_draft_without_replaying_ordinary_input():
    app, original = active()
    acknowledge(app, original)
    app._input_controls_supported = True
    app._send_runtime_input("follow_up", "uncertain agent lane")
    (control,) = drain(app)
    assert control["type"] == "input_control"
    app._editor.set_text("current draft")
    app._handle_response({"type": "error", "run_id": original["run_id"], "message": "model failed"})
    assert not app._routed_inputs and not app._input_requests
    assert app._editor.get_text() == "uncertain agent lane\n\ncurrent draft"
    assert not any(row["type"] == "message" for row in drain(app))


def test_host_aborted_terminal_receipt_does_not_suppress_unrelated_queued_input():
    app, original = active()
    acknowledge(app, original)
    app._pending_messages.append("unrelated queued input")
    app._pending_ids.sync(1)
    app._handle_response({"type": "aborted", "run_id": original["run_id"]})
    (sent,) = [row for row in drain(app) if row["type"] == "message"]
    assert sent["content"] == "unrelated queued input" and sent["run_id"] != original["run_id"]


def test_cancel_intent_survives_ack_loss_and_never_resubmits_a_known_absent_original():
    app, original = active()
    app._handle_escape()
    (abort,) = drain(app)
    assert abort["type"] == "abort" and abort["run_id"] == original["run_id"]
    disconnect(app)
    (query,) = [row for row in reconnect(app) if row["type"] == "input_status"]
    app._handle_response(result(query, None))
    assert not any(row["type"] == "message" for row in drain(app))
    assert app._native_submission is None
