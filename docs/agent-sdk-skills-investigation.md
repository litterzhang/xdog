# SDK skills: investigation and approved F1 WIP verification

> **Historical / superseded:** this record predates the current capability SDK.
> Old skill slugs, hosting APIs and activation examples below are not current
> integration instructions. Use `sdk-capability-assembly.md`, package READMEs and
> `eyes-thin-tool-design.md` for the supported contracts; canonical slugs are
> `flow`, `eyes`, `goal` and `plan`.


> 后续状态（2026-10-12）：服务运行时goal已完成当前Linux验收，包括跨进程skill恢复与关闭。本文保留此前调查及F1子任务记录；最终范围、结果与限制见 [运行时验收](agent-sdk-service-runtime-plan.md#10-最终验收2026-10-12)。
Updated **October 12, 2026**. The initial investigation is historical; **that checkout
implemented an opt-in read-only loader and request-local turn skills**.
This follow-up finishes scoped examples/docs/tests against that implementation.
It does **not** complete F1's cross-process restore requirement or the broader
snapshot/lifecycle gates in [the service-runtime plan](agent-sdk-service-runtime-plan.md).
Agent lifecycle integration, shutdown, snapshots, and product adaptation are owned
by the parent task, not this change set.

## Current syntax note (2026-10-24)

The illustrative Python blocks below use current SkillRepo registration syntax;
historical counts/hashes remain evidence of the earlier checkout, not verification
of the new API. Default discovery does not inject bodies. Read instructions through
the read-only skill list/load entry; explicit host activation is separate.
See [the current SDK guide](sdk-capability-assembly.md) for additive repository
composition and select-before-open capability lifetime.

## Two distinct SDK operations

### Host-selected activation

```python
from pathlib import Path
from xdog.agent import Agent, AgentConfig
from xdog.agent.skills import SkillRepo

skills = SkillRepo().register(directory=Path("./skills"), name="host")
agent = Agent(
    stream_fn,  # host-configured adapter, or the example's offline adapter
    config=AgentConfig(model="your-provider/model", system_prompt="Base instructions"),
    skills=skills,
    active_skills=["flow"],
    tools=[],  # activation grants NO execution capabilities
)
```

The host supplies trusted roots and selected slugs. `skills=None` means no SDK
skill discovery/activation. Supplying `active_skills` alone does not install a
package. Current explicit activation validates slugs and rejects missing/empty
bodies instead of claiming they are active.
`set_active_skills()` replaces the idle selection; it does not start a request.

### Model-requested optional list/load

```python
from xdog.agent.skills import create_skill_tool

reader = create_skill_tool(
    skills,
    allowed_slugs=["flow"],
    max_skill_bytes=256 * 1024,
    max_listed_skills=1,
)
agent = Agent(
    stream_fn,
    config=AgentConfig(model="your-provider/model"),
    tools=[reader],
    before_tool_call=host_permission_hook,
)
```

Registering `skill` is explicit. `list` returns metadata without bodies; `load`
returns rendered instructions as an ordinary tool result. **Neither operation
activates a skill, edits the system prefix, installs anything, executes commands,
reads referenced files, or grants tools/permissions named in the document.**
`scope: turn` in a loaded document does not give its tool result automatic expiry:
normal tool-result history remains subject to ordinary host history management.
Use host activation when request-local/session lifetime is required.

The runnable example supplies the permission hook and permits only this read-only
capability. Generic SDK tools are not a permission boundary: host applications
must decide which calls to approve. Do not add Claw's writable skill tool or bash
merely to read a skill. Snapshot contents must never choose executable modules,
install packages, configure loader roots, or expand permissions; those remain
explicit trusted host configuration. This follow-up adds no snapshot loading code.

**Allowlist visibility:** `allowed_slugs` filters this tool only. Passing
`skills=skills` to Agent also builds the repo's independent discovery index.
The model-loading example deliberately omits that argument so the bounded tool
catalog is the sole skill-discovery surface. To use both, the host must deliberately
select the desired index and roots; do not mistake a loader allowlist for index
redaction or a security sandbox.

## Runnable offline examples

Full source: [load_skills.py](../packages/agent/examples/load_skills.py). All modes
use deterministic stream adapters, capture actual model-entry contexts, and make
no provider/auth/network requests. Run from the root using the existing environment:

```sh
# Activate the real packaged session skill; explicitly unload before request 2.
UV_OFFLINE=1 uv run --no-sync python packages/agent/examples/load_skills.py

# Temporary local turn skill; request 2 must NOT contain its body, without unloading.
UV_OFFLINE=1 uv run --no-sync python packages/agent/examples/load_skills.py --demo-turn

# Offline model calls list → load → replies, through the real tool loop + host hook.
UV_OFFLINE=1 uv run --no-sync python packages/agent/examples/load_skills.py --mode model-load

# Loading turn-scoped metadata is still only a tool result, not activation.
UV_OFFLINE=1 uv run --no-sync python packages/agent/examples/load_skills.py --demo-turn --mode model-load
```

The current default modes require `xdog-flow` **already installed**. The canonical
slug is **`flow`**, not `xdog-flow` (distribution/CLI). Historical artifact records
used the retired `flow-workflows` slug. The demo fixture only writes
inside its temporary directory. Neither mode executes the commands taught by the
flow skill; successful context assembly does not demonstrate LLM quality or actual
workflow execution.

An application-owned local skill can be used in either mode:

```text
my-skills/
  review-procedure/
    SKILL.md
```

```markdown
---
name: review-procedure
description: Review a change before accepting it.
metadata:
  scope: turn
---
Explain expected behavior, risks, and a focused test.
```

```sh
UV_OFFLINE=1 uv run --no-sync python packages/agent/examples/load_skills.py \
  --skills-dir ./my-skills --skill review-procedure --no-packaged
```

Omit `metadata.scope` for a session skill. Add `--mode model-load` to exercise
optional tool reading instead of activation. `--no-packaged` disables namespace
discovery, not file-content trust requirements. No user skill directory needs to be
populated from site-packages. A standalone application supplies its existing SDK
and, if wanted, flow installation; this task performs no installs or builds.

## Current placement and lifetime contract

- Available metadata comes first in the system prompt, followed by active session
  bodies and the base instructions. Explicit `SystemPromptBlock` cache flags are
  preserved. Session bodies stay active until host replacement/unload.
- The index no longer promises an unregistered `skill` tool. A SkillRepo alone adds
  no tool to the agent. Both public `create_skill_tool` import paths already exist
  in the WIP; this follow-up does not edit exports.
- Constructor and idle `set_active_skills()` activation capture turn-skill content.
  Turn bodies are projected into request messages, not canonical history or the
  cacheable system prefix. A file edit after activation does not silently change
  the captured body; explicit reactivation loads the new content.
- A turn skill belongs to one admitted public prompt run, including subsequent tool
  calls and follow-up continuation in that run. Rebuilding the base prompt does
  not move that body into the prefix.
- Consumption occurs at actual stream-adapter entry, after context conversion and
  image validation. Handled/rejected input, failed context transforms, pre-model
  cancellation and rejected image contexts do not consume the pending skill.
- Once used, completion, provider error or cancellation retires it before the next
  independent prompt. Idle reactivation can explicitly re-arm it. Busy selection
  mutation is refused. `continue_()` is not implicit reactivation; when history
  ends in an assistant message it requires queued input under the existing API.
- Cleanup does not remove arbitrary user messages whose text resembles a skill
  heading. Same-process clear/restore tests here are regression checks on owned
  transient projection, **not proof of versioned cross-process skill restoration**.

Sources: `agent.py::_place_skills`, `set_active_skills`, `_run_loop`;
`agent_loop.py::_stream_assistant_response`; `skills/render.py`.
Those Agent implementation files are not changed by this follow-up.

## Read-only loader review

`skills/tool.py` uses application-selected roots captured when the tool is created.
It retains **group > shared > packaged** precedence. Recreate the tool to change
roots; same-name unsafe higher tiers must not silently read lower-tier content.

- Slugs must be 1–64 lowercase letters/digits/hyphens, without leading/trailing or
  doubled hyphens. Full-string validation now also rejects trailing newlines.
  Traversal, arbitrary paths, unknown/disabled names and write actions are refused.
- `allowed_slugs=[]` denies all; omission allows the configured catalog. The
  allowlist accepts at most 512 names. No snapshot supplies the allowlist.
- Default file limit is 256 KiB (configurable 1 byte–1 MiB); listing defaults to 128
  entries (1–512). Local scans stop after 4096 entries across the configured roots.
  List strings are capped; output is capped at `max(128 KiB, max_skill_bytes + 8192)`
  UTF-8 bytes, including rendered path substitution. Oversized results are rejected,
  not silently truncated instruction bodies.
- On the tested Linux path, directory-relative opens use no-follow/nonblocking
  flags. Files must be regular, single-link and within the byte limit; file and
  child-directory symlinks, hardlinks, FIFOs and unsafe shadows are refused.
  Missing tiers can fall back. Invalid UTF-8/oversized shadows cannot.
- Blocking reads run in an owned worker thread. Pre-cancellation starts no reader;
  task/event/repeated cancellation signals cooperative stop and waits for that
  original reader to settle rather than orphaning background work. **This can wait
  on a stuck filesystem/native call**; Python cannot hard-kill the thread. This is
  not a universal wall-clock cancellation bound or F7 shutdown acceptance.
- Skill text and installed Python packages remain trusted input. Bounded file I/O
  is not an adversarial YAML/text parser sandbox or an OS filesystem sandbox. The
  portable fallback uses path/link/identity checks; native Windows/macOS and hostile
  ancestor-directory replacement races are not validated here.

The bounds above belong to the **model-facing tool**, not every `SkillRepo`
API. Host discovery/activation still uses the repo's ordinary synchronous reads;
listing parses bodies internally and then omits them from returned metadata. Do not
use these trusted-host APIs as a bounded arbitrary-path interface for model input.
No new runtime dependency, package discovery scheme or command executor was added.

## Discovery and packaged flow evidence

`packaged_skills()` enumerates installed `xdog.*` namespace packages and looks for
`skills/<slug>/SKILL.md`; it is not a general third-party entry-point scheme.
Current API: `SkillRepo(builtin=True, packaged=None)` discovers installed packaged
skills by default. `SkillRepo(builtin=False)` disables default discovery; an
explicit mapping pins packaged sources. Register local roots with
`.register(directory=path, name="host")`. Directory-name and different same-slug
definition conflicts fail; there is no implicit override priority. Read-time
construction does not create directories. `from_packaged`, `select` and `merge`
are SDK operations. The historical artifact evidence below predates this API.

`save_skill(scope="shared"|"group")` chooses a storage directory, not prompt
lifetime. Lifetime is the SKILL.md `metadata.scope`. Parsing uses the existing
PyYAML dependency, not only the standard library. Rendering states the skill root
and expands `${SKILL_DIR}`/`${CLAUDE_SKILL_DIR}` without reading references.

The historical measured flow resource was
`packages/flow/src/xdog/flow/skills/flow-workflows/`, with SKILL.md and 12 examples.
The current canonical skill slug and directory are `flow`; the measured hashes
below are not a current artifact certification. Keep dependency direction **flow → agent**;
agent does not need flow installed to load local skills. Normal unpacked wheels
work; ZIP/non-filesystem resource layouts remain unverified.

### Installed artifacts: baseline only, deliberately not rebuilt

The pre-existing `/tmp/xdog-sdk-skills/{agent-only,with-flow}` installed targets were
re-probed, sequentially, outside the checkout using `python -I -S`. XDOG imports
and distribution metadata must originate inside the selected target. Only a clean
third-party dependency directory is added (no XDOG modules/metadata, `.pth` files or
editable hooks). The targets reuse existing dependencies, not fresh resolution.

Both baseline scenarios pass: local session activation without flow, and real
packaged flow session activation/unload with flow. The flow resource's 13 hashes
still match the historical checkout/sdist/wheel evidence. **Both targets predate
`create_skill_tool` and report `readonly_loader_present: false`.** They therefore
prove baseline packaging only, not installed-artifact F1 loader/turn behavior.
Current loader/turn coverage runs against the checkout; new builds are left to the
parent's coordinated artifact gate. No target was rebuilt or installed here.

Probe source: [skill_artifact_probe.py](../packages/agent/tests/fixtures/skill_artifact_probe.py).
Its JSON explicitly names that limited verification scope and keeps the broader
gate false.

## Verification, coverage, and handoff

Exact commands, return codes, logs, source hashes, example output and installed
probe results are in [agent-sdk-skills-evidence.json](agent-sdk-skills-evidence.json).
Final focused tests: **112 passed**, no failures/skips:

```sh
UV_OFFLINE=1 .venv/bin/python -m pytest -q \
  packages/agent/tests/test_skill_tool.py \
  packages/agent/tests/test_skill_lifecycle.py \
  packages/agent/tests/test_skill_sdk_usage.py \
  packages/agent/tests/test_skills.py packages/flow/tests/test_skill.py
```

Coverage includes the real model list/load roundtrip and permission denial; local
session/turn and actual packaged flow examples; bounds, path refusals, invalid
UTF-8, no lower-tier fallback on unsafe content, no reference/command execution;
reader task/event/pre/repeated cancellation; provider-error retirement;
pre-model input/context cancellation; image capability/base64 validation; captured
turn bodies across file edits and tool iterations; same-run follow-ups, busy
activation, prefix stability and preservation of ordinary user history.

Scoped Ruff and strict mypy pass. Mypy separately checks `xdog.agent.skills` and
three typed example/probe/test files with `--follow-imports=silent`; it is not the
parent's full strict workspace gate. An initial combined path-based mypy invocation
hit duplicate namespace-module detection; separate module/path invocations correct
that invocation, rather than hiding a source error. The requested candidate-tuple
annotation in `tool.py` is fixed.

The first new provider-error test tried `continue_()` directly after an assistant
error message, which the existing API rejects. It now verifies that rejection,
queues an explicit follow-up, then checks no skill revival on continuation or the
next prompt. This was a test assumption correction, not an Agent implementation
fix. No remaining failure was observed in the focused gate.

Remaining parent-owned verification: coordinated fresh artifacts, versioned skill
state/content identity in cross-process dump/restore, full lifecycle/close gates,
Coding/Claw projection/expiry compatibility, and broader regressions. This document
does not assert those gates complete. No live provider, network, install, service
operation, git command, or full-workspace rebuild was used in this follow-up.

## Historical investigation (superseded implementation claims)

The earlier October 12 investigation completed SK-001 through SK-006 as research:
public API/defaults, placement/expiry observations, installed packaging, an offline
session example, minimal recommendations, and scoped evidence. It recorded 48
baseline tests and then 52 tests with the original example. At that time it correctly
reproduced an advertised-but-absent loader, dropped dynamic turn activation and
constructor turn bodies leaking into the next prompt. Those are **historical
observations, not descriptions of today's checkout**. Priority 1 (read-only loader)
and the core Priority 2 turn projection now have WIP implementation and the scoped
coverage above. Cross-process and product integration remain separate acceptance.

The original evidence is preserved verbatim as `historical_investigation` in the
JSON, including artifact/source hashes and old observations. Its `complete` research
criteria and `runtime_modified: false` apply only to that initial investigation,
not to F1 or the current service-runtime plan. Earlier build/install commands are
historical records, not operations performed by this follow-up.
