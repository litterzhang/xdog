# UI verification sidecar — 2026-10-11

This is supporting evidence, **not the central ledger or a completion declaration**.
The historical comparison date remains **2026-10-09**. Implementation was explicitly
expanded by the user during this sidecar to the bounded UI-041 write-proposal contract;
main retains UI-043 messaging/queues and final freeze. No full-root pytest has been run
by this sidecar. Non-Linux native verification remains deferred, not passed.

## Reference provenance and limits (UI-053)

The OpenAI Docs skill was read and its official-document search/fetch order attempted.
The web tool returned no usable content. A direct retrieval of
`https://developers.openai.com/codex/cli/features/` returned **HTTP 403**, a 59-byte Forbidden body, not documentation:
`/tmp/ui-sidecar-20261011/openai.headers` and `openai-features.html`.
No new Codex documentation claim or latest-release claim is based on that response.
The repository's `agent-ui-reference-baseline.md` remains the historical account of the
successful 2026-10-09 reference capture, not a newly fetched/pinned upstream document.

The user supplied the other worker's read-only npm reference acquisition at
`/tmp/ui036-reference-20261011`. This sidecar independently checked the tarball against
both SHA-512 integrity and SHA-1 shasum in its saved publisher registry metadata:

- Package: `@mariozechner/pi-coding-agent`, version **0.73.1**.
- Registry publication timestamp: **2026-05-07T14:45:19.966Z**, before the historical baseline.
- `gitHead`: `781152fc24841dc54b22284514604048ebe5e2c9`.
- Tarball SHA-256: `7bf5d492670c04fd7c599dee7e6eaabff964084affd216766107e6741df7a2e1`.
- `extensions.md` and `types.d.ts` used below are byte-identical to their tarball members.
- Relevant other members were read without installing/executing the package:
  `package/docs/rpc.md`, `package/docs/custom-provider.md`,
  `package/dist/core/extensions/{runner,loader}.js`, and
  `package/dist/core/{agent-session,model-registry}.js`.

This establishes consistency with the saved publisher metadata, not independent
cryptographic verification of its registry signature. It supplements the historical
baseline; it does not establish that 0.73.1 is the latest Pi. Pi is not on this PATH and
no native Pi execution is claimed. A preceding raw GitHub fetch failed with curl exit35;
that failure was not silently upgraded into a successful fetch.

### Finite baseline-to-ledger mapping

These are behavior families already in the historical audit, not new requirement IDs
or a promise to reproduce every vendor method/hosted product.

| Historical applicable behavior | Existing stable IDs / owning evidence |
| --- | --- |
| Terminal input, path/file completion, shortcuts, external editor | UI-001–003, UI-036; shared prompt/key/argument/terminal owners and both app input tests |
| Steering/follow-up and editable queues | UI-004, UI-016–018; canonical Agent input queue and original parent/child receipts. Extension-origin requests remain UI-043, not supplied by user queues by implication |
| Session search, names, branches/checkpoints, compaction recovery | UI-005–007, UI-032, UI-041/043; native session managers, staged navigation, original broker review |
| Images and actual typed attachment/context admission | UI-008–009, UI-015, UI-038/040; typed Agent messages, binary sources and draft ownership; real provider/desktop evidence stays UI-052/059 |
| Full transcript navigation, copy/export/local share | UI-010–011, UI-038–040; canonical/display source readers and reviewed private exports, not automatic upload |
| Repository review and explicit plan/execution transition | UI-012–013; read-only Git review and session-owned planning/approval gates |
| Child supervision and durable/recoverable drafts | UI-014–024 and UI-040/046/047; actual child factories and private stores, not a second tool loop or physical-power-loss proof |
| MCP discovery/content/watch/OAuth/client capabilities | UI-025–029, UI-045/048/050/051/057; typed manager, original UI owners and independent SDK fixtures; no blanket third-party conformance |
| Extension hooks/tools/commands/live registration | UI-030, UI-035; actual worker dispatch/registration/disposal and argument-admission tests |
| Native/remote custom rendering and transient contributions | UI-031, UI-036–037/039/042; bounded frame, nested input, view/overlay and original transport owners |
| Editor/session/inventory/model inspection and host controls | UI-032/041/043; positive scope distinctions below. Inspection is not mutation or another composer's lease |
| Session-owned providers, model/effort selection and authentication | UI-033/044; callback registry/routing and the explicit OAuth delta below |
| Current terminal/safety/transport and final regression/artifacts | UI-049/053–056/064; named final gates, not implied by historical green counts |
| External and non-current-Linux verification | UI-050/052/057–059 remain independent; UI-060–063 deferred only |

