---
title: Overview
---

*Independent observations, explicit JSON destinations.*

Eyes collects file/time evidence, turns it into events and routes it to explicit
handlers. It never owns a model/tool loop or decides whether a user received an
Agent's response. The CLI and installed model tool manage the same user-local
shared daemon.

## Highlights

- Versioned JSON with file, one-off `at`, interval and zoned cron sources.
- Frozen definitions, validation, detectors and explicit handler routes.
- Durable owner-scoped registrations, source cursors and delivery checkpoints.
- One daemon under `~/.local/share/xdog/eyes`, honoring `XDG_DATA_HOME`.
- Application-scoped `eyes` tool and discoverable skill; no default body injection.
- Unknown/interrupted effects are not automatically replayed.

## Start with a definition

```bash
pip install xdog-eyes
xdog-eyes validate /absolute/path/to/eyes.json
xdog-eyes --owner repo-review start /absolute/path/to/eyes.json
xdog-eyes --owner repo-review list
```

First copy/edit a [shipped example](https://github.com/litterzhang/xdog/tree/main/packages/eyes/src/xdog/eyes/examples)
with a real receiver, existing destination and deliberately scoped source roots.
The placeholder path above is not an observation created for you.

See [Reference](/packages/eyes/reference) for controls and
[Skills](/packages/skills) for Goal's optional task observation.
