# Shared Goal Python extension and owner-bound views implementation plan

> Command naming update: the current display command is `/goals`; the skill is `/goal`.
> See `docs/extension-public-commands.md`. Qualified names below are historical/internal identities.

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:executing-plans inline, with TDD and a per-task ledger. The user authorized continuous implementation without further stage approvals. One fresh-context whole-branch review follows the complete objective, not each task.

**Goal:** Run the same packaged read-only Goal display extension in Coding and Claw through a small generic Agent view capability and the existing shared TUI renderer.

**Architecture:** Goal owns its read-only projection. Each actual host adapter grants that projection to an explicitly configured trusted extension under its real owner. Agent owns only bounded request transport, authorization, revocation and change hints; the Python extension turns snapshots into generic status/notice/command text. Existing `xdog.tui.components.extension_view.ExtensionView` already serves both hosts and must not gain Goal semantics.

**Tech Stack:** Python3.12 async, existing ExtensionRuntime/worker stdio requests and InvocationLease, frozen Goal data and leased store, existing bounded status/notice UI and namespaced extension commands.

**Spec:** `docs/superpowers/specs/2026-10-19-claw-assistant-core-design.md`, sections9–11; acceptance UI-01..05 and Goal owner/recovery gates.

## Global Constraints

- Current checkout/feature branch only; no worktree, paid provider, real user service/data changes, push, merge, publish or version bump.
- Goal package presence supplies capability and built-in skill; there is no Goal enable setting. UI execution still requires an explicitly trusted configured extension ID. Loading/installing a skill never delegates a task.
- Goal directly depends only on Agent/stdlib; Agent/TUI must not import Goal or know Goal schema. No second Agent/controller/tool loop, UI-owned service lifetime or arbitrary method/renderer protocol.
- Read-only views cannot execute Goal tools, change criteria, grant human authority or borrow permissions. Exact Coding session/branch versus stable Claw group remains unchanged.
- No old-data migration/dual-read compatibility. Existing headless tools/notifications work if display is absent, fails or unloads.
- No child lifetime/token/model-call/concurrency caps. Changed hints never poll models or create work intentions.
- All verification uses `TMPDIR=/data/t.Vy5SaQ`; root filesystem is nearly full. Do not remove user caches.

## Interface alignment with existing framework

Use configured extension ID and directory `goal_ui`, retaining the current1..32 ASCII letter/digit/underscore rule and manifest directory identity check. The design's illustrative `goal-ui` spelling is not a reason to widen that protocol. Query command is the existing namespaced `ext-goal_ui-goals`; `/goals` remains the packaged skill command, not an alias silently shadowed by a display plugin. Both spellings must be documented explicitly.

## Review Focus

1. Old process/owner replacement during an awaited snapshot: no late result or hint may reach a successor UI. Task1 barrier test, Task3 actual rotation test.
2. Child/another extension reusing the view name: same name or source labels cannot borrow the parent's grant. Task1 actual worker/client and Task3 child runtime tests.
3. UI unload/crash during a real worker completion: Goal commits/notices survive and no worker/source is implicitly cancelled. Task3 actual supervisor test.
4. Huge/non-finite/deep Goal display data and terminal control text: explicit bounded/unavailable/truncated display, never fake empty success or executable terminal output. Task1 codec and Task2/3 rendering tests.
5. Packaged extension discovery collision/shadowing and partial optional install: only explicitly configured trusted IDs execute; a failed display does not disable business. Task2 package tests and Task4 cold consumers.

### Task 1: Generic authorized read-only views through the actual extension worker

**Files:** Create `packages/agent/src/xdog/agent/extensions/views.py` and `packages/agent/tests/test_extension_views.py`; modify `extensions/{api,runtime,worker,dialogs,hooks}.py` and invocation context only as required by the actual existing request path. Keep view handling out of UI approval/dialog handlers.

**Interfaces:**