OS-enforced sandboxing and hosted-service parity are not introduced into this scope.
Approval gates are not an OS sandbox. The mapping does not turn a host-native action
into an extension API merely because names resemble one another.

## UI-041: exact editor and host invocation reconciliation

### What the pinned Pi source actually says

- `types.d.ts:207–280` and `extensions.md:974–976`: command context extends the general
  context specifically for wait/new/fork/tree/switch/reload; the documentation warns
  that those operations can deadlock from event handlers. Keep these command-owned.
- `runner.js:createContext()` supplies UI to general callback contexts, not only
  commands. `extensions.md:2229–2230` names editor text read/write on that UI context.
- Pinned `rpc.md:987–1005`: RPC editor writes are fire-and-forget client requests;
  RPC editor reads return an empty default and custom terminal rendering is unavailable.
  Therefore **do not invent a requirement to expose live parent draft reads to every
  RPC callback**. The concrete missing behavior was a broader *write request*.
- The types comment saying `hasUI` is false in RPC conflicts with the same package's
  RPC documentation and actual UI-context binding. It is not used to waive RPC UI.

### Bounded implementation added by this sidecar

`ExtensionAPI.request_editor_text(text, timeout=300)` is a separate write-only proposal.
Existing `get_editor`/`set_editor` remain original-command CAS APIs. The new API returns
queue acceptance, **not application, persistence, model submission, or permission**.

| Invocation / host | Contract |
| --- | --- |
| Original interactive command | Existing direct command CAS unchanged; new exact-text proposal independently reviewed |
| Original tool, event, pre/post, input/context callback | Explicitly installed `editor_write_handler` may review a proposal; old read/replace APIs remain unavailable |
| Custom input callback | Same original callback ancestry, with nested completion staged into the suspended outer callback; an outer failure discards it |
| Custom render, provider, raw-terminal interception, setup, completion, headless/silent inherited validation | No proposal authority. Render cannot open a dialog; data-only interception cannot borrow a review surface |
| Native Coding parent | Full-text boolean form, private text/cursor/attachment snapshot, original session/runtime/cancellation owner, and one-use CAS. No model/history/permission change |
| Native Coding child | Only that child's selected steering/follow-up draft under its original UI token/form generation; no selected draft means unavailable, never parent fallback |
| Coding RPC parent | Independent default-off `extension_editor_write`; opaque reviewed client revision; original scope/session/capability generation; command-result delivery precedes the apply request |
| Coding RPC child | Independent `child_editor_write`, original child/connection/epoch and effective grant generation. No parent dialog/editor capability borrowing |
| Claw native/socket | Separate worker owns its exact implementation/tests; use its handoff evidence rather than inferring completion from Coding |

The shared `extensions/invocation.py` lease is deliberately small:
`current_invocation()` exposes the captured extension/session/child/method identity to
host code, and `defer(async_commit, sync_discard=None)` stages at most eight actions.
Only runtime completion may consume it. Actual callback cleanup and a successful
result are required; a JSON-RPC success carrying `isError: true` is **not** a successful
tool callback for commit purposes. Nested callbacks stage into the original parent.
Failure/cancellation/changed worker ownership discards pending work; admitted uncertain
commits are never replayed or described as rolled back. This is reusable by main's
message/compaction proposals without granting those operations automatically.

`editor_request.reviewed()` adds host-generated `editor_write` identity and only accepts
`{approved: bool, revision: opaque64hex-or-empty}`. No composer data crosses that reply.
Host application still rechecks its own original editor and capability. RPC apply events
are requests to a client, not proof the remote application painted or changed anything.
The client must retain the exact original draft/attachment/owner CAS and apply once.

