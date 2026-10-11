from __future__ import annotations

from xdog.agent.extensions.dialogs import validate_dialog
from xdog.tui.components.extension_dialog import ExtensionDialog
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.utils import visible_width

FIELDS = [
    {"name": "name", "kind": "text", "label": "Name", "required": True, "min_length": 1},
    {"name": "count", "kind": "integer", "label": "Count", "minimum": 0, "maximum": 10, "default": 0},
    {"name": "ratio", "kind": "number", "label": "Ratio", "default": 0.5},
    {"name": "enabled", "kind": "boolean", "label": "Enabled", "default": False},
    {
        "name": "mode",
        "kind": "choice",
        "label": "Mode",
        "choices": [
            {"label": "Boolean false", "value": False},
            {"label": "Integer zero", "value": 0},
            {"label": "Explicit null", "value": None},
        ],
    },
    {"name": "notes", "kind": "text", "label": "Notes", "multiline": True},
]


def panel(fields=None):
    answers = []
    dialog = validate_dialog(
        {
            "id": "form",
            "kind": "form",
            "title": "Typed details",
            "message": "Not tool approval",
            "fields": fields or FIELDS,
        }
    )
    result = ExtensionDialog("test", dialog, answers.append)
    result.focused = True
    return result, answers


def review_all(wrapper):
    for _ in range(100):
        rows = wrapper.render(80)
        if wrapper._form._visible:
            assert "Enter submits reviewed form" in rows[-1]
            return
        wrapper.handle_input(KeyEvent("pagedown"))
    raise AssertionError("Form fields did not complete visible review")


def test_typed_fields_multiline_navigation_explicit_review_and_submission():
    wrapper, answers = panel()
    form = wrapper._form
    wrapper.handle_input(KeyEvent("enter"))
    assert form._phase == "edit" and "Required" in form._error and not answers
    wrapper.handle_paste("Alice")
    wrapper.handle_input(KeyEvent("tab"))
    form._input.set_text("3")
    wrapper.handle_input(KeyEvent("tab"))
    form._input.set_text("1.25")
    wrapper.handle_input(KeyEvent("tab"))
    wrapper.handle_input(KeyEvent("space"))  # Explicitly changes False to True.
    wrapper.handle_input(KeyEvent("tab"))
    wrapper.handle_input(KeyEvent("right"))  # First offered typed false, not zero.
    wrapper.handle_input(KeyEvent("tab"))
    wrapper.handle_paste("line one")
    wrapper.handle_input(KeyEvent("enter"))
    wrapper.handle_paste("line two")
    assert form._input.get_text() == "line one\nline two"
    wrapper.handle_input(KeyEvent("enter", ctrl=True))
    assert form._phase == "review" and not answers
    wrapper.handle_input(KeyEvent("enter"))
    assert not answers
    review_all(wrapper)
    wrapper.handle_input(KeyEvent("enter", event_type=KeyEventType.REPEAT))
    wrapper.handle_paste("\n")
    assert not answers
    wrapper.handle_input(KeyEvent("enter"))
    assert answers == [
        {"name": "Alice", "count": 3, "ratio": 1.25, "enabled": True, "mode": False, "notes": "line one\nline two"}
    ]
    wrapper.handle_input(KeyEvent("escape"))
    assert len(answers) == 1


def test_false_zero_explicit_null_and_unset_optional_are_not_coerced_or_defaulted():
    wrapper, answers = panel(
        [
            {"name": "enabled", "kind": "boolean", "default": False, "required": True},
            {"name": "zero", "kind": "integer", "default": 0},
            {"name": "empty", "kind": "text", "default": ""},
            {
                "name": "nullable",
                "kind": "choice",
                "default": None,
                "choices": [{"label": "Null", "value": None}, {"label": "False", "value": False}],
            },
            {"name": "optional", "kind": "text"},
        ]
    )
    wrapper.handle_input(KeyEvent("enter"))
    review_all(wrapper)
    wrapper.handle_input(KeyEvent("enter"))
    assert answers == [{"enabled": False, "zero": 0, "empty": "", "nullable": None}]


def test_invalid_numeric_draft_retained_review_resize_zero_budget_and_edit_invalidate_confirmation():
    wrapper, answers = panel([{"name": "count", "kind": "integer", "required": True, "maximum": 5}])
    form = wrapper._form
    for invalid in ("3.5", "nan", "6", "9007199254740992"):
        form._input.set_text(invalid)
        wrapper.handle_input(KeyEvent("enter"))
        assert form._phase == "edit" and form._input.get_text() == invalid and not answers
    form._input.set_text("0")
    wrapper.handle_input(KeyEvent("enter"))
    review_all(wrapper)
    wrapper.set_render_budget(0)
    assert wrapper.render(80) == []
    wrapper.handle_input(KeyEvent("enter"))
    assert not answers
    wrapper.set_render_budget(8)
    wrapper.render(16)
    wrapper.handle_input(KeyEvent("enter"))
    assert not answers  # Changed wrap needs full review.
    review_all(wrapper)
    wrapper.handle_input(KeyEvent("e", ctrl=True))
    assert form._phase == "edit"
    wrapper.handle_paste("bad")
    wrapper.handle_input(KeyEvent("enter"))
    assert form._phase == "edit" and not answers
    wrapper.handle_input(KeyEvent("escape"))
    assert answers == [None]


