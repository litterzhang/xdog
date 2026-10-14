from __future__ import annotations

import asyncio
import copy

import pytest
from xdog.agent.extensions.dialogs import DialogBroker, validate_answer, validate_dialog

FIELDS = [
    {"name": "name", "kind": "text", "required": True, "min_length": 1},
    {"name": "count", "kind": "integer", "default": 0, "minimum": 0, "maximum": 10},
    {"name": "enabled", "kind": "boolean", "default": False},
    {
        "name": "choice",
        "kind": "choice",
        "choices": [
            {"label": "False", "value": False},
            {"label": "Zero", "value": 0},
            {"label": "Null", "value": None},
        ],
    },
]


def dialog(fields=None):
    return validate_dialog({"kind": "form", "id": "worker-id", "title": "Typed form", "fields": fields or FIELDS})


def test_typed_answer_defaults_snapshot_and_cancellation_are_independent_of_tool_approval():
    fields = copy.deepcopy(FIELDS)
    value = dialog(fields)
    fields[0]["name"] = "changed"
    assert value["fields"][0]["name"] == "name"
    answer = {"name": "Alice", "count": 0, "enabled": False, "choice": None}
    assert validate_answer(value, answer) == answer
    answer["name"] = "Changed"
    assert validate_answer(value, {"name": "Alice", "choice": False})["choice"] is False
    assert validate_answer(value, {"name": "Alice", "choice": 0})["choice"] == 0
    assert validate_answer(value, None) is None  # Does not silently submit offered defaults.


def test_invalid_answers_rejected_without_coercion() -> None:

    def _check_case(answer):
        with pytest.raises(ValueError):
            validate_answer(dialog(), answer)

    for answer in [
        True,
        [],
        {},
        {"name": "x", "extra": "unknown"},
        {"name": ""},
        {"name": "x", "count": False},
        {"name": "x", "count": "1"},
        {"name": "x", "count": 1.0},
        {"name": "x", "count": -1},
        {"name": "x", "enabled": 1},
        {"name": "x", "choice": "0"},
        {"name": "x", "choice": 0.0},
    ]:
        _check_case(answer)


@pytest.mark.parametrize(
    "fields",
    [
        [],
        [{"name": "bad-name", "kind": "text"}],
        [{"name": "x", "kind": "text"}, {"name": "x", "kind": "number"}],
        [{"name": "x", "kind": "unknown"}],
        [{"name": "x", "kind": "integer", "default": True}],
        [{"name": "x", "kind": "number", "default": float("nan")}],
        [{"name": "x", "kind": "number", "minimum": 2, "maximum": 1}],
        [{"name": "x", "kind": "integer", "multiline": True}],
        [{"name": "x", "kind": "text", "min_length": 2, "max_length": 1}],
        [{"name": "x", "kind": "text", "pattern": "unsupported"}],
        [{"name": "x", "kind": "choice", "choices": [{"label": "A", "value": 0}, {"label": "B", "value": 0}]}],
    ],
)
def test_invalid_or_unsupported_field_declarations_rejected(fields):
    with pytest.raises(ValueError):
        validate_dialog({"kind": "form", "id": "worker", "title": "Title", "fields": fields})


async def test_broker_invalid_form_answers_do_not_resolve_and_close_returns_none():
    broker = DialogBroker()
    events = []
    broker.publish = events.append
    task = asyncio.create_task(broker.ask("forms", dialog()))
    await asyncio.sleep(0)
    key = events[0]["dialog"]["id"]
    with pytest.raises(ValueError):
        broker.resolve(key, {"name": "x", "count": False})
    assert not task.done() and broker.contains(key)
    broker.close()
    assert await task is None and not broker.contains(key)


@pytest.mark.parametrize('selected', [[False, 0, None], [], ['x', 1.0], [0, False]])
def test_multichoice_named_arrays_preserve_typed_values_empty_and_order(selected):
    from xdog.agent.extensions.forms import validate_fields, validate_form_answer
    fields = validate_fields([{'name': 'many', 'kind': 'multi_choice', 'choices': [
        {'label': str(index), 'value': value} for index, value in enumerate([False, 0, None, 'x', 1.0])]}])
    assert validate_form_answer({'fields': fields}, {'many': selected}) == {'many': selected}
    assert validate_form_answer({'fields': fields}, {}) == {}


def test_multichoice_unoffered_duplicate_or_out_of_range_arrays_fail_without_coercion() -> None:

    def _check_case(selected):
        from xdog.agent.extensions.forms import validate_fields, validate_form_answer

        fields = validate_fields(
            [
                {
                    "name": "many",
                    "kind": "multi_choice",
                    "min_items": 1,
                    "max_items": 2,
                    "choices": [
                        {"label": "false", "value": False},
                        {"label": "zero", "value": 0},
                        {"label": "null", "value": None},
                    ],
                }
            ]
        )
        with pytest.raises(ValueError):
            validate_form_answer({"fields": fields}, {"many": selected})

    for selected in [[False, False], [0, 0], [1], ["unknown"], [False, 0, None], "false", None]:
        _check_case(selected)


def test_multichoice_bad_constraints_defaults_and_scalar_count_constraints_are_rejected() -> None:

    def _check_case(updates):
        from xdog.agent.extensions.forms import validate_fields

        row = {"name": "many", "kind": "multi_choice", "choices": [{"label": "zero", "value": 0}]}
        with pytest.raises(ValueError):
            validate_fields([{**row, **updates}])
        with pytest.raises(ValueError):
            validate_fields([{"name": "text", "kind": "text", "max_items": 1}])

    for updates in [
        {"min_items": True},
        {"max_items": True},
        {"min_items": -1},
        {"max_items": 4},
        {"min_items": 2, "max_items": 1},
        {"default": [0, 0]},
    ]:
        _check_case(updates)
