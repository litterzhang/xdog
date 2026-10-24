"""flow.runners — pluggable agent-execution backends.

An agent node's execution is funneled through the :class:`AgentRunner` contract:
given the interpolated prompts, the resolved model, and the node, produce
``(output_value, tokens)``.  Everything else (input assembly, storing, retry,
checkpoint, budget, events) is the executor driver's job.

Two backends implement the contract:

* :class:`SdkRunner` — the in-process ``agent``/``ai`` SDK (the default). Builds an
  ``Agent``, adds the ``submit_result`` tool when the node is structured, and drains
  one turn.  (SDK imports are lazy so a CLI-only future need not load them.)
* CLI runners (added later) shell out to an external coding-agent CLI.

See ``docs/cli-agent.md``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from xdog.flow.errors import WorkflowExecutionError
from xdog.flow.models import (
    NodeDef,
    agent_is_structured,
    agent_output_schema,
    submit_instruction,
)

if TYPE_CHECKING:
    from xdog.agent import ToolRepo
    from xdog.agent.core import StreamFn, WebSearchFn
    from xdog.agent.skills import SkillRepo


class AgentRunner(Protocol):
    """Executes one agent turn: (prompts, model, node) -> (value, tokens).

    The value is the structured object (when the node has structured output ports)
    or the joined assistant text; ``tokens`` is the turn's total token count.

    ``inputs``/``step``/``fan_index`` describe *which* activation this turn belongs
    to.  The SDK and CLI backends ignore them; :class:`StubRunner` uses them to pick
    a test stub deterministically (see :class:`NodeStubs`).

    ``session`` is a dumped agent session to start from (``inherit``), and
    ``session_sink`` is where this turn leaves its own for a later node.  Both
    are passed rather than returned so the ``(value, tokens)`` contract — shared
    with script, human and subflow nodes in both engines — stays put.  A ``None``
    session means start cold: absent is not an error, since a self-inheriting
    loop has none on its first pass.
    """

    async def run(
        self,
        node: NodeDef,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        timeout: float,
        inputs: Mapping[str, object],
        step: int,
        fan_index: int | None,
        session: Mapping[str, object] | None = None,
        session_sink: dict[str, object] | None = None,
    ) -> tuple[object, int]: ...


class NodeStubs(Protocol):
    """Test-time overrides for a workflow's non-deterministic boundaries.

    Implemented by :mod:`flow.testing`; consulted by the executor at exactly three
    points so that everything else — edges, conditions, loops, fan-out, mappings,
    coercion, ``$output`` collection — runs for real.

    ``step`` identifies one scheduler activation (a fan-out node's instances all
    share it, so it is the loop-iteration ordinal); ``fan_index`` is an instance's
    position in the fanned array, or ``None`` outside a fan.  Both are stable under
    concurrency, unlike completion order.
    """

    def agent(
        self,
        node: NodeDef,
        *,
        inputs: Mapping[str, object],
        step: int,
        fan_index: int | None,
    ) -> tuple[object, int]:
        """Answer an agent turn.  Raises when the node has no matching stub."""
        ...

    def subflow(
        self,
        node: NodeDef,
        *,
        inputs: Mapping[str, object],
        step: int,
    ) -> dict[str, object] | None:
        """Output ports for a stubbed subflow, or ``None`` to run the child for real."""
        ...

    def script(
        self,
        node: NodeDef,
        *,
        inputs: Mapping[str, object],
        step: int,
        fan_index: int | None,
    ) -> dict[str, object] | None:
        """Output ports for a stubbed script, or ``None`` to run it for real."""
        ...


class StubRunner:
    """The test backend: answers every agent turn from :class:`NodeStubs`.

    Installed for *all* agent nodes in test mode — including ``backend="claude-cli"``
    ones — so no provider or CLI is ever constructed and a test cannot reach the
    network by accident.

    It sits exactly where the real backends sit: prompt interpolation has already
    run, and the value it returns still goes through the node's required-field
    checks and ``to_state`` coercion.  So a stub is validated against the node's
    declared output contract by the production code path, for free.
    """

    def __init__(self, stubs: NodeStubs) -> None:
        self._stubs = stubs

    async def run(
        self,
        node: NodeDef,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        timeout: float,
        inputs: Mapping[str, object],
        step: int,
        fan_index: int | None,
        session: Mapping[str, object] | None = None,
        session_sink: dict[str, object] | None = None,
    ) -> tuple[object, int]:
        _ = timeout
        value, tokens = self._stubs.agent(node, inputs=inputs, step=step, fan_index=fan_index)
        if session_sink is not None:
            # Synthesise the turn a real agent would have had.  Without this,
            # every `xdog-flow test` of an inheriting workflow would see an empty
            # context and pass for the wrong reason.
            raw_prior = session.get("messages") if session else None
            prior = list(raw_prior) if isinstance(raw_prior, list) else []
            text = value if isinstance(value, str) else repr(value)
            session_sink["session"] = {
                "messages": [
                    *prior,
                    {"role": "user", "content": [{"type": "text", "text": user_prompt}]},
                    {"role": "assistant", "content": [{"type": "text", "text": text}]},
                ],
                "system_prompt": system_prompt,
                "model": model,
            }
        return value, tokens


def _skill_repo(skill_dirs: "Sequence[Path]") -> "SkillRepo":
    """A SkillRepo over the workflow's own `skills/` plus installed packages.

    The same loader every product uses, rather than a flow-specific resolver:
    what differs between products is *where to look*, and that is exactly what a
    repository is parameterised by.
    """
    from xdog.agent.skills import SkillRepo

    local = list(skill_dirs)
    repo = SkillRepo()
    if local:
        repo.register(directory=local[0], name="shared")
    return repo


class SdkRunner:
    """The default backend: the in-process ``agent`` + ``ai`` SDK.

    Holds the SDK wiring (stream-fn factory, tool registry, web-search factory) and
    runs each agent turn by building an ``Agent``, draining/exporting it, and
    closing that node-owned runtime on every exit. Cleanup may outlast the node
    timeout while cooperative tools settle; factory-supplied providers stay shared.
    """

    def __init__(
        self,
        *,
        stream_fn_factory: Callable[[str], StreamFn],
        tool_repo: ToolRepo,
        web_search_fn_factory: Callable[[str], WebSearchFn] | None,
        workspace: Path | None = None,
        allow_paths: Sequence[Path] = (),
        skill_dirs: Sequence[Path] = (),
        confine_to: Sequence[Path] | None = None,
    ) -> None:
        self._stream_fn_factory = stream_fn_factory
        self._tool_repo = tool_repo
        self._web_search_fn_factory = web_search_fn_factory
        # Where "here" is. A relative path resolves inside it; None leaves the
        # tool's historical behaviour, where a relative path is an error.
        self._workspace = workspace
        # Granted trees are named to the agent whether or not the run is
        # confined: a grant it is not told about is a grant it cannot use, and
        # the compiled engine has always passed them unconditionally.
        self._allow_paths = allow_paths
        # Where a workflow's own skills live: `skills/` beside the workflow file.
        # It travels with the artifact, so unlike a machine's skill directory it
        # cannot make the same workflow behave differently for two people.
        self._skill_dirs = skill_dirs
        # None means unconfined, which is what every caller got before this
        # existed. A list — even an empty one — is a bound.
        self._confine_to = confine_to

    async def run(
        self,
        node: NodeDef,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        timeout: float,
        inputs: Mapping[str, object],
        step: int,
        fan_index: int | None,
        session: Mapping[str, object] | None = None,
        session_sink: dict[str, object] | None = None,
    ) -> tuple[object, int]:
        _ = (inputs, step, fan_index)  # activation identity: only StubRunner needs it
        from xdog.agent.agent import Agent
        from xdog.agent.core import AgentConfig, AgentTool
        from xdog.agent.events import TurnEndEvent
        from xdog.agent.helpers import model_supports_tool_calls
        from xdog.agent.tools.tool_filesystem import CONFINE_CTX_KEY, WORKSPACE_CTX_KEY
        from xdog.agent.workspace import workspace_briefing
        from xdog.ai.types import AssistantMessage, TextContent

        stream_fn = self._stream_fn_factory(model)
        resolved_tools: tuple[AgentTool, ...] = self._tool_repo.select(node.tools).tools

        ctx_bits: dict[str, object] = {}
        if self._workspace is not None:
            ctx_bits[WORKSPACE_CTX_KEY] = str(self._workspace)
        if self._confine_to is not None:
            ctx_bits[CONFINE_CTX_KEY] = [str(p) for p in self._confine_to]
        tool_ctx: dict[str, object] | None = ctx_bits or None
        sink: dict[str, object] = {}
        # Every agent node is briefed, whatever tools it declares. Keying this
        # off the tool list would be a guess: a custom tool can touch the
        # filesystem, so can an MCP server, and a model can name a path for a
        # downstream node to use. We tell it where its files go; we cannot audit
        # that it obeys.
        final_sys_prompt = system_prompt + workspace_briefing(
            self._workspace, self._allow_paths, confined=self._confine_to is not None
        )
        structured = agent_is_structured(node)
        if structured:
            from xdog.agent.tools import create_submit_result_tool

            resolved_tools = resolved_tools + (create_submit_result_tool(),)
            tool_ctx = {
                **(tool_ctx or {}),
                "result_schema": agent_output_schema(node),
                "result_sink": sink,
            }
            # `final_sys_prompt`, not `system_prompt`: restarting from the raw
            # prompt here dropped the workspace briefing for exactly the nodes
            # doing the most work, and only in this engine -- codegen appends.
            final_sys_prompt = final_sys_prompt + submit_instruction(node)

        web_search_fn = None
        if node.web_search and self._web_search_fn_factory is not None:
            web_search_fn = self._web_search_fn_factory(node.web_search_model or model)

        agent = Agent(
            stream_fn,
            config=AgentConfig(
                model=model,
                system_prompt=final_sys_prompt,
                supports_tool_calls=model_supports_tool_calls(model),
            ),
            tools=resolved_tools,
            tool_ctx=tool_ctx,
            skills=_skill_repo(self._skill_dirs),
            active_skills=node.skills,
            web_search_fn=web_search_fn,
        )

        try:
            if session:
                # The node owns configuration; inherited messages are data, not a
                # grant to adopt another node's model or skill selection. In
                # particular, restore validates adapter model availability BEFORE
                # adopting data, so a post-restore set_model() alone is too late.
                inherited = dict(session)
                if node.model:
                    inherited["model"] = model
                if "format" in inherited or "version" in inherited:
                    # Reuse the SDK's captured, versioned identities for this
                    # node's explicit selection (including []). Do not resolve
                    # obsolete inherited skills just to discard them afterwards.
                    inherited["skills"] = agent.dump()["skills"]
                agent.restore(inherited)
                # An absent node system prompt retains the inherited base prompt.
                if node.system_prompt:
                    agent.set_system_prompt(final_sys_prompt)
                if node.model:
                    agent.set_model(model)

            accumulated: list[str] = []
            total_tokens = 0

            async def _drain() -> None:
                nonlocal total_tokens
                event_stream = await agent.prompt(user_prompt)
                async for event in event_stream:
                    if isinstance(event, TurnEndEvent) and event.message is not None:
                        msg = event.message
                        if isinstance(msg, AssistantMessage):
                            for part in msg.content:
                                if isinstance(part, TextContent):
                                    accumulated.append(part.text)
                            # Some providers report per-category counts but leave
                            # total_tokens at 0; fall back to input + output.
                            _u = msg.usage
                            total_tokens += _u.total_tokens or (_u.input + _u.output)
                await agent.wait_for_idle()

            await asyncio.wait_for(_drain(), timeout=timeout)

            if session_sink is not None:
                session_sink["session"] = agent.dump()

            if structured:
                result_obj = sink.get("result")
                if result_obj is None:
                    raise WorkflowExecutionError(f"Node {node.id!r}: agent did not submit a result via submit_result")
                return result_obj, total_tokens
            return "".join(accumulated), total_tokens
        finally:
            # A node owns its Agent, not the factory's potentially shared provider.
            # Dump above while settled; never publish a timeout/interrupted session
            # as resumable. Cleanup must finish even if the caller cancels again.
            close_task = asyncio.create_task(agent.aclose())
            cancelled = False
            while not close_task.done():
                try:
                    await asyncio.shield(close_task)
                except asyncio.CancelledError:
                    cancelled = True
            close_task.result()  # Surface cleanup failures; do not claim settlement.
            if cancelled:
                raise asyncio.CancelledError



# ---------------------------------------------------------------------------
# CLI backends — shell out to an external coding-agent CLI for one turn.
# See docs/cli-agent.md.  Adapters own the per-CLI argv + stdout grammar; the
# CliRunner is adapter-agnostic (spawn, feed stdin, check exit, hand to parse).
# ---------------------------------------------------------------------------

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _resolve_env(value: object) -> object:
    """Recursively resolve ``${VAR}`` references against the run-time environment.

    A string ``"${VAR}"`` becomes ``os.environ["VAR"]``; an unset var raises (fail
    fast, never a silent empty).  Dicts/lists are walked; other scalars pass through.
    So a workflow JSON carries the reference, never the secret (cli-agent.md §5.2).
    """
    if isinstance(value, str):
        def _sub(m: "re.Match[str]") -> str:
            var = m.group(1)
            got = os.environ.get(var)
            if got is None:
                raise WorkflowExecutionError(f"mcp_servers: environment variable ${{{var}}} is not set")
            return got

        return _ENV_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    return value


def claude_mcp_config(mcp_servers: "tuple[tuple[str, dict[str, object]], ...]") -> str:
    """Render an MCP-server spec into claude's ``--mcp-config`` JSON string.

    ``{"mcpServers": {name: <spec>, ...}}`` with ``${ENV}`` resolved.  The spec dict
    is passed through verbatim (opaque) apart from env interpolation.
    """
    servers = {name: _resolve_env(spec) for name, spec in mcp_servers}
    return json.dumps({"mcpServers": servers}, sort_keys=True)


def codex_mcp_config(mcp_servers: "tuple[tuple[str, dict[str, object]], ...]") -> str:
    """Render an MCP-server spec into codex ``config.toml`` ``[mcp_servers.*]`` text.

    Minimal TOML writer for the pass-through spec (string/number/bool/list of those),
    with ``${ENV}`` resolved.  Kept dependency-free (no toml lib).
    """
    lines: list[str] = []
    for name, spec in mcp_servers:
        lines.append(f"[mcp_servers.{name}]")
        for key, raw in spec.items():
            lines.append(f"{key} = {_toml_value(_resolve_env(raw))}")
        lines.append("")
    return "\n".join(lines)


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        # inline table
        return "{" + ", ".join(f"{k} = {_toml_value(v)}" for k, v in value.items()) + "}"
    return json.dumps(str(value))  # quoted string


class CliAdapter(Protocol):
    """Maps the agent-turn contract onto one CLI's argv + stdout grammar."""

    name: str

    def binary(self) -> str:
        """Default executable name (overridden by FLOW_CLI_BIN)."""
        ...

    def argv(
        self,
        *,
        model: str,
        system_prompt: str,
        output_schema: dict[str, object] | None,
        allowed_tools: tuple[str, ...],
        mcp_servers: tuple[tuple[str, dict[str, object]], ...],
        scratch: Path,
    ) -> list[str]:
        """Command-line args after the binary (prompt is fed on stdin).

        *scratch* is a per-turn temp directory an adapter may write files into
        (e.g. codex's ``--output-schema`` file, MCP config); removed after the turn.
        """
        ...

    def stdin(self, *, system_prompt: str, user_prompt: str) -> str:
        """The text piped to the CLI's stdin."""
        ...

    def parse(self, stdout: str, *, structured: bool) -> tuple[object, int]:
        """Parse the CLI's stdout into (value, tokens)."""
        ...


