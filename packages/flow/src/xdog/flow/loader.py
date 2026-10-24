"""flow.loader — load and validate WorkflowDef from JSON.

Data flows through explicit ports and edge mappings (see :mod:`flow.models`).
A node declares ``inputs`` / ``outputs`` port lists; an edge declares ``map`` —
``{source_output_port: destination_input_port}``.  The workflow's ``state`` block
seeds the output ports of the reserved source node :data:`flow.models.IN_NODE_ID`
(``$in``), which is referenced by edges like any other source but never appears
in ``wf.nodes``.  Edges may target the reserved sink :data:`flow.models.OUT_NODE_ID`
(``$output``) to collect workflow outputs; ``$output`` is a dst-only mirror of
``$in`` and likewise never appears in ``wf.nodes``.
"""

from __future__ import annotations

import ast
import contextlib
import json
import logging
import re
import warnings
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

from xdog.flow import error_codes as codes
from xdog.flow.coerce import VALID_TYPES
from xdog.flow.errors import FlowWarning, WorkflowValidationError
from xdog.flow.frontier import build_frontier_spec
from xdog.flow.models import (
    IN_NODE_ID,
    OUT_NODE_ID,
    Condition,
    EdgeDef,
    InheritSpec,
    NodeDef,
    Port,
    RetryPolicy,
    ScheduleDef,
    WorkflowDef,
    entry_frontier,
)

logger = logging.getLogger(__name__)

# CLI agent backends (docs/cli-agent.md): an agent node with one of these shells
# out to that CLI instead of the in-process SDK.
_KNOWN_CLI_BACKENDS = frozenset({"claude-cli", "codex-cli"})

# Default loop bound for ``while`` sugar when no explicit ``max`` is given: a safe
# ceiling so a non-converging condition fails loudly (non-convergence error)
# instead of spinning forever.  An author who needs more iterations sets an
# explicit ``{"while": {"cond": ..., "max": N}}``.
_WHILE_SAFE_MAX = 100


def _parse_condition(data: Any) -> Condition:
    if not isinstance(data, dict):
        raise WorkflowValidationError(f"Condition must be a dict, got {type(data)}", code=codes.WRONG_SHAPE)
    if "equals" in data:
        inner = data["equals"]
        return Condition(op="equals", value=str(inner["value"]), text=str(inner["text"]))
    if "contains" in data:
        inner = data["contains"]
        return Condition(op="contains", value=str(inner["value"]), text=str(inner["text"]))
    for _num_op in ("gt", "gte", "lt", "lte"):
        if _num_op in data:
            inner = data[_num_op]
            return Condition(op=_num_op, value=str(inner["value"]), text=str(inner["text"]))
    if "not" in data:
        return Condition(op="not", children=(_parse_condition(data["not"]),))
    if "and" in data:
        return Condition(op="and", children=tuple(_parse_condition(c) for c in data["and"]))
    if "or" in data:
        return Condition(op="or", children=tuple(_parse_condition(c) for c in data["or"]))
    raise WorkflowValidationError(
        f"Unknown condition keys: {list(data.keys())}",
        code=codes.UNKNOWN_FIELD,
        hint="Use one of: equals, contains, gt, gte, lt, lte, not, and, or.",
    )


def _parse_ports(raw: Any) -> tuple[Port, ...]:
    """Parse an ``inputs``/``outputs`` list.

    Each entry is a bare name (a required string port) or a canonical object:
    ``{name, schema, required}``. The schema is a JSON Schema fragment and
    ``required`` defaults to true.
    """
    if not raw:
        return ()
    ports: list[Port] = []
    for item in raw:
        if isinstance(item, dict):
            ports.append(_port_from_obj(item))
        else:
            ports.append(Port(name=str(item)))
    return tuple(ports)


def _port_from_obj(item: dict[str, Any]) -> Port:
    """Build a typed port from the canonical object form."""
    name = str(item["name"])
    schema = item.get("schema")
    if not isinstance(schema, dict):
        raise WorkflowValidationError(
            f"Port {name!r}: object form requires a 'schema' object",
            code=codes.MISSING_REQUIRED,
            hint='Use the bare-string form ("text") for a port that needs no schema.',
        )
    return Port(name=name, schema=schema, required=bool(item.get("required", True)))


def _parse_output_ports(data: dict[str, Any]) -> tuple[Port, ...]:
    """Parse the canonical plural ``outputs`` port list."""
    return _parse_ports(data.get("outputs", []))


def _parse_node(
    data: dict[str, Any],
    *,
    base_dir: Path | None = None,
    _seen: frozenset[Path] | None = None,
) -> NodeDef:
    raw_tools = data.get("tools", [])
    tools: tuple[str, ...] = tuple(str(t) for t in raw_tools) if raw_tools else ()
    raw_skills = data.get("skills", [])
    skills: tuple[str, ...] = tuple(str(s) for s in raw_skills) if raw_skills else ()
    node_id = str(data["id"])
    retry: RetryPolicy | None = None
    raw_retry = data.get("retry")
    if raw_retry is not None:
        if not isinstance(raw_retry, dict):
            raise WorkflowValidationError(
                f"Node {node_id!r}: retry must be an object",
                code=codes.WRONG_SHAPE,
                hint='Write it as {"max": 2, "backoff": 1.0}.',
            )
        raw_max = raw_retry.get("max", 0)
        if not isinstance(raw_max, int) or raw_max < 0:
            raise WorkflowValidationError(f"Node {node_id!r}: retry.max must be >= 0", code=codes.INVALID_VALUE)
        raw_backoff = raw_retry.get("backoff", 0.0)
        if not isinstance(raw_backoff, (int, float)) or raw_backoff < 0:
            raise WorkflowValidationError(
                f"Node {node_id!r}: retry.backoff must be >= 0", code=codes.INVALID_VALUE
            )
        retry = RetryPolicy(max=int(raw_max), backoff=float(raw_backoff))
    raw_on_error = data.get("on_error", "fail")
    if raw_on_error not in ("fail", "isolate"):
        raise WorkflowValidationError(
            f"Node {node_id!r}: on_error must be 'fail' or 'isolate'", code=codes.INVALID_VALUE
        )
    on_error: Literal["fail", "isolate"] = raw_on_error
    raw_type = data.get("type", "agent")
    if raw_type not in ("agent", "script", "human", "subflow"):
        raise WorkflowValidationError(
            f"Node {node_id!r}: type must be 'agent', 'script', 'human', or 'subflow', got {raw_type!r}",
            code=codes.INVALID_VALUE,
        )
    node_type: Literal["agent", "script", "human", "subflow"] = raw_type
    signal = str(data.get("signal", ""))

    # Sub-workflow (G5): parse the INLINE child and DERIVE the node's ports from
    # its signature — the author does not declare inputs/outputs on a subflow node.
    child: WorkflowDef | None = None
    sub_input_ports: tuple[Port, ...] = _parse_ports(data.get("inputs", []))
    sub_output_ports: tuple[Port, ...] = _parse_output_ports(data)
    if node_type == "subflow":
        raw_child = data.get("subflow")
        if data.get("inputs") or data.get("outputs"):
            raise WorkflowValidationError(
                f"Subflow node {node_id!r}: must not declare 'inputs'/'outputs' — they are "
                f"derived from the child workflow's signature",
                code=codes.INVALID_SUBFLOW,
            )
        if isinstance(raw_child, str):
            # Path reference: resolve against base_dir, guard against cycles, and
            # inline the loaded child (execution/codegen/serialize see an inline child).
            if base_dir is None:
                raise WorkflowValidationError(
                    f"Subflow node {node_id!r}: a path reference {raw_child!r} needs a base directory; "
                    f"load via load_workflow(path), not parse_workflow(dict)",
                    code=codes.INVALID_SUBFLOW,
                )
            child_path = (base_dir / raw_child).resolve()
            seen = _seen or frozenset()
            if child_path in seen:
                raise WorkflowValidationError(
                    f"Subflow node {node_id!r}: cyclic subflow reference to {child_path}",
                    code=codes.INVALID_SUBFLOW,
                )
            if not child_path.is_file():
                raise WorkflowValidationError(
                    f"Subflow node {node_id!r}: child workflow file not found: {child_path}",
                    code=codes.INVALID_SUBFLOW,
                    hint="The path resolves against the parent workflow file's directory, not the cwd.",
                )
            with child_path.open(encoding="utf-8") as _fh:
                child_data = json.load(_fh)
            child = parse_workflow(child_data, base_dir=child_path.parent, _seen=seen | {child_path})
        elif isinstance(raw_child, dict):
            child = parse_workflow(raw_child, base_dir=base_dir, _seen=_seen)
        else:
            raise WorkflowValidationError(
                f"Subflow node {node_id!r}: must set 'subflow' to an inline child workflow object "
                f"or a path string to a child workflow JSON",
                code=codes.MISSING_REQUIRED,
            )
        in_sig = workflow_input_schema(child)["properties"]
        out_sig = workflow_output_schema(child)["properties"]
        assert isinstance(in_sig, dict) and isinstance(out_sig, dict)
        sub_input_ports = tuple(
            Port(name=k, schema=v if isinstance(v, dict) else {"type": "string"}) for k, v in in_sig.items()
        )
        sub_output_ports = tuple(
            Port(name=k, schema=v if isinstance(v, dict) else {"type": "string"}) for k, v in out_sig.items()
        )

    # CLI agent backend fields (docs/cli-agent.md).
    backend = data.get("backend")
    backend_val: str | None = str(backend) if backend is not None else None
    raw_allowed = data.get("allowed_tools", [])
    allowed_tools: tuple[str, ...] = tuple(str(t) for t in raw_allowed) if raw_allowed else ()
    raw_mcp = data.get("mcp_servers", {})
    mcp_servers: tuple[tuple[str, dict[str, object]], ...]
    if isinstance(raw_mcp, dict) and raw_mcp:
        mcp_list: list[tuple[str, dict[str, object]]] = []
        for k, v in raw_mcp.items():
            if not isinstance(v, dict):
                raise WorkflowValidationError(
                    f"Node {node_id!r}: mcp_servers[{k!r}] must be an object", code=codes.WRONG_SHAPE
                )
            mcp_list.append((str(k), v))
        mcp_servers = tuple(mcp_list)
    else:
        mcp_servers = ()

    inherit_raw = data.get("inherit")
    inherit: InheritSpec | None = None
    if inherit_raw is not None:
        if not isinstance(inherit_raw, dict):
            raise WorkflowValidationError(
                f"Node {node_id!r}: 'inherit' must be an object like {{\"from\": \"other-node\"}}",
                code=codes.WRONG_SHAPE, node=node_id,
            )
        unknown = sorted(set(inherit_raw) - {"from"})
        if unknown:
            raise WorkflowValidationError(
                f"Node {node_id!r}: unknown 'inherit' keys {unknown}",
                code=codes.UNKNOWN_FIELD, node=node_id,
                hint="Only 'from' is defined; overrides are the node's own fields.",
            )
        source = inherit_raw.get("from")
        if not isinstance(source, str) or not source:
            raise WorkflowValidationError(
                f"Node {node_id!r}: 'inherit.from' must be a non-empty node id",
                code=codes.MISSING_REQUIRED, node=node_id,
            )
        inherit = InheritSpec(from_node=source)

    return NodeDef(
        id=node_id,
        type=node_type,
        signal=signal,
        model=data.get("model"),
        system_prompt=data.get("system_prompt", ""),
        prompt=data.get("prompt", ""),
        tools=tools,
        skills=skills,
        run=data.get("run"),
        input_ports=sub_input_ports,
        output_ports=sub_output_ports,
        code=data.get("code"),
        web_search=bool(data.get("web_search", False)),
        web_search_model=data.get("web_search_model"),
        retry=retry,
        on_error=on_error,
        deterministic=bool(data.get("deterministic", False)),
        child=child,
        backend=backend_val,
        allowed_tools=allowed_tools,
        mcp_servers=mcp_servers,
        inherit=inherit,
    )


