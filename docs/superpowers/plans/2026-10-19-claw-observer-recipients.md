# Claw Observer Original Recipient Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans, native continuous execution; no additional approvals.

**Goal:** Observer reports use a host-captured original destination, never the current group recipient/broadcast.

**Architecture:** A small frozen Core ReplyReference carries only routing context and a live endpoint binding, not prompt/history/permissions. Channels explicitly declare whether that destination can survive restart (Weixin yes, Native connection no). The original input's tool context supplies the reference; observers persist it in their own journal and their existing report worker uses that explicit reference. Missing/offline original endpoints retain known-not-sent output; no different connection is adopted. This recipient milestone does not claim the later per-message report/outbox unification or Goal delivery complete.

**Tech Stack:** Python>=3.12/Linux, asyncio, existing Core input/output/channel adapters, ObserverJournal/LeasedSqlite and deterministic model/real Unix/Weixin request tests.

**Spec:** docs/superpowers/specs/2026-10-19-claw-assistant-core-design.md, sections5.3/8/11.

## Global Constraints

- Current checkout; no worktree, push/publish/merge, paid providers or user-service/data mutation.
- No message approval, second Agent/tool loop, TCP/broker or Goal/Eyes policy in ACP/Core controller.
- Reply metadata/source labels never grant authority or select a target from untrusted tool arguments.
- Observer storage format changes fail closed; no upgrade/dual read and no deleting old data.
- Observation stop, report stop and input execution cancellation remain independent.
- Reports retain original execution attempt identity; unknown sends are never automatically retried.

## Review Focus

1. Two Weixin peers in one group cannot redirect each other's scheduled/file reports after the mutable last-peer cache changes.
2. A receiver restart restores original Weixin account/context, never a newly connected TUI socket.
3. No creator destination means no implicit group report; a tool's extra reply/group fields cannot supply one.
4. Explicit source cancellation does not suppress already accepted reports; explicit notification cancellation still stops unsent reports.
5. Duplicate observer names cannot change recipients; changed scope/corrupt reference/unsupported journal fail without sending or changing original bytes.

### Task 1: Generic frozen recipient reference and observer persistence

**Files:** Create `core/outputs/targets.py`; modify `core/runtime/channel_adapter.py`, `core/observers/{models,journal}.py`; tests `test_reply_references.py`, existing journal tests.

**Interfaces:** `ReplyReference(scope,binding,original:UserInput)` freezes routing-only sender/channel/metadata (no input text/images/authority). Encode/decode versioned bounded JSON; `original` returns a detached projection. ChannelReplies captures a reference for a registered channel or restores a typed saved reference only via explicit accepts_saved_reply. ObserverRecord.reply optional reference; ObserverJournal formatv2 adds immutable reply JSON, includes it in duplicate-content validation, enforces matching group/workspace and exposes reply_for(receipt) for its actual owned outbox. No reference is not-required report delivery; begin/finish delivery can retain pending for a known-not-sent original endpoint.

- [ ] Write actual immutable codec/SQLite/reopen tests for captured metadata independent from original mutation, rejection of input text/images/authority fields, stable explicit recipient across duplicate name/restart and wrong scope. Test an unknown/old journal format is refused with original bytes unchanged.

```python
saved = await journal.create('main',root,'watch',definition,reply=original_reference)
assert (await journal.get('main',root,saved.id)).reply == original_reference
with pytest.raises(ValueError):
    await journal.create('main',root,'watch',definition,reply=other_reference)
```

- [ ] Run `uv run pytest packages/claw/tests/test_reply_references.py -q -n 0`; expected missing reference/signature RED.
- [ ] Implement bounded frozen reference/registry restoration and own-journal schema/validation. Keep existing transport/execution/report attempt fences. Update journal tests to supply explicit trusted references where they intentionally exercise delivery; do not add a default group target.
- [ ] Run all recipient/journal tests, Ruff/strict Claw mypy and root suite; expected green. Commit `feat(claw): persist immutable observer reply references`.

### Task 2: Actual input/tool capture and targeted Native/Weixin reports

**Files:** Modify `orchestrator.py`, `gateway.py`, `channels/tui/{channel,tui_app}.py`, `tool_eyes.py`, `observers/manager.py`; tests `test_observer_recipients.py` and existing Eyes integration fixtures/tests.

**Interfaces:** Actual route_message may receive trusted reply_reference from its channel/Native adapter; original input execution exposes it in host-only tool context and clears it on idle. The Eyes tool passes only this typed reference, not any JSON argument/provenance. Native report channels register a live binding and publish generic notification frames; disconnect unregisters that binding. Weixin references use original account/sender/context. deliver_report(reference,text,delivery_id) sends only that destination with stable chunk correlation IDs. Absent sender stays pending/not-sent; no target is not-required; an uncertain/partial send never becomes automatic retry. Status never exposes raw context tokens/bindings.

- [ ] Write actual model/tool/Weixin API request tests creating an observer from Alice, accepting Bob later, triggering scheduler/file evidence and proving the callback's report still uses Alice/context. Test actual Native socket creator versus another same-group viewer, receiver disappearance/restart and no destination fallback. Test forged reply arguments, duplicate name/different creator, source cancel versus notification cancel, and known-not-sent report recovery without another model turn.

```python
await alice_creates_observer()
await bob_speaks_in_same_group()
clock.advance(600)
assert (sent.to_user_id,sent.context_token)==('alice','original-context')
```

- [ ] Run new tests; expected current manager broadcasts via group recipient RED.
- [ ] Capture references in the actual single input execution/tool path; persist/dispatch only frozen references. Keep unknown output terminal, no report-driven model replay or source-label authorization. Let collection/execution and a pending offline recipient remain separate facts; do not pin collection solely on offline report delivery. Update deterministic report-sink fixtures to choose explicit destinations (not an application fallback).
- [ ] Run all Claw/Eyes/ACP/permission tests and root suite; Ruff/strict8 mypy, Flow examples, lock/diff and affected wheel/sdist/isolated consumers. Record evidence/limits in `docs/claw-observer-recipients-verification.md`; commit `fix(claw): route observer reports to their original recipients` and task-done.

## Follow-through

Unify feature report streaming/cancellation with generic per-message output receipts as part of shared Goal host notification ports; aggregate-only observer reports are not claimed as final IN-08 completion. Continue optional standalone Goal, two real host adapters, shared Goal extension UI and final legacy Goal/config removal; the overall47-item audit/fresh-context whole-branch review remains open.