class ClaudeAdapter:
    """Adapter for the ``claude`` CLI (Claude Code) in headless print mode."""

    name = "claude-cli"

    def binary(self) -> str:
        return "claude"

    def argv(
        self,
        *,
        model: str,
        system_prompt: str,
        output_schema: dict[str, object] | None,
        allowed_tools: tuple[str, ...],
        mcp_servers: tuple[tuple[str, dict[str, object]], ...],
        scratch: Path,
    ) -> list[str]:
        args = ["-p", "--output-format", "json"]
        if model:
            args += ["--model", model]
        if system_prompt:
            args += ["--append-system-prompt", system_prompt]
        if output_schema is not None:
            args += ["--json-schema", json.dumps(output_schema, sort_keys=True)]
        if mcp_servers:
            cfg = scratch / "mcp.json"
            cfg.write_text(claude_mcp_config(mcp_servers), encoding="utf-8")
            args += ["--mcp-config", str(cfg)]
        if allowed_tools:
            args += ["--allowedTools", ",".join(allowed_tools)]
        return args

    def stdin(self, *, system_prompt: str, user_prompt: str) -> str:
        # claude takes the system prompt via a flag, so stdin is the user prompt.
        return user_prompt

    def parse(self, stdout: str, *, structured: bool) -> tuple[object, int]:
        env = json.loads(stdout)
        if not isinstance(env, dict):
            raise WorkflowExecutionError("claude-cli: unexpected output (not a JSON object)")
        # Real claude nests token counts under "usage"; fall back to top-level.
        _u = env.get("usage")
        usage: dict[str, object] = _u if isinstance(_u, dict) else env
        _i = usage.get("input_tokens")
        _o = usage.get("output_tokens")
        tokens = (int(_i) if isinstance(_i, int) else 0) + (int(_o) if isinstance(_o, int) else 0)
        if structured:
            obj = env.get("structured_output")
            if obj is None:
                raise WorkflowExecutionError("claude-cli: no structured_output in response")
            return obj, tokens
        result = env.get("result", "")
        return (result if isinstance(result, str) else json.dumps(result)), tokens


