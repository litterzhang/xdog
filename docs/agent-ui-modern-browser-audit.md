# UI-048 sidecar — modern native MCP browser continuations

Checkpoint: October 11, 2026. Test-only sidecar to the MCP owner's finite
client-capability audit; not a new requirement or a central ledger edit. Production
MCP changes belong to the MCP owner, Coding changes to main, Claw changes to its
integration owner. The sidecar began test-only; the user subsequently authorized
the bounded cold-owner fix documented below, coordinated with the Claw owner.

## Peer and applicable scope

The existing pinned independent SDK **2.3.1** at
`/tmp/xdog-mcp-pass68.lw1gcs` runs
`packages/agent/tests/fixtures/mcp_official_sdk/input_peer.mjs`. The fixture asserts
the SDK version. No install/download or live MCP/provider/browser service is used.

The modern peer returns `input_required` for actual `resources/read` and
`prompts/get`, keyed by `review`, with an opaque state string that includes leading
spaces, NUL, Unicode and newline. The original method resumes only after the owned
review resolves; it uses a new JSON-RPC request ID and exact state. Resource names
are `test://roots`, `test://sampling`, `test://form`, `test://url`; prompt names are
`roots`, `sampling`, `form`, `url` with no arguments.

The ordinary native browser keys `b` and `f` arm sampling/elicitation before
connection. Native hosts legitimately advertise `sampling.context` as well as
`sampling.tools` because their existing owning-history context resolver is
registered; the bare manager fixture without that resolver advertises less.
Review events preserve `protocol: "2026-07-28"`. Modern URL requests have no
`elicitationId`, require explicit acknowledgment, and create neither a legacy
pending-URL registry nor an external-completion notice. Painting/acknowledging is
not permission to launch a browser or authenticate.

## Added tests and verified behavior

### Actual native browser + worker/socket integration

`packages/coding/tests/test_mcp_modern_browser_ui.py`: **56 passed**, 62.00s,
`/tmp/ui048-browser/ordered.log`.

- Coding and Claw, real stdio and HTTP SDK peers, resource/read and prompt/get,
  all four input families.
- Original outer browser request identity remains current through input/request
  and output/response review; full-paint consent remains mandatory. No second
  ordinary MCP browser job is manufactured to answer a review.
- Zero model calls before sampling input approval, exactly one isolated configured
  adapter invocation for approved sampling, zero for roots/form/URL; no parent
  history mutation or tool execution. Original parent draft stays editable.
- Exact fresh-ID continuation/state and final document retention after natural
  closure. Additional ordering cases hold the **authentic already-admitted** panel
  close callback until the outer document is delivered, then verify the same
  preview remains. This controls event order, not ownership/approval policy.
- Preview loading is asynchronous; tests wait for actual painted document text,
  not merely `_mcp_document` assignment.

The first natural-order matrix was **32 passed**, 36.22s
(`/tmp/ui048-browser/initial3.log`), overlapping rather than additive evidence.
Earlier test-development failures were corrected expectations (native context
capability and asynchronous preview painting), not production patches.

### Cancellation during the original outer job

`packages/coding/tests/test_mcp_modern_browser_cancel.py`: **12 passed**, 16.53s,
`/tmp/ui048-browser/cancel.log`.

Both native hosts × resource/prompt × sampling/form/URL, real stdio peer. Closing
the actual browser revokes the original registrations and settles input jobs;
there is one original operation, no unreviewed continuation, no model call, no late
preview revival, and the parent draft/history remains unchanged. A subsequent
catalog request on the same peer verifies cleanup actually settled.

### Current Linux physical terminals

`packages/coding/tests/test_mcp_modern_browser_terminal.py` selected-owner matrix:
**16 passed**, 54.56s, `/tmp/ui048-browser/terminal-selected-owner.log`.

Actual Coding/Claw native clients in isolated test-owned tmux terminals ×
resource/prompt × roots/sampling/form/URL, using the pinned real stdio serving SDK:

