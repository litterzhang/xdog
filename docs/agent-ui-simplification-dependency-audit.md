# Simplification dependency audit

Date: 2026-10-16. This is scoped evidence for the completed26-requirement goal, not
proof about unknown installations or a live third-party interoperability claim.
No service restart, live MCP/provider call, login or credential migration occurred.

**Final disposition:** the proposals/checkpoints below form the audit chronology,
not unresolved current scope. The user retained UI-029; UI-019/028/048 reductions
are implemented and verified. All26 requirements are complete, remaining0. See
`agent-ui-simplification-plan.md` and the JSON ledger for the current final status.

- **19:** current-session transient recovery only; no child disk/catalog UI;
  old files, primary persistence and Agent durable safety backups untouched.
- **28:** pre-registered public S256 PKCE only. Removed client-metadata/DCR and
  confidential/JWT variants; retained pinned resource/issuer discovery and safety.
- **29:** explicit file/SecretService MCP stores and provider native vault remain;
  no automatic migration or plaintext fallback. Real service interoperability
  is not inferred from simulated native-helper safety checks.
- **48:** Sampling/Elicitation/Tasks and mutable Roots UI removed. Configured
  Roots, exact modern Roots continuation, ordinary content/subscriptions and
  SSE/GET/cancellation/no-POST-replay safety remain.

Final evidence: all8package runner5782passed/7existing opt-in CLI skips,
543strictsourcefiles, Ruff,17workflow examples and18fresh artifacts. The private
schema helper now separates bounded cold startup from unchanged execution limits;
the deterministic red/green and reaping/vendored checks are recorded alongside
`final-integration/completion-audit.json` under `/tmp/xdog-ui-simplification`.

## Checked deployment configuration

A read-only shape audit checked current default/environment-selected Coding
configuration, project `.coding/settings.json`, and Claw configuration. It did
not inspect credential files or record endpoint URLs, client IDs, headers, tokens,
command arguments or environment values. Only counts and protocol option enums
were recorded in `/tmp/xdog-ui-simplification/mcp-deployment-shape.json`.

- Coding global settings file: absent at the resolved default/environment path.
- Project Coding settings file: absent.
- Claw config: present; **zero configured MCP servers**, credential backend uses
  the explicit/default **file** choice.
- This audit does not enumerate independently configured external installations
  or infer live service behavior from configuration absence.

## UI-028: OAuth narrowing

The current implementation offers pre-registered, self-hosted metadata and dynamic
registration plus public/shared-secret/private-key token authentication variants.
No mandatory deployed service for any such combination was found in the checked
configuration. Internal conformance peers and scripted terminal fixtures are not
real service dependencies.

The bounded candidate contract is **pre-registered public-client PKCE**, explicit
issuer/client/scopes, loopback callback, reviewed login and resource-bound token
handling. Dynamic/self-hosted registration and confidential-client combinations
are candidates for removal, not silently treated as already removed or tested.
Before applying parser/manager/protocol removals, reconcile existing public API
callers and retained safe login/cancel/no-POST-replay tests. UI-028 remains pending
implementation; the audit alone does not close it.

## UI-029: Linux credential deployment

The checked current deployment uses file storage. Its required behavior is
private, bounded no-follow local files, explicit write/restore/revocation and
fail-closed errors. A configured native store must **not** silently become file
storage. Secret Service is currently an explicit opt-in Linux path rather than
an automatic fallback; it requires an actual desktop/session service environment.

UI-045 removed other native platforms, not these Linux guarantees. The remaining
choice is whether to retain the explicit Secret Service option for supported
Linux desktop deployments or remove it with explicit rejection of old settings.
No existing credential store is migrated or rewritten by this audit. UI-029 is
not declared complete solely because the current deployment chooses file.

## UI-048: additional client capabilities

Repository callers show Sampling/Elicitation/Tasks wired through the SDK MCP
manager, Coding native MCP UI and Claw gateway/UI adapters. Flow and Eyes have
no direct Sampling consumer. These are capability implementations, not evidence
of an application dependency on a real service. The checked deployment has no
configured MCP peer that requires them. Existing real transport tests mostly
exercise owned stdio/HTTP peers; their capability tests are retained until the
corresponding optional feature is deliberately removed.

- **Roots**: retain explicitly configured local file URI metadata. No inferred
  filesystem roots, file authority or permission widening.
- **Sampling**: no required service dependency found in the checked scope;
  removal is now supported by that bounded audit, subject to coordinated SDK,
  host/gateway/UI, docs and tests cleanup. Do not leave advertisement or an
  automatic model-call path behind.
- **Elicitation / Tasks / resumption**: no required service dependency found, but
  each must be traced separately before narrowing. Keep explicit refusal of
  unsupported reverse requests, cancellation/cleanup, no automatic retry of
  side-effecting calls and normal MCP tool/content behavior.
- Unknown external deployments are not claimed verified. No source fixture is
  misrepresented as a real vendor integration.

## UI-019: child draft scope still to be settled

Current local child draft management shares its codec, disk worker, crash-safe
store and review component with parent drafts. Removing those shared primitives
would violate the retained parent save/restore/crash contract. Remote draft
protocols are separate and have been removed under UI-040.

The remaining local candidate is a small explicit child save/recover path without
autosave controls or a multi-draft management browser; parent drafts retain their
own stable save/recovery/preview/exit protections. Exact retained save-slot and
restore ownership rules must be settled against actual host callers and tests
before the shared draft UI is changed. UI-019/021/022/023/024/047 remain distinct
acceptance requirements, not one count per helper/module.

