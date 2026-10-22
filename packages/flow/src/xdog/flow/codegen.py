"""flow.codegen — generate a self-contained Python module from a WorkflowDef.

Data flows through node-private ports: the generated module keeps a nested
``_OUT[node_id][port]`` store instead of a flat ``STATE``.  Each ``node_X``
assembles its input namespace ``ins`` from its incoming edge mappings
(``_OUT[src][src_port] -> ins[dst_port]``), runs, and writes its output ports
into ``_OUT[node_id]``.  Prompt ``{{x}}`` and script inputs are PORT-LOCAL (read
``ins``); an edge ``when`` reads the *source* node's output ports.
"""

from __future__ import annotations

import ast
import importlib.resources
import pprint
import re
import string
import textwrap

from xdog.flow.checkpoint import render_checkpoint_interceptor
from xdog.flow.frontier import build_frontier_spec, render_frontier_runtime
from xdog.flow.models import (
    IN_NODE_ID,
    OUT_NODE_ID,
    Condition,
    EdgeDef,
    NodeDef,
    WorkflowDef,
    agent_is_structured,
    agent_output_schema,
    edge_identities,
    submit_instruction,
)
from xdog.flow.preview import render_preview_runtime
from xdog.flow.result import render_run_result

# A ContainerExpr is a Python expression string evaluating to the state dict
# (``dict[str, object]``) that a ``{{path}}`` placeholder is resolved against.
ContainerExpr = str
# Placeholder grammar: a dotted path of \w+ segments (mirrors flow.interpolate).
# A placeholder is ``{{ <jsonpath> }}`` — matches the interpreter's _PATTERN.
_PLACEHOLDER = re.compile(r"\{\{\s*(.+?)\s*\}\}")


# ---------------------------------------------------------------------------
# SDK block (emitted only when the workflow has an SDK agent node / tool manifest).
# A pure-CLI/script module omits all of this and imports no agent/ai.
# ---------------------------------------------------------------------------
_SDK_IMPORTS = (
    "from xdog import ai\n"
    "from xdog.agent import Agent\n"
    "from xdog.agent.core import AgentConfig, AgentTool, AgentToolResult, StreamFn, WebSearchFn\n"
    "from xdog.agent.events import TurnEndEvent\n"
    # One line: the generated import block is assembled line by line, so a
    # parenthesised import would be split into fragments and re-ordered.
    "from xdog.agent.helpers import model_supports_tool_calls, stream_fn_from_provider, web_search_fn_from_provider\n"  # noqa: E501
    "from xdog.agent.tools import create_submit_result_tool\n"
    "from xdog.agent.workspace import workspace_briefing\n"
    "from xdog.ai.types import AssistantMessage, TextContent\n"
)

_SDK_REGISTRY = '''# ---------------------------------------------------------------------------
# Inlined tool registry (equivalent to flow.tools) so this module does not
# import the flow package for tool resolution either.  Tools themselves are an
# agent concept, so this still depends on agent.core / agent.tools / ai.types.
# ---------------------------------------------------------------------------
class ToolRegistry:
    """Registry mapping tool names to agent AgentTool objects."""

    def __init__(self) -> None:
        from xdog.agent.tools import ToolRegistry as AgentToolRegistry

        self._registry = AgentToolRegistry(builtins=False)

    def register(self, tool: AgentTool) -> None:
        self._registry.replace_source(tool.name, (tool,))

    def get(self, name: str) -> AgentTool:
        try:
            return next(tool for tool in self._registry.tools if tool.name == name)
        except StopIteration:
            known = ", ".join(sorted(tool.name for tool in self._registry.tools)) or "<none>"
            raise WorkflowExecutionError(f"Unknown tool {name!r}. Known tools: {known}") from None

    def resolve(self, names: "tuple[str, ...]") -> tuple[AgentTool, ...]:
        return tuple(self.get(n) for n in names)


async def _echo_execute(
    tool_call_id: str,
    params: dict[str, Any],
    cancel: Any = None,
    on_update: Any = None,
    ctx: dict[str, Any] | None = None,
) -> AgentToolResult:
    text: str = params.get("text", "")
    return AgentToolResult(content=(TextContent(text=text),))


_ECHO_TOOL = AgentTool(
    name="echo",
    description="Echo the given text.",
    parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    label="Echo",
    execute=_echo_execute,
)


def _agent_builtin_tools() -> tuple[AgentTool, ...]:
    try:
        from xdog.agent.tools import ToolRegistry as AgentToolRegistry
    except ImportError:
        return ()
    registry = AgentToolRegistry()
    return tuple(registry.create_tools(names=(*registry.default_names(), "submit_result")))


def default_registry() -> ToolRegistry:
    """A fresh SDK-backed registry with defaults and structured submission."""
    registry = ToolRegistry()
    registry.register(_ECHO_TOOL)
    for tool in _agent_builtin_tools():
        registry.register(tool)
    return registry


def _coerce_tool(obj: object) -> AgentTool:
    if isinstance(obj, AgentTool):
        return obj
    if callable(obj):
        built = obj()
        if not isinstance(built, AgentTool):
            raise WorkflowExecutionError(
                f"Tool factory {getattr(obj, '__name__', obj)!r} must return an AgentTool, "
                f"got {type(built).__name__}"
            )
        return built
    raise WorkflowExecutionError(
        f"Tool ref must resolve to an AgentTool or a factory returning one, got {type(obj).__name__}"
    )


def bind_tool(obj: object, name: str) -> AgentTool:
    """Coerce *obj* to an AgentTool and rename it to *name* (for manifest tools)."""
    from dataclasses import replace
    return replace(_coerce_tool(obj), name=name)
'''

