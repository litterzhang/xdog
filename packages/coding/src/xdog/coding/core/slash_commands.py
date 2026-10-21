"""Slash commands: built-in commands available in interactive and REPL modes."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import cast

from xdog.agent.async_work import run_owned
from xdog.agent.capabilities.runtime import CapabilitySet
from xdog.agent.extensions.command_result import ExtensionCommandResult
from xdog.agent.skills import Skill, SkillManager
from xdog.ai.types import ThinkingLevel
from xdog.coding.config import get_skills_dir
from xdog.coding.core.agent_session import AgentSession


@dataclass(frozen=True)
class CommandResult:
    """Result of executing a slash command."""

    output: str
    exit_requested: bool = False
    #: Text to send to the agent as if the user had typed it. Loading a skill
    #: is only useful if its instructions reach the model — printing them to
    #: the terminal would tell the human something they already asked for and
    #: leave the agent none the wiser.
    prompt: str = ""
    rich_output: ExtensionCommandResult | None = None


# Registry of built-in slash commands: name → description
BUILTIN_COMMANDS: dict[str, str] = {
    "inbox": "Inspect automatic inbox: /inbox [list [OFFSET]|show ID|cancel ID|retry ID]",
    "help": "Search terminal commands: /help [QUERY]",
    "artifact": "Load a checksummed command artifact read-only: /artifact PATH",
    "terminal-input": "Inspect/restore/discard original rejected terminal input",
    "login": "Extension-provider authentication menu: /login [EXTENSION_PROVIDER_ID]; native auth uses xdog-ai",
    "extensions": "Trusted extensions: /extensions [list|reload NAME|unload NAME|auth list|auth menu]",
    "agents": "Child browser/controls; start supports --model/--thinking and reviewed specialization settings",
    "readonly": "Host read-only scope: /readonly on | off | status",
    "tools": "Read-only configured/active tool/schema browser: /tools [QUERY]",
    "mcp": "Manage explicit MCP connections: /mcp [list|connect|disconnect|refresh] [server]",
    "skills": "List available skills: /skills",
    "unload": "Drop an active skill: /unload [name|all]",
    "model": "Show or switch model: /model [name]",
    "thinking": "Show or set thinking level: /thinking [off|low|medium|high]",
    "compact": "Summarize older context: /compact [focus instructions]; TUI /compact cancel",
    "clear": "Clear conversation history",
    "session": "Show session info",
    "name": "Inspect/name the current session: /name [NAME|- to clear]",
    "sessions": "Browse saved sessions",
    "resume": "Activate a saved session: /resume ID",
    "new": "Create a new session",
    "tree": "Browse conversation branches/checkpoints",
    "goto": "Fork/navigate at a stable checkpoint: /goto INDEX",
    "fork": "Create a conversation branch",
    "branch": "Restore a conversation branch",
    "quit": "Exit the agent",
    "exit": "Exit the agent",
}


@lru_cache(maxsize=1)
def skill_manager() -> SkillManager:
    """The skill source for slash commands.

    Cached because it is consulted on every unrecognised command, and because
    constructing one creates its directory. Discovery of packaged skills is
    left at its default, so installing a distribution that ships one is all it
    takes for a new command to appear.
    """
    return SkillManager(shared_dir=get_skills_dir())


def available_skills() -> list[Skill]:
    """Skills usable as commands right now. Never raises — a broken skills
    directory should not take the command dispatcher down with it."""
    try:
        return skill_manager().list_skills()
    except OSError:
        return []


def command_reservations() -> tuple[frozenset[str], frozenset[str]]:
    """Caller runs filesystem skill discovery in owned IO, never in a worker RPC reader."""
    from xdog.coding.core.command_names import UI_COMMANDS
    return frozenset({*BUILTIN_COMMANDS, *UI_COMMANDS, 'draft'}), frozenset(s.slug for s in available_skills())


def list_commands() -> dict[str, str]:
    """Return every command, built-in and skill-provided.

    Skills are listed alongside built-ins deliberately: to the person typing,
    `/flow` and `/model` are the same gesture, and a skill that does not appear
    in completion may as well not be installed. Built-ins win a name clash, so
    a skill can never shadow `/quit`.
    """
    commands = {s.slug: s.description or f"Skill: {s.name}" for s in available_skills()}
    commands.update(BUILTIN_COMMANDS)
    return commands


async def execute_command(
    command: str,
    args: str,
    session: AgentSession,
) -> CommandResult:
    """Execute a slash command."""
    cmd = command.lower().strip()
    from xdog.agent.extensions.runtime import ExtensionRuntime
    extensions = getattr(session, 'extensions', None)
    public_extension = False
    if isinstance(extensions, ExtensionRuntime):
        builtins, skills = await run_owned(command_reservations)
        extensions.reserve_commands(builtins, skill_commands=skills)
        if cmd in extensions.command_conflicts():
            raise ValueError(f'Extension command conflict: /{cmd}; resolve the conflict or unload the extension')
        public_extension = cmd in extensions.commands()
    capabilities = getattr(session, 'capabilities', None)
    native_candidate = (cmd not in BUILTIN_COMMANDS and isinstance(capabilities, CapabilitySet)
                        and cmd in capabilities.declared_commands)
    worker_control = (isinstance(extensions, ExtensionRuntime)
                      and (public_extension or cmd.startswith('ext-') or cmd in {'extensions', 'login'}))
    synchronous_controls = {"readonly", "model", "thinking", "clear", "name", "resume", "new",
                            "goto", "fork", "branch", "unload"}
    requires_owner = native_candidate or worker_control or cmd in synchronous_controls or (
        cmd not in BUILTIN_COMMANDS and not cmd.startswith("ext-") and await run_owned(_load_skill, cmd) is not None)
    controller = (session.runtime.controller if session.runtime is not None else session._sdk_controller
                  ) if requires_owner else None
    # Local controls/skill activation retain their original no-owner path.
    # Only a declared capability command or real extension operation may open
    # an SDK owner, and only the former starts installed capabilities.
    if controller is None and (native_candidate or worker_control):
        controller, _ = await session._acquire_owner()
        try:
            async def acquired() -> CommandResult:
                return await execute_command(command, args, session)
            return await asyncio.wrap_future(controller.control(acquired, name="slash-command"))
        finally:
            closing = session._release_sdk_owner(controller)
            if closing is not None:
                from xdog.agent.async_work import wait_owned
                await wait_owned(asyncio.wrap_future(closing))
    if controller is not None and not controller.owns_current_task:
        async def owned() -> CommandResult:
            return await execute_command(command, args, session)
        return await asyncio.wrap_future(controller.control(owned, name="slash-command"))
    if native_candidate:
        await session.initialize_capabilities()
        public_extension = cmd in session.extensions.commands()
    if cmd == 'login':
        from xdog.agent.extensions.provider_oauth_host import login_arguments
        cmd, args = 'extensions', 'auth ' + login_arguments(args)

    if cmd == "extensions" or cmd.startswith("ext-") or public_extension:
        result = await session.extension_command(cmd, args, user_authorized=True)
        return (CommandResult(output=result.summary, rich_output=result) if isinstance(result, ExtensionCommandResult)
                else CommandResult(output=result))
    if cmd == "inbox":
        from xdog.coding.core.inbox_commands import inbox_command
        return await inbox_command(session, args)
    if cmd == 'tools':
        from xdog.agent.extensions.tool_inventory import page
        snapshot = await asyncio.to_thread(session._extension_tools)
        if snapshot is None:
            return CommandResult(output='Tool inventory unavailable; no empty/approved fallback')
        matched = []
        offset = 0
        inventory_revision = ''
        while True:
            chunk = page(snapshot, query=args, offset=offset, revision=inventory_revision)
            assert chunk is not None
            matched.extend(chunk['tools'])
            inventory_revision = chunk['revision']
            if chunk['next_offset'] is None:
                break
            offset = chunk['next_offset']
        active = ({'active_tools': [row['name'] for row in matched if row['name'] in snapshot['active_tools']]}
                  if 'active_tools' in snapshot else {})
        return CommandResult(output=json.dumps({**snapshot, **active, 'tools': matched,
            'notice': 'Configured definitions and selection; permission policy and model API gate still apply'},
            ensure_ascii=False))
    if cmd == "agents":
        return CommandResult(output=await session.agents_command(args))
    if cmd == "mcp":
        parts = args.split()
        if len(parts) > 2:
            return CommandResult(output="Usage: /mcp [list|connect SERVER|disconnect SERVER|refresh SERVER]")
        return CommandResult(output=await session.manage_mcp(parts[0] if parts else "list",
                                                            parts[1] if len(parts) > 1 else ""))
    if cmd == 'compact':
        return await _cmd_compact(session, args)
    return await run_owned(_local_command, cmd, args, session)


def _local_command(cmd: str, args: str, session: AgentSession) -> CommandResult:
    """Finite synchronous preparation under the controller's copied owner token."""
    if cmd == "readonly":
        action = args.strip() or "status"
        if action not in {"on", "off", "status"}:
            return CommandResult(output="Usage: /readonly on | off | status")
        state = session.access.state if action == "status" else session.access_control(action == "on")
        return CommandResult(output=json.dumps(state.to_dict(), indent=2))

    if cmd == "help":
        return _cmd_help(session, args)
    elif cmd == "model":
        return _cmd_model(args, session)
    elif cmd == "thinking":
        return _cmd_thinking(args, session)
    elif cmd == "clear":
        return _cmd_clear(session)
    elif cmd == "name":
        name = session.rename_session("" if args.strip() == "-" else args) if args else session.session_data.name
        return CommandResult(output=f"Session name: {name or '(unnamed)'}")
    elif cmd == "session":
        return _cmd_session(session)
    elif cmd == "sessions":
        return _cmd_sessions(session)
    elif cmd == "resume":
        return CommandResult(output=(
            "Session activated" if session.switch_session(args.strip()) else "Session unavailable"
        ))
    elif cmd == "new":
        return CommandResult(output=f"New session: {session.new_session()}")
    elif cmd == "goto":
        from xdog.agent.history import validate_checkpoint
        from xdog.coding.core.messages import dicts_to_messages
        parts = args.split()
        if len(parts) in {1, 2, 3}:
            checkpoint = int(parts[-1]) - 1
            target_id = parts[0] if len(parts) > 1 else session.session_id
            target_branch = parts[1] if len(parts) == 3 and parts[1] != "-" else ""
            target = session.session_manager.load_session(target_id)
            if target is None:
                return CommandResult(output="Session unavailable")
            source = session.messages if target_id == session.session_id else target.messages
            if target_branch:
                branch = next((value for value in target.branches if value.get("branch_id") == target_branch), None)
                if branch is None:
                    return CommandResult(output="Branch unavailable")
                source = dicts_to_messages(branch["messages"])
            validate_checkpoint(source, checkpoint)
        if len(parts) == 1:
            key = session.goto_checkpoint(int(parts[0]) - 1)
        elif len(parts) in {2, 3}:
            key = session.navigate_checkpoint(parts[0],
                branch_id=parts[1] if len(parts) == 3 and parts[1] != "-" else "", index=int(parts[-1]) - 1)
        else:
            return CommandResult(output="Usage: /goto [SESSION [BRANCH|-]] INDEX")
        return CommandResult(output=f"Checkpoint branch: {key}")
    elif cmd == "fork":
        return _cmd_fork(args, session)
    elif cmd == "branch":
        parts = args.split()
        if len(parts) == 2:
            from xdog.agent.history import validate_checkpoint
            from xdog.coding.core.messages import dicts_to_messages
            target = session.session_manager.load_session(parts[0])
            branch = (next((value for value in target.branches if value.get("branch_id") == parts[1]), None)
                      if target else None)
            if branch is None:
                return CommandResult(output="Branch unavailable")
            branch_messages = dicts_to_messages(branch["messages"])
            if branch_messages:
                validate_checkpoint(branch_messages, len(branch_messages) - 1)
            if parts[0] != session.session_id:
                session.navigate_checkpoint(parts[0], branch_id=parts[1])
                return CommandResult(output=f"Restored branch: {parts[1]}")
            args = parts[1]
        return _cmd_branch(args, session)
    elif cmd in ("quit", "exit"):
        return CommandResult(output="Goodbye.", exit_requested=True)
    elif cmd == "skills":
        return _cmd_skills(session)
    elif cmd == "unload":
        return _cmd_unload(args, session)

    # Not a built-in: a skill of that name becomes a command. Checked last so
    # a skill can never take over `/quit`.
    skill = _load_skill(cmd)
    if skill is not None:
        return _run_skill(skill, args, session)

    return CommandResult(output=f"Unknown command: /{cmd}. Type /help for available commands.")


