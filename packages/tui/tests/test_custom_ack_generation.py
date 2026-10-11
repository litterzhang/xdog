"""A late rejected viewport ACK settles old work, never revokes a newer paint/request."""
from __future__ import annotations

import pytest
from xdog.tui.components.extension_custom import ExtensionCustom
from xdog.tui.keys import KeyEvent


def panel():
    requests = []
    view = ExtensionCustom('nested', {'id': 'parent', 'kind': 'custom', 'title': 'Nested parent'},
                           lambda _: None, lambda value: requests.append(dict(value)) or True)
    view.focused = True
    view.set_render_budget(30)
    view.render(80)
    assert view.poll()
    return view, requests


def paint(view, generation, revision=3):
    assert view.accept({'type': 'extension_custom_frame', 'id': 'parent', 'generation': generation,
        'width': 80, 'height': 27, 'revision': revision, 'lines': ['after nested review'], 'cursor': None})
    view.render(80)
    assert view._painted == revision


@pytest.mark.parametrize('already_painted', [False, True])
def test_old_refusal_cannot_duplicate_new_viewport_or_revoke_its_fresh_paint(already_painted):
    view, requests = panel()
    old = requests[-1]
    # Original nested parent resumes and queues its new viewport before an earlier
    # suspended-parent refusal arrives. This is the observed socket ordering.
    view.focused = False
    view.focused = True
    view.render(80)
    assert view.poll()
    fresh, requested = requests[-1], view._requested
    assert fresh['generation'] > old['generation'] and view._inflight == 2
    if already_painted:
        paint(view, fresh['generation'])
    expected_paint = view._painted
    assert view.accept({'type': 'extension_custom_ack', 'id': 'parent', 'accepted': False,
                        'generation': old['generation']})
    assert view._inflight == (0 if already_painted else 1)
    assert view._requested == requested and view._painted == expected_paint
    assert not view.poll() and len(requests) == 2  # No duplicate same-generation resize ahead of Enter.
    if not already_painted:
        paint(view, fresh['generation'])
    view.handle_input(KeyEvent('enter'))
    assert requests[-1]['kind'] == 'key' and requests[-1]['painted'] == 3
    assert requests[-1]['generation'] == fresh['generation']


@pytest.mark.parametrize('legacy', [False, True])
def test_current_or_generationless_refusal_still_revokes_and_requires_fresh_review(legacy):
    view, requests = panel()
    generation = requests[-1]['generation']
    paint(view, generation)
    view.handle_input(KeyEvent('n'))
    assert view._inflight == 1
    value = {'type': 'extension_custom_ack', 'id': 'parent', 'accepted': False}
    if not legacy:
        value['generation'] = generation
    assert view.accept(value)
    assert view._inflight == 0 and view._requested is None and view._painted == -1
    assert view._error == 'Host refused custom input; review or cancel'
    before = len(requests)
    view.handle_input(KeyEvent('enter'))
    assert len(requests) == before  # No automatic replay/completion while proof is invalid.
    assert view.poll() and requests[-1]['kind'] == 'resize' and requests[-1]['painted'] == -1
    paint(view, generation, 4)
    view.handle_input(KeyEvent('enter'))
    assert requests[-1]['painted'] == 4


@pytest.mark.parametrize('generation', [None, True, False, '1', 1.0, -1, 2**53, 999])
def test_malformed_or_future_ack_owns_no_counter_or_current_paint(generation):
    view, requests = panel()
    current = requests[-1]['generation']
    paint(view, current)
    view.handle_input(KeyEvent('n'))
    previous = view._inflight, view._requested, view._painted, view._error
    assert not view.accept({'type': 'extension_custom_ack', 'id': 'parent', 'accepted': False,
                            'generation': generation})
    assert (view._inflight, view._requested, view._painted, view._error) == previous


def test_positive_ack_does_not_retire_frame_work_or_fabricate_paint():
    view, requests = panel()
    assert view.accept({'type': 'extension_custom_ack', 'id': 'parent', 'accepted': True,
                        'generation': requests[-1]['generation']})
    assert view._inflight == 1 and view._painted == -1
    paint(view, requests[-1]['generation'])
    assert view._inflight == 0