def _parse_edge(data: dict[str, Any]) -> EdgeDef:
    when: Condition | None = None
    if "when" in data:
        when = _parse_condition(data["when"])
    loop_max: int | None = None
    loop_strict = False
    if "while" in data:
        # ``while`` loop sugar (docs/expressiveness.md B2): a strict bounded loop.
        # Accepts either ``"while": <condition>`` or the long form
        # ``"while": {"cond": <condition>, "max": N?}``.  Desugars to when=<cond> +
        # a loop bound (explicit ``max`` or a safe default); hitting the bound with
        # the condition still true is a non-convergence error (loop_strict=True),
        # unlike a plain ``loop.max`` which silently stops.
        if "loop" in data:
            raise WorkflowValidationError(
                f"Edge {data.get('from')!r}->{data.get('to')!r}: cannot use both 'while' and 'loop'",
                code=codes.INVALID_LOOP,
                hint='Keep the while form and set its bound with {"cond": ..., "max": N}.',
            )
        if "when" in data:
            raise WorkflowValidationError(
                f"Edge {data.get('from')!r}->{data.get('to')!r}: cannot use both 'while' and 'when'"
                f" ('while' already carries the loop condition)",
                code=codes.INVALID_LOOP,
            )
        raw_while = data["while"]
        if isinstance(raw_while, dict) and ("cond" in raw_while or "max" in raw_while):
            if "cond" not in raw_while:
                raise WorkflowValidationError(
                    f"Edge {data.get('from')!r}->{data.get('to')!r}: while must declare a 'cond' condition",
                    code=codes.INVALID_LOOP,
                )
            when = _parse_condition(raw_while["cond"])
            if "max" in raw_while:
                try:
                    loop_max = int(raw_while["max"])
                except (TypeError, ValueError):
                    raise WorkflowValidationError(
                        f"Edge {data.get('from')!r}->{data.get('to')!r}: while 'max' must be an"
                        f" integer, got {raw_while['max']!r}",
                        code=codes.INVALID_LOOP,
                    ) from None
                if loop_max <= 0:
                    raise WorkflowValidationError(
                        f"Edge {data.get('from')!r}->{data.get('to')!r}: while 'max' must be positive,"
                        f" got {loop_max}",
                        code=codes.INVALID_LOOP,
                    )
            else:
                loop_max = _WHILE_SAFE_MAX
        else:
            when = _parse_condition(raw_while)
            loop_max = _WHILE_SAFE_MAX
        loop_strict = True
    elif "loop" in data and isinstance(data["loop"], dict):
        if "max" not in data["loop"]:
            raise WorkflowValidationError(
                f"Edge {data.get('from')!r}->{data.get('to')!r}: loop must declare an integer 'max'",
                code=codes.INVALID_LOOP,
                hint='Bound it with {"loop": {"max": N}}, or use "while" for a condition-driven loop.',
            )
        try:
            loop_max = int(data["loop"]["max"])
        except (TypeError, ValueError):
            raise WorkflowValidationError(
                f"Edge {data.get('from')!r}->{data.get('to')!r}: loop 'max' must be an integer,"
                f" got {data['loop']['max']!r}",
                code=codes.INVALID_LOOP,
            ) from None
        if loop_max <= 0:
            raise WorkflowValidationError(
                f"Edge {data.get('from')!r}->{data.get('to')!r}: loop 'max' must be positive,"
                f" got {loop_max}",
                code=codes.INVALID_LOOP,
            )
    raw_map = data.get("map", {})
    mapping: tuple[tuple[str, str], ...]
    if isinstance(raw_map, dict) and raw_map:
        mapping = tuple((str(s), str(d)) for s, d in raw_map.items())
    else:
        mapping = ()
    fan_out: str | None = None
    raw_fan_out = data.get("fan_out")
    if raw_fan_out is not None:
        fan_out = str(raw_fan_out)
    fan_in: Literal["list", "concat"] | None = None
    raw_fan_in = data.get("fan_in")
    if raw_fan_in is not None:
        if raw_fan_in not in ("list", "concat"):
            raise WorkflowValidationError(
                f"Edge {data.get('from')!r}->{data.get('to')!r}: fan_in must be 'list' or 'concat',"
                f" got {raw_fan_in!r}",
                code=codes.INVALID_FANOUT,
            )
        fan_in = raw_fan_in
    return EdgeDef(
        src=str(data["from"]),
        dst=str(data["to"]),
        mapping=mapping,
        when=when,
        loop_max=loop_max,
        loop_strict=loop_strict,
        fan_out=fan_out,
        fan_in=fan_in,
    )


