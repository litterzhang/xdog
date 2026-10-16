"""Focused child roster/forms/transcript; hosts retain all execution and policy ownership."""
from __future__ import annotations

import hashlib
import json
import queue
import shlex
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from xdog.tui.attachments import Attachment, AttachmentComposer
from xdog.tui.child_browser import ChildEntry
from xdog.tui.child_drafts import ChildDraft, draft_receipt
from xdog.tui.components.attachment_panel import AttachmentPanel
from xdog.tui.components.child_draft_panel import ChildDraftPanel, DraftEntry, draft_entries
from xdog.tui.components.child_queue_panel import ChildQueuePanel
from xdog.tui.components.input_panel import InputPanel
from xdog.tui.components.transcript_panel import TranscriptPanel
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.transcript_browser import TranscriptRecord
from xdog.tui.tui import CURSOR_MARKER, Component
from xdog.tui.utils import sanitize_terminal_text, truncate_to_width, wrap_text_with_ansi


class _Theme:
    accent = bold = dim = border = staticmethod(lambda text: text)


class ChildPanel(Component):
    requires_visible_confirmation = True

    def __init__(self, send: Callable[[str, str, str, str], None], read: Callable[[str], None],
                 close: Callable[[], None], output: Callable[[str, str], None],
                 fullscreen: Callable[[], None], *,
                 configure: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
                 configured_start: Callable[[str, str, dict[str, Any]], None] | None = None,
                 image_send: Callable[[str, str, str, str, dict[str, Any], tuple[Attachment, ...]], None] | None = None,
                 root: Path = Path('.'), drafts: dict[tuple[str, str], ChildDraft] | None = None,
                 retained_bytes: Callable[[], int] | None = None) -> None:
        self.owner_token = uuid.uuid4().hex
        self.send, self.read, self.on_close = send, read, close
        self.output, self.fullscreen = output, fullscreen
        self.configure, self.configured_start = configure, configured_start
        self.image_send, self._root = image_send, root
        self._retained_bytes = retained_bytes
        self.draft_panel: ChildDraftPanel | None = None
        self._drafts = drafts if drafts is not None else {}
        self._attachment_events: queue.SimpleQueue[tuple[int, dict[str, object]]] = queue.SimpleQueue()
        self._attachment_generation = 0
        self.composer: AttachmentComposer | None = None
        self._image_pending: tuple[str, str, ChildDraft] | None = None
        self._image_preview: AttachmentPanel | None = None
        self._image_selected = 0
        self._presets: tuple[dict[str, Any], ...] = ()
        self._preset_selected = 0
        self._preset_error = ""
        self._config: dict[str, Any] = {}
        self._task_draft = ""
        self._review_scroll = 0
        self._reviewed: set[int] = set()
        self._review_layout: tuple[int, int] = (0, 0)
        self._focused = False
        self._budget = 8
        self._entries: tuple[ChildEntry, ...] = ()
        self._selected_id = ""
        self._query = ""
        self._filtering = False
        self._mode = "list"
        self._terminal_epoch = 0
        self._busy = False
        self._error = ""
        self._confirm: tuple[str, str, str, str] | None = None
        self._form_action = ""
        self._form_child = ""
        self._draft_slot = ""
        self._access = "read-only"
        self._visible = False
        self._live = False
        self._waiting_id = ""
        self._last_refresh = 0.0
        self.preview: TranscriptPanel | None = None
        self.queue_enabled = False
        self.queue: ChildQueuePanel | None = None
        self._input = InputPanel(_Theme(), max_rows=3)
        self._input.on_submit = self._submit
        self._input.on_escape = self.back

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, value: bool) -> None:
        self._focused = value
        self._input.focused = value and self._mode in {"form", "config", "attach"}
        if self.preview is not None:
            self.preview.focused = value and self._mode == "preview"
        if self.queue is not None:
            self.queue.focused = value and self._mode == 'queue'

        if self.draft_panel is not None:
            self.draft_panel.focused = value and self._mode == 'drafts'

    @property
    def terminal_child(self) -> str:
        """Only an existing child's editable steering/follow-up draft is hookable."""
        return (self._form_child if self.focused and self._mode == "form"
                and self._form_action in {"steer", "follow-up"} and not self._busy else "")

    @property
    def terminal_lease(self) -> str:
        return f"{self._terminal_epoch}:{self._mode}:{self._form_action}:{self._form_child}"

    def terminal_policy_action(self, event: KeyEvent) -> str | None:
        return self._input._keymap.resolve(event)

    def restore_terminal_text(self, text: str) -> bool:
        return bool(self.terminal_child) and self._input.handle_paste(text)

    @property
    def child_id(self) -> str:
        return self._selected_id if self._mode == "preview" else ""

    def set_render_budget(self, rows: int) -> None:
        self._budget = max(0, rows if self.preview is not None and self.preview.fullscreen else min(12, rows))

    def publish(self, entries: tuple[ChildEntry, ...], error: str = "") -> None:
        self._entries = entries
        self._error = sanitize_terminal_text(error)
        self._busy = False
        visible = self._rows()
        if self._selected_id not in {entry.id for entry in visible}:
            if self.preview is not None:
                self.back()
            self._selected_id = visible[0].id if visible else ""
        if self._confirm is not None and self._confirm[1] and self._confirm[1] not in {e.id for e in entries}:
            self._confirm = None
            self._mode = "list"
        self._last_refresh = time.monotonic()

    def complete(self, action: str, error: str = "", data: Any = None,
                 images: tuple[Attachment, ...] = ()) -> None:
        if self._image_pending is not None and action == self._image_pending[0]:
            pending_action, child, draft = self._image_pending
            if (not error and (not isinstance(data, dict)
                    or data.get('accepted_input') != draft_receipt(draft.text, draft.images))):
                error = 'Child image admission receipt missing/mismatched; draft retained, inspect before retrying'
            if error:
                self.fail(error)
            else:
                self._image_pending = None
                self._drafts.pop((pending_action, self._draft_slot), None)
                if self.composer is not None:
                    self.composer.take()
                self._busy = False
                self._mode = 'list'
            return
        if action == 'queue' and self.queue is not None:
            if error:
                self.queue.fail(error)
            else:
                try:
                    if images or not isinstance(data, dict) or set(data) != {'queue'}:
                        raise ValueError('Queue inspection refuses binary data and recovery/mutation receipts')
                    self.queue.publish(data['queue'])
                except (ValueError, TypeError, KeyError) as exc:
                    self.queue.fail(str(exc))
            return
        if action == "wait":
            self._waiting_id = ""
        self.fail(error)

    def open_presets(self) -> None:
        if self._mode != "form" or self._form_action != "start":
            self._form("start")
        self._mode = "presets"
        self._input.focused = False
        self._visible = False

    def publish_presets(self, rows: tuple[dict[str, Any], ...], error: str = "") -> None:
        self._presets = rows
        self._preset_error = sanitize_terminal_text(error)
        self._preset_selected = min(self._preset_selected, max(0, len(rows) - 1))
        self._visible = False

    def fail(self, error: str) -> None:
        if error and self._image_pending is not None:
            action, child, draft = self._image_pending
            self._drafts[action, self._draft_slot] = replace(draft, uncertain=True)
            self._image_pending = None
            self._form_action, self._form_child = action, child
            self._mode = 'form'
            self._input.set_text(draft.text)
            self._input.focused = self.focused
            self._visible = False
        self._busy = False
        if self.queue is not None and error:
            self.queue.fail(error)
        self._error = sanitize_terminal_text(error)
        if self._mode == "presets":
            self._preset_error = self._error

    def source(self, identifier: str, records: tuple[TranscriptRecord, ...], error: str = "") -> None:
        if identifier != self.child_id or self.preview is None:
            return
        if error:
            self.preview.replace_records(())
            self.preview.set_source_status("Child source failed: " + error)
        else:
            self.preview.set_source_status("Child snapshot · l toggles live")
            self.preview.replace_records(records)

    def reading(self) -> None:
        if self.preview is not None:
            self.preview.set_source_status("Child source loading", busy=True)

    def _rows(self) -> tuple[ChildEntry, ...]:
        return tuple(entry for entry in self._entries if self._query.casefold() in
                     f"{entry.id} {entry.label} {entry.task} {entry.status} {entry.access}".casefold())

    def poll(self) -> bool:
        attachments_changed = self._poll_attachments()
        changed = self.preview.poll() if self.preview is not None else False
        if (time.monotonic() - self._last_refresh >= 1 and not self._busy
                and self._mode not in {"form", "confirm", "pending", "attach", "images", "image-preview",
                                       "config", "drafts"}):
            self._last_refresh = time.monotonic()
            self._busy = True
            self.send("list", "", "", "read-only")
            if (self._live and self.preview is not None and self.preview.settled
                    and self.preview.selection is None and not self.preview.interactive_query):
                self.read(self.child_id)
            changed = True
        return changed or attachments_changed

    def request_list(self) -> None:
        self._busy = True
        self._last_refresh = time.monotonic()
        self.send("list", "", "", "read-only")

    def inspect(self, identifier: str) -> None:
        if identifier not in {entry.id for entry in self._entries}:
            self._error = "Child unavailable; refresh before inspection"
            return
        if self.preview is not None:
            self.preview.close()
        self._selected_id = identifier
        self._mode = "preview"
        self._live = False
        self.preview = TranscriptPanel(lambda: (), self.back, self.fullscreen,
                                      on_refresh=lambda: self.read(self.child_id), on_output=self.output)
        self.preview.focused = self.focused
        self.read(identifier)

    def close(self) -> None:
        self._close_draft_recovery()
        if self.queue is not None:
            self.queue.close()
        self._save_draft()
        if self.composer is not None:
            self.composer.close()
        if self.preview is not None:
            self.preview.close()

    def back(self) -> None:
        self._close_draft_recovery()
        if self.queue is not None:
            self.queue.close()
        self._save_draft()
        if self.composer is not None:
            self.composer.close()
            self.composer = None
            self._attachment_generation += 1
        self.queue = None
        self.draft_panel = None
        self._terminal_epoch += 1
        if self.preview is not None:
            if self.preview.fullscreen:
                self.fullscreen()
            self.preview.close()
            self.preview = None
        self._mode = "list"
        self._confirm = None
        self._visible = False
        self._input.focused = False

    def _close_draft_recovery(self) -> None:
        if self.draft_panel is not None:
            self.draft_panel.close()
            self.draft_panel = None

    def _transient_entries(self) -> tuple[DraftEntry, ...]:
        return draft_entries({self.owner_token: self._drafts}, {self.owner_token: 'Current session'}, self.owner_token)

    def open_drafts(self) -> None:
        self._poll_attachments()
        if self._image_pending is not None or self.composer is not None and self.composer.reader.pending:
            self._error = 'Wait for child image read/admission settlement before recovery'
            return
        self._save_draft()
        self._close_draft_recovery()
        self._terminal_epoch += 1
        if self.preview is not None and self.preview.fullscreen:
            self.fullscreen()
        if self.composer is not None:
            self.composer.close()
            self.composer = None
            self._attachment_generation += 1
        self._mode = 'drafts'
        self._input.focused = False
        def reopen(action: str, entry: DraftEntry) -> None:
            if (action != 'reopen' or not self.focused or self._mode != 'drafts'
                    or self.draft_panel is not browser or self.terminal_lease != lease
                    or browser.view_lease != view_lease):
                raise ValueError('Child recovery owner/view changed; review current input, no replay')
            self.adopt_draft(entry, reopen=True)
        browser = ChildDraftPanel(self._transient_entries, reopen, self.back)
        self.draft_panel = browser
        browser.focused = self.focused
        lease, view_lease = self.terminal_lease, browser.view_lease
        self._visible = False

    def adopt_draft(self, entry: DraftEntry, *, reopen: bool = False) -> None:
        # Same-session editing recovery only. Disk/cross-context/copy grants are gone.
        current = self._drafts.get(entry.key)
        if (not reopen or not entry.current or entry.scope != self.owner_token
                or current is None or current is not entry.original or current != entry.value):
            raise ValueError('Only an unchanged current-session transient input can reopen')
        if entry.value.images and self.image_send is None:
            raise ValueError('Image-capable child draft owner required; no text fallback')
        action, slot = entry.key
        if action not in {'start', 'steer', 'follow-up'}:
            raise ValueError('Unsupported retained draft action')
        if action != 'start':
            child = next((row for row in self._entries if row.id == slot), None)
            if child is None or child.status != 'running':
                raise ValueError('Child no longer running; transient input kept, no automatic replay')
            self._selected_id = slot
        self._close_draft_recovery()
        self._form(action, draft_slot=slot)
        self._input.set_text(entry.value.text)
        self._config = json.loads(json.dumps(entry.value.config))
        self._access = entry.value.access
        if entry.value.cursor is not None:
            self._input._cursor = min(max(0, entry.value.cursor), len(self._input.get_text()))
        if entry.value.uncertain:
            self._error = 'Previous receipt uncertain; inspect actual children before explicitly submitting'
        self._save_draft()
        self._input.focused = self.focused
        self._visible = False

    def open_queue(self) -> None:
        if not self.queue_enabled or not self._selected_id:
            self._error = 'Updated read-only child queue host required; no local replica or fallback'
            return
        self._save_draft()
        self._close_draft_recovery()
        if self.queue is not None:
            self.queue.close()
        child = self._selected_id
        self._terminal_epoch += 1
        self._mode = 'queue'
        self._input.focused = False
        lease = self.terminal_lease
        def refresh() -> None:
            if (not self.focused or not self.queue_enabled or self._mode != 'queue'
                    or self.queue is not inspector or self.terminal_lease != lease
                    or inspector._closed or not inspector.focused or not inspector.enabled):
                raise ValueError('Child queue inspection owner changed; explicitly refresh current view')
            self.send('queue', child, json.dumps({'action': 'list'}), 'read-only')
        inspector = ChildQueuePanel(child, refresh, self.back)
        self.queue = inspector
        inspector.focused = self.focused
        inspector.refresh()  # Only list metadata; Agent/SDK recovery and mutation are not UI actions.

    def _form(self, action: str, *, draft_slot: str | None = None) -> None:
        self._close_draft_recovery()
        self._save_draft()
        self._terminal_epoch += 1
        if action != "start" and not self._selected_id:
            return
        if self.preview is not None and self.preview.fullscreen:
            self.fullscreen()
        self._form_action, self._form_child = action, "" if action == "start" else self._selected_id
        self._draft_slot = self._form_child if draft_slot is None else draft_slot
        self._mode = "form"
        self._access = "read-only"
        self._config = {}
        draft = self._drafts.get((action, self._draft_slot), ChildDraft())
        self._config, self._access = dict(draft.config), draft.access
        if draft.uncertain:
            self._error = 'Previous child submission receipt uncertain; inspect roster before resubmitting'
        self._input.set_text(draft.text)
        if self.image_send is not None:
            if self.composer is not None:
                self.composer.close()
            self._attachment_generation += 1
            generation = self._attachment_generation
            self.composer = AttachmentComposer(
                lambda event: self._attachment_events.put((generation, event)), self._attachment_changed,
                lambda notice: setattr(self, '_error', sanitize_terminal_text(notice)),
                self._attachment_paste, self._preview_image)
            self.composer.restore(draft.images)
        if draft.cursor is not None:
            self._input._cursor = min(max(0, draft.cursor), len(self._input.get_text()))
        self._input.focused = self.focused
        self._visible = False

    def _save_draft(self) -> None:
        if not self._form_action:
            return
        if self._image_pending is not None:
            action, child, draft = self._image_pending
            self._drafts[action, self._draft_slot] = replace(draft, uncertain=True)
            return
        if self._mode not in {'form', 'config', 'confirm', 'attach', 'images', 'image-preview'}:
            return
        text = (self._confirm[2] if self._mode == 'confirm' and self._confirm is not None else
                self._task_draft if self._mode in {'config', 'attach'} else self._input.get_text())
        self._drafts[self._form_action, self._draft_slot] = ChildDraft(
            text, self._access, dict(self._config), tuple(self.composer.items.values()) if self.composer else (),
            self._drafts.get((self._form_action, self._draft_slot), ChildDraft()).uncertain,
            self._input._cursor if self._mode == 'form' else None)

    def _attachment_changed(self) -> None:
        images = tuple(self.composer.items.values()) if self.composer else ()
        self._input.set_attachment_summary(f'Images: {len(images)} · Ctrl+B inspect/remove' if images else '')
        if self._mode == 'confirm' and self._confirm is not None:
            self._input.set_text(self._confirm[2])
            self._confirm = None
            self._mode = 'form'
            self._input.focused = self.focused
        self._visible = False
        self._reviewed.clear()

    def _attachment_paste(self, text: str) -> bool:
        if self._mode == 'confirm' and self._confirm is not None:
            self._input.set_text(self._confirm[2])
            self._confirm = None
            self._mode = 'form'
            self._input.focused = self.focused
        if self._mode in {'config', 'attach'}:
            self._task_draft += text
            return True
        return self._input.handle_paste(text)

    def _poll_attachments(self) -> bool:
        changed = False
        while not self._attachment_events.empty():
            generation, event = self._attachment_events.get()
            if generation == self._attachment_generation and self.composer is not None:
                current = set(self.composer.items)
                other = {item.id: item for draft in self._drafts.values() for item in draft.images
                         if item.id not in current}
                retained = self._retained_bytes() if self._retained_bytes is not None else sum(
                    len(item.data) for item in other.values())
                self.composer.handle(event, retained=retained)
                changed = True
        return changed

    def paste_clipboard(self) -> None:
        if self._mode == 'form' and self.composer is not None and not self._busy:
            self.composer.command('paste', '', self._root)
            self._visible = False

    def _preview_image(self, image: Attachment) -> None:
        self._mode = 'image-preview'
        self._input.focused = False
        self._image_preview = AttachmentPanel(image, self._leave_images)
        self._image_preview.focused = self.focused

    def _leave_images(self) -> None:
        self._mode = 'form'
        self._image_preview = None
        self._input.focused = self.focused
        self._visible = False

    def _submit(self, text: str) -> None:
        if self._mode == 'attach' and self.composer is not None:
            self.composer.command('attach', shlex.quote(text.strip()), self._root)
            self._mode = 'form'
            self._input.set_text(self._task_draft)
            self._input.focused = self.focused
            self._visible = False
            return
        if self._mode == "config":
            if not self._visible or self.configure is None:
                return
            try:
                value = json.loads(text)
                self._config = self.configure(value)
            except (ValueError, TypeError) as exc:
                self._error = str(exc)
                return
            self._error = ""
            self._mode = "form"
            self._input.set_text(self._task_draft)
            self._visible = False
            return
        # Preparing a local review is not execution. A pending roster reply must
        # not drop the task after InputPanel has consumed its submitted draft.
        # The final confirmation below still requires a settled owner request.
        images = tuple(self.composer.items.values()) if self.composer else ()
        if not self._visible or not (text.strip() or images) or len(text.encode("utf-8")) > 16000:
            return
        if self.composer is not None and self.composer.reader.pending:
            self._input.set_text(text)
            self._error = 'Wait for the child attachment/clipboard read before reviewing'
            return
        self._reviewed.clear()
        self._review_scroll = 0
        self._review_layout = (0, 0)
        self._confirm = self._form_action, self._form_child, text, self._access
        self._mode = "confirm"
        self._input.focused = False
        self._visible = False

    def handle_paste(self, text: str) -> bool:
        if self._mode == 'drafts' and self.draft_panel is not None:
            return self.draft_panel.handle_paste(text)
        if self._mode == 'queue' and self.queue is not None:
            return self.queue.handle_paste(text)
        if self._mode in {"form", "config", "attach"}:
            self._input.handle_paste(text)
        elif self._mode == "preview" and self.preview is not None:
            self.preview.handle_paste(text)
        elif self._filtering:
            self._query += sanitize_terminal_text(text).replace("\n", " ")[:512]
        return True

    def handle_input(self, event: KeyEvent) -> bool:
        self._poll_attachments()
        if event.event_type is not KeyEventType.PRESS:
            return True
        if self._mode == 'drafts' and self.draft_panel is not None:
            return self.draft_panel.handle_input(event)
        if self._mode == 'queue' and self.queue is not None:
            self.queue.enabled = self.queue_enabled
            return self.queue.handle_input(event)
        if self._mode == 'pending':
            return True  # Receipt settlement owns this draft; no repeated submission.
        if self._mode == 'image-preview' and self._image_preview is not None:
            return self._image_preview.handle_input(event)
        if self._mode == 'images' and self.composer is not None:
            images = tuple(self.composer.items.values())
            self._image_selected = min(self._image_selected, max(0, len(images) - 1))
            if event.matches('escape') or event.matches('ctrl+c'):
                self._leave_images()
            elif event.key in {'up', 'down'}:
                self._image_selected = min(max(0, self._image_selected + (-1 if event.key == 'up' else 1)),
                                           max(0, len(images) - 1))
            elif event.matches('x') and images:
                self.composer.command('attachments', 'remove ' + images[self._image_selected].id, self._root)
            elif event.matches('c'):
                self.composer.command('attachments', 'clear', self._root)
            elif event.matches('enter') and images:
                self._preview_image(images[self._image_selected])
            return True
        if self._mode == 'attach':
            if event.matches('escape') or event.matches('ctrl+c'):
                self._mode = 'form'
                self._input.set_text(self._task_draft)
                self._visible = False
            elif not event.matches('enter') or self._visible:
                self._input.handle_input(event)
            return True
        if self._mode == "confirm":
            if event.matches("escape") or event.matches("ctrl+c"):
                draft = self._confirm[2] if self._confirm is not None else ""
                self._mode = "form" if self._form_action and self._confirm and self._confirm[0] in {
                    "start", "steer", "follow-up"} else "list"
                if self._mode == "form":
                    self._input.set_text(draft)
                self._terminal_epoch += 1
                self._confirm = None
                self._input.focused = self.focused and self._mode == "form"
            elif event.key in {"up", "down", "pageup", "pagedown", "home", "end"}:
                delta = max(1, self._budget - 3) if event.key in {"pageup", "pagedown"} else 1
                self._review_scroll = (0 if event.key == "home" else 100000 if event.key == "end" else
                                       max(0, self._review_scroll
                                           + (-delta if event.key in {"up", "pageup"} else delta)))
                self._visible = False
            elif event.matches("enter") and self._visible and self._confirm is not None and not self._busy:
                action, child, text, access = self._confirm
                self._confirm = None
                self._mode = "list"
                self._busy = True
                images = tuple(self.composer.items.values()) if self.composer else ()
                if images and self.image_send is not None:
                    image_draft = ChildDraft(text, access, dict(self._config), images)
                    self._image_pending = action, child, image_draft
                    self._drafts[action, self._draft_slot] = image_draft
                    self._mode = 'pending'
                    self.image_send(action, child, text, access, dict(self._config), images)
                elif action == "start" and self.configured_start is not None:
                    self.configured_start(text, access, dict(self._config))
                else:
                    self.send(action, child, text, access)
            return True
        if self._mode == "presets":
            if event.matches("escape") or event.matches("ctrl+c"):
                self._mode = "form"
                self._input.focused = self.focused
            elif event.key in {"up", "down"}:
                self._preset_selected = min(max(0, self._preset_selected + (-1 if event.key == "up" else 1)),
                                            max(0, len(self._presets) - 1))
                self._visible = False
            elif event.matches("enter") and self._visible and self._presets and self.configure is not None:
                try:
                    self._config = self.configure({"preset": self._presets[self._preset_selected]["name"]})
                except (ValueError, TypeError) as exc:
                    self._preset_error = str(exc)
                    return True
                self._mode = "form"
                self._input.focused = self.focused
                self._visible = False
            return True
        if self._mode == "config":
            if event.matches("escape") or event.matches("ctrl+c"):
                self._mode = "form"
                self._input.set_text(self._task_draft)
                self._visible = False
            elif not event.matches("enter") or self._visible:
                self._input.handle_input(event)
            return True
        if self._mode == "form":
            if event.matches('ctrl+o') and self.composer is not None:
                self._task_draft = self._input.get_text()
                self._mode = 'attach'
                self._input.set_text('')
                self._visible = False
            elif event.matches('ctrl+b') and self.composer is not None:
                self._mode = 'images'
                self._input.focused = False
                self._visible = False
            elif event.matches('ctrl+v') and self.composer is not None:
                self.paste_clipboard()
            elif event.matches('enter') and self._visible and self.composer is not None and self.composer.items \
                    and not self._input.get_text().strip():
                self._submit('')
            elif event.matches("ctrl+c"):
                self.back()
            elif event.matches("ctrl+p") and self._form_action == "start":
                self.open_presets()
            elif event.matches("ctrl+g") and self._form_action == "start" and self.configure is not None:
                self._task_draft = self._input.get_text()
                self._mode = "config"
                self._input.set_text(json.dumps(self._config, indent=2))
                self._visible = False
            elif event.matches("tab") and self._form_action == "start":
                self._access = "inherit" if self._access == "read-only" else "read-only"
                self._visible = False
            elif not event.matches("enter") or self._visible:
                self._input.handle_input(event)
            return True
        if self._mode == "preview" and self.preview is not None:
            if event.matches("ctrl+s"):
                self._form("steer")
            elif event.matches("ctrl+t"):
                self._form("follow-up")
            elif event.matches('ctrl+q'):
                self.open_queue()
            elif event.matches("l") and not self.preview.searching:
                self._live = not self._live
            else:
                self.preview.handle_input(event)
            return True
        if event.matches("escape") or event.matches("ctrl+c"):
            if self._filtering:
                self._filtering = False
            else:
                self.on_close()
        elif self._filtering:
            if event.matches("enter"):
                self._filtering = False
            elif event.matches("backspace"):
                self._query = self._query[:-1]
            elif len(event.key) == 1 and not event.ctrl and not event.alt:
                self._query = (self._query + sanitize_terminal_text(event.key))[:512]
        elif event.matches("/"):
            self._filtering = True
        elif event.key in {"up", "down"}:
            rows = self._rows()
            index = next((i for i, row in enumerate(rows) if row.id == self._selected_id), 0)
            if rows:
                self._selected_id = rows[min(max(0, index + (-1 if event.key == "up" else 1)), len(rows) - 1)].id
        elif event.matches("enter") and self._visible and self._selected_id:
            self.inspect(self._selected_id)
        elif event.matches('d'):
            self.open_drafts()
        elif event.matches('q'):
            self.open_queue()
        elif event.matches("n"):
            self._form("start")
        elif event.key in {"s", "t"}:
            self._form("steer" if event.key == "s" else "follow-up")
        elif event.key in {"c", "x"} and self._selected_id:
            self._confirm = ("cancel" if event.key == "c" else "close", self._selected_id, "", "read-only")
            self._mode = "confirm"
            self._visible = False
        elif event.matches("w") and self._selected_id and not self._waiting_id:
            self._waiting_id = self._selected_id
            self.send("wait", self._selected_id, "", "read-only")
        elif event.matches("r"):
            self.request_list()
        return True

    def render(self, width: int) -> list[str]:
        self._visible = False
        if not self._budget:
            return []
        title = f"Child agents · {len(self._entries)} · {'updating' if self._busy else 'live roster'}"
        if self._mode == 'drafts' and self.draft_panel is not None:
            self.draft_panel.set_render_budget(self._budget)
            return self.draft_panel.render(width)
        if self._mode == 'pending':
            return ['Child image input settling · draft retained until exact acceptance receipt'][:self._budget]
        if self._mode == 'image-preview' and self._image_preview is not None:
            self._image_preview.set_render_budget(self._budget)
            return self._image_preview.render(width)
        if self._mode == 'images' and self.composer is not None:
            images = tuple(self.composer.items.values())
            rows = ['Child draft images · NOT sent',
                    *(f'{"▶" if index == self._image_selected else " "} {item.summary}'
                      for index, item in enumerate(images)),
                    '↑/↓ choose · Enter preview · x remove · c clear · Esc task']
            return [truncate_to_width(row, max(0, width), '') for row in rows[:self._budget]]
        if self._mode == 'attach':
            self._input.set_render_budget(max(1, min(3, self._budget - 3)), show_borders=False)
            rows = ['Attach image to CHILD draft · enter a literal path', *self._input.render(width),
                    'Enter reads asynchronously · Esc preserves task']
            self._visible = self._budget >= 4 and width >= 24
            return [truncate_to_width(row, max(0, width), '') for row in rows[:self._budget]]
        if self._mode == 'queue' and self.queue is not None:
            self.queue.enabled = self.queue_enabled
            self.queue.set_render_budget(self._budget)
            return self.queue.render(width)
        if self._mode == "preview" and self.preview is not None:
            self.preview.set_render_budget(max(0, self._budget - 1))
            rows = [f"Child {self.child_id} · {'LIVE' if self._live else 'snapshot'} · Ctrl+S steer · Ctrl+T follow-up",
                    *self.preview.render(width)]
        elif self._mode == "presets":
            rows = ["Child specialist presets · Enter applies draft only"]
            count = max(0, self._budget - 2)
            start = max(0, min(self._preset_selected - count // 2, len(self._presets) - count))
            for index in range(start, min(len(self._presets), start + count)):
                preset = self._presets[index]
                rows.append(f"{'▶' if index == self._preset_selected else ' '} {preset['name']} · "
                            + sanitize_terminal_text(preset["description"]))
            if not self._presets and count:
                rows.append(self._preset_error or "No configured presets; use Ctrl+G custom settings")
            rows.append("↑/↓ choose · Enter loads settings · Esc task · start still requires full review")
            self._visible = bool(self._presets) and self._budget >= 3 and width >= 24
        elif self._mode == "config":
            self._input.set_render_budget(max(1, min(4, self._budget - 3)), show_borders=False)
            rows = ["Child settings JSON · no work starts", *self._input.render(width)]
            if self._error:
                rows.append(sanitize_terminal_text(self._error))
            rows.append("label/tools/context/budgets/model/thinking · Enter applies draft · Esc back")
            self._visible = self._budget >= 4 and width >= 24
        elif self._mode == "form":
            title = f"Child {self._form_action} {self._form_child} · {self._access}"
            self._input.set_render_budget(max(1, min(3, self._budget - 3)), show_borders=False)
            rows = [f"Child {self._form_action} {self._form_child}", f"Access: {self._access}",
                    *self._input.render(width),
                    ("Enter reviews · Tab access · Ctrl+G settings · Ctrl+P presets"
                     if self.composer is not None else
                     "Enter reviews · Tab access · Ctrl+G settings · Ctrl+P presets · Esc back")]
            if self.composer is not None:
                rows.append('Ctrl+O attach · Ctrl+V clipboard · Ctrl+B images · Esc back')
            if self._error:
                rows.insert(-1, sanitize_terminal_text(self._error))
            self._visible = self._budget >= 4 and width >= 24
        elif self._mode == "confirm" and self._confirm is not None:
            action, child, text, access = self._confirm
            rows = ["Confirm child action", f"{action} · {child or 'new'}",
                    f"Access: {access} · " + (sanitize_terminal_text(text).replace("\n", " ")[:120]
                                            or "Cancel settles; close archives."),
                    "Enter confirms this child/action · Esc back · NOT tool approval"]
            images = tuple(self.composer.items.values()) if self.composer else ()
            if action == "start" and self._config or images:
                disclosure = ('Task: ' + text + '\nConfiguration:\n' + json.dumps(self._config, indent=2)
                              + '\nChild images (exact bytes; not a tool grant):\n' + '\n'.join(
                                  item.summary + '\nSHA256: ' + hashlib.sha256(item.data).hexdigest()
                                  for item in images))
                body = wrap_text_with_ansi(sanitize_terminal_text(disclosure), max(1, width))
                count = max(0, self._budget - 3)
                layout = (width, count)
                if layout != self._review_layout:
                    self._reviewed.clear()
                    self._review_layout = layout
                self._review_scroll = min(self._review_scroll, max(0, len(body) - count))
                end = min(len(body), self._review_scroll + count)
                if width >= 24 and count:
                    self._reviewed.update(range(self._review_scroll, end))
                complete = len(self._reviewed) == len(body)
                rows = [f"Confirm child {action}", f"Access: {access} · {self._review_scroll + 1}-{end}/{len(body)}",
                        *body[self._review_scroll:end],
                        "Enter confirms reviewed settings · Esc back · NOT tool approval" if complete else
                        "PgUp/PgDn review ALL settings before Enter · Esc back"]
                self._visible = complete and self._budget >= 4 and width >= 24
            else:
                self._visible = self._budget >= 4 and width >= 24
        else:
            rows = [title]
            if self._filtering:
                rows.append(truncate_to_width("/ " + self._query, max(0, width - 1), "")
                            + (CURSOR_MARKER if self.focused else ""))
            entries = self._rows()
            if self._selected_id not in {entry.id for entry in entries}:
                self._selected_id = entries[0].id if entries else ""
            selected = next((i for i, entry in enumerate(entries) if entry.id == self._selected_id), 0)
            count = max(0, self._budget - len(rows) - 1 - bool(self._error))
            start = max(0, min(selected - count // 2, len(entries) - count))
            for entry in entries[start:start + count]:
                rows.append(f"{'▶' if entry.id == self._selected_id else ' '} {entry.id} [{entry.status}] "
                            f"{entry.access} · {entry.calls} calls · {entry.tokens} tokens · "
                            f"{entry.label + ' · ' if entry.label else ''}{entry.task}")
            if not entries and count:
                rows.append("No matching children · n starts read-only work")
            if self._error:
                rows.append("Child error: " + self._error)
            rows.append("Enter inspect · / filter · n new · s steer · t follow-up · q inspect inputs · "
                        "d recover unsent · c cancel · x close · Esc back")
            self._visible = self._budget >= 3 and width >= 24 and bool(entries)
        return [truncate_to_width(row, max(0, width), "") for row in rows[:self._budget]]
