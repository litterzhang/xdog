from __future__ import annotations

import pytest
from xdog.tui.components.oauth_panel import OAuthPanel
from xdog.tui.keys import KeyEvent, KeyEventType

REVIEW = {"review_id": "a" * 32, "issuer": "https://issuer.test/tenant", "resource": "https://resource.test/mcp",
          "client_id": "registered-client", "scopes": ["tools:read", "tools:inspect"],
          "authorization_endpoint": "https://issuer.test/authorize", "token_endpoint": "https://issuer.test/token"}


def test_consent_requires_all_fields_painted_and_resize_paste_repeat_cannot_confirm():
    actions = []
    panel = OAuthPanel(REVIEW, lambda *args: actions.append(args), lambda: None)
    panel.focused = True
    panel.handle_input(KeyEvent("enter"))
    assert not actions
    panel.set_render_budget(5)
    for _ in range(30):
        panel.render(80)
        if panel._visible:
            break
        panel.handle_input(KeyEvent("pagedown"))
    assert panel._visible
    panel.handle_paste("\n")
    panel.handle_input(KeyEvent("enter", event_type=KeyEventType.REPEAT))
    assert not actions
    panel.set_render_budget(3)
    panel.render(32)
    panel.handle_input(KeyEvent("enter"))
    assert not actions
    panel.focused = True
    panel.set_render_budget(8)
    panel.render(80)
    assert not panel._visible
    panel.handle_input(KeyEvent("home"))
    for _ in range(30):
        panel.render(80)
        if panel._visible:
            break
        panel.handle_input(KeyEvent("pagedown"))
    panel.handle_input(KeyEvent("enter"))
    panel.handle_input(KeyEvent("enter"))
    assert actions == [("auth_start", "a" * 32)]


def test_failed_owner_does_not_keep_copyable_old_authorization_uri():
    copied = []
    panel = OAuthPanel(REVIEW, lambda *args: None, lambda: None, copied.append)
    panel.publish("auth_start", {"authorization_url": "https://issuer.test/authorize?state=old"})
    panel.publish("auth_status", {}, "Session changed; prepare a fresh review")
    assert panel.phase == "failed" and not panel.url
    panel.render(80)
    panel.handle_input(KeyEvent("y"))
    assert not copied


def test_cancel_preempts_parent_status_poll_instead_of_being_dropped():
    from xdog.tui.components.mcp_panel import McpPanel
    sent = []
    parent = McpPanel(lambda *args: sent.append(args), lambda: None, lambda: None, lambda: None)
    parent.publish("list", rows=({"server": "peer", "state": "disconnected"},))
    parent._server = "peer"
    parent.publish_auth("auth_prepare", REVIEW)
    parent.publish_auth("auth_start", {"authorization_url": "https://issuer.test/authorize"})
    parent.auth.poll()
    assert parent._busy and sent[-1][0] == "auth_status"
    parent.handle_input(KeyEvent("c"))
    assert sent[-1][0] == "auth_cancel"


def test_refresh_consent_shows_paging_and_confirmation_then_cancel_while_busy():
    actions = []
    panel = OAuthPanel({**REVIEW, "operation": "refresh"}, lambda *args: actions.append(args), lambda: None)
    panel.focused = True
    panel.set_render_budget(8)
    rows = panel.render(80)
    assert "PgUp/PgDn review ALL fields" in rows[-1]
    panel.handle_input(KeyEvent("enter"))
    assert not actions
    for _ in range(30):
        rows = panel.render(80)
        if panel._visible:
            break
        panel.handle_input(KeyEvent("pagedown"))
    assert panel._visible
    assert "Enter confirms reviewed endpoints/scopes" in rows[-1]
    panel.handle_input(KeyEvent("enter"))
    assert actions == [("auth_refresh", "a" * 32)]
    assert panel.phase == "refreshing"
    assert "c cancels pending refresh" in panel.render(80)[-1]
    panel.handle_input(KeyEvent("c"))
    assert actions[-1] == ("auth_cancel", "")


@pytest.mark.parametrize('options', [
    {'token_auth_method': 'client_secret_jwt', 'client_secret_env': 'XDOG_JWT_SECRET'},
    {'token_auth_method': 'private_key_jwt', 'client_secret_env': 'XDOG_PRIVATE_KEY'},
    {'registration': 'dynamic'}, {'registration': 'metadata'},
])
def test_retired_auth_combinations_refused_before_any_consent_or_action(options):
    actions = []
    with pytest.raises(ValueError, match='pre-registered public PKCE'):
        OAuthPanel({**REVIEW, **options}, lambda *args: actions.append(args), lambda: None)
    assert not actions


def test_host_disabled_auth_view_cannot_poll_launch_copy_or_reactivate_from_late_receipts():
    calls, closed = [], []
    panel = OAuthPanel({'review_id': 'a' * 32, 'issuer': 'issuer', 'resource': 'resource', 'client_id': 'client',
        'scopes': [], 'authorization_endpoint': 'authorize', 'token_endpoint': 'token',
        'token_auth_method': 'none'}, lambda *args: calls.append(args), lambda: closed.append(True))
    panel.phase = 'waiting'
    panel.url = 'private-pending-url'
    panel.disable('Support withdrawn; completed tokens not remotely revoked')
    assert panel.phase == 'canceled' and not panel.url and not panel.poll()
    for key in ('enter', 'r', 's', 'p', 'f', 'y', 'c'):
        panel.handle_input(KeyEvent(key))
    panel.publish('auth_start', {'authorization_url': 'late-private-url'})
    panel.publish('auth_status', {'state': 'authorized'})
    assert not calls and panel.phase == 'canceled' and not panel.url
    assert any('withdrawn' in line for line in panel.render(120))
    panel.handle_input(KeyEvent('escape'))
    assert closed == [True]



def test_unfocused_oauth_cannot_accumulate_consent_and_blur_requires_fresh_whole_review():
    actions = []
    panel = OAuthPanel(REVIEW, lambda *args: actions.append(args), lambda: None)
    for _ in range(30):
        panel.render(80)
        panel.handle_input(KeyEvent('pagedown'))
    assert not panel._seen and not panel._visible
    panel.focused = True
    for _ in range(30):
        panel.render(80)
        if panel._visible:
            break
        panel.handle_input(KeyEvent('pagedown'))
    assert panel._visible
    panel.focused = False
    panel.render(80)
    panel.handle_input(KeyEvent('enter'))
    assert not actions and not panel._seen
    panel.focused = True
    panel.render(80)
    assert not panel._visible
    for _ in range(30):
        panel.render(80)
        if panel._visible:
            break
        panel.handle_input(KeyEvent('pagedown'))
    panel.handle_input(KeyEvent('enter'))
    assert actions == [('auth_start', REVIEW['review_id'])]