- Physical `b`/`f`, catalog filtering and explicit resource selection / prompt
  argument submission; no mocked browser/action routing.
- Sampling input/output and form/URL request/reply/response reviews are fully
  painted before actual Enter. The peer receives no resumed operation before the
  last approval. URL acknowledgment does not launch any desktop browser.
- Shrink/grow and fresh paint during review/final preview; exact final returned
  document remains visible; ordinary parent Unicode editing and hardware cursor
  recover afterward.
- Exact termios restoration, no main-buffer or scrollback clear, no alternate
  buffer entry; exact model-call counts and no unsolicited tools/call.

The Claw matrix selects an original idle session before attaching the terminal,
as the existing sampling gateway fixtures do. **It is not cold-start evidence.**
The first root test accidentally requested a same-size resize; the test now really
shrinks before growing and asserts both paints. The fixture's failure cleanup now
always disconnects its owned tmux before joining gateway shutdown. The first
failure left one private test tmux until explicit cleanup; that exact owned server
was removed, not any user terminal/service.

## Reproduced Claw cold-admission defect (corrected below)

Dedicated regression:
`test_linux_cold_claw_browser_sampling_arm_uses_current_owner` in the same terminal
file. `/tmp/ui048-browser/cold.log`: **1 failed**, 12.40s. This repeats the earlier
cold Claw failure in `terminal-second.log`; it is not waived by the selected-owner
matrix above.

Exact recorded packets in
`/tmp/pytest-of-zhang/pytest-2031/test_linux_cold_claw_browser_s0/cold-admission.jsonl`:

1. Native UI sends `mcp_sampling_control`, `action: enable`, valid request ID,
   group `main`, a valid view lease, server `modern`, but `session_id: ""`.
2. Actual orchestrator `source_lease(main)` is `("", 0)` at admission.
3. `SamplingConnection.request()` rejects the empty session and replies only with
   `{type: mcp_sampling_result, success: false, error: Invalid sampling control}`.
   It omits request ID/group/session/view correlation.
4. The native client cannot match that reply to its pending arm action and remains
   on `MCP servers · modern · loading`. No SDK peer/model operation has started.

**Bounded patch request sent to main and the Claw owner (subsequently authorized):** establish an
authoritative original idle session before arming the review capability, and
ensure a rejected otherwise-identifiable request can settle its own frontend
request through a correlated fail-closed result. Do not simply accept arbitrary
empty/stale owners, remove idle/reservation checks, or automatically replay a
review/operation. Exact bootstrap/transport implementation was explicitly authorized for this sidecar after coordination
of the exact disjoint MCP methods with the Claw owner. Coding showed no corresponding host gap in
this matrix. Other cold controls should be checked by the owner rather than
silently assumed fixed by the sampling change.

## Commands / recommendation

All commands run from repository root with existing dependencies:

```bash
XDOG_MCP_OFFICIAL_SDK_ROOT=/tmp/xdog-mcp-pass68.lw1gcs \
  uv run --no-sync pytest -c pyproject.toml -q \
  packages/coding/tests/test_mcp_modern_browser_ui.py \
  packages/coding/tests/test_mcp_modern_browser_cancel.py \
  packages/coding/tests/test_mcp_modern_browser_terminal.py
```

The command includes the cold regression; its authorized fix and current
verification are recorded below. Native class/socket tests do not substitute for
physical terminal execution; both forms of evidence are named above. HTTP
physical terminal combinations and non-Linux native execution are not claimed.

This sidecar verifies the modern browser continuation, review/close ordering and
current Linux physical paths. It does not by itself close all UI-048 capability
requirements. Main retains the final integrated lifecycle/static/source gates.
No central ledger, Coding application, extension API, or navigation module was
changed here. The limited authorized Claw edits are enumerated below.


## Authorized cold-owner correction

