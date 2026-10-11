"""Read-only presentation of an application's current terminal command menu."""
from __future__ import annotations

import unicodedata
from collections.abc import Mapping

from xdog.tui.utils import sanitize_terminal_text


def command_help(commands: Mapping[str, str], query: str = '') -> str:
    """Render all matches; metadata is never parsed as a command or an approval."""
    if not isinstance(query, str) or len(query.encode()) > 256:
        raise ValueError('Help search is limited to 256 UTF-8 bytes')
    clean = sanitize_terminal_text(query)
    if (clean != query or '\n' in query or '\r' in query
            or any(unicodedata.category(char) in {'Cc', 'Cs', 'Cf'} for char in query)):
        raise ValueError('Help search must be single-line plain text')
    rows = normalize_command_menu(commands)
    selected = [(name, detail) for name, detail in sorted(rows.items(), key=lambda row: row[0].casefold())
                if clean.strip().casefold() in (name + ' ' + detail).casefold()]
    header = f'Terminal command menu · {len(selected)}/{len(rows)} matches · read-only'
    notes = 'Availability is checked when invoked. Help never runs commands or grants approval.'
    if not selected:
        return header + '\n' + notes + '\nNo matching commands; use /help without a filter.'
    return '\n'.join([header, notes, '', *(name + ' — ' + detail for name, detail in selected)])


def normalize_command_menu(commands: Mapping[str, str]) -> dict[str, str]:
    """Same inert metadata used by native help and scoped extension menu inspection."""
    if len(commands) > 10000:
        raise ValueError('Command menu exceeds help capacity; no partial catalog shown')
    rows: dict[str, str] = {}
    size = 0
    for name, description in commands.items():
        if (not isinstance(name, str) or not isinstance(description, str)
                or len(name.encode()) > 256 or len(description.encode()) > 8192):
            raise ValueError('Invalid terminal command menu metadata')
        normalized = name.removeprefix('/')
        if not normalized or any(char.isspace() for char in normalized):
            raise ValueError('Invalid command name in terminal menu')
        if (sanitize_terminal_text(normalized) != normalized
                or any(unicodedata.category(char) in {'Cc', 'Cs', 'Cf'} for char in normalized)):
            raise ValueError('Unsafe command name in terminal menu')
        # Never turn line/escape-bearing metadata into another terminal action.
        detail = ' '.join(''.join(char for char in sanitize_terminal_text(description)
                                  if unicodedata.category(char) not in {'Cs', 'Cf'}).split())
        key = '/' + normalized
        if key in rows and rows[key] != detail:
            raise ValueError('Conflicting terminal command menu metadata')
        rows[key] = detail
        size += len(key.encode()) + len(detail.encode())
        if size > 1024 * 1024:
            raise ValueError('Command menu exceeds help byte capacity; no partial catalog shown')
    return rows
