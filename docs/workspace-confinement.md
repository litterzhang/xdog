# Workspace confinement — a workflow gets a directory, not a filesystem

Status: **idea, not designed** · Audience: whoever picks this up · Related:
[`tasks-service.md`](./tasks-service.md), which is why this matters now

## The idea

A workflow runs inside a **workspace** and cannot read or write outside it.

The default workspace is `runtime/` beneath the workflow file's directory — or
beneath the bundle directory for a `--portable` build. A run may be granted extra
readable or writable directories.

**The grant is an executor action, never part of the workflow definition.**

## Why that last sentence is the whole design

If `workflow.json` could declare its own access, the thing being confined would
be declaring its own confinement, which is not confinement. Three concrete
consequences:

- **An AI writes these.** The [tasks service](./tasks-service.md) has an agent
  authoring workflows, and the `flow` skill ships in the wheel so any
  coding agent can. A generated workflow that can request `/` is a generated
  workflow that has `/`. The grant has to come from whoever decided to run it.
- **A workflow is a shared artifact.** It goes in Git, it gets bundled, someone
  sends you one. Its declared paths would be *their* machine's paths, and
  accepting them means accepting a stranger's idea of what your filesystem looks
  like.
- **The reviewer is looking at the wrong file.** If access lives in the workflow,
  reviewing access means reviewing every workflow. If it lives at the call site,
  there is one place to look — and it is the place where the person with the
  authority to grant it actually is.

So:

```bash
xdog-flow run report.json                          # runtime/ only
xdog-flow run report.json --allow-read ~/data      # plus one readable tree
xdog-flow run report.json --allow-write ./out      # plus one writable tree
```

The workflow file is identical in all three. Nothing in it can tell which one
happened, which is the point.

---

## Recommendation, and what it costs

**Take the cooperative path, and make it refuse what it cannot confine.** Not the
OS sandbox, not yet.

The reason is not that enforcement is unimportant — it is that cooperative
confinement can be made to give a *real* guarantee for a *subset* of workflows,
and that subset is checkable before anything runs. flow already knows every
node's kind at validate time. So:

```bash
xdog-flow run report.json --confined --allow-read ~/data
```

`--confined` refuses to run a workflow that contains anything unconfinable:

| Refused | Why |
|---|---|
> **Update.** The claim below that script nodes cannot be confined is correct
> *in-process* but wrong as a conclusion. A script node's contract is already a
> pure JSON → JSON function, so it can be moved into a Landlocked child without
> the provider/credentials problem that makes confining an *agent* node
> expensive. Verified prototype and a ~5-day estimate in
> [`script-node-confinement.md`](./script-node-confinement.md).

| a `script` node with inline `code` | `executor.py:129` is `exec(node.code, namespace)`, carrying the comment *"inline workflow code, trusted author"*. That comment **is** the current security model, and it stops being true the moment an AI writes the workflow. |
| the `bash` tool | a shell is a general-purpose escape; there is no cooperative version of confining it |
| a CLI backend | the subprocess owns its own filesystem access |

What is left — agent nodes with the filesystem tool, and `script` nodes using
`run: module:callable` pointing at reviewed code — **is** fully confined by
cooperative checks, because every filesystem access in that subset goes through a
tool that can check. That is a guarantee worth stating, and it is narrow enough
to state honestly.

The refusal is what makes it honest. Without it, `--confined` on a workflow with
an inline script would run and confine nothing, which is worse than not having
the flag.

### Effort

**Cooperative + refusal: about four days.**

| Step | Work | Est. |
|---|---|---|
| 1. Workspace exists | `execute(workspace=...)` — the plumbing already has the same shape in `base_dir` (`executor.py:196`), defaulting to `<workflow dir>/runtime`; thread it to the tool factories; **mirror it in codegen**, which is the recurring tax on anything touching execution | 1d |
| 2. Cooperative confinement | `validate_path` gains an allowlist mode; the filesystem tool honours it. Note this lives in `xdog-agent`, so `xdog-coding` and `xdog-claw` are downstream — they must keep working unconfined by default, which is the current behaviour and therefore free | 2d |
| 3. `--confined` + grants | a `confinable` check in the loader (reuses the per-node validation it already does), `--allow-read` / `--allow-write`, and the grant recorded in the scheduling unit — since a workflow installed as a timer still needs its grant somewhere that is not the workflow file | 1d |

**OS enforcement: two to three weeks, and it changes a core API.** The blocker is
not Landlock itself (three syscalls via `ctypes`, two or three days). It is that
confining a run means putting it in a subprocess, and
`execute(stream_fn_factory=...)` takes a **Python callable**. A callable does not
cross a process boundary, so the child would have to construct its own provider
from configuration — which means credentials cross the boundary, events and
results need IPC, and every existing caller and test that passes a stub
`stream_fn` needs another path. That is the real cost, not the syscalls.

It is also Linux-only in practice: macOS `sandbox_init` is deprecated and Windows
has no close equivalent. Acceptable for a task service running on a server;
a significant asterisk for a local-first CLI.

**So: build steps 1–3 when something needs them. Build the sandbox only if
AI-authored workflows are going to run unattended** — which is exactly the
[tasks service](./tasks-service.md)'s second position, and is the strongest
argument for its first position instead.