def parse_slash_command(text: str) -> tuple[str, str] | None:
    """Parse a slash command from user input.

    Returns (command, args) or None if not a slash command.
    """
    if not text.startswith("/"):
        return None
    parts = text[1:].split(None, 1)
    cmd = parts[0] if parts else ""
    args = parts[1] if len(parts) > 1 else ""
    return (cmd, args)


# -- Command implementations --


def _load_skill(slug: str) -> Skill | None:
    try:
        return skill_manager().load_skill(slug)
    except OSError:
        return None


def _run_skill(skill: Skill, args: str, session: AgentSession) -> CommandResult:
    """Activate a skill, and start a turn if the user gave one.

    The instructions go into the system prompt rather than the message
    history. That costs a prompt-cache miss on activation, and buys the only
    thing the message-history approach cannot offer: taking them back out.

    `/flow` on its own just activates. `/flow add a retry step` activates and
    sends the request, so it reads as one instruction — the skill says how,
    the argument says what.
    """
    session.activate_skill(skill.slug)
    origin = " (shipped with an installed package)" if skill.packaged else ""
    note = f"Activated skill: {skill.name}{origin} — /unload {skill.slug} to drop it"
    return CommandResult(output=note, prompt=args)


def _cmd_unload(args: str, session: AgentSession) -> CommandResult:
    active = session.active_skills
    if not args:
        if not active:
            return CommandResult(output="No skills are active.")
        return CommandResult(
            output="Active skills: " + ", ".join(sorted(active)) + "\nDrop one with /unload <name>."
        )

    slug = args.strip()
    if slug == "all":
        for s in sorted(active):
            session.deactivate_skill(s)
        return CommandResult(output=f"Unloaded {len(active)} skill(s)." if active else "Nothing to unload.")

    if session.deactivate_skill(slug):
        return CommandResult(output=f"Unloaded skill: {slug}")
    return CommandResult(output=f"Skill not active: {slug}")