def test_form_row_bounds_and_untrusted_controls_do_not_escape_transient_rows():
    wrapper, answers = panel(
        [
            {
                "name": "x",
                "kind": "choice",
                "default": "raw",
                "label": "label\x1b[2J\nline",
                "choices": [{"label": "raw\x1b]52;secret\a\nlabel", "value": "raw"}],
            }
        ]
    )
    for phase in ("edit", "review"):
        wrapper._form._phase = phase
        for budget in (0, 1, 2, 4, 8, 12):
            wrapper.set_render_budget(budget)
            for width in (0, 1, 24, 80):
                rows = wrapper.render(width)
                assert len(rows) <= budget and all(visible_width(row) <= width and "\n" not in row for row in rows)
                assert "\x1b[2J" not in "\n".join(rows) and "\x1b]52" not in "\n".join(rows)
    assert not answers


def test_multi_choice_keyboard_typed_order_empty_unset_bounds_and_focus_owned_review():
    fields = [{'name': 'many', 'kind': 'multi_choice', 'required': True, 'min_items': 1, 'max_items': 2,
        'choices': [{'label': 'False', 'value': False}, {'label': 'Zero', 'value': 0},
                    {'label': 'Null', 'value': None}]}]
    wrapper, answers = panel(fields)
    form = wrapper._form
    wrapper.handle_input(KeyEvent('space'))
    wrapper.handle_input(KeyEvent('down'))
    wrapper.handle_input(KeyEvent('space'))
    assert form.values['many'] == [False, 0] and form._choice == 1
    wrapper.handle_input(KeyEvent('down'))
    wrapper.handle_input(KeyEvent('space'))
    assert form.values['many'] == [False, 0] and 'Maximum' in form._error
    wrapper.handle_input(KeyEvent('delete'))
    assert form.values['many'] == []
    wrapper.handle_input(KeyEvent('enter'))
    assert form._phase == 'edit' and 'Invalid' in form._error
    wrapper.handle_input(KeyEvent('backspace'))
    assert 'many' not in form.values
    wrapper.handle_input(KeyEvent('enter'))
    assert 'Required' in form._error
    wrapper.handle_input(KeyEvent('space'))  # Focus resets to first choice on failed review.
    wrapper.handle_input(KeyEvent('down'))
    wrapper.handle_input(KeyEvent('space'))
    wrapper.handle_input(KeyEvent('enter'))
    review_all(wrapper)
    wrapper.focused = False
    for _ in range(30):
        wrapper.render(80)
        wrapper.handle_input(KeyEvent('pagedown'))
    assert not form._visible and not form._seen
    wrapper.focused = True
    wrapper.handle_input(KeyEvent('enter'))
    assert not answers
    review_all(wrapper)
    wrapper.handle_input(KeyEvent('enter'))
    assert answers == [{'many': [False, 0]}]


def test_optional_multichoice_empty_is_distinct_from_omitted_and_paste_never_selects_or_submits():
    fields = [{'name': 'many', 'kind': 'multi_choice', 'choices': [{'label': 'Null', 'value': None}]}]
    wrapper, answers = panel(fields)
    wrapper.handle_paste('[null]\n')
    assert 'many' not in wrapper._form.values
    wrapper.handle_input(KeyEvent('delete'))
    wrapper.handle_input(KeyEvent('enter'))
    review_all(wrapper)
    wrapper.handle_input(KeyEvent('enter'))
    assert answers == [{'many': []}]


def test_actual_printable_space_toggles_existing_boolean_and_single_choice_fields():
    from xdog.tui.keys import parse_key_events
    wrapper, _answers = panel([{'name': 'enabled', 'kind': 'boolean', 'default': False},
        {'name': 'mode', 'kind': 'choice', 'choices': [{'label': 'Zero', 'value': 0}]}])
    space = parse_key_events(b' ')[0]
    wrapper.handle_input(space)
    assert wrapper._form.values['enabled'] is True
    wrapper.handle_input(KeyEvent('tab'))
    wrapper.handle_input(space)
    assert type(wrapper._form.values['mode']) is int and wrapper._form.values['mode'] == 0