_SDK_RUN_AGENT = '''async def _run_agent(
    provider: object,
    model: str,
    system_prompt: str,
    prompt: str,
    tools: tuple[AgentTool, ...] = (),
    output_schema: dict[str, object] | None = None,
    submit_hint: str = "",
    web_search_model: str | None = None,
    inherit_from: str | None = None,
    skills: tuple[str, ...] = (),
    node_id: str = "",
    override_model: bool = False,
    override_system_prompt: bool = False,
    timeout: float = 120.0,
) -> tuple[object, int]:
    stream_fn: StreamFn = stream_fn_from_provider(provider)  # type: ignore[arg-type]
    # Structured output: register submit_result + a result sink, and instruct the
    # agent to call it.  The submitted object becomes the node's structured output.
    _sink: dict[str, object] = {}
    # The workspace is unconditional; confinement is not. Both arrive from the
    # environment rather than from the workflow — a generated module that could
    # be told by its own source which directories it may touch would not be
    # confined by it. Kept identical to the interpreter.
    _roots = _confinement_roots()
    _ws = _workspace_dir()
    _tool_ctx: dict[str, object] = {"fs_workspace": str(_ws)} if _ws is not None else {}
    if _roots is not None:
        _tool_ctx["fs_confine_to"] = _roots
    # Every agent node is briefed, whatever tools it declares -- keying this off
    # the tool list would be a guess, since a custom tool or an MCP server can
    # touch the filesystem too. We tell it where its files go; we cannot audit
    # that it obeys, which is the honest difference from a script node.
    _sys = system_prompt + workspace_briefing(
        _ws, _granted_paths(), confined=_roots is not None
    )
    if output_schema is not None:
        tools = tools + (create_submit_result_tool(),)
        _tool_ctx = {
            **(_tool_ctx or {}),
            "flow_output_schema": output_schema,
            "flow_result_sink": _sink,
        }
        # Computed by the generator via models.submit_instruction, the same
        # function the interpreter calls -- deriving it here from
        # output_schema alone cannot work, because a single structured port's
        # schema is the port's own and carries no field name.
        _sys = _sys + submit_hint
    # Built-in web_search tool (search model may differ from the node model).
    _web_search_fn: WebSearchFn | None = None
    if web_search_model is not None:
        _web_search_fn = web_search_fn_from_provider(provider, web_search_model)  # type: ignore[arg-type]
    agent = Agent(
        stream_fn,
        config=AgentConfig(
            model=model,
            system_prompt=_sys,
            supports_tool_calls=model_supports_tool_calls(model),
        ),
        tools=tools,
        tool_ctx=_tool_ctx,
        skills=_skill_manager(),
        active_skills=skills,
        web_search_fn=_web_search_fn,
    )
    try:
        # Node fields are authoritative, not the workflow defaults used to
        # construct this Agent. Copy before overriding: siblings may share the
        # ancestor snapshot. Model availability is checked during restore.
        _inherited = _SESSIONS.get(inherit_from) if inherit_from else None
        if _inherited:
            inherited = dict(_inherited)
            if override_model:
                inherited["model"] = model
            if "format" in inherited or "version" in inherited:
                inherited["skills"] = agent.dump()["skills"]
            agent.restore(inherited)
            if override_system_prompt:
                agent.set_system_prompt(_sys)
            if override_model:
                agent.set_model(model)
        accumulated: list[str] = []
        _node_tokens = 0

        async def _drain() -> None:
            nonlocal _node_tokens
            event_stream = await agent.prompt(prompt)
            async for event in event_stream:
                if isinstance(event, TurnEndEvent) and event.message is not None:
                    msg = event.message
                    if isinstance(msg, AssistantMessage):
                        for part in msg.content:
                            if isinstance(part, TextContent):
                                accumulated.append(part.text)
                        _u = msg.usage
                        # Same per-turn total/fallback accounting as SdkRunner.
                        _node_tokens += _u.total_tokens or (_u.input + _u.output)
            await agent.wait_for_idle()

        # Match the interpreter default; callers of this private helper can
        # supply a different node deadline without changing the shared adapter.
        await asyncio.wait_for(_drain(), timeout=timeout)
        if node_id:
            _SESSIONS[node_id] = agent.dump()
        if output_schema is not None:
            _result_obj = _sink.get("result")
            if _result_obj is None:
                raise WorkflowExecutionError(f"Node {node_id!r}: agent did not submit a result via submit_result")
            # Preserve the submitted value, never parse prose or flatten JSON.
            return _result_obj, _node_tokens
        return "".join(accumulated), _node_tokens
    finally:
        # This node owns the Agent, not its shared provider. Settle even repeated
        # cancellation; cleanup may outlast the timeout for cooperative tools.
        close_task = asyncio.create_task(agent.aclose())
        cancelled = False
        while not close_task.done():
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                cancelled = True
        close_task.result()
        if cancelled:
            raise asyncio.CancelledError'''


def _safe_id(node_id: str) -> str:
    """Convert a node id to a valid Python identifier."""
    return re.sub(r"[^0-9a-zA-Z_]", "_", node_id)


def _safe_ids(wf: WorkflowDef) -> dict[str, str]:
    """Map every node id to a UNIQUE Python identifier.

    ``_safe_id`` alone collides when distinct ids normalise to the same symbol
    (e.g. ``a-b`` and ``a.b`` both become ``a_b``).  Collisions get a ``_2``/``_3``
    suffix.  Deterministic in ``wf.nodes`` order.
    """
    used: set[str] = set()
    mapping: dict[str, str] = {}
    for node in wf.nodes:
        base = _safe_id(node.id)
        cand = base
        i = 2
        while cand in used:
            cand = f"{base}_{i}"
            i += 1
        used.add(cand)
        mapping[node.id] = cand
    return mapping


def _ESC(text: str) -> str:
    """Escape *text* for embedding inside a double-quoted string literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _render_in_seed(wf: WorkflowDef) -> str:
    """Render the full ``_OUT`` seed dict literal: ``{'$in': {<initial_state>}}``.

    repr() escapes backslashes/quotes/newlines so the emitted literal is faithful.
    The assignment line is ``_OUT: dict[str, dict[str, object]] = <literal>`` (prefix
    37 chars); a long initial state can exceed 120, so a whole-line noqa is
    appended to keep the generated module ruff-clean.
    """
    pairs = ", ".join(f"{k!r}: {v!r}" for k, v in wf.initial_state)
    literal = "{" + f"{IN_NODE_ID!r}: {{{pairs}}}" + "}"
    if 37 + len(literal) > 120:
        return literal + "  # noqa: E501"
    return literal


def _interp_expr(template: str, container: ContainerExpr) -> str:
    """Compile an interpolated *template* to a Python str expression.

    Each ``{{path}}`` becomes ``interp_path(<container>, "path")`` — the SAME
    inlined helper the interpreter uses — so a structured value is projected to
    canonical JSON identically in both engines.  Literal spans are ``repr``'d and
    concatenated.  A template with no placeholder is just its ``repr``.
    """
    parts: list[str] = []
    pos = 0
    for m in _PLACEHOLDER.finditer(template):
        if m.start() > pos:
            parts.append(repr(template[pos : m.start()]))
        parts.append(f"interp_path({container}, {m.group(1)!r})")
        pos = m.end()
    if pos < len(template):
        parts.append(repr(template[pos:]))
    if not parts:
        return repr(template)
    return " + ".join(parts)


def _render_prompt(prompt: str, container: ContainerExpr) -> str:
    """Emit *prompt* as a Python str expression with ``{{path}}`` interpolated."""
    return _interp_expr(prompt, container)


def _str_expr(s: str, container: ContainerExpr) -> str:
    """Compile a condition operand to a projected-string Python expression.

    Both a bare ``{{x}}`` and an embedded placeholder go through ``interp_path``,
    so a condition always compares the canonical string projection on both sides —
    matching the interpreter, which routes every operand through ``interpolate``.
    """
    return _interp_expr(s, container)


_NUM_OP_PY = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


def _condition_to_expr(cond: Condition, container: ContainerExpr) -> str:
    """Translate a Condition tree to a Python boolean expression (ports via *container*)."""
    if cond.op == "equals":
        return f"{_str_expr(cond.value or '', container)} == {_str_expr(cond.text or '', container)}"
    if cond.op == "contains":
        return f"{_str_expr(cond.text or '', container)} in {_str_expr(cond.value or '', container)}"
    if cond.op in _NUM_OP_PY:
        left = _str_expr(cond.value or "", container)
        right = _str_expr(cond.text or "", container)
        return f"_ncmp({left}, {right}, {cond.op!r})"
    if cond.op == "not":
        return f"not ({_condition_to_expr(cond.children[0], container)})"
    if cond.op == "and":
        return " and ".join(f"({_condition_to_expr(c, container)})" for c in cond.children)
    if cond.op == "or":
        return " or ".join(f"({_condition_to_expr(c, container)})" for c in cond.children)
    return "True"


def _wrap_string_expr(expr: str, indent: int = 4) -> str:
    """Return expr; append ``# noqa: E501`` if the assignment line would exceed 120 chars."""
    prefix = " " * indent
    if len(prefix) + 7 + len(expr) <= 120:
        return expr
    return expr + "  # noqa: E501"


