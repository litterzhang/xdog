"""Extensible event handling without eagerly importing optional protocol SDKs."""
from xdog.eyes.handlers.contracts import EventHandler, HandlerHealthError, HandlerSnapshot
from xdog.eyes.handlers.registry import HandlerRegistry, builtin_handlers

__all__ = ['EventHandler', 'HandlerHealthError', 'HandlerRegistry', 'HandlerSnapshot', 'builtin_handlers']
