"""No plugin write proposal renderer or CAS lease adapter survives UI032."""
import importlib.util

from xdog.tui.components.extension_stack import ExtensionDialogStack


def test_editor_write_protocol_components_are_absent():
    assert importlib.util.find_spec('xdog.tui.editor_write') is None
    assert importlib.util.find_spec('xdog.tui.components.extension_editor_write') is None
    for operation in ('read', 'replace', 'propose'):
        assert not ExtensionDialogStack.accepts({'id': 'stale', 'kind': 'editor', 'operation': operation}, [])
