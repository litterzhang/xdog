"""Host-owned prompt input hooks: FIFO, protected controls and explicit recovery."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from xdog.tui.keys import KeyEvent, KeyEventType, parse_key_events
from xdog.tui.stdin_buffer import KeyBytes, Paste, key_tokens
from xdog.tui.terminal_interception import HeldTerminalInput, TerminalInputQueue
from xdog.tui.tui import TUI

# No transformed key can invoke submission, extensions, external programs or a
# host surface. Hosts additionally classify their configurable bindings.
EDIT_ACTIONS = frozenset({
    'left', 'right', 'home', 'end', 'previous', 'next', 'backspace', 'delete',
    'start', 'finish', 'kill_end', 'kill_start', 'yank', 'yank_pop', 'undo', 'redo', 'complete',
})
ORDERED_ACTIONS = frozenset({'send', 'newline', 'steer', 'follow_up'})


def prompt_key_policy(event: KeyEvent, action: str | None, *, extension: bool = False) -> str:
    if event.key == 'escape' or event.ctrl and event.key.lower() in {'c', 'd', 'l', 'z'}:
        return 'immediate'
    if extension:
        return 'immediate'
    if action in ORDERED_ACTIONS:
        return 'ordered'
    if action in EDIT_ACTIONS:
        return 'edit'
    if action is not None or event.ctrl or event.alt or len(event.key) != 1 or not event.key.isprintable():
        return 'immediate'
    return 'edit'


class PromptTerminalInterceptor:
    """Prompt edits can transform; opt-in releases are observation-only, never edits.

    Other surfaces and protected controls stay host-owned.

    send() is enqueue-only. Completion is accepted on the UI thread. Original
    submission controls are ordered behind prior text, but never sent to hooks.
    Interrupts/handoffs invalidate held input immediately. Hook errors also
    discard ordered submissions, rather than submitting a partial draft.
    """

    def __init__(self, tui: TUI, eligible: Callable[[], bool], owner: Callable[[], str],
                 available: Callable[[], bool], policy: Callable[[KeyEvent], str],
                 send: Callable[[HeldTerminalInput], bool], cancel: Callable[[], None],
                 notice: Callable[[str], None], *, surface: Callable[[], str] = lambda: 'prompt',
                 release_available: Callable[[], bool] = lambda: False) -> None:
        self.tui, self.eligible, self.host_owner = tui, eligible, owner
        self.available, self.policy, self.send, self.cancel = available, policy, send, cancel
        self.notice, self.surface = notice, surface
        self.release_available = release_available
        self._failed = False
        self._closed = False
        self.paused = False
        self._control_was_paused: bool | None = None
        self.queue = TerminalInputQueue(self.owner, self._send, self._deliver, self._notice)
        tui.set_frame_interceptor(self.intercept)

    def owner(self) -> str:
        return f'{self.host_owner()}:{self.tui.input_lease}:{self.eligible()}:{self._closed}:{self.release_available()}'

    def _notice(self, message: str) -> None:
        self._failed = True
        self.paused = True
        self.notice(message + '; hooks paused; /terminal-input list to inspect originals, resume to re-enable')

    def _send(self, item: HeldTerminalInput) -> bool:
        if self._failed:
            return False
        if item.surface == 'prompt-control':
            self.queue.accept(item.id, {})
            return True
        if self.paused:
            return False
        if not self.available():
            # A last-hook unsubscribe is a normal registry update, not an RPC
            # failure. Same-owner queued input drains once without another hook.
            # Lifecycle/capability changes are fenced before dispatch in poll().
            self.queue.accept(item.id, {})
            return True
        if item.kind == 'release' and not self.release_available():
            return False
        return self.send(item)

    def _deliver(self, item: HeldTerminalInput, data: str) -> None:
        if not self.eligible() or self._failed:
            self.queue.reject(item, 'Terminal prompt no longer owns input')
            return
        if item.surface == 'prompt-control':
            if data != item.data:
                self.queue.reject(item, 'Host control cannot be transformed')
                return
        elif item.kind == 'key':
            try:
                tokens = key_tokens(data.encode())
                events = [parse_key_events(token) for token in tokens]
                if any(len(row) != 1 or row[0].event_type is KeyEventType.RELEASE
                       or self.policy(row[0]) != 'edit' for row in events):
                    raise ValueError('Transformed terminal control is protected')
            except (ValueError, UnicodeError):
                self.queue.reject(item, 'Unsafe terminal transformation rejected')
                return
        # Atomic paste is always paste, including ESC/newline/submit-looking text.
        # The focused editor sanitizes it; it can never become key/protocol input.
        self.tui.deliver_frame(Paste(data) if item.kind == 'paste' else KeyBytes(data.encode()))
        self.tui.request_render()

    def invalidate(self) -> None:
        self.cancel()
        self.queue.invalidate()
        self._failed = False

    def intercept(self, frame: KeyBytes | Paste) -> bool:
        if self._closed:
            return False
        if not self.eligible():
            if self.queue.pending:
                self.invalidate()
            return False
        if (self.paused or not self.available()) and not self.queue.pending:
            return False
        if isinstance(frame, Paste):
            if not self.queue.submit(frame.text, kind='paste', surface=self.surface()):
                self.notice('Paste exceeds terminal-hook input limits; paste was not replayed')
            return True
        try:
            tokens = key_tokens(frame.data)
            if any(len(token) > 16000 for token in tokens):
                raise ValueError("Terminal key token exceeds interception limit")
        except (ValueError, UnicodeError):
            self.invalidate()
            self.paused = True
            self.notice('Unsupported terminal key encoding refused; hooks paused, no raw fallback')
            return True
        editable = bytearray()
        def flush() -> None:
            if editable:
                self.queue.submit(editable.decode(), kind='key', surface=self.surface())
                editable.clear()
        for token in tokens:
            events = parse_key_events(token)
            if len(events) == 1 and events[0].event_type is KeyEventType.RELEASE:
                # Opt-in observation only. Classify as a press solely for protected
                # binding checks; preserve the original release bytes for the hook.
                if (self.release_available()
                        and self.policy(replace(events[0], event_type=KeyEventType.PRESS)) == 'edit'):
                    flush()
                    self.queue.submit(token.decode(), kind='release', surface=self.surface())
                continue  # Never replay release or let it become an editing action.
            mode = self.policy(events[0]) if len(events) == 1 else 'immediate'
            if mode == 'edit':
                if len(editable) + len(token) > 16000:
                    flush()
                editable.extend(token)
                continue
            flush()  # Never batch across a submit, handoff or reserved control.
            if mode == 'immediate':
                was_paused = self.paused
                self.invalidate()
                self._control_was_paused = was_paused
                try:
                    self.tui.deliver_frame(KeyBytes(token))
                finally:
                    self._control_was_paused = None
            elif not self.queue.pending:
                self.tui.deliver_frame(KeyBytes(token))
            else:
                self.queue.submit(token.decode(), kind='key', surface='prompt-control')
            if not self.eligible() or self.tui._stopped or self.tui._suspend_requested \
                    or self.tui._external_callback is not None or self.tui._screen_change is not None:
                return True
        flush()
        return True

    def poll(self) -> bool:
        if self.queue.pending and (not self.eligible() or any(
                item.owner != self.owner() for item in self.queue.held)):
            self.invalidate()
            return True
        changed = self.queue.poll()
        if self._failed:
            self.invalidate()
        return changed

    def toggle_pause(self) -> None:
        # Interrupting a held callback auto-pauses before the host shortcut runs.
        # Toggle the state at key arrival, not that newly auto-paused state.
        previous = self.paused if self._control_was_paused is None else self._control_was_paused
        self.set_paused(not previous)

    def set_paused(self, paused: bool) -> None:
        if paused:
            self.invalidate()
        self.paused = paused
        self.notice('Terminal input hooks ' + ('paused; prompt input stays host-owned' if paused else 'resumed'))
        self.tui.request_render()

    def recovery_command(self, args: str, restore: Callable[[str], object]) -> None:
        parts = args.split()
        if parts in (['pause'], ['resume']):
            self.set_paused(parts == ['pause'])
            return
        if not parts or parts == ['list']:
            records = self.queue.recovery
            self.notice('Retained terminal inputs (never automatically replayed):\n' +
                        ('\n'.join(f'{item.id} · {item.kind} · {item.data[:120]!r}' for item in records)
                         if records else '(none)') +
                        '\n/terminal-input restore ID | discard ID | clear')
            return
        if parts == ['clear']:
            for record in self.queue.recovery:
                self.queue.take_recovery(record.id)
            self.notice('Retained terminal inputs cleared')
            return
        if (len(parts) != 2 or parts[0] not in {'restore', 'discard'}
                or not parts[1].isascii() or not parts[1].isdigit()):
            self.notice('Usage: /terminal-input [list|restore ID|discard ID|clear|pause|resume]')
            return
        identifier = int(parts[1]) if len(parts[1]) <= 16 else -1
        item = next((item for item in self.queue.recovery if item.id == identifier), None)
        if item is None:
            self.notice('Retained terminal input not found')
            return
        if parts[0] == 'restore':
            if not self.eligible():
                self.notice('Close the active surface before restoring terminal input')
                return
            if item.kind == 'release' or item.kind == 'key' and (item.surface == 'prompt-control' or
                                      any(not char.isprintable() for char in item.data)):
                self.notice('Control keys cannot be restored as input; inspect or discard this record')
                return
            # Explicit draft insertion only, not a raw key replay, attachment
            # import, model submission or extension callback.
            if restore(item.data) is False:
                self.notice('Draft rejected restored text; original input remains retained')
                return
            self.notice('Original terminal text restored to editable draft; not submitted')
        else:
            self.notice('Retained terminal input discarded')
        self.queue.take_recovery(identifier)

    def close(self) -> None:
        self._closed = True
        self.cancel()
        self.queue.close()
        self.tui.set_frame_interceptor(None)
