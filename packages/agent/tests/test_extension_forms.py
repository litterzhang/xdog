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


@pytest.mark.parametrize(
    "answer",
    [
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
    ],
)
def test_invalid_answers_rejected_without_coercion(answer):
    with pytest.raises(ValueError):
        validate_answer(dialog(), answer)


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


async def test_real_extension_form_suspension_typed_answer_headless_and_deadline(tmp_path):
    import shutil
    from pathlib import Path

    from xdog.agent.extensions.runtime import ExtensionRuntime
    source = Path(__file__).parent / 'fixtures/extensions'
    root = tmp_path / 'extensions'
    shutil.copytree(source, root)
    runtime = ExtensionRuntime(['dialogue'], root, tmp_path)
    seen = []
    async def answer(extension, form):
        assert extension == 'dialogue' and form['kind'] == 'form'
        seen.append(form)
        return {'name': 'Alice', 'count': 0, 'enabled': False, 'mode': None, 'notes': 'multi\nline'}
    runtime.dialog_handler = answer
    try:
        text = await runtime.command('ext-dialogue-form', '')
        assert '"count": 0' in text and '"enabled": false' in text and '"mode": null' in text
        assert len(seen) == 1
        runtime.dialog_handler = None
        assert await runtime.command('ext-dialogue-form', '') == 'FORM-RESULT-null'
        broker = DialogBroker()
        runtime.dialog_handler = broker.ask
        assert await runtime.command('ext-dialogue-form', 'timeout') == 'FORM-RESULT-null'
        broker.close()
        assert runtime.snapshot()['extensions'][0]['state'] == 'active'
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('selected', [[False, 0, None], [], ['x', 1.0], [0, False]])
def test_multichoice_named_arrays_preserve_typed_values_empty_and_order(selected):
    from xdog.agent.extensions.forms import validate_fields, validate_form_answer
    fields = validate_fields([{'name': 'many', 'kind': 'multi_choice', 'choices': [
        {'label': str(index), 'value': value} for index, value in enumerate([False, 0, None, 'x', 1.0])]}])
    assert validate_form_answer({'fields': fields}, {'many': selected}) == {'many': selected}
    assert validate_form_answer({'fields': fields}, {}) == {}


@pytest.mark.parametrize('selected', [[False, False], [0, 0], [1], ['unknown'], [False, 0, None], 'false', None])
def test_multichoice_unoffered_duplicate_or_out_of_range_arrays_fail_without_coercion(selected):
    from xdog.agent.extensions.forms import validate_fields, validate_form_answer
    fields = validate_fields([{'name': 'many', 'kind': 'multi_choice', 'min_items': 1, 'max_items': 2, 'choices': [
        {'label': 'false', 'value': False}, {'label': 'zero', 'value': 0}, {'label': 'null', 'value': None}]}])
    with pytest.raises(ValueError):
        validate_form_answer({'fields': fields}, {'many': selected})


@pytest.mark.parametrize('updates', [{'min_items': True}, {'max_items': True}, {'min_items': -1},
    {'max_items': 4}, {'min_items': 2, 'max_items': 1}, {'default': [0, 0]}])
def test_multichoice_bad_constraints_defaults_and_scalar_count_constraints_are_rejected(updates):
    from xdog.agent.extensions.forms import validate_fields
    row = {'name': 'many', 'kind': 'multi_choice', 'choices': [{'label': 'zero', 'value': 0}]}
    with pytest.raises(ValueError):
        validate_fields([{**row, **updates}])
    with pytest.raises(ValueError):
        validate_fields([{'name': 'text', 'kind': 'text', 'max_items': 1}])


async def test_real_multiselect_worker_receives_typed_array_or_none_never_implicit_defaults(tmp_path):
    import shutil
    from pathlib import Path

    from xdog.agent.extensions.runtime import ExtensionRuntime
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    runtime = ExtensionRuntime(['dialogue'], root, tmp_path)
    requests = []
    async def answer(extension, request):
        requests.append(request)
        return {'values': [False, 0, None], 'empty': []}
    try:
        runtime.dialog_handler = answer
        result = await runtime.command('ext-dialogue-many', '')
        assert result == 'MANY-RESULT-{"empty": [], "values": [false, 0, null]}'
        assert requests[0]['fields'][0]['kind'] == 'multi_choice'
        runtime.dialog_handler = None
        assert await runtime.command('ext-dialogue-many', '') == 'MANY-RESULT-null'
    finally:
        await runtime.aclose()
