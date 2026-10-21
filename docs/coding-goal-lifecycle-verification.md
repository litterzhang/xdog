> **历史快照（2026-10-20）**：本文记录此前冻结实现，涉及 Goal worker/reviewer/attempt
> 的流程已由主 Agent + task files + ACP 提醒设计替代。不要据此推断当前功能或验收状态；
> 当前入口见 `goal-simple-design.md` 与 `goal-simple-requirements.json`。原始证据保留不改写。

# Coding optional Goal lifecycle — actual host and wheel verification

> Historical lifecycle milestone at736705c. The user subsequently removed the
> Goal enable setting: installed package presence now supplies the capability
> automatically in both hosts. See `docs/coding-goals.md` and
> `docs/claw-goal-cutover-verification.md` for the current boundary and evidence.
> Counts below remain the original milestone snapshot, not final branch results.

Date: 2026-10-19. Coding adoption milestone, not whole assistant-core completion.

## Delivered

- `xdog-coding` declares the optional `goals` extra. Strict global-only
  `goals_enabled=False` is lazy: import, constructing an idle session and project/
  session settings cannot activate Goal storage, child work or a provider.
  Explicit malformed values are rejected, never coerced or silently defaulted.
- The existing sole session controller owns initialization and callback lifetime.
  For enabled SDK sessions its supported private thread/loop remains available
  across individual awaits; no second controller/Agent/tool loop/CLI/daemon is
  introduced. Retention depends on an opened capability, not unfinished Goal state.
- Actual child execution, Goal commits and retired capability resources settle
  before loop/lease release. Shutdown cancels/joins actual session children; Goal
  itself does not implicitly cancel workers because of status, notification stop
  or UI visibility. Restart records interruption and never replays unknown work.
- Idle navigation is allowed with unfinished stored Goals. Exact old owner data
  stays in its own journal; stale tools are removed and old capability storage is
  closed before successor intake. The new owner does not read old Goal state.
- User authority follows actual prompt objects through input transformation and
  consumption, cleared at turn boundaries. Existing canonical inbox messages,
  source text and provenance cannot grant criteria/pause-release authorization.
- Worker/context report capabilities retain actual object-identity checks; other
  tool calls still pass normal Coding permission/readonly/extension policy.
  Notifications always use the existing durable automatically consumed Inbox,
  including headless/offline-display operation, with no message approval.

## Evidence

- Coding adapter11 + lifecycle12: **23 passed**. Includes actual SDK owner/thread,
  busy child callback, headless queued input, global-only strict flag, original
  owner navigation, shutdown/join/reopen-no-replay and human vs queued tool-hook
  authorization. Existing child suite plus initial integration40 also passed.
- Final full9: **6995 passed,34 skipped**,151.01s; Coding1250/12 skipped,
  Claw1230. `/data/t.Vy5SaQ/coding-goal-lifecycle-final-root9.log`.
- Strict9 mypy593 files, Ruff packages and both artifact verifiers, Flow17,
  lock/diff checks passed.
- AI/Agent/Goal/TUI/Coding/Claw6 wheels+6 sdists rebuilt, all12 Twine pass.
  Runtime/resource source-byte parity53/122/17/89/98/102 members; py.typed and
  packaged Goal skills/roles present. Goal directly requires Agent only; Coding's
  Goal dependency is conditional on its `goals` extra.
- Fresh wheel-only `/data/t.Vy5SaQ/coding-goal-installed-a7stdpf0` first executes
  a real ordinary Coding SDK turn **without Goal installed or imported**. It then
  installs only the Goal wheel and executes the real optional lifecycle: actual
  Agent/ChildSupervisor/file IO, worker report, explicit acceptance, independent
  readonly reviewer, completed Goal, three durable notices automatically consumed.
  **Worker4 + reviewer4 + parent2 scripted model calls**, default ask; attempted
  reviewer write blocked. No Claw/Eyes/Flow/site installed/imported, no TUI,
  receiver, real provider, user credentials or user service started.
  `/data/t.Vy5SaQ/coding-goal-installed-final.log`.
- Same rebuilt Goal-only artifacts pass actual independent SDK lifecycle in
  `/data/t.Vy5SaQ/goal-only-installed-bc4idivs`: two distinct Agents/five scripted
  calls, no installed/imported hosts/TUI/Eyes/Flow/site.

## Observed verification limitation

The initial full9 run, concurrent with artifact builds/installs, hit the existing
TUI `test_async_results_are_polled_not_applied_by_worker` three-second path-index
settlement timeout (685 TUI passed/one failed). No TUI source was changed or test
removed/relaxed: that exact case passed in isolation0.15s and full TUI686 passed
in13s; the final fresh full9 above also passed. This records an observed timing
limitation, **not a claim that a TUI production bug was diagnosed or fixed**.
Initial log: `/data/t.Vy5SaQ/coding-goal-lifecycle-root9.log`.

## Remaining architecture gates

Actual Claw adoption and deletion of its active legacy GoalManager/Tracker,
hardcoded prompt summary/implicit turn-end reviewer and Goal-driven cleanup;
generic owner-bound readonly extension views and the same Goal Python UI
extension; native Goal UI removal; observer per-assistant-message output/cancel
unification; final47-item audit, true two-host installed lifecycle and one
fresh-context whole-branch review. The overall goal is still active. No push,
merge, publish or user data/service changes were performed.
