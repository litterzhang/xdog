"""Bounded resource-change notices; never resource reads, model input or request replay."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any


class SubscriptionError(ValueError):
    pass


def resource_uri(value: Any) -> str:
    if (not isinstance(value, str) or not value or len(value.encode()) > 8192
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise SubscriptionError('Invalid resource subscription URI')
    return value


def subscription_identity(message: dict[str, Any]) -> Any:
    """Notifications carry subscription metadata; responses use the RPC ID."""
    if 'method' not in message:
        return message.get('id')
    params = message.get('params')
    meta = params.get('_meta') if isinstance(params, dict) else None
    return meta.get('io.modelcontextprotocol/subscriptionId') if isinstance(meta, dict) else None


CATALOGS = {"tools": "toolsListChanged", "prompts": "promptsListChanged", "resources": "resourcesListChanged"}


def notification_filters(value: Any) -> dict[str, Any]:
    """Snapshot an exact nonempty bounded filter, without URI inference or wildcard expansion."""
    if not isinstance(value, dict) or value.keys() - {*CATALOGS.values(), 'resourceSubscriptions'}:
        raise SubscriptionError('Invalid subscription filter fields')
    result: dict[str, Any] = {}
    for key in CATALOGS.values():
        if key in value:
            if type(value[key]) is not bool:
                raise SubscriptionError('Subscription catalog flags must be booleans')
            if value[key]:
                result[key] = True
    if 'resourceSubscriptions' in value:
        uris = value['resourceSubscriptions']
        if not isinstance(uris, list) or len(uris) > 16:
            raise SubscriptionError('Subscription requires at most 16 explicit resource URIs')
        selected = [resource_uri(uri) for uri in uris]
        if len(set(selected)) != len(selected) or sum(len(uri.encode()) for uri in selected) > 65536:
            raise SubscriptionError('Duplicate/excessive subscription URIs')
        if selected:
            result['resourceSubscriptions'] = sorted(selected)
    if not result:
        raise SubscriptionError('Select at least one subscription notification filter')
    return result


def filter_key(value: Any) -> str:
    return json.dumps(notification_filters(value), sort_keys=True, ensure_ascii=True, separators=(',', ':'))


class ResourceWatch:
    def __init__(self, uri: str, identifier: int, sink: Callable[[dict[str, Any]], None],
                 stop: Callable[[ResourceWatch], Awaitable[None]], *, kind: str = "resource") -> None:
        self.uri, self.id, self.sink, self.stop = uri, identifier, sink, stop
        self.kind = kind
        self.ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.state = 'opening'
        self.changes = 0
        self._timer: asyncio.TimerHandle | None = None

    def snapshot(self) -> dict[str, Any]:
        return {'uri': self.uri, 'state': self.state, 'changes': self.changes,
                **({'catalog': self.kind} if self.kind != 'resource' else {})}

    def filters(self) -> dict[str, Any]:
        return {'resourceSubscriptions': [self.uri]} if self.kind == 'resource' else {CATALOGS[self.kind]: True}

    def publish(self) -> None:
        if self._timer is None:
            self._timer = asyncio.get_running_loop().call_later(0.05, self._emit)

    def _emit(self) -> None:
        self._timer = None
        self.sink(self.snapshot())

    def activate(self) -> None:
        self.state = 'active'
        if not self.ready.done():
            self.ready.set_result(None)
        self._emit()

    def changed(self) -> None:
        if self.state not in {'opening', 'active'}:
            return
        self.changes = min(2**53, self.changes + 1)
        if self.state == 'active':
            self.publish()

    def end(self, state: str = 'ended', *, reason: str = '') -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self.state = state
        if not self.ready.done():
            self.ready.set_exception(SubscriptionError(reason or 'Resource subscription was not acknowledged'))
            self.ready.exception()
        self._emit()

    async def close(self) -> None:
        await self.stop(self)


class NotificationWatch(ResourceWatch):
    def __init__(self, selected: dict[str, Any], identifier: int, sink: Callable[[dict[str, Any]], None],
                 stop: Callable[[ResourceWatch], Awaitable[None]]) -> None:
        super().__init__('', identifier, sink, stop, kind='notifications')
        self._filters = notification_filters(selected)

    def filters(self) -> dict[str, Any]:
        return notification_filters(self._filters)

    def snapshot(self) -> dict[str, Any]:
        return {'uri': '', 'state': self.state, 'changes': self.changes, 'notifications': self.filters()}


class ResourceSubscriptions:
    def __init__(self) -> None:
        self.watches: dict[int, ResourceWatch] = {}
        self.sequence = 0
        self.lock = asyncio.Lock()

    def add(self, uri: str, sink: Callable[[dict[str, Any]], None],
            stop: Callable[[ResourceWatch], Awaitable[None]], *, kind: str = 'resource') -> ResourceWatch:
        if len(self.watches) >= 16:
            raise SubscriptionError('Resource subscription capacity reached')
        # Negative IDs form a separate monotonic namespace from ordinary RPCs.
        # Retired IDs never target a successor, without an unbounded receipt set.
        self.sequence += 1
        if kind != 'resource' and kind not in CATALOGS:
            raise SubscriptionError('Unknown catalog watch kind')
        watch = ResourceWatch(resource_uri(uri) if kind == 'resource' else '', -self.sequence, sink, stop, kind=kind)
        self.watches[watch.id] = watch
        return watch

    def add_notifications(self, selected: Any, sink: Callable[[dict[str, Any]], None],
                          stop: Callable[[ResourceWatch], Awaitable[None]]) -> ResourceWatch:
        filters = notification_filters(selected)
        if len(self.watches) >= 16:
            raise SubscriptionError('Subscription capacity reached')
        self.sequence += 1
        watch = NotificationWatch(filters, -self.sequence, sink, stop)
        self.watches[watch.id] = watch
        return watch

    def remove(self, watch: ResourceWatch, state: str = 'stopped', *, reason: str = '') -> None:
        if self.watches.get(watch.id) is watch:
            self.watches.pop(watch.id)
            watch.end(state, reason=reason)

    def close(self) -> None:
        for watch in tuple(self.watches.values()):
            self.remove(watch, 'disconnected')

    def legacy_notice(self, params: Any) -> None:
        if not isinstance(params, dict):
            raise SubscriptionError('Invalid resource update notification')
        uri = resource_uri(params.get('uri'))
        for watch in tuple(self.watches.values()):
            if watch.uri == uri:
                watch.changed()

    def legacy_catalog_notice(self, kind: str) -> None:
        for watch in tuple(self.watches.values()):
            if watch.kind == kind:
                watch.changed()

    def modern_frame(self, message: dict[str, Any]) -> bool:
        notification = 'method' in message
        params = message.get('params') if notification else message.get('result')
        identifier = subscription_identity(message)
        if type(identifier) is not int or identifier >= 0:
            return False
        watch = self.watches.get(identifier)
        if watch is None:
            if -identifier <= self.sequence:
                return True  # Retired/canceled stream: no mutation or replay.
            raise SubscriptionError('Unknown resource subscription identity')
        if message.get('jsonrpc') != '2.0':
            raise SubscriptionError('Invalid subscription JSON-RPC version')
        if notification:
            if 'result' in message or 'error' in message:
                raise SubscriptionError('Resource notification contains a response payload')
        else:
            if ('result' in message) == ('error' in message):
                raise SubscriptionError('Resource response must contain exactly one result or error')
            payload = message.get('error') if 'error' in message else params
            if not isinstance(payload, dict):
                raise SubscriptionError('Invalid resource subscription response payload')
            # Duplicate metadata is optional, but cannot disagree with the RPC ID.
            if '_meta' in payload:
                meta = payload['_meta']
                if not isinstance(meta, dict):
                    raise SubscriptionError('Invalid resource subscription response metadata')
                key = 'io.modelcontextprotocol/subscriptionId'
                if key in meta and (type(meta[key]) is not int or meta[key] != identifier):
                    raise SubscriptionError('Resource subscription response identity mismatch')
            if 'error' in message:
                if type(payload.get('code')) is not int or not isinstance(payload.get('message'), str):
                    raise SubscriptionError('Invalid resource subscription error response')
                self.remove(watch, 'failed')
                return True
        if not isinstance(params, dict):
            raise SubscriptionError('Invalid resource subscription payload')
        if notification:
            if 'id' in message:
                raise SubscriptionError('Resource subscription notification has a response identity')
            if message.get('method') == 'notifications/subscriptions/acknowledged' and watch.state == 'opening':
                notifications = params.get('notifications')
                expected = watch.filters()
                booleans = set(CATALOGS.values())
                if (not isinstance(notifications, dict)
                        or notifications.keys() - {'resourceSubscriptions'} - booleans
                        or any(type(notifications[key]) is not bool
                               or notifications[key] and expected.get(key) is not True
                               for key in booleans & notifications.keys())):
                    raise SubscriptionError('Subscription acknowledged an unrequested notification filter')
                actual_uris = notifications.get('resourceSubscriptions', [])
                expected_uris = expected.get('resourceSubscriptions', [])
                if (not isinstance(actual_uris, list) or len(actual_uris) > 16
                        or any(not isinstance(uri, str) or uri not in expected_uris for uri in actual_uris)
                        or len(set(actual_uris)) != len(actual_uris)
                        or actual_uris != [uri for uri in expected_uris if uri in actual_uris]):
                    raise SubscriptionError('Subscription acknowledged an unrequested/reordered URI filter')
                if any(notifications.get(key) != value for key, value in expected.items()):
                    self.remove(watch, 'unsupported', reason='Requested subscription filter was not acknowledged')
                    return True
                watch.activate()
            elif (watch.state == 'active' and any(
                    message.get('method') == 'notifications/' + kind + '/list_changed'
                    and watch.filters().get(flag) is True for kind, flag in CATALOGS.items())):
                watch.changed()
            elif (message.get('method') == 'notifications/resources/updated' and watch.state == 'active'
                    and params.get('uri') in watch.filters().get('resourceSubscriptions', [])):
                watch.changed()
            else:
                raise SubscriptionError('Unacknowledged/unrelated resource subscription notification')
        else:
            if message.get('id') != identifier or type(message.get('id')) is not int:
                raise SubscriptionError('Resource subscription response identity mismatch')
            if params.get('resultType', 'complete') != 'complete' or params.keys() - {'resultType', '_meta'}:
                raise SubscriptionError('Invalid resource subscription completion payload')
            if watch.state == 'active':
                self.remove(watch, 'ended')
            elif watch.state == 'opening':
                self.remove(watch, 'unsupported',
                            reason='Resource subscription closed before acknowledgement; not retried')
            else:
                raise SubscriptionError('Invalid resource subscription response ordering')
        return True
