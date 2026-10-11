from __future__ import annotations

from xdog.tui.components.extension_stack import ExtensionDialogStack
from xdog.tui.components.text import Text


def test_nested_leaf_preempts_parent_without_reordering_other_roots_and_cache_is_preserved():
    stack = ExtensionDialogStack()
    root = {'id': 'root', 'kind': 'custom', 'extension': 'same'}
    other = {'id': 'other', 'kind': 'input', 'extension': 'other'}
    child = {'id': 'child', 'kind': 'custom', 'extension': 'same', 'parent_id': 'root'}
    grandchild = {'id': 'grandchild', 'kind': 'input', 'extension': 'same', 'parent_id': 'child'}
    panel = Text('owned parent state')
    stack.panels['root'] = panel
    assert stack.choose([root, other, child, grandchild]) is grandchild
    assert stack.choose([root, other, child]) is child and stack.panels['root'] is panel
    assert stack.choose([root, other]) is root and stack.panels['root'] is panel
    assert stack.choose([other]) is other and not stack.panels


def test_foreign_or_missing_parent_cannot_borrow_a_session_child_or_extension():
    root = {'id': 'root', 'kind': 'custom', 'extension': 'same', 'session_id': 'owned',
            'child_id': 'child', 'child_owner': 'lease'}
    nested = {**root, 'id': 'nested', 'kind': 'input', 'parent_id': 'root'}
    assert ExtensionDialogStack.accepts(nested, [root])
    for key in ('parent_id', 'extension', 'session_id', 'child_id', 'child_owner'):
        assert not ExtensionDialogStack.accepts({**nested, key: 'foreign'}, [root])
    assert ExtensionDialogStack().choose([nested]) is None
    assert not ExtensionDialogStack.accepts({**nested, 'parent_id': 'nested'}, [root])
