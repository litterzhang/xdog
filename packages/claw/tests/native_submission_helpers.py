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
