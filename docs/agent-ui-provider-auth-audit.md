# UI-044 — bounded provider auth/registration contract audit

Date: October 11, 2026 (+08). Scope: provider modules, provider tests, this audit,
and the explicitly authorized `register_provider` region/import in `extensions/api.py`.
No central ledger or application-host source edits. Existing unrelated dirty work
was retained. Main owns final ledger acceptance and all broader host integration.

## Status and scope boundary

- **UI-044 remains open for integrated host acceptance.** The env/resolver checkpoint
  alone was not closure. The OAuth callback/private-native-storage core added below
  is implemented and tested; main owns native/RPC/Claw menu/review acceptance. Existing
  session-owned registration, routing, typed errors and capabilities were retained,
  not reimplemented as a second model loop or native/global provider registry.
- **UI-052: missing authorized external environment / not verified.** All credentials
  below are offline sentinels and callbacks perform no network request. No named live
  provider, token endpoint, paid inference, or external image decoder was exercised.
- The reconciled requirement includes OAuth `name`, `login(callbacks)`,
  `refreshToken(credentials)`, `getApiKey(credentials)`, optional
  `modifyModels(credentials, models)`, and reviewed login/refresh/persistence UX.
  Python spellings below implement those callback semantics, **not Pi wire/API
  compatibility or a general OAuth HTTP client**. Embedding/search/image-generation
  are **not required by UI-044**; they are not counted as remaining scope here.

## Initial env/resolver checkpoint — audited methods and actual gaps

Read `AGENTS.md`, the UI-044/UI-052 ledger entries, the completion audit, reference
baseline, and provider passes 60–65, 71, 73, 80–81 in the gap plan. Inspected actual
source rather than inferring capabilities from the historical OPEN labels.

| Surface | Existing contract retained | Concrete addition/fix |
| --- | --- | --- |
| `registration`, `provider_catalog` | Explicit bounded model limits/cost/input/tools/reasoning/vision; unknown fields rejected; registration IDs and live catalog revisions | Auth remains outside this metadata; no credential or source-name fields added |
| `ExtensionAPI.register_provider` / `unregister_provider` | Three-argument callbacks; four registrations/extension, 32 models/registration; explicit replacement and original-entry disposer ownership | Optional private `auth=ProviderAuth(...)`; invalid binding rejected before changing revision or registration; old disposer cannot remove successor/auth binding |
| `ExtensionProviderStream.models`, `resolve_model` | Same configured native adapter plus session aliases; no global registry; missing aliases never fall back | Catalog/bootstrap/resume do not resolve credentials |
| `ExtensionProviderStream.__call__` | Existing context/options transfer, current capability enforcement, offered-tool checks, signatures, usage and partial typed errors | Honor caller cancel event while waiting for an RPC/admission; settle interrupted work; preserve partial aborted result; reject nonpositive/noninteger output bounds; validate image base64/header/size even without declared vision limits |
| `ProviderCalls`: `provider_begin/chunk/start/next/close` | Checksummed bounded context, opaque iterator token, explicit terminal events and output budgets | Deep-copy nested model metadata; fence concurrent pending admission and late registration/worker retirement; close invalid iterators; release failed transfer spools; exact cleanup for malformed/exhausted/canceled/over-budget streams; foreign stream close cannot destroy an input upload |
| Worker snapshot / owned runtime / two app hosts | Per-registration routing fences, saved aliases, independent children, withdrawal and no fallback | New auth-specific worker, both-host, persisted/resumed alias, child and terminal evidence without host-source changes |

The model stream's existing limit is 4,096 events / 8 MiB serialized output;
individual wire events remain bounded at 64 KiB. Binary/image/audio output events
are not part of this typed text/thinking/tool/usage/terminal stream and are rejected,
not silently converted into text. New binary admission retains a
10 MiB per-image ceiling and bounds otherwise-unreported image count to 1,024.
Declared stricter count/byte/MIME limits still apply. Binary checks are **base64,
size and PNG/JPEG/WebP header/MIME checks, not full raster decoding or proof that
a remote provider accepts the content**. UI-052 remains separate.

## Private auth contract

Import directly from the provider module; no package `__init__` export changed:

```python
from xdog.agent.extensions.provider_auth import ProviderAuth

async def stream(model, context, options, credential):
    # credential.kind is "bearer" or "api_key"; credential.value is private.
    # The trusted callback owns any actual transport; this example makes none.
    yield {"type": "text", "text": "Public response"}
    yield {"type": "done"}

remove = api.register_provider(
    "local", models, stream,
    auth=ProviderAuth(kind="bearer", env="MY_EXPLICIT_PROVIDER_TOKEN"),
)
```