def _cmd_skills(session: AgentSession) -> CommandResult:
    skills = available_skills()
    if not skills:
        return CommandResult(
            output=(
                "No skills found.\n\n"
                f"Add one at {get_skills_dir()}/<name>/SKILL.md, or install a package "
                "that ships one — `pip install xdog-flow` provides /flow."
            )
        )

    active = session.active_skills
    lines = ["Available skills:", ""]
    width = max(len(s.slug) for s in skills)
    for s in skills:
        mark = "*" if s.packaged else " "
        state = "  [active]" if s.slug in active else ""
        lines.append(f"  {mark} /{s.slug:<{width}s}  {s.description}{state}")
    lines += [
        "",
        "  * shipped with an installed package",
        "",
        "Activate with /<name> [request], drop with /unload <name>.",
    ]
    return CommandResult(output="\n".join(lines))


def _cmd_help(session: AgentSession | None, query: str = '') -> CommandResult:
    from xdog.tui.command_help import command_help
    commands = {**BUILTIN_COMMANDS, **(session.extensions.commands() if session is not None else {})}
    output = command_help(commands, query)
    if session is not None and session.extensions.command_conflicts():
        conflicts = ', '.join('/' + name for name in session.extensions.command_conflicts())
        output += '\nExtension command conflict (resolve or unload the extension): ' + conflicts
    skills = {skill.slug: skill.description for skill in available_skills()}
    if skills:
        output += '\n\nSkills:\n' + command_help(skills, query)
    return CommandResult(output=output)


