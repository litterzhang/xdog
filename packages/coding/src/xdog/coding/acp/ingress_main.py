"""Explicit lightweight ACP service entry; not an interactive coding CLI callback."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sessions-dir', required=True, type=Path)
    parser.add_argument('--inbox-dir', required=True, type=Path)
    parser.add_argument('--socket', required=True, type=Path)
    options = parser.parse_args()

    async def run() -> None:
        from xdog.coding.acp.ingress import IngressConfig, IngressServer

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for name in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(name, stop.set)
        server = await IngressServer.start(IngressConfig(options.sessions_dir, options.inbox_dir, options.socket))
        try:
            print(json.dumps({'address': str(options.socket), 'generation': server.generation}), flush=True)
            await stop.wait()
        finally:
            await server.aclose()
            for name in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(name)

    asyncio.run(run())


if __name__ == '__main__':
    main()