Alternatively `ProviderAuth(kind="api_key", resolver=resolve)` takes a zero-argument
sync/async callable returning a token string. Exactly one environment source or
resolver is required. Resolvers must not block the worker event loop. The actual
worker RPC already supplies its configured timeout/cancellation boundary.

- Constructor/registration/catalog/model inspection and saved-alias bootstrap do
  not read an environment credential or run the resolver. Resolution is lazy,
  once per explicit stream admission, with no cache, implicit refresh, or retry.
- The optional binding wraps only the private callback slot of the existing
  `(public_info, callback)` registration. Metadata, `Model`, transferred model
  context/options, routing aliases and persisted session choices gain no auth field.
- An auth-bound callback receives a fourth positional immutable
  `ProviderCredential(kind, value)`; legacy callbacks still receive exactly three
  arguments. Credential values are 1–8,192 visible ASCII characters, excluding
  spaces/control/CR/LF; this token contract is not a private-key/password container.
- `repr` omits both credential values and auth source/resolver fields. Missing or
  invalid values and resolver exceptions produce only the public typed error
  `Extension provider credentials unavailable`; no source or exception text is
  returned. The stream callback is not invoked, the worker remains healthy, and an
  explicit later request may resolve changed credentials. No request is replayed.
- Replacement/removal during awaited resolution refuses the original callback.
  Auth callback exceptions remain private generic provider failures and quarantine
  the uncertain worker. Pending-auth/read cancellation follows the existing worker
  ownership/quarantine path; pre-cancel and safe between-event cancel need not
  quarantine. Canceling a waiter cannot kill another stream's admitted worker.
- Trusted extensions already have process permissions. This is **separation of
  credential plumbing from public metadata**, not secret-exfiltration prevention,
  OS sandboxing, encrypted storage, or guaranteed memory erasure. A plugin can
  explicitly read `credential.value` (or serialize it with `dataclasses.asdict`),
  log it, or put it in a public event; doing so violates the plugin contract. Public
  error event messages are intentionally visible/persisted, not redacted heuristically.

## Acceptance evidence

All final commands run from repository root with existing dependencies only:
`uv run --no-sync --offline python -m pytest -c pyproject.toml ...`.
Explicit root configuration avoids package-local pytest root discovery, and
`python -m pytest` makes the shared terminal harness importable independently.

Final-current provider group: **150 passed**, no skips, **83.85 s** in
`/tmp/ui044-provider-acceptance.log`. This includes the four new physical auth paths,
both-host saved-alias/independent-child auth cases, and all worker admission fixes.

Runtime/inspection/native-adapter/child-resolution regression group: **104 passed**,
no skips, **37.88 s**, `/tmp/ui044-runtime-regression.log`.
Scoped Ruff: **clean**, `/tmp/ui044-ruff-acceptance.log`.
Strict mypy using root configuration: **4 production modules, clean** (only unused
configuration notes), `/tmp/ui044-mypy-acceptance.log`. The late worker admission
fence needed an explicit type narrowing for its provider name; that was fixed before
these acceptance runs. Production/test SHA256 inventory: `/tmp/ui044-source-final.json`.
This is a timestamped provider checkpoint; the API file is also shared with main's
other disjoint work and is not asserted immutable for later tasks.
The tests use actual owned extension subprocesses, Coding's SDK/interactive UI,
Claw's temporary Unix-socket gateway/UI, and independent child runtimes. Restart
coverage stops/recreates **only a test-owned temporary gateway**; no user service
was restarted.

- Provider group: agent provider/auth/admission/worker tests plus Coding/Claw
  provider startup, dynamic/capability/UI/auth and the new physical auth tests.
  Evidence: `/tmp/ui044-provider-acceptance.log` (final-current run).
- New auth-specific physical Linux paths: both clients × success/failure,
  zero catalog-time resolution, exact one resolution, no native fallback, no token
  in pane/raw output, narrow resize/Unicode draft/cursor recovery and main-buffer/
  termios restoration. **4 passed**, no skips, 11.95 s:
  `/tmp/ui044-auth-terminal-second.log`.