def _script_is_async(node: NodeDef) -> bool | None:
    """Whether a script node's function is ``async``; None when it cannot be known.

    Inline ``code`` is settled from its AST.  A ``run: "module:func"`` reference is
    resolved at run time, so the answer is None and the generated call decides at
    run time — mirroring the interpreter, which awaits only what
    ``inspect.isawaitable`` says is awaitable.  Assuming ``async`` here used to
    break every synchronous run-ref function on the compiled side while it worked
    fine interpreted.
    """
    if node.code is None:
        return None
    tree = ast.parse(node.code)
    return any(isinstance(s, ast.AsyncFunctionDef) for s in tree.body)


def _emit_ins_assign(
    lines: list[str], src: str, sport: str, dport: str, guard: str | None, *, concat: bool = False
) -> None:
    """Emit the guarded ``ins[dport] = <source value>`` for one edge mapping.

    The source key is a JSONPath resolved against the source node's output-ports
    dict via the inlined ``jsonpath_get`` — a plain port (``plan``) or a nested
    field (``$.verdict.within_budget``).  A miss (None) leaves the port unfed —
    mirroring the interpreter's ``_build_inputs``.  *guard* is an extra condition
    (loop edges) or None.  *concat* flattens a fan_in="concat" list-of-arrays.
    """
    getter = f"_fan_concat(jsonpath_get(_OUT.get({src!r}, {{}}), {sport!r}))" if concat else (
        f"jsonpath_get(_OUT.get({src!r}, {{}}), {sport!r})"
    )
    cond = f"({guard})" if guard else None
    lines.append(f"    _rv = {getter}" if cond is None else f"    if {cond}:")
    if cond is None:
        lines.append("    if _rv is not None:")
        lines.append(f"        ins[{dport!r}] = _rv")
    else:
        lines.append(f"        _rv = {getter}")
        lines.append("        if _rv is not None:")
        lines.append(f"            ins[{dport!r}] = _rv")


def _render_ins(node: NodeDef, wf: WorkflowDef) -> list[str]:
    """Emit input assembly from $in plus scheduler-enabled edge ids."""
    if not node.input_ports:
        return []
    lines = ["    ins: dict[str, object] = {}"]
    for edge, edge_id in zip(wf.edges, edge_identities(wf), strict=True):
        if edge.dst != node.id:
            continue
        guard = None if edge.src == IN_NODE_ID else f"{edge_id!r} in enabled_edges"
        for sport, dport in edge.mapping:
            _emit_ins_assign(
                lines,
                edge.src,
                sport,
                dport,
                guard,
                concat=edge.fan_in == "concat",
            )
    return lines


def _render_node_tail(node: NodeDef, wf: WorkflowDef, *, step_expr: str = "len(_STACK)") -> list[str]:
    """Emit the per-node epilogue: push a trace frame + flush any ``$output`` edges.

    Mirrors the executor's ``_record_frame``: the step is ``len(_STACK)`` (one frame
    per node run), the frame records this node's assembled ``ins`` and its output
    ports, and any edge to ``$output`` copies the mapped keys into ``_OUTPUT`` (a
    ``when`` guard is evaluated against this node's output ports).
    """
    ins_expr = "dict(ins)" if node.input_ports else "{}"
    frame = (
        f"{{'step': {step_expr}, 'node': {node.id!r}, "
        f"'in': {ins_expr}, 'out': dict(_OUT[{node.id!r}])}}"
    )
    frame_line = f"    _STACK.append({frame})"
    if len(frame_line) > 120:
        frame_line += "  # noqa: E501"
    lines = [frame_line]
    out_container: ContainerExpr = f"_OUT[{node.id!r}]"
    for edge in wf.edges:
        if edge.dst != OUT_NODE_ID or edge.src != node.id:
            continue
        guard = _condition_to_expr(edge.when, out_container) if edge.when is not None else None
        for sport, okey in edge.mapping:
            head = sport[2:] if sport.startswith("$.") else (sport[1:] if sport.startswith("$") else sport)
            head = re.split(r"[.\[]", head, maxsplit=1)[0]
            cond = f"{head!r} in _OUT[{node.id!r}]"
            if guard is not None:
                cond = f"({guard}) and {cond}"
            lines.append(f"    if {cond}:")
            getter = f"jsonpath_get(_OUT[{node.id!r}], {sport!r})"
            if edge.fan_in == "concat":
                getter = f"_fan_concat({getter})"
            lines.append(f"        _rv = {getter}")
            lines.append("        if _rv is not None:")
            lines.append(f"            _OUTPUT[{okey!r}] = _rv")
    return lines


def _retry_spec(node: NodeDef) -> str:
    """Emit retry and memo arguments passed to the generated node driver."""
    max_attempts = 1 + (node.retry.max if node.retry is not None else 0)
    backoff = node.retry.backoff if node.retry is not None else 0.0
    return f"retry_max={max_attempts}, backoff={backoff!r}, deterministic={node.deterministic!r}"


def _incoming_fan_out_edge(node_id: str, wf: WorkflowDef) -> EdgeDef | None:
    """The single fan_out edge feeding *node_id*, or None (loader forbids >1)."""
    for e in wf.edges:
        if e.dst == node_id and e.fan_out is not None:
            return e
    return None


def _fan_worker_port(edge: EdgeDef) -> str:
    """The worker input port that receives one array element (the fan_out mapping's dst)."""
    for sport, dport in edge.mapping:
        head = sport[2:] if sport.startswith("$.") else (sport[1:] if sport.startswith("$") else sport)
        head = re.split(r"[.\[]", head, maxsplit=1)[0]
        if head == edge.fan_out:
            return dport
    return ""


def _render_inputs_fn(node: NodeDef, wf: WorkflowDef, safe: str) -> list[str]:
    """Emit input assembly parameterized by scheduler-enabled edge ids."""
    lines = [f"def _inputs_{safe}(enabled_edges: tuple[str, ...]) -> dict[str, object]:"]
    body = _render_ins(node, wf)
    if not body:
        lines.append("    _ = enabled_edges")
        lines.append("    return {}")
        return lines
    lines += body
    lines.append("    return ins")
    return lines


