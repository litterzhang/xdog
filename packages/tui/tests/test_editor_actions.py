from __future__ import annotations

from xdog.tui.editor_actions import EditorActions


def test_read_replace_single_use_preserves_attachments_and_captures_cursor():
    state = ["draft", 3, ("image-one",)]

    def snapshot():
        return state[0], state[1], state[2]

    def replace(value):
        state[0], state[1] = value, len(value)
        return True

    owner = EditorActions(snapshot, replace)
    captured = owner.handle("command-session", {"operation": "read"})
    assert captured["text"] == "draft" and captured["cursor"] == 3 and captured["image_count"] == 1
    request = {"operation": "replace", "text": "new draft", "revision": captured["revision"]}
    assert owner.handle("command-session", request)["applied"]
    assert state == ["new draft", 9, ("image-one",)]
    assert not owner.handle("command-session", request)["applied"]


def test_typing_cursor_attachments_command_owner_or_invalidated_receipt_refuses_overwrite():
    for change in ("typing", "cursor", "images", "owner", "invalidate"):
        state = ["draft", 5, ("image",)]
        writes = []
        actions = EditorActions(lambda: tuple(state), lambda text: writes.append(text) or True)
        read = actions.handle("old-owner", {"operation": "read"})
        if change == "typing":
            state[0] = "user edited"
        elif change == "cursor":
            state[1] = 1
        elif change == "images":
            state[2] = ("new-image",)
        elif change == "invalidate":
            actions.invalidate()
        result = actions.handle(
            "new-owner" if change == "owner" else "old-owner",
            {"operation": "replace", "text": "must not overwrite", "revision": read["revision"]},
        )
        assert not result["applied"] and not writes


def test_invalid_editor_text_rejection_consumes_receipt_without_changes():
    actions = EditorActions(lambda: ("draft", 5, ()), lambda text: False)
    read = actions.handle("owner", {"operation": "read"})
    assert not actions.handle("owner", {"operation": "replace", "text": "\x1b[2J", "revision": read["revision"]})[
        "applied"
    ]
