"""No custom-frame ACK component can claim a native dialog's input state."""
import importlib.util

from xdog.tui.components.extension_dialog import ExtensionDialog


def test_native_dialog_has_no_remote_frame_or_ack_acceptance_api():
    panel = ExtensionDialog('test', {'kind': 'confirm', 'title': 'Question'}, lambda value: None)
    assert not hasattr(panel, 'accept') and not hasattr(panel, 'accept_control')
    assert importlib.util.find_spec('xdog.tui.components.extension_custom') is None
