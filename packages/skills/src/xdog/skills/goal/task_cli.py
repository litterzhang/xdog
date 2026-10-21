"""Explicitly bound task list/update helper; no DB, RPC or execution loop.

Run through the host's normally permission-gated shell tool. This module does not
check the host's current binding or authorize writes on behalf of a caller.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .codec import json_text, read_json
from .files import TaskFiles

_MAX_INPUT = 262144


def _data(argument: str | None) -> dict[str, Any]:
    payload = sys.stdin.read(_MAX_INPUT + 1) if argument is None else argument
    if len(payload.encode('utf-8')) > _MAX_INPUT:
        raise ValueError('CLI input exceeds bounded capacity')
    return read_json(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True, help='Exact absolute owner workspace; no symlinks')
    parser.add_argument('--goal-id', required=True, help='Exact Goal ID returned by the host; never inferred')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list')
    update = commands.add_parser('update', help='Update an existing task using an observed version')
    update.add_argument('--task-id', required=True)
    update.add_argument('--expected-version', type=int, required=True)
    update.add_argument('--data', help='JSON changes; defaults to bounded JSON on stdin')
    args = parser.parse_args(argv)
    try:
        files = TaskFiles(args.workspace, args.goal_id)
        value: Any
        if args.command == 'list':
            value = [asdict(task) for task in files.list()]
        else:
            changes = _data(args.data)
            for key in ('depends_on', 'evidence'):
                if key in changes:
                    if not isinstance(changes[key], list):
                        raise ValueError('Task collections must be arrays of strings')
                    changes[key] = tuple(changes[key])
            value = asdict(files.update(args.task_id, args.expected_version, changes))
        print(json_text(value))
        return 0
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        print(json_text({'error': str(exc)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
