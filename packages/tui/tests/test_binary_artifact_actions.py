from __future__ import annotations

import hashlib
import time

import pytest
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.transcript_actions import TranscriptActions
from xdog.tui.transcript_transfer import PreparedTransfer, parse_transfer


def wait(actions, events, predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        while events:
            actions.handle(events.pop(0))
        actions.poll()
        if predicate():
            return
        time.sleep(0.003)
    raise AssertionError("Artifact output did not settle")


def test_binary_snapshot_always_reviews_explicit_path_private_no_overwrite_and_owner_fence(tmp_path):
    events, notices = [], []
    context = ["current"]
    actions = TranscriptActions(events.append, notices.append, lambda: context[0], lambda: None)
    data = b"original binary and metadata"

    def prepare(options, owner, key):
        return PreparedTransfer(
            key,
            owner,
            options,
            "Lossless binary artifact; no redaction",
            data,
            hashlib.sha256(data).hexdigest(),
            1,
            ("command", "binary", "metadata"),
            False,
        )

    path = tmp_path / "output.json"
    try:
        actions.start_snapshot(parse_transfer("export", f"{path} artifact"), tmp_path, prepare)
        wait(actions, events, lambda: actions.panel is not None)
        assert not path.exists()  # Explicit path must not bypass review.
        panel = actions.panel
        panel.focused = True
        panel.handle_input(KeyEvent("enter"))
        assert not path.exists()
        panel.set_render_budget(12)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            panel.render(120)
            panel.poll()
            if panel._visible:
                break
            time.sleep(0.003)
        assert panel._visible
        panel.handle_input(KeyEvent("enter", event_type=KeyEventType.REPEAT))
        assert not path.exists()
        panel.handle_input(KeyEvent("enter"))
        wait(actions, events, lambda: not actions.pending and not actions.worker.busy)
        assert path.read_bytes() == data and path.stat().st_mode & 0o777 == 0o600
        actions.start_snapshot(parse_transfer("export", f"{path} artifact"), tmp_path, prepare)
        wait(actions, events, lambda: actions.panel is not None)
        context[0] = "changed"
        actions.poll()
        assert actions.panel is None and path.read_bytes() == data
    finally:
        actions.close()


@pytest.mark.parametrize(
    "action,args",
    [("copy", "artifact"), ("share", "out artifact"), ("export", "out artifact text"), ("export", "out artifact md")],
)
def test_binary_artifact_not_implicitly_copied_shared_or_lossy(action, args):
    with pytest.raises(ValueError):
        parse_transfer(action, args)
    assert parse_transfer("export", "out artifact").format == "json"