def _cmd_model(args: str, session: AgentSession) -> CommandResult:
    """Show current model or switch to a new one."""
    current = session.model or "unknown"

    if not args:
        all_models = session.model_catalog()
        lines = [f"Current model: {current}", "", "Available models:"]
        for m in sorted(all_models, key=lambda x: x.id):
            short = m.id  # Canonical adapter IDs avoid ambiguous cross-provider suffixes.
            marker = "  → " if short == current or m.id == current else "    "
            reasoning = " (reasoning)" if m.reasoning else ""
            lines.append(f"{marker}{short}{reasoning}")
        if not all_models:
            lines.append("    (no models available)")
        return CommandResult(output="\n".join(lines))

    # Switch model
    target = args.strip()
    session.set_model(target)
    return CommandResult(output=f"Switched to model: {session.model}")


def _cmd_thinking(args: str, session: AgentSession) -> CommandResult:
    """Show or change the thinking level."""
    if not args:
        level = session.agent.options.thinking or "off"
        return CommandResult(output=f"Current thinking level: {level}")

    requested = args.strip().lower()
    valid_levels = ("off", "minimal", "low", "medium", "high", "xhigh")
    if requested not in valid_levels:
        return CommandResult(
            output=f"Invalid thinking level: {requested}\nValid levels: {', '.join(valid_levels)}"
        )

    if requested == "off":
        session.set_thinking_level(None)
    else:
        session.set_thinking_level(cast(ThinkingLevel, requested))
    return CommandResult(output=f"Thinking level set to: {requested}")


