"""A read-only session catalog is not a deferred native-browser capability."""
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode


def test_deferred_extension_browser_controls_are_absent(coding_session):
    ui = InteractiveMode(coding_session)
    try:
        ui._editor.set_text('draft')
        assert not hasattr(ui, '_extension_browser') and not hasattr(ui, '_finish_extension_browser')
        assert ui._navigation_panel is None and ui._editor.get_text() == 'draft'
        assert not coding_session.agent.is_streaming
    finally:
        ui._request_exit()