def _parse_schedule(raw: Any) -> ScheduleDef | None:
    """Parse + validate a top-level ``schedule`` block (docs/scheduling.md).

    None (absent) means run-once.  Validates mode-specific rules so a broken
    schedule fails at load, not at install time.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise WorkflowValidationError("schedule must be an object", code=codes.INVALID_SCHEDULE)
    mode = raw.get("mode")
    if mode not in ("timer", "hook"):
        raise WorkflowValidationError(
            f"schedule.mode must be 'timer' or 'hook', got {mode!r}", code=codes.INVALID_SCHEDULE
        )

    raw_inputs = raw.get("inputs", {})
    inputs: tuple[tuple[str, object], ...]
    if isinstance(raw_inputs, dict):
        inputs = tuple((str(k), v) for k, v in raw_inputs.items())
    elif not raw_inputs:
        inputs = ()
    else:
        raise WorkflowValidationError("schedule.inputs must be an object", code=codes.INVALID_SCHEDULE)

    if mode == "timer":
        every = raw.get("every")
        cron = raw.get("cron")
        if (every is None) == (cron is None):
            raise WorkflowValidationError(
                "schedule (timer) needs exactly one of 'every' or 'cron'", code=codes.INVALID_SCHEDULE
            )
        if every is not None:
            every = str(every)
            if not _EVERY_RE.match(every):
                raise WorkflowValidationError(
                    f"schedule.every must look like '30s'/'15m'/'2h'/'1d', got {every!r}",
                    code=codes.INVALID_SCHEDULE,
                )
        cron = str(cron) if cron is not None else None
        if cron is not None and len(cron.split()) != 5:
            raise WorkflowValidationError(
                f"schedule.cron must be a 5-field cron expression, got {cron!r}",
                code=codes.INVALID_SCHEDULE,
                hint="Fields are: minute hour day-of-month month day-of-week (no seconds field).",
            )
        return ScheduleDef(
            mode="timer",
            every=every,
            cron=cron,
            inputs=inputs,
            timeout=_duration(raw.get("timeout"), field="schedule.timeout"),
            jitter=_duration(raw.get("jitter"), field="schedule.jitter"),
        )

    # hook
    signal = raw.get("signal")
    if not signal or not isinstance(signal, str):
        raise WorkflowValidationError("schedule (hook) needs a non-empty 'signal'", code=codes.INVALID_SCHEDULE)
    listen = raw.get("listen")
    if not isinstance(listen, dict):
        raise WorkflowValidationError(
            "schedule (hook) needs a 'listen' object",
            code=codes.INVALID_SCHEDULE,
            hint='Say how the signal arrives, e.g. "listen": {"type": "http"}.',
        )
    ltype = listen.get("type")
    if ltype not in ("http", "file", "socket"):
        raise WorkflowValidationError(
            f"schedule.listen.type must be 'http', 'file', or 'socket', got {ltype!r}",
            code=codes.INVALID_SCHEDULE,
        )
    return ScheduleDef(
        mode="hook",
        inputs=inputs,
        signal=signal,
        listen=listen,
        timeout=_duration(raw.get("timeout"), field="schedule.timeout"),
    )


# schedule.every / .timeout / .jitter grammar: an integer followed by a unit (s/m/h/d).
_EVERY_RE = re.compile(r"^\d+[smhd]$")


def _duration(raw: Any, *, field: str) -> str | None:
    """Validate an optional duration ('30s'/'15m'/'2h'/'1d'); None when absent."""
    if raw is None:
        return None
    value = str(raw)
    if not _EVERY_RE.match(value):
        raise WorkflowValidationError(
            f"{field} must look like '30s'/'15m'/'2h'/'1d', got {value!r}", code=codes.INVALID_SCHEDULE
        )
    return value


def parse_workflow(
    data: dict[str, Any],
    *,
    base_dir: Path | None = None,
    _seen: frozenset[Path] | None = None,
) -> WorkflowDef:
    """Build a WorkflowDef from a raw dict (already parsed from JSON).

    ``base_dir`` is the directory a ``"subflow": "./child.json"`` path reference is
    resolved against (set by :func:`load_workflow`).  ``_seen`` carries the set of
    already-loaded child paths so a cyclic subflow reference (A -> B -> A) is caught
    at load time instead of recursing forever.
    """
    name = str(data.get("name", ""))
    provider = str(data.get("provider", ""))
    entry = str(data.get("entry", ""))
    default_model = str(data.get("defaults", {}).get("model", ""))
    raw_state = data.get("state", {})
    initial_state: tuple[tuple[str, object], ...]
    if isinstance(raw_state, dict):
        # Keep seed values type-native (a structured seed stays a dict/list, not a
        # Python repr); the wire format carries them through unchanged.
        initial_state = tuple((str(k), v) for k, v in raw_state.items())
    else:
        initial_state = ()

    # Optional per-$in JSON Schema (opt-in typed input signature).  A ``state`` key
    # without a schema here stays untyped; a key WITH one is type-checked.
    raw_in_schema = data.get("in_schema", {})
    in_schema: tuple[tuple[str, dict[str, object]], ...]
    if isinstance(raw_in_schema, dict) and raw_in_schema:
        parsed: list[tuple[str, dict[str, object]]] = []
        for k, v in raw_in_schema.items():
            if not isinstance(v, dict):
                raise WorkflowValidationError(
                    f"in_schema[{k!r}] must be a JSON Schema object",
                    code=codes.INVALID_SCHEMA,
                    hint='Give a schema fragment such as {"type": "string"}, not a bare type name.',
                )
            parsed.append((str(k), v))
        in_schema = tuple(parsed)
    else:
        in_schema = ()

    raw_tools = data.get("tools", {})
    tool_refs: tuple[tuple[str, str], ...]
    if isinstance(raw_tools, dict) and raw_tools:
        tool_refs = tuple((str(k), str(v)) for k, v in raw_tools.items())
    else:
        tool_refs = ()

    raw_max_concurrency = data.get("max_concurrency", 0)
    if not isinstance(raw_max_concurrency, int) or raw_max_concurrency < 0:
        raise WorkflowValidationError(
            "max_concurrency must be an int >= 0",
            code=codes.INVALID_VALUE,
            hint="Omit it or use 0 for no limit.",
        )
    max_concurrency = raw_max_concurrency

    raw_fan_max = data.get("fan_max_concurrency", 0)
    if not isinstance(raw_fan_max, int) or raw_fan_max < 0:
        raise WorkflowValidationError(
            "fan_max_concurrency must be an int >= 0",
            code=codes.INVALID_VALUE,
            hint="Omit it or use 0 for no limit.",
        )
    fan_max_concurrency = raw_fan_max

    schedule = _parse_schedule(data.get("schedule"))

    nodes = tuple(_parse_node(n, base_dir=base_dir, _seen=_seen) for n in data.get("nodes", []))
    edges = tuple(_parse_edge(e) for e in data.get("edges", []))

    return WorkflowDef(
        name=name,
        provider=provider,
        entry=entry,
        nodes=nodes,
        edges=edges,
        default_model=default_model,
        initial_state=initial_state,
        in_schema=in_schema,
        tool_refs=tool_refs,
        max_concurrency=max_concurrency,
        fan_max_concurrency=fan_max_concurrency,
        schedule=schedule,
    )


def _validate_script_node(node: NodeDef, run_re: re.Pattern[str]) -> None:
    """Validate a script node: exactly one code source; inline code compiles and
    has a ``ctx``-first signature whose remaining params match the declared input ports."""
    has_code = node.code is not None
    has_run = bool(node.run)
    if has_code and has_run:
        raise WorkflowValidationError(
            f"Script node {node.id!r}: set either 'code' or 'run', not both", code=codes.NODE_KIND_CONFLICT
        )
    if not has_code and not has_run:
        raise WorkflowValidationError(
            f"Script node {node.id!r}: must set 'code' or 'run'",
            code=codes.MISSING_REQUIRED,
            hint="'code' holds an inline function; 'run' points at an importable 'module.path:callable'.",
        )

    if has_run:
        assert node.run is not None
        if not run_re.match(node.run):
            raise WorkflowValidationError(
                f"Script node {node.id!r}: 'run' must match 'module.path:callable', got {node.run!r}",
                code=codes.INVALID_VALUE,
            )
        return

    # Inline code: must compile, and its function must be (ctx, *input_ports).
    assert node.code is not None
    try:
        tree = ast.parse(node.code, filename=f"<{node.id}>", mode="exec")
    except SyntaxError as exc:
        raise WorkflowValidationError(
            f"Script node {node.id!r}: invalid code — {exc}", code=codes.INVALID_SCRIPT
        ) from exc
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
    if len(funcs) != 1:
        raise WorkflowValidationError(
            f"Script node {node.id!r}: 'code' must define exactly one top-level function, found {len(funcs)}",
            code=codes.INVALID_SCRIPT,
            hint="Move helpers inside that function, or use 'run' to point at a module-level callable.",
        )
    fn = funcs[0]
    arg_names = [a.arg for a in fn.args.args]
    if not arg_names or arg_names[0] != "ctx":
        raise WorkflowValidationError(
            f"Script node {node.id!r}: function's first parameter must be 'ctx'", code=codes.INVALID_SCRIPT
        )
    declared = set(node.input_names)
    got = set(arg_names[1:])
    if got != declared:
        raise WorkflowValidationError(
            f"Script node {node.id!r}: function params {sorted(got)} != declared inputs {sorted(declared)}",
            code=codes.INVALID_SCRIPT,
            hint="Each declared input port becomes one parameter after 'ctx'.",
        )


def _output_port_names(wf: WorkflowDef, node_id: str, in_ports: set[str]) -> set[str]:
    """Output port names available on *node_id* (the ``$in`` source exposes state keys)."""
    if node_id == IN_NODE_ID:
        return in_ports
    for n in wf.nodes:
        if n.id == node_id:
            return set(n.output_names)
    return set()


def _jsonpath_root(key: str) -> tuple[str, bool]:
    """Return (root_field, has_subpath) for an edge-mapping JSONPath source key.

    ``$.verdict.within_budget`` -> ("verdict", True); ``$.plan`` -> ("plan", False);
    a bare ``plan`` -> ("plan", False); ``$.tasks[0]`` -> ("tasks", True).
    """
    body = key[2:] if key.startswith("$.") else (key[1:] if key.startswith("$") else key)
    # The root field ends at the first '.' or '[' separator.
    root = re.split(r"[.\[]", body, maxsplit=1)[0]
    has_sub = len(root) < len(body)
    return root, has_sub


# A ``{{ <jsonpath> }}`` placeholder — mirrors flow.interpolate._PATTERN.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*(.+?)\s*\}\}")


def _placeholder_roots(text: str) -> list[str]:
    """Root port names referenced by the ``{{ $.path }}`` placeholders in *text*.

    Skips a placeholder whose root can't be determined (an odd JSONPath),
    so strict validation only flags a clearly-wrong root.
    """
    roots: list[str] = []
    for m in _PLACEHOLDER_RE.finditer(text):
        root, _ = _jsonpath_root(m.group(1))
        if root:
            roots.append(root)
    return roots


def _condition_operand_roots(cond: Condition) -> list[str]:
    """All placeholder roots referenced by a condition tree's value/text operands."""
    roots: list[str] = []
    if cond.value is not None:
        roots += _placeholder_roots(cond.value)
    if cond.text is not None:
        roots += _placeholder_roots(cond.text)
    for child in cond.children:
        roots += _condition_operand_roots(child)
    return roots


