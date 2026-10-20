# Extension public slash commands

Approved design: expose `/goals` from goal_ui through a generic extension public
command name, not Goal-specific host routing. Internal extension/command identity,
callback fencing and tool authority stay unchanged. Work inline in this checkout;
preserve the preceding uncommitted Coding reliability changes. No commit/push,
new goal, worktree or new dependencies.

## Tasks

- [x] Agent protocol/runtime: optional public_name on command registration; bounded
  validation, collision rejection (host reservations/skills/other extensions), one
  routing/catalog mapping for names, owners and argument completion. Preserve
  qualified internal identity, command result attribution and existing keybindings.
  Add API, actual-worker, live replacement/unload and collision regressions first.
- [x] Host integration: Coding and Claw route public names by the actual catalog,
  retain original extension identity for dialogs/catalog grants, and advertise the
  same names in help/completion/shortcuts. Reserve built-in and skill commands,
  with no model/worker activation caused by discovery. Test actual both-host paths.
- [x] Goal UI/docs: declare goals public name, show /goals in usage/pagination;
  retain /goal skill. Verify readonly display/no model execution, formatting,
  strict affected typing and full workspace tests. Record exact evidence below.

## Evidence / rulings

- Scope is the approved user-command change, not a model tool or host-specific
  Goal endpoint. Existing fully qualified callback/result/keybinding IDs remain
  internal identities; public menus show only the declared public name.
- API/real worker RED public-commands-agent-red.log:8 failed; GREEN8 passed.
- Both-host/shortcut RED public-commands-host-red.log:4 failed; GREEN8 passed.
- Remote snapshot ownership/name/case audit RED4 failed; GREEN18 passed with
  existing shortcuts. Public ownership cannot reassign a qualified source.
- Extra actual Coding/Claw + protected native names/goal skill/UI source checks:
  public-commands-host-extra.log,10 passed; no model invocation for queries.
- Intermediate five-package run public-commands-related.log:5393 passed,1 failed.
  Sole failure pinned the old goal_ui Python source SHA. Kept the byte fingerprint
  assertion and updated it to actual new source (8eef6b34bd726682d30b3a6c65dd6ca6327cd37d201df248f67199ec015724ce).
- Public commands without public_name retain existing names. A public alias never
  changes rich-result provenance or user keybinding identity. Host builtin/skill
  reservations are prepared off-loop; no new worker/model is started by discovery.
- Read-only independent review running while final docs/cold artifact verification
  proceed. Final current suite, formatting and typing evidence is recorded below.

## Independent review fix pass

- Review confirmed three Important defects: actual Claw sender still parsed public
  names with split('-'); later skill reservations poisoned catalog and recovery;
  completion scanned failed historical owners after selecting a live identity.
- RED public-commands-review-red.log:4 failures (two actual native command names,
  recoverable dynamic skill collision, real two-worker alias takeover).
- Fixed actual native send to use validated ownership before publishing pending.
  Dynamic host/skill conflicts quarantine only the alias; help/status/unload stay
  functional and invoking the collision explicitly refuses fallback to a skill.
  One current route resolver now drives both command and completion callbacks.
- While fixing completion, retained its post-RPC registration/result validation
  and restored closed-owner staleness: RED2 failures (one existing regression plus
  explicit public completion-after-close), then GREEN40 passed across the reviewed
  API/Coding/Claw/argument-completion paths (public-commands-review-final.log).
- No new model call, source-label authority, Goal lifecycle decision, or tool
  permission was introduced. No second reviewer; final full suite verifies the fix.
- Both source and rebuilt cold installed Coding/Claw public Goal queries succeeded
  before this fix pass; repeat cold probes after rebuilding current artifacts.

## Final acceptance

- Full configured workspace: `uv run python scripts/test.py` -> **7280 passed,
  7 existing opt-in external-CLI skips**, all9 paths, exit0,230.54s. Coding1337,
  Claw1304, Agent1968. Log: /data/t.Vy5SaQ/public-commands-workspace.log.
- `uv run ruff check packages` clean; nine-package strict mypy clean in608 source
  files. Flow examples17 passed. Logs: public-commands-accepted-{ruff,mypy}.log
  and public-commands-flow.log.
- Rebuilt9 wheels +9 sdists after all production fixes; Twine passes (only the
  pre-existing Site long-description metadata warnings). Fresh installed Coding
  and Claw base-without-skills plus installed-with-skills Goal lifecycles pass;
  both query public `goals`, verify its actual owner and absence of the qualified
  ID in public menus. Both report identical shared Goal UI bundle fingerprint
  fc08e714b25824df62c2de9aa52e98e811bfc76691223a160be12bf9a512d512.
- Cold evidence: public-commands-accepted-{coding,claw}-cold.log; artifact directory
  /data/t.Vy5SaQ/public-command-accepted-artifacts. Installed Claw native frontend
  was separately exercised through real /goals submission -> original-owner
  extension_control packet, no socket/model startup (not a mocked sender).
- Independent reviewer findings reproduced and fixed in one pass; no remaining
  findings. Source tests now use public Goal commands too. An initial cold consumer
  indentation error in an added test assertion was corrected before acceptance;
  both fresh installed probes were rerun successfully against final artifacts.
- Main checkout and all earlier uncommitted changes retained. No new Goal, worktree,
  dependencies, commit/push, version/tag, publish, actual provider or user-data
  migration. Model tool names, permissions and Goal business lifetime unchanged.
