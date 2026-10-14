from __future__ import annotations

import base64
from pathlib import Path

from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.attachments import attachment

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=")


def test_rejected_local_turn_uses_dispatch_owned_images_not_an_outdated_ui_active_batch(tmp_path):
    from unittest.mock import MagicMock

    from xdog.agent import InputRejectedEvent
    from xdog.ai.types import UserMessage

    session = MagicMock()
    session.working_dir = tmp_path
    ui = InteractiveMode(session)
    try:
        wrong, owned = attachment(PNG, "old.png"), attachment(PNG, "current.png")
        ui._active_images = (wrong,)
        ui._dispatch_images = (owned,)
        ui._dispatch_agent_event(InputRejectedEvent(UserMessage(content="current text"), "prompt"))
        ui._event_queue.drain(ui._handle_ui_event)
        assert owned.id in ui._composer.items and wrong.id not in ui._composer.items
        assert "current text" in ui._editor.get_text()
    finally:
        ui._request_exit()