# A JSONPath tail step: a ``.field`` name, or an ``[index]``/``[*]`` bracket.
_JSONPATH_STEP = re.compile(r"\.(\w+)|\[([^\]]*)\]")


def _jsonpath_tail_steps(key: str) -> list[str] | None:
    """Parse the tail of a mapping JSONPath into ordered descent steps.

    Returns a list where a field step is its name and a plain-index step is the
    marker ``"[]"``.  Returns None when the tail contains a construct we can't
    statically type (a wildcard ``[*]``, a filter ``[?(...)]``, recursive ``..``,
    a union) — the caller then leaves the path un-typed (lenient).
    """
    body = key[2:] if key.startswith("$.") else (key[1:] if key.startswith("$") else key)
    # Everything after the root token (the name up to the first '.' or '[').
    root_len = len(re.split(r"[.\[]", body, maxsplit=1)[0])
    tail_src = body[root_len:]
    if not tail_src:
        return []
    if ".." in tail_src:
        return None  # recursive descent — not statically typable
    steps: list[str] = []
    pos = 0
    for m in _JSONPATH_STEP.finditer(tail_src):
        if m.start() != pos:  # a gap means an unparsed construct
            return None
        pos = m.end()
        field, bracket = m.group(1), m.group(2)
        if field is not None:
            steps.append(field)
        elif bracket is not None:
            # Only a plain integer index descends to the item type.  A wildcard
            # ``[*]`` (all elements), a filter, or a quoted key is not statically
            # typable -> leave the whole path un-typed.
            b = bracket.strip()
            if b.lstrip("-").isdigit():
                steps.append("[]")
            else:
                return None
    if pos != len(tail_src):
        return None
    return steps


def _schema_subschema(schema: dict[str, object], steps: list[str]) -> dict[str, object] | None:
    """Descend *schema* along *steps* and return the complete leaf schema."""
    cur: object = schema
    for step in steps:
        if not isinstance(cur, dict):
            return None
        if step == "[]":
            cur = cur.get("items")
        else:
            props = cur.get("properties")
            if not isinstance(props, dict):
                return None
            cur = props.get(step)
        if cur is None:
            return None
    return cur if isinstance(cur, dict) else None


def _schema_subtype(schema: dict[str, object], steps: list[str]) -> str | None:
    """Descend *schema* along *steps*; return the leaf ``type`` name, or None.

    A ``field`` step reads ``schema["properties"][field]``; an ``"[]"`` step reads
    ``schema["items"]``.  Any step the schema doesn't describe (no ``properties``/
    ``items``, missing field) yields None — the path is left un-typed (lenient).
    """
    leaf = _schema_subschema(schema, steps)
    if leaf is None:
        return None
    t = leaf.get("type")
    return t if isinstance(t, str) else None


def _output_port_schemas(wf: WorkflowDef, node_id: str) -> dict[str, dict[str, object]]:
    """Map a node's output port name -> its full JSON Schema.

    ``$in`` seed keys are untyped UNLESS the workflow declares ``in_schema`` for
    them (opt-in): a declared key returns its schema (so sub-field / fan_out type
    checks apply); an undeclared key is absent (exempt), as before.
    """
    if node_id == IN_NODE_ID:
        return {k: v for k, v in wf.in_schema}
    for n in wf.nodes:
        if n.id == node_id:
            return {p.name: p.schema for p in n.output_ports}
    return {}


def _output_port_types(wf: WorkflowDef, node_id: str) -> dict[str, str]:
    """Map a node's output port name -> declared type.

    ``$in`` seed keys are untyped UNLESS declared in ``in_schema`` (opt-in): a
    declared key returns its top-level ``type``; an undeclared key is absent
    (edges out of it are exempt from type checking), as before.
    """
    if node_id == IN_NODE_ID:
        out: dict[str, str] = {}
        for k, schema in wf.in_schema:
            t = schema.get("type")
            if isinstance(t, str):
                out[k] = t
        return out
    for n in wf.nodes:
        if n.id == node_id:
            return {p.name: p.type for p in n.output_ports}
    return {}


def infer_input_schema(wf: WorkflowDef) -> dict[str, dict[str, object]]:
    """Infer a JSON Schema per ``$in`` seed key from its typed consumers.

    For each key, collect the schema of every input port fed by a plain mapped
    edge out of ``$in`` (the CONSUMER's declared type — not the seed's runtime
    value type, which is unreliable: ``"347"`` legitimately feeds an ``integer``
    port under flow's tolerant coercion).  A key is typed only when all its
    consumers agree; a key with conflicting consumers or no typed consumer is
    omitted (left untyped).

    ``fan_out`` edges never reach here: fan-out from ``$in`` already REQUIRES an
    explicit ``in_schema`` (see ``_validate_fan_out_edge``), so a workflow that
    fans out of ``$in`` has an explicit signature and skips inference entirely.
    """
    node_by_id = {n.id: n for n in wf.nodes}
    # key -> set of frozen (json) schemas seen from consumers; >1 distinct = conflict.
    seen: dict[str, list[dict[str, object]]] = {}
    for edge in wf.edges:
        if edge.src != IN_NODE_ID or edge.fan_out is not None:
            continue
        dst = node_by_id.get(edge.dst)
        if dst is None:
            continue
        dst_ports = {p.name: p.schema for p in dst.input_ports}
        for sport, dport in edge.mapping:
            head, subpath = _jsonpath_root(sport)
            if subpath or dport not in dst_ports:
                continue  # sub-field seeds / control-only ports are not typed here
            seen.setdefault(head, []).append(dst_ports[dport])
    inferred: dict[str, dict[str, object]] = {}
    for key, schemas in seen.items():
        first = schemas[0]
        if all(s == first for s in schemas):  # all consumers agree
            inferred[key] = dict(first)
        # else: conflicting consumers -> leave untyped (author must declare)
    return inferred


def workflow_input_schema(wf: WorkflowDef) -> dict[str, object]:
    """The workflow's typed input signature as a JSON Schema object.

    If the workflow declares ``in_schema`` explicitly, that IS the signature (no
    inference, no merge — the author is authoritative).  Otherwise the signature
    is inferred from each ``$in`` key's typed consumers (:func:`infer_input_schema`).
    Undeclared/uninferable keys are simply absent from the properties.
    """
    props = {k: v for k, v in wf.in_schema} if wf.in_schema else infer_input_schema(wf)
    return {"type": "object", "properties": props, "required": sorted(props)}


def workflow_output_schema(wf: WorkflowDef) -> dict[str, object]:
    """The workflow's output signature as a JSON Schema object, derived statically.

    For every edge into the ``$output`` sink, the mapped output key takes the
    schema of its source node's output port or statically-resolvable JSONPath leaf.
    The result is ``{"type": "object", "properties": {out_key: port_schema, ...}}``.
    A key whose source has no schema, or whose path cannot be typed statically, is
    typed as a bare ``string`` (the wire default). A ``when``-guarded ``$output``
    edge still contributes its key (the schema is static; the guard is runtime).
    """
    props: dict[str, object] = {}
    for edge in wf.edges:
        if edge.dst != OUT_NODE_ID:
            continue
        src_schemas = _output_port_schemas(wf, edge.src)
        for sport, okey in edge.mapping:
            head, subpath = _jsonpath_root(sport)
            schema = src_schemas.get(head)
            if subpath and isinstance(schema, dict):
                steps = _jsonpath_tail_steps(sport)
                schema = _schema_subschema(schema, steps) if steps is not None else None
            source_schema = dict(schema) if isinstance(schema, dict) else {"type": "string"}
            if edge.fan_in == "list":
                # One value per worker instance becomes an ordered aggregate array.
                props[okey] = {"type": "array", "items": source_schema}
            elif edge.fan_in == "concat":
                # Each instance emits an array, but aggregate cardinality differs from
                # any one instance. Preserve only its element schema, not whole-array
                # constraints such as minItems/maxItems/uniqueItems.
                items = source_schema.get("items")
                props[okey] = (
                    {"type": "array", "items": dict(items)}
                    if isinstance(items, dict)
                    else {"type": "array"}
                )
            else:
                props[okey] = source_schema
    return {"type": "object", "properties": props, "required": sorted(props)}



