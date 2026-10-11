"""Explicit composer image/clipboard reads, owned outside the terminal thread."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from xdog.tui.terminal_image import get_image_dimensions

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_MESSAGE_IMAGES = 4
MAX_RETAINED_BYTES = 40 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Attachment:
    id: str
    name: str
    mime_type: str
    data: bytes
    width: int
    height: int

    @property
    def summary(self) -> str:
        return f"{self.id} {self.name} · {self.mime_type} · {self.width}×{self.height} · {len(self.data):,} bytes"


def attachment(data: bytes, name: str) -> Attachment:
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Each image must be at most 10 MiB")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise ValueError("Only PNG, JPEG and WebP images are supported")
    dims = get_image_dimensions(data)
    if dims is None or dims.width_px <= 0 or dims.height_px <= 0:
        raise ValueError("Invalid or incomplete image header")
    if dims.width_px * dims.height_px > 100_000_000:
        raise ValueError("Image dimensions exceed the 100 megapixel limit")
    return Attachment("a" + uuid.uuid4().hex[:8], name, mime, data, dims.width_px, dims.height_px)


def read_attachment(path: Path) -> Attachment:
    path = path.expanduser()
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Image source must be a regular file")
        data = stream.read(MAX_IMAGE_BYTES + 1)
    return attachment(data, path.name)


@dataclass(frozen=True, slots=True)
class ClipboardResult:
    text: str = ""
    image: Attachment | None = None


class AttachmentReader:
    """One explicit read at a time, with bounded clipboard subprocess ownership."""

    def __init__(self, publish: Callable[[dict[str, object]], None]) -> None:
        self._publish = publish
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[bytes] | None = None

    def read(self, path: Path | None, epoch: int) -> bool:
        with self._lock:
            if self._closed.is_set() or self._thread is not None and self._thread.is_alive():
                return False
            self._thread = threading.Thread(target=self._work, args=(path, epoch), daemon=True,
                                            name="xdog-attachment-reader")
            self._thread.start()
        return True

    @property
    def pending(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def close(self) -> None:
        self._closed.set()

    def _command(self, args: list[str], *, maximum: int = MAX_IMAGE_BYTES) -> bytes:
        with tempfile.TemporaryFile() as output:
            self._process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 3
            try:
                while self._process.poll() is None:
                    if self._closed.is_set():
                        raise ValueError("Clipboard read canceled")
                    if time.monotonic() >= deadline:
                        raise ValueError("Clipboard read timed out")
                    if os.fstat(output.fileno()).st_size > maximum:
                        raise ValueError("Clipboard contents exceed the size limit")
                    self._closed.wait(0.01)
                if self._process.returncode:
                    raise ValueError("Clipboard command failed")
                output.seek(0)
                data = output.read(maximum + 1)
                if len(data) > maximum:
                    raise ValueError("Clipboard contents exceed the size limit")
                return data
            finally:
                process, self._process = self._process, None
                if process is not None and process.poll() is None:
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                        process.wait(timeout=0.5)

    def clipboard(self) -> ClipboardResult:
        if sys.platform == "win32":
            from xdog.tui.clipboard_windows import read_windows_clipboard
            kind, data = read_windows_clipboard(self._command, sys.executable, image_limit=MAX_IMAGE_BYTES)
            if kind == "image":
                return ClipboardResult(image=attachment(data, "clipboard"))
            return ClipboardResult(text=data.decode("utf-8"))
        if sys.platform == "darwin":
            helper = shutil.which("osascript")
            if helper:
                from xdog.tui.clipboard_macos import read_macos_clipboard
                kind, data = read_macos_clipboard(self._command, helper, image_limit=MAX_IMAGE_BYTES)
                if kind == "image":
                    return ClipboardResult(image=attachment(data, "clipboard"))
                return ClipboardResult(text=data.decode("utf-8"))
            if shutil.which("pbpaste"):
                return ClipboardResult(text=self._command(["pbpaste"], maximum=1024 * 1024).decode("utf-8"))
            raise ValueError("macOS clipboard helpers unavailable; use /attach or terminal paste")
        if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-paste"):
            types = self._command(["wl-paste", "--list-types"], maximum=64 * 1024).decode(errors="replace").splitlines()
            for mime in ("image/png", "image/jpeg", "image/webp"):
                if mime in types:
                    return ClipboardResult(image=attachment(self._command(["wl-paste", "--type", mime]), "clipboard"))
            return ClipboardResult(text=self._command(["wl-paste", "--no-newline", "--type", "text"],
                                                     maximum=1024 * 1024).decode("utf-8"))
        if os.environ.get("DISPLAY") and shutil.which("xclip"):
            base = ["xclip", "-selection", "clipboard", "-o"]
            types = self._command(
                [*base, "-target", "TARGETS"], maximum=64 * 1024,
            ).decode(errors="replace").splitlines()
            for mime in ("image/png", "image/jpeg", "image/webp"):
                if mime in types:
                    return ClipboardResult(image=attachment(self._command([*base, "-target", mime]), "clipboard"))
            return ClipboardResult(text=self._command([*base, "-target", "UTF8_STRING"],
                                                     maximum=1024 * 1024).decode("utf-8"))
        if shutil.which("pbpaste"):
            return ClipboardResult(text=self._command(["pbpaste"], maximum=1024 * 1024).decode("utf-8"))
        raise ValueError("No supported clipboard helper (wl-paste, xclip, pbpaste); use /attach or terminal paste")

    def _work(self, path: Path | None, epoch: int) -> None:
        try:
            result = ClipboardResult(image=read_attachment(path)) if path is not None else self.clipboard()
            event: dict[str, object] = {"type": "attachment_result", "epoch": epoch,
                                        "text": result.text, "image": result.image}
        except Exception as exc:
            event = {"type": "attachment_result", "epoch": epoch, "error": str(exc)}
        if not self._closed.is_set():
            self._publish(event)


class AttachmentComposer:
    """UI-owned bytes; asynchronous reads never submit or overwrite a draft."""

    def __init__(self, publish: Callable[[dict[str, object]], None], changed: Callable[[], None],
                 notice: Callable[[str], None], paste: Callable[[str], object],
                 preview: Callable[[Attachment], None]) -> None:
        self.items: dict[str, Attachment] = {}
        self.epoch = 0
        self._discard_before = 0
        self._changed = changed
        self._notice = notice
        self._paste = paste
        self._preview = preview
        self.reader = AttachmentReader(publish)

    def handle(self, event: dict[str, object], *, retained: int = 0) -> None:
        epoch = event.get("epoch")
        if isinstance(epoch, int) and epoch < self._discard_before:
            self._notice("Pending clipboard/image result discarded by explicit attachment clear")
            self._changed()
            return
        if event.get("error"):
            self._notice(str(event["error"]))
        else:
            image = event.get("image")
            if isinstance(image, Attachment):
                if (len(self.items) >= MAX_MESSAGE_IMAGES
                        or self.bytes + retained + len(image.data) > MAX_RETAINED_BYTES):
                    self._notice("Attachment limit reached (4 per draft / 40 MiB retained); image was not attached")
                else:
                    self.items[image.id] = image
                    self._notice("Image attached to editable draft (not sent)")
            text = event.get("text")
            if isinstance(text, str) and text:
                self._paste(text)
            if event.get("epoch") != self.epoch:
                self._notice("Late clipboard/image result kept in the current draft; not submitted")
        self._changed()

    @property
    def bytes(self) -> int:
        return sum(len(item.data) for item in self.items.values())

    def take(self) -> tuple[Attachment, ...]:
        items = tuple(self.items.values())
        self.items.clear()
        self.epoch += 1
        self._changed()
        return items

    def restore(self, items: tuple[Attachment, ...]) -> None:
        # Recovery never drops bytes to satisfy the normal input limit. The user
        # can inspect/remove recovered items; sending still validates limits.
        self.items.update((item.id, item) for item in items)
        self._changed()

    def command(self, command: str, args: str, root: Path) -> None:
        import shlex
        try:
            if command == "attach":
                paths = shlex.split(args)
                if len(paths) != 1:
                    raise ValueError("Usage: /attach PATH (quote spaces)")
                path = Path(paths[0].removeprefix("@")).expanduser()
                if not path.is_absolute():
                    path = root / path
                if not self.reader.read(path, self.epoch):
                    raise ValueError("An attachment/clipboard read is already pending")
            elif command == "paste":
                if not self.reader.read(None, self.epoch):
                    raise ValueError("An attachment/clipboard read is already pending")
            elif not args.strip():
                self._notice("\n".join((
                    "Draft attachments (not sent):", *(item.summary for item in self.items.values()),
                )))
            elif args.strip() == "clear":
                self.items.clear()
                self.epoch += 1
                self._discard_before = self.epoch
            else:
                action, _, key = args.partition(" ")
                item = self.items.get(key.strip())
                if item is None:
                    raise ValueError("Attachment ID unavailable; inspect /attachments")
                if action == "remove":
                    self.items.pop(item.id)
                elif action == "preview":
                    self._preview(item)
                else:
                    raise ValueError("Usage: /attachments [remove ID|preview ID|clear]")
        except (OSError, ValueError) as exc:
            self._notice(str(exc))
        self._changed()

    def close(self) -> None:
        self.reader.close()
