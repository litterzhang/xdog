import json
from dataclasses import FrozenInstanceError

import pytest


def test_relative_root_and_defaults(tmp_path, observer_data):
    from xdog.eyes.loader import parse

    result = parse(observer_data, base_dir=tmp_path)
    assert result.sources[0].root == tmp_path.resolve()
    assert result.sources[0].poll_interval_ms == 1000
    assert result.sources[0].include == ("**",)
    assert result.detectors[0].debounce_ms == 500
    with pytest.raises(FrozenInstanceError):
        result.name = "changed"


@pytest.mark.parametrize("collection,field,value", [
    ("sources", "unknown", True),
    ("sources", "type", "http"),
    ("sources", "poll_interval_ms", True),
    ("sources", "max_files", 0),
    ("sources", "include", ["../secret"]),
    ("sources", "include", ["[ab].py"]),
    ("sources", "include", ["!private"]),
    ("sources", "include", []),
    ("detectors", "source", "missing"),
    ("detectors", "debounce_ms", float("nan")),
    ("detectors", "max_batch_delay_ms", 1),
    ("handlers", "target", "other-session"),
    ("handlers", "instruction", ""),
    ("routes", "handler", "missing"),
    ("routes", "event_types", ["file.modified", "file.modified"]),
    ("routes", "event_types", ["arbitrary"]),
])
def test_invalid_configuration_rejected(tmp_path, observer_data, collection, field, value):
    from xdog.eyes.errors import ObserverValidationError
    from xdog.eyes.loader import parse

    observer_data[collection][0][field] = value
    with pytest.raises(ObserverValidationError) as error:
        parse(observer_data, base_dir=tmp_path)
    assert error.value.path
    assert error.value.code


@pytest.mark.parametrize("key,value", [("unknown", True), ("version", True), ("sources", []), ("name", "")])
def test_invalid_root_rejected(tmp_path, observer_data, key, value):
    from xdog.eyes.errors import ObserverValidationError
    from xdog.eyes.loader import parse

    observer_data[key] = value
    with pytest.raises(ObserverValidationError):
        parse(observer_data, base_dir=tmp_path)


def test_duplicate_identifiers_rejected(tmp_path, observer_data):
    from xdog.eyes.errors import ObserverValidationError
    from xdog.eyes.loader import parse

    observer_data["sources"].append(dict(observer_data["sources"][0]))
    with pytest.raises(ObserverValidationError):
        parse(observer_data, base_dir=tmp_path)


def test_load_resolves_against_document_not_cwd(tmp_path, monkeypatch, observer_data):
    from xdog.eyes.loader import load

    folder = tmp_path / "project"
    folder.mkdir()
    path = folder / "observer.json"
    path.write_text(json.dumps(observer_data))
    monkeypatch.chdir(tmp_path)
    assert load(path).sources[0].root == folder


def test_load_rejects_duplicate_json_keys(tmp_path):
    from xdog.eyes.errors import ObserverValidationError
    from xdog.eyes.loader import load

    path = tmp_path / "observer.json"
    path.write_text('{"version":1,"version":2}')
    with pytest.raises(ObserverValidationError):
        load(path)


@pytest.mark.parametrize("target", ["name", "identifier", "instruction"])
def test_definition_text_respects_host_protocol_capacity(tmp_path, observer_data, target):
    from xdog.eyes.errors import ObserverValidationError
    from xdog.eyes.loader import parse

    if target == "name":
        observer_data["name"] = "x" * 257
    elif target == "identifier":
        observer_data["sources"][0]["id"] = "x" * 257
        observer_data["detectors"][0]["source"] = "x" * 257
    else:
        observer_data["handlers"][0]["instruction"] = "x" * 65537
    with pytest.raises(ObserverValidationError):
        parse(observer_data, base_dir=tmp_path)
