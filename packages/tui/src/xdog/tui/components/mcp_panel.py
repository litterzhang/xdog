"""Focused MCP server/catalog/preview surface; hosts route operations and consent."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from xdog.tui.components.input_panel import InputPanel
from xdog.tui.components.oauth_panel import OAuthPanel
from xdog.tui.components.transcript_panel import TranscriptPanel
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.transcript_browser import TranscriptRecord
from xdog.tui.tui import Component
from xdog.tui.utils import sanitize_terminal_text, truncate_to_width


class _Theme:
    accent = bold = dim = border = staticmethod(lambda text: text)


class McpPanel(Component):

    def __init__(self, send: Callable[[str, str, str, dict[str, str]], None], on_close: Callable[[], None],
                 on_insert: Callable[[], None], on_export: Callable[[], None],
                 on_auth_copy: Callable[[str], None] | None = None) -> None:
        self._focused = False
        self.send, self.on_close = send, on_close
        self.on_insert, self.on_export = on_insert, on_export
        self.on_auth_copy = on_auth_copy
        self.auth: OAuthPanel | None = None
        self._authorization_target = ""
        self._mode = "servers"
        self._server = ""
        self._kind = ""
        self._rows: tuple[dict[str, Any], ...] = ()
        self._selected = 0
        self._budget = 8
        self._busy = False
        self._message = ""
        self._review_ended_notice = ""
        self._query = ""
        self._filtering = False
        self._preview: TranscriptPanel | None = None
        self._argument_name = ""
        self._arguments = InputPanel(_Theme(), max_rows=1)
        self._arguments.on_submit = self._submit_arguments
        self._arguments.on_escape = lambda: self._back()
        self.send("list", "", "", {})
        self._busy = True

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, value: bool) -> None:
        self._focused = value
        if self.auth is not None:
            self.auth.focused = value
        self._arguments.focused = value and self._mode in {
            'arguments', 'uri'}


    def authorize_server(self, server: str) -> None:
        if self._busy:
            self._authorization_target = server
        else:
            self._server = server
            self.request("auth_prepare")

    def _auth_action(self, operation: str, receipt: str) -> None:
        if operation == "auth_cancel":
            self._busy = False  # Cancellation outranks an in-flight read-only status poll.
        self.request(operation, receipt)

    def publish_auth(self, action: str, data: dict[str, Any], error: str = "") -> None:
        self._busy = False
        if error:
            self._message = sanitize_terminal_text(error)
            if self.auth is not None:
                self.auth.publish(action, data, error)
            return
        if action in {"auth_prepare", "auth_refresh_prepare", "auth_credentials_save_prepare",
                      "auth_credentials_restore_prepare", "auth_credentials_forget_prepare"}:
            self._mode = "auth"
            self.requires_visible_confirmation = True
            self.auth = OAuthPanel(data, self._auth_action,
                                   self._back, self.on_auth_copy)
            self.auth.focused = self.focused
        else:
            if self.auth is None and action == "auth_status" and isinstance(data.get("review"), dict):
                self._mode = "auth"
                self.requires_visible_confirmation = True
                self.auth = OAuthPanel(data["review"], self._auth_action,
                                       self._back, self.on_auth_copy)
            if self.auth is not None:
                self.auth.focused = self.focused
                self.auth.publish(action, data)
            else:
                self._message = "Authorization state: " + str(data.get("state", "unknown"))

    def _submit_arguments(self, value: str) -> None:
        if self._mode not in {"arguments", "uri"}:
            return
        # InputPanel clears on submit; keep explicit input available after a failed/canceled request.
        self._arguments.set_text(value)
        if self._busy:
            return
        if self._mode == "uri":
            uri = value.strip()
            if not uri or len(uri) > 8192 or "{" in uri or "}" in uri:
                self._message = "Enter a resolved resource URI (replace template expressions explicitly)"
                self._arguments.set_text(value)
                return
            self._busy = True
            self.send("read", self._server, uri, {})
            return
        import json
        try:
            args = json.loads(value)
            if (not isinstance(args, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                                  for k, v in args.items())):
                raise ValueError("Arguments must be a JSON object of named strings")
        except ValueError as exc:
            self._message = str(exc)
            self._arguments.set_text(value)
            return
        self._busy = True
        self.send("prompt", self._server, self._argument_name, args)

    def request(self, action: str, name: str = "", args: dict[str, str] | None = None) -> None:
        if action not in {'list', 'connect', 'disconnect', 'refresh', 'resources', 'templates',
                          'prompts', 'read', 'prompt'} and not action.startswith('auth_'):
            return  # Retired controls cannot change a newer request/view or its busy/message state.
        if self._busy:
            return
        self._busy = True
        self._review_ended_notice = ""
        self._message = ""
        self.send(action, self._server, name, args or {})

    def publish(self, action: str, rows: tuple[dict[str, Any], ...] = (), text: str | None = None,
                error: str = "") -> None:
        if action not in {'list', 'connect', 'disconnect', 'refresh', 'resources',
                          'templates', 'prompts', 'read', 'prompt'}:
            return  # Retired/unknown results cannot unlock or replace a successor view.
        self._busy = False
        self._message = (sanitize_terminal_text(error) or self._review_ended_notice
                         if action in {"list", "connect", "refresh"} else sanitize_terminal_text(error))
        if error:
            return
        next_mode = ("servers" if action in {"list", "connect", "disconnect", "refresh"}
                     else "catalog" if action in {"resources", "templates", "prompts"} else "")
        same_view = next_mode == self._mode and (next_mode != "catalog" or action == self._kind)
        selected_name: str | None = None
        if same_view:
            visible = self._visible()
            if visible:
                key = "server" if next_mode == "servers" else "name"
                selected_name = str(visible[min(self._selected, len(visible) - 1)].get(key, ""))
        elif next_mode:
            self._query = ""
            self._filtering = False
        self._selected = 0
        if action in {"list", "connect", "disconnect", "refresh"}:
            self._mode = "servers"
            self._rows = rows
            if self._authorization_target:
                self._server, self._authorization_target = self._authorization_target, ""
                self.request("auth_prepare")
        elif action in {"resources", "templates", "prompts"}:
            self._mode, self._kind, self._rows = "catalog", action, rows
        elif text is not None:
            self._query = ""
            self._filtering = False
            if self._preview is not None:
                self._preview.close()
            self._mode = "preview"
            record = TranscriptRecord("mcp-preview", "system", "Untrusted MCP data", text)
            self._preview = TranscriptPanel(lambda: (record,),
                                            self._back, lambda: None)

        if selected_name is not None:
            key = "server" if next_mode == "servers" else "name"
            self._selected = next((index for index, row in enumerate(self._visible())
                                   if str(row.get(key, "")) == selected_name), 0)

    def close(self) -> None:
        if self._preview is not None:
            self._preview.close()

    def poll(self) -> bool:
        if self.auth is not None:
            return self.auth.poll()
        return self._preview.poll() if self._preview is not None else False

    def invalidate(self) -> None:
        if self.auth is not None:
            self.auth.invalidate()

    def set_render_budget(self, rows: int) -> None:
        self._budget = max(0, min(12, rows))
        if self.auth is not None:
            self.auth.set_render_budget(self._budget)

    def _visible(self) -> tuple[dict[str, Any], ...]:
        return tuple(row for row in self._rows if self._query.casefold() in
                     (str(row.get('name', row.get('server', ''))) + " " + str(row.get('title', ''))
                      + " " + str(row.get('description', ''))).casefold())

    def _back(self) -> None:
        if self.auth is not None:
            self.send("auth_leave", self._server, "", {})
            self.auth = None
            self.requires_visible_confirmation = False
        if self._preview is not None:
            self._preview.close()
            self._preview = None
        self._query = ""
        self._filtering = False
        self._mode = "servers"
        self._busy = True
        self.send("list", "", "", {})

    def handle_paste(self, text: str) -> bool:
        if self._mode in {"arguments", "uri"}:
            self._arguments.handle_paste(text)
        elif self._filtering:
            self._query += sanitize_terminal_text(text).replace("\n", " ")
            self._selected = 0
        return True

    def handle_input(self, event: KeyEvent) -> bool:
        if (event.event_type == KeyEventType.RELEASE
                or (event.matches("enter") or event.matches("j") or event.matches("k"))
                and event.event_type != KeyEventType.PRESS):
            return True  # External operations require a distinct press, not a held/released key.
        if self.auth is not None:
            return self.auth.handle_input(event)
        if event.matches("ctrl+c"):
            self.on_close()
        elif event.matches("escape"):
            if self._filtering:
                self._filtering = False
            elif self._mode != "servers":
                self._back()
            else:
                self.on_close()
        elif self._mode in {"arguments", "uri"}:
            if not event.matches("enter") or not self._busy:
                self._arguments.handle_input(event)
        elif self._filtering:
            if event.matches("enter"):
                self._filtering = False
            elif event.matches("backspace"):
                self._query = self._query[:-1]
                self._selected = 0
            elif len(event.key) == 1 and not event.ctrl and not event.alt:
                self.handle_paste(event.key)
        elif self._mode == "preview":
            if event.matches("i") and not self._busy:
                self.on_insert()
            elif event.matches("e") and not self._busy:
                self.on_export()
            elif self._preview is not None:
                self._preview.handle_input(event)
        elif event.matches("/"):
            self._filtering = True
        elif not self._busy:
            rows = self._visible()
            if any(event.matches(key) for key in {"up", "down", "pageup", "pagedown", "home", "end"}):
                step = max(1, self._budget - 3) if event.key in {"pageup", "pagedown"} else 1
                target = self._selected + (-step if event.key in {"up", "pageup"} else step)
                if event.key == "home":
                    target = 0
                elif event.key == "end":
                    target = len(rows) - 1
                self._selected = min(max(0, target), max(0, len(rows) - 1))
            elif rows:
                row = rows[min(self._selected, len(rows) - 1)]
                if self._mode == "servers":
                    self._server = str(row.get("server", ""))
                    actions = {"s": "auth_status", "a": "auth_prepare", "l": "auth_credentials_restore_prepare",
                               "x": "auth_credentials_forget_prepare", "c": "connect", "d": "disconnect",
                               "r": "refresh", "u": "resources",
                               "t": "templates", "p": "prompts"}
                    if any(event.matches(key) for key in actions):
                        self.request(actions[event.key])
                elif event.matches("enter"):
                    if self._kind == "resources":
                        self.request("read", str(row["name"]))
                    elif self._kind == "prompts":
                        self._mode = "arguments"
                        self._argument_name = str(row["name"])
                        self._arguments.focused = self.focused
                        self._arguments.set_text("{}")
                        self._message = "Named string arguments: " + str(row.get("arguments", ()))
                    else:
                        self._mode = "uri"
                        self._arguments.focused = self.focused
                        self._arguments.set_text(str(row["name"]))
                        self._message = "Edit a resolved URI explicitly; no automatic template expansion/fetch"
        return True

    def render(self, width: int) -> list[str]:
        if not self._budget:
            return []
        if self.auth is not None:
            self.auth.set_render_budget(self._budget)
            return self.auth.render(width)
        title = f"MCP {self._mode} · {self._server or 'configured servers'} · "
        title += "loading" if self._busy else "explicit external connections/data"
        if self._mode == "preview" and self._preview is not None:
            self._preview.set_render_budget(max(0, self._budget - 2 - int(bool(self._message))))
            rows = [title, *([self._message] if self._message else []), *self._preview.render(width),
                    "Untrusted data · i inserts into editable draft · e raw export · Esc back"]
        elif self._mode in {"arguments", "uri"}:
            self._arguments.set_render_budget(max(1, min(3, self._budget - 3)), show_borders=False)
            hint = "Enter retrieves preview only · Esc back"
            rows = [title, self._message, *self._arguments.render(width), hint]
        else:
            visible = self._visible()
            budget = max(1, self._budget - 3)
            top = max(0, self._selected - budget + 1)
            body = []
            for index, row in enumerate(visible[top:top + budget], top):
                name = str(row.get("server", "") if self._mode == "servers" else row.get("name", ""))
                detail = str(row.get("state", row.get("title", "")))
                body.append(f"{'▶' if index == self._selected else ' '} {name} · {detail}")
            rows = [title, *(body or ["No entries"]), self._message or f"Filter /{self._query}",
                    "c/d/r connection/refresh · u/t/p catalogs · Enter preview · Esc"]
        return [truncate_to_width(sanitize_terminal_text(row) if "\x1b_pi:c" not in row else row,
                                  max(0, width), "") for row in rows[:self._budget]]