`EditorWriteReviews` retains at most eight private snapshots, prunes expired/invalid
owners before admission/answer/application, and uses the original bounded dialog lifetime.
A lost delivery cannot permanently exhaust slots; unrelated live child reviews are not
cleared. Withdrawal/re-enable invalidates prior generations, including replies accepted
before the toggle. No timeout, approval, or old denial assertion was weakened to pass.

Additional finite host-scope facts were handed to main: the pinned Pi API declares
`setSessionName`, `getCommands`, and active-tool access as extension-wide actions,
whereas existing XDOG rename/menu/selection review remains command-owned. Captured
name and tool-inventory reads already cover non-command invocations; do not reimplement
those reads. Main still needs to reconcile the remaining named caller scopes or record
an explicit original-owner restriction; editor-proposal success does not resolve them
by implication. Active-tool caller scope belongs UI-043. This is not a demand for
vendor aliases, unbounded model controls, or unrestricted session/composer access.

Implemented earlier name, session/tree catalog, client command-menu and browser-request
slices remain implemented. They are not repeatedly counted as new OPEN tasks. Their
actual worker/CLI/capability/deferred-delivery regressions are included in this sidecar's
selected gate. Final frozen all-host/terminal/root evidence remains separate.

## UI-043 and UI-044: concrete deltas handed to main

These sections record source applicability, not completion or new implementation by
this sidecar.

### Message/queue and compaction contracts (main owns implementation)

`types.d.ts:833–843`, `extensions.md:1268–1316`, and the actual loader/session code expose:

- Custom messages with content/display/type/details, `steer`, `followUp`, and **nextTurn**.
  Next-turn delivery neither interrupts nor triggers a turn. Optional trigger applies
  to the other delivery modes. An extension metadata entry is not this context message.
- User-message requests accept text **or typed text/image content**, with explicit
  steer/follow-up while streaming; the actual session passes `source: extension`.
- These are extension-wide actions, not command-only methods. The pinned documentation
  explicitly demonstrates a tool scheduling a follow-up. Tool/hook/child applicability
  therefore needs original-owner reviewed/canonical admission, not unconditional denial
  relabeled as complete parity. Headless/foreign-owner denial still needs an explicit
  authority reason; it does not confer a user or parent UI lease.
- `ctx.compact()` is in general `ExtensionContext`, not only the command extension.
  Reviewed parent-command compaction is one existing slice, not broader-scope completion.

Thus idle text-only parent-command `request_message` alone does not close UI-043.
Reuse the canonical queues/loop, label provenance, preserve typed attachment admission,
review/owner/revision/cancellation, and distinguish queued/consumed/completed receipts.
No vendor alias or second loop is required. Exact current implementation status belongs
to main's contemporaneous evidence, not this historical delta list.

### Provider OAuth contract (UI-044)

Pinned `types.d.ts:956–967` has a concrete provider OAuth registration with `name`,
`login(callbacks)`, `refreshToken(credentials)`, `getApiKey(credentials)` and optional
`modifyModels(models, credentials)`. `extensions.md:1574–1606` and
`custom-provider.md:252–340` show explicit login UI, URL/code callbacks, and persisted
access/refresh/expiry data. `model-registry.js:666–672,706–710` actually registers the
OAuth provider and applies credential-dependent models; it is not just an unused type.

Finite remaining applicability: explicitly reviewed owned login, private credential
result persistence, expiry/refresh/key resolution, optional credential-derived model
metadata, and cancellation/withdrawal/error/secret isolation across original owners.
Environment/resolver private auth binding does not implement that lifecycle. Actual
live provider and independent deployment evidence remain UI-052/057. This registration
surface does **not** name embedding/search/image-generation endpoints: do not invent
those as additional requirements from generic provider terminology.

## UI-053: installed Codex contract repair, not a skipped failure

Read-only versions: Codex **0.160.0**, Claude Code **2.1.276**, Node **v24.19.0**;
Pi not found on PATH. These are installed versions, not latest upstream claims.

The initial selected opt-in gate had **35 passed / 2 failed**, 95.32s. One failure was
an obsolete assertion that the standard Codex request always offers flat
`function/custom/tool_search` kinds. A second isolated capture showed the actual
0.160.0 standard fixture request instead contained seven root functions plus namespace
`multi_agent_v1` with five functions, with no custom/search declarations. The local
proxy response completed correctly. Exact owned wire evidence is retained at
`/tmp/ui-sidecar-20261011/codex-standard-wire.{json,log}`; no real provider/auth call.

