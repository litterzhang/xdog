"""Explicit handler type registration, with lazy optional implementation imports."""
from collections.abc import Callable
from pathlib import Path

from xdog.eyes.errors import ObserverValidationError
from xdog.eyes.handlers.contracts import EventHandler
from xdog.eyes.models import HandlerDef

HandlerParser = Callable[[object, str, Path], HandlerDef]
HandlerFactory = Callable[[HandlerDef], EventHandler]


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

    def create(self, definition: HandlerDef) -> EventHandler:
        factory = self._factories.get(definition.type)
        if factory is None:
            raise ObserverValidationError('Unknown handler type: ' + definition.type, code='unknown_type')
        return factory(definition)


def builtin_handlers() -> HandlerRegistry:
    from xdog.eyes.handlers.acp_config import parse_definition

    def create(definition: HandlerDef) -> EventHandler:
        from xdog.eyes.handlers.acp import AcpHandler
        from xdog.eyes.models import AcpHandlerDef

        if not isinstance(definition, AcpHandlerDef):
            raise ValueError('Expected ACP definition')
        return AcpHandler(definition)

    registry = HandlerRegistry()
    registry.register('acp', parse_definition=parse_definition, create=create)
    registry.register('events', parse_definition=_parse_events, create=_events_only)
    return registry


def _parse_events(value: object, path: str, base_dir: Path) -> HandlerDef:
    from xdog.eyes.loader import _object, _text
    row = _object(value, path, {'id', 'type'})
    return HandlerDef(_text(row.get('id'), path + '.id'), 'events')


def _events_only(definition: HandlerDef) -> EventHandler:
    raise ValueError('events handlers require the durable daemon events delivery mode')
