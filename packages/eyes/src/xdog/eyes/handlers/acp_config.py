"""ACP definition validation without importing the optional protocol SDK."""
from pathlib import Path

from xdog.eyes.connections import ConnectionDef, StdioConnection, UnixConnection
from xdog.eyes.errors import ObserverValidationError
from xdog.eyes.loader import _items, _object, _text
from xdog.eyes.models import AcpHandlerDef


def _connection(value: object, path: str) -> ConnectionDef:
    data = _object(value, path, {"transport", "path", "command"})
    transport = data.get("transport")
    try:
        if transport == "unix":
            data = _object(value, path, {"transport", "path"})
            return UnixConnection(Path(_text(data.get("path"), path + ".path", maximum=4096)))
        if transport == "stdio":
            data = _object(value, path, {"transport", "command"})
            return StdioConnection(tuple(
                _text(arg, f"{path}.command[{i}]", maximum=65536)
                for i, arg in enumerate(_items(data.get("command"), path + ".command"))
            ))
    except ObserverValidationError:
        raise
    except (ValueError, UnicodeError) as exc:
        raise ObserverValidationError(str(exc), path=path, code="wrong_shape") from exc
    raise ObserverValidationError("Expected unix or stdio ACP transport", path=path, code="unknown_type")


def parse_definition(value: object, path: str, base_dir: Path) -> AcpHandlerDef:
    data = _object(value, path, {"id", "type", "connection", "session", "instruction", "delivery"})
    if data.get("type") != "acp":
        raise ObserverValidationError("Only ACP handlers are supported", path=path, code="unknown_type")
    delivery = data.get("delivery", "prompt")
    if delivery not in ("prompt", "enqueue"):
        raise ObserverValidationError("Expected prompt or enqueue delivery", path=path + ".delivery")
    return AcpHandlerDef(
        _text(data.get("id"), path + ".id"),
        "acp",
        _connection(data.get("connection"), path + ".connection"),
        _text(data.get("session"), path + ".session"),
        _text(data.get("instruction"), path + ".instruction", maximum=65536),
        delivery,
    )

