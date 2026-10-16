"""Native basic dialogs keep FIFO/cache behavior without plugin parent nesting."""
from xdog.tui.components.extension_stack import ExtensionDialogStack
from xdog.tui.components.text import Text


def test_unrelated_basic_dialogs_stay_fifo_and_only_live_panels_are_cached():
    stack = ExtensionDialogStack()
    first = {'id': 'first', 'kind': 'input', 'extension': 'one'}
    second = {'id': 'second', 'kind': 'confirm', 'extension': 'two'}
    panel = Text('owned state')
    stack.panels['first'] = panel
    assert stack.choose([first, second]) is first and stack.panels['first'] is panel
    assert stack.choose([second]) is second and not stack.panels


def test_custom_or_parent_ids_cannot_borrow_session_child_or_extension_ownership():
    first = {'id': 'root', 'kind': 'input', 'extension': 'same', 'session_id': 'owned',
             'child_id': 'child', 'child_owner': 'lease'}
    for changes in ({'kind': 'custom'}, {'kind': 'form'}, {'parent_id': 'root'}, {'parent_id': 'foreign'}):
        rejected = {**first, **changes}
        assert not ExtensionDialogStack.accepts(rejected, [first])
        assert ExtensionDialogStack().choose([rejected]) is None


def test_malformed_or_smuggled_operations_are_refused_without_hashing_untrusted_values():
    stack = ExtensionDialogStack()
    for kind, operation in [('session', []), ('session', {}), ('session', None),
                            ([], 'snapshot'), ('input', None), ('confirm', 'rename')]:
        assert not stack.accepts({'id': 'old', 'kind': kind, 'operation': operation}, [])
