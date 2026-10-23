---
title: Overview
---

*Goal and plan instructions, not another Agent harness.*

The common skills hub ships `goal` and `plan`, plus isolated Goal domain support
and an optional readonly display extension. Flow and Eyes keep their own engines
and packaged skills. There is no separate `xdog-goal` distribution or
`xdog-skills` management CLI.

## Highlights

- Coding and Claw include `xdog-skills` by default.
- Skill metadata is discoverable; full bodies are not activated by default.
- Goal model tools and native `/goal` commands are independent of body activation.
- The current main Agent plans, works/delegates, reports progress and judges completion.
- Goal owns records/task files and signal-driven reminders, not a worker/reviewer runner.
- One current Goal per stable owner; completed remains visible until explicit clear or a new Goal.

```bash
pip install xdog-skills
```

Use the commands inside a capability-enabled Coding/Claw session, not as shell
commands. A generic SDK host must open the appropriate installed capability with
real owner/ACP/lifecycle ports; installing a skill does not invent those ports.

See [Reference](/packages/skills/reference) and the
[packaged instructions](https://github.com/litterzhang/xdog/tree/main/packages/skills/src/xdog/skills/skills).
