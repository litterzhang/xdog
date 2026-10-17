"""Shared view policy stays independent of host approval/focus/retention adapters."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from xdog.tui.child_drafts import ChildDraft
from xdog.tui.components.child_draft_panel import DraftEntry
from xdog.tui.components.parent_draft_panel import ParentDraftPanel
from xdog.tui.parent_draft_presenter import ParentDraftPresenter
from xdog.tui.parent_drafts import ParentDrafts


def make(tmp_path, *, restore=None):
    context, available, notices, events = ['original'], [True], [], []
    current = ChildDraft('UNSENT', config={'access': 'not authority'}, cursor=2)
    drafts = ParentDrafts(lambda: tmp_path / 'drafts', lambda: current, lambda: context[0], notices.append)
    def mount(panel):
        panel.focused = True
        events.append(('mount', panel))
    presenter = ParentDraftPresenter(drafts, available=lambda: available[0],
        prepare=lambda: events.append(('prepare', None)), mount=mount,
        unmount=lambda panel: events.append(('unmount', panel)),
        restore=restore or (lambda value: events.append(('restore', value))),
        changed=lambda: events.append(('changed', None)))
    return presenter, drafts, context, available, notices, events


def panel(context='original', mode='restore'):
    return ParentDraftPanel(lambda: (), lambda *_: None, lambda: None, context=context, mode=mode)


def test_replacing_loading_view_preserves_adopted_records_but_retires_old_actor(tmp_path):
    presenter, drafts, _, _, _, events = make(tmp_path)
    loading, recovery = panel(mode='loading'), panel()
    presenter.attach(loading)
    old_lease = loading.view_lease
    saved = object()
    drafts.saved['source'] = saved
    presenter.attach(recovery)
    assert drafts.saved == {'source': saved} and drafts._read_alive()
    assert not loading.focused and not presenter.alive(loading, old_lease)
    assert presenter.panel is recovery and events[-2:] == [('mount', recovery), ('changed', None)]
    presenter.close()
    assert not drafts.saved and not drafts._read_alive() and presenter.panel is None
    assert events[-2:] == [('unmount', recovery), ('changed', None)]


def test_context_change_poll_closes_and_cannot_resurrect_old_view(tmp_path):
    presenter, _, context, _, _, _ = make(tmp_path)
    active = panel()
    presenter.attach(active)
    lease = active.view_lease
    assert presenter.alive(active, lease)
    context[0] = 'successor'
    presenter.poll()
    context[0] = 'original'
    assert presenter.panel is None and not presenter.alive(active, lease)


@pytest.mark.parametrize('blocked', ['approval-or-modal', 'disk'])
def test_begin_does_not_dismiss_current_view_or_prepare_host_when_blocked(tmp_path, blocked):
    presenter, drafts, _, available, _, events = make(tmp_path)
    active = panel()
    presenter.attach(active)
    before = list(events)
    if blocked == 'disk':
        drafts.io = SimpleNamespace(busy=True)
    else:
        available[0] = False
    with pytest.raises(ValueError):
        presenter.begin()
    assert presenter.panel is active and events == before


@pytest.mark.parametrize('retire', ['focus', 'context', 'modal', 'successor', 'close'])
def test_old_save_actor_never_admits_work_after_owner_or_view_changes(tmp_path, retire):
    presenter, drafts, context, available, _, _ = make(tmp_path)
    presenter.show_save()
    active = presenter.panel
    entry, = active.source()
    actor = active.act
    if retire == 'focus':
        active.focused = False
    elif retire == 'context':
        context[0] = 'other'
    elif retire == 'modal':
        available[0] = False
    elif retire == 'successor':
        presenter.attach(panel())
    else:
        presenter.close()
    with pytest.raises(ValueError, match='view changed'):
        actor('save', entry)
    assert not drafts.io.busy and drafts.snapshot().text == 'UNSENT'


def test_host_retention_refusal_leaves_restore_view_and_composer_intact(tmp_path, monkeypatch):
    def refuse(draft):
        raise ValueError('host image retention exceeded')
    presenter, drafts, _, _, notices, _ = make(tmp_path, restore=refuse)
    # Exercise the presentation port independently; native tests cover service receipt validation.
    monkeypatch.setattr(drafts, 'action', lambda action, entry, restore: restore(entry.value))
    presenter.show_restore()
    active = presenter.panel
    source = ChildDraft('RECOVERED', config={'access': 'allow-all'}, cursor=999)
    entry = DraftEntry('original', ('parent', ''), source, source, 'source', True)
    with pytest.raises(ValueError, match='retention'):
        active.act('restore', entry)
    assert presenter.panel is active and drafts.snapshot().text == 'UNSENT'
    assert not any('NOT sent' in notice for notice in notices) and not drafts.io.busy


def test_presenter_import_does_not_depend_on_agent_or_applications():
    import subprocess
    import sys
    code = '''
import importlib.abc, sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.startswith(('xdog.agent','xdog.ai','xdog.coding','xdog.claw')):
            raise AssertionError('TUI presenter acquired application dependency: '+name)
sys.meta_path.insert(0,Guard())
from xdog.tui.parent_draft_presenter import ParentDraftPresenter
assert callable(ParentDraftPresenter)
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