- `ViewRegistry.bind(extension_id: str, name: str, read: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]], *, valid: Callable[[], bool]) -> Callable[[], None]`: immutable per-binding identity; returned remover revokes only that binding. Registry belongs to one ExtensionRuntime, never a process-global locator.
- `await ViewRegistry.read(extension_id: str, name: str, query: dict[str, Any]) -> dict[str, Any] | None`: actual client identity supplies extension_id. Check same grant, validity, live client/owner before and after await; revoked/absent/unavailable returns None, not empty state. Preserve cancellation and settle admitted work.
- `await ExtensionAPI.read_view(name: str, *, query: dict[str, Any] | None = None) -> dict[str, Any] | None`: permitted in configured command/event callbacks without TUI/dialog permission; worker supplies only name/query, never owner/client/provider path.
- Generic outer request has exactly name/query, IDs use existing bounds, finite JSON query <=4096 UTF-8 bytes and response <=65536 bytes and nesting depth <=32; reject unknown outer owner/method/extension_id fields, oversized/non-finite data and malformed responses. Registry supplies no writes or arbitrary host calls.
- Add generic `view_changed` to the existing event contract with only view-name/revision hints. At most one queued hint per binding; use the runtime's actual owned loop/client, not a model/timer or newly constructed Agent. Re-read snapshot for truth.

- [ ] Write and run RED tests with the real registry and actual existing subprocess worker. Cover authorized headless read, missing grant, another ID/child, forged outer owner/method, NaN/oversize and revocation while provider is awaiting a barrier. No fake ExtensionRuntime counts as transport proof.

```python
entered, release = asyncio.Event(), asyncio.Event()
async def read(query):
    entered.set()
    await release.wait()
    return {"revision": "1", "items": ["private"]}
registry = ViewRegistry()
remove = registry.bind("display", "work", read, valid=lambda: True)
task = asyncio.create_task(registry.read("display", "work", {}))
await entered.wait()
remove()
release.set()
assert await task is None
assert await registry.read("other", "work", {}) is None
```

Run: `uv run pytest packages/agent/tests/test_extension_views.py -q -n 0`. Expected initial missing registry/API/protocol RED, then all cases pass.

- [ ] Implement the bounded registry and add one generic branch to the existing pending callback request/reply path. Capture real InvocationLease/client identity; validate current binding again after provider awaits. A view read must not invoke `dialog_handler`, open a UI, enqueue input or initialize another owner.
- [ ] Verify actual worker events/commands headlessly plus existing dialog/invocation/pending-message/hook suites. Run whole9/strict9/Ruff/Flow/lock/diff gates before marking this task done. Commit `feat(agent): expose owner-bound read-only extension views`.

### Task 2: Generic packaged extension lookup and the one Goal display implementation

**Files:** Create `packages/agent/src/xdog/agent/extensions/discovery.py`, `packages/agent/tests/test_packaged_extensions.py`, `packages/goal/src/xdog/goal/ui_views.py`, `packages/goal/src/xdog/goal/extensions/goal_ui/{extension.yaml,main.py}`, `packages/goal/tests/test_ui_views.py`; modify Agent extension configured-path construction only, Goal resources exports/docs as needed.

**Interfaces:**

- `extension_directory(name: str, root: Path) -> Path | None`: explicit configured local root/name takes existing precedence; otherwise inspect installed namespace package extensions/name resources, without a Goal-specific import/map or copying. Package presence alone must not initialize any extension worker. Duplicate installed candidates for the same selected ID fail clearly; invalid manifest identity is not a fallback to another implementation.
- `GoalView(service: GoalService).read(query: dict[str, Any]) -> Awaitable[dict[str, Any]]`: uses only existing owner-checked `service.list/status` reads. No reconcile, notice drain, acceptance, artifact generation or agent startup. Query permits only optional goal_id and strict offset/limit (default16,1..32); reject all write/owner/path/method arguments. Return deterministic revision plus preview-labelled bounded display data, continuation cursor and actual statuses.
- `goal_ui` main registers `goals` command and session_start/view_changed refresh callbacks through ExtensionAPI. It calls only `read_view("goals", query=...)`, formats generic status/notice text, and says unavailable on None/error rather than claiming zero Goals. It imports no TUI/host/store and exposes no business mutation commands.

- [ ] Write/run RED real discovery tests and real GoalStore-backed projection tests. Assert install-resource lookup with no worker startup; explicit directory/collision behavior; no new journal/history/notice writes after reads; unknown/write queries refused; previews/truncation and finite bounds are explicit.

```python
before = await service.store.history(goal.id)
view = GoalView(service)
snapshot = await view.read({"limit": 1})
assert snapshot["items"][0]["id"] == goal.id
assert await service.store.history(goal.id) == before
with pytest.raises(ValueError):
    await view.read({"action": "complete", "goal_id": goal.id})
```

