"""SDK factory: create a fully-wired AgentSession.

This is the main entry point for constructing a coding agent session.
It wires together agent.Agent, ai.stream, tools, settings,
and session management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xdog.agent.skills import SkillManager

import logging
from dataclasses import dataclass
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

    settings = SettingsManager(project_dir=wd)
    session_mgr = SessionManager(sessions_dir=get_sessions_dir())

    # Build model catalog for default model resolution
    import xdog.ai as ai
    provider = ai.load()

    def _first_model_id() -> str:
        """Return the first model id from the provider, or 'sonnet' as last resort."""
        all_models = provider.models()
        return all_models[0].id if all_models else "sonnet"

    # Resolve or create session
    session_data: SessionData
    model_fallback_message: str | None = None

    if opts.resume:
        loaded = session_mgr.get_most_recent()
        if loaded is None:
            raise RuntimeError("No previous session to resume.")
        session_data = loaded
    elif opts.resume_id:
        loaded = session_mgr.load_session(opts.resume_id)
        if loaded is None:
            raise RuntimeError(f"Session not found: {opts.resume_id}")
        session_data = loaded
    else:
        model_name = ov.get("model") or config.model or DEFAULT_MODEL or _first_model_id()
        session_data = session_mgr.create_session(model=model_name, working_dir=wd)

    if not session_data.working_dir:
        session_data.working_dir = str(wd)

    # Resolve active model from catalog
    model_id = ov.get("model") or session_data.model or config.model or DEFAULT_MODEL or _first_model_id()
    model = provider.model(model_id)
    if model is None:
        model_fallback_message = f"Could not resolve model: {model_id}"
        fallback_id = _first_model_id()
        model = provider.model(fallback_id)
        if model is not None:
            model_id = model.id
            session_data.model = model_id
            model_fallback_message += f". Using {model.id}"

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

    context_window = (model.context_window if model is not None else 0) or MAX_CONTEXT_TOKENS
    max_prompt_tokens = model.max_prompt_tokens if model is not None else 0

    # Create tools from agent built-in factories
    agent_tools: list[CoreAgentTool] = get_default_tools(wd)

    # Create Agent with explicit stream_fn from provider
    permissions = PermissionManager(config.permission_mode)

    from xdog.agent import AgentConfig
    from xdog.agent.helpers import model_supports_tool_calls, stream_fn_from_provider
    from xdog.ai.types import StreamOptions

    agent = Agent(
        stream_fn_from_provider(provider),
        config=AgentConfig(
            model=model_id,
            supports_tool_calls=model_supports_tool_calls(model_id),
            system_prompt="",  # will be rebuilt on first prompt
            context_window=context_window,
            max_prompt_tokens=max_prompt_tokens,
            options=StreamOptions(
                thinking=effective_thinking,
            ),
        ),
        tools=agent_tools,
        before_tool_call=permissions.before_tool_call,
        # The Agent owns where the index and the bodies go; this only says which
        # manager to read them from.
        skills=_coding_skill_manager(),
    )

    # Restore messages if resuming
    if session_data.messages:
        agent.replace_messages(session_data.messages)

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
        permissions=permissions,
    )
    # Persist the effective value rather than a legacy alias such as "normal".
    session.settings.set_session_thinking(effective_thinking or "off")
    session._persist()

    return CreateSessionResult(
        session=session,
        model_fallback_message=model_fallback_message,
    )


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