def _render_store_fn(node: NodeDef, wf: WorkflowDef, safe: str) -> list[str]:
    """Emit ``_store_<safe>(ins, _res) -> None`` — write _OUT + flush $output.

    *_res* is the pure node function's returned output-port dict; it is copied
    into ``_OUT[node]`` verbatim (the node fn already coerced/fanned per port),
    then any ``$output`` edge is flushed.  The ``ins`` param feeds the $output
    ``when`` guards' unused-arg-free signature and keeps parity with the frame.
    """
    lines = [f"def _store_{safe}(ins: dict[str, object], _res: dict[str, object]) -> None:"]
    lines.append(f"    _OUT[{node.id!r}] = dict(_res)")
    # $output flush (mirrors _render_node_tail's second half).
    out_container: ContainerExpr = f"_OUT[{node.id!r}]"
    flushed = False
    for edge in wf.edges:
        if edge.dst != OUT_NODE_ID or edge.src != node.id:
            continue
        guard = _condition_to_expr(edge.when, out_container) if edge.when is not None else None
        for sport, okey in edge.mapping:
            head = sport[2:] if sport.startswith("$.") else (sport[1:] if sport.startswith("$") else sport)
            head = re.split(r"[.\[]", head, maxsplit=1)[0]
            cond = f"{head!r} in _OUT[{node.id!r}]"
            if guard is not None:
                cond = f"({guard}) and {cond}"
            lines.append(f"    if {cond}:")
            getter = f"jsonpath_get(_OUT[{node.id!r}], {sport!r})"
            if edge.fan_in == "concat":
                getter = f"_fan_concat({getter})"
            lines.append(f"        _rv = {getter}")
            lines.append("        if _rv is not None:")
            lines.append(f"            _OUTPUT[{okey!r}] = _rv")
            flushed = True
    if not flushed:
        lines.append("    _ = ins  # (no $output flush; ins unused)")
    return lines


def _transitive_successors_ids(node_id: str, wf: WorkflowDef) -> list[str]:
    """Compute transitive successors of *node_id* in the forward graph (code-gen time)."""
    visited: list[str] = []
    seen: set[str] = set()
    queue = [node_id]
    while queue:
        cur = queue.pop()
        for e in wf.edges:
            if e.src != cur or e.loop_max is not None or e.dst == OUT_NODE_ID:
                continue
            if e.dst not in seen:
                seen.add(e.dst)
                visited.append(e.dst)
                queue.append(e.dst)
    return visited


def _entry_guards(node_id: str) -> list[str]:
    """The two early-return guards every node wrapper opens with (already-done / isolated)."""
    return [
        f"    if {node_id!r} in _COMPLETED:",
        "        return",
        f"    if {node_id!r} in _ISOLATED:",
        "        return",
    ]


def _param_list(node: NodeDef) -> list[str]:
    """Signature params for a node function, required ports first.

    A non-required port gets a default, because the generated module builds its
    `ins` from edges that fired and the interpreter passes every declared port
    with `ins.get(name, "")`. Without the default, a loop-carried optional port
    -- absent by design on the first pass -- raised TypeError in the compiled
    engine and worked in the interpreter. It surfaced inside a systemd timer,
    which is the environment least able to explain it.

    Required first because Python rejects a non-default after a default; call
    sites pass everything by keyword, so the order is free.
    """
    required = [p.name for p in node.input_ports if p.required]
    optional = [p.name for p in node.input_ports if not p.required]
    return [f"{name}: object" for name in required] + [f'{name}: object = ""' for name in optional]


def _render_script_node(node: NodeDef, fn_name: str, safe: str, wf: WorkflowDef) -> str:
    """Render a script node as a pure function: (provider, ctx, **inputs) -> (outputs, 0).

    Assembles typed kwargs from its input params, calls the inlined ``_script_X``,
    coerces the return into the output-port dict, and returns ``(outputs, 0)``.
    Storing/retry/events are the driver's job.
    """
    params = ["provider: object", "ctx: RuntimeContext"]
    params += _param_list(node)
    sig = f"async def {fn_name}({', '.join(params)}) -> tuple[dict[str, object], int]:"
    lines = [sig if len(sig) <= 120 else sig + "  # noqa: E501"]
    call_args = ["ctx"]
    for p in node.input_ports:
        lines.append(f'    _in_{p.name} = to_python({p.name}, "{p.type}")')
        call_args.append(f"{p.name}=_in_{p.name}")
    is_async = _script_is_async(node)
    await_kw = "await " if is_async else ""
    # The audit bound wraps the call in both engines. It has to be inside the
    # script node's own function rather than around `node_fn`, because that
    # generic driver also runs agent and subflow nodes, which are governed by a
    # prompt and by their own child run respectively -- not by this hook.
    lines.append("    with script_bound(_workspace_dir(), _granted_paths(),")
    lines.append("                      confined=_confinement_roots() is not None):")
    core = f"        _val = {await_kw}_script_{safe}({', '.join(call_args)})"
    lines.append(core if len(core) <= 120 else core + "  # noqa: E501")
    if is_async is None:
        # A run-ref may be sync or async; decide at run time exactly as the
        # interpreter's _node_script does.
        lines.append("        if inspect.isawaitable(_val):")
        lines.append("            _val = await _val")
    lines.append("    _out: dict[str, object] = {}")
    if len(node.output_ports) <= 1:
        if node.output_ports:
            p = node.output_ports[0]
            lines.append(f'    _out[{p.name!r}] = to_state(_val, "{p.type}")')
    else:
        for p in node.output_ports:
            store = f'    _out[{p.name!r}] = to_state(_val[{p.name!r}], "{p.type}")'
            lines.append(store if len(store) <= 120 else store + "  # noqa: E501")
    lines.append("    return _out, 0")
    return "\n".join(lines)


def _rename_inline_fn(code: str, alias: str) -> str:
    """Return *code* with its single top-level function renamed to *alias*."""
    tree = ast.parse(code)
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            stmt.name = alias
            break
    source = ast.unparse(tree)
    return "\n".join(line if len(line) <= 120 else line + "  # noqa: E501" for line in source.splitlines())


def _render_inline_scripts(wf: WorkflowDef, safe_ids: dict[str, str]) -> str:
    """Emit each inline-``code`` script node's function as a top-level ``_script_<id>``.

    Wrapped in ``script_bound`` because a script node's ``code`` is not only its
    function: any other top-level statement in it runs too, here at import time,
    before ``main()`` is ever called.  Unwrapped, a workflow could put its
    filesystem access at module level and step around the bound entirely -- which
    it could, and did, until this was tested.

    ``with`` introduces no scope in Python, so ``_script_<id>`` is still a
    module-level name and every call site is unchanged.
    """
    blocks: list[str] = []
    for node in wf.nodes:
        if node.type == "script" and node.code is not None:
            alias = f"_script_{safe_ids[node.id]}"
            body = textwrap.indent(_rename_inline_fn(node.code, alias), "    ")
            blocks.append(
                "with script_bound(_workspace_dir(), _granted_paths(),\n"
                "                  confined=_confinement_roots() is not None):\n"
                f"{body}"
            )
    return "\n\n\n".join(blocks)