async def _cmd_compact(session: AgentSession, instructions: str = "") -> CommandResult:
    original = session.messages
    msg_before = len(original)
    await session.compact(instructions)
    msg_after = len(session.messages)
    if original == session.messages:
        return CommandResult(output="No older context to compact; no summary request needed")
    recovery = (session.session_data.compaction or {}).get('recovery_branch_id', '')
    return CommandResult(output=f"Compacted: {msg_before} → {msg_after} messages"
                         + ("\nRecovery checkpoint saved; /tree → Before compaction" if recovery else ""))


def _cmd_clear(session: AgentSession) -> CommandResult:
    session.clear()
    return CommandResult(output="Conversation cleared.")


def _cmd_session(session: AgentSession) -> CommandResult:
    info = session.session_info()
    lines = [
        f"Session ID: {info['session_id']}",
        f"Name:       {info['name'] or '(unnamed)'}",
        f"Messages:   {info['usage']['message_count']}",
        f"Model:      {info['model']['id'] or 'unknown'}",
        f"Thinking:   {info['model']['thinking']}",
        f"Permissions: {info['permission_mode']}",
        f"Working dir: {info['cwd']}",
    ]
    return CommandResult(output="\n".join(lines))


def _cmd_sessions(session: AgentSession) -> CommandResult:
    metas = session.session_manager.list_sessions(limit=10)
    if not metas:
        return CommandResult(output="No sessions found.")

    lines = ["Recent sessions:", ""]
    for i, meta in enumerate(metas):
        current = " ← current" if meta.session_id == session.session_id else ""
        summary = (meta.name or meta.summary)[:60] or "(empty)"
        lines.append(f"  {i + 1}. {meta.session_id[:12]} | {meta.model} | {summary}{current}")
    return CommandResult(output="\n".join(lines))


def _cmd_fork(args: str, session: AgentSession) -> CommandResult:
    at_index: int | None = None
    if args:
        try:
            at_index = int(args.strip())
        except ValueError:
            return CommandResult(output=f"Invalid index: {args}")

    branch_id = session.create_branch(at_index=at_index)
    return CommandResult(output=f"Branch created: {branch_id}")


def _cmd_branch(args: str, session: AgentSession) -> CommandResult:
    if not args:
        branches = session.session_data.branches
        if not branches:
            return CommandResult(output="No branches. Use /fork to create one.")
        lines = ["Branches:", ""]
        for b in branches:
            lines.append(f"  {b['branch_id']} (at message {b['branch_point']})")
        return CommandResult(output="\n".join(lines))

    branch_id = args.strip()
    if session.restore_branch(branch_id):
        return CommandResult(output=f"Restored branch: {branch_id}")
    return CommandResult(output=f"Branch not found: {branch_id}")
