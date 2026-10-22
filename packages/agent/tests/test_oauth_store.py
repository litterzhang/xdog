from __future__ import annotations

import json
import os
import time
from dataclasses import replace

import pytest
from xdog.agent.mcp import McpRegistry
from xdog.agent.mcp.oauth import OAuthConfig, OAuthError, OAuthOwner, OAuthReview
from xdog.agent.mcp.oauth_store import CredentialStore, capture, restore

RESOURCE = 'https://resource.test/mcp'
PROFILE = {'issuer': 'https://issuer.test', 'client_id': 'client', 'scopes': ['read']}


def seeded():
    owner = OAuthOwner(RESOURCE, OAuthConfig.parse(PROFILE))
    owner._token, owner._refresh = 'PRIVATE-ACCESS-TOKEN', 'PRIVATE-REFRESH-TOKEN'
    owner._expires = time.monotonic() + 1200
    owner._grant = OAuthReview('a' * 32, PROFILE['issuer'], RESOURCE, 'client', ('read',),
                              'https://issuer.test/authorize', 'https://issuer.test/token')
    return owner


def test_private_snapshot_roundtrip_no_secret_repr_and_profile_resource_expiry_bindings(tmp_path):
    owner = seeded()
    record = capture(owner)
    store = CredentialStore(tmp_path / 'private')
    store.save(record, lambda: True)
    file = next(store.directory.glob('*.json'))
    assert os.stat(store.directory).st_mode & 0o777 == 0o700
    assert os.stat(file).st_mode & 0o777 == 0o600
    loaded = store.read(RESOURCE)
    assert loaded == record and 'PRIVATE-' not in repr(loaded)
    assert 'PRIVATE-' not in json.dumps(loaded.public('restore'))
    fresh = OAuthOwner(RESOURCE, owner.config)
    assert fresh.credential() is None
    restore(fresh, loaded)
    assert fresh.credential() == owner.credential() and fresh.status()['state'] == 'authorized'
    with pytest.raises(OAuthError):
        restore(OAuthOwner('https://resource.test/other', owner.config), loaded)
    changed = replace(owner.config, scopes=('write',))
    with pytest.raises(OAuthError):
        restore(OAuthOwner(RESOURCE, changed), loaded)
    expired = replace(loaded, saved_at=time.time() - 2000, expires_at=time.time() - 100)
    restore(fresh, expired)
    assert fresh.credential() is None and fresh.status()['refresh_available']
    store.forget(RESOURCE)
    assert not list(store.directory.glob('*.json'))


def test_unsafe_modes_links_duplicates_unknown_fields_and_canceled_save_never_admit_tokens(tmp_path):
    owner, directory = seeded(), tmp_path / 'private'
    store = CredentialStore(directory)
    with pytest.raises(OAuthError):
        store.save(capture(owner), lambda: False)
    assert not list(directory.glob('*.json'))
    store.save(capture(owner), lambda: True)
    file = next(directory.glob('*.json'))
    file.chmod(0o644)
    with pytest.raises(OAuthError):
        store.read(RESOURCE)
    file.chmod(0o600)
    data = json.loads(file.read_text())
    data['review']['access_token'] = 'MUST-NOT-LEAK'
    file.write_text(json.dumps(data))
    with pytest.raises(OAuthError) as error:
        store.read(RESOURCE)
    assert 'MUST-NOT-LEAK' not in str(error.value)
    file.unlink()
    target = tmp_path / 'target'
    target.write_text('untouched')
    file.symlink_to(target)
    with pytest.raises(OAuthError):
        store.save(capture(owner), lambda: True)
    assert target.read_text() == 'untouched'
    linked = tmp_path / 'linked'
    linked.symlink_to(directory, target_is_directory=True)
    with pytest.raises(OAuthError):
        CredentialStore(linked / 'nested').save(capture(owner), lambda: True)
    assert not (directory / 'nested').exists()


