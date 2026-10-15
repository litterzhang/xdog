"""Author, validate and run declarative observers, like flow's CLI boundary."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from xdog.eyes.control import request
from xdog.eyes.loader import load
from xdog.eyes.process import ensure_runtime
from xdog.eyes.runtime import definition_data
from xdog.eyes.transport.protocol import JsonValue, ProtocolError, load_context, object_value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "start"):
        commands.add_parser(name).add_argument("definition", type=Path)
    commands.add_parser("status").add_argument("registration_id", nargs="?")
    for name in ("stop", "resume"):
        commands.add_parser(name).add_argument("registration_id")
    options = parser.parse_args(argv)
    try:
        if options.command == "validate":
            definition = load(options.definition)
            print(json.dumps({"valid": True, "name": definition.name, "sources": len(definition.sources)}))
            return 0
        descriptor = os.environ.get("XDOG_EVENT_CONTEXT")
        if not descriptor:
            raise ProtocolError("No current session event capability; start from a live coding session")
        context = load_context(Path(descriptor))
        body: dict[str, JsonValue] = {}
        if options.command == "start":
            definition = load(options.definition)
            ensure_runtime(context)
            body["definition"] = definition_data(definition)
        elif options.registration_id:
            body["registration_id"] = options.registration_id
        response = asyncio.run(request(context, options.command, body))
        if response["status"] not in {"ok", "accepted", "duplicate"}:
            details = object_value(response["body"])
            raise ProtocolError(str(details.get("error", "Command refused: " + str(response["status"]))))
        print(json.dumps(response["body"], ensure_ascii=True, allow_nan=False))
        return 0
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
