"""Explicit handler type registration, with lazy optional implementation imports."""
from collections.abc import Callable
from pathlib import Path

from xdog.eyes.errors import ObserverValidationError
from xdog.eyes.handlers.contracts import EventHandler
from xdog.eyes.models import HandlerDef

HandlerParser = Callable[[object, str, Path], HandlerDef]
HandlerFactory = Callable[[HandlerDef, Path], EventHandler]


class HandlerRegistry:
    def __init__(self) -> None:
        self._parsers: dict[str, HandlerParser] = {}
        self._factories: dict[str, HandlerFactory] = {}

    def register(self, type_name: str, *, parse_definition: HandlerParser, create: HandlerFactory) -> None:
        if not isinstance(type_name, str) or not type_name.strip() or '\x00' in type_name:
            raise ValueError('Expected a nonempty handler type')
        if type_name in self._parsers:
            raise ValueError('Handler type is already registered')
        self._parsers[type_name] = parse_definition
        self._factories[type_name] = create

    def parse_definition(self, value: object, path: str, base_dir: Path) -> HandlerDef:
        from xdog.eyes.loader import _text

        if not isinstance(value, dict):
            raise ObserverValidationError('Expected handler object', path=path, code='wrong_shape')
        name, kind = _text(value.get('id'), path + '.id'), _text(value.get('type'), path + '.type')
        parser = self._parsers.get(kind)
        if parser is None:
            raise ObserverValidationError('Unknown handler type: ' + kind, path=path, code='unknown_type')
        definition = parser(value, path, base_dir)
        if not isinstance(definition, HandlerDef) or (definition.id, definition.type) != (name, kind):
            raise ObserverValidationError('Handler parser changed definition identity', path=path)
        return definition

    def create(self, definition: HandlerDef, cwd: Path) -> EventHandler:
        factory = self._factories.get(definition.type)
        if factory is None:
            raise ObserverValidationError('Unknown handler type: ' + definition.type, code='unknown_type')
        return factory(definition, cwd)


def builtin_handlers() -> HandlerRegistry:
    from xdog.eyes.handlers.acp_config import parse_definition

    def create(definition: HandlerDef, cwd: Path) -> EventHandler:
        from xdog.eyes.handlers.acp import AcpHandler
        from xdog.eyes.models import AcpHandlerDef

        if not isinstance(definition, AcpHandlerDef):
            raise ValueError('Expected ACP definition')
        return AcpHandler(definition, cwd=cwd)

    registry = HandlerRegistry()
    registry.register('acp', parse_definition=parse_definition, create=create)
    return registry