class CodexAdapter:
    """Adapter for the ``codex`` CLI (``codex exec``) in headless JSONL mode.

    Differences the adapter absorbs vs claude: no system-prompt flag (folded into
    the prompt), a schema *file* (``--output-schema FILE``) rather than a schema
    string, and a JSONL event stream (parse the final ``agent_message`` item +
    ``turn.completed`` usage) rather than a single JSON object.
    """

    name = "codex-cli"

    def binary(self) -> str:
        return "codex"

    def argv(
        self,
        *,
        model: str,
        system_prompt: str,
        output_schema: dict[str, object] | None,
        allowed_tools: tuple[str, ...],
        mcp_servers: tuple[tuple[str, dict[str, object]], ...],
        scratch: Path,
    ) -> list[str]:
        args = ["exec", "--json", "--skip-git-repo-check"]
        if model:
            args += ["-m", model]
        if output_schema is not None:
            schema_file = scratch / "output_schema.json"
            schema_file.write_text(json.dumps(output_schema, sort_keys=True), encoding="utf-8")
            args += ["--output-schema", str(schema_file)]
        if mcp_servers:
            # codex reads MCP servers from its config.toml; point CODEX_HOME at a
            # scratch dir holding a generated config (set by CliRunner via env).
            cfg = scratch / "config.toml"
            cfg.write_text(codex_mcp_config(mcp_servers), encoding="utf-8")
        # codex has no per-tool allow-list; capability is bounded by the sandbox.
        # With no tools requested we keep the default read-only sandbox.
        return args

    def stdin(self, *, system_prompt: str, user_prompt: str) -> str:
        # codex has no system-prompt flag — fold it into the prompt deterministically.
        if system_prompt:
            return f"{system_prompt}\n\n{user_prompt}"
        return user_prompt

    def parse(self, stdout: str, *, structured: bool) -> tuple[object, int]:
        final_text: str | None = None
        tokens = 0
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue
            if ev.get("type") == "item.completed":
                item = ev.get("item")
                if isinstance(item, dict) and item.get("type") == "agent_message":
                    text = item.get("text")
                    if isinstance(text, str):
                        final_text = text
            elif ev.get("type") == "turn.completed":
                usage = ev.get("usage")
                if isinstance(usage, dict):
                    _i = usage.get("input_tokens")
                    _o = usage.get("output_tokens")
                    tokens = (int(_i) if isinstance(_i, int) else 0) + (int(_o) if isinstance(_o, int) else 0)
        if final_text is None:
            raise WorkflowExecutionError("codex-cli: no agent_message in output")
        if structured:
            try:
                return json.loads(final_text), tokens
            except json.JSONDecodeError as exc:
                raise WorkflowExecutionError(f"codex-cli: structured output is not valid JSON: {exc}") from exc
        return final_text, tokens


