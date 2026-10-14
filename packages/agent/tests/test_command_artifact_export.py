from __future__ import annotations

import json
import os
import threading

import pytest
from xdog.agent.extensions.artifact_export import ArtifactLoader, export_snapshot
from xdog.agent.extensions.command_result import (
    ExtensionCommandResult,
    artifact_bytes,
    artifact_from_bytes,
    read_artifact,
)
from xdog.agent.extensions.tool_result import make_result

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII="


def artifact():
    return ExtensionCommandResult(
        "rich",
        "ext-rich-show",
        make_result(
            [
                {"type": "text", "text": "sensitive operator output"},
                {"type": "image", "data": PNG, "mimeType": "image/png"},
            ],
            structured=[False, 0, None, ""],
            details={"private": "credentials stay private", "enabled": False},
        ),
    )


def test_lossless_file_codec_preserves_original_binary_and_typed_metadata_without_sanitizing(tmp_path):
    value = artifact()
    data = artifact_bytes(value)
    restored = artifact_from_bytes(data)
    assert restored.to_dict() == value.to_dict() and restored.result["content"][1]["data"] == PNG
    file = tmp_path / "artifact.json"
    file.write_bytes(data)
    assert read_artifact(file).to_dict() == value.to_dict()
    packed, preview, images = export_snapshot(value.to_dict())
    assert packed == data and images == 1 and PNG not in preview
    assert "NO redaction" in preview and "image/png" in preview and "SHA-256" in preview


def test_tampering_and_unsupported_file_versions_refused() -> None:

    def _check_case(mutate):
        value = json.loads(artifact_bytes(artifact()))
        if mutate == "schema":
            value["schema"] = "unsupported/v2"
        elif mutate == "checksum":
            value["sha256"] = "0" * 64
        elif mutate == "image":
            value["artifact"]["result"]["content"][1]["data"] = "bad"
        elif mutate == "command":
            value["artifact"]["command"] = "/quit"
        else:
            value["extra"] = "ignored must not be accepted"
        with pytest.raises((ValueError, RuntimeError)):
            artifact_from_bytes(json.dumps(value).encode())

    for mutate in ["schema", "checksum", "image", "command", "extra"]:
        _check_case(mutate)


def test_nonregular_fifo_and_oversized_sources_do_not_hang(tmp_path):
    if hasattr(os, "mkfifo"):
        file = tmp_path / "pipe"
        os.mkfifo(file)
        with pytest.raises(ValueError, match="regular"):
            read_artifact(file)
    with pytest.raises(ValueError, match="retention"):
        artifact_from_bytes(b"x" * (4 * 1024 * 1024 + 1))


def test_async_loader_never_executes_data_and_close_suppresses_late_publication(tmp_path, monkeypatch):
    import xdog.agent.extensions.command_result as codec

    file = tmp_path / "output.json"
    file.write_bytes(artifact_bytes(artifact()))
    events = []
    reader = ArtifactLoader(events.append)
    assert reader.start(file, "owner")
    reader._thread.join(3)
    assert events[0]["type"] == "artifact_loaded" and events[0]["context"] == "owner"
    assert events[0]["artifact"].result["content"][1]["data"] == PNG
    entered, release = threading.Event(), threading.Event()
    original = codec.read_artifact

    def slow(path):
        entered.set()
        assert release.wait(3)
        return original(path)

    monkeypatch.setattr(codec, "read_artifact", slow)
    events.clear()
    assert reader.start(file, "old") and entered.wait(2)
    assert not reader.start(file, "new")
    reader.close()
    release.set()
    reader._thread.join(3)
    assert not events