def _render_human_node(node: NodeDef, fn_name: str, wf: WorkflowDef) -> str:
    """Render a human node wrapper driven by the frontier scheduler."""
    safe = fn_name.removeprefix("node_")
    lines = [
        f"async def {fn_name}(provider: object, step: int, enabled_edges: tuple[str, ...]) -> None:"
    ]
    lines += _entry_guards(node.id)
    lines.append(f"    ins = _inputs_{safe}(enabled_edges)")
    lines.append(f"    if {node.signal!r} in _SIGNALS:")
    lines.append(
        f"        _EVENT_LOG.info('NodeStarted node=%s step=%d | %s', "
        f"{node.id!r}, step, preview_ports(ins) or '-')  # noqa: E501"
    )
    lines.append("        _t0 = time.monotonic()")
    lines.append(f"        _OUT[{node.id!r}] = {{}}")
    if node.output_ports:
        p = node.output_ports[0]
        lines.append(f"        _OUT[{node.id!r}][{p.name!r}] = 'approved'")
    for tl in _render_node_tail(node, wf, step_expr="step"):
        lines.append("    " + tl)
    _finished_log = (
        f"        _EVENT_LOG.info('NodeFinished node=%s step=%d duration_s=%f | %s', "
        f"{node.id!r}, step, time.monotonic() - _t0, "
        f"preview_ports(_OUT.get({node.id!r}, {{}})) or '-')  # noqa: E501"
    )
    lines.append(_finished_log)
    lines.append("    else:")
    lines.append(f"        raise SystemExit(f'PAUSED: {_ESC(node.id)} awaiting {_ESC(node.signal)}')")
    return "\n".join(lines)


def _render_agent_node(node: NodeDef, fn_name: str, wf: WorkflowDef) -> str:
    """Render an agent node as a pure function: (provider, ctx, **inputs) -> (outputs, tokens).

    Reconstructs a local ``ins`` from its params (so ``{{$.x}}`` prompt
    interpolation is unchanged), runs the agent once, and coerces the result into
    the output-port dict.  Storing/retry/budget/events are the driver's job.
    """
    model = node.model or wf.default_model
    params = ["provider: object", "ctx: RuntimeContext"]
    params += _param_list(node)
    sig = f"async def {fn_name}({', '.join(params)}) -> tuple[dict[str, object], int]:"
    lines = [sig if len(sig) <= 120 else sig + "  # noqa: E501"]
    if node.input_ports:
        pairs = ", ".join(f"{p.name!r}: {p.name}" for p in node.input_ports)
        ins_line = f"    ins: dict[str, object] = {{{pairs}}}"
        lines.append(ins_line if len(ins_line) <= 120 else ins_line + "  # noqa: E501")
        ins_container: ContainerExpr = "ins"
    else:
        ins_container = "{}"
    sys_prompt = _render_prompt(node.system_prompt, ins_container)
    user_prompt = _render_prompt(node.prompt, ins_container)
    lines.append(f"    _sys = {_wrap_string_expr(sys_prompt)}")
    lines.append(f"    _usr = {_wrap_string_expr(user_prompt)}")
    _call_args = [f'provider, "{model}", _sys, _usr']
    if node.tools:
        tool_names = ", ".join(f'"{t}"' for t in node.tools)
        lines.append(f"    _tools = _REGISTRY.resolve(({tool_names},))")
        _call_args.append("_tools")
    _structured = agent_is_structured(node)
    if _structured:
        _call_args.append(f"output_schema={agent_output_schema(node)!r}")
        _call_args.append(f"submit_hint={submit_instruction(node)!r}")
    if node.web_search:
        _call_args.append(f'web_search_model="{_ESC(node.web_search_model or model)}"')
    if node.inherit is not None:
        _call_args.append(f'inherit_from="{_ESC(node.inherit.from_node)}"')
    if node.skills:
        _call_args.append(f"skills={tuple(node.skills)!r}")
    _call_args.append(f'node_id="{_ESC(node.id)}"')
    if node.model:
        _call_args.append("override_model=True")
    if node.system_prompt:
        _call_args.append("override_system_prompt=True")
    _core = f"    result, _node_tokens = await _run_agent({', '.join(_call_args)})"
    lines.append(_core if len(_core) <= 120 else _core + "  # noqa: E501")
    lines.append("    _out: dict[str, object] = {}")
    if node.output_ports:
        if _structured and len(node.output_ports) > 1:
            _not_obj_msg = f"Node {node.id!r}: multi-output agent must submit an object"
            lines.append("    if not isinstance(result, dict):")
            lines.append(f"        raise WorkflowExecutionError({_not_obj_msg!r})")
            for p in node.output_ports:
                _miss_msg = f"Node {node.id!r}: submitted result is missing field {p.name!r}"
                lines.append(f"    if {p.name!r} not in result:")
                lines.append(f"        raise WorkflowExecutionError({_miss_msg!r})")
                store = f"    _out[{p.name!r}] = to_state(result[{p.name!r}], {p.type!r})"
                lines.append(store if len(store) <= 120 else store + "  # noqa: E501")
        else:
            lines.append(f"    _out[{node.output_ports[0].name!r}] = result")
    lines.append("    return _out, _node_tokens")
    return "\n".join(lines)


def _render_cli_agent_node(node: NodeDef, fn_name: str, wf: WorkflowDef) -> str:
    """Render a CLI agent node: shell out to a coding-agent CLI via the inlined
    ``_run_cli_agent`` helper (no ``agent``/``ai`` import).

    Same shape as ``_render_agent_node`` (reconstruct ``ins``, interpolate prompts,
    coerce the result into output ports) — only the execution call differs.  Both
    engines pass the identical (backend, model, prompts, schema, allowed_tools) so
    interpret == compile holds.
    """
    model = node.model or wf.default_model
    params = ["provider: object", "ctx: RuntimeContext"]
    params += _param_list(node)
    sig = f"async def {fn_name}({', '.join(params)}) -> tuple[dict[str, object], int]:"
    lines = [sig if len(sig) <= 120 else sig + "  # noqa: E501"]
    if node.input_ports:
        pairs = ", ".join(f"{p.name!r}: {p.name}" for p in node.input_ports)
        ins_line = f"    ins: dict[str, object] = {{{pairs}}}"
        lines.append(ins_line if len(ins_line) <= 120 else ins_line + "  # noqa: E501")
        ins_container: ContainerExpr = "ins"
    else:
        ins_container = "{}"
    sys_prompt = _render_prompt(node.system_prompt, ins_container)
    user_prompt = _render_prompt(node.prompt, ins_container)
    lines.append(f"    _sys = {_wrap_string_expr(sys_prompt)}")
    lines.append(f"    _usr = {_wrap_string_expr(user_prompt)}")
    _structured = agent_is_structured(node)
    _schema_expr = f"{agent_output_schema(node)!r}" if _structured else "None"
    _mcp_expr = f"{[[name, spec] for name, spec in node.mcp_servers]!r}"
    _call = (
        f"    result, _node_tokens = await _run_cli_agent({node.backend!r}, {model!r}, "
        f"_sys, _usr, {_schema_expr}, {tuple(node.allowed_tools)!r}, {_mcp_expr})"
    )
    lines.append(_call if len(_call) <= 120 else _call + "  # noqa: E501")
    lines.append("    _out: dict[str, object] = {}")
    if node.output_ports:
        if _structured and len(node.output_ports) > 1:
            _not_obj_msg = f"Node {node.id!r}: multi-output agent must submit an object"
            lines.append("    if not isinstance(result, dict):")
            lines.append(f"        raise WorkflowExecutionError({_not_obj_msg!r})")
            for p in node.output_ports:
                _miss_msg = f"Node {node.id!r}: submitted result is missing field {p.name!r}"
                lines.append(f"    if {p.name!r} not in result:")
                lines.append(f"        raise WorkflowExecutionError({_miss_msg!r})")
                store = f"    _out[{p.name!r}] = to_state(result[{p.name!r}], {p.type!r})"
                lines.append(store if len(store) <= 120 else store + "  # noqa: E501")
        else:
            lines.append(f"    _out[{node.output_ports[0].name!r}] = result")
    lines.append("    return _out, _node_tokens")
    return "\n".join(lines)


