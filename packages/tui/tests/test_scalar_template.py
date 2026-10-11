from __future__ import annotations

import pytest
from xdog.tui.scalar_template import expand

VALUES = {"var": "value", "hello": "Hello World!", "path": "/foo/bar", "empty": "", "x": "1024", "y": "768"}


@pytest.mark.parametrize(
    "template,expected",
    [
        ("{var}", "value"),
        ("{hello}", "Hello%20World%21"),
        ("{+path}/here", "/foo/bar/here"),
        ("{+path:6}/here", "/foo/b/here"),
        ("{#hello}", "#Hello%20World!"),
        ("X{.var}", "X.value"),
        ("{/var,x}", "/value/1024"),
        ("{;x,y,empty}", ";x=1024;y=768;empty"),
        ("{?x,y,empty}", "?x=1024&y=768&empty="),
        ("{&x,y,empty}", "&x=1024&y=768&empty="),
        ("{var:3}", "val"),
        ("{var*}", "value"),
        ("{missing}", ""),
        ("{?missing}", ""),
        ("{empty}", ""),
        ("{#empty}", "#"),
        ("{/empty}", "/"),
    ],
)
def test_scalar_rfc6570_operators_prefix_explode_empty_and_undefined(template, expected):
    assert expand(template, VALUES) == expected


def test_unicode_prefix_percent_handling_dotted_and_encoded_variable_names_are_scalar():
    assert (
        expand("fixture://{name:2}{?other}", {"name": "你好🙂", "other": "a/b &c"})
        == "fixture://%E4%BD%A0%E5%A5%BD?other=a%2Fb%20%26c"
    )
    assert expand("{+x}", {"x": "/%2F/%oops"}) == "/%2F/%25oops"
    assert expand("{x}", {"x": "%2F"}) == "%252F"
    assert expand("{a.b,var%20name}", {"a.b": "first", "var%20name": "second"}) == "first,second"


@pytest.mark.parametrize(
    "template", ["{}", "{", "}", "{{name}}", "{=name}", "{name:0}", "{name:10000}", "{name**}", "{a..b}"]
)
def test_invalid_expressions_fail_without_fetch_or_silent_partial_expansion(template):
    with pytest.raises(ValueError):
        expand(template, {"name": "x"})


def test_scalar_limits_reject_oversized_inputs_and_encoded_uri():
    for template, values in [("x" * 8193, {}), ("{x}", {"x": "x" * 8193}), ("{x}", {"x": "🙂" * 1000})]:
        with pytest.raises(ValueError):
            expand(template, values)