With explicit user authorization, the existing standard fixture assertion now checks
**every actual wire declaration against provider normalization**, including exact names,
namespace identity, argument schemas, reverse output kinds, and argument replay.
`packages/ai/tests/test_codex_wire_contract.py` independently retains custom and
client-search branches, namespace collisions and deliberately corrupted definitions.
An initial new helper failure incorrectly treated client-search output as a named JSON
function; its distinct nameless/client-execution/object-argument shape was corrected,
not removed. Model strings in these fixtures are not model-availability claims.

The other initial failure was the Claw SDK physical test expecting the connected status
row in a 24-line visible pane. Captured **preserved scrollback contained the exact
connected state and protocol**; expanded capability diagnostics pushed it above the
viewport. A second isolated run reproduced that layout cause. The authorized fix is
only this node's explicit tall startup viewport; later narrow/resize/watch checks and
all timeouts remain unchanged. No global wait helper or search assertion was weakened.

Final selected opt-in verification: **44 passed, zero skips**, 88.66s,
`/tmp/ui-sidecar-20261011/optin-final.log` and `optin-final.xml`. Includes seven installed
CLI/mock cases, seven new normalization cases, independent SDK/schema cases, both host
resource-watch tests and both physical SDK watch cases. It is not the final root suite.

## UI-054: exact skips, not inferred historical nodes

`/tmp/pass171-workspace.log` records **5545 passed / 29 skipped**, but has no per-node
`-rs` reason list. Source hashes are not historical test contents or execution receipts.
The exact historical 29 nodes therefore cannot honestly be reconstructed from that log.
No guessed list is presented as proof.

A current deliberately default-environment **selected** run recorded **4 passed / 33
skipped**, 2.91s. Every skipped node and actual reason is enumerated in
`agent-ui-sidecar-skips-2026-10-11.json`, derived from its JUnit XML, mapped to stable
IDs, and joined to the explicit opt-in rerun: **all 33 passed**.

- Six Codex + one Claude node: available installed CLI opt-ins, UI-053/054.
- Twenty independent SDK/schema nodes: available local pinned SDK root, UI-048/051/054/064.
- Four host SDK resource-watch nodes: available opt-in SDK, same owning requirements.
- Two physical SDK nodes: UI-049/051/054/064; current Linux/tmux is available.

The SDK root is `/tmp/xdog-mcp-pass68.lw1gcs`; installed server/core package metadata
both report **2.3.1**. No dependency was downloaded or installed. These opt-ins must be
enabled in main's final frozen gate; they are not external blockers or deferred native
OS tests. Current Linux POSIX/OpenSSL/GI/Node/Git/LittleCMS prerequisites were found;
source-only skip inspection does not substitute for running each corresponding gate.

Final whole-root `pytest -rs` and its new per-node skip record remain UI-054/055 work for
main after freeze. More tests added by other workers can create new skip nodes; this
selected 33-node mapping does not assert a final root count.

## External requirements: passive environment evidence, no unauthorized operations

Evidence files: `/tmp/ui-sidecar-20261011/environment.txt`, `desktop-passive.txt`, and
`environment-addendum.txt`. Only executable/version/library/socket metadata, process
names, and existing DBus name/PID queries were read. No credentials, clipboard contents,
provider request, login/unlock, package install, user-service restart, or power cycle.