def _render_subflow_node(node: NodeDef, fn_name: str, safe: str) -> str:
    """Render a subflow node (G5) as a pure function calling ``execute()`` on the
    embedded child.

    The child workflow JSON is embedded as a module-level dict literal so the
    generated module is self-carrying; the function maps its ``ins`` into the
    child's ``$in`` and the child's ``runtime["out"]`` into this node's output
    ports, returning ``(outputs, tokens)`` for the generic ``_drive``.  This is the
    one place the generated module imports ``flow`` (see docs/subflow.md §3).
    """
    from xdog.flow.builder.serialize import workflow_to_dict

    assert node.child is not None
    child_literal = repr(workflow_to_dict(node.child))
    in_names = [p.name for p in node.input_ports]
    out_names = [p.name for p in node.output_ports]
    params = ["provider: object", "ctx: RuntimeContext"]
    params += _param_list(node)
    sig = f"async def {fn_name}({', '.join(params)}) -> tuple[dict[str, object], int]:"
    lines = [sig if len(sig) <= 120 else sig + "  # noqa: E501"]
    lines.append("    from xdog.flow.executor import execute as _flow_execute")
    lines.append("    from xdog.flow.loader import parse_workflow as _flow_parse")
    child_line = f"    _child = _flow_parse(_CHILD_{safe})"
    lines.append(child_line)
    pairs = ", ".join(f"{n!r}: {n}" for n in in_names)
    lines.append(f"    _child_in: dict[str, object] = {{{pairs}}}")
    lines.append("    _res = await _flow_execute(_child, inputs=_child_in, stream_fn_factory=None)")
    lines.append("    _cout = _res.runtime.get('out', {})")
    lines.append("    _out: dict[str, object] = {}")
    for n in out_names:
        lines.append(f"    if {n!r} in _cout:")
        lines.append(f"        _out[{n!r}] = _cout[{n!r}]")
    lines.append("    _ctoks = _res.runtime.get('tokens_used', 0)")
    lines.append("    return _out, _ctoks if isinstance(_ctoks, int) else 0")
    # Module-level embedded child literal (emitted before the function).
    literal = f"_CHILD_{safe} = {child_literal}"
    literal_line = literal if len(literal) <= 120 else literal + "  # noqa: E501"
    return literal_line + "\n\n\n" + "\n".join(lines)


def _render_node_function(node_id: str, wf: WorkflowDef, safe_ids: dict[str, str]) -> str:
    """Emit a node's code: its pure function plus, for script/agent, the driver
    helpers ``_inputs_<safe>`` and ``_store_<safe>``.  Human nodes stay
    self-contained (they pause via SystemExit and don't fit the retry driver)."""
    node_map = {n.id: n for n in wf.nodes}
    node = node_map[node_id]
    safe = safe_ids[node_id]
    fn_name = f"node_{safe}"

    if node.type == "human":
        return _render_human_node(node, fn_name, wf)

    if node.type == "script":
        node_fn = _render_script_node(node, fn_name, safe, wf)
    elif node.type == "subflow":
        node_fn = _render_subflow_node(node, fn_name, safe)
    elif node.backend is not None:
        node_fn = _render_cli_agent_node(node, fn_name, wf)
    else:
        node_fn = _render_agent_node(node, fn_name, wf)
    blocks = [
        node_fn,
        "\n".join(_render_inputs_fn(node, wf, safe)),
        "\n".join(_render_store_fn(node, wf, safe)),
    ]
    return "\n\n\n".join(blocks)


def _sort_import_lines(lines: list[str]) -> list[str]:
    """Order import lines the way ruff's isort wants them within one section.

    Straight ``import x`` first, then ``from x import y``, each alphabetical.
    Generated modules are ruff-checked by whoever vendors them, so getting this
    wrong is a lint failure in someone else's repository, not ours.
    """
    straight = sorted(ln for ln in lines if ln.startswith("import "))
    from_imports = sorted(ln for ln in lines if not ln.startswith("import "))
    return straight + from_imports


def _render_third_party_imports(wf: WorkflowDef, safe_ids: dict[str, str], *, sdk: bool) -> str:
    """The generated module's whole third-party import block, correctly ordered.

    Script and tool refs used to be appended after the fixed block, which put a
    module named earlier in the alphabet after ``jsonpath_ng`` and made every
    run-ref workflow's output fail ``ruff check`` on I001. No shipped example had
    a run ref, so nothing caught it.
    """
    lines = ["from jsonpath_ng import parse as _jsonpath_parse"]
    if sdk:
        lines += [ln for ln in _SDK_IMPORTS.splitlines() if ln]
    lines += [ln for ln in _render_script_imports(wf, safe_ids).splitlines() if ln]
    return "\n".join(_sort_import_lines(lines)) + "\n"


def _render_script_imports(wf: WorkflowDef, safe_ids: dict[str, str]) -> str:
    """Emit imports for custom-tool manifest refs and run-ref scripts.

    The flow-internal helpers (errors / coerce / runtime) AND the tool registry
    (ToolRegistry / default_registry / bind_tool) are inlined into the template,
    so the generated module never imports the ``flow`` package for them. Only
    external references remain: a workflow's custom-tool manifest ``module:attr``
    entries and its script nodes' ``run: module:func`` references.
    """
    extra: dict[str, list[str]] = {}
    for node in wf.nodes:
        if node.type == "script" and node.run:
            module, func = node.run.rsplit(":", 1)
            safe = safe_ids[node.id]
            extra.setdefault(module, []).append(f"{func} as _script_{safe}")

    flow_tools_aliases = extra.pop("xdog.flow.tools", [])
    lines: list[str] = []

    # Custom-tool manifest: import each ref.
    if wf.tool_refs:
        for i, (_name, ref) in enumerate(wf.tool_refs):
            module, attr = ref.rsplit(":", 1)
            lines.append(f"from {module} import {attr} as _tool_{i}")

    for alias in flow_tools_aliases:
        lines.append(f"from xdog.flow.tools import {alias}")
    for module, aliases in sorted(extra.items()):
        for alias in aliases:
            lines.append(f"from {module} import {alias}")

    if not lines:
        return ""
    # Sort all lines to satisfy ruff import ordering within the third-party block.
    lines = sorted(lines)

    return "\n".join(lines) + "\n"