async def test_manager_explicit_review_save_restart_restore_and_forget_have_no_network_or_implicit_load(tmp_path):
    configs = {'peer': {'transport': 'http', 'url': RESOURCE, 'oauth': PROFILE}}
    manager = McpRegistry(configs, tmp_path, credential_dir=tmp_path / 'private')
    fresh = None
    try:
        manager._oauth['peer'] = seeded()
        with pytest.raises(Exception):
            await manager.manage_auth('peer', 'credentials_save', 'unreviewed', lease='one')
        review = await manager.manage_auth('peer', 'credentials_save_prepare', lease='one')
        assert 'PLAINTEXT' in review['storage_notice'] and 'PRIVATE-' not in json.dumps(review)
        saved = await manager.manage_auth('peer', 'credentials_save', review['review_id'], lease='one')
        assert saved['state'] == 'authorized' and 'PRIVATE-' not in json.dumps(saved)
        await manager.aclose()
        fresh = McpRegistry(configs, tmp_path, credential_dir=tmp_path / 'private')
        assert not fresh._oauth and not fresh._clients
        loaded = await fresh.manage_auth('peer', 'credentials_restore_prepare', lease='two')
        assert fresh._oauth['peer'].credential() is None and not fresh._clients
        await fresh.manage_auth('peer', 'credentials_restore', loaded['review_id'], lease='two')
        assert fresh._oauth['peer'].credential() == 'PRIVATE-ACCESS-TOKEN' and not fresh._clients
        forgotten = await fresh.manage_auth('peer', 'credentials_forget_prepare', lease='two')
        await fresh.manage_auth('peer', 'credentials_forget', forgotten['review_id'], lease='two')
        assert not list((tmp_path / 'private').glob('*.json'))
        assert fresh._oauth['peer'].credential() == 'PRIVATE-ACCESS-TOKEN'  # Saved-file erase is not implicit revoke.
    finally:
        await manager.aclose()
        if fresh is not None:
            await fresh.aclose()


async def test_detached_or_foreign_storage_reviews_do_not_commit(tmp_path):
    manager = McpRegistry({'peer': {'transport': 'http', 'url': RESOURCE, 'oauth': PROFILE}}, tmp_path,
                         credential_dir=tmp_path / 'private')
    try:
        manager._oauth['peer'] = seeded()
        review = await manager.manage_auth('peer', 'credentials_save_prepare', lease='one')
        with pytest.raises(Exception):
            await manager.manage_auth('peer', 'credentials_save', review['review_id'], lease='foreign')
        await manager.detach_auth('one')
        with pytest.raises(Exception):
            await manager.manage_auth('peer', 'credentials_save', review['review_id'], lease='one')
        assert not (tmp_path / 'private').exists()
    finally:
        await manager.aclose()


async def test_saved_refresh_snapshot_is_invalidated_before_rotating_post_not_on_wrong_receipt(tmp_path):
    import httpx
    owner = seeded()
    store_dir = tmp_path / 'private'
    manager = McpRegistry({'peer': {'transport': 'http', 'url': RESOURCE, 'oauth': PROFILE}}, tmp_path,
                         credential_dir=store_dir)
    posts = []
    async def handler(request):
        assert not list(store_dir.glob('*.json'))
        posts.append(request)
        return httpx.Response(200, json={'access_token': 'ROTATED-ACCESS', 'refresh_token': 'ROTATED-REFRESH',
            'token_type': 'Bearer', 'expires_in': 900, 'scope': 'read'})
    owner.client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    manager._oauth['peer'] = owner
    try:
        saved = await manager.manage_auth('peer', 'credentials_save_prepare', lease='one')
        await manager.manage_auth('peer', 'credentials_save', saved['review_id'], lease='one')
        assert list(store_dir.glob('*.json'))
        reviewed = await manager.manage_auth('peer', 'refresh_prepare', lease='one')
        with pytest.raises(Exception):
            await manager.manage_auth('peer', 'refresh', 'wrong', lease='one')
        assert list(store_dir.glob('*.json')) and not posts
        await manager.manage_auth('peer', 'refresh', reviewed['review_id'], lease='one')
        assert owner._refresh == 'ROTATED-REFRESH' and len(posts) == 1
        assert not list(store_dir.glob('*.json'))  # Never implicitly save a rotated token.
    finally:
        await manager.aclose()


async def test_canceling_save_settles_local_writer_and_revokes_atomic_commit(tmp_path, monkeypatch):
    import asyncio
    import threading
    manager = McpRegistry({'peer': {'transport': 'http', 'url': RESOURCE, 'oauth': PROFILE}}, tmp_path,
                         credential_dir=tmp_path / 'private')
    entered, release = threading.Event(), threading.Event()
    original = manager.credential_store.save
    def blocked(record, guard):
        entered.set()
        release.wait(3)
        original(record, guard)
    monkeypatch.setattr(manager.credential_store, 'save', blocked)
    try:
        manager._oauth['peer'] = seeded()
        review = await manager.manage_auth('peer', 'credentials_save_prepare', lease='one')
        task = asyncio.create_task(manager.manage_auth('peer', 'credentials_save', review['review_id'], lease='one'))
        async with asyncio.timeout(3):
            while not entered.is_set():
                await asyncio.sleep(0.003)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()  # The actual file owner settles before releasing the transaction.
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not list((tmp_path / 'private').glob('*.json'))
    finally:
        release.set()
        await manager.aclose()
