"""flow.builder.app — terminal-facing shell wiring BuilderModel + actions to tui.

This module is the thin, terminal-touching layer over the headless
:class:`~flow.builder.model.BuilderModel` and the pure functions in
:mod:`flow.builder.actions`.  It translates single-char / arrow
:class:`~tui.keys.KeyEvent` presses into action calls and renders a
**two-panel** layout: a left panel of three boxed blocks (Graph / Nodes /
Edges) that ``Tab`` cycles focus between, and a right panel whose content
follows the focused block — the graph diagram, the selected node's details, or
the selected edge's parameter flow.

Every state transition goes through the pure action functions, so the app stays
trivially unit-testable by feeding synthetic ``KeyEvent`` objects.  Only
:func:`run` touches the terminal and is excluded from coverage.
"""

from __future__ import annotations

import os
import pathlib
import textwrap
from collections.abc import Callable

from xdog.flow.builder import actions, style
from xdog.flow.builder.io import dump_any, load_any
from xdog.flow.builder.model import BuilderModel, empty_model, model_from_workflow
from xdog.flow.graph import to_ascii_diagram
from xdog.flow.models import NodeDef, Port
from xdog.flow.tools import ToolInfo, describe_tools, read_run_source
from xdog.tui.keys import KeyEvent
from xdog.tui.tui import TUI, Component
from xdog.tui.utils import string_width, wrap_text_with_ansi

_MIN_WIDTH = 60
_LEFT_MAX = 40
_GAP = "  "
_KEYHINT = "S-tab page   tab block   a/s add   j/k move   PgUp/PgDn scroll   d del   p prompt   e edge   w save   q"

# Node-type -> accent colour for the nodes list + detail tag.
_NODE_ACCENT = {
    "agent": style.AGENT,
    "script": style.SCRIPT,
    "human": style.HUMAN,
    "subflow": style.SUBFLOW,
}


def _schema_label(schema: dict[str, object]) -> str:
    """Compact one-line rendering of a port's JSON Schema.

    ``{"type":"string"}`` -> ``string``; an object -> ``object{k:string, ...}``;
    an array -> ``array<string>``.  Nested structure is shown one level deep so a
    detail row stays readable.
    """
    t = schema.get("type")
    t = t if isinstance(t, str) else "string"
    if t == "object":
        props = schema.get("properties")
        if isinstance(props, dict) and props:
            inner = ", ".join(
                f"{k}:{(v.get('type') if isinstance(v, dict) else None) or 'string'}" for k, v in props.items()
            )
            return f"object{{{inner}}}"
        return "object"
    if t == "array":
        items = schema.get("items")
        item_t = (items.get("type") if isinstance(items, dict) else None) or "any"
        return f"array<{item_t}>"
    return t


def _port_label(p: Port) -> str:
    """``name:<schema>`` with a trailing ``?`` for a non-required input port."""
    return f"{p.name}:{_schema_label(p.schema)}{'' if p.required else '?'}"

# The three left-panel blocks, in Tab-cycle order.
_FOCI = ("graph", "nodes", "edges")

# The three top-level pages, in Shift+Tab-cycle order.
_PAGES = ("builder", "functions", "tools")