def _render_tool_registration(wf: WorkflowDef) -> str:
    """Emit ``_REGISTRY.register(...)`` calls for the custom-tool manifest.

    Returns an empty string when there is no manifest, so the template line stays
    ``_REGISTRY = default_registry()`` unchanged.  Each ref (imported as
    ``_tool_i`` by :func:`_render_script_imports`) is coerced to an AgentTool and
    re-named to its manifest key, mirroring ``register_workflow_tools``.
    """
    if not wf.tool_refs:
        return ""
    lines = [""]
    for i, (name, _ref) in enumerate(wf.tool_refs):
        lines.append(f"_REGISTRY.register(bind_tool(_tool_{i}, {name!r}))")
    return "\n".join(lines)


def _render_concurrency_boilerplate(wf: WorkflowDef) -> str:
    """Emit _SEM + _capped helper when max_concurrency > 0, else empty string."""
    if wf.max_concurrency <= 0:
        return ""
    cap = wf.max_concurrency
    lines = [
        "",
        f"_SEM: asyncio.Semaphore | None = asyncio.Semaphore({cap})",
        "",
        "",
        "async def _capped(coro: \"Awaitable[None]\") -> None:",
        "    if _SEM is None:",
        "        await coro",
        "    else:",
        "        async with _SEM:",
        "            await coro",
    ]
    return "\n".join(lines)


def _has_human_nodes(wf: WorkflowDef) -> bool:
    """True if any node in the workflow is a human node."""
    return any(n.type == "human" for n in wf.nodes)


def _split_top_level_bool(expr: str) -> list[str]:
    """Split ``"(A) and (B)"`` into operand segments, ignoring nested parens.

    _condition_to_expr parenthesises every operand, so a depth-0 ``and``/``or``
    is always a real join and never part of a value.
    """
    segments: list[str] = []
    depth = 0
    start = 0
    i = 0
    while i < len(expr):
        char = expr[i]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif depth == 0:
            for op in (" and ", " or "):
                if expr.startswith(op, i):
                    segments.append(expr[start:i])
                    start = i + 1  # keep the operator leading the next segment
                    i += len(op) - 1
                    break
        i += 1
    segments.append(expr[start:])
    return segments


def _render_assignment(indent: str, target: str, expr: str, limit: int = 120) -> list[str]:
    """``target = expr``, parenthesised across lines when it would be too long.

    Generated modules are expected to pass ``ruff check`` wherever they land, and
    a compound edge condition is the one expression a workflow author can make
    arbitrarily wide without writing any code.
    """
    single = f"{indent}{target} = {expr}"
    if len(single) <= limit:
        return [single]
    segments = _split_top_level_bool(expr)
    if len(segments) == 1:
        # Nothing to break on — a single very wide comparison.
        return [f"{single}  # noqa: E501"]
    body = indent + "    "
    lines = [f"{indent}{target} = ("]
    for segment in segments:
        line = body + segment.strip()
        lines.append(line if len(line) <= limit else f"{line}  # noqa: E501")
    lines.append(f"{indent})")
    return lines


def _render_edge_resolver(wf: WorkflowDef) -> str:
    """Emit source-local outgoing condition evaluation keyed by internal edge id."""
    lines = ["def _resolve_outgoing(node_id: str) -> dict[str, bool]:", "    resolved: dict[str, bool] = {}"]
    for edge, edge_id in zip(wf.edges, edge_identities(wf), strict=True):
        if edge.dst == OUT_NODE_ID or edge.src == IN_NODE_ID:
            continue
        condition = (
            _condition_to_expr(edge.when, f"_OUT.get({edge.src!r}, {{}})")
            if edge.when is not None
            else "True"
        )
        lines.append(f"    if node_id == {edge.src!r}:")
        lines += _render_assignment("        ", f"resolved[{edge_id!r}]", condition)
    lines.append("    return resolved")
    return "\n".join(lines)


def _render_dispatch(wf: WorkflowDef, safe_ids: dict[str, str]) -> str:
    """Emit the scheduler's node-id dispatch into existing generated drivers."""
    lines = [
        "async def _dispatch(node_id: str, step: int, enabled_edges: tuple[str, ...]) -> None:"
    ]
    for index, node in enumerate(wf.nodes):
        safe = safe_ids[node.id]
        keyword = "if" if index == 0 else "elif"
        lines.append(f"    {keyword} node_id == {node.id!r}:")
        if node.type == "human":
            lines.append(f"        await node_{safe}(_PROVIDER, step, enabled_edges)")
        else:
            fan_edge = _incoming_fan_out_edge(node.id, wf)
            if fan_edge is not None:
                fan_port = _fan_worker_port(fan_edge)
                out_ports = tuple(port.name for port in node.output_ports)
                call = (
                    f"_drive_fan({node.id!r}, node_{safe}, _inputs_{safe}, _store_{safe}, "
                    f"step, enabled_edges, {fan_port!r}, {out_ports!r}, {wf.fan_max_concurrency})"
                )
            else:
                call = (
                    f"_drive({node.id!r}, node_{safe}, _inputs_{safe}, _store_{safe}, "
                    f"step, enabled_edges, {_retry_spec(node)})"
                )
            lines.append(f"        await {call}" if len(call) <= 105 else f"        await {call}  # noqa: E501")
    if not wf.nodes:
        lines.append("    _ = node_id, step, enabled_edges")
    else:
        lines.append("    else:")
        lines.append("        raise WorkflowExecutionError(f'unknown node id {node_id!r}')")
    return "\n".join(lines)