---

## The honest part: what can actually be enforced

This is where the idea either becomes real or becomes theatre, so it goes near
the top rather than in a caveats section.

**A `script` node runs arbitrary Python in the executor's own process.** It gets
`code` inline or `run: module:callable`, and either way `open("/etc/passwd")`
works, `import shutil` works, `os.system` works. No path-checking helper prevents
that, because the script never calls the helper.

So there are two different things being asked for, and conflating them is how
this ships as a false promise:

| | Stops | Does not stop |
|---|---|---|
| **Cooperative** — tools and helpers validate paths | mistakes, and an agent that wanders | a script node, deliberate or generated |
| **Enforced** — the run is confined by the OS | both | nothing much |

Cooperative is worth building and is most of the value: an agent node that
wanders into `~/.ssh` is the realistic failure, not an adversary. But it must be
described as *containment against accident*, never as a sandbox — and if the
tasks service ever runs AI-authored workflows unattended, cooperative is not
enough and the second column is what is needed.

For real enforcement on Linux, the plausible mechanism is a subprocess plus
[Landlock](https://docs.kernel.org/userspace-api/landlock.html) (5.13+, no root
required) or `bwrap`. That is a much larger change: the executor becomes a
supervisor, `stdout` and events cross a process boundary, and the in-process
`stream_fn` handed to `execute()` no longer works as it does. Worth scoping
separately and probably worth doing behind a flag before it is a default.

---

## What exists today, and how far it is from this

| Piece | Today | Gap |
|---|---|---|
| Path checking | `validate_path` in `xdog/agent/tools/_utils.py` | It is a **denylist** — `/etc/passwd`, `/proc`, `..` traversal. A workspace is an allowlist, which is the opposite shape. |
| A cwd | `create_bash_tool(initial_cwd=...)` | cwd is not confinement: `cd /` and `../..` both work. It sets where you start, not where you may go. |
| Anything in flow | **Nothing.** `executor.py` and `runners.py` have no notion of a working directory at all. | The workspace concept has to be introduced from scratch, and threaded to every node kind. |
| Subprocess isolation | The CLI backend already spawns one | It is spawned for a different reason and is not confined either. |

The denylist → allowlist inversion is the substantive change. A denylist answers
"is this one of the bad places"; a workspace answers "is this inside the one good
place", which is a different function with a different failure mode — an
unlisted path is *denied* rather than allowed.

---

## Decisions that have to be made

**Where does `runtime/` live for each run mode?** Relative to the workflow file,
relative to the bundle root, relative to cwd — these differ, and a workflow run
from two places would otherwise get two different workspaces. Probably: the
workflow file's directory, since that is the thing that travels with it.

**Does the workspace persist between runs?** A scheduled workflow that writes to
`runtime/` accumulates. Wiping it each run is predictable; keeping it makes
resume and incremental work possible. Likely keep, with a `--clean` flag.

**Where do checkpoints and traces go?** Inside the workspace is tidy but means a
resumable run's state can be clobbered by the workflow itself. Probably beside
it, not in it.

**What about subflows?** A child workflow almost certainly shares the parent's
workspace rather than nesting a new one — otherwise `runtime/runtime/runtime/`.
But then a subflow from elsewhere runs in *your* workspace, which is worth
stating rather than discovering.

**CLI backends cannot be confined.** `claude-cli` and `codex-cli` spawn processes
that own their own filesystem access; flow passes an allow-list of *tools*, not
of paths. Same shape as the `inherit` limitation — the honest move is to say so
in the docs and, if the workspace is meant to be a boundary, to refuse CLI
backends when a confined run is requested.

**Is `bash` allowed at all inside a workspace?** A shell is a general-purpose
escape. Either the workspace flag implies no `bash`, or `bash` runs under the
same OS confinement, or the promise is explicitly weaker when `bash` is present.
Whichever — the workflow should not be the thing that decides.

---

## Smallest useful first step

See the effort table above; this is the same sequence in prose. Not the sandbox —
the concept:

1. **A workspace exists.** `execute()` takes one, defaulting to
   `<workflow dir>/runtime`, and it is threaded to the bash tool's `initial_cwd`
   and the filesystem tool. Nothing is enforced yet; relative paths simply
   resolve somewhere predictable.
2. **Cooperative confinement.** `validate_path` gains an allowlist mode; the
   filesystem and bash tools refuse paths outside the workspace plus any granted
   trees. Script nodes are documented as unconfined — accurately.
3. **`--allow-read` / `--allow-write`**, and the same for the generated module
   and the scheduling unit, since a workflow installed as a timer needs its grant
   recorded somewhere that is still not the workflow file.
4. **Only then**, if it is needed: OS-level enforcement behind a flag.

Step 2 is where the value is. Step 4 is where the promise becomes true, and until
it exists the documentation should not claim it.

---

## The thing to get right in the wording

Whatever ships, the README sentence matters more than the implementation. "flow
workflows are sandboxed" would be false at steps 1–3 and would be believed. "A
workflow's tools are confined to its workspace; script nodes run unrestricted
Python and are trusted code" is longer, accurate, and tells the reader exactly
which of their two questions has been answered.