class BuilderApp(Component):
    """A :class:`~tui.tui.Component` shell over a headless builder model."""

    def __init__(self, model: BuilderModel, path: pathlib.Path) -> None:
        self._model = model
        self._path = path
        self._mode = "normal"
        self._page = "builder"  # top-level page (builder/functions/tools)
        self._focus = "graph"  # which left block is active within the builder page
        self._edge_idx = 0  # selected edge within the Edges block
        self._fn_idx = 0  # selected script within the Functions page
        self._tool_idx = 0  # selected tool within the Tools page
        self._scroll = 0  # vertical scroll offset of the right (detail) pane
        self._buf = ""
        self._edge_src: str | None = None
        self._message = ""
        self.on_quit: Callable[[], None] | None = None

    @property
    def model(self) -> BuilderModel:
        """The current :class:`~flow.builder.model.BuilderModel`."""
        return self._model

    # -- render ----------------------------------------------------------------

    def render(self, width: int) -> list[str]:
        # Guard against zero/negative widths (a pty can report width 0), so the
        # UI never collapses to all-empty lines.
        w = max(width, _MIN_WIDTH)
        left_w = min(_LEFT_MAX, max(24, w * 2 // 5))
        right_w = max(20, w - left_w - string_width(_GAP))

        # Header + footer chrome is fixed height; the body fills the rest of the
        # screen.  The left list stays put while the right (detail/source) pane
        # scrolls independently, so long sources are fully reachable.
        header = self._render_header(w)
        footer = self._render_footer(w)
        body_h = max(1, self._screen_height() - len(header) - len(footer))

        left = self._left_column(left_w)
        right = self._right_column(right_w)

        # Scroll the right pane; clamp so the last line stays in view.
        max_scroll = max(0, len(right) - body_h)
        self._scroll = max(0, min(self._scroll, max_scroll))
        right_view = right[self._scroll : self._scroll + body_h]

        out: list[str] = list(header)
        gap = _GAP
        for i in range(body_h):
            lcell = left[i] if i < len(left) else ""
            rcell = right_view[i] if i < len(right_view) else ""
            out.append(style.pad(style.pad(lcell, left_w) + gap + style.pad(rcell, right_w), w))
        out += footer
        return [style.pad(line, w) for line in out]

    @staticmethod
    def _screen_height() -> int:
        """Terminal height in rows (fallback to a sensible default off-tty)."""
        try:
            return max(10, os.get_terminal_size().lines)
        except OSError:
            return 30

    def _render_header(self, w: int) -> list[str]:
        model = self._model
        title = style.bold(style.fg(f" flow builder — {model.wf.name} ", style.TITLE))
        return [style.pad(title, w), style.dim("─" * w), style.pad(self._page_tabs(), w)]

    def _render_footer(self, w: int) -> list[str]:
        model = self._model
        out: list[str] = [style.dim("─" * w)]
        if model.error is None:
            status = style.fg("● valid", style.OK)
        else:
            status = style.fg(f"✗ {model.error}", style.ERR)
        locus = f"{self._page}·{self._focus}" if self._page == "builder" else self._page
        mode = style.dim(f"[{self._mode}·{locus}]")
        out.append(style.pad(f"{status}   {mode}", w))
        if self._mode == "prompt":
            out.append(style.pad(style.fg(f"prompt> {self._buf}", style.ENTRY), w))
        if self._message:
            out.append(style.pad(style.dim(self._message), w))
        out.append(style.pad(style.dim(_KEYHINT), w))
        return out

    # -- page tab strip --------------------------------------------------------

    def _page_tabs(self) -> str:
        """A one-line strip showing the three pages, current one highlighted."""
        labels = {"builder": "1 Builder", "functions": "2 Functions", "tools": "3 Tools"}
        cells: list[str] = []
        for page in _PAGES:
            text = f" {labels[page]} "
            if page == self._page:
                cells.append(style.bold(style.fg(text, style.TITLE)))
            else:
                cells.append(style.dim(text))
        return "  ".join(cells) + style.dim("   (Shift+Tab)")

    # -- left panel: three boxed blocks ---------------------------------------

    def _box(self, title: str, inner: list[str], width: int, *, focused: bool, wrap: bool = False) -> list[str]:
        """Frame *inner* lines in a titled box of exactly *width* columns.

        A focused box is drawn in the accent colour; an unfocused box is dimmed
        so it recedes.  Inner lines keep their own styling when focused.  When
        *wrap* is set, each inner line is soft-wrapped to the box width (ANSI
        preserved) so long content flows onto continuation lines instead of
        being truncated.
        """
        inner_w = max(1, width - 2)
        label = f"─ {title} "
        if string_width(label) > inner_w:
            label = label[:inner_w]
        fill = inner_w - string_width(label)
        top = "┌" + label + "─" * fill + "┐"
        bot = "└" + "─" * inner_w + "┘"
        if wrap:
            wrapped: list[str] = []
            for line in inner:
                wrapped.extend(wrap_text_with_ansi(line, inner_w) or [""])
            inner = wrapped
        body = ["│" + style.pad(line, inner_w) + "│" for line in inner]
        if focused:
            return [style.bold(style.fg(top, style.TITLE)), *body, style.fg(bot, style.TITLE)]
        return [style.dim(top), *[style.dim(line) for line in body], style.dim(bot)]

    def _left_column(self, w: int) -> list[str]:
        if self._page == "functions":
            return self._functions_left(w)
        if self._page == "tools":
            return self._tools_left(w)
        out: list[str] = []
        out += self._box("Graph", self._graph_block(), w, focused=self._focus == "graph")
        out += self._box("Nodes", self._nodes_block(w - 2), w, focused=self._focus == "nodes")
        out += self._box("Edges", self._edges_block(w - 2), w, focused=self._focus == "edges")
        return out

    def _graph_block(self) -> list[str]:
        wf = self._model.wf
        return [
            f"{len(wf.nodes)} nodes  ·  {len(wf.edges)} edges",
            "entry: " + (wf.entry or style.dim("(unset)")),
            "provider: " + (wf.provider or style.dim("(unset)")),
        ]

    def _nodes_block(self, inner_w: int) -> list[str]:
        model = self._model
        if not model.node_ids:
            return [style.dim("(press 'a' to add a node)")]
        lines: list[str] = []
        for node in model.wf.nodes:
            accent = _NODE_ACCENT.get(node.type, style.SCRIPT)
            tag = style.fg(f"[{node.type}]", accent)
            entry = style.fg(" *", style.ENTRY) if node.id == model.wf.entry else ""
            selected = node.id == model.selected
            edge_src = self._mode == "edge" and node.id == self._edge_src
            marker = "▸ " if selected else ("◆ " if edge_src else "  ")
            row = f"{marker}{node.id} {tag}{entry}"
            if selected:
                row = style.bg(style.pad(row, inner_w), style.SELECT_BG)
            lines.append(row)
        return lines

    def _edges_block(self, inner_w: int) -> list[str]:
        edges = self._model.wf.edges
        if not edges:
            return [style.dim("(select a node, press 'e' to connect)")]
        active = self._focus == "edges"
        lines: list[str] = []
        for i, e in enumerate(edges):
            selected = active and i == self._edge_idx
            arrow = "↺" if e.loop_max is not None else "→"
            tail = style.fg(" (loop)", style.ERR) if e.loop_max is not None else ""
            if e.when is not None:
                tail += style.fg(" ?", style.ENTRY)
            marker = "▸ " if selected else "  "
            row = f"{marker}{e.src} {arrow} {e.dst}{tail}"
            if selected:
                row = style.bg(style.pad(row, inner_w), style.SELECT_BG)
            lines.append(row)
        return lines

    # -- right panel: follows the focused block -------------------------------

    def _right_column(self, w: int) -> list[str]:
        if self._page == "functions":
            return self._functions_right(w)
        if self._page == "tools":
            return self._tools_right(w)
        if self._focus == "graph":
            # The diagram is a pre-laid-out 2-D drawing; wrapping would corrupt
            # it, so it truncates (and scrolls) rather than soft-wrapping.
            return self._box("GRAPH", self._graph_diagram(), w, focused=True)
        if self._focus == "edges":
            return self._box("EDGE", self._edge_detail_lines(), w, focused=True, wrap=True)
        return self._box("DETAILS", self._detail_lines(), w, focused=True, wrap=True)

    def _graph_diagram(self) -> list[str]:
        lines: list[str] = []
        for gl in to_ascii_diagram(self._model.wf).splitlines():
            stripped = gl.strip()
            if stripped.startswith(("┌", "└", "│")):
                lines.append(style.dim(gl))
            elif "↺" in gl:
                lines.append(style.fg(gl, style.ERR))
            elif stripped in ("│", "▼") or "├─" in gl:
                lines.append(style.fg(gl, style.MUTED))
            else:
                lines.append(gl)
        lines += self._workflow_signature_lines()
        return lines

    def _workflow_signature_lines(self) -> list[str]:
        """The whole workflow's typed $in/$output signature, shown under the graph.

        ``$in`` uses the declared ``in_schema`` when present, else the inferred
        signature (marked ``(inferred)``); ``$output`` is derived from the
        ``$output`` edges.  Returns ``[]`` when the workflow has neither.
        """
        from xdog.flow.loader import workflow_input_schema, workflow_output_schema

        wf = self._model.wf
        in_props = workflow_input_schema(wf).get("properties")
        out_props = workflow_output_schema(wf).get("properties")

        def sig(props: object) -> str:
            if not isinstance(props, dict) or not props:
                return ""
            return ", ".join(f"{k}:{_schema_label(v)}" for k, v in props.items() if isinstance(v, dict))

        in_sig, out_sig = sig(in_props), sig(out_props)
        if not in_sig and not out_sig:
            return []
        src = "" if wf.in_schema else style.dim(" (inferred)")
        out: list[str] = ["", style.dim("── signature ──")]
        if in_sig:
            out.append(style.dim("$in     ") + in_sig + src)
        if out_sig:
            out.append(style.dim("$output ") + out_sig)
        return out

    def _detail_lines(self) -> list[str]:
        """Field-by-field detail of the currently selected node (dim labels)."""
        selected = self._model.selected
        node = next((n for n in self._model.wf.nodes if n.id == selected), None)
        if node is None:
            return [style.dim("(no node selected)")]

        def row(label: str, value: str) -> str:
            return style.dim(f"{label:<8}") + value

        out: list[str] = [row("id", node.id), row("type", node.type)]
        if node.type == "script":
            if node.code is not None:
                first = node.code.splitlines()[0] if node.code else ""
                out.append(row("code", first))
            else:
                out.append(row("run", node.run or "(unset)"))
        elif node.type == "subflow":
            child = node.child
            if child is not None:
                out.append(row("child", child.name or "(unnamed)"))
                out.append(row("nodes", f"{len(child.nodes)} node(s), {len(child.edges)} edge(s)"))
        else:
            out.append(row("model", node.model or "(default)"))
            if node.system_prompt:
                out.append(row("system", node.system_prompt))
            if node.prompt:
                out.append(row("prompt", node.prompt))
            if node.tools:
                out.append(row("tools", ", ".join(node.tools)))
        if node.input_ports:
            out.append(row("inputs", ", ".join(_port_label(p) for p in node.input_ports)))
        if node.output_ports:
            out.append(row("outputs", ", ".join(_port_label(p) for p in node.output_ports)))
        return out

    def _edge_detail_lines(self) -> list[str]:
        """Detail of the selected edge: endpoints, guard, and parameter flow."""
        edges = self._model.wf.edges
        if not edges:
            return [style.dim("(no edges — press 'e' on a node to connect)")]
        idx = min(self._edge_idx, len(edges) - 1)
        e = edges[idx]

        def row(label: str, value: str) -> str:
            return style.dim(f"{label:<10}") + value

        arrow = style.fg("↺" if e.loop_max is not None else "→", style.MUTED)
        out: list[str] = [
            row("edge", f"{style.fg(e.src, style.AGENT)} {arrow} {style.fg(e.dst, style.AGENT)}"),
            row("when", e.when.op if e.when is not None else style.dim("(none)")),
            row("loop", str(e.loop_max) if e.loop_max is not None else style.dim("(none)")),
            style.dim("── parameter flow ──"),
        ]
        if e.mapping:
            for sport, dport in e.mapping:
                out.append(style.fg(f"{sport} → {dport}", style.OK))
        else:
            out.append(row("map", style.dim("(control edge, no data)")))
        return out

    # -- Functions page: script node sources ----------------------------------

    def _script_nodes(self) -> list[NodeDef]:
        """The current workflow's script nodes, in declaration order."""
        return [n for n in self._model.wf.nodes if n.type == "script"]

    def _functions_left(self, w: int) -> list[str]:
        scripts = self._script_nodes()
        inner_w = w - 2
        if not scripts:
            return self._box("Functions", [style.dim("(no script nodes)")], w, focused=True)
        idx = min(self._fn_idx, len(scripts) - 1)
        lines: list[str] = []
        for i, node in enumerate(scripts):
            kind = "code" if node.code is not None else "run"
            tag = style.fg(f"[{kind}]", style.SCRIPT)
            marker = "▸ " if i == idx else "  "
            row = f"{marker}{node.id} {tag}"
            if i == idx:
                row = style.bg(style.pad(row, inner_w), style.SELECT_BG)
            lines.append(row)
        return self._box("Functions", lines, w, focused=True)

    def _functions_right(self, w: int) -> list[str]:
        scripts = self._script_nodes()
        if not scripts:
            return self._box("SOURCE", [style.dim("(select a script node)")], w, focused=True)
        node = scripts[min(self._fn_idx, len(scripts) - 1)]
        return self._box(f"SOURCE · {node.id}", self._script_source_lines(node), w, focused=True, wrap=True)

    def _script_source_lines(self, node: NodeDef) -> list[str]:
        """Source lines for *node*: inline code verbatim, or the imported run: fn."""
        if node.code is not None:
            return textwrap.dedent(node.code).splitlines() or [style.dim("(empty)")]
        # run: "module:func" — statically read the source (no import / execution),
        # searching the workflow dir and its subdirectories (e.g. scripts/).
        source = read_run_source(node.run or "", self._path.parent)
        if source is None:
            return [style.dim(f"(source unavailable for {node.run})")]
        return textwrap.dedent(source).splitlines()

    # -- Tools page: built-in + custom tool sources ----------------------------

    def _tool_infos(self) -> tuple[ToolInfo, ...]:
        return describe_tools(self._model.wf, self._path.parent)

    def _tools_left(self, w: int) -> list[str]:
        infos = self._tool_infos()
        inner_w = w - 2
        if not infos:
            return self._box("Tools", [style.dim("(no tools)")], w, focused=True)
        idx = min(self._tool_idx, len(infos) - 1)
        lines: list[str] = []
        for i, info in enumerate(infos):
            accent = style.SCRIPT if info.origin == "custom" else style.AGENT
            tag = style.fg(f"[{info.origin}]", accent)
            marker = "▸ " if i == idx else "  "
            row = f"{marker}{info.name} {tag}"
            if i == idx:
                row = style.bg(style.pad(row, inner_w), style.SELECT_BG)
            lines.append(row)
        return self._box("Tools", lines, w, focused=True)

    def _tools_right(self, w: int) -> list[str]:
        infos = self._tool_infos()
        if not infos:
            return self._box("TOOL", [style.dim("(no tools)")], w, focused=True)
        info = infos[min(self._tool_idx, len(infos) - 1)]

        def row(label: str, value: str) -> str:
            return style.dim(f"{label:<8}") + value

        out: list[str] = [row("name", info.name), row("origin", info.origin)]
        if info.description:
            out.append(row("desc", info.description))
        props = info.params.get("properties") if isinstance(info.params, dict) else None
        if isinstance(props, dict) and props:
            fields = ", ".join(f"{k}:{v.get('type', '?') if isinstance(v, dict) else '?'}" for k, v in props.items())
            out.append(row("params", fields))
        out.append(style.dim("── source ──"))
        if info.source is not None:
            out.extend(textwrap.dedent(info.source).splitlines())
        else:
            out.append(style.dim("(source unavailable)"))
        return self._box(f"TOOL · {info.name}", out, w, focused=True, wrap=True)

    # -- input dispatch --------------------------------------------------------

    def handle_input(self, event: KeyEvent) -> bool:
        # Shift+Tab cycles the top-level page from any normal-mode context.
        if self._mode == "normal" and event.matches("shift+tab"):
            self._page = _PAGES[(_PAGES.index(self._page) + 1) % len(_PAGES)]
            self._scroll = 0  # each page starts scrolled to the top
            return True
        if self._mode == "prompt":
            return self._handle_prompt(event)
        if self._mode == "edge":
            return self._handle_edge(event)
        if self._page == "functions":
            return self._handle_functions(event)
        if self._page == "tools":
            return self._handle_tools(event)
        return self._handle_normal(event)

    def _handle_functions(self, event: KeyEvent) -> bool:
        """Read-only Functions page: up/down move the script selection; q quits."""
        key = event.key
        if key in ("j", "down"):
            self._select_fn(self._fn_idx + 1)
            return True
        if key in ("k", "up"):
            self._select_fn(self._fn_idx - 1)
            return True
        if self._scroll_key(event):
            return True
        if key == "q" and self.on_quit is not None:
            self.on_quit()
            return True
        return True

    def _select_fn(self, idx: int) -> None:
        new = self._clamp_idx(idx, len(self._script_nodes()))
        if new != self._fn_idx:
            self._fn_idx = new
            self._scroll = 0  # new source starts from the top

    def _handle_tools(self, event: KeyEvent) -> bool:
        """Read-only Tools page: up/down move the tool selection; q quits."""
        key = event.key
        if key in ("j", "down"):
            self._select_tool(self._tool_idx + 1)
            return True
        if key in ("k", "up"):
            self._select_tool(self._tool_idx - 1)
            return True
        if self._scroll_key(event):
            return True
        if key == "q" and self.on_quit is not None:
            self.on_quit()
            return True
        return True

    def _select_tool(self, idx: int) -> None:
        new = self._clamp_idx(idx, len(self._tool_infos()))
        if new != self._tool_idx:
            self._tool_idx = new
            self._scroll = 0

    def _scroll_key(self, event: KeyEvent) -> bool:
        """Scroll the right (detail/source) pane.  Returns True if consumed.

        PageDown / Ctrl+F jump a screenful down; PageUp / Ctrl+B a screenful up;
        Ctrl+D / Ctrl+U a half; ``g`` / ``G`` jump to top / bottom.  The offset
        is clamped against the content in :meth:`render`.
        """
        page = max(1, self._screen_height() - 6)
        key = event.key
        if key == "pagedown" or (event.ctrl and key == "f"):
            self._scroll += page
        elif key == "pageup" or (event.ctrl and key == "b"):
            self._scroll = max(0, self._scroll - page)
        elif event.ctrl and key == "d":
            self._scroll += page // 2
        elif event.ctrl and key == "u":
            self._scroll = max(0, self._scroll - page // 2)
        elif key == "g":
            self._scroll = 0
        elif key == "G":
            self._scroll = 10**9  # clamped down to max in render()
        else:
            return False
        return True

    @staticmethod
    def _clamp_idx(idx: int, count: int) -> int:
        if count == 0:
            return 0
        return max(0, min(count - 1, idx))

    def _handle_normal(self, event: KeyEvent) -> bool:
        key = event.key
        model = self._model
        if key == "tab":
            self._focus = _FOCI[(_FOCI.index(self._focus) + 1) % len(_FOCI)]
            self._scroll = 0  # each block's detail pane starts at the top
            return True
        if key == "a":
            self._model = actions.add_node(model, "agent")
            return True
        if key == "s":
            self._model = actions.add_node(model, "script")
            return True
        if key in ("j", "down"):
            self._nav(1)
            return True
        if key in ("k", "up"):
            self._nav(-1)
            return True
        if self._scroll_key(event):
            return True
        if key == "d":
            self._delete()
            return True
        if key == "w":
            self._save()
            return True
        if key == "p":
            if model.selected is not None:
                self._mode = "prompt"
                self._buf = self._selected_prompt()
            return True
        if key == "e":
            if model.selected is not None:
                self._mode = "edge"
                self._edge_src = model.selected
            return True
        if key == "q":
            if self.on_quit is not None:
                self.on_quit()
            return True
        return False

    def _handle_prompt(self, event: KeyEvent) -> bool:
        key = event.key
        if key == "enter":
            selected = self._model.selected
            if selected is not None:
                self._model = actions.set_field(self._model, selected, "prompt", self._buf)
            self._mode = "normal"
            return True
        if key == "escape":
            self._buf = ""
            self._mode = "normal"
            return True
        if key == "backspace":
            self._buf = self._buf[:-1]
            return True
        if len(key) == 1:
            self._buf += key
            return True
        return True

    def _handle_edge(self, event: KeyEvent) -> bool:
        key = event.key
        if key in ("j", "down"):
            self._move(1)
            return True
        if key in ("k", "up"):
            self._move(-1)
            return True
        if key == "enter":
            src = self._edge_src
            dst = self._model.selected
            if src is not None and dst is not None:
                self._model = actions.add_edge(self._model, src, dst)
            self._edge_src = None
            self._mode = "normal"
            return True
        if key == "escape":
            self._edge_src = None
            self._mode = "normal"
            return True
        return True

    # -- helpers ---------------------------------------------------------------

    def _nav(self, delta: int) -> None:
        """Move the selection within the focused block (nodes or edges).

        The Graph block has no selectable content, so arrows are inert there —
        press Tab to move focus to Nodes before navigating.
        """
        if self._focus == "graph":
            return
        if self._focus == "edges":
            self._move_edge(delta)
        else:
            self._move(delta)

    def _delete(self) -> None:
        """Delete the focused element: an edge in the Edges block, else a node."""
        model = self._model
        if self._focus == "edges":
            if model.wf.edges:
                idx = min(self._edge_idx, len(model.wf.edges) - 1)
                self._model = actions.remove_edge(model, idx)
                self._edge_idx = max(0, min(self._edge_idx, len(self._model.wf.edges) - 1))
            return
        if model.selected is not None:
            self._model = actions.remove_node(model, model.selected)

    def _selected_prompt(self) -> str:
        selected = self._model.selected
        for node in self._model.wf.nodes:
            if node.id == selected:
                return node.prompt
        return ""

    def _move(self, delta: int) -> None:
        ids = self._model.node_ids
        if not ids:
            return
        idx = ids.index(self._model.selected) if self._model.selected in ids else 0
        new_idx = max(0, min(len(ids) - 1, idx + delta))
        self._model = actions.select(self._model, ids[new_idx])

    def _move_edge(self, delta: int) -> None:
        count = len(self._model.wf.edges)
        if count == 0:
            return
        self._edge_idx = max(0, min(count - 1, self._edge_idx + delta))

    def _save(self) -> None:
        if self._model.error is None:
            dump_any(self._model.wf, self._path)
            self._model = model_from_workflow(self._model.wf)


def build_app(path: str | pathlib.Path) -> BuilderApp:
    """Build a :class:`BuilderApp`, loading *path* if it exists else starting empty."""
    p = pathlib.Path(path)
    if p.exists():
        model = model_from_workflow(load_any(p))
    else:
        model = empty_model(name=p.stem)
    return BuilderApp(model, p)


def run(path: str | pathlib.Path) -> None:  # pragma: no cover
    """Build the app and run it in a real terminal (blocking)."""
    app = build_app(path)
    tui = TUI()
    tui.add_child(app)
    tui.set_focus(app)
    app.on_quit = tui.stop  # 'q' exits the loop
    try:
        tui.start()
    finally:
        tui.stop()
