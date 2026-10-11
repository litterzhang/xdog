"""Isolated-system-Python native Secret Service transport. No XDOG imports, unlock or Prompt call.

Run only by the explicitly selected host backend, with one bounded stdin request.
OS GI/libsecret are optional prerequisites; absence is refusal, not plaintext fallback.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any


def main() -> None:
    try:
        import gi  # type: ignore[import-not-found]  # OS prerequisite of this isolated-system-Python helper.
        gi.require_version('Secret', '1')
        from gi.repository import Gio, GLib, Secret  # type: ignore[import-not-found]
    except (ImportError, ValueError):
        raise ValueError('Native OS GI/libsecret prerequisite unavailable; no fallback') from None

    raw = sys.stdin.buffer.readline(256 * 1024 + 1)
    if len(raw) > 256 * 1024 or not raw.endswith(b'\n') or not os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
        raise ValueError('Invalid request/session bus')
    request = json.loads(raw)
    if not isinstance(request, dict) or request.get('action') not in ('read', 'save', 'forget'):
        raise ValueError('Invalid native operation')
    attrs = request.get('attributes')
    if (not isinstance(attrs, dict) or set(attrs) != {'xdog.namespace', 'xdog.resource', 'xdg:schema'}
            or attrs['xdg:schema'] != 'org.xdog.McpCredentials'
            or any(not isinstance(attrs[key], str) or len(attrs[key]) != 64
                   or any(char not in '0123456789abcdef' for char in attrs[key])
                   for key in ('xdog.namespace', 'xdog.resource'))):
        raise ValueError('Invalid native binding')
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    # Resolve an ALREADY RUNNING service, then pin its unique owner. Never activate
    # a daemon or switch to a replacement well-known owner after a race/disconnect.
    owner = connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
        'org.freedesktop.DBus', 'GetNameOwner', GLib.Variant('(s)', ('org.freedesktop.secrets',)),
        None, Gio.DBusCallFlags.NO_AUTO_START, 2000, None).unpack()[0]
    service = Secret.Service.open_sync(Secret.Service.__gtype__, owner, Secret.ServiceFlags.NONE, None)
    service.ensure_session_sync(None)
    if service.get_session_algorithms() != 'dh-ietf1024-sha256-aes128-cbc-pkcs7':
        raise ValueError('Encrypted native session unavailable')

    def call(path: str, interface: str, method: str, args: Any) -> Any:
        return connection.call_sync(owner, path, interface, method, args,
                                    None, Gio.DBusCallFlags.NO_AUTO_START, 3000, None)

    def locked(path: str, interface: str) -> Any:
        return call(path, 'org.freedesktop.DBus.Properties', 'Get',
                    GLib.Variant('(ss)', (interface, 'Locked'))).unpack()[0]

    collection = call('/org/freedesktop/secrets', 'org.freedesktop.Secret.Service', 'ReadAlias',
                      GLib.Variant('(s)', ('default',))).unpack()[0]
    if collection == '/' or locked(collection, 'org.freedesktop.Secret.Collection'):
        raise ValueError('Native collection locked/unavailable; unlock externally before requesting storage')
    unlocked, blocked = call('/org/freedesktop/secrets', 'org.freedesktop.Secret.Service', 'SearchItems',
                            GLib.Variant('(a{ss})', (attrs,))).unpack()
    if blocked or len(unlocked) > 1:
        raise ValueError('Locked/ambiguous native credential binding')
    if unlocked and not unlocked[0].startswith(collection + '/'):
        raise ValueError('Credential is not in the explicitly selected collection')
    action = request['action']
    if action == 'read':
        if not unlocked or locked(unlocked[0], 'org.freedesktop.Secret.Item'):
            raise ValueError('Saved native credential unavailable')
        result = call(unlocked[0], 'org.freedesktop.Secret.Item', 'GetSecret',
                      GLib.Variant('(o)', (service.get_session_dbus_path(),)))
        value = service.decode_dbus_secret(result.get_child_value(0))
        if value is None:
            raise ValueError('Native secret decode failed')
        payload = bytes(value.get())
        if len(payload) > 64 * 1024:
            raise ValueError('Native credential exceeds capacity')
        print(json.dumps({'ok': True, 'payload': payload.decode('utf-8')}, ensure_ascii=True), flush=True)
        return
    print(json.dumps({'ready': True}), flush=True)
    # Native modifications require a second host-owned admission after preflight.
    admission = sys.stdin.buffer.readline(128)
    if admission != b'{"commit":true}\n':
        raise ValueError('Native modification not admitted')
    if locked(collection, 'org.freedesktop.Secret.Collection'):
        raise ValueError('Native collection locked after preflight')
    if action == 'save':
        saving = request.get('payload')
        if not isinstance(saving, str) or len(saving.encode()) > 64 * 1024:
            raise ValueError('Invalid native credential payload')
        properties = {'org.freedesktop.Secret.Item.Label': GLib.Variant('s', 'XDOG reviewed MCP credential'),
                      'org.freedesktop.Secret.Item.Attributes': GLib.Variant('a{ss}', attrs)}
        value = Secret.Value.new(saving, len(saving.encode()), 'application/json')
        secret = service.encode_dbus_secret(value)
        result = call(collection, 'org.freedesktop.Secret.Collection', 'CreateItem',
            GLib.Variant.new_tuple(GLib.Variant('a{sv}', properties), secret, GLib.Variant('b', True)))
        _item, prompt = result.unpack()
    elif not unlocked:
        print(json.dumps({'ok': True}), flush=True)
        return
    else:
        if locked(unlocked[0], 'org.freedesktop.Secret.Item'):
            raise ValueError('Native item locked after preflight')
        prompt = call(unlocked[0], 'org.freedesktop.Secret.Item', 'Delete', None).unpack()[0]
    if prompt != '/':
        # CreateItem/Delete may offer a prompt if the collection locks in-flight.
        # Never call Prompt; dismiss/refuse instead of launching a desktop dialog.
        call(prompt, 'org.freedesktop.Secret.Prompt', 'Dismiss', None)
        raise ValueError('Native operation required an unreviewed prompt; refused')
    print(json.dumps({'ok': True}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Never echo exception text, requests, tokens, environment or vault secrets.
        print(json.dumps({'ok': False, 'error': 'Native storage unavailable/locked/unsafe; no fallback'}), flush=True)
        sys.exit(1)
