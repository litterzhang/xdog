from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from xdog.tui import attachments
from xdog.tui.attachments import AttachmentReader
from xdog.tui.clipboard_macos import MACOS_SCRIPT, MAX_TEXT_BYTES, read_macos_clipboard

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=')
ENCODED = base64.b64encode(PNG).decode()


def test_codec_invokes_static_native_program_with_bounds_without_shell_or_pbpaste():
    calls = []
    def command(args, *, maximum):
        calls.append((args, maximum))
        return json.dumps({'kind': 'image', 'data': ENCODED}).encode()
    assert read_macos_clipboard(command, '/usr/bin/osascript', image_limit=100) == ('image', PNG)
    assert calls == [(['/usr/bin/osascript', '-l', 'JavaScript', '-e', MACOS_SCRIPT, '--',
                      '100', str(MAX_TEXT_BYTES)], 264)]


@pytest.mark.parametrize('reply', [b'not json SECRET', b'[]', b'null', b'{"kind":"image","data":""}',
    b'{"kind":"file","data":"L3RtcC9zZWNyZXQ="}', b'{"kind":"image","data":"not-base64-SECRET"}',
    b'{"kind":"text","data":false}', b'{"kind":"image","data":"YQ==","extra":"SECRET"}',
    json.dumps({'kind': 'image', 'data': ENCODED}).encode(),
    json.dumps({'kind': 'text', 'data': base64.b64encode(b'x' * (MAX_TEXT_BYTES + 1)).decode()}).encode()])
def test_codec_rejects_invalid_or_oversized_results_without_echoing_contents(reply):
    with pytest.raises(ValueError) as caught:
        read_macos_clipboard(lambda args, maximum: reply, 'osascript', image_limit=16)
    assert 'SECRET' not in str(caught.value)


def native(config):
    node = shutil.which('node')
    if not node:
        pytest.skip('native JXA program contract requires Node; actual macOS validation remains separate')
    prelude = (Path(__file__).parent / 'fixtures/macos_clipboard_bridge.js').read_text()
    suffix = "\ntry { console.log(JSON.stringify({result: JSON.parse(run(['100','1048576'])), calls})); } "
    suffix += "catch (error) { console.log(JSON.stringify({error: error.message, calls})); }"
    result = subprocess.run([node, '-e', prelude + '\n' + MACOS_SCRIPT + suffix],
                            env={**os.environ, 'XDOG_NATIVE_PEER': json.dumps(config)},
                            capture_output=True, text=True, check=True, timeout=5)
    return json.loads(result.stdout)


@pytest.mark.parametrize('uti', ['public.png', 'public.jpeg', 'org.webmproject.webp'])
def test_native_program_reads_binary_types_before_text_without_conversion(uti):
    value = native({'types': [uti], 'bytes': ENCODED, 'text': 'not the selected image'})
    assert value['result'] == {'kind': 'image', 'data': ENCODED}
    assert ['read', uti] in value['calls']
    assert not any(call[0] in ('bitmap', 'text') for call in value['calls'])


def test_native_program_png_priority_tiff_conversion_and_plain_or_empty_unicode_text():
    png = native({'types': ['public.tiff', 'public.jpeg', 'public.png'], 'bytes': ENCODED})
    assert ['read', 'public.png'] in png['calls']
    tiff = native({'types': ['public.tiff'], 'bytes': 'VElGRg==', 'png': ENCODED})
    assert tiff['result'] == {'kind': 'image', 'data': ENCODED}
    assert ['png', 4, {}] in tiff['calls']
    for text in ('', '多行\nclipboard 🙂'):
        value = native({'text': text})
        assert base64.b64decode(value['result']['data']).decode() == text
    assert native({})['result'] == {'kind': 'text', 'data': ''}


@pytest.mark.parametrize('config,error', [
    ({'types': ['public.png'], 'bytes': ENCODED, 'changed': True}, 'changed'),
    ({'types': ['public.png'], 'missing': True, 'text': 'must not fall back'}, 'unavailable'),
    ({'types': ['public.png'], 'length': 101}, 'size limit'),
    ({'types': ['public.tiff'], 'bytes': 'VElGRg==', 'invalidBitmap': True}, 'conversion'),
    ({'types': ['public.tiff'], 'bytes': 'VElGRg==', 'invalidConversion': True}, 'unavailable'),
    ({'types': ['public.tiff'], 'bytes': 'VElGRg==', 'width': 0}, 'pixel limit'),
    ({'types': ['public.tiff'], 'bytes': 'VElGRg==', 'width': 10001, 'height': 10000}, 'pixel limit'),
    ({'types': ['public.tiff'], 'bytes': 'VElGRg==', 'png': ENCODED, 'convertedLength': 101}, 'size limit'),
    ({'text': 'SECRET', 'length': MAX_TEXT_BYTES + 1}, 'size limit'),
    ({'text': 'SECRET', 'changed': True}, 'changed')])
