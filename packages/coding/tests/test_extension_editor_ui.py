"""Native editor helpers cannot be acquired by an extension command."""
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode


def test_command_editor_mutation_helpers_are_absent(coding_session):
    ui = InteractiveMode(coding_session)
    try:
        ui._editor.set_text('preserved')
        for name in ('_command_editor_snapshot', '_command_editor_replace', '_editor_actions',
                     '_prepare_editor_write', '_request_editor_write'):
            assert not hasattr(ui, name)
        assert ui._editor.get_text() == 'preserved' and not coding_session.agent.is_streaming
    finally:
        ui._request_exit()
