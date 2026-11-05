"""Offline CDN/crypto/filesystem and actual channel-to-Core-Inbox image coverage."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from PIL import Image
from xdog.claw.channels.weixin import media
from xdog.claw.channels.weixin.channel import WeixinChannel, _TypingTicketCache
from xdog.claw.channels.weixin.monitor import _load_get_updates_buf, _save_get_updates_buf
from xdog.claw.channels.weixin.types import (
    CDNMedia,
    GetUpdatesResp,
    ImageItem,
    MessageItem,
    MessageItemType,
    TextItem,
    parse_weixin_message,
)
from xdog.claw.config import ClawConfig
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group

from packages.claw.tests.eyes_helpers import ScriptedModel
from packages.claw.tests.weixin_application_intake_helpers import ChannelApi
from packages.claw.tests.weixin_monitor_helpers import user_message, wait

KEY = bytes(range(16))


def png(color='red'):
    stream = io.BytesIO()
    Image.new('RGB', (8, 8), color).save(stream, format='PNG')
    return stream.getvalue()


def encrypted(value):
    padder = padding.PKCS7(128).padder()
    padded = padder.update(value) + padder.finalize()
    encryptor = Cipher(algorithms.AES(KEY), modes.ECB()).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def image_item(**kwargs):
    return ImageItem(media=CDNMedia(encrypt_query_param='signed/locator?secret=fixture',
                                    aes_key=base64.b64encode(KEY).decode()), **kwargs)


@pytest.mark.parametrize('encoding', ['raw-base64', 'plain'])
async def test_cdn_download_key_variants_and_no_auth_or_signed_url_logs(encoding, caplog):
    import logging

    caplog.set_level(logging.INFO, logger='httpx')
    value = png()
    item = image_item()
    if encoding == 'hex-base64':
        item = replace(item, media=replace(item.media, aes_key=base64.b64encode(KEY.hex().encode()).decode()))
    elif encoding == 'preferred-hex':
        item = replace(item, aeskey=KEY.hex(), media=replace(item.media, aes_key='invalid-ignored'))
    elif encoding == 'plain':
        item = replace(item, media=replace(item.media, aes_key='',
                       full_url='https://novac2c.cdn.weixin.qq.com/c2c/download?token=fixture'))
    calls = []
    def respond(request):
        calls.append(request)
        assert request.method == 'GET'
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        if encoding != 'plain':
            assert request.url.params['encrypted_query_param'] == 'signed/locator?secret=fixture'
        return httpx.Response(200, content=value if encoding == 'plain' else encrypted(value))
    assert await media.download_image(item, transport=httpx.MockTransport(respond)) == value
    assert len(calls) == 1 and 'signed/locator' not in caplog.text and 'token=fixture' not in caplog.text


@pytest.mark.parametrize('url', ['https://evil.example/x', 'http://novac2c.cdn.weixin.qq.com/x', 'https://user:password@novac2c.cdn.weixin.qq.com/x'])
async def test_untrusted_media_urls_are_rejected_without_fetch(url):
    item = replace(image_item(), media=CDNMedia(full_url=url))
    def forbidden(request):
        pytest.fail('Untrusted URL was fetched')
    with pytest.raises(ValueError):
        await media.download_image(item, transport=httpx.MockTransport(forbidden))


@pytest.mark.parametrize('status', [302])
async def test_cdn_errors_do_not_follow_redirects_or_expose_locators(status):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(status, headers={'location': 'https://127.0.0.1/private'})
    with pytest.raises(ValueError, match=f'HTTP {status}') as error:
        await media.download_image(image_item(), transport=httpx.MockTransport(respond))
    assert len(calls) == 1 and 'signed' not in str(error.value)


async def test_invalid_key_ciphertext_and_download_limits(monkeypatch):
    def forbidden(request):
        pytest.fail('Bad key reached the network')
    with pytest.raises(ValueError, match='key'):
        await media.download_image(replace(image_item(), aeskey='not-a-key'),
                                   transport=httpx.MockTransport(forbidden))
    with pytest.raises(ValueError, match='decryption failed'):
        await media.download_image(image_item(), transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b'invalid-ciphertext')))
    monkeypatch.setattr(media, 'MAX_IMAGE_BYTES', 32)
    with pytest.raises(ValueError, match='download limit'):
        await media.download_image(image_item(), transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b'x' * 49)))


def test_save_prefix_format_permissions_deduplication_and_group_isolation(tmp_path):
    value = png()
    path = media.save_image(tmp_path/'main'/'sessions'/'images', value)
    assert path.name == hashlib.md5(value).hexdigest()[:8] + '.png'
    assert path.read_bytes() == value and path.stat().st_mode & 0o777 == 0o600
    inode = path.stat().st_ino
    assert media.save_image(path.parent, value) == path and path.stat().st_ino == inode
    other = media.save_image(tmp_path/'other'/'sessions'/'images', value)
    assert other != path and other.read_bytes() == value
    assert not list(path.parent.glob('*.tmp'))


def test_short_hash_collision_symlink_and_invalid_content_never_overwrite(tmp_path):
    value = png()
    directory = tmp_path/'images'
    directory.mkdir()
    name = hashlib.md5(value).hexdigest()[:8] + '.png'
    existing = directory/name
    existing.write_bytes(b'preserve')
    with pytest.raises(ValueError, match='collision'):
        media.save_image(directory, value)
    assert existing.read_bytes() == b'preserve'
    existing.unlink()
    target = tmp_path/'private'
    target.write_bytes(b'private')
    existing.symlink_to(target)
    with pytest.raises(OSError):
        media.save_image(directory, value)
    assert target.read_bytes() == b'private' and not list(directory.glob('*.tmp'))
    with pytest.raises(ValueError, match='valid supported image'):
        media.save_image(directory, b'not an image')
    link = tmp_path/'symlink-dir'
    link.symlink_to(directory, target_is_directory=True)
    with pytest.raises(OSError):
        media.save_image(link, value)


def test_parser_preserves_full_url_and_plaintext_is_validated(tmp_path):
    msg = parse_weixin_message({'item_list': [{'type': 2, 'image_item': {
        'media': {'full_url': 'https://novac2c.cdn.weixin.qq.com/x'}}}]})
    assert msg.item_list[0].image_item.media.full_url.endswith('/x')
    path = media.save_image(tmp_path/'images', png())
    assert path.suffix == '.png'


def inbound(*, text='', count=1):
    items = ((MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text=text)),) if text else ())
    items += tuple(MessageItem(type=MessageItemType.IMAGE, image_item=image_item()) for _ in range(count))
    return replace(user_message(9), from_user_id='actual-sender', context_token='private-context', item_list=items)


@pytest.mark.parametrize('text', ['', '请看看这张图片'])
async def test_standalone_images_and_text_become_path_only_input(tmp_path, monkeypatch, text):
    channel = WeixinChannel(tmp_path, 'account', 'https://example.invalid', '', group_id='cats')
    download = AsyncMock(return_value=png())
    monkeypatch.setattr(media, 'download_image', download)
    channel._start_typing = AsyncMock()
    channel._stop_typing = AsyncMock()
    received = []
    async def accept(value):
        received.append(value)
    channel.set_on_message(accept)
    try:
        await channel._on_inbound(inbound(text=text, count=2))
        assert len(received) == 1 and download.await_count == 2
        value = received[0]
        expected = tmp_path/'groups'/'cats'/'sessions'/'images'/(hashlib.md5(png()).hexdigest()[:8]+'.png')
        assert value.images == () and value.group_id == 'cats' and value.sender == 'actual-sender'
        assert value.content.count(f'The user uploaded an image, saved at: {expected}') == 2
        assert (value.content.startswith(text+'\n\n')) if text else value.content.startswith('The user uploaded')
        assert 'signed/locator' not in value.content and 'private-context' not in value.content
        assert len(list(expected.parent.glob('*.png'))) == 1
    finally:
        await channel.disconnect()


async def actual_application(tmp_path, *, defer=True):
    model = ScriptedModel()
    root = tmp_path/'data'
    app = Orchestrator(ClawConfig(data_dir=str(root)), model='test/dummy', stream_fn=model, defer_inputs=defer)
    app.register_group(Group(id='main', workspace=str(tmp_path/'workspace'), enabled_tools=()))
    channel = WeixinChannel(root, 'account', 'https://example.invalid', '')
    api = ChannelApi()
    channel._api_client = api
    channel._typing_tickets = _TypingTicketCache(api)
    app.add_channel(channel)
    return app, channel, api, model


async def test_file_commit_and_inbox_precede_cursor_and_dispatch_never_redownloads(tmp_path, monkeypatch):
    app, channel, api, model = await actual_application(tmp_path)
    owner = threading.get_ident()
    threads = []
    save = media.save_image
    def observed(directory, value):
        threads.append(threading.get_ident())
        return save(directory, value)
    monkeypatch.setattr(media, 'save_image', observed)
    download = AsyncMock(return_value=png())
    monkeypatch.setattr(media, 'download_image', download)
    message = inbound(text='保留文字')
    api.updates.put_nowait(GetUpdatesResp(msgs=(message,), get_updates_buf='image-committed'))
    await channel.connect()
    try:
        assert (await wait(api.polls.get()))[0] == ''
        assert (await wait(api.polls.get()))[0] == 'image-committed'
        scope = InputScope('main', tmp_path/'workspace')
        records = await (await app.input_service()).store.records(scope)
        assert len(records) == 1 and records[0].status == 'pending' and model.calls == []
        body = records[0].envelope.message.instruction
        path = Path(body.split('saved at: ', 1)[1])
        assert path.parent == channel._state_dir/'groups'/'main'/'sessions'/'images'
        assert path.read_bytes() == png() and records[0].envelope.images == ()
        assert records[0].envelope.reply.recipient == message.from_user_id
        assert json.loads(records[0].envelope._context_json)['identity']['principal']['role'] == 'external_user'
        assert threads and all(thread != owner for thread in threads)
        # Same already-committed object goes to presentation with the exact input.
        await asyncio.sleep(0)
        assert download.await_count == 1
        assert not channel._prepared_images
    finally:
        await app.stop()


async def test_failed_image_keeps_original_cursor_and_no_text_only_partial_input(tmp_path, monkeypatch):
    import xdog.claw.channels.weixin.monitor as monitor

    monkeypatch.setattr(monitor, 'RETRY_DELAY_S', 0.001)
    app, channel, api, model = await actual_application(tmp_path)
    download = AsyncMock(side_effect=ValueError('fixture download failed'))
    monkeypatch.setattr(media, 'download_image', download)
    _save_get_updates_buf(channel._state_dir, 'account', 'original')
    api.updates.put_nowait(GetUpdatesResp(msgs=(inbound(text='text plus failed image'),), get_updates_buf='new'))
    await channel.connect()
    try:
        assert (await wait(api.polls.get()))[0] == 'original'
        assert (await wait(api.polls.get()))[0] == 'original'
        assert _load_get_updates_buf(channel._state_dir, 'account') == 'original'
        records = await (await app.input_service()).store.records(InputScope('main', tmp_path/'workspace'))
        assert not records and not model.calls and not list(channel._media_dir.glob('*'))
    finally:
        await app.stop()


async def test_core_receipt_recovers_original_paths_after_restart_with_expired_cdn(tmp_path, monkeypatch):
    app, channel, api, model = await actual_application(tmp_path)
    download = AsyncMock(return_value=png())
    monkeypatch.setattr(media, 'download_image', download)
    message = inbound(text='original')
    scope = InputScope('main', tmp_path/'workspace')
    try:
        await channel._accept_inbound(message)
        original = (await (await app.input_service()).store.records(scope))[0].envelope
        assert download.await_count == 1
    finally:
        await app.stop()
    app2, channel2, api2, model2 = await actual_application(tmp_path)
    download.side_effect = ValueError('CDN already expired; must not fetch')
    try:
        await channel2._accept_inbound(replace(message, context_token='later-token'))
        rows = await (await app2.input_service()).store.records(scope)
        assert len(rows) == 1 and rows[0].envelope == original
        assert download.await_count == 1 and model2.calls == []
        changed = replace(message, item_list=(
            MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text='edited')), *message.item_list[1:]))
        with pytest.raises(ValueError, match='differs from the original'):
            await channel2._accept_inbound(changed)
        assert download.await_count == 1
    finally:
        await app2.stop()


async def test_cancellation_joins_file_io_without_acking_input_or_cursor(tmp_path, monkeypatch):
    app, channel, api, model = await actual_application(tmp_path)
    monkeypatch.setattr(media, 'download_image', AsyncMock(return_value=png()))
    entered, release = threading.Event(), threading.Event()
    save = media.save_image
    def held(directory, value):
        path = save(directory, value)
        entered.set()
        assert release.wait(5)
        return path
    monkeypatch.setattr(media, 'save_image', held)
    _save_get_updates_buf(channel._state_dir, 'account', 'original')
    operation = asyncio.create_task(channel._accept_inbound(inbound()))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        operation.cancel()
        await asyncio.sleep(0.01)
        assert not operation.done(), 'Cancellation returned while the file writer was still active'
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        rows = await (await app.input_service()).store.records(InputScope('main', tmp_path/'workspace'))
        assert not rows and _load_get_updates_buf(channel._state_dir, 'account') == 'original'
        assert len(list(channel._media_dir.glob('*.png'))) == 1
        assert not list(channel._media_dir.glob('*.tmp'))
    finally:
        release.set()
        await asyncio.gather(operation, return_exceptions=True)
        await app.stop()


async def test_only_direct_image_items_download_not_quoted_images(tmp_path, monkeypatch):
    from xdog.claw.channels.weixin.types import RefMessage

    channel = WeixinChannel(tmp_path, 'account', 'https://example.invalid', '')
    download = AsyncMock(side_effect=AssertionError('Quoted image must not be fetched'))
    monkeypatch.setattr(media, 'download_image', download)
    item = MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text='current reply'),
        ref_msg=RefMessage(message_item=MessageItem(type=MessageItemType.IMAGE, image_item=image_item())))
    try:
        message = await channel._prepare_inbound(replace(user_message(1), item_list=(item,)), durable=True)
        assert message.content == 'current reply' and not message.images
        assert download.await_count == 0
    finally:
        await channel.disconnect()


async def test_actual_agent_receives_original_text_and_saved_path_once(tmp_path, monkeypatch):
    app, channel, api, model = await actual_application(tmp_path, defer=False)
    monkeypatch.setattr(media, 'download_image', AsyncMock(return_value=png()))
    api.updates.put_nowait(GetUpdatesResp(msgs=(inbound(text='这是什么'),), get_updates_buf='image'))
    await channel.connect()
    try:
        sent = await wait(api.sent.get())
        scope = InputScope('main', tmp_path/'workspace')
        service = await app.input_service()
        rows = await service.store.records(scope)
        record = await wait(service.wait(scope, rows[0].envelope.message.id))
        body = record.envelope.message.instruction
        assert body.startswith('这是什么\n\nThe user uploaded an image, saved at: ')
        assert model.calls == [body] and record.envelope.images == ()
        assert sent.to_user_id == 'actual-sender' and sent.context_token == 'private-context'
        path = Path(body.split('saved at: ', 1)[1])
        assert path.is_file() and path.read_bytes() == png()
        transcripts = list((channel._state_dir/'groups'/'main'/'sessions').glob('*.jsonl'))
        assert transcripts and str(path) in transcripts[0].read_text()
        assert 'signed/locator' not in transcripts[0].read_text()
    finally:
        await app.stop()


async def test_download_gate_does_not_ack_or_dispatch_before_image_exists(tmp_path, monkeypatch):
    app, channel, api, model = await actual_application(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    async def download(item):
        entered.set()
        await release.wait()
        return png()
    monkeypatch.setattr(media, 'download_image', download)
    _save_get_updates_buf(channel._state_dir, 'account', 'original')
    api.updates.put_nowait(GetUpdatesResp(msgs=(inbound(),), get_updates_buf='next'))
    await channel.connect()
    try:
        await wait(entered.wait())
        assert _load_get_updates_buf(channel._state_dir, 'account') == 'original'
        assert not list(channel._media_dir.glob('*'))
        assert not await (await app.input_service()).store.records(InputScope('main', tmp_path/'workspace'))
        release.set()
        assert (await wait(api.polls.get()))[0] == 'original'
        assert (await wait(api.polls.get()))[0] == 'next'
        records = await (await app.input_service()).store.records(InputScope('main', tmp_path/'workspace'))
        assert len(records) == 1 and 'The user uploaded an image' in records[0].envelope.message.instruction
    finally:
        release.set()
        await app.stop()