_CLI_ADAPTERS: dict[str, CliAdapter] = {a.name: a for a in (ClaudeAdapter(), CodexAdapter())}


def cli_adapter_for(backend: str) -> CliAdapter:
    """Return the adapter for a ``node.backend`` value, or raise."""
    adapter = _CLI_ADAPTERS.get(backend)
    if adapter is None:
        known = ", ".join(sorted(_CLI_ADAPTERS)) or "<none>"
        raise WorkflowExecutionError(f"unknown CLI backend {backend!r}; known: {known}")
    return adapter


class CliRunner:
    """Runs an agent turn by shelling out to a coding-agent CLI (adapter-driven).

    Imports no ``agent``/``ai`` — only ``asyncio`` subprocess + ``json``.  The CLI
    binary is the adapter's default, overridable per-backend by ``FLOW_CLI_BIN`` (or
    ``FLOW_CLI_BIN_<BACKEND>``) — the test-stub hook.
    """

    def __init__(self, adapter: CliAdapter) -> None:
        self._adapter = adapter

    def _resolve_binary(self) -> str:
        env_key = "FLOW_CLI_BIN_" + self._adapter.name.upper().replace("-", "_")
        return os.environ.get(env_key) or os.environ.get("FLOW_CLI_BIN") or self._adapter.binary()

    async def run(
        self,
        node: NodeDef,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        timeout: float,
        inputs: Mapping[str, object],
        step: int,
        fan_index: int | None,
        session: Mapping[str, object] | None = None,
        session_sink: dict[str, object] | None = None,
    ) -> tuple[object, int]:
        _ = (inputs, step, fan_index)  # activation identity: only StubRunner needs it
        structured = agent_is_structured(node)
        schema = agent_output_schema(node) if structured else None
        scratch = Path(tempfile.mkdtemp(prefix="flow-cli-"))
        try:
            argv = self._adapter.argv(
                model=model,
                system_prompt=system_prompt,
                output_schema=schema,
                allowed_tools=node.allowed_tools,
                mcp_servers=node.mcp_servers,
                scratch=scratch,
            )
            stdin_text = self._adapter.stdin(system_prompt=system_prompt, user_prompt=user_prompt)
            binary = self._resolve_binary()
            # codex reads MCP servers from CODEX_HOME/config.toml; point it at the
            # scratch config the adapter wrote (claude takes --mcp-config directly).
            env = None
            if node.mcp_servers and self._adapter.name == "codex-cli":
                env = {**os.environ, "CODEX_HOME": str(scratch)}

            proc = await asyncio.create_subprocess_exec(
                binary,
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                out_b, err_b = await asyncio.wait_for(proc.communicate(stdin_text.encode()), timeout=timeout)
            except TimeoutError:
                proc.kill()
                raise WorkflowExecutionError(
                    f"Node {node.id!r}: CLI {binary!r} timed out after {timeout}s"
                ) from None
            if proc.returncode != 0:
                err = err_b.decode(errors="replace").strip()
                raise WorkflowExecutionError(
                    f"Node {node.id!r}: CLI {binary!r} exited {proc.returncode}: {err or '(no stderr)'}"
                )
            return self._adapter.parse(out_b.decode(errors="replace"), structured=structured)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
