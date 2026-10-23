---
title: Overview
---

*Interactive coding-agent CLI, with a session SDK over the shared Agent runtime.*

Coding combines `xdog.ai`, `xdog.agent` and `xdog.tui` into a Linux terminal coding
assistant. Interactive TUI, one-shot print and explicit ACP intake share the same
session controller; none introduces a second tool-calling loop.

## Highlights

- Native scrollback, a multiline editor, tool/reasoning details and explicit tool
  approvals; print mode supports text, JSON and Markdown output.
- JSON-persisted sessions, explicit session/branch navigation, searchable history
  and context compaction. `-r` opens a current-directory selector, not automatic
  most-recent-session execution.
- Host-owned filesystem/bash/current-time tools, a separate MCP registry and
  explicitly trusted extension hooks, commands, basic dialogs and providers.
  Extension workers do not register model tools.
- Default `ask` tool policy, separate readonly scope and explicit `allow-all` for
  trusted unattended mutation. Tool selection and skill loading grant no authority.
- Host-owned children with independent task knowledge, system prompts, models and
  approvals, limited to frozen subsets of actual parent tools and available skills.
- `xdog-skills` is included by default: native Goal commands/tools are independent
  of skill-body activation and the optional readonly display worker.
- Installed Eyes/Flow capabilities use their public CLIs. Eyes shares an independent
  user-local daemon; Flow has none. Discovery creates no observation or workflow.

## Try it

```bash
uv run xdog-coding
uv run xdog-coding -r
uv run xdog-coding --print --thinking-level off --prompt "Explain this repository"
```

Choose a real configured model with `--model MODEL_ID`; `--list-models` lists
available models. Reasoning levels are `off`, `minimal`, `low`, `medium`, `high`,
`xhigh`, subject to the actual model's capabilities. See the
[CLI and permission reference](/packages/coding/reference).

## Durable work without another Agent

Goal records an explicitly delegated outcome for the exact Coding session/branch.
The current main Agent plans from its real conversation, works itself or delegates
through authorized host tools, saves progress assessments and judges completion.
Task JSON files are authoritative. No mandatory reviewer or Goal-owned scheduler
exists, and the Goal skill body is **not active by default**.

Completed remains current/viewable until clear or a newly delegated Goal replaces
that completed binding. User waits require actual user evidence; task waits require
the main Agent's evidence check and versioned activation. Pause stops proactive
reminders and Goal's own observation until explicit user resume, not accepted tools
or child work. See [skills](/packages/skills) and [Coding design](/packages/coding/design).

ACP callbacks use an explicit endpoint and session. The lightweight receiver can
persist inputs while the TUI/owner is absent; an activated owner automatically
consumes pending input under the original tool policy. Intake ACKs are not execution
results, and interrupted/unknown effects are never automatically replayed. Coding's
former JSON-lines frontend-control RPC is removed; ACP and private MCP/extension
transports remain separate.

Full user configuration and interaction details remain in the
[package README](https://github.com/litterzhang/xdog/blob/main/packages/coding/README.md).