def _validate_inherit(
    node: NodeDef,
    *,
    node_by_id: dict[str, NodeDef],
    node_index: dict[str, int],
    fan_out_workers: set[str],
    ancestors: dict[str, set[str]],
) -> None:
    """Check that a node's `inherit.from` names a session it can actually get.

    Strict here, lenient at run time — and the split is deliberate. A missing
    session is tolerated when the run happens (the first pass of a
    self-inheriting loop has none yet, and a skipped branch never produces one),
    so a typo in `from` would otherwise do nothing at all and never say so. The
    reference is therefore checked before anything runs; only its *absence* is
    forgiven later.
    """
    spec = node.inherit
    if spec is None:
        return
    source_id = spec.from_node

    source = node_by_id.get(source_id)
    if source is None:
        raise WorkflowValidationError(
            f"Node {node.id!r}: inherit.from {source_id!r} not found in nodes",
            code=codes.UNKNOWN_REFERENCE,
        )
    if source.type != "agent":
        raise WorkflowValidationError(
            f"Node {node.id!r}: inherit.from {source_id!r} is a {source.type!r} node, "
            "which has no agent session",
            code=codes.NODE_KIND_CONFLICT,
        )
    if source.backend is not None or node.backend is not None:
        raise WorkflowValidationError(
            f"Node {node.id!r}: 'inherit' needs the in-process SDK on both ends, but "
            f"a CLI backend is set",
            code=codes.NODE_KIND_CONFLICT,
            hint="A CLI agent owns its own session; flow cannot read or seed it.",
        )
    if source.deterministic:
        raise WorkflowValidationError(
            f"Node {node.id!r}: cannot inherit from {source_id!r}, which is deterministic",
            code=codes.NODE_KIND_CONFLICT,
            hint="A memoised node returns its stored ports without running, so it "
                 "never produces a session to inherit.",
        )
    if source_id in fan_out_workers:
        raise WorkflowValidationError(
            f"Node {node.id!r}: cannot inherit from {source_id!r}, a fan-out worker (v1)",
            code=codes.INVALID_FANOUT,
            hint="A fanned node runs N times under one id, so 'the' session is ambiguous.",
        )

    # Self-inheritance is the loop case: the node keeps its own context across
    # iterations. Everything else must be an earlier node that always runs.
    if source_id == node.id:
        return
    if node_index.get(source_id, 0) >= node_index.get(node.id, 0):
        raise WorkflowValidationError(
            f"Node {node.id!r}: inherit.from {source_id!r} is not declared before it",
            code=codes.GRAPH_INCOMPLETE,
            hint="Nodes run in declaration order; a node can only inherit from an "
                 "earlier one, or from itself.",
        )
    if source_id not in ancestors.get(node.id, set()):
        raise WorkflowValidationError(
            f"Node {node.id!r}: inherit.from {source_id!r} is not guaranteed to run first",
            code=codes.GRAPH_INCOMPLETE,
            hint="Only an unconditional ancestor always produces a session; a "
                 "'when'-gated one may never run.",
        )


def _detect_cycle(wf: WorkflowDef) -> list[str] | None:
    """Return a cycle path over non-loop edges, or None if the graph is acyclic.

    A legal bounded loop declares ``loop.max`` on its back-edge, so those edges
    are excluded — only unbounded cycles (which would never terminate) are
    reported.  ``$in``/``$output`` are excluded (a source and a sink).  Uses an
    iterative white/grey/black DFS so a large graph can't blow the stack.
    """
    adj: dict[str, list[str]] = defaultdict(list)
    for e in wf.edges:
        if e.loop_max is not None:
            continue  # bounded loop back-edge is legal
        if e.src in (IN_NODE_ID, OUT_NODE_ID) or e.dst in (IN_NODE_ID, OUT_NODE_ID):
            continue
        adj[e.src].append(e.dst)

    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[str, int] = {n.id: WHITE for n in wf.nodes}
    # (node, index-into-neighbours, path-so-far). path lets us report the cycle.
    for start in (n.id for n in wf.nodes):
        if color[start] != WHITE:
            continue
        stack: list[tuple[str, int, list[str]]] = [(start, 0, [start])]
        color[start] = GREY
        while stack:
            node, idx, path = stack[-1]
            neighbours = adj.get(node, ())
            if idx < len(neighbours):
                stack[-1] = (node, idx + 1, path)
                nxt = neighbours[idx]
                if color.get(nxt, BLACK) == GREY:
                    # Found a back-edge into the current DFS stack — a cycle.
                    return [*path[path.index(nxt):], nxt]
                if color.get(nxt, BLACK) == WHITE:
                    color[nxt] = GREY
                    stack.append((nxt, 0, [*path, nxt]))
            else:
                color[node] = BLACK
                stack.pop()
    return None


def _validate_fan_out_edge(
    wf: WorkflowDef,
    edge: EdgeDef,
    src_outputs: set[str],
    loop_touched: set[str],
) -> None:
    """Validate a ``fan_out`` edge (G1): the named source port must be an ARRAY
    output; the worker must not be a loop node or itself a fan-out worker (no
    nesting); the array must be one of the edge's mapping sources."""
    assert edge.fan_out is not None
    label = f"Edge {edge.src!r}->{edge.dst!r}"
    if edge.fan_out not in src_outputs:
        raise WorkflowValidationError(
            f"{label}: fan_out names {edge.fan_out!r} which is not an output port of {edge.src!r}",
            code=codes.UNKNOWN_REFERENCE,
        )
    # The fan_out port must be declared as an array.  A real node's output port
    # carries its schema; a ``$in`` seed carries one only when declared in
    # ``in_schema`` (opt-in typed input) — an undeclared ``$in`` key stays untyped
    # and cannot drive a typed fan-out.
    src_schemas = _output_port_schemas(wf, edge.src)
    if edge.src == IN_NODE_ID and edge.fan_out not in src_schemas:
        raise WorkflowValidationError(
            f"{label}: fan_out from {IN_NODE_ID} requires an array in_schema for {edge.fan_out!r} "
            f"(untyped seed cannot drive fan-out)",
            code=codes.INVALID_FANOUT,
            hint='Declare it in in_schema, e.g. {"type": "array", "items": {"type": "string"}}.',
        )
    src_schema = src_schemas.get(edge.fan_out, {})
    if src_schema.get("type") != "array":
        raise WorkflowValidationError(
            f"{label}: fan_out port {edge.fan_out!r} must be an array output, "
            f"got type {src_schema.get('type', 'string')!r}",
            code=codes.INVALID_FANOUT,
        )
    # The fan_out port must appear as a mapping source (its elements feed the worker).
    if edge.fan_out not in {_jsonpath_root(s)[0] for s, _ in edge.mapping}:
        raise WorkflowValidationError(
            f"{label}: fan_out port {edge.fan_out!r} must be mapped to a worker input port",
            code=codes.INVALID_FANOUT,
            hint='Add it to the edge map, e.g. "map": {"items": "item"} — each element feeds one worker.',
        )
    # v1 scope guards: no worker inside a loop, no fan_out on a loop back-edge, no nesting.
    if edge.loop_max is not None:
        raise WorkflowValidationError(
            f"{label}: a fan_out edge may not be a bounded loop back-edge", code=codes.INVALID_FANOUT
        )
    if edge.dst in loop_touched:
        raise WorkflowValidationError(
            f"{label}: fan-out worker {edge.dst!r} may not also be part of a bounded loop (v1)",
            code=codes.INVALID_FANOUT,
        )
    if any(e.fan_out is not None and e.dst == edge.src for e in wf.edges):
        raise WorkflowValidationError(
            f"{label}: nested fan-out is not supported (v1) — {edge.src!r} is itself a fan-out worker",
            code=codes.INVALID_FANOUT,
        )


def _validate_fan_in_edge(edge: EdgeDef, fan_out_workers: set[str]) -> None:
    """Validate a ``fan_in`` edge (G1): its source must be a fan-out worker."""
    assert edge.fan_in is not None
    if edge.src not in fan_out_workers:
        raise WorkflowValidationError(
            f"Edge {edge.src!r}->{edge.dst!r}: fan_in source {edge.src!r} is not a fan-out worker "
            f"(no incoming fan_out edge feeds it)",
            code=codes.INVALID_FANOUT,
        )


def _validate_concat_source(
    wf: WorkflowDef,
    edge: EdgeDef,
    sport: str,
    head: str,
    subpath: bool,
) -> None:
    """Require concat to read a whole array-valued worker output port."""
    if edge.fan_in != "concat":
        return
    # The worker store is already ``port -> [value_i]``; subpaths address that
    # aggregate, not each instance independently, so they are not concat sources.
    src_types = _output_port_types(wf, edge.src)
    item_type = src_types.get(head) if not subpath else None
    if item_type != "array":
        raise WorkflowValidationError(
            f"Edge {edge.src!r}->{edge.dst!r}: fan_in='concat' source {sport!r} must be"
            " a whole array-valued worker output port",
            code=codes.INVALID_FANOUT,
            hint="Map the port itself (no JSONPath sub-field), or use fan_in='list' to collect one value each.",
        )


