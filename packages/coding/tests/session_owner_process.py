"""Owned child used to prove OS locks survive competing writers and process death."""

import sys
from pathlib import Path

from xdog.coding.core.sessions.ownership import SessionLease

if __name__ == "__main__":
    lease = SessionLease.acquire(Path(sys.argv[1]), sys.argv[2])
    print("ready", flush=True)
    try:
        sys.stdin.buffer.read(1)
    finally:
        lease.close()
