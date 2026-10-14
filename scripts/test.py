"""Run every configured test path once, concurrently, without filtering cases."""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_path(path: str) -> tuple[str, int, str]:
    label = Path(path).parent.name
    # Balance the larger packages (owned workers, sockets, CLI and build I/O).
    # A small worker pool overlaps that I/O without collecting unrelated packages.
    workers = {"agent": 2, "coding": 2, "flow": 2, "claw": 2}.get(label, 0)
    command = [
        sys.executable, "-m", "pytest", path, "-q", "-n", str(workers),
        "-o", f"cache_dir={ROOT / '.pytest_cache' / 'packages' / label}",
    ]
    result = subprocess.run(
        command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace", check=False,
    )
    return path, result.returncode, result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=4, help="maximum concurrent package test processes (default: 4)")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    paths = project["tool"]["pytest"]["ini_options"]["testpaths"]
    if not isinstance(paths, list) or not paths or not all(isinstance(path, str) and path for path in paths):
        parser.error("pyproject.toml must configure at least one pytest test path")
    # Start long-running packages before the tiny paths so their I/O overlaps
    # from the beginning; every configured path is still submitted exactly once.
    priority = {"agent": 0, "coding": 1, "flow": 2, "claw": 3, "tui": 4, "ai": 5, "site": 6}
    paths = sorted(paths, key=lambda path: priority.get(Path(path).parent.name, 0))
    start = time.perf_counter()
    failed = []
    print(f"Running all {len(paths)} configured test paths with up to {args.jobs} processes", flush=True)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_path, path) for path in paths]
        for future in as_completed(futures):
            path, code, output = future.result()
            print(f"\n--- {path} ---\n{output}", end="" if output.endswith("\n") else "\n", flush=True)
            if code:
                failed.append(path)
    elapsed = time.perf_counter() - start
    print(f"\nAll {len(paths)} test paths completed in {elapsed:.2f}s", flush=True)
    if failed:
        print("Failed test paths: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
