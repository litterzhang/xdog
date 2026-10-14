"""Host adapter uses real private worker OAuth, never transports credential records."""
from __future__ import annotations

import pytest

from packages.agent.tests.test_extension_provider_oauth import PROVIDER


def test_login_alias_extra_or_invalid_tokens_do_not_become_actions() -> None:

    def _check_case(arguments):
        from xdog.agent.extensions.provider_oauth_host import login_arguments

        with pytest.raises(ValueError, match="Usage"):
            login_arguments(arguments)

    for arguments in ["one two", "ext-provider extra", "ext-provider logout", '"', '""', "--native"]:
        _check_case(arguments)


def test_login_alias_is_extension_only_and_requires_explicit_selection():
    from xdog.agent.extensions.provider_oauth_host import login_arguments
    assert login_arguments('') == 'menu'
    assert login_arguments(PROVIDER) == 'login ' + PROVIDER