Run: `uv run pytest packages/agent/tests/test_packaged_extensions.py packages/goal/tests/test_ui_views.py -q -n 0`. Expected missing lookup/projection/resources RED, then pass with real storage.

- [ ] Implement selected-resource lookup without changing existing identifier/manifest rules. Ship the single worker extension and read-only projection. Verify the extension in an actual Agent ExtensionRuntime/worker connected to Task1 registry, not merely source text/schema assertions. Error and unloaded states clear only its UI/subscription.
- [ ] Run whole9/strict9/Ruff/Flow/lock/diff; commit `feat(goal): ship a shared read-only Python display extension`.

### Task 3: Actual Coding/Claw bindings, changes and the existing generic TUI

**Files:** Modify `packages/coding/src/xdog/coding/core/goal_adapter.py` and `packages/claw/src/xdog/claw/core/runtime/goal_adapter.py`; add `packages/{coding,claw}/tests/test_goal_extension_views.py`, `packages/tui/tests/test_extension_view.py`. Only generic view forwarding changes may enter host extension/session/UI plumbing.

**Interfaces:** Each adapter registers GoalView.read only for explicitly configured `goal_ui` on the actual parent extension runtime. Binding validity fixes the exact actual extension process/current context plus Goal owner; no source label grants access. Claw refreshes the new parent view after safe context rotation without changing group-owned Goal; Coding unbinds exact old session/branch before successor access. Task1 registry validates before/after await. Goal change emits a coalesced generic hint; unloading UI removes its binding/rows/subscriptions but does not close Goal or cancel children/observers/notices.

- [ ] Write/run RED tests using actual current host factories, real extension worker, GoalStore/Core Inbox/ChildSupervisor and shared ExtensionView. Script only model transport. Assert same extension resource and generic renderer in both hosts, headless query, stale-owner response revocation, child/wrong-ID denial, actual worker completion with UI unloaded/crashed and unchanged independent business lifetime.

```python
# In each actual host fixture, install the same packaged goal_ui resource.
original_goal = await host.service.status(goal.id)
result = await session.extensions.command("ext-goal_ui-goals", "")
assert "unavailable" not in str(result).lower()
assert await host.service.status(goal.id) == original_goal
await session.extensions.manage("unload", "goal_ui")
assert await host.service.status(goal.id) == original_goal
```

Run each new host test file and generic renderer tests. Expected missing bound view/refresh RED, then both actual host paths pass.

- [ ] Implement bindings inside adapters, not ACP/TUI/Session Goal state. Fence and settle admitted read/change jobs before host store teardown. Reuse existing shared `ExtensionView`; test narrow/zero width, control characters and Unicode. No Goal schema/renderer/state or default extension auto-enable may enter TUI/Agent.
- [ ] Verify no extension event/read constructs a main Agent or calls a model; actual business notices still use original durable Inbox. Run whole9/strict9/Ruff/Flow/lock/diff and commit `feat(goal): bind the shared display to actual session owners`.

### Task 4: Cold same-extension artifacts and acceptance evidence

**Files:** Extend `packages/{coding,claw}/tests/goal_installed_consumer.py`, their tracked artifact verifiers; create `docs/shared-goal-extension-verification.md`; update architecture/current acceptance evidence and packaged docs. No final acceptance promotion for still-open observer output work.

- [ ] Add a cold real consumer scenario which cannot pass before the packaged extension/binding exists: with the same Goal wheel, query its actual worker in both hosts, render generic status, unload while a real child completes, and prove Goal/notifications remain intact. Check owner rotation and absent/unavailable display without fake empty status.
- [ ] Rebuild affected wheels/sdists, Twine and complete runtime/resource byte parity. Base Claw remains usable without Goal/Eyes/ACP; Goal-only remains free of hosts/TUI. Installing Goal supplies skill/capability but not automatic extension execution. Use no source-runtime injection, paid provider or user state.
- [ ] Run all nine unfiltered paths (document actual runner concurrency and retained timing limitations), strict9/Ruff/Flow17/lock/diff. Record exact command/results/source hashes; commit `test(goal): verify one installed display extension across both hosts`.

## Follow-through, not omitted scope

After this plan: finish observer per-assistant-message output/cancellation integration, remove inert queue/debounce configuration, audit every47 requirement and all named deliverables against current evidence, then one fresh-context whole-branch review and any evidence-backed fixes. This UI plan alone cannot complete the overall Goal. No push/merge/publish is authorized.
