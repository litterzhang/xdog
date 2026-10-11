"""Focused MCP server/catalog/preview surface; hosts route operations and consent."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from xdog.tui.components.input_panel import InputPanel
from xdog.tui.components.mcp_completion import McpCompletionPanel
from xdog.tui.components.mcp_elicitation_panel import McpElicitationPanel
from xdog.tui.components.mcp_roots_panel import McpRootsReview
from xdog.tui.components.mcp_sampling_panel import McpSamplingPanel
from xdog.tui.components.oauth_panel import OAuthPanel
from xdog.tui.components.transcript_panel import TranscriptPanel
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.mcp_resource import resource_request
from xdog.tui.mcp_subscription import filter_text
from xdog.tui.scalar_template import expand
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
        self.elicitation_review: McpElicitationPanel | None = None
        self.sampling_review: McpSamplingPanel | None = None
        self.roots_review: McpRootsReview | None = None
        self.auth: OAuthPanel | None = None
        self.completion: McpCompletionPanel | None = None
        self._completion_origin: tuple[str, str] = ("", "")
        self._completion_names: tuple[str, ...] = ()
        self._template_name = ""
        self._template_values: dict[str, str] = {}
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
        self._watches: dict[tuple[str, str, str], dict[str, Any]] = {}
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
        if self.elicitation_review is not None:
            self.elicitation_review.focused = value
        if self.sampling_review is not None:
            self.sampling_review.focused = value
        if self.roots_review is not None:
            self.roots_review.focused = value
        if self.auth is not None:
            self.auth.focused = value
        if self.completion is not None:
            self.completion.focused = value
        self._arguments.focused = value and self._mode in {
            'arguments', 'uri', 'resource_arguments', 'subscriptions', 'roots'}

    def publish_elicitation(self, event: dict[str, Any]) -> None:
        if (self.elicitation_review is not None or self.sampling_review is not None or self.auth is not None
                or self.roots_review is not None or self.completion is not None):
            raise ValueError('Protected MCP review already owns focus')
        self._review_ended_notice = ''
        panel = McpElicitationPanel(event, self._answer_elicitation, self._cancel_elicitation,
                                  open_url=lambda url: self.send('elicitation_open', self._server, url, {}))
        self.elicitation_review = panel
        panel.focused = self.focused
        self.requires_visible_confirmation = self.requires_current_viewport_input = True
        self._server = str(event.get('server', ''))
        self._busy = True

    def _answer_elicitation(self, response: dict[str, Any]) -> None:
        import json
        self.send('elicitation_answer', self._server, json.dumps(response), {})
        self.elicitation_review = None
        self.requires_visible_confirmation = self.requires_current_viewport_input = False
        self._mode = 'servers'
        self._busy = True
        self._message = 'Elicitation reply validation settling · Esc closes and cancels'

    def _cancel_elicitation(self) -> None:
        self.send('elicitation_off', self._server, '', {})
        self.elicitation_review = None
        self.requires_visible_confirmation = self.requires_current_viewport_input = False
        self._busy = False
        self._mode = 'servers'

    def elicitation_closed(self, identifier: str) -> None:
        if self.sampling_review is not None or self.roots_review is not None or self.auth is not None:
            return
        if self.elicitation_review is not None:
            if self.elicitation_review.event['id'] != identifier:
                return
            self.elicitation_review.disable('Elicitation ended; no replay')
            self.elicitation_review = None
        self.requires_visible_confirmation = self.requires_current_viewport_input = False
        self._busy = False
        self._message = self._review_ended_notice = 'Elicitation ended · no automatic replay'

    def publish_sampling(self, event: dict[str, Any], models: tuple[str, ...]) -> None:
        if (self.elicitation_review is not None or self.sampling_review is not None or self.auth is not None
                or self.roots_review is not None or self.completion is not None):
            raise ValueError('Protected MCP review already owns focus')
        self._review_ended_notice = ''
        panel = McpSamplingPanel(event, models, self._answer_sampling, self._cancel_sampling)
        self.sampling_review = panel
        panel.focused = self.focused
        self.requires_visible_confirmation = True
        self.requires_current_viewport_input = True
        self._server = str(event.get('server', ''))
        self._busy = True

    def _answer_sampling(self, response: dict[str, Any]) -> None:
        import json
        self.send('sampling_answer', self._server, json.dumps(response), {})
        self.sampling_review = None
        self.requires_visible_confirmation = False
        self.requires_current_viewport_input = False
        self._mode = 'servers'
        self._busy = True
        self._message = 'Sampling approved phase settling · Esc closes and cancels'

    def _cancel_sampling(self) -> None:
        self.send('sampling_off', self._server, '', {})
        self.sampling_review = None
        self.requires_visible_confirmation = False
        self.requires_current_viewport_input = False
        self._busy = False
        self._mode = 'servers'

    def sampling_closed(self, identifier: str) -> None:
        if self.elicitation_review is not None:
            return
        if self.sampling_review is not None:
            if self.sampling_review.event['id'] != identifier:
                return  # Another completed request cannot remove this review's focus/paint guards.
            self.sampling_review.disable('Sampling request ended; no replay')
            self.sampling_review = None
        self.requires_visible_confirmation = False
        self.requires_current_viewport_input = False
        self._busy = False
        self._message = self._review_ended_notice = 'Sampling request ended · no automatic replay'

    def publish_roots(self, action: str, data: dict[str, Any], error: str = '') -> None:
        self._busy = False
        if error:
            self._message = sanitize_terminal_text(error)
            if self.roots_review is not None:
                self.roots_review.invalidate()
            return
        if action == 'roots_prepare':
            self.roots_review = McpRootsReview(data, lambda receipt: self.request('roots_commit', receipt),
                                             self._cancel_roots)
            self.roots_review.focused = self.focused
            self.requires_visible_confirmation = True
        else:
            self.roots_review = None
            self.requires_visible_confirmation = False
            self._mode = 'servers'
            self._message = ('Root metadata updated; no file/tool/model grant'
                             if action == 'roots_commit' else 'Canceled')

    def _cancel_roots(self) -> None:
        self.send('roots_cancel', self._server, '', {})
        self.roots_review = None
        self.requires_visible_confirmation = False
        self._busy = False
        self._mode = 'servers'

    def _root_form(self) -> None:
        self._mode = 'roots'
        self._arguments.focused = self.focused
        self._arguments.set_text('[]')
        self._message = 'JSON roots array within configured ceiling · [] withdraws all · Enter prepares review'

    def publish_watch(self, data: dict[str, Any], error: str = "", *, receipt: bool = False) -> None:
        if receipt:
            self._busy = False
        if error:
            self._message = sanitize_terminal_text(error)
            return
        server, uri, state, changes = (data.get(key) for key in ('server', 'uri', 'state', 'changes'))
        if (not isinstance(server, str) or not isinstance(uri, str) or not isinstance(state, str)
                or state not in {'active', 'opening', 'stopped', 'ended', 'failed', 'interrupted', 'disconnected'}
                or type(changes) is not int or not 0 <= changes <= 2**53):
            self._message = 'Invalid resource watch update ignored'
            return
        catalog = data.get('catalog', '')
        if catalog not in ('', 'tools', 'prompts', 'resources'):
            self._message = 'Invalid catalog watch kind ignored'
            return
        filters = data.get('notifications')
        if filters is not None:
            try:
                selected = filter_text(filters)
            except (ValueError, UnicodeError):
                self._message = 'Invalid combined notification filter ignored'
                return
            if catalog or uri:
                self._message = 'Mixed resource/catalog/combined watch update ignored'
                return
            key = server, 'notifications', selected
        else:
            key = server, catalog, uri
        if len(self._watches) >= 16 and key not in self._watches:
            retired = next((key for key, value in self._watches.items()
                            if value.get('state') not in {'opening', 'active'}), None)
            if retired is None:
                self._message = 'Resource watch view capacity reached'
                return
            self._watches.pop(retired)
        self._watches[key] = dict(data)
        from xdog.tui.utils import redact_sensitive_text
        self._message = (f'Watch {state} · {changes} change notice(s) · '
                         + ('combined notification filters' if filters is not None else
                            catalog + ' catalog' if catalog else redact_sensitive_text(uri))
                         + (' · no automatic read/registration/prompt insertion' if catalog or filters is not None
                            else ' · no automatic read/prompt insertion'))

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

    def _cancel_completion(self) -> None:
        self._busy = False
        self.send("completion_cancel", self._server, "", {})

    def _close_completion(self) -> None:
        if self.completion is not None:
            self.completion.close()
        self.completion = None
        self.requires_visible_confirmation = False
        self._mode = self._completion_origin[0]
        self._arguments.focused = self.focused

    def _resource_form(self, uri: str = "") -> None:
        import json
        self._mode = "resource_arguments"
        self._arguments.focused = self.focused
        self._arguments.set_text(json.dumps({"uri": uri, "argument": "", "value": "", "context": {}},
                                            ensure_ascii=False))
        self._message = "Specify documented names/context; suggestions are opaque data, NOT URIs"

    def _open_completion(self) -> None:
        import json
        if self._busy:
            return
        try:
            draft = self._arguments.get_text()
            if self._mode == "resource_arguments":
                name, argument, value, context = resource_request(draft)
                names = (argument, *context)
                context = {argument: value, **context}
                kind = "resource"
            else:
                if not self._completion_names:
                    raise ValueError("No advertised named variables available for completion")
                context = json.loads(draft) if self._mode == "arguments" else dict(self._template_values)
                if (not isinstance(context, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                                         for k, v in context.items())
                        or context.keys() - set(self._completion_names)):
                    raise ValueError("Review a JSON object containing only advertised named string arguments")
                names = self._completion_names
                kind = "prompt" if self._mode == "arguments" else "template"
                name = self._argument_name if kind == "prompt" else self._template_name
            self._completion_origin = self._mode, draft
            self.completion = McpCompletionPanel(self._server, kind, name, names, context,
                lambda args: self.request("complete_" + kind, name, args), self._cancel_completion,
                self._insert_completion, self._close_completion)
            self.completion.focused = self.focused
            self._mode = "completion"
            self.requires_visible_confirmation = True
            self._arguments.focused = False
        except (ValueError, TypeError) as exc:
            self._message = str(exc)

    def _insert_completion(self, argument: str, value: str) -> None:
        import json
        origin, draft = self._completion_origin
        if self.completion is None or self._arguments.get_text() != draft:
            self._message = "Parent argument draft changed; completion was not inserted"
            self._close_completion()
            return
        try:
            if origin == "resource_arguments":
                envelope = json.loads(draft)
                values = {**self.completion.drafts, argument: value}
                envelope.update(argument=argument, value=value,
                                context={key: item for key, item in values.items() if key != argument})
                text = json.dumps(envelope, ensure_ascii=False)
            elif origin == "arguments":
                args = {**json.loads(draft), **self.completion.drafts, argument: value}
                text = json.dumps(args, ensure_ascii=False)
            else:
                values = {**self._template_values, **self.completion.drafts, argument: value}
                text = expand(self._template_name, values)
                self._template_values = values
            self._close_completion()
            self._arguments.set_text(text)
            self._message = ("Selected argument saved; Enter reviews data locally (NOT a resource read)"
                             if origin == "resource_arguments" else
                             "Selected value inserted into editable draft; Enter separately retrieves preview")
        except (ValueError, TypeError) as exc:
            self._message = str(exc)

    def _subscription_form(self) -> None:
        self._mode = 'subscriptions'
        self._arguments.focused = self.focused
        self._arguments.set_text('{"toolsListChanged":true,"promptsListChanged":true,'
                                 '"resourcesListChanged":true,"resourceSubscriptions":[]}')
        self._message = 'Edit exact catalog flags/URI array; Enter starts/stops this filter only (no reads)'

    def _submit_arguments(self, value: str) -> None:
        if self._busy:
            self._arguments.set_text(value)
            return
        if self._mode == 'roots':
            import json
            self._arguments.set_text(value)
            try:
                parsed = json.loads(value)
                if not isinstance(parsed, list):
                    raise ValueError('Roots must be a JSON array')
            except ValueError as exc:
                self._message = str(exc)
                return
            self.request('roots_prepare', value)
            return
        if self._mode == 'subscriptions':
            import json
            self._arguments.set_text(value)
            try:
                selected = filter_text(json.loads(value))
            except (ValueError, UnicodeError) as exc:
                self._message = str(exc)
                return
            state = self._watches.get((self._server, 'notifications', selected), {}).get('state')
            self.request('unwatch_notifications' if state == 'active' else 'watch_notifications', selected)
            return
        if self._mode == "resource_arguments":
            import json
            try:
                uri, argument, prefix, context = resource_request(value)
            except ValueError as exc:
                self._message = str(exc)
                self._arguments.set_text(value)
                return
            self.request("resource_completion_preview", uri,
                         {"argument": argument, "value": prefix, "context": json.dumps(context, ensure_ascii=False)})
            return  # Explicit local review, NEVER resources/read or implicit URI expansion.
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
        if self._busy:
            return
        self._busy = True
        self._review_ended_notice = ""
        self._message = ""
        self.send(action, self._server, name, args or {})

    def publish(self, action: str, rows: tuple[dict[str, Any], ...] = (), text: str | None = None,
                error: str = "") -> None:
        self._busy = False
        self._message = (sanitize_terminal_text(error) or self._review_ended_notice
                         if action in {"list", "connect", "refresh"} else sanitize_terminal_text(error))
        if action in {"complete_prompt", "complete_template", "complete_resource"}:
            if self.completion is not None:
                self.completion.publish(rows[0] if rows else {}, error)
            return
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
        if self.elicitation_review is not None:
            self._cancel_elicitation()
        if self.sampling_review is not None:
            self._cancel_sampling()
        if self.roots_review is not None or self._mode == 'roots':
            self._cancel_roots()
        if self.completion is not None:
            self.completion.close()
        if self._preview is not None:
            self._preview.close()

    def poll(self) -> bool:
        if self.auth is not None:
            return self.auth.poll()
        return self._preview.poll() if self._preview is not None else False

    def invalidate(self) -> None:
        if self.elicitation_review is not None:
            self.elicitation_review.invalidate()
        if self.sampling_review is not None:
            self.sampling_review.invalidate()
        if self.roots_review is not None:
            self.roots_review.invalidate()
        if self.auth is not None:
            self.auth.invalidate()
        if self.completion is not None:
            self.completion.invalidate()

    def set_render_budget(self, rows: int) -> None:
        self._budget = max(0, min(12, rows))
        if self.auth is not None:
            self.auth.set_render_budget(self._budget)
        if self.completion is not None:
            self.completion.set_render_budget(self._budget)

    def _visible(self) -> tuple[dict[str, Any], ...]:
        return tuple(row for row in self._rows if self._query.casefold() in
                     (str(row.get('name', row.get('server', ''))) + " " + str(row.get('title', ''))
                      + " " + str(row.get('description', ''))).casefold())

    def _back(self) -> None:
        if self.roots_review is not None or self._mode == 'roots':
            self._cancel_roots()
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
        if self.elicitation_review is not None:
            return self.elicitation_review.handle_paste(text)
        if self.sampling_review is not None:
            return self.sampling_review.handle_paste(text)
        if self.roots_review is not None:
            return True  # Reviewed snapshot cannot change through the underlying form.
        if self._mode in {"arguments", "uri", "resource_arguments", "subscriptions", "roots"}:
            self._arguments.handle_paste(text)
        elif self.completion is not None:
            self.completion.handle_paste(text)
        elif self._filtering:
            self._query += sanitize_terminal_text(text).replace("\n", " ")
            self._selected = 0
        return True

    def handle_input(self, event: KeyEvent) -> bool:
        if (event.event_type == KeyEventType.RELEASE
                or (event.matches("enter") or event.matches("j") or event.matches("k"))
                and event.event_type != KeyEventType.PRESS):
            return True  # Accepting a value and retrieving its preview require distinct presses.
        if self.elicitation_review is not None:
            return self.elicitation_review.handle_input(event)
        if self.sampling_review is not None:
            return self.sampling_review.handle_input(event)
        if self.auth is not None:
            return self.auth.handle_input(event)
        if self.completion is not None:
            return self.completion.handle_input(event)
        if self.roots_review is not None:
            return self.roots_review.handle_input(event)
        if event.matches("ctrl+c"):
            self.on_close()
        elif event.matches("escape"):
            if self._filtering:
                self._filtering = False
            elif self._mode != "servers":
                self._back()
            else:
                self.on_close()
        elif self._mode in {"arguments", "uri", "resource_arguments", "subscriptions", "roots"}:
            if event.matches("ctrl+space") and self._mode != "subscriptions":
                self._open_completion()
            elif not event.matches("enter") or not self._busy:
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
                    catalogs = {'t': 'tools', 'p': 'prompts', 'u': 'resources'}
                    if event.matches('j'):
                        self.request('sampling_tasks_on')
                    elif event.matches('k'):
                        self.request('elicitation_tasks_on')
                    elif event.matches('f'):
                        self.request('elicitation_on')
                    elif event.matches('alt+f'):
                        self.request('elicitation_off')
                    elif event.matches('b'):
                        self.request('sampling_on')
                    elif event.matches('alt+b'):
                        self.request('sampling_off')
                    elif event.matches('o'):
                        self._root_form()
                    elif event.matches('alt+w'):
                        self._subscription_form()
                    elif any(event.matches('alt+' + key) for key in catalogs):
                        kind = catalogs[event.key]
                        state = self._watches.get((self._server, kind, ''), {}).get('state')
                        self.request('unwatch_catalog' if state == 'active' else 'watch_catalog', kind)
                    elif event.matches("v"):
                        self._resource_form()
                    elif any(event.matches(key) for key in actions):
                        self.request(actions[event.key])
                elif event.matches("w") and self._kind == "resources":
                    uri = str(row['name'])
                    state = self._watches.get((self._server, '', uri), {}).get('state')
                    self.request('unwatch_resource' if state == 'active' else 'watch_resource', uri)
                elif event.matches("v") and self._kind == "resources":
                    self._resource_form(str(row["name"]))
                elif event.matches("enter"):
                    if self._kind == "resources":
                        self.request("read", str(row["name"]))
                    elif self._kind == "prompts":
                        self._completion_names = tuple(arg[0] for arg in row.get("arguments", ()))
                        self._mode = "arguments"
                        self._argument_name = str(row["name"])
                        self._arguments.focused = self.focused
                        self._arguments.set_text("{}")
                        self._message = "Named string arguments: " + str(row.get("arguments", ()))
                    else:
                        self._completion_names = tuple(arg[0] for arg in row.get("arguments", ()))
                        self._template_name, self._template_values = str(row["name"]), {}
                        self._mode = "uri"
                        self._arguments.focused = self.focused
                        self._arguments.set_text(str(row["name"]))
                        self._message = "Edit a resolved URI explicitly; no automatic template expansion/fetch"
        return True

    def render(self, width: int) -> list[str]:
        if not self._budget:
            return []
        if self.elicitation_review is not None:
            self.elicitation_review.set_render_budget(self._budget)
            return self.elicitation_review.render(width)
        if self.sampling_review is not None:
            self.sampling_review.set_render_budget(self._budget)
            return self.sampling_review.render(width)
        if self.roots_review is not None:
            self.roots_review.set_render_budget(self._budget)
            return self.roots_review.render(width)
        if self.auth is not None:
            self.auth.set_render_budget(self._budget)
            return self.auth.render(width)
        if self.completion is not None:
            self.completion.set_render_budget(self._budget)
            return self.completion.render(width)
        title = f"MCP {self._mode} · {self._server or 'configured servers'} · "
        title += "loading" if self._busy else "explicit external connections/data"
        if self._mode == "preview" and self._preview is not None:
            self._preview.set_render_budget(max(0, self._budget - 2 - int(bool(self._message))))
            rows = [title, *([self._message] if self._message else []), *self._preview.render(width),
                    "Untrusted data · i inserts into editable draft · e raw export · Esc back"]
        elif self._mode in {"arguments", "uri", "resource_arguments", "subscriptions", "roots"}:
            self._arguments.set_render_budget(max(1, min(3, self._budget - 3)), show_borders=False)
            hint = ("Enter prepares full roots review · Esc back" if self._mode == 'roots' else
                    "Enter starts/stops exact notification watch · Esc back" if self._mode == 'subscriptions' else
                    "Ctrl+Space completion · Enter reviews edited data locally · Esc back"
                    if self._mode == "resource_arguments" else
                    "Ctrl+Space named completion · Enter retrieves preview only · Esc back")
            rows = [title, self._message, *self._arguments.render(width), hint]
        else:
            visible = self._visible()
            budget = max(1, self._budget - 3)
            top = max(0, self._selected - budget + 1)
            body = []
            for index, row in enumerate(visible[top:top + budget], top):
                name = str(row.get("server", "") if self._mode == "servers" else row.get("name", ""))
                detail = str(row.get("state", row.get("title", "")))
                if self._mode != "servers" and self._kind == "resources":
                    watch = self._watches.get((self._server, '', name))
                    if watch is not None:
                        detail += f" · watch {watch['state']} · {watch['changes']} change notice(s)"
                body.append(f"{'▶' if index == self._selected else ' '} {name} · {detail}")
            rows = [title, *(body or ["No entries"]), self._message or f"Filter /{self._query}",
                    "b sampling/f form · j sampling tasks/k form tasks · Alt+b/f OFF · "
                    "Alt+t/p/u catalog watch · w resource watch · Esc"]
        return [truncate_to_width(sanitize_terminal_text(row) if "\x1b_pi:c" not in row else row,
                                  max(0, width), "") for row in rows[:self._budget]]
