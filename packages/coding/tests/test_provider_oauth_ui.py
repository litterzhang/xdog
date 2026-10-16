"""Native extension-provider authentication command discovery remains explicit."""

def test_login_command_discovery_is_explicit_extension_provider_auth_not_native_provider_cli():
    from xdog.coding.core.slash_commands import BUILTIN_COMMANDS
    assert 'Extension-provider' in BUILTIN_COMMANDS['login']
    assert 'xdog-ai' in BUILTIN_COMMANDS['login']
    assert 'auth menu' in BUILTIN_COMMANDS['extensions']