def _validate_loop_regions(wf: WorkflowDef) -> None:
    """Validate loop sources and reject crossing forward invalidation regions."""
    loop_edges = [edge for edge in wf.edges if edge.loop_max is not None]
    if not loop_edges:
        return
    spec = build_frontier_spec(wf)
    raw_regions = spec["invalidation_regions"]
    if not isinstance(raw_regions, dict):
        raise WorkflowValidationError("Invalid frontier loop-region metadata", code=codes.INVALID_LOOP)
    regions = {
        str(destination): {str(node) for node in region}
        for destination, region in raw_regions.items()
        if isinstance(region, tuple)
    }
    for edge in loop_edges:
        if edge.src not in regions.get(edge.dst, set()):
            raise WorkflowValidationError(
                f"Loop edge {edge.src!r}->{edge.dst!r}: source must be forward-reachable "
                "from its loop destination",
                code=codes.INVALID_LOOP,
            )
    destinations = tuple(regions)
    for index, left in enumerate(destinations):
        for right in destinations[index + 1 :]:
            overlap = regions[left] & regions[right]
            if overlap and not (regions[left] <= regions[right] or regions[right] <= regions[left]):
                shared = ", ".join(sorted(overlap))
                raise WorkflowValidationError(
                    f"Loop regions for {left!r} and {right!r} cross without nesting "
                    f"(shared nodes: {shared})",
                    code=codes.INVALID_LOOP,
                    hint="Nest one region fully inside the other, or move the shared nodes out of one loop.",
                )


def _validate_subflow_node(node: NodeDef) -> None:
    """Validate a ``type="subflow"`` node (G5): inline child, no nesting, strict
    signature, and the mutually-exclusive-field rules.  Recursively validates the
    child so a broken child fails at the parent's load time."""
    if node.child is None:
        raise WorkflowValidationError(
            f"Subflow node {node.id!r}: missing inline child workflow", code=codes.INVALID_SUBFLOW
        )
    if node.code is not None:
        raise WorkflowValidationError(f"Subflow node {node.id!r} must not set 'code'", code=codes.NODE_KIND_CONFLICT)
    if node.prompt or node.system_prompt:
        raise WorkflowValidationError(f"Subflow node {node.id!r} must not set a prompt", code=codes.NODE_KIND_CONFLICT)
    if node.tools:
        raise WorkflowValidationError(
            f"Subflow node {node.id!r} must not set 'tools'", code=codes.NODE_KIND_CONFLICT
        )
    # v1: no nested subflows (recursion is structurally impossible with inline
    # children, but a child may still itself contain a subflow node — reject that).
    if any(cn.type == "subflow" for cn in node.child.nodes):
        raise WorkflowValidationError(
            f"Subflow node {node.id!r}: nested sub-workflows are not supported (v1)",
            code=codes.INVALID_SUBFLOW,
        )
    # Recursively validate the child (a broken child fails here).
    validate_workflow(node.child)
    # Strict signature (option A): every derived input port must be typed.  A key
    # that is neither declared in the child's in_schema nor inferable is dropped by
    # workflow_input_schema, so an unfed parent mapping to it would fail the normal
    # port check; but a fed key with a bare "string" fallback from an untyped
    # consumer is still allowed (string is a concrete type).  Nothing more to do:
    # the derived ports already ARE the strict signature.


class _ErrorCollector:
    """Gathers per-item validation errors instead of stopping at the first.

    An authoring agent handed one error per run needs one round trip per error,
    which is the cost the repair-loop literature says to avoid.  The per-node and
    per-edge loops are the seams where continuing is sound: each iteration is
    independent, so a failure in one says nothing about the next.  Checks outside
    those loops still abort, because everything after them reads state they were
    supposed to establish.
    """

    def __init__(self) -> None:
        self.errors: list[WorkflowValidationError] = []

    @contextlib.contextmanager
    def item(
        self,
        *,
        node: str | None = None,
        edge: tuple[str, str] | None = None,
    ) -> Iterator[None]:
        """Absorb a failure for one node or edge and carry on to the next."""
        try:
            yield
        except WorkflowValidationError as exc:
            if exc.node is None:
                exc.node = node
            if exc.edge is None:
                exc.edge = edge
            self.errors.append(exc)


def _validate_skills(node: NodeDef, base_dir: Path | None) -> None:
    """Every named skill must resolve from an installed package.

    Failing at load rather than at run is the whole point: an agent told to
    produce a format it was never shown does not fail, it produces something
    plausible and wrong. That is not hypothetical -- a scheduling workflow was
    written with an invented node type by an agent that had no access to the
    skill describing the format, and every check downstream passed.
    """
    from xdog.agent.skills import resolvable_skill

    search = [base_dir / "skills"] if base_dir is not None else []
    for slug in node.skills:
        if not resolvable_skill(slug, search):
            raise WorkflowValidationError(
                f"Node {node.id!r} references unknown skill {slug!r}. "
                f"A skill comes from an installed package or from a skills/ directory "
                f"beside this workflow file.",
                code=codes.UNKNOWN_REFERENCE,
            )


def unconfinable_reasons(wf: WorkflowDef) -> list[str]:
    """Why this workflow cannot be confined to a workspace, if it cannot.

    What is left here is what runs **outside this process**, because that is the
    boundary of what can be enforced:

    * the ``bash`` tool — a shell is a general-purpose escape;
    * a CLI backend — the subprocess owns its own filesystem access.

    Script nodes used to be listed and are not any more. They run in the
    executor's own interpreter, which is exactly why an audit hook can hold them:
    :func:`xdog.agent.workspace.script_bound` refuses paths outside the workspace
    for every script node, confined or not, and under ``--confined`` also refuses
    the calls it cannot follow — ``subprocess``, ``ctypes``, ``os.system``. So an
    inline script is no longer a reason to refuse the whole workflow; it is a
    thing that gets stopped when it actually reaches for something.

    Returns one human-readable reason per offending node, empty when the
    workflow can be confined.
    """
    reasons: list[str] = []
    for node in wf.nodes:
        if node.backend is not None:
            reasons.append(
                f"node {node.id!r}: the {node.backend!r} backend owns its own session "
                f"and filesystem access"
            )
        if "bash" in node.tools:
            reasons.append(f"node {node.id!r}: the 'bash' tool is a general-purpose escape")
        if node.child is not None:
            reasons.extend(
                f"subflow {node.id!r} -> {reason}" for reason in unconfinable_reasons(node.child)
            )
    return reasons


def validation_errors(
    wf: WorkflowDef, *, base_dir: Path | None = None
) -> list[WorkflowValidationError]:
    """Every validation problem found, in declaration order; empty when valid.

    A fatal structural failure (a duplicate node id, an unbounded cycle) stops
    the pass and is returned alone: the checks after it read state it was meant
    to establish, so continuing would report failures that are artefacts.
    """
    collector = _ErrorCollector()
    try:
        _validate_workflow_into(wf, collector, base_dir=base_dir)
    except WorkflowValidationError as exc:
        collector.errors.append(exc)
    return collector.errors


def validate_workflow(wf: WorkflowDef, *, base_dir: Path | None = None) -> None:
    """Validate a WorkflowDef. Raises WorkflowValidationError on any problem.

    *base_dir* is where the workflow was loaded from, when it came from a file:
    a run-time fact, so it is passed rather than carried on the definition. It
    lets a `skills/` directory beside the workflow resolve.
    """
    errors = validation_errors(wf, base_dir=base_dir)
    if errors:
        raise errors[0]