- Existing physical provider matrix: both clients × success/typed failure/abort/
  dynamic replacement/capabilities, plus configured/saved alias startup.
  **12 passed**, 229 deselected, no skips, 30.52 s (final-current rerun):
  `/tmp/ui044-terminal-acceptance.log`. Earlier 38.39 s run retained in
  `/tmp/ui044-terminal-regression.log`.
- Additional direct worker pending-admission/late-retirement/invalid-iterator cases:
  **18 passed**, no skips, 0.54 s: `/tmp/ui044-worker-admission-final.log`.

Both-host auth tests verify the **saved alias** after creating a fresh worker,
no bootstrap resolution, original parent resolution count unchanged after an
independent authenticated child, success/failure persistence with no sentinel in
catalogs/transcripts/settings, and explicit withdrawal preserving the selected
alias without invoking the native adapter. These are local integration proofs,
not external-provider authentication proofs.

### Validation history (not silently promoted to passing evidence)

Initial unchanged provider baseline: 50 passed. Initial implementation group:
79 passed. The first direct worker test collection used pytest's reserved `request`
parameter name; it was renamed. Initial Ruff found test line lengths and strict
mypy found helper annotations, all corrected. A shared API typing diagnostic in a
separate owner's terminal-input region was reported to main, not edited here;
subsequent targeted API/provider strict checks passed. The first new Claw success
assertion assumed persisted default `stop_reason` was always explicit; the format
omits default success, and the assertion now respects that existing contract.
The first standalone terminal invocation lacked root importability; the explicit
root `python -m pytest -c pyproject.toml` command fixed the harness invocation,
not production behavior. Logs remain under `/tmp/ui044-*`.

No downloads, live authentication, paid calls, user-service restart, publication,
commit/reset/stash, dependency metadata edits, or central ledger edits were made.
This is a focused acceptance gate, not the final whole-workspace UI-049/regression
matrix or proof of any missing native/external environment.


## OAuth callback and private native-storage core — October 11, 2026, 16:10 +08

This supersedes any interpretation of the 150-test env/resolver result as complete
UI-044 acceptance. The explicit user reconciliation identified the missing OAuth
callback lifecycle and `/login`/code review. The host owner is implementing the
native/RPC/Claw command/menu/reviewer integration separately; it is not claimed by
the core tests below. No other provider endpoint family is needed for UI-044.

### Implemented API and trust separation

```python
from xdog.agent.extensions.provider_oauth import ProviderOAuth

api.register_provider(
    "local", models, stream,
    oauth=ProviderOAuth(
        name="Example provider",
        login=login,                  # await login(callbacks) -> credentials dict
        refresh_token=refresh_token,  # await refresh_token(copy) -> credentials dict
        get_api_key=get_api_key,      # sync/async opaque mapped key
        modify_models=modify_models,  # optional sync/async (copy, base_models) -> list
    ),
)
```

- `oauth` and the existing `auth` binding are mutually exclusive. The old API and
  env/resolver behavior are unchanged. The stream receives its mapped private key
  in the same fourth `ProviderCredential` argument; no credential field is added to
  `Context`, `StreamOptions`, model metadata, session choices or public catalogs.
- Credentials require bounded `access`, `refresh` and finite `expires` in **epoch
  milliseconds**; additional bounded JSON account fields are retained privately.
  Access/refresh use the existing 8-KiB visible-ASCII token bound; the whole private
  record is at most 64 KiB before native-storage encoding. No callback is invoked by
  registration, catalog inspection or ordinary persisted-alias bootstrap.
- Login receives `OAuthCallbacks` with async `on_auth(url, instructions='')` and
  `on_prompt(prompt)` methods. Both worker and host bridge validate bounded HTTPS
  authorization URLs and bounded prompts. URL review returns exact `True`; prompt
  review returns a bounded nonempty code string or cancellation. No callback opens
  a browser, resolves a URL, sends a network request, or starts a model/tool call.
  `on_prompt` is marked sensitive for the host renderer. At most 16 challenge steps
  are admitted, with bounded dialog and overall operation deadlines.
- Credential resolution, login/refresh results, `get_api_key`, and
  `modify_models` are worker-private. Callbacks receive copied credentials and model
  declarations. Credentials plus validated models publish atomically under the
  original registration. Model filters may return zero models while retaining the
  provider auth controls; missing aliases fail without native fallback. Logout
  restores the base model declaration. Generic auth-callback logs/UI contributions
  and extension entries are not forwarded to UI/history; only the dedicated review
  bridge receives auth challenges. This is not protection against a trusted plugin
  deliberately putting a secret in its model name, public stream output, or a file.
