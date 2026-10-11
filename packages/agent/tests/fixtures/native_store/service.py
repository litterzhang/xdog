"""Owned private bus peer. Environment is isolated BEFORE bus activation, never a user keyring."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import gi

gi.require_version('Secret', '1')
from gi.repository import Gio, GLib  # noqa: E402 -- GI version must be selected first.

root = Path(os.environ['XDOG_NATIVE_TEST_ROOT'])
log = (root / 'service.log').open('wb')
daemon = subprocess.Popen(['/usr/bin/gnome-keyring-daemon', '--foreground', '--unlock', '--components=secrets',
    '--control-directory', str(root / 'control')], stdin=subprocess.PIPE, stdout=log, stderr=log,
    start_new_session=True)
try:
    daemon.stdin.write(b'owned-offline-keyring-password')
    daemon.stdin.close()
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    for _ in range(80):
        try:
            connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                'GetNameOwner', GLib.Variant('(s)', ('org.freedesktop.secrets',)), None,
                Gio.DBusCallFlags.NO_AUTO_START, 1000, None)
            break
        except GLib.Error:
            time.sleep(0.025)
    else:
        raise RuntimeError('Owned keyring did not initialize')
    process = subprocess.run([sys.argv[1], '-I', str(Path(__file__).with_name('client.py'))], timeout=30,
                             capture_output=True)
    sys.stdout.buffer.write(process.stdout)
    sys.stderr.buffer.write(process.stderr)
    if process.returncode:
        raise SystemExit(process.returncode)
    # No automatic Prompt/Unlock may have activated a prompter on THIS bus.
    names = connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
        'ListNames', None, None, Gio.DBusCallFlags.NO_AUTO_START, 1000, None).unpack()[0]
    assert 'org.gnome.keyring.SystemPrompter' not in names
    files = [p for p in (root / 'data').rglob('*') if p.is_file()]
    assert files and all(b'PRIVATE-NATIVE-DUMMY' not in p.read_bytes() for p in files)
    print(json.dumps({'native_owned_service': True, 'no_prompt_service': True,
                      'dummy_plaintext_absent': True, 'service_files': len(files)}))
finally:
    os.killpg(daemon.pid, signal.SIGTERM)
    try:
        daemon.wait(timeout=3)
    except subprocess.TimeoutExpired:
        os.killpg(daemon.pid, signal.SIGKILL)
        daemon.wait(timeout=3)
    log.close()
