> **历史归档，不是当前接口、待办或执行授权。**
> 原日期、结论和验证范围保留；当前入口见 [文档索引](../../README.md)。

# Confining a script node — the one thing `--confined` refuses

Status: **verified prototype, not implemented** · Related:
[`workspace-confinement.md`](workspace-confinement.md), which says this is out
of scope, and is wrong about why

## The question

`--confined` **used to refuse** a workflow containing an inline `code` script
node, because `exec(node.code, namespace)` ran unrestricted Python in the
executor's own process and met no path check. That has since shipped: an audit
hook now bounds script nodes in both engines (see the flow README). This document
is what remains — the part a hook structurally cannot do. That refusal is honest, but it is
also the biggest hole in the feature: script nodes are the ordinary way to do
non-LLM work, and telling someone their workflow cannot be confined because it
parses a CSV is a poor answer.

So: can a script node be confined?

**Yes, and it is far cheaper than the existing doc claims — because that doc
priced the wrong thing.**

## Why the existing estimate does not apply

`workspace-confinement.md` puts OS enforcement at two to three weeks, and the
blocker it names is real:

> confining a run means putting it in a subprocess, and
> `execute(stream_fn_factory=...)` takes a **Python callable**. A callable does
> not cross a process boundary, so the child would have to construct its own
> provider from configuration — which means credentials cross the boundary,
> events and results need IPC…

All true. But that is the cost of confining **an agent node**, which needs a live
provider, a streaming connection, and an event sink. A script node needs none of
those. Its contract today is already:

```python
ctx = RuntimeContext(step=step, node_id=node_id, workflow_name=wf.name)
kwargs = {p.name: to_python(ins.get(p.name, ""), p.type) for p in node.input_ports}
raw = fn(ctx, **kwargs)
```

`RuntimeContext` is three scalars — `step: int`, `node_id: str`,
`workflow_name: str`. The kwargs are port values, which are JSON by construction.
The return is coerced back into a port dict, also JSON. **A script node is
already a pure JSON → JSON function.** Nothing about it resists a process
boundary, because nothing live crosses one.

That is the whole finding. The expensive part of sandboxing flow is the part
script nodes do not have.

## What was actually verified

On this machine — Linux 6.12, Landlock ABI v6, `bwrap` present:

| Probe | Result |
|---|---|
| write inside the workspace | allowed |
| write `/tmp/ESCAPE.txt` | `PermissionError` |
| write `~/.ssh/x` | `PermissionError` |
| read `/etc/hostname` | allowed — **imports still work**, which is what makes this usable |
| `subprocess.run(["/bin/sh", "-c", "echo > /tmp/x"])` | blocked; the child inherits the ruleset |
| call `landlock_restrict_self` again with `/` | still denied — rulesets **intersect**, they never widen |

The read/write asymmetry is the practical shape: *read the world, write only the
workspace*. A Python child that cannot read `/usr/lib` cannot import anything, so
a total-isolation policy is not the goal and would not work.

~60 lines of `ctypes` — three syscalls (444/445/446) plus `prctl(PR_SET_NO_NEW_PRIVS)`.
No root, no daemon, no extra dependency.

### Cost

**27 ms per script node** (interpreter spawn + JSON round trip; `-S` saves ~2 ms).
Against an LLM call measured in seconds, this does not register. It matters only
for a fan-out of hundreds of trivial script nodes, which is not what they are for.

## The portable tier: audit hooks, not a patched file API

The obvious cross-platform move is to inject Python's file API — replace
`builtins.open`, `io.open`, `os.open` — and check paths there. **That is strictly
weaker than what CPython already gives you**, and measurably so. The same four
escapes, against each mechanism:

| Escape | patched `io.open`/`os.open` | `sys.addaudithook` |
|---|---|---|
| `open(path, "w")` | blocked | blocked |
| `os.open(path, O_CREAT)` | blocked | blocked |
| `sqlite3.connect(path)` | **wrote the file** | blocked (`sqlite3.connect`) |
| `ctypes.CDLL("libc.so.6").fopen(...)` | **wrote the file** | blocked (`ctypes.dlopen`) |
| files created anyway | `a.db`, `d.txt` | none |

Patching only sees calls that go *through Python*. `sqlite3` opens its file in C
and never touches `os.open`; `ctypes` goes straight to libc. Audit events are
raised from inside CPython's own C implementation, so they fire on both.

Two more properties that matter for a boundary, both verified:

- **The hook cannot be removed.** There is no `sys.removeaudithook`.
- **It cannot be neutered from Python.** Reassigning `sys.audit` succeeds and
  changes nothing — the C layer calls `PySys_Audit` directly. A patched
  `io.open`, by contrast, is one `importlib.reload(io)` from gone.

And audit hooks are **already cross-platform** — Windows, macOS, Linux — which is
the property the patched-API idea was reaching for.

