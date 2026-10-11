# UI-053 final baseline / public-export / owner-evidence reconciliation

**Sidecar signoff: October 11, 2026, 18:23 +08.** This signs off the finite UI-053
reference-to-implementation reconciliation, not the ongoing final root, UI-054 skip
mapping, external requirements, or goal completion. Main owns central-ledger status.
No code/test change, new collection or test invocation was made for this signoff.

## Reference identity and epistemic limits

- The historical baseline is **2026-10-09**, as retained in
  `agent-ui-reference-baseline.md`. Its successful pass134 capture is historical
  evidence, not a newly fetched document or pinned latest release.
- The **OpenAI Docs** skill was used. The sidecar's earlier official search returned
  no usable content and direct CLI-features retrieval returned HTTP403 (saved headers
  and59-byte body under `/tmp/ui-sidecar-20261011/openai*`). The final official-domain
  search and features-page open again returned no usable content. No updated/current
  OpenAI documentation or latest-version assertion is inferred from these attempts.
- The installed **Codex0.160.0** and **Claude2.1.276** receipts describe local clients.
  Codex's captured actual standard wire was retained, not edited to match a fixture:
  seven root function tools plus five functions under `multi_agent_v1`. The approved
  fixture correction checks actual-wire-to-provider normalization of tool names,
  kinds, namespaces, schemas and argument replay. Dedicated tests still cover custom
  and tool-search branches and corruption. This is offline owned-proxy evidence,
  not live model/provider conformance. The selected opt-in44-case gate passed;
  its logs/capture are retained by `agent-ui-sidecar-reconciliation-2026-10-11.md`.
- The supplied author-published `@mariozechner/pi-coding-agent` **0.73.1** archive has
  `gitHead=781152fc24841dc54b22284514604048ebe5e2c9`, publication
  **2026-05-07T14:45:19.966Z**, and SHA256
  `7bf5d492670c04fd7c599dee7e6eaabff964084affd216766107e6741df7a2e1`.
  SHA512/SHA1 agreement with saved registry metadata is integrity evidence, not
  registry-signature verification. This release predates and supplements the
  historical baseline; it is not asserted to be latest.
- Archive `package.json` exposes `.` through `dist/index.{d.ts,js}` and `./hooks`
  through `dist/core/hooks/index.{d.ts,js}`. The root declarations explicitly export
  `ExtensionAPI`, `ExtensionContext`, `ExtensionCommandContext`, `ExtensionUIContext`
  and `ProviderConfig`; the scoped APIs were not inferred from private filenames.
  The relevant declarations, docs and session/runner implementations were read as
  archive bytes. **No Pi installation/import/native execution is claimed.**

The existing finite family-to-stable-ID table in
`agent-ui-sidecar-reconciliation-2026-10-11.md` remains the baseline-wide mapping,
including terminal/session/images/repository/planning/children/MCP and the five
independent external prerequisites. It is not a promise to reproduce every upstream
export, vendor alias, hosted feature or OS sandbox. No new speculative parity gap is
introduced by this signoff.

## Actual final XDOG public surface

AST inspection was checked against main's **1426-entry `source-final.json`** without
importing providers or running tests. Six inspected export/API/auth files matched
that manifest. Exact hashes, public class fields/methods and literal `__all__` values
are in `/tmp/ui-sidecar-20261011/ui053-final-public-exports.json`.

- `xdog.agent.extensions` exports **`ExtensionAPI`**. The empty legacy
  `xdog.coding.core.extensions` initializer is not the implementation/public authority.
- `xdog.agent` explicitly exports the canonical `Agent`, `CustomAgentMessage`,
  `Delivery`, `QueuedInput`, history helpers, input-consumption/rejection events and
  original agent-loop/continuation functions. Custom messages are not ordinary user
  messages merely because model projection has a compatible shape.
- `ProviderAuth` and `ProviderOAuth` are deliberate direct-module imports from
  `xdog.agent.extensions.provider_auth` / `provider_oauth`, not promised package-root
  reexports. `register_provider(..., auth=..., oauth=...)` validates these bindings;
  public catalog metadata remains distinct from private credentials.