| ID | Actually available locally | Exact outstanding prerequisite/evidence |
| --- | --- | --- |
| UI-050 | GI Secret1, libsecret tools, DBus and running `org.freedesktop.secrets`; its PID2196 is GNOME keyring. The already-owned GNOME fixture is not a second implementation. No KeePassXC/KWallet executable found on PATH | A **named additional** Secret Service implementation in an authorized isolated profile, with both-host store/restore/lock/refusal/cancel evidence. Do not touch or unlock the user's running vault |
| UI-052 | Offline adapters, image fixtures and system LittleCMS are available | Explicitly authorized real provider/model/decoder target and credentials/test allowance for capability/error/image/cancel interoperability. None supplied; fake provider tests do not replace it |
| UI-057 | Existing pinned independent SDK2.3.1, stdio/loopback HTTP and owned auth peers | A named independent deployed MCP/OAuth endpoint, version and permitted credentials/consents/refresh lifecycle for both clients. No such deployment was supplied; SDK conformance is not deployment interoperability |
| UI-058 | QEMU and dmsetup binaries exist; this process is uid1000 with effective capabilities0, and no `/dev/kvm` was found | An authorized disposable power-loss/equivalent storage-fault testbed with defined acknowledged-data criteria. No such guest/block target/physical control was designated. Binaries or process SIGKILL alone are not this evidence; never fault/restart the user's host/disks |
| UI-059 | **GNOME Shell, Xwayland, Wayland socket and X11 sockets exist.** `wl-copy`/`wl-paste` are installed; current shell is tty with DISPLAY/WAYLAND_DISPLAY unset | Controlled original-owner graphical clipboard/text/image roundtrips in both native clients and a named image-protocol-capable terminal. Kitty not found on PATH; no such terminal/session test ownership established. Do not falsely claim there is no desktop, or use user clipboard/focus without ownership |

These are five existing requirement IDs, not five new tests. Some environment components
are present; the missing item is the **specific authorized acceptance setup/evidence**,
not a blanket claim that this machine cannot run such tests. Non-current-Linux native
UI-060–063 remain separately deferred and are not used to excuse any Linux gap.

## Final-gate map retained for main (UI-049/064)

| Final gate family | Current named evidence/entry points | Still required after implementation freeze |
| --- | --- | --- |
| Parent native controls/composer/transcripts | `test_terminal_acceptance.py`, new `test_editor_write_terminal.py`, active-tools/compaction and other dedicated `*_terminal.py` groups | Whole current Linux narrow/Unicode/resize/key/paste/focus/scrollback/termios matrix, including newly added contracts |
| Child/native extension and draft paths | Child terminal/input/draft tests, new editor-write child tests, Claw worker's child physical evidence | Both hosts and each original child lease; protected approval/editor and no parent fallback |
| RPC/custom/physical editing | `test_rpc_physical_terminal.py`, `test_rpc_physical_editing.py`, `test_rpc_parent_draft_terminal.py`, all scoped custom/editor/deferred/generation groups | Current source opt-in and old-client refusal plus EOF/quit/closed/slow peer cleanup |
| Canonical safety/admission/durability | Permission, callback/child/MCP owner tests; input-admission and draft-crash audits | Fail-closed/lease/revision/cancel/no-replay across SDK, both hosts, Claw gateway and Coding RPC; no substitution of one passing slice |
| External desktop/storage semantics | UI-058/059 above | Separate real fault/desktop evidence; PTY/mock helpers cannot establish it |
| Whole workspace/artifacts/examples | UI-055/056 | Final source/test/config before/during/after consistency, complete root pytest/skip mapping, Ruff/seven-namespace strict mypy, affected distribution bytes and workflow examples |

No central ledger edit was made. Chronological selected test receipts are supporting
checkpoints; they do not independently mark UI-041/049/053/054/064 or the overall goal complete.

## Sidecar implementation verification receipts

- Expanded actual worker/native/RPC gate: **330 passed, zero skips**, 247.72s,
  `/tmp/ui-sidecar-20261011/editor-integrated.{log,xml}`. Includes old command/editor
  denial, name/catalog/menu/browser/deferred-result/generation tests, original callback
  inspection and new tool/hook/parent/child proposals, actual CLI and Coding physical
  approve/deny cases. Before/after hashes recorded concurrent changes to `runtime.py`,
  `queued_input.py`, and three OAuth files by other workers; **not a frozen gate**.
- Additional result-write/cancellation/withdrawal and stale-capacity checks:
  **5 passed**, 6.93s, `editor-late-guards.log`. Actual Coding canonical model/tool
  permission deny/ask/allow-all precedes a separate editor review: **3 passed**, 6.38s,
  `editor-canonical-permissions-fixed.log`. No proposal text is sent as model input.
