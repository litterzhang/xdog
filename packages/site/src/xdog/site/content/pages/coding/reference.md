---
title: Reference
---

The `xdog-coding` command, its slash commands, and the embedding SDK.

## CLI — `xdog-coding`

A single command (not subcommands) with flags. FILES… are appended to the initial
message.

| Flag | Does |
|---|---|
| `-m / --model` | Choose the model. |
| `-r / --resume · --resume-id` | Resume the most-recent session, or one by id. |
| `-p / --prompt` | Provide the initial prompt. |
| `--print · --output-format` | Non-interactive output as text / json / markdown. |
| `--working-dir · --config` | Set the working directory / config file. |
| `--thinking-level` | none / normal / deep / ultrathink. |
| `--list-models · --pick-session` | List models / pick a session interactively. |
| `--acp · --session ID` | Explicit ACP ingress / send to an exact live session. |
| `--verbose` | Verbose logging. |

## Slash commands

Available inside the interactive session (`slash_commands.py`).

| Command | Does |
|---|---|
| `/help` | List commands. |
| `/model · /thinking` | Switch model / reasoning depth. |
| `/compact · /clear` | Compact history / clear the session. |
| `/session · /sessions` | Show the current session / list sessions. |
| `/fork · /branch` | Fork the conversation / manage branches. |
| `/quit · /exit` | Leave. |

## SDK entry points

For embedding the coding agent programmatically (`core/sdk.py`).

| Name | Purpose |
|---|---|
| `create_agent_session` | Build a ready `AgentSession` from options. |
| `CreateSessionOptions / Result` | Typed inputs / outputs for session creation. |
| `AgentSession` | The persisted, event-subscribed session wrapper. |
