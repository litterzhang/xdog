---
title: Design
---

How coding composes ai + agent + tui into a terminal coding assistant — sessions,
three run modes, and layered settings.

## The reference application for the stack

coding is where the lower-level packages become a product: it builds an
`agent.Agent` over ai's Copilot provider and the default `filesystem` / `bash` /
`current_time` tools, wraps it in an `AgentSession`, and renders it with `tui`. If
you want to see how ai + agent + tui compose, this is the worked example.

## Sessions that persist, branch, and compact

A session is JSON-persisted `SessionData` (`session_manager.py`). The
`AgentSession` subscribes to `Agent` events to persist as it runs, compacts
history when it approaches the model window (`core/compaction`), and supports
branching — fork a conversation and restore a branch — so exploratory work does
not clobber the main thread.

## Native run modes and explicit ACP ingress

The same session core drives the native front-ends: an interactive TUI (header / chat
log / status / editor) that streams tokens and visualises tool runs; a
non-interactive print mode with `text` / `json` / `markdown` output for scripting. Explicit ACP intake routes to the same sole session
controller; it is not a replacement complex frontend-control protocol.

## Layered settings

Settings resolve session > project > global via pydantic models
(`settings_manager.py`), so a repository can pin a model or thinking level while a
single session overrides it for one run.

## Extensible via skills and extensions

YAML skills (`core/skills.py`) and an extension loader (`core/extensions`) let a
project add reusable prompts and capabilities without forking the agent.