All public `ExtensionAPI` method/property names found by this inspection are grouped
below. A method's presence is **not** proof of host authority: the listed owner's
runtime, negotiated native/RPC/socket binding, review and lifecycle tests provide it.

| Actual public methods/properties | Stable scope | Owning evidence |
| --- | --- | --- |
| `has_ui`, `select`, `input`, `confirm`, `form` | UI-030/031/041 | Extension runtime/worker/dialogs; Coding RPC scope audit and both-host dialog/form/child gates. `has_ui` is only an availability hint. |
| `request_editor_text`, `get_editor`, `set_editor` | UI-041 | Sidecar editor tests and `agent-ui-sidecar-reconciliation-2026-10-11.md`: broader write proposal separate from original-command composer read/CAS; original selected-child binding only. |
| `get_session`, `get_session_name`, `set_session_name`, `get_command_catalog` | UI-032/041 | `agent-ui-command-menu-name-scope-audit.md`: actual independent read-only host menu; original-parent reviewed name proposal waits for actual producer cleanup and expected-name persistence CAS. |
| `list_sessions`, `get_session_tree`, `open_sessions`, `navigate_session` | UI-005–007/032/041/043 | Session/browser/navigation owners and `agent-ui-extension-host-controls.md`; command-owned lifecycle operations are not borrowed from a tool/child. |
| `get_model`, `get_context_usage`, `get_tool_inventory`, `get_active_tools` | UI-032/033/043 | Read-only original invocation projections and inventory owner tests; no implied credential resolution, composer access or selection authority. |
| `set_active_tools` | UI-043 | `agent-ui-active-tools-after-run-audit.md`: reviewed original-parent noncommand proposal, actual producer barrier and canonical selection/permission pipeline. |
| `request_compaction` | UI-043 | `agent-ui-after-run-compaction-audit.md`: supported original-parent tools/hooks defer canonical compaction until the producer settles; no recursive compaction callback or second loop. |
| `request_message`, `request_queued_input`, `has_pending_messages` | UI-004/016–018/043 | `agent-ui-queued-input-audit.md`, `agent-ui-claw-host-delivery-audit.md`, `agent-ui-idle-message-images-audit.md`; reviewed canonical user/input queues, typed images, independent remote negotiation, receipt/owner fences. |
| `request_context_message`, `append_entry`, `drain_entries` | UI-030/043 | `agent-ui-custom-delivery-contract.md` and context-message audit. Canonical custom delivery is implemented separately; extension metadata entries/draining are **not** substitutes for message delivery. |
| `custom`, `request_render`, `request_overlay` | UI-031/037/039/042 | Renderer and RPC-custom contract audits; bounded frames/layout/overlay refresh, original repaint/input owner, native/remote capability differences. |
| `on_terminal_input`, `terminal_events_for`, `terminal_events` | UI-036 | `agent-ui-input-surface-audit.md`: registered press/repeat/release observation and exact original raw/data interception boundaries, not dialog or headless authority. |
| `on`, `context`, `on_input`, `before_tool`, `after_tool`, `hook_snapshot` | UI-030/035/043 | Hook/worker registration/lifecycle and input/context tests; invocation-scoped positive handlers and explicit denied phases. Snapshot metadata does not grant a host operation. |
| `tool`, `tool_snapshot`, `result` | UI-030/035/043 | Tool registration/inventory/result owners, structured result/error provenance and cancellation tests. Successful JSON-RPC transport is not successful `ToolResult` commitment. |
| `command`, `command_snapshot`, `shortcut` | UI-001–003/030/035/041 | Actual command/shortcut registration/disposal/completion and owner-scope tests, not synthesized parent menu rows. |
| `notify`, `status`, `widget`, `clear_status`, `clear_widget`, `drain_ui` | UI-031/035 | Bounded UI contribution/retirement tests; no replacement for canonical history/messages or raw terminal authority. |
| `register_provider`, `unregister_provider` | UI-033/044 | `agent-ui-provider-auth-audit.md`: session registry, private auth, explicit OAuth lifecycle and both native/RPC/socket owners. |

Public registration containers/settings and snapshot/drain helpers are supporting
registration/transport state, not extra requirements or alternative admission APIs.
Different Python spellings and argument order are not vendor-wire compatibility claims.

## Finite deltas closed by their actual owners

### UI-041