## Sampling implementation checkpoint

After the above audit, Sampling removal was started as the user-approved
conditional narrowing: SDK registration/owner/executor/advertisement paths,
Claw gateway protocol and native Sampling controls are being removed together.
Shared typed/JSON review helpers and non-Sampling transport fixtures are extracted
rather than deleted with their former Sampling module. Elicitation and explicit
Roots remain for the next retained-contract review; UI-048 is not marked complete.
Retired Sampling UI callbacks must not clear a newer request's busy/owner/message
state. A parent regression exposed that mistake (red1failed/25passed) and the
client owner corrected it; subsequent check26passed. This is a real regression
fix, not deletion or weakening of the stale-owner test.

## Remaining-boundary source trace (after the green eight-package checkpoint)

Current authoritative count remains **4待实现 / 0待验证 / 0缺外部环境 /
22已完成 / 0延期**, total26. This trace prepares the four pending decisions;
it does **not** approve or implement a further removal.

### UI-048: three different meanings of continuation

- `mcp/task_registry.py` and the task branches of `mcp/reverse_requests.py` own
  optional server-initiated task creation/state/result/cancellation. Their native
  Elicitation transport is in Claw `core/runtime/elicitation_protocol.py` and both
  clients' MCP panel wiring. These are candidate removal paths.
- `mcp/input_requests.py::InputRequests` also handles **explicit Roots responses**
  inside modern owned `tools/call`, `resources/read` and `prompts/get`. It is not
  an Elicitation-only module. Root-only operation must remain possible without
  registering Elicitation, Tasks or a model handler. An explicit root continuation
  has a fresh request ID, unchanged original arguments, exact opaque requestState
  and only the declared inputResponses; it is not retry after uncertain outcome.
- `mcp/http.py` SSE cursor parsing, Last-Event-ID handling, bounded GET result
  recovery, subscriptions and no-POST-replay errors belong to ordinary retained
  transport safety. Removing optional task/input handlers must not delete these
  independently or turn an interrupted side-effecting POST into a fresh POST.

New regression `packages/agent/tests/test_mcp_roots_only_baseline.py` exercises
all3 owned operations, JSON/SSE responses and configured/unconfigured Roots,
without any Elicitation registration. It checks exact capability declarations,
no implicit Roots disclosure, original argument/state correlation, no GET/POST
retry, ordinary follow-up usability and complete manager-thread cleanup.
The first attempt correctly hit the existing prompt-catalog guard in4 cases:
the test peer did not advertise a `roots` prompt. Only that peer's retained-mode
prompt list was extended; the production catalog guard was not bypassed.

**UI-048 implementation guard:** do not remove `InputRequests` wholesale. Narrow
it to the confirmed retained vocabulary, keeping explicit Roots and ordinary
transport cancellation/no-replay protections. Sampling is already removed in
current source; earlier Sampling checkpoint wording above is historical.

### UI-029: shared native helper has a non-MCP caller

`mcp/manager.py` selects explicit file/Secret Service MCP stores. Separately,
`extensions/provider_oauth_store.py` uses the same NativeCredentialStore helper
for extension-provider credentials. Its storage configuration is **native-only**;
`extensions/provider_oauth_host.py::ProviderAuthHost` checks explicit save/restore/
forget reviews, and Coding/Claw bind that optional host capability. This is an
actual source consumer, not evidence of a configured third-party deployment.

Therefore the suggested file-only decision has an additional observable effect:
removing Secret Service globally also retires extension-provider native
save/restore/forget. It must **not** silently convert those private credentials
to plaintext files or write them during login. In-memory provider OAuth callbacks
are separate and must remain unaffected. This side effect needs inclusion in the
user's pending UI-029 confirmation. No vault, credential file, login or live
service was opened to establish this trace.

### UI-028: narrowing crosses persisted-profile validation

`mcp/oauth.py::OAuthConfig.parse` and OAuthOwner cover registration/auth choices;
`oauth_registration.py`, `oauth_client_auth.py` and `oauth_private_jwt.py` implement
optional variants. `oauth_store.py::profile_binding/restore` also bind saved
credentials to registration/auth/resource settings. A pre-registered public PKCE
reduction cannot simply remove UI switches: unsupported old profiles must be
rejected before helper/network operations, while already supported public PKCE
profiles retain issuer/resource binding, private file protections, explicit
save/restore consent, cancellation and no token-POST replay. No stored profile
was migrated or loaded during this audit.

### UI-019: distinguish UI drafts, shared primary storage and Agent return backup

Both native clients still have manual child disk helpers (`_durable_child_store`,
`_load_saved_child_drafts`, `_finish_child_draft_io`, `_child_draft_action`) and the
in-memory `_child_drafts` cache. Primary save/recovery independently uses
`tui/parent_drafts.py::ParentDrafts`, including ChildDraftStore/IO/codec/review
primitives. Those shared primitives cannot be deleted with child disk UI.

Agent-controlled queue return durability is a third path:
`agent/supervision.py` and `supervision_store.py` persist conservative returned
backups before dequeue. It is **not** the local manual child draft catalog.
Removing child draft management must not delete these UI-018 safety backups,
child journals, interrupted-operation state or controller recovery. Existing
manual saved files remain untouched; no automatic migration/deletion or
side-effect replay is authorized by the pending scope decision.
