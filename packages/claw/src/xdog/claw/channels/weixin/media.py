"""Bounded Weixin image intake: CDN/decryption, then immutable group-owned files.

Protocol reference: Tencent's @tencent-weixin/openclaw-weixin 2.4.10,
src/cdn/{cdn-url,pic-decrypt,aes-ecb}.ts and src/media/media-download.ts.
No Agent, inbox, session lifecycle, or outbound message policy lives here.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import os
import re
import stat
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from PIL import Image

from .types import ImageItem

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGES = 16
MAX_MESSAGE_BYTES = 64 * 1024 * 1024
_CDN_BASE = 'https://novac2c.cdn.weixin.qq.com/c2c'
_EXTENSIONS = {'PNG': '.png', 'JPEG': '.jpg', 'GIF': '.gif', 'WEBP': '.webp', 'BMP': '.bmp', 'TIFF': '.tiff'}


def _source(item: ImageItem) -> tuple[str, bytes | None]:
    media = item.media
    if media is None:
        raise ValueError('Weixin image has no original CDN reference')
    url = media.full_url
    if not url:
        query = media.encrypt_query_param
        if not isinstance(query, str) or not query or len(query) > 8192:
            raise ValueError('Weixin image has no bounded CDN locator')
        url = _CDN_BASE + '/download?encrypted_query_param=' + quote(query, safe='')
    if not isinstance(url, str) or len(url) > 16 * 1024 or any(ord(char) < 32 or ord(char) == 127 for char in url):
        raise ValueError('Invalid Weixin image CDN URL')
    try:
        target = urlsplit(url)
        host = target.hostname or ''
        valid = (target.scheme == 'https' and not target.username and not target.password and not target.fragment
                 and target.port in (None, 443)
                 and (host.endswith('.weixin.qq.com') or host.endswith('.qpic.cn')))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('Weixin image requires an HTTPS Weixin CDN address')
    try:
        if item.aeskey:
            if not isinstance(item.aeskey, str) or re.fullmatch(r'[0-9a-fA-F]{32}', item.aeskey) is None:
                raise ValueError
            key = bytes.fromhex(item.aeskey)
        elif media.aes_key:
            if not isinstance(media.aes_key, str) or len(media.aes_key) > 256:
                raise ValueError
            key = base64.b64decode(media.aes_key, validate=True)
            if len(key) == 32 and re.fullmatch(rb'[0-9a-fA-F]{32}', key):
                key = bytes.fromhex(key.decode('ascii'))
            if len(key) != 16:
                raise ValueError
        else:
            key = None  # The upstream protocol also allows unencrypted CDN images.
    except (ValueError, TypeError):
        raise ValueError('Invalid Weixin image decryption key') from None
    return url, key


async def download_image(item: ImageItem, *, transport: httpx.AsyncBaseTransport | None = None) -> bytes:
    url, key = _source(item)
    try:
        async with asyncio.timeout(60):
            # Use the transport directly: CDN requests inherit neither bot auth nor
            # proxy settings, and signed URLs never enter httpx's INFO request log.
            async with transport or httpx.AsyncHTTPTransport(retries=0) as connection:
                request = httpx.Request('GET', url, extensions={'timeout': {
                    'connect': 20, 'read': 30, 'write': 30, 'pool': 20}})
                response = await connection.handle_async_request(request)
                try:
                    if response.status_code != 200:  # Never follow remote redirects.
                        raise ValueError(f'Weixin image download failed (HTTP {response.status_code})')
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > MAX_IMAGE_BYTES + 16:
                            raise ValueError('Weixin image exceeds the download limit')
                finally:
                    await response.aclose()
    except (httpx.HTTPError, TimeoutError):
        raise ValueError('Weixin image CDN download unavailable') from None
    if key is not None:
        try:
            decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
            padded = decryptor.update(bytes(raw)) + decryptor.finalize()
            unpadder = padding.PKCS7(128).unpadder()
            value = unpadder.update(padded) + unpadder.finalize()
        except ValueError:
            raise ValueError('Weixin image decryption failed') from None
    else:
        value = bytes(raw)
    if not value or len(value) > MAX_IMAGE_BYTES:
        raise ValueError('Weixin image is empty or exceeds the size limit')
    return value


def save_image(directory: Path, value: bytes) -> Path:
    """Publish a verified image atomically; an eight-hex collision never overwrites."""
    if not value or len(value) > MAX_IMAGE_BYTES:
        raise ValueError('Weixin image is empty or exceeds the size limit')
    try:
        with Image.open(io.BytesIO(value)) as image:
            extension = _EXTENSIONS.get(image.format or '')
            if extension is None or image.width * image.height > 32_000_000:
                raise ValueError('Unsupported image format or dimensions')
            image.verify()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise ValueError('Weixin download is not a valid supported image') from None
    # MD5 is only the user-requested filename, not a trust or integrity boundary.
    name = hashlib.md5(value, usedforsecurity=False).hexdigest()[:8] + extension
    directory = directory.absolute()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    temporary = '.' + uuid.uuid4().hex + '.tmp'
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, name, src_dir_fd=folder, dst_dir_fd=folder, follow_symlinks=False)
        except FileExistsError:
            existing = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
            with os.fdopen(existing, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode) or stream.read(MAX_IMAGE_BYTES + 1) != value:
                    raise ValueError('Weixin image short-hash collision; existing file was not overwritten')
        os.fsync(folder)
    finally:
        try:
            os.unlink(temporary, dir_fd=folder)
        finally:
            os.close(folder)
    return directory / name
