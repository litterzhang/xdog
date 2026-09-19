"""Agent session: wraps agent.Agent with session persistence, compaction, and branching."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xdog.agent import (
    AgentEndEvent,
    AgentEvent,
    AgentMessage,
    MessageEndEvent,
)
from xdog.agent.agent import Agent
from xdog.ai.types import (
    AssistantMessage,
    TextContent,
    ThinkingLevel,
    UserMessage,
)
from xdog.coding.core.defaults import COMPACTION_THRESHOLD_RATIO, MAX_CONTEXT_TOKENS
from xdog.coding.core.event_bus import EventBus, get_event_bus
from xdog.coding.core.messages import dicts_to_messages, messages_to_dicts
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionData, SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.core.system_prompt import build_system_prompt

logger = logging.getLogger(__name__)


@dataclass
class AgentSession:
    """Manages a coding conversation wrapping agent.Agent.

    The session owns the Agent instance, subscribes to its events for
    persistence, and provides higher-level operations like compaction
    and session branching.
    """

    agent: Agent
    session_data: SessionData
    session_manager: SessionManager
    settings: SettingsManager
    tool_registry: Any  # kept for backward compat, may be None
    bash: Any  # kept for backward compat, may be None
    working_dir: Path
    context_window: int = MAX_CONTEXT_TOKENS
    max_prompt_tokens: int = 0
    permissions: PermissionManager = field(default_factory=PermissionManager)
    event_bus: EventBus = field(default_factory=get_event_bus)
    _unsubscribe: Any = field(default=None, init=False, repr=False)
    #: Slugs of skills whose instructions are currently in the system prompt.
    #: Held here rather than pushed into the message history so they can be
    #: taken back out — see `activate_skill`.
    _active_skills: frozenset[str] = field(default=frozenset(), init=False, repr=False)

    def __post_init__(self) -> None:
        # Restore session-level settings
        if self.session_data.settings:
            self.settings.load_session_settings(self.session_data.settings)

        # Sessions constructed outside the SDK still receive enforcement.
        self.agent.set_before_tool_call(self.permissions.before_tool_call)

        # Subscribe to agent events for persistence and event bus forwarding
        self._unsubscribe = self.agent.subscribe(self._on_agent_event)

    # -- Properties --

    @property
    def messages(self) -> list[AgentMessage]:
        return list(self.agent.state.messages)

    @property
    def session_id(self) -> str:
        return self.session_data.session_id

    @property
    def model(self) -> str:
        return self.agent.state.model

    @property
    def is_streaming(self) -> bool:
        return self.agent.state.is_streaming

    @property
    def context_limit(self) -> int:
        """Prompt limit used for compaction; falls back to the model window."""
        return self.max_prompt_tokens or self.context_window or MAX_CONTEXT_TOKENS

    # -- Public API --

    @property
    def active_skills(self) -> frozenset[str]:
        """Slugs whose instructions are currently in the system prompt."""
        return self._active_skills

    def activate_skill(self, slug: str) -> None:
        """Put a skill's instructions in the system prompt until told otherwise.

        The obvious implementation — append the skill body as a message — is
        what the format's reference clients do, and it is a one-way door: a
        message cannot be taken back out of a conversation, so the body keeps
        occupying the window for the rest of the session whether or not it is
        still relevant. Keeping it in the system prompt is what makes
        deactivation possible at all.

        `Agent.set_skills` does the placing, so that reasoning now lives in one
        place instead of being re-derived by every product that has skills.
        """
        self._active_skills = self._active_skills | {slug}
        self._push_skills()
        self._rebuild_system_prompt()

    def deactivate_skill(self, slug: str) -> bool:
        """Drop a skill from the system prompt. False if it was not active."""
        if slug not in self._active_skills:
            return False
        self._active_skills = self._active_skills - {slug}
        self._push_skills()
        self._rebuild_system_prompt()
        return True

    async def send_message(self, user_text: str) -> AssistantMessage | None:
        """Send a user message and run the full agent loop.

        Returns the final assistant message, or None if aborted.
        """
        # Rebuild system prompt before each turn
        self._rebuild_system_prompt()

        # Check for compaction before the turn
        await self._maybe_compact()

        # Run the agent loop
        event_stream = await self.agent.prompt(user_text)

        last_assistant: AssistantMessage | None = None
        async for event in event_stream:
            if isinstance(event, MessageEndEvent) and isinstance(event.message, AssistantMessage):
                last_assistant = event.message

        # Persist after turn
        self._persist()

        self._expire_turn_scoped_skills()

        return last_assistant

    def _expire_turn_scoped_skills(self) -> None:
        """Retire skills their author declared as lasting one turn.

        Declared, not inferred. A timer cannot tell a one-shot procedure from a
        guardrail, and "unused for N turns" is unmeasurable anyway — there is no
        way to observe whether the model followed an instruction. Only the
        author knows which kind they wrote, so only the author gets to say.
        """
        if not self._active_skills:
            return
        try:
            from xdog.coding.core.slash_commands import skill_manager

            manager = skill_manager()
            expired = {
                slug
                for slug in self._active_skills
                if (skill := manager.load_skill(slug)) is not None and skill.expires_after_turn
            }
        except Exception:
            logger.debug("could not expire turn-scoped skills", exc_info=True)
            return

        if expired:
            self._active_skills = self._active_skills - expired
            self._push_skills()
            self._rebuild_system_prompt()
            logger.debug("expired turn-scoped skills: %s", sorted(expired))

    def steer(self, message: str) -> None:
        """Queue a steering interrupt for the current turn."""
        self.agent.steer(message)

    def abort(self) -> None:
        """Cancel the current agent turn."""
        self.agent.abort()

    def cancel(self) -> None:
        """Cancel active work and discard agent-level queued messages."""
        self.agent.clear_all_queues()
        self.permissions.deny_all()
        self.agent.abort()

    # -- Model / thinking --

    def set_model(self, model: str) -> None:
        """Switch the active model and refresh its provider-reported limits."""
        self.agent.set_model(model)
        try:
            import xdog.ai as ai

            model_info = ai.load().model(model)
            if model_info is not None:
                self.context_window = model_info.context_window or MAX_CONTEXT_TOKENS
                self.max_prompt_tokens = model_info.max_prompt_tokens
        except Exception:
            logger.debug("could not refresh model limits for %s", model, exc_info=True)
        self.settings.set_session_model(model)
        self._persist()

    def set_thinking_level(self, level: ThinkingLevel | None) -> None:
        """Switch and persist the thinking/reasoning level."""
        from dataclasses import replace

        self.agent.set_options(replace(self.agent.options, thinking=level))
        self.settings.set_session_thinking(level or "off")
        self._persist()

    # -- Compaction --

    async def compact(self) -> None:
        """Force conversation compaction."""
        await self._run_compaction()

    async def _maybe_compact(self) -> None:
        """Check if the conversation needs compaction and run it if so."""
        messages = self.messages
        if not messages:
            return

        char_count = 0
        for msg in messages:
            if isinstance(msg, UserMessage):
                if isinstance(msg.content, str):
                    char_count += len(msg.content)
                else:
                    for part in msg.content:
                        if isinstance(part, TextContent):
                            char_count += len(part.text)
                        else:
                            char_count += 200
            elif isinstance(msg, AssistantMessage):
                for reply_part in msg.content:
                    if isinstance(reply_part, TextContent):
                        char_count += len(reply_part.text)
                    else:
                        char_count += 200
            else:
                char_count += 200

        estimated_tokens = char_count / 4
        threshold = self.context_limit * COMPACTION_THRESHOLD_RATIO

        if estimated_tokens > threshold:
            await self._run_compaction()

    async def _run_compaction(self) -> None:
        """Execute compaction: summarize old messages and replace them."""
        from xdog.coding.core.compaction.compaction import compact_messages

        current_messages = self.messages
        if len(current_messages) < 4:
            return

        try:
            compacted = await compact_messages(current_messages, self.agent)
            self.agent.replace_messages(compacted)
            await self.event_bus.emit("compaction", message_count=len(compacted))
            self._persist()
        except Exception as exc:
            logger.warning("Compaction failed: %s", exc)

    # -- Session management --

    def clear(self) -> None:
        """Clear conversation history."""
        self.agent.clear_messages()
        self._persist()

    def switch_session(self, session_id: str) -> bool:
        """Switch to a different session. Returns True if successful."""
        loaded = self.session_manager.load_session(session_id)
        if loaded is None:
            return False

        self.session_data = loaded
        self.permissions.clear_session_rules()
        restored_messages = list(self.session_data.messages)
        self.agent.replace_messages(restored_messages)

        if self.session_data.settings:
            self.settings.load_session_settings(self.session_data.settings)

        return True

    # -- Branch management --

    def create_branch(self, *, at_index: int | None = None) -> str:
        """Create a conversation branch. Returns the branch id."""
        messages = self.messages
        branch_point = at_index if at_index is not None else len(messages) - 1
        branch_id = uuid.uuid4().hex[:8]
        branch_messages = messages_to_dicts(messages[:branch_point + 1])
        self.session_data.branches.append({
            "branch_id": branch_id,
            "branch_point": branch_point,
            "messages": branch_messages,
        })
        self._persist()
        return branch_id

    def restore_branch(self, branch_id: str) -> bool:
        """Restore a previously created branch."""
        for branch in self.session_data.branches:
            if branch["branch_id"] == branch_id:
                restored = dicts_to_messages(branch["messages"])
                self.agent.replace_messages(restored)
                self._persist()
                return True
        return False

    # -- Internal --

    def _push_skills(self) -> None:
        """Tell the Agent which skills are active; it decides where they go.

        The index and the bodies are both its business now — this only says
        *which*, which is the part a product legitimately differs on.
        """
        try:
            self.agent.set_active_skills(sorted(self._active_skills))
        except Exception:
            logger.debug("could not push active skills to the agent", exc_info=True)

    def _native_tool_calls(self) -> bool:
        """Whether this model gets tool definitions through the API.

        Reads the same answer the Agent uses to decide whether to send them, so
        the two cannot disagree — a model whose tools are withheld from the API
        while the prompt stays silent about them has no tools at all, and
        nothing would report that.

        Unknown answers False, so the tools stay described. Both halves then
        fire, which wastes a few hundred tokens and cannot leave the model
        unable to act.
        """
        from xdog.agent.helpers import model_supports_tool_calls

        return model_supports_tool_calls(self.agent.state.model or "") is True

    def _rebuild_system_prompt(self) -> None:
        """Rebuild and set the system prompt from config and tools."""
        from xdog.coding.config import PlatformInfo, RuntimeConfig

        # Get tool definitions from the agent's current tools
        tool_defs = [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in self.agent.state.tools
        ]

        # Build a minimal RuntimeConfig for system prompt generation
        prompt = build_system_prompt(
            RuntimeConfig(
                model=self.agent.state.model or "unknown",
                thinking_level=str(self.agent.options.thinking or "normal"),
                permission_mode=self.permissions.mode,
                allowed_tools=tuple(t.name for t in self.agent.state.tools),
                custom_instructions=self.settings.custom_instructions,
                extensions=(),
                working_dir=str(self.working_dir),
                platform_info=PlatformInfo.detect(),
            ),
            tool_defs,
            extra_context=_skills_context(self._active_skills),
            native_tool_calls=self._native_tool_calls(),
        )
        self.agent.set_system_prompt(prompt)

    def _on_agent_event(self, event: AgentEvent) -> None:
        """Handle agent lifecycle events."""
        if isinstance(event, AgentEndEvent):
            self._persist()

    def _persist(self) -> None:
        """Save session state to disk."""
        self.session_data.messages = self.messages
        self.session_data.settings = self.settings.session_to_dict()
        self.session_data.model = self.agent.state.model or ""

        # Update summary from last assistant message
        msgs = self.messages
        if msgs:
            last = msgs[-1]
            if isinstance(last, AssistantMessage):
                for part in last.content:
                    if isinstance(part, TextContent):
                        self.session_data.summary = part.text[:120]
                        break

        self.session_manager.save_session(self.session_data)

    def dispose(self) -> None:
        """Clean up resources."""
        self.permissions.close()
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None


def _skills_context(active: frozenset[str] = frozenset()) -> str:
    """The skills section of the system prompt.

    Two levels, following the format's progressive disclosure: every skill
    contributes one line so the model knows it exists, and the ones the user
    has activated contribute their full instructions. Rebuilt from scratch on
    every turn, which is what makes deactivation work — an activated skill
    leaves no trace once its slug is dropped.

    Returns an empty string on any failure. A skills directory that cannot be
    read is not a reason to start without a system prompt.
    """
    try:
        from xdog.coding.core.slash_commands import skill_manager

        manager = skill_manager()
        summary = manager.skills_summary()
        # Bodies are the Agent's to place (`set_skills`); this section carries
        # only the standing note about them. Rendering them here as well was how
        # three packages ended up with three answers to the same question.
        bodies = ["(active: " + ", ".join(sorted(active)) + ")"] if active else []
    except Exception:
        logger.debug("could not build the skills section of the system prompt", exc_info=True)
        return ""

    sections = []
    if summary:
        sections.append(summary + "\nRun one with the /<slug> command, or ask the user to.")
    if bodies:
        sections.append(
            "# Active skills\n\n"
            "The user activated these; follow them.\n\n"
            "You cannot deactivate a skill — there is no tool for it, by design. "
            "Skills are often constraints rather than conveniences, and the party "
            "being constrained should not hold the release. If one looks finished "
            "or irrelevant, say so and suggest `/unload <name>`; the user decides.\n\n"
            + "\n\n".join(bodies)
        )
    return "\n\n".join(sections)
