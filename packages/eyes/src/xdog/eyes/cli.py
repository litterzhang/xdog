"""Explicit Eyes control: bounded JSON commands and unacknowledged event JSONL."""
from __future__ import annotations

import argparse
import asyncio
import math
import sys
from collections.abc import Sequence
from pathlib import Path

from xdog.eyes._cli_io import JsonOutput, OutputClosed, _read_definition, argument_json, read_stdin_json
from xdog.eyes._daemon_control import DaemonControl
from xdog.eyes.loader import parse
from xdog.eyes.local_control import daemon_location
from xdog.eyes.process import reap_runtime


def _positive_timeout(value: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError('timeout must be finite positive seconds')
    return result


async def _follow(control: DaemonControl, output: JsonOutput, *, after: int | None, limit: int) -> None:
    async def consume() -> None:
        async for event in control.subscribe(after=after, limit=limit):
            await output.write(event)

    reader = asyncio.create_task(consume())
    detached = asyncio.create_task(output.wait_closed())
    try:
        done, _ = await asyncio.wait({reader, detached}, return_when=asyncio.FIRST_COMPLETED)
        if reader in done:
            reader.result()
    finally:
        for task in (reader, detached):
            task.cancel()
        await asyncio.gather(reader, detached, return_exceptions=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, help='Private daemon isolation directory (default: shared per UID)')
    parser.add_argument('--owner', default='cli', help='Stable owner namespace within this user')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('validate').add_argument('definition', type=Path)
    daemon = commands.add_parser('daemon').add_subparsers(dest='daemon_command', required=True)
    for name in ('start', 'status', 'stop'):
        daemon.add_parser(name)
    start = commands.add_parser('start')
    start.add_argument('definition', help=(
        'Definition file (roots relative to JSON directory), or - for bounded JSON with roots relative to cwd'))
    start.add_argument('--delivery', choices=('events', 'handlers', 'auto'), default='handlers')
    start.add_argument('--key')
    start.add_argument('--no-recover', action='store_true')
    start.add_argument('--no-wait', action='store_true',
                       help='Return registration receipt without waiting for baseline')
    start.add_argument('--source-checkpoints-json',
                       help='JSON object of definition-bound scheduler source checkpoints')
    publish = commands.add_parser('publish', help='Durably submit an event to a declared source; no delivery guarantee')
    publish.add_argument('--observer', required=True)
    publish.add_argument('--source', required=True)
    publish.add_argument('--event-id', required=True)
    publish.add_argument('--event-file', required=True, help='Bounded regular JSON object file, or - for stdin')
    status = commands.add_parser('status')
    status.add_argument('registration_id', nargs='?')
    status.add_argument('--wait-ready', action='store_true', help='Read-only wait for original registration baseline')
    status.add_argument('--timeout', type=_positive_timeout, default=30.)
    commands.add_parser('list')
    for name in ('stop', 'cancel', 'pause', 'resume'):
        commands.add_parser(name).add_argument('registration_id')
    events = commands.add_parser('events')
    events.add_argument('--after', type=int)
    events.add_argument('--limit', type=int, default=64)
    events.add_argument('--follow', action='store_true', help='Stream one event per JSON line, without ACK or startup')
    commands.add_parser('ack').add_argument('sequence', type=int)
    for scope in [*commands.choices.values(), *daemon.choices.values()]:
        scope.add_argument('--directory', type=Path, default=argparse.SUPPRESS)
        scope.add_argument('--owner', default=argparse.SUPPRESS)
    options = parser.parse_args(argv)
    if options.command == 'status' and options.wait_ready and options.registration_id is None:
        parser.error('status --wait-ready requires a registration ID or key')
    if options.command == 'events' and (not 1 <= options.limit <= 128
                                      or options.after is not None and options.after < 0):
        parser.error('events requires --limit 1..128 and nonnegative --after')

    async def run() -> None:
        control = DaemonControl(owner=options.owner, directory=options.directory)
        output = JsonOutput()
        try:
            if options.command == 'validate':
                data, base_dir = await asyncio.to_thread(_read_definition, str(options.definition.absolute()))
                definition = parse(data, base_dir=base_dir)
                await output.write({'valid': True, 'name': definition.name, 'sources': len(definition.sources)})
            elif options.command == 'daemon':
                if options.daemon_command == 'start':
                    result = await control.start_daemon()
                else:
                    try:
                        result = (await control.daemon_status() if options.daemon_command == 'status'
                                  else await control.stop_daemon())
                    except (FileNotFoundError, ConnectionRefusedError):
                        result = {'state': 'not-running'}
                await output.write(result)
            elif options.command == 'start':
                if options.definition == '-':
                    definition = parse(read_stdin_json(), base_dir=Path.cwd())
                else:
                    data, base_dir = await asyncio.to_thread(
                        _read_definition, str(Path(options.definition).absolute()))
                    definition = parse(data, base_dir=base_dir)
                checkpoints = (argument_json(options.source_checkpoints_json)
                               if options.source_checkpoints_json is not None else None)
                # Invalid stdin/definition/checkpoint JSON cannot start a daemon.
                delivery = options.delivery
                if delivery == 'auto':
                    delivery = 'events' if all(item.type == 'events' for item in definition.handlers) else 'handlers'
                await control.start_daemon()
                await output.write(await control.start(definition, delivery=delivery,
                    key=options.key, recover=not options.no_recover, wait_ready=not options.no_wait,
                    source_checkpoints=checkpoints))
            elif options.command == 'publish':
                if options.event_file == '-':
                    payload = read_stdin_json()
                else:
                    payload, _ = await asyncio.to_thread(
                        _read_definition, str(Path(options.event_file).absolute()))
                await output.write(await control.publish(options.observer, source_id=options.source,
                    event_id=options.event_id, payload=payload))
            elif options.command == 'events':
                if options.follow:
                    await _follow(control, output, after=options.after, limit=options.limit)
                else:
                    await output.write({'events': await control.events(after=options.after, limit=options.limit)})
            elif options.command == 'ack':
                await output.write(await control.ack(options.sequence))
            elif options.command in {'status', 'list'}:
                if options.command == 'status' and options.wait_ready:
                    await output.write(await control.wait_ready(options.registration_id, timeout=options.timeout))
                else:
                    try:
                        result = await control.status(getattr(options, 'registration_id', None))
                    except (FileNotFoundError, ConnectionRefusedError):
                        result = {'registrations': [], 'state': 'not-running'}
                    await output.write(result)
            else:
                method = control.cancel if options.command in {'stop', 'cancel'} else getattr(control, options.command)
                await output.write(await method(options.registration_id))
        finally:
            await control.aclose()  # detach only, even after EOF, pipe close or cancellation
            output.close()

    try:
        asyncio.run(run())
        if options.command == 'daemon' and options.daemon_command == 'stop':
            reap_runtime(daemon_location(options.directory), timeout=.5)
        return 0
    except OutputClosed:
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError, TimeoutError, ImportError) as exc:
        try:
            print(str(exc)[:2048], file=sys.stderr)
        except (OSError, ValueError):
            pass
        return 2