- Expired credentials do not invoke `get_api_key` or trigger hidden refresh. Refresh
  is a separately reviewed action; uncertain refresh failure invalidates the old
  in-memory grant and the failed worker is quarantined rather than replayed. Parent
  credentials are not borrowed by independent child workers. Restoring a saved grant
  is explicit and reruns `modify_models` without login, refresh or a model call.

### Host bridge contract (implemented core, hosts separately owned)

```python
runtime.provider_auth_catalog()  # public cached rows; never reads a vault or logs in
await runtime.provider_auth_control(
    provider_id, action, ask=ask, alive=alive, storage=storage,
)
```

- Provider IDs are `ext-<extension>-<provider>` (not model aliases). Actions are
  `login`, `refresh`, `logout`, `save`, `restore`, `forget`.
- `ask(public_request)` gets `phase=consent|on_auth|on_prompt`, `provider_id`,
  `name`, `action`, and only the relevant public URL/instructions or prompt.
  Consent/on-auth answers are exact booleans; on-prompt answers are transient code
  strings. No private access/refresh/account record ever crosses this host bridge.
- `alive()` must bind the original host's session, UI/view/connection and idle auth
  owner. The core additionally pins the worker/generation/registration, checks before
  and after review, watches withdrawal, serializes against provider streams, and
  fences stale credential/catalog revisions before admission. A parallel reviewed
  logout cannot apply to a credential granted after that logout was reviewed.
- Successful receipts contain only `ok`, `name`, `authenticated`, `expires`,
  `refresh_available`. A declined initial consent returns `ok=False` without invoking
  the worker. Mid-operation cancellation/failure has a generic no-replay error and
  quarantines uncertain work. This receipt is not evidence of external provider
  authentication; the callbacks may be entirely offline.
- OAuth-only 100-ms worker pending polling keeps long token/native steps from
  blocking EOF or depending on the ordinary 3-second RPC response limit. Teardown
  gives the worker's native-helper cancellation up to 12 seconds of shielded grace
  before ordinary forced reaping. Both cancellation and unload tests hold native
  helper cleanup for 400 ms—longer than the ordinary worker grace—and verify cleanup
  actually completes before control returns. Noncooperative external effects are
  still uncertain and are never retried or described as rolled back.

### Native persistence (no private session/catalog fields, no file fallback)

Storage actions require a separate reviewed explicit configuration:

```python
{"backend": "secret-service", "namespace": "/absolute/application-owned/namespace"}
```

Only `secret-service`, `keychain`, and `windows-credential` are accepted. Production
storage uses the existing `NativeCredentialStore` owned helper transport with a
separate provider namespace and a synthetic non-fetched `.invalid` resource key.
Credentials remain in the worker/native helper; no raw token is placed in argv,
environment variables, session JSON, catalogs or a fallback credential file.

The encrypted record is bound to provider/name, namespace, extension source path/
source bytes and settings digest. Restore rejects changed source, foreign namespace,
duplicate/malformed records and unavailable/locked native storage. `save` does not
silently enable automatic restore; `logout` clears memory but does not erase saved
credentials; `forget` erases the exact saved item but is not logout. Refresh/save and
restore/refresh are separate reviewed operations, not hidden compound actions.

The Linux test uses a **new private dbus-run-session and test-owned GNOME keyring**,
with user desktop/bus variables removed before startup. It seeds only offline dummy
credentials, verifies actual provider-worker restart/restore/rotation and locked
refusal, checks no prompter was activated and no dummy token appears in vault files,
then tears down only that fixture. No user vault, live provider, browser or service
was contacted/restarted. macOS/Windows native execution and additional Secret Service
implementations are not proved by that Linux test.

### Core evidence and remaining acceptance

- `packages/agent/tests/test_extension_provider_oauth*.py`: **61 passed**, no skips,
  **21.85 s**, `/tmp/ui044-oauth-contract-current.log`. Includes the actual isolated
  native service plus validator, callback lifecycle, copy/privacy, conditional model,
  expired/refresh, stale-review, cancellation/unload and held-cleanup cases.
- Full prior provider/worker/env/native-host/physical-auth group plus new OAuth core:
  **211 passed**, no skips, **96.00 s**, `/tmp/ui044-oauth-provider-regression.log`.
  This does **not** include the other owner's new OAuth host/menu/renderer tests.
