"""Policy-free full-page OAuth consent and progress; hosts own all leases/network effects."""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.tui import Component
from xdog.tui.utils import sanitize_terminal_text, truncate_to_width, wrap_text_with_ansi


class OAuthPanel(Component):
    requires_visible_confirmation = True

    def __init__(self, review: dict[str, Any], action: Callable[[str, str], None], close: Callable[[], None],
                 copy: Callable[[str], None] | None = None) -> None:
        if (review.get('registration', 'pre-registered') != 'pre-registered'
                or review.get('token_auth_method', 'none') != 'none' or review.get('client_secret_env', '')):
            raise ValueError('Only pre-registered public PKCE consent is supported')
        self.review, self.action, self.on_close, self.copy = json.loads(json.dumps(review)), action, close, copy
        operation = review.get("operation")
        self.phase = (operation if operation in {"credentials_save", "credentials_restore", "credentials_forget"}
                      else "refresh" if operation == "refresh" else "review")
        self.url = ""
        self.message = ""
        self._budget = 8
        self._scroll = 0
        self._seen: set[int] = set()
        self._layout = (0, 0, "")
        self._visible = False
        self._busy = False
        self._last_poll = 0.0
        self._disabled = False
        self._focused = False

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, value: bool) -> None:
        if value != self._focused:
            self.invalidate()
        self._focused = value

    def disable(self, message: str) -> None:
        """Host-owned loss of operation capability, not remote credential revocation."""
        self._disabled = True
        self.phase = 'canceled'
        self.url = ''
        self._busy = False
        self.message = sanitize_terminal_text(message)
        self.invalidate()

    def invalidate(self) -> None:
        self._seen.clear()
        self._layout = (0, 0, "")
        self._scroll = 0
        self._visible = False

    def set_render_budget(self, rows: int) -> None:
        budget = max(0, min(12, rows))
        if budget != self._budget:
            self.invalidate()
        self._budget = budget

    def publish(self, action: str, data: dict[str, Any], error: str = "") -> None:
        if self._disabled:
            return  # Late status/launch receipts cannot re-admit a retired operation view.
        self._busy = False
        self.message = sanitize_terminal_text(error or str(data.get("error", "")))
        if error:
            self.phase, self.url = "failed", ""
            self._seen.clear()
            return
        if action == "auth_start":
            self.url = str(data["authorization_url"])
            self.phase = "waiting"
            self._seen.clear()
            self._scroll = 0
        elif action in {"auth_status", "auth_cancel", "auth_forget", "auth_refresh", "auth_credentials_save",
                        "auth_credentials_restore", "auth_credentials_forget"}:
            state = str(data.get("state", ""))
            if state != "waiting":
                self.phase = state
                self.url = ""
                self._seen.clear()
                self._scroll = 0

    def poll(self) -> bool:
        if (not self._disabled and self.phase == "waiting" and not self._busy
                and time.monotonic() - self._last_poll >= 1):
            self._last_poll = time.monotonic()
            self._busy = True
            self.action("auth_status", "")
            return True
        return False

    def handle_paste(self, text: str) -> bool:
        return True  # No URI/code paste, no consent from pasted newlines.

    def handle_input(self, event: KeyEvent) -> bool:
        if event.event_type != KeyEventType.PRESS:
            return True
        if event.matches("escape") or event.matches("ctrl+c"):
            self.on_close()
        elif self._disabled:
            return True  # Only the host-owned close route survives capability loss.
        elif event.key in {"up", "down", "pageup", "pagedown", "home", "end"}:
            delta = max(1, self._budget - 3) if event.key in {"pageup", "pagedown"} else 1
            self._scroll = (0 if event.key == "home" else 100000 if event.key == "end" else
                            max(0, self._scroll + (-delta if event.key in {"up", "pageup"} else delta)))
            self._visible = False
        elif (event.matches("enter") and self._visible and not self._busy
              and self.phase in {"review", "refresh", "forget", "credentials_save", "credentials_restore",
                                 "credentials_forget"}):
            self._busy = True
            action = ("auth_" + self.phase if self.phase.startswith("credentials_") else
                      "auth_start" if self.phase == "review" else
                      "auth_refresh" if self.phase == "refresh" else "auth_forget")
            if action == "auth_refresh":
                self.phase = "refreshing"
            self.action(action, self.review["review_id"])
        elif event.matches("c") and self.phase in {"waiting", "refreshing"}:
            self._busy = True
            self.action("auth_cancel", "")
        elif event.matches("y") and self._visible and self.phase == "waiting" and self.copy is not None:
            self.copy(self.url)
        elif event.matches("p") and self.phase in {"authorized", "expired"} and not self._busy:
            self._busy = True
            self.action("auth_credentials_save_prepare", "")
        elif event.matches("r") and self.phase in {"authorized", "expired"} and not self._busy:
            self._busy = True
            self.action("auth_refresh_prepare", "")
        elif event.matches("f") and self.phase in {"authorized", "expired", "failed", "canceled"}:
            self.phase = "forget"
            self._seen.clear()
            self._scroll = 0
            self._visible = False
        elif event.matches("s") and not self._busy and self.phase != "review":
            self._busy = True
            self.action("auth_status", "")
        return True

    def render(self, width: int) -> list[str]:
        self._visible = False
        if not self._budget:
            return []
        if self.phase in {"review", "refresh", "forget", "credentials_save", "credentials_restore",
                          "credentials_forget"}:
            content = ("Issuer: " + str(self.review["issuer"]) + "\nResource: " + str(self.review["resource"])
                       + "\nTLS trust: " + str(self.review.get("tls_trust", "system"))
                       + "\nClient type: pre-registered public (PKCE S256)"
                       + "\nClient ID: " + str(self.review["client_id"])
                       + "\nCallback: " + (f"http://127.0.0.1:{self.review['callback_port']}/callback"
                                              if self.review.get("callback_port") else
                                              "127.0.0.1 /callback; ephemeral port allocated after consent")
                       + "\nScopes: " + (", ".join(self.review["scopes"]) or "none requested")
                       + "\nAuthorization endpoint: " + str(self.review["authorization_endpoint"])
                       + "\nToken endpoint: " + str(self.review["token_endpoint"])
                       + "\nNo tool approval, automatic connection or browser launch. Credentials stay in memory.")
            if self.phase == "refresh":
                content = ("Refresh only this resource's existing grant; no MCP call replay/client registration.\n"
                           + content)
            if self.phase == "forget":
                content = "Forget in-memory AND saved credentials and disconnect. NOT remote revocation.\n" + content
            if self.phase.startswith("credentials_"):
                label = {"credentials_save": ("SAVE completed credentials to NATIVE SECRET SERVICE storage"
                         if self.review.get('storage_backend') == 'secret-service' else
                         "SAVE completed credentials to a PRIVATE LOCAL PLAINTEXT file"),
                         "credentials_restore": "RESTORE saved credentials into memory; no network/connection",
                         "credentials_forget": ("DELETE saved native vault credential only; not remote revocation"
                           if self.review.get('storage_backend') == 'secret-service' else
                           "DELETE saved file only; not remote revocation")}[self.phase]
                content = (label + "\nStorage: " + str(self.review.get("storage_path", ""))
                           + "\n" + str(self.review.get("storage_notice", ""))
                           + "\nSaved access expiry: " + str(self.review.get("expires_at", ""))
                           + "\n" + content)
            if self.phase == "refresh":
                content = ("Saved credential snapshots are invalidated BEFORE a potentially rotating token POST.\n"
                           + content)
        elif self.phase == "refreshing":
            content = "Refresh POST is settling. Outcome may be uncertain on cancel; old refresh token is not replayed."
        elif self.phase == "waiting":
            content = ("Open this reviewed URL in your browser; do NOT paste codes/tokens into chat.\n" + self.url
                       + "\nAuthorization is NOT tool permission; connection remains explicit.")
        else:
            content = (f"Authorization state: {self.phase}\n" + self.message
                       + "\nNo tools automatically connected. Esc returns to servers; c there connects explicitly."
                       + "\np reviews plaintext save · r refresh · f forgets memory/saved file; no remote revocation.")
        body = wrap_text_with_ansi(sanitize_terminal_text(content), max(1, width))
        count = max(0, self._budget - 3)
        layout = (width, count, self.phase + content)
        if layout != self._layout:
            self._seen.clear()
            self._scroll = 0  # Re-review starts at the first field after layout/content changes.
            self._layout = layout
        self._scroll = min(self._scroll, max(0, len(body) - count))
        end = min(len(body), self._scroll + count)
        if width >= 24 and count and self._focused:
            self._seen.update(range(self._scroll, end))
        complete = len(self._seen) == len(body)
        self._visible = complete and width >= 24 and self._budget >= 4 and self._focused
        hint = ("Enter confirms reviewed endpoints/scopes · Esc cancels · NOT tool approval" if complete else
                "PgUp/PgDn review ALL fields before confirmation · Esc cancels")
        if self.phase == "waiting":
            hint = "y copies URL after full review · c cancels callback · s status · Esc leaves/cancels pending"
        elif self.phase == "refreshing":
            hint = "c cancels pending refresh · Esc leaves/cancels pending · no token replay"
        elif self.phase not in {"review", "refresh", "forget", "credentials_save", "credentials_restore",
                                "credentials_forget"}:
            hint = "r refresh review · s status · f forget review · Esc servers · NOT tool permission"
        if self._disabled:
            hint = 'Signing operations disabled · Esc closes · completed tokens not remotely revoked'
        rows = [f"MCP OAuth · {self.phase}" + (" · waiting for owner" if self._busy else ""),
                f"Review {self._scroll + 1}-{end}/{len(body)}", *body[self._scroll:end], hint]
        return [truncate_to_width(row, max(0, width), "") for row in rows[:self._budget]]
