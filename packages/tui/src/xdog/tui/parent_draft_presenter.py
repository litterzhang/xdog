"""Shared native primary-draft views; host policy and persistence stay separate."""
from __future__ import annotations

from collections.abc import Callable

from xdog.tui.child_drafts import ChildDraft
from xdog.tui.components.child_draft_panel import DraftEntry
from xdog.tui.components.parent_draft_panel import ParentDraftPanel
from xdog.tui.parent_drafts import ParentDrafts


class ParentDraftPresenter:
    def __init__(self, drafts: ParentDrafts, *, available: Callable[[], bool], prepare: Callable[[], None],
                 mount: Callable[[ParentDraftPanel], None], unmount: Callable[[ParentDraftPanel], None],
                 restore: Callable[[ChildDraft], None], changed: Callable[[], None]) -> None:
        self.drafts = drafts
        self._available, self._prepare, self._mount = available, prepare, mount
        self._unmount, self._restore, self._changed = unmount, restore, changed
        self.panel: ParentDraftPanel | None = None

    def alive(self, panel: ParentDraftPanel, lease: int) -> bool:
        return (self.panel is panel and panel.focused and panel.view_lease == lease
                and panel.context == self.drafts.context() and self._available())

    def close(self) -> None:
        panel, self.panel = self.panel, None
        self.drafts.invalidate_read()
        if panel is not None:
            panel.close()
            self._unmount(panel)  # Host retires only this view, never a protected overlay.
        self._changed()

    def attach(self, panel: ParentDraftPanel) -> None:
        # Successful loading -> recovery replaces the view, not the adopted records.
        if self.panel is not None:
            self.panel.close()
        self.panel = panel
        self._mount(panel)
        lease = panel.view_lease
        self.drafts.bind_read_view(lambda: self.alive(panel, lease))
        self._changed()

    def begin(self) -> None:
        if not self._available():
            raise ValueError('Another modal UI owns input; parent draft unchanged')
        if self.drafts.io.busy:
            raise ValueError('Parent draft disk work still settling')
        self.close()
        self._prepare()

    def command(self, args: str) -> None:
        try:
            if args.strip() == 'save':
                self.show_save()
            elif args.strip().partition(' ')[0] in {'restore', 'saved'}:
                self.begin()
                panel = ParentDraftPanel(lambda: (), lambda *_: None, self.close,
                                         context=self.drafts.context(), mode='loading')
                self.attach(panel)
                lease = panel.view_lease
                try:
                    self.drafts.command(args, alive=lambda: self.alive(panel, lease))
                except ValueError:
                    self.close()
                    raise
            else:
                self.drafts.command(args)
        except ValueError as exc:
            self.drafts.notice(str(exc))
        self._changed()

    def show_save(self) -> None:
        self.begin()
        self.drafts.prepare_save(alive=lambda: False)
        entry = self.drafts.save_entry()
        def act(action: str, reviewed: DraftEntry) -> None:
            if action != 'save' or not self.alive(panel, lease):
                raise ValueError('Parent save view changed; reopen full review')
            self.drafts.confirm_save(reviewed)
            self.close()  # Admitted disk work still owns the host's quit barrier.
        panel = ParentDraftPanel(lambda: (entry,), act, self.close,
                                 context=self.drafts.context(), mode='save')
        panel.prepare_save(entry)
        self.attach(panel)
        lease = panel.view_lease

    def show_restore(self) -> None:
        if not self._available():
            self.close()
            self.drafts.notice('Parent recovery blocked by modal owner; explicitly reload after it settles')
            return
        def act(action: str, entry: DraftEntry) -> None:
            if not self.alive(panel, lease):
                raise ValueError('Parent recovery view changed; reopen and review')
            def restore(draft: ChildDraft) -> None:
                self._restore(draft)  # Host checks its actual retention budget before text/image mutation.
                self.close()
                self.drafts.notice('Parent draft recovered; NOT sent · inspect before explicit submission')
            self.drafts.action(action, entry, restore)
        panel = ParentDraftPanel(self.drafts.entries, act, self.close,
                                 context=self.drafts.context(), mode='restore')
        self.attach(panel)
        lease = panel.view_lease

    def poll(self) -> None:
        panel = self.panel
        if panel is not None and (not panel.focused or panel.context != self.drafts.context()
                                  or not self._available()):
            self.close()
        self.drafts.poll(self.show_restore)
        panel = self.panel
        if panel is not None and panel.mode == 'loading' and not self.drafts.io.busy:
            self.close()  # Failed/revoked reads cannot leave a dead loading surface.