- Scoped Ruff clean; strict mypy **8 core production modules clean** (unused-config
  notes only), `/tmp/ui044-oauth-static-final.log`. Shared API/runtime/worker files
  continue to receive other owners' disjoint changes; these are checkpoint results,
  not a final whole-workspace regression claim.
- `/login`/equivalent native menu, whole-review/masked-code UI, RPC independent auth
  capability and Claw owner/transport application acceptance belong to main/host
  owner. UI-044 remains open until that named host evidence is integrated. UI-052
  remains missing authorized external provider/decoder evidence regardless of those
  local host results.

## Authorized OAuth host adapter and Coding/RPC UI checkpoint (2026-10-11)

This is the separately authorized host expansion after the core-only sections
above. Main approved only auth-specific existing Coding regions; queue/message/
compaction edits from other owners were preserved. The Claw owner is integrating
its own small transport/orchestrator/TUI hooks; this worker has not edited Claw.

### Implemented host contracts

- `agent/extensions/provider_oauth_host.py` calls only the core's public
  `provider_auth_catalog()` / `provider_auth_control()` interface. Catalogs and
  results are validated public-only DTOs. No token resolution, credential record,
  OAuth network exchange, model/tool loop, browser or default storage path is
  added to the host adapter.
- Canonical `/extensions auth` lists status without acting. `menu` uses paged
  provider selection and a separately selected action, with **Cancel first**.
  Explicit login/refresh/logout take one current provider ID. Save/restore/forget
  take an explicit native backend and absolute namespace; the core's separate
  consent includes both values. RPC refuses storage before review/native helper.
- The approved **`/login` alias** uses that same menu/controller. `/login ID`
  requests only that selected extension-provider login, with normal consent.
  Extra tokens, malformed quoting, non-extension IDs and unknown providers refuse;
  they do not become alternate actions. Help explicitly distinguishes extension
  provider login from native-provider authentication through `xdog-ai`.
- Every sensitive-operation dialog carries a host lease and SHA256 of the exact
  public request. The host retains the matching active digest; changing a public
  action/body under a valid lease cannot authorize the original operation. Late
  phase answers and stale/foreign owner identifiers are refused by the existing
  broker/control scope plus this auth-specific binding.
- `ExtensionProviderAuth`, an `ExtensionDialog` subclass, reuses the existing
  dialog stack and full-paint exact-data renderer. Consent and on_auth require
  every page at the current viewport/focus and a distinct press. URL acknowledgment
  is separate from an optional explicit `o` opener; no opener means a truthful
  “manual URL only” label, not a dead or automatic navigation promise.
- on_prompt uses an **isolated ephemeral masked buffer**, not the normal editor's
  undo/history/kill-ring machinery. No code enters render lines, public dialog
  dictionaries, command output, persisted session JSON or parent drafts. Blur,
  submit and cancel erase the local buffer. The reply is delivered only to the
  original private worker callback; access/refresh credentials never traverse the
  host review or result interface.
- Native Coding pins the original session/runtime/command/context and idle agent/
  children/permission state. The existing authorization-change guard remains held
  through actual cleanup. Its explicit URL opener rechecks the original fully
  painted panel, focus and live digest, including after uncertain helper effects.
- RPC independently negotiates exact integer `extension_provider_auth:1`; generic
  dialog grants do not authorize auth. Provider menus work without granting
  unrelated dialogs. Auth operations reuse `extension_control` and its current
  request/session/scope; canonical auth arguments are the only action data, with
  ambiguous name/extra storage fields refused before admission. Withdrawal/cancel/
  disconnect retires the review owner and waits the existing core cleanup. No
  model/permission/history change or native-storage downgrade is introduced.

### Changed paths owned by this host worker

New production modules:

- `packages/agent/src/xdog/agent/extensions/provider_oauth_host.py`
- `packages/tui/src/xdog/tui/components/extension_provider_auth.py`
- `packages/coding/src/xdog/coding/modes/interactive/provider_auth.py`
- `packages/coding/src/xdog/coding/modes/rpc/provider_auth.py`

Approved auth-only integration in existing files:
`coding/core/agent_session.py`(transient handler + auth/login dispatch),
`coding/core/slash_commands.py`(alias and discoverable help),
`coding/modes/interactive/interactive_mode.py`(adapter binding, special dialog
factory and alias normalization), and `coding/modes/rpc/extensions.py`(auth
capability/binding/answer/admission only). Core OAuth files, `extensions/api.py`,
central ledger and all Claw application files were not edited by this worker.