- Initial new tests had fixture mistakes (terminal result includes unchanged `data`,
  non-command native tests must bind the real UI handler, failed worker callback raises
  rather than returning a ToolResult, and PermissionPrompt wraps its request). Those
  initial failures are retained in logs and are not claimed as production defects.
- Seven-namespace strict mypy at the recorded checkpoint: **525 source files clean**,
  `static.log`. Root Ruff in that combined command had three concurrent unrelated test
  diagnostics; later Ruff had two new concurrent Claw diagnostics (`ruff-final.log`).
  These are not quietly called root-clean, and their owners were notified. Sidecar-owned
  source/test Ruff and changed-path whitespace checks passed.
- `uv build --offline --no-build-isolation` initially failed because hatchling was not
  importable from `.venv`; the package correctly declares its build backend. No dependency
  was installed. The already cached hatchling/trove-classifiers code was added only to
  that build process's `PYTHONPATH`. Agent/TUI/Coding wheel+sdist builds then succeeded.
  A final scoped build recorded **13 relevant source modules unchanged during build and
  byte-exact in both artifacts**, `artifacts-final.log` and `artifacts-final-receipt.json`.
  An earlier build/source comparison saw five concurrently changed modules and is retained
  as such, not substituted for the successful stable 13-module receipt. Neither build
  substitutes for main's final frozen all-package/UI-056 gate.

Sidecar production contributions (shared files contain others' disjoint changes):

- Agent extensions: new `invocation.py`, `editor_request.py`; exact API/codec/request
  admission regions in `api.py`, `editor.py`, `runtime.py`, `worker.py`.
- Agent child ownership: exact editor-write token/generation/handler regions in
  `supervision_extensions.py`; no terminal metadata redesign.
- TUI: new `editor_write.py` private review controller and
  `components/extension_editor_write.py` full-review component.
- Coding: new `modes/rpc/editor_write.py`; capability/bind/editor-delivery regions in
  RPC `extensions.py`/`children.py`; native editor review/commit and child binding regions
  in `interactive_mode.py`. Main's message/queue branches were not rewritten.
- Dedicated tests: Agent `test_extension_editor_write.py`,
  `test_extension_host_scope_verification.py`, `fixtures/extensions/editorwrite/`;
  Coding `test_editor_write_ui.py`, `test_rpc_editor_write.py`,
  `test_rpc_child_editor_write.py`, `test_editor_write_terminal.py`;
  TUI `test_editor_write.py`; AI `test_codex_wire_contract.py`.
- Two specifically authorized existing verification adjustments:
  `test_proxy_codex_cli.py` actual-wire normalization assertion and the single physical
  SDK Claw node's startup viewport in `test_terminal_acceptance.py`.
- Claw production and its dedicated tests belong to its separate worker, not this list.

No install, live provider/authentication, publishing, user-service restart, destructive
storage operation, or full-root pytest was used for these receipts.

### Final sidecar handoff checkpoint

The complete seven-file **78-case** sidecar editor suite passed with **zero skips** in
**102.51s**, `editor-final.log`/`editor-final.xml`. Its source receipt shows concurrent
changes in provider registration/OAuth and RPC auth-control files; it remains a selected
current-worktree checkpoint, not main's freeze. Those source hashes are retained in
`editor-final-before.json`/`editor-final-after.json`.

The latest separate static receipts supersede the earlier transient diagnostics:
**root Ruff clean** (`ruff-handoff.log`) and **seven-namespace strict mypy clean, 529
source files** (`mypy-final.log`). The earlier failures/drift remain explicitly recorded.
UI-049/054/055/056/064 final all-source gates and the five external requirements remain
independent. No central requirement was marked complete by this sidecar.

### Subsequent explicitly assigned UI-041 positive scopes

`agent-ui-command-menu-name-scope-audit.md` supersedes the earlier remaining menu/name
scope notes for the two implemented finite deltas. Actual original-parent noncommand
read-only menu access has108 passing selected regressions. Reviewed original-parent
name proposals now wait for real producer settlement before canonical expected-name
CAS; the final new/old naming gate has55 passes, including four physical native cases.
These additions do not change the historical baseline, skip mapping, five external
requirements, or ownership of the final frozen root gates.
