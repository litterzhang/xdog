import json


def test_validate_is_standalone_and_reports_invalid_definitions(tmp_path, observer_data, capsys):
    from xdog.eyes.cli import main

    path = tmp_path / "eyes.json"
    path.write_text(json.dumps(observer_data))
    assert main(["validate", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
    observer_data["unknown"] = True
    path.write_text(json.dumps(observer_data))
    assert main(["validate", str(path)]) == 2
    assert "Unknown fields" in capsys.readouterr().err


def test_start_requires_host_before_creating_any_process(tmp_path, observer_data, monkeypatch, capsys):
    from xdog.eyes.cli import main

    monkeypatch.delenv("XDOG_EVENT_CONTEXT", raising=False)
    path = tmp_path / "eyes.json"
    path.write_text(json.dumps(observer_data))
    assert main(["start", str(path)]) == 2
    assert "current session" in capsys.readouterr().err


def test_runtime_file_validation_precedes_truncation(tmp_path):
    import os

    import pytest
    from xdog.eyes.process import _private_open
    from xdog.eyes.transport.protocol import ProtocolError

    path = tmp_path / "existing.json"
    path.write_text("preserve this data")
    path.chmod(0o644)
    with pytest.raises(ProtocolError):
        _private_open(path, os.O_WRONLY | os.O_TRUNC)
    assert path.read_text() == "preserve this data"
