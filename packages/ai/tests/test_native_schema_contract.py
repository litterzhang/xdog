import pytest
from xdog.ai.utils.validation import validate_tool_arguments, validate_tool_schema


@pytest.mark.parametrize('bad', [{'type': 'made-up'}, {'type': 'object', 'required': 'wrong'},
                                 {'$ref': 'https://never-fetch.invalid/schema'}, {'default': float('nan')}])
def test_invalid_or_remote_native_schema_is_rejected_without_fetch(bad):
    with pytest.raises(ValueError):
        validate_tool_schema(bad)


def test_nested_union_local_refs_and_extra_keys_are_enforced_without_echoing_values():
    schema = {'type': 'object', 'properties': {'items': {'type': 'array', 'items': {'$ref': '#/$defs/item'}}},
              'required': ['items'], 'additionalProperties': False,
              '$defs': {'item': {'type': ['integer', 'null'], 'minimum': 1}}}
    assert validate_tool_arguments({'items': [1, None]}, schema).valid
    for value in ({'items': [True]}, {'items': [0]}, {'items': [1], 'extra': 'private-secret'}):
        result = validate_tool_arguments(value, schema)
        assert not result.valid and 'private-secret' not in str(result.errors)


def test_literal_example_references_are_data_but_dynamic_remote_refs_are_not():
    from xdog.ai.utils.validation import validate_tool_schema

    validate_tool_schema({'type': 'object', 'default': {'$ref': 'literal-data'},
                          'examples': [{'$dynamicRef': 'https://literal.invalid'}]})
    with pytest.raises(ValueError):
        validate_tool_schema({'$dynamicRef': 'https://must-not-fetch.invalid/schema'})
