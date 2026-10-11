"""Remote editable keys and opt-in release observation; never submission or terminal writing."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from xdog.agent.extensions.terminal_input import text
from xdog.tui.extension_shortcuts import ExtensionShortcuts
from xdog.tui.keys import KeyEventType, parse_key_events
from xdog.tui.prompt_keymap import PromptKeymap
from xdog.tui.prompt_terminal import prompt_key_policy
from xdog.tui.stdin_buffer import key_tokens


def editable_data(value: object, kind: object, *, keymap: PromptKeymap | None = None,
                  shortcuts: list[dict[str, Any]] | None = None, release: bool = False) -> str:
    data = text(value)
    if kind not in ('key', 'paste', 'release') or kind == 'release' and not release:
        raise ValueError('Terminal input kind unavailable; release requires explicit negotiation')
    if kind == 'paste':
        return data  # Remains paste even if it contains newline or escape bytes.
    bindings = keymap or PromptKeymap()
    if bindings.error:
        raise ValueError("Configured RPC keymap is invalid; terminal input unavailable")
    effective = ExtensionShortcuts(shortcuts or [], bindings)
    tokens = key_tokens(data.encode())
    if kind == 'release' and not tokens:
        raise ValueError('Terminal release observation requires an encoded release')
    for token in tokens:
        events = parse_key_events(token)
        if len(events) != 1 or (events[0].event_type is KeyEventType.RELEASE) != (kind == 'release'):
            raise ValueError('Remote terminal event phase does not match negotiated kind')
        event = replace(events[0], event_type=KeyEventType.PRESS) if kind == 'release' else events[0]
        if prompt_key_policy(event, bindings.resolve(event), extension=effective.command(event) is not None) != 'edit':
            raise ValueError('Remote terminal keys must be editable; controls remain client-owned')
    return data


def plain_custom_line(value: str) -> str:
    """Vetted frame controls without destroying ZWJ display-cell geometry."""
    from xdog.tui.components.extension_custom import safe_custom_line
    from xdog.tui.utils import strip_ansi
    return strip_ansi(safe_custom_line(value))
