"""SDK factory: create a fully-wired AgentSession.

This is the main entry point for constructing a coding agent session.
It wires together agent.Agent, ai.stream, tools, settings,
and session management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xdog.agent.skills import SkillManager

import asyncio
import concurrent.futures
import logging
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from xdog.agent import AgentTool as CoreAgentTool
from xdog.agent.agent import Agent
from xdog.ai.types import ThinkingLevel
from xdog.coding.config import (
    GlobalConfig,
    ProjectConfig,
    RuntimeConfig,
    get_sessions_dir,
    validate_max_messages_per_turn,
)
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.defaults import DEFAULT_MODEL, DEFAULT_THINKING_LEVEL, MAX_CONTEXT_TOKENS
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionData, SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.core.tools import get_default_tools

_THINKING_LEVELS: tuple[ThinkingLevel, ...] = ("minimal", "low", "medium", "high", "xhigh")


def _as_thinking_level(raw: object) -> ThinkingLevel | None:
    """Normalize configured and legacy names to provider thinking levels."""
    aliases: dict[str, ThinkingLevel | None] = {
        "none": None,
        "off": None,
        "normal": "medium",
        "deep": "high",
        "ultrathink": "xhigh",
    }
    if isinstance(raw, str) and raw in aliases:
        return aliases[raw]
    for level in _THINKING_LEVELS:
        if raw == level:
            return level
    return None

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CreateSessionOptions:
    """Options for creating an agent session."""

    working_dir: Path | None = None
    model_name: str | None = None
    thinking_level: str | None = None
    resume: bool = False
    resume_id: str | None = None
    verbose: bool = False
    config_path: Path | None = None
    overrides: dict[str, Any] | None = None


@dataclass(frozen=True)
class CreateSessionResult:
    """Result from create_agent_session."""

    session: AgentSession
    model_fallback_message: str | None = None


def create_agent_session(options: CreateSessionOptions | None = None) -> CreateSessionResult:
    """Claim the writer before reading or constructing a writable session."""
    from xdog.coding.core.sessions.ownership import SessionLease, runtime_directory
    opts = options or CreateSessionOptions()
    sessions = get_sessions_dir()
    if opts.resume:
        listing = SessionManager(sessions_dir=sessions).list_sessions(limit=1)
        if not listing:
            raise RuntimeError("No previous session to resume.")
        opts = replace(opts, resume=False, resume_id=listing[0].session_id)
    selected = opts.resume_id or uuid.uuid4().hex
    lease = SessionLease.acquire(runtime_directory(sessions), selected)
    try:
        result = _build_agent_session(opts, new_session_id=selected)
        result.session._lease = lease
        return result
    except BaseException:
        lease.close()
        raise


def _build_agent_session(options: CreateSessionOptions, *, new_session_id: str) -> CreateSessionResult:
    """Create a fully-wired AgentSession.

    This factory:
    1. Loads global + project config
    2. Creates SessionManager and SettingsManager
    3. Creates or resumes a session
    4. Resolves the model (from options, session, settings, or defaults)
    5. Creates tools from agent built-in factories
    6. Constructs agent.Agent with stream_fn from ai provider
    7. Wraps in AgentSession for lifecycle management

    Returns
    -------
    CreateSessionResult
        The session and any model fallback warning.
    """
    opts = options or CreateSessionOptions()
    wd = (opts.working_dir or Path.cwd()).resolve()

    # Load configuration
    global_cfg = GlobalConfig.load(opts.config_path)
    project_cfg = ProjectConfig.load(wd)
    ov = opts.overrides or {}

    config = RuntimeConfig.resolve(
        global_cfg=global_cfg,
        project_cfg=project_cfg,
        working_dir=wd,
        overrides=ov,
    )

    settings = SettingsManager(global_path=opts.config_path, project_dir=wd)
    session_mgr = SessionManager(sessions_dir=get_sessions_dir())

    # Validate persisted owner settings under its lease, before provider bootstrap.
    loaded = None
    if opts.resume_id:
        loaded = session_mgr.load_session(opts.resume_id)
        if loaded is None:
            raise RuntimeError(f"Session not found: {opts.resume_id}")
    elif opts.resume:
        loaded = session_mgr.get_most_recent()
        if loaded is None:
            raise RuntimeError('No previous session to resume.')
    saved_limit = None
    if loaded is not None and 'max_messages_per_turn' in loaded.settings:
        saved_limit = validate_max_messages_per_turn(loaded.settings['max_messages_per_turn'])
    batch_limit = validate_max_messages_per_turn(
        ov['max_messages_per_turn'] if 'max_messages_per_turn' in ov else
        saved_limit if saved_limit is not None else config.max_messages_per_turn)

    # Build model catalog for default model resolution
    import xdog.ai as ai
    provider = ai.load()
    from xdog.agent.helpers import stream_fn_from_provider
    from xdog.coding.core.model_catalog import validated
    adapter = stream_fn_from_provider(provider)

    def _first_model_id() -> str:
        """Choose only a real streaming chat profile when no model was specified."""
        all_models = [model for model in provider.models() if model.model_type == 'chat' and model.supports_streaming]
        if not all_models:
            raise ValueError('No streaming chat model available on the configured adapter; no fallback')
        return validated(all_models[0]).id

    # Resolve or create session
    session_data: SessionData
    model_fallback_message: str | None = None

    if loaded is not None:
        session_data = loaded
    else:
        model_name = opts.model_name or ov.get("model") or config.model or DEFAULT_MODEL or _first_model_id()
        if not model_name.startswith('ext-'):
            initial = adapter.resolve_model(model_name)
            if initial is None:
                raise ValueError('Requested model unavailable on the configured adapter; no fallback')
            initial = validated(initial)
            initial_thinking = _as_thinking_level(ov.get('thinking_level') or config.thinking_level
                                                  or DEFAULT_THINKING_LEVEL)
            if (initial.reasoning and initial_thinking is not None and initial.supported_efforts is not None
                    and initial_thinking not in initial.supported_efforts):
                raise ValueError('Configured thinking level is not advertised by selected model; no fallback')
            model_name = initial.id
        session_data = session_mgr.create_session(model=model_name, working_dir=wd, session_id=new_session_id)

    if not session_data.working_dir:
        session_data.working_dir = str(wd)

    # Resolve active model from catalog
    model_id = (opts.model_name or ov.get("model") or session_data.model
                or config.model or DEFAULT_MODEL or _first_model_id())
    extension_model = model_id.startswith("ext-")
    model = None if extension_model else adapter.resolve_model(model_id)
    if not extension_model:
        if model is None:
            raise ValueError('Saved/requested model unavailable on the configured adapter; no fallback')
        model = validated(model)
        model_id = model.id

    # CLI override > saved session > project/global configuration. Session
    # settings must participate here, before AgentSession loads its manager,
    # because StreamOptions are constructed below.
    saved_thinking = session_data.settings.get("thinking_level")
    thinking_level = (
        ov.get("thinking_level")
        or saved_thinking
        or config.thinking_level
        or DEFAULT_THINKING_LEVEL
    )
    effective_thinking = _as_thinking_level(thinking_level)
    if model and not model.reasoning:
        effective_thinking = None
    if (model is not None and effective_thinking is not None and model.supported_efforts is not None
            and effective_thinking not in model.supported_efforts):
        raise ValueError('Configured thinking level is not advertised by selected model; no fallback')

    context_window = (model.context_window if model is not None else 0) or MAX_CONTEXT_TOKENS
    max_prompt_tokens = model.max_prompt_tokens if model is not None else 0

    # Create tools from agent built-in factories
    agent_tools: list[CoreAgentTool] = get_default_tools(wd)

    # Create Agent with explicit stream_fn from provider
    permissions = PermissionManager(config.permission_mode)

    from xdog.agent import AgentConfig
    from xdog.agent.helpers import stream_fn_from_provider
    from xdog.ai.types import StreamOptions

    agent = Agent(
        adapter,
        config=AgentConfig(
            model=model_id,
            supports_tool_calls=model.supports_tool_calls if model is not None else None,
            system_prompt="",  # will be rebuilt on first prompt
            context_window=context_window,
            max_prompt_tokens=max_prompt_tokens,
            options=StreamOptions(
                thinking=effective_thinking,
            ),
        ),
        tools=agent_tools,
        tool_ctx={"fs_workspace": str(wd)},
        before_tool_call=permissions.before_tool_call,
        # The Agent owns where the index and the bodies go; this only says which
        # manager to read them from.
        skills=_coding_skill_manager(),
    )

    # Restore messages if resuming
    if session_data.messages:
        agent.replace_messages(session_data.messages)

    session_data.settings = {**session_data.settings, 'max_messages_per_turn': batch_limit}

    # Create AgentSession wrapper
    session = AgentSession(
        agent=agent,
        session_data=session_data,
        session_manager=session_mgr,
        settings=settings,
        tool_registry=None,
        bash=None,
        working_dir=wd,
        context_window=context_window,
        max_prompt_tokens=max_prompt_tokens,
        model_info=model,
        permissions=permissions,
    )
    try:
        if extension_model:
            async def activate() -> None:
                try:
                    await session.initialize_extensions()
                    declared = session.extensions.provider_model(model_id)
                    if declared is None:
                        raise ValueError("Configured extension model is not declared by an explicitly trusted worker")
                    thinking = session.agent.options.thinking
                    if (thinking is not None and declared.reasoning and declared.supported_efforts is not None
                            and thinking not in declared.supported_efforts):
                        raise ValueError(
                        "Configured thinking level is not advertised by selected extension model")
                    session.set_model(model_id)
                    if not declared.reasoning:
                        session.agent.set_options(replace(session.agent.options, thinking=None))
                except BaseException:
                    await session.children.supervisor.aclose()
                    await session.extensions.aclose()
                    if session.mcp is not None:
                        await session.mcp.aclose()
                    raise
            # This synchronous factory is also called from running event loops. Use
            # one owned bootstrap loop, never nest asyncio.run on the caller's loop.
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="xdog-provider-bootstrap") as pool:
                pool.submit(lambda: asyncio.run(activate())).result()
        # Persist the effective value rather than a legacy alias such as "normal".
        session.settings.set_session_thinking(session.agent.options.thinking or "off")
        session._persist()

        return CreateSessionResult(
            session=session,
            model_fallback_message=model_fallback_message,
        )
    except BaseException:
        session.dispose()
        raise


def _coding_skill_manager() -> "SkillManager | None":
    """coding's SkillManager, or None if it cannot be built.

    Skills are a convenience; failing to read them must not stop a session from
    starting, which is why this swallows rather than raises.
    """
    try:
        from xdog.coding.core.slash_commands import skill_manager

        return skill_manager()
    except Exception:  # pragma: no cover - defensive
        import logging

        logging.getLogger(__name__).debug("no skill manager available", exc_info=True)
        return None
