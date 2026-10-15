"""Author, validate and explicitly manage observers, like flow's CLI boundary."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from xdog.eyes.control import request
from xdog.eyes.loader import definition_data, load
from xdog.eyes.local_control import MAX_FRAME, JsonValue, object_value, read_location, runtime_location
from xdog.eyes.process import ensure_runtime, reap_runtime


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('validate', 'start'):
        commands.add_parser(name).add_argument('definition', type=Path)
    commands.add_parser('status').add_argument('registration_id', nargs='?')
    for name in ('stop', 'resume'):
        commands.add_parser(name).add_argument('registration_id')
    options = parser.parse_args(argv)
    try:
        if options.command == 'validate':
            definition = load(options.definition)
            print(json.dumps({'valid': True, 'name': definition.name, 'sources': len(definition.sources)}))
            return 0
        location = runtime_location()
        body: dict[str, JsonValue] = {}
        if options.command == 'start':
            data = definition_data(load(options.definition))
            if len(json.dumps(data, ensure_ascii=True).encode()) > MAX_FRAME - 4096:
                raise ValueError('Observer definition exceeds control capacity')
            body['definition'] = cast(dict[str, JsonValue], data)
            location = ensure_runtime(location)
        else:
            try:
                location = read_location(location)
            except FileNotFoundError:
                if options.command == 'status':
                    print(json.dumps({'registrations': [], 'state': 'not-running'}))
                    return 0
                raise RuntimeError('No active Eyes runtime for this project') from None
            if options.registration_id:
                body['registration_id'] = options.registration_id
        response = asyncio.run(request(
            location, options.command, body, timeout=125 if options.command == 'resume' else 30))
        if response['status'] not in {'ok', 'accepted', 'duplicate'}:
            raise ValueError(str(object_value(response['body']).get('error', 'Eyes command refused; inspect status')))
        print(json.dumps(response['body'], ensure_ascii=True, allow_nan=False))
        if options.command == 'stop':
            reap_runtime(location, timeout=0.25)
        return 0
    except (OSError, ValueError, RuntimeError, TimeoutError, ImportError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