def _validate_workflow_into(
    wf: WorkflowDef, collector: "_ErrorCollector", *, base_dir: Path | None = None
) -> None:
    """Run every check, routing per-item failures into *collector*."""
    node_ids = [n.id for n in wf.nodes]

    for nid in node_ids:
        if not nid:
            raise WorkflowValidationError("Node id must be non-empty", code=codes.MISSING_REQUIRED)
        if nid == IN_NODE_ID:
            raise WorkflowValidationError(
                f"Node id {IN_NODE_ID!r} is reserved for the workflow input source",
                code=codes.DUPLICATE_OR_RESERVED_ID,
            )
        if nid == OUT_NODE_ID:
            raise WorkflowValidationError(
                f"Node id {OUT_NODE_ID!r} is reserved for the workflow output sink",
                code=codes.DUPLICATE_OR_RESERVED_ID,
            )
    if len(node_ids) != len(set(node_ids)):
        raise WorkflowValidationError(f"Duplicate node ids: {node_ids}", code=codes.DUPLICATE_OR_RESERVED_ID)

    # $in is index -1 (earliest); real nodes are 0..n-1 in declaration order;
    # $output is index n (latest) so edges into it are always forward.
    node_index: dict[str, int] = {nid: i for i, nid in enumerate(node_ids)}
    node_index[IN_NODE_ID] = -1
    node_index[OUT_NODE_ID] = len(node_ids)
    node_by_id = {n.id: n for n in wf.nodes}
    in_ports = {k for k, _ in wf.initial_state}

    # Reject an unbounded cycle first (a loop without a declared loop.max would
    # never terminate).  Bounded loops declare loop.max and are excluded.  Doing
    # this before the entry check gives the more actionable error: a full cycle
    # leaves an empty entry frontier, but "cycle" explains why.
    cycle = _detect_cycle(wf)
    if cycle is not None:
        raise WorkflowValidationError(
            f"Workflow has a cycle with no bounded loop: {' -> '.join(cycle)}. "
            f"Add loop.max to a back-edge to make it a bounded loop.",
            code=codes.INVALID_LOOP,
        )

    # ``entry`` is optional: when set it names a single start node (must exist);
    # when empty the entry frontier is derived from the nodes that depend only on
    # ``$in``.  Either way at least one start node must exist.
    if wf.entry and wf.entry not in node_by_id:
        raise WorkflowValidationError(f"Entry node {wf.entry!r} not found in nodes", code=codes.UNKNOWN_REFERENCE)
    if not entry_frontier(wf):
        raise WorkflowValidationError(
            "Workflow has no entry node: it has no nodes, or every node has a predecessor so "
            "nothing can start. Feed at least one node from $in (or set an explicit entry).",
            code=codes.GRAPH_INCOMPLETE,
        )

    # A provider is required only if the workflow has an SDK agent node (an agent
    # node with no CLI backend).  A pure-CLI (or script-only) workflow needs none —
    # a CLI agent node reuses the CLI's own auth.  See docs/cli-agent.md §7.
    _has_sdk_agent = any(n.type == "agent" and n.backend is None for n in wf.nodes)
    if _has_sdk_agent and not wf.provider:
        raise WorkflowValidationError(
            "Workflow has an SDK agent node but no 'provider'. Set a provider, or give the "
            "agent node a CLI 'backend' (e.g. 'claude-cli') which needs no provider.",
            code=codes.PROVIDER_REQUIRED,
        )

    _run_re = re.compile(r"^[\w.]+:[\w]+$")

    # Validate the custom-tool manifest and build the set of resolvable tool
    # names (built-ins + manifest keys) so unknown node tool names fail fast.
    from xdog.flow.tools import default_repo

    manifest_names: set[str] = set()
    for tname, ref in wf.tool_refs:
        if not tname:
            raise WorkflowValidationError("Tool manifest: tool name must be non-empty", code=codes.MISSING_REQUIRED)
        if not _run_re.match(ref):
            raise WorkflowValidationError(
                f"Tool {tname!r}: ref must match 'module.path:callable', got {ref!r}",
                code=codes.INVALID_VALUE,
                hint="Left of ':' is an importable module, right of it a callable in that module.",
            )
        manifest_names.add(tname)
    known_tools = default_repo().names() | manifest_names

    for node in wf.nodes:
        with collector.item(node=node.id):
            # Every declared port type must be one of the known JSON types, so a typo
            # like "int" fails at load time instead of surfacing later in to_python.
            for _port in (*node.input_ports, *node.output_ports):
                if _port.type not in VALID_TYPES:
                    raise WorkflowValidationError(
                        f"Node {node.id!r}: port {_port.name!r} has unknown type {_port.type!r}; "
                        f"expected one of {', '.join(VALID_TYPES)}",
                        code=codes.INVALID_SCHEMA,
                    )
            _validate_skills(node, base_dir)
            for tool in node.tools:
                if not tool:
                    raise WorkflowValidationError(
                        f"Node {node.id!r}: tool name must be non-empty", code=codes.MISSING_REQUIRED
                    )
                if tool not in known_tools:
                    known = ", ".join(sorted(known_tools)) or "<none>"
                    raise WorkflowValidationError(
                        f"Node {node.id!r} references unknown tool {tool!r}. Known tools: {known}",
                        code=codes.UNKNOWN_REFERENCE,
                        hint="Register custom tools in the workflow's top-level 'tools' manifest.",
                    )
            if node.type == "script":
                _validate_script_node(node, _run_re)
            elif node.type == "agent":
                if node.run is not None:
                    raise WorkflowValidationError(
                        f"Agent node {node.id!r} must not set 'run'",
                        code=codes.NODE_KIND_CONFLICT,
                        hint="Use type='script' for a node that calls a Python callable.",
                    )
                if node.code is not None:
                    raise WorkflowValidationError(
                        f"Agent node {node.id!r} must not set 'code'",
                        code=codes.NODE_KIND_CONFLICT,
                        hint="Use type='script' for a node whose body is inline Python.",
                    )
                if node.backend is not None and node.backend not in _KNOWN_CLI_BACKENDS:
                    known = ", ".join(sorted(_KNOWN_CLI_BACKENDS))
                    raise WorkflowValidationError(
                        f"Agent node {node.id!r}: unknown backend {node.backend!r}; expected one of {known}",
                        code=codes.INVALID_VALUE,
                    )
                if node.backend is None and (node.allowed_tools or node.mcp_servers):
                    raise WorkflowValidationError(
                        f"Agent node {node.id!r}: 'allowed_tools'/'mcp_servers' require a CLI 'backend'",
                        code=codes.NODE_KIND_CONFLICT,
                        hint="Set a CLI 'backend', or drop these fields — the SDK path has no equivalent.",
                    )
                # Strict interpolation: every {{ $.x }} in a prompt must root at a
                # declared input port (a typo would otherwise silently interpolate to "").
                _in_names = set(node.input_names)
                for _tmpl in (node.system_prompt, node.prompt):
                    for _root in _placeholder_roots(_tmpl):
                        if _root not in _in_names:
                            raise WorkflowValidationError(
                                f"Agent node {node.id!r}: prompt references {{{{ ${_root} }}}} but "
                                f"{_root!r} is not a declared input port",
                                code=codes.INVALID_TEMPLATE,
                                hint="Add an input port with that name and an edge mapping that feeds it.",
                            )
            elif node.type == "human":
                if not node.signal:
                    raise WorkflowValidationError(
                        f"Human node {node.id!r} must declare a non-empty 'signal'", code=codes.MISSING_REQUIRED
                    )
                if node.code is not None:
                    raise WorkflowValidationError(
                        f"Human node {node.id!r} must not set 'code'", code=codes.NODE_KIND_CONFLICT
                    )
                if node.run is not None:
                    raise WorkflowValidationError(
                        f"Human node {node.id!r} must not set 'run'", code=codes.NODE_KIND_CONFLICT
                    )
                if node.prompt:
                    raise WorkflowValidationError(
                        f"Human node {node.id!r} must not set 'prompt'", code=codes.NODE_KIND_CONFLICT
                    )
                if node.tools:
                    raise WorkflowValidationError(
                        f"Human node {node.id!r} must not set 'tools'", code=codes.NODE_KIND_CONFLICT
                    )
                if len(node.output_ports) > 1:
                    raise WorkflowValidationError(
                        f"Human node {node.id!r} may declare at most one output port",
                        code=codes.NODE_KIND_CONFLICT,
                        hint="A human node emits the one value supplied on resume; use two nodes for two values.",
                    )
            elif node.type == "subflow":
                _validate_subflow_node(node)

    # Edges: endpoints exist, ports exist, mappings are well-formed, loops bounded.
    # fed[(dst_node, dst_port)] counts feeding data edges — every input port needs
    # one; unconditional_fed counts only always-on (non-when, non-loop) feeders, so
    # two producers can't silently target the same port (the old shared-key clash).
    fed: dict[tuple[str, str], int] = {}
    unconditional_fed: dict[tuple[str, str], int] = {}
    # Dynamic fan-out (G1) topology: a fan-out worker is the dst of a fan_out edge;
    # loop_touched nodes appear on either end of a bounded-loop edge.  Used to reject
    # a worker inside a loop, nested fan-out, and a fan_in whose src isn't a worker.
    fan_out_workers = {e.dst for e in wf.edges if e.fan_out is not None}
    loop_edges = [e for e in wf.edges if e.loop_max is not None]
    loop_touched = {e.src for e in loop_edges} | {e.dst for e in loop_edges}
    # v1 scope guards for subflow nodes: no subflow inside a loop or as a fan-out
    # worker (the loop×subflow and fan×subflow interactions are out of scope; the
    # fan-out limiter is the prerequisite for the latter).
    for _n in wf.nodes:
        if _n.type != "subflow":
            continue
        if _n.id in loop_touched:
            raise WorkflowValidationError(
                f"Subflow node {_n.id!r} may not be part of a bounded loop (v1)",
                code=codes.INVALID_SUBFLOW,
            )
        if _n.id in fan_out_workers:
            raise WorkflowValidationError(
                f"Subflow node {_n.id!r} may not be a fan-out worker (v1)",
                code=codes.INVALID_SUBFLOW,
            )
    # `inherit` needs the fan-out and ordering facts above, so it is checked here
    # rather than in the per-node agent branch.
    _unconditional_ancestors: dict[str, set[str]] = {}
    for _n in wf.nodes:
        seen: set[str] = set()
        frontier = [
            e.src for e in wf.edges
            if e.dst == _n.id and e.when is None and e.loop_max is None
            and e.src not in (IN_NODE_ID, OUT_NODE_ID)
        ]
        while frontier:
            current = frontier.pop()
            if current in seen:
                continue
            seen.add(current)
            frontier.extend(
                e.src for e in wf.edges
                if e.dst == current and e.when is None and e.loop_max is None
                and e.src not in (IN_NODE_ID, OUT_NODE_ID)
            )
        _unconditional_ancestors[_n.id] = seen
    for _n in wf.nodes:
        with collector.item(node=_n.id):
            _validate_inherit(
                _n,
                node_by_id=node_by_id,
                node_index=node_index,
                fan_out_workers=fan_out_workers,
                ancestors=_unconditional_ancestors,
            )
    for edge in wf.edges:
        with collector.item(edge=(edge.src, edge.dst)):
            if edge.src == OUT_NODE_ID:
                raise WorkflowValidationError(
                    f"Edge src {OUT_NODE_ID!r} is not allowed ($output is a sink only)",
                    code=codes.DUPLICATE_OR_RESERVED_ID,
                    hint="Read from the node that produced the value; $output only collects results.",
                )
            if edge.src != IN_NODE_ID and edge.src not in node_by_id:
                raise WorkflowValidationError(
                    f"Edge src {edge.src!r} not found in nodes", code=codes.UNKNOWN_REFERENCE
                )
            if edge.dst != OUT_NODE_ID and edge.dst not in node_by_id:
                raise WorkflowValidationError(
                    f"Edge dst {edge.dst!r} not found in nodes", code=codes.UNKNOWN_REFERENCE
                )
            if edge.dst == IN_NODE_ID:
                raise WorkflowValidationError(
                    f"Edge dst {IN_NODE_ID!r} is not allowed ($in is a source only)",
                    code=codes.DUPLICATE_OR_RESERVED_ID,
                    hint="Seed values in the workflow's 'state' block; $in only feeds edges.",
                )
            # Strict ``while`` is a public-model invariant too (not only loader sugar):
            # it must have both a positive bound and a condition to converge against.
            if edge.loop_strict and (edge.loop_max is None or edge.loop_max < 1 or edge.when is None):
                raise WorkflowValidationError(
                    f"Edge {edge.src!r} -> {edge.dst!r}: loop_strict requires loop_max >= 1 and a 'when' condition",
                    code=codes.INVALID_LOOP,
                )

            src_outputs = _output_port_names(wf, edge.src, in_ports)
            # Strict interpolation for a condition: every {{ $.x }} operand must root at
            # an output port of the edge's source node (or a $in seed key).
            if edge.when is not None:
                for _root in _condition_operand_roots(edge.when):
                    if _root not in src_outputs:
                        raise WorkflowValidationError(
                            f"Edge {edge.src!r}->{edge.dst!r}: condition references {{{{ ${_root} }}}} but "
                            f"{_root!r} is not an output port of {edge.src!r}",
                            code=codes.INVALID_TEMPLATE,
                        )

            # Dynamic fan-out (G1) edge-shape validation.
            if edge.fan_out is not None:
                _validate_fan_out_edge(wf, edge, src_outputs, loop_touched)
            if edge.fan_in is not None:
                _validate_fan_in_edge(edge, fan_out_workers)
            # $output is a free-form sink: its destination "ports" are arbitrary output
            # keys, so only the source port must exist (no declared input ports to check,
            # and it never needs to be "fed").
            if edge.dst == OUT_NODE_ID:
                for sport, _dport in edge.mapping:
                    head, subpath = _jsonpath_root(sport)
                    # A sub-field key (``$.verdict.within_budget``) into $output resolves
                    # a nested field of a structured source port at run time (both engines
                    # use jsonpath_get); its root must still be a real output port.
                    if head not in src_outputs:
                        raise WorkflowValidationError(
                            f"Edge {edge.src!r}->{edge.dst!r}: source has no output port {head!r}",
                            code=codes.UNKNOWN_REFERENCE,
                        )
                    _validate_concat_source(wf, edge, sport, head, subpath)
                continue

            dst_inputs = set(node_by_id[edge.dst].input_names)
            src_types = _output_port_types(wf, edge.src)  # empty for $in (untyped seed)
            src_schemas = _output_port_schemas(wf, edge.src)  # for sub-field type checks
            dst_types = {p.name: p.type for p in node_by_id[edge.dst].input_ports}
            for sport, dport in edge.mapping:
                # A JSONPath source key (``$.plan.owner``) reads a nested field of a
                # structured port; its root must be a real output port.
                head, subpath = _jsonpath_root(sport)
                if head not in src_outputs:
                    raise WorkflowValidationError(
                        f"Edge {edge.src!r}->{edge.dst!r}: source has no output port {head!r}",
                        code=codes.UNKNOWN_REFERENCE,
                    )
                if dport not in dst_inputs:
                    raise WorkflowValidationError(
                        f"Edge {edge.src!r}->{edge.dst!r}: destination has no input port {dport!r}",
                        code=codes.UNKNOWN_REFERENCE,
                    )
                # concat flattens one whole array emitted by each worker instance.
                _validate_concat_source(wf, edge, sport, head, subpath)

                # Edge type consistency: the resolved source type must match the
                # destination input type.  $in seeds are untyped (absent from
                # src_types/src_schemas) so edges out of $in are exempt.
                if dport in dst_types and (head in src_types or head in src_schemas):
                    src_type: str | None
                    if edge.fan_in is not None:
                        # fan_in (G1): both reducers feed an array to the collector.
                        src_type = "array"
                    elif edge.fan_out is not None and head == edge.fan_out and not subpath:
                        # fan_out (G1): the worker consumes ONE array element, so the
                        # element (items) type must match — not the array itself.
                        src_type = _schema_subtype(src_schemas[head], ["[]"]) if head in src_schemas else None
                    elif not subpath:
                        src_type = src_types.get(head)
                    else:
                        # 2b-2: descend the source port's schema along the JSONPath tail
                        # to find the sub-field type.  An un-typable path (wildcard,
                        # filter, scalar interior, missing field) yields None -> lenient.
                        steps = _jsonpath_tail_steps(sport)
                        src_type = (
                            _schema_subtype(src_schemas[head], steps)
                            if steps is not None and head in src_schemas
                            else None
                        )
                    if src_type is not None and src_type != dst_types[dport]:
                        raise WorkflowValidationError(
                            f"Edge {edge.src!r}->{edge.dst!r}: type mismatch — source {sport!r} is "
                            f"{src_type!r} but destination port {dport!r} is {dst_types[dport]!r}",
                            code=codes.TYPE_MISMATCH,
                            hint="Change one of the two port schemas, or convert the value in a script node.",
                        )
                fed[(edge.dst, dport)] = fed.get((edge.dst, dport), 0) + 1
                if edge.when is None and edge.loop_max is None:
                    unconditional_fed[(edge.dst, dport)] = unconditional_fed.get((edge.dst, dport), 0) + 1

            # back-edge (dst not strictly after src) must be a bounded loop
            if node_index[edge.dst] <= node_index[edge.src]:
                if not (edge.loop_max is not None and edge.loop_max >= 1):
                    raise WorkflowValidationError(
                        f"Back-edge {edge.src!r} -> {edge.dst!r} must have loop.max >= 1",
                        code=codes.INVALID_LOOP,
                        hint="Nodes run in declaration order, so an edge to an earlier node is a loop.",
                    )

            # G6: a bounded loop with no `when` guard runs the full loop_max every time,
            # which is almost always an authoring mistake (a loop usually exits early on
            # a condition).  Warn, don't reject — an unconditional N-times loop is legal.
            if edge.loop_max is not None and edge.when is None:
                warnings.warn(
                    f"Edge {edge.src!r} -> {edge.dst!r}: loop.max={edge.loop_max} with no 'when' guard "
                    f"runs all {edge.loop_max} iterations unconditionally (usually a mistake — add a "
                    f"'when' to exit early).",
                    FlowWarning,
                    stacklevel=2,
                )

    _validate_loop_regions(wf)

    # Two unconditional producers into one input port is the old shared-key clash.
    for (dst, port), count in unconditional_fed.items():
        if count > 1:
            raise WorkflowValidationError(
                f"Node {dst!r}: input port {port!r} is fed by {count} unconditional edges "
                f"(ambiguous producer; use conditional edges if mutually exclusive)",
                code=codes.AMBIGUOUS_INPUT,
            )

    # Every declared input port must be fed by at least one edge mapping — unless
    # it is not required (e.g. a loop-carried value absent on the first pass).
    for node in wf.nodes:
        with collector.item(node=node.id):
            for p in node.input_ports:
                if not p.required:
                    continue
                if fed.get((node.id, p.name), 0) == 0:
                    raise WorkflowValidationError(
                        f"Node {node.id!r}: input port {p.name!r} is not fed by any edge mapping",
                        code=codes.GRAPH_INCOMPLETE,
                        hint='Add an edge whose map targets it, or mark the port {"required": false}.',
                    )


def load_workflow(path: str | Path) -> WorkflowDef:
    """Load a WorkflowDef from a JSON file at *path*.

    A ``"subflow": "./child.json"`` path reference is resolved relative to *path*'s
    directory; the loaded child is inlined into the node (so execution, codegen, and
    serialization are unchanged).  A cyclic reference is rejected at load time.
    """
    p = Path(path).resolve()
    logger.debug("Loading workflow from %s", p)
    with p.open() as fh:
        data: dict[str, Any] = json.load(fh)
    wf = parse_workflow(data, base_dir=p.parent, _seen=frozenset({p}))
    validate_workflow(wf, base_dir=p.parent)
    return wf