Menu, write proposal and after-run naming positive scopes are implemented, not left
open by a command-only paper restriction. Provider/render/raw-terminal/headless paths
cannot borrow those hosts; child operations require their own existing original lease,
never a synthesized parent's composer/menu/name. The menu108, naming55 and editor78
selected receipts remain in their audit. Diagnostic root failure0 was **before tool
execution** (empty inventory after contained bootstrap failure); strengthened bootstrap
assertions retain the original error on any recurrence. It is not described as a
naming review/CAS defect or a proved timeout. Final root revalidation remains separate.

### UI-043

The formerly open canonical custom active `steer` / `follow_up` and idle-trigger
branches are now integrated and accepted by main, superseding the earlier pending
section of the sidecar reconciliation. `next_turn` remains non-triggering and consumed
only by an admitted user prompt. Idle non-triggering delivery immediately persists
canonical custom context without a model call; true triggers continue canonical
history. Active delivery uses the existing Agent queues. Type/content/display/details
remain canonical, with model projection/provenance kept distinct.

The current owner document is `agent-ui-custom-delivery-contract.md`. The sidecar
read the actual log tails: **12 physical**, **24 socket/grant**, **105 integration**
and **54 RPC scope/continuation** tests passed. These overlapping groups are not added
as separate requirements. Original-loop admission, complete delivery/persistence,
competing producer isolation, draft/image separation and one-use negotiated grants
are part of the accepted local contract, not aliases standing in for it.

### UI-044

The finite Pi OAuth registration includes `name`, `login(callbacks)`,
`refreshToken(credentials)`, `getApiKey(credentials)` and optional model modification.
XDOG supplies explicit `ProviderOAuth` fields `name/login/refresh_token/get_api_key/
modify_models`, original-worker private credential ownership and separately reviewed
login/refresh/save/restore/forget/logout controls. Python callback conventions are
stated in its audit rather than assumed to be Pi's call signature. Native storage,
masked code/URL review and original host negotiation are independently owned.

The old150-test env/resolver slice is **not** used as OAuth completion. The later
owner audit and inspected logs record **211 provider/OAuth**, **100 both-host** and
**18 final Claw** passing cases, including cold native startup. The audit's earlier
then-pending Claw clause is explicitly superseded by those receipts. No finite local
OAuth callback/host delta remains in this reconciliation. Live provider/deployment
requirements remain UI-052/057; embedding/search/image-generation endpoints are not
invented from this registration contract.

## Signoff boundary

UI-053's applicable historical behavior families now have stable IDs, actual exported
surfaces and owning evidence. No additional finite unmatched local contract was found
in this reconciliation. This is **not** a latest-upstream, native-Pi, blanket API/wire
compatibility, external interoperability or full-root-pass claim.

The fresh root started **2026-10-11 18:15:44 +08** and is still running. Main's static,
artifact/import/example receipts are separately retained; UI-054's exact final skip
map and final source-freeze comparison remain pending that invocation's completion.
The historical29 skipped nodes remain unreconstructable from their count-only log.
The five external prerequisites and non-current-Linux deferred native evidence remain
separate and are not converted into passing local tests.

### Subsequent complete-run receipt

The18:15:44 invocation later completed:8183 passed,3 failed,0 skipped/8186 unique
cases, with source drift in two test files. Its exact XML/verbose-log node sets match
and all33 earlier default-skipped nodes passed. See
`agent-ui-final-skips-2026-10-11.json` and the final verification reconciliation.
This does not retract the finite reference/export mapping, but it is not a frozen
passing gate or goal completion. No failed test was converted into a skip/waiver.

### Authoritative local final gate subsequently passed

The teardown-final root completed22:38:48 +08 on October 11, 2026:8200 passed,
zero failures/errors/skips, pytest/source-check exits0 and1427 scoped source members/
hashes unchanged. All14 named Linux terminal/safety rows pass, and all33 previously
default-skipped opt-in nodes pass in that same invocation. Exact evidence is in
`agent-ui-teardown-final-skips-2026-10-11.json` and the final named-gates documents.
This supports final local adjudication without changing the October9 historical
baseline or making a latest-upstream/native-Pi/external-conformance claim. Historical
29 skip identities remain unknown; the five external requirements remain required.
