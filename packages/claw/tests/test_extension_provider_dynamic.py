from __future__ import annotations

from xdog.claw.channels.tui.tui_app import ChatApp


def test_model_display_limits_ignore_foreign_model_and_invalid_or_legacy_metadata():
    ui = ChatApp('/tmp/unused-provider-dynamic.sock')
    try:
        ui._state['model'] = 'ext-providerdynamic-live/echo'
        ui._context_window = 16000
        ui._accept_extension_model_limits({'model': 'foreign/model', 'model_context_window': 48000})
        assert ui._context_window == 16000 and ui._state['model'] == 'ext-providerdynamic-live/echo'
        for value in (True, -1, 2**32, '48000', None):
            ui._accept_extension_model_limits({'model': ui._state['model'], 'model_context_window': value})
            assert ui._context_window == 16000
        ui._accept_extension_model_limits({'model': ui._state['model']})  # Older sender: no guessed limits.
        assert ui._context_window == 16000
    finally:
        ui._request_exit()


def test_model_context_and_usage_remain_visible_at_default_width_with_long_aliases():
    from xdog.tui.utils import strip_ansi, visible_width

    ui = ChatApp('/tmp/unused-provider-dynamic.sock')
    try:
        ui._state.update(model='ext-' + 'provider' * 8 + '/echo', session_id='long-session-identity',
                         group_id='long-group-identity', connection_status='connected', activity_status='idle')
        ui._context_window = 16000
        ui._render_status()
        rows = ui._footer.render(80)
        assert len(rows) == 1 and visible_width(rows[0]) <= 80
        assert '16k' in strip_ansi(rows[0]) and '↑0' in strip_ansi(rows[0]) and '↓0' in strip_ansi(rows[0])
    finally:
        ui._request_exit()