After the reproducible Linux failure, the user authorized a direct bounded fix.
The Claw integration owner confirmed these exact MCP seams were free; their
concurrent editor/queue/auth/extension work was not overwritten.

- `core/runtime/sampling_protocol.py::SamplingConnection.request` and
  `elicitation_protocol.py::ElicitationConnection.request` still reject empty,
  invalid or stale owners. Invalid-control replies now echo only bounded string
  correlation fields so identifiable frontend requests can settle. A denial is
  not a capability grant.
- `core/runtime/gateway.py::_handle_client`, only the `mcp_control` branch: a new
  explicit `owner` operation carries a 32-hex browser view and exact empty expected
  session. It reserves the idle group and checks the authoritative source is still
  empty/non-navigating before constructing the original active session. Existing
  or concurrently selected sessions are refused, never silently adopted. This
  does not initialize a provider, connect a peer, arm reviews, or start a model.
  The normal `list` operation remains read-only.
- `gateway.py::_dispatch` pong advertises `mcp_browser_owner: 1`. Updated native
  clients use this support only for explicit cold MCP opening; older gateways
  are not sent an unknown owner operation.
- `channels/tui/tui_app.py` MCP state initialization, `_mcp_send`, `_close_mcp`,
  and MCP-result/pong branches of `_handle_response`: cold browser opening requests
  authoritative ownership before enabling its list. The reply must match the
  original request, view, group and unchanged connection/session/output context.
  Closed/replaced/foreign replies cannot adopt a session. Only the exact successful
  owner result updates the browser's session context. `b`/`f` remain subsequent
  explicit gestures; no control or outer operation is replayed. On older gateways
  an empty-owner arm is refused locally with a settled notice rather than sent.

Targeted actual Linux cold check went **red (12.40s) → green (4.30s)**:
`/tmp/ui048-browser/cold.log`, `cold-patch.log`. The physical cold regression is
retained, not replaced by a prewarmed fixture. New
`packages/claw/tests/test_mcp_browser_owner.py` initially ran **15 passed** in
1.01s: actual socket correlated invalid sampling/elicitation replies, idle/busy/
existing/invalid-view/wrong-expected owner admission, no implicit arm, foreign
response rejection, older-gateway denial and closed-view fencing. Strict Claw
mypy: **82 source files, no errors**, `cold-types.log`.

Final affected gate: **127 passed, zero skips**, 132.63s,
`/tmp/ui048-browser/cold-regression.log`. It includes the modern native/socket and
physical matrix, cancellation, cold-owner regression, exact owner/rejection tests,
and both hosts' existing sampling/elicitation suites. The three subsequent
negative owner-result cases and explicit **sampling-first / elicitation-first cold
Linux** cases passed with existing MCP browser and Claw roots regressions:
**36 passed, zero skips**, 10.63s (`cold-final-extra.log`). These groups overlap;
do not add their totals. The owner test file now contains **18 cases** and the
physical file **18 cases** (16 selected-owner + two cold-start orders).

Final changed-file Ruff and diff whitespace checks are clean. Root strict Claw
mypy reports **82 source files, no errors** (unused-override notes only),
`/tmp/ui048-browser/final-static.log`. Source/test/peer hashes and timestamp are in
`final-source.log`. Other owners were editing unrelated Claw regions concurrently;
this is scoped current-source evidence, not a frozen full-workspace claim.

**Final sidecar recommendation:** the native modern browser continuation and
current-Linux physical gate are satisfied, including the formerly failing cold
Claw admission. No remaining Coding or Claw host patch is requested by this
sidecar. Main may use these results with the MCP owner's finite protocol matrix
when deciding UI-048 status; the sidecar does not edit that status or substitute
for final whole-root integration. The separately reported UI-037 nonreproduced
Claw nested-completion timeout remains a final lifecycle follow-up, not erased by
this MCP result. Non-Linux native execution remains deferred.
