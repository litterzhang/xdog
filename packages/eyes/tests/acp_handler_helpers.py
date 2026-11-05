"""Eyes must interoperate with a real third-party SDK process, not a coding host."""

import asyncio
import json
import sys
from pathlib import Path

from xdog.eyes.deliveries import Delivery
from xdog.eyes.models import AcpHandlerDef, Event, FileEvidence, FileState


def definition(record, *, mode="success", xdog=False, gate=None):
    argv = [
        sys.executable,
        str(Path(__file__).with_name("fixtures") / "acp_agent.py"),
        "--record",
        str(record),
        "--mode",
        mode,
    ]
    if xdog:
        argv.append("--xdog")
    if gate is not None:
        argv.extend(["--gate", str(gate)])
    from xdog.eyes.connections import StdioConnection

    return AcpHandlerDef("handler", "acp", StdioConnection(tuple(argv)), "target", "Review tests")


def delivery(identifier="d1"):
    evidence = FileEvidence("src/a.py; $(touch should-not-exist)", None, FileState("hash", 3), 1.0, "/project")
    return Delivery(
        identifier,
        "registration",
        "route",
        (Event("event", "files", "changes", "file.created", 1.0, (evidence,)),),
    )


def rows(record):
    return [json.loads(line) for line in record.read_text().splitlines()] if record.exists() else []


async def wait_for(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)
