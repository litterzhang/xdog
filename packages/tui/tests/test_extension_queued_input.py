"""Extensions cannot register a queue-input review or smuggle one into a basic dialog."""
import importlib.util

from xdog.tui.components.extension_stack import ExtensionDialogStack


def test_extension_queue_renderer_is_removed_even_for_binary_free_or_child_proposals():
    assert importlib.util.find_spec('xdog.tui.components.extension_queued_input') is None
    for extra in ({}, {'images': []}, {'child_id': 'a' * 12, 'child_owner': 'b' * 32}):
        assert not ExtensionDialogStack.accepts({'id': 'stale', 'kind': 'session', 'operation': 'queue_input',
                                                **extra}, [])
        assert not ExtensionDialogStack.accepts({'id': 'stale', 'kind': 'confirm', 'operation': 'queue_input',
                                                **extra}, [])