def test_native_program_refuses_changes_invalid_images_and_bounds_before_output(config, error):
    value = native(config)
    assert error in value['error'] and 'result' not in value
    assert 'SECRET' not in value['error']
    assert not any(call[0] == 'base64' for call in value['calls'])


def helper(tmp_path, response):
    binary = tmp_path / 'osascript'
    binary.write_text(f'#!{sys.executable}\nimport sys\nassert sys.argv[1:4] == ["-l", "JavaScript", "-e"]\n'
                      f'sys.stdout.buffer.write({response!r})\n')
    binary.chmod(0o700)
    return binary


def mac_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(attachments, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setenv('PATH', str(tmp_path))
    monkeypatch.setenv('WAYLAND_DISPLAY', 'must-not-query-a-different-clipboard')
    monkeypatch.setenv('DISPLAY', 'must-not-query-a-different-clipboard')


def test_reader_actual_owned_helper_image_unicode_text_and_empty_are_off_terminal_thread(tmp_path, monkeypatch):
    mac_environment(tmp_path, monkeypatch)
    for expected in (('image', ENCODED), ('text', base64.b64encode('多行\n🙂'.encode()).decode()), ('text', '')):
        helper(tmp_path, json.dumps(dict(zip(('kind', 'data'), expected))).encode())
        events, delivered = [], threading.Event()
        def publish(event):
            assert threading.current_thread() is not threading.main_thread()
            events.append(event)
            delivered.set()
        reader = AttachmentReader(publish)
        try:
            assert reader.read(None, 7)
            assert delivered.wait(3)
            assert events[0]['epoch'] == 7 and 'error' not in events[0]
            if expected[0] == 'image':
                assert events[0]['image'].data == PNG and events[0]['text'] == ''
            else:
                assert events[0]['text'] == base64.b64decode(expected[1]).decode()
        finally:
            reader.close()
            reader._thread.join(3)
            assert not reader._thread.is_alive() and reader._process is None


def test_native_failure_does_not_silently_query_text_or_log_helper_contents(tmp_path, monkeypatch):
    mac_environment(tmp_path, monkeypatch)
    helper(tmp_path, b'BROKEN-CLIPBOARD-SECRET')
    fallback = tmp_path / 'pbpaste'
    fallback.write_text(f'#!{sys.executable}\nraise AssertionError("Must not retry clipboard as text")\n')
    fallback.chmod(0o700)
    with pytest.raises(ValueError, match='macOS clipboard snapshot') as caught:
        AttachmentReader(lambda event: None).clipboard()
    assert 'BROKEN-CLIPBOARD-SECRET' not in str(caught.value)


def test_text_only_fallback_requires_native_helper_absence(tmp_path, monkeypatch):
    mac_environment(tmp_path, monkeypatch)
    reader = AttachmentReader(lambda event: None)
    with pytest.raises(ValueError, match='helpers unavailable'):
        reader.clipboard()
    binary = tmp_path / 'pbpaste'
    binary.write_text(f'#!{sys.executable}\nprint("text-only fallback")\n')
    binary.chmod(0o700)
    assert reader.clipboard().text == 'text-only fallback\n'


def test_closing_macos_helper_reaps_process_and_never_publishes_late_clipboard(tmp_path, monkeypatch):
    mac_environment(tmp_path, monkeypatch)
    binary = tmp_path / 'osascript'
    binary.write_text(f'#!{sys.executable}\nimport time\ntime.sleep(60)\n')
    binary.chmod(0o700)
    events = []
    reader = AttachmentReader(events.append)
    assert reader.read(None, 0)
    deadline = time.monotonic() + 3
    while reader._process is None and time.monotonic() < deadline:
        time.sleep(0.005)
    process = reader._process
    assert process is not None
    reader.close()
    reader._thread.join(3)
    assert not reader._thread.is_alive() and process.poll() is not None and not events