New tests: agent `test_provider_oauth_host.py`, TUI
`test_extension_provider_oauth_panel.py`, Coding `test_provider_oauth_ui.py` and
`test_provider_oauth_terminal.py`.

### Evidence and remaining separation

- Root-config broad host/prior-provider/RPC regression **83passed, no skips,
  60.98s**, `/tmp/ui044-host-handoff-integrated.log`. Includes real offline worker
  login, native Coding, actual JSONL reader, independent RPC capability, ownership/
  cancellation, public-only snapshots, storage refusal/denial and existing provider
  registration/alias/model/session behavior. Counts overlap smaller gates below.
- Latest native opener/current panel/JSONL/physical group **19passed, no skips,
  19.15s**, `/tmp/ui044-auth-open-current.log`. Native opener is replaced only by
  an owned test observer; full-paint/one-use/current-owner checks execute, no actual
  desktop browser is launched. The two physical Linux paths cover `/login` menu
  approval and canonical-command cancellation, resize, code invisibility in raw
  terminal bytes, editable parent/cursor/main buffer and termios restoration.
- Scoped strict mypy **8host/integration modules clean**,
  `/tmp/ui044-host-complete-static.log`; focused Ruff clean at that checkpoint.
  Shared RpcExtensions later received the other worker's in-progress after-run
  bindings; temporary unused-binding Ruff diagnostics were reported to that owner,
  not “fixed” by deleting their cleanup ownership. Final shared-source/root gates
  remain main-owned.
- Initial test-only red: RPC teardown used `close` rather than actual `aclose`;
  corrected without weakening assertions. Physical resize requested the same size
  in later phases; changed to genuinely distinct phase sizes so the cursor-query/
  repaint assertion still runs each time. Partial alias wiring initially left the
  native command name unnormalized; the complete approved alias now enters the
  canonical `extensions` owner before dispatch and passes native/physical tests.

No installs, live provider/token-endpoint calls, real browser login, user vault
access or user-service restart occurred in these host tests. The accepted/rotated/
restored native-vault implementation is the separate core fixture evidence above;
host storage tests explicitly cover review and refusal/denial without touching a
user vault. This is not UI-052 live-provider evidence.

At this checkpoint the exact remaining UI-044 integration is Claw's independently
negotiated native auth/menu/control, including actual **cold** session bootstrap,
source-revision ownership, cancellation and physical native acceptance. The Claw
owner has the shared parser/adapter/panel contract and is implementing those hooks;
no fixture-only preselected-session workaround is accepted as cold-start evidence.
Main owns aggregate ledger disposition and final frozen root/static/artifact/example/
Linux lifecycle gates. No extra requirement, automatic restoration, new auth loop,
additional platform deferral or scope waiver is introduced here.

## Main reconciliation — October 11, 2026 16:47 +08

UI-044's named local provider/auth/registration contract is accepted as implemented
and verified in the authoritative ledger. This supersedes earlier chronological
OPEN statements about the then-missing OAuth interface; it does not erase their
history or reclassify external interoperability as passed. Evidence: current OAuth
plus prior provider211 pass (`/tmp/ui044-oauth-provider-regression.log`), both-host
native/RPC authentication100 pass (`/tmp/ui044-both-hosts-integrated.log`), and later
Claw18 pass including the fixed same-buffer before-admission withdrawal receipt
(`/tmp/ui044-claw-auth-final.log`). The final Claw gate includes cold Linux physical
menu/secret-input paths. Root-config scoped Ruff/strict8 clean at the auth handoff.
Final whole-root/current-source safety/static/artifact gates remain independent.
UI-050/052/057 genuine external environments remain required, not waived.

### Auth host handoff received before subsequent image work

The combined Coding/RPC/Claw auth-host group passed **100tests, no skips,75.30s**
(`/tmp/ui044-both-hosts-integrated.log`); scoped Ruff and strict **8module** mypy
were clean (`/tmp/ui044-host-handoff-static.log`). The Claw owner subsequently
reported its final **18passed/no skips13.39s** gate
(`/tmp/ui044-claw-auth-final.log`), including cold `/login`, independent capability,
masked-code withdrawal and same-buffer pre-admission cancellation. These groups
overlap and are not summed. The native/Claw pending clause above is superseded by
these receipts; no remaining auth-host feature edit was requested. Final aggregate
ledger/artifact/full-root disposition remains main-owned. The later idle-message
image task is a separate UI-043 scope and did not reopen provider/auth behavior.
