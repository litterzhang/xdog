"""Pure lossless command artifact packing; clients own review and file-writing policy."""

from __future__ import annotations

import base64
import hashlib
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from xdog.agent.extensions.command_result import ExtensionCommandResult, artifact_bytes


def export_snapshot(value: dict[str, Any]) -> tuple[bytes, str, int]:
    artifact = ExtensionCommandResult.from_dict(value)
    data = artifact_bytes(artifact)
    images = [part for part in artifact.result["content"] if part["type"] == "image"]
    summary = (
        artifact.summary + "\nLossless JSON includes original binary bytes and typed metadata. "
        "NO redaction or upload.\n"
    )
    for index, image in enumerate(images, 1):
        original = base64.b64decode(image["data"], validate=True)
        summary += (
            f"Image {index}: {image['mimeType']} · {len(original):,} bytes · "
            f"SHA-256 {hashlib.sha256(original).hexdigest()}\n"
        )
    summary += "\n" + artifact.text()
    return data, summary, len(images)


class ArtifactLoader:
    """One explicit bounded local read; close/change of owner suppresses late publication."""

    def __init__(self, publish: Callable[[dict[str, Any]], None]) -> None:
        self.publish = publish
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, path: Path, context: str) -> bool:
        if self._closed.is_set() or self._thread is not None and self._thread.is_alive():
            return False

        def load() -> None:
            from xdog.agent.extensions.command_result import read_artifact

            try:
                value = read_artifact(path)
                event = {"type": "artifact_loaded", "context": context, "artifact": value}
            except Exception as exc:
                event = {"type": "artifact_failed", "context": context, "error": str(exc)}
            if not self._closed.is_set():
                self.publish(event)

        self._thread = threading.Thread(target=load, daemon=True, name="xdog-artifact-reader")
        self._thread.start()
        return True

    def close(self) -> None:
        self._closed.set()
