"""Editor access is absent from the SDK; host helper codecs may remain during migration."""
import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.dialogs import validate_sdk_dialog


@pytest.mark.parametrize('operation', ['read', 'replace', 'propose'])
def test_editor_requests_cannot_cross_the_sdk_boundary(operation):
    with pytest.raises(ValueError, match='removed'):
        validate_sdk_dialog({'kind': 'editor', 'id': 'request', 'title': 'Editor', 'operation': operation})
    api = ExtensionAPI({})
    assert all(not hasattr(api, name) for name in ('get_editor', 'set_editor', 'request_editor_text'))
    assert not api._dialogs