def _render_frontier_scheduler(wf: WorkflowDef) -> str:
    """Emit the generated host loop around the exact inlined transition kernel."""
    isolate_scopes = {
        node.id: tuple(_transitive_successors_ids(node.id, wf))
        for node in wf.nodes
        if node.on_error == "isolate"
    }
    edge_errors = {
        edge_id: (
            f"while loop {edge.src!r}->{edge.dst!r} did not converge within "
            f"{edge.loop_max} iterations (condition still true)"
        )
        for edge, edge_id in zip(wf.edges, edge_identities(wf), strict=True)
        if edge.loop_strict
    }
    capped = wf.max_concurrency > 0
    frontier_literal = pprint.pformat(build_frontier_spec(wf), width=88, sort_dicts=False)
    isolate_literal = pprint.pformat(isolate_scopes, width=88, sort_dicts=False)
    errors_literal = pprint.pformat(edge_errors, width=88, sort_dicts=False)
    lines = [
        "_FRONTIER_SPEC: dict[str, object] = (",
        *[f"    {line}" for line in frontier_literal.splitlines()],
        ")",
        "_ISOLATE_SCOPES: dict[str, tuple[str, ...]] = (",
        *[f"    {line}" for line in isolate_literal.splitlines()],
        ")",
        "_LOOP_ERRORS: dict[str, str] = (",
        *[f"    {line}" for line in errors_literal.splitlines()],
        ")",
        "",
        _render_edge_resolver(wf),
        "",
        "async def _run_generated_frontier() -> None:",
        "    state = new_frontier_state(_FRONTIER_SPEC, set(_COMPLETED))",
        "    for node_id in _FRONTIER_SPEC['nodes']:",
        "        if node_id in _COMPLETED:",
        "            replay_completed(_FRONTIER_SPEC, state, node_id, _resolve_outgoing(node_id))",
        "    restore_loop_activations(_FRONTIER_SPEC, state, _LOOP_COUNTERS)",
        "    next_step = max((int(frame.get('step', -1)) for frame in _STACK), default=-1) + 1",
        "    while True:",
        "        ready = take_ready(_FRONTIER_SPEC, state)",
        "        if not ready:",
        "            break",
        "        scheduled = []",
        "        for node_id, epoch, enabled_edges in ready:",
        "            scheduled.append((node_id, epoch, enabled_edges, next_step))",
        "            next_step += 1",
    ]
    if capped:
        lines += [
            "        results = await asyncio.gather(",
            "            *[_capped(_dispatch(node, step, enabled)) for node, _epoch, enabled, step in scheduled],",
            "            return_exceptions=True,",
            "        )",
        ]
    else:
        lines += [
            "        results = await asyncio.gather(",
            "            *[_dispatch(node, step, enabled) for node, _epoch, enabled, step in scheduled],",
            "            return_exceptions=True,",
            "        )",
        ]
    lines += [
        "        successful = []",
        "        deferred_failure: BaseException | None = None",
        "        for (node_id, epoch, _enabled, _step), result in zip(scheduled, results, strict=True):",
        "            if not isinstance(result, BaseException):",
        "                successful.append((node_id, epoch, _resolve_outgoing(node_id)))",
        "                continue",
        "            if node_id in _ISOLATE_SCOPES and not isinstance(",
        "                result, (WorkflowBudgetExceeded, KeyboardInterrupt, SystemExit, asyncio.CancelledError)",
        "            ):",
        "                _FAILED[node_id] = f'{type(result).__name__}: {result}'",
        "                scope = {node_id, *_ISOLATE_SCOPES[node_id]}",
        "                _ISOLATED.update(scope)",
        "                isolate_nodes(state, scope)",
        "            elif deferred_failure is None:",
        "                deferred_failure = result",
        "        def _settle_frontier_batch() -> None:",
        "            strict_edge = complete_batch(_FRONTIER_SPEC, state, successful, _LOOP_COUNTERS)",
        "            if strict_edge is not None:",
        "                raise WorkflowExecutionError(_LOOP_ERRORS[strict_edge])",
        "            frontier_completed = state['completed']",
        "            assert isinstance(frontier_completed, dict)",
        "            _COMPLETED.clear()",
        "            _COMPLETED.update(str(node_id) for node_id in frontier_completed)",
        "        if successful:",
        "            _CHECKPOINT.intercept('frontier-batch', _settle_frontier_batch)",
        "        elif isinstance(deferred_failure, SystemExit):",
        "            _CHECKPOINT.commit('human-pause')",
        "        if _MAX_TOKENS is not None and _MAX_TOKENS > 0 and _TOKENS_USED > _MAX_TOKENS:",
        "            raise WorkflowBudgetExceeded(_TOKENS_USED, _MAX_TOKENS)",
        "        if deferred_failure is not None:",
        "            raise deferred_failure",
        "    global _STOPPED_BY",
        "    _label = exhausted_edge_label(_FRONTIER_SPEC, state)",
        "    if _label is not None:",
        "        _STOPPED_BY = {'reason': 'loop_exhausted', 'edge': _label}",
    ]
    return "\n".join(lines)


def generate(wf: WorkflowDef) -> str:
    """Generate a complete Python module string for the given WorkflowDef."""
    tmpl_path = importlib.resources.files("xdog.flow") / "templates" / "runtime.py.tmpl"
    tmpl_text = tmpl_path.read_text(encoding="utf-8")

    safe_ids = _safe_ids(wf)

    inline_scripts = _render_inline_scripts(wf, safe_ids)
    node_functions = "\n\n\n".join(_render_node_function(n.id, wf, safe_ids) for n in wf.nodes)
    if inline_scripts:
        node_functions = inline_scripts + "\n\n\n" + node_functions
    node_functions = "\n\n\n".join(
        part for part in (node_functions, _render_dispatch(wf, safe_ids), _render_frontier_scheduler(wf)) if part
    )
    main_body = "    await _run_generated_frontier()"
    concurrency_boilerplate = _render_concurrency_boilerplate(wf)

    if _has_human_nodes(wf):
        signals_boilerplate = (
            "\n_SIGNALS: set[str] = set()\n"
        )
        signals_main_init = (
            "    _raw_signals = os.environ.get('FLOW_SIGNALS', '')\n"
            "    _SIGNALS.update(s.strip() for s in _raw_signals.split(',') if s.strip())"
        )
    else:
        signals_boilerplate = "\n_SIGNALS: set[str] = set()"
        signals_main_init = ""

    # _drive always references hashlib in its (deterministic-only) memo branch.
    hashlib_import = "import hashlib\n"

    # Only a `run:` script node needs the run-time awaitable check (its function is
    # resolved at import time, so async-ness cannot be settled while compiling).
    # Emitting the import unconditionally would leave every other module ruff-dirty.
    inspect_import = (
        "import inspect\n"
        if any(n.type == "script" and _script_is_async(n) is None for n in wf.nodes)
        else ""
    )

    # The SDK block (agent/ai imports + inlined tool registry + _run_agent + the
    # _REGISTRY line) is emitted ONLY when the workflow has an SDK agent node (or a
    # tool manifest, which only an SDK agent uses).  A pure-CLI/script module then
    # imports no agent/ai — its --portable bundle need not vendor them.
    _has_sdk_agent = any(n.type == "agent" and n.backend is None for n in wf.nodes)
    _needs_sdk = _has_sdk_agent or bool(wf.tool_refs)
    if _needs_sdk:
        sdk_registry = _SDK_REGISTRY
        sdk_run_agent = _SDK_RUN_AGENT
        registry_line = f"_REGISTRY = default_registry(){_render_tool_registration(wf)}"
    else:
        sdk_registry = "# (no SDK agent node — agent/ai tool registry omitted)"
        sdk_run_agent = "# (no SDK agent node — SDK turn helper omitted)"
        registry_line = ""

    if _has_sdk_agent:
        provider_init = (
            '    provider = ai.provider(os.environ.get("FLOW_PROVIDER") or "$provider")\n'
            "    global _PROVIDER\n"
            "    _PROVIDER = provider"
        )
        provider_init = string.Template(provider_init).substitute(provider=wf.provider)
    else:
        provider_init = "    pass  # no SDK agent node — no provider needed"

    result = string.Template(tmpl_text).substitute(
        workflow_name=wf.name,
        in_seed=_render_in_seed(wf),
        in_node_id=repr(IN_NODE_ID),
        node_functions=node_functions,
        frontier_runtime=render_frontier_runtime(),
        checkpoint_runtime=render_checkpoint_interceptor(),
        result_runtime=render_run_result(),
        preview_runtime=render_preview_runtime(),
        provider_init=provider_init,
        third_party_imports=_render_third_party_imports(wf, safe_ids, sdk=_needs_sdk),
        sdk_registry=sdk_registry,
        sdk_run_agent=sdk_run_agent,
        registry_line=registry_line,
        main_body=main_body,
        concurrency_boilerplate=concurrency_boilerplate,
        signals_boilerplate=signals_boilerplate,
        signals_main_init=signals_main_init,
        hashlib_import=hashlib_import,
        inspect_import=inspect_import,
    )
    return result