### Correcting the earlier draft of this document

An earlier version of this file reported that `os.remove` "fired no audit event"
and treated that as a hole in CPython. That was wrong, and wrong in the direction
that flatters the person writing it: the event exists. Every path-mutating call
raises one:

```
os.remove -> ['os.remove']      os.mkdir    -> ['os.mkdir']
os.rename -> ['os.rename']      os.truncate -> ['os.truncate']
os.rmdir  -> ['os.rmdir']       os.symlink  -> ['os.symlink']
open(w)   -> ['open']           os.chmod    -> ['os.chmod']
```

The file got deleted because *my hook did not handle that event*, not because
CPython failed to raise it. The lesson survives — a hook is an enumeration you
have to complete — but it is a much smaller lesson than "there are holes in the
mechanism", and the mechanism deserves the correction.

### The hole that does remain

Coverage of C libraries is by **explicit annotation, not interception**.
`sqlite3.connect` is caught because someone added that event to CPython by hand —
note it raises `sqlite3.connect`, not `open`. A third-party extension that writes
via libc from its own already-linked C code raises nothing, needs no `dlopen`, and
is invisible. That is structural and no amount of care in the hook fixes it.

So: audit hooks are a real containment boundary against Python-level code,
portable, and unremovable — and still not an OS boundary. Landlock is. They
compose: same policy, two enforcement layers, in the same child process.

## What this would change

| Today | With this |
|---|---|
| inline `code` → `--confined` refuses | runs in a Landlocked child; **no longer a refusal reason** |
| `bash` tool → refuses | plausibly the same treatment, since the shell inherits the ruleset |
| CLI backend → refuses | spawn it under the same ruleset |
| `script` with `run: mod:fn` → allowed but *unconfined* | actually confined |

The last row matters most and is easy to miss: `run:` references are permitted
under `--confined` today on the argument that they are reviewed code. That is a
trust assumption, not an enforcement — the file could be anything. Confining
script nodes turns the one currently-allowed script form from trusted into
bounded.

## Limits, stated plainly

- **Two tiers, two platform stories, and the difference must be visible.** The
  audit-hook tier runs everywhere. The Landlock tier is Linux-only — macOS
  `sandbox_init` is deprecated, Windows has no equivalent — so a confined run on
  macOS gets containment against Python-level code and nothing against a C
  extension. That is a *weaker guarantee under the same flag*, which is the
  failure mode this whole feature was built to avoid. Either `--confined` reports
  which tiers are active, or it refuses where it cannot reach the OS. Silently
  meaning different things on different machines is not an option.
- **Kernel 5.13+**, and per-ABI feature gaps (`TRUNCATE` needs ABI 3, network
  rules ABI 4). Probe at startup, refuse when absent; never silently downgrade,
  because a run that quietly stops enforcing is the failure this whole feature
  exists to prevent.
- **An injected `script_resolver` cannot cross.** `execute(script_resolver=...)`
  hands back a live callable, which is the `stream_fn` problem one level down. A
  confined run must re-resolve `run: module:callable` by import in the child, and
  refuse when a resolver was injected.
- **Not free of blast radius.** The child can still read everything the user can.
  A workflow that exfiltrates rather than destroys is unaffected — that needs the
  network rules (ABI 4+), which is a separate decision.

## Effort

| Step | Work | Est. |
|---|---|---|
| 0. Audit-hook policy (portable) | one hook handling the path-mutating event table above, scoped to the child; works on Windows/macOS/Linux | 1–2d |
| 1. `landlock.py` in `xdog-agent` | the ~60 lines above, plus ABI probe and a clean "unavailable" path | 1d |
| 2. Script-node child runner | serialize `(code or run, ctx, kwargs)` → child → JSON back; map a non-zero exit onto the existing `WorkflowExecutionError` | 2d |
| 3. Wire into `--confined` | drop inline `code` from `unconfinable_reasons`, add the platform/kernel refusal, mirror in codegen — **the recurring tax on anything touching execution** | 1–2d |
| 4. Tests | the probe table above becomes the suite; plus a codegen parity test, since this is exactly where the two engines drifted last time | 1d |

**~6–7 days**, against the 2–3 weeks the existing doc estimates for confining a
run. The difference is entirely that script nodes never needed the provider.

## Recommendation

Build it when a workflow that needs a script node also needs to be confined —
which is the [tasks service](tasks-service.md)'s first real requirement, and
does not exist yet. Until then the refusal is the correct behaviour, because it
is the honest one.

If it is built: **step 0 and 2 first** — the audit hook plus the child process
give a portable boundary against Python-level code, which is the realistic
failure. Landlock (step 1) then layers underneath on Linux for the C-extension
hole that a hook structurally cannot see. Do
not add a second flag, do not add a "partial" mode, and do not ship the audit-hook
tier as though it were the boundary. One flag that either confines or refuses is
the property worth keeping.
