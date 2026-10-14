from __future__ import annotations

import pytest
from xdog.agent.session_name import session_name


def test_session_name_rejects_nontext_oversize_or_invisible_terminal_controls() -> None:

    def _check_case(value):
        with pytest.raises(ValueError):
            session_name(value)

    for value in [
        None,
        False,
        4,
        [],
        "x" * 257,
        "界" * 86,
        "\x1b[31mname",
        "a\nb",
        "a\tb",
        "\x7f",
        "\x85",
        "\u2028",
        "\u2029",
        "\ud800",
        "\u202eabc",
        "\u2066x",
        "\ufeff",
    ]:
        _check_case(value)


@pytest.mark.parametrize('value', ['', 'x' * 256, '界' * 85, '开发 · café', '👩\u200d💻', 'کار\u200cها'])
def test_session_name_keeps_unicode_joiners_and_byte_boundary_without_loss(value):
    assert session_name(' ' + value + ' ') == value if len(value.encode()) < 254 else session_name(value) == value
