# UI-045: non-Linux native credential backend implementation

> **Superseded on 2026-10-16:** the user removed feature 45. Keychain and Windows
> Credential Manager implementations and their platform tests are being deleted;
> the current selector accepts only explicit `file` or `secret-service`. The
> implementation descriptions below are historical evidence, not current support
> claims or outstanding non-Linux requirements. Track this cleanup in
> [the simplification plan](agent-ui-simplification-plan.md).

Implementation checkpoint completed locally 2026-10-11. Native macOS/Windows
execution is **not verified**; UI-062 remains deferred by the user's scope update.
This document records implementation and Linux-run contracts, not native conformance.

## Explicit selection and ownership

Coding global settings and Claw configuration `mcp_credential_backend` accept:

- `file`: existing explicitly reviewed private plaintext file backend.
- `secret-service`: existing Linux Secret Service, already-running/unlocked service.
- `keychain`: macOS Security.framework generic password, noninteractive calls.
- `windows-credential`: current-user Windows Credential Manager generic credentials.

Native selections refuse on a mismatched platform or unavailable native library;
there is no automatic file downgrade. Constructors do not read/login/contact storage.
Project/session metadata cannot replace Coding global storage policy. Public reviews
name the chosen vault, namespace and explicit storage behavior without token bodies.
Existing single-use review, view/lease ownership, restore resource/profile checking,
cancelled consent and no implicit connect/network/token refresh/tool grant remain.

The directory argument supplies a hashed namespace only: native credential backends
never write tokens to that path. Resource identity is hashed separately. Token payloads
travel through a bounded helper stdin, never argv, environment, shell or temporary
plaintext file. Error messages are fixed secret-free diagnostics. Helper process,
reader/writer threads and pipe capacity remain owned through timeout/cancellation.

## Native calls implemented

macOS uses absolute system framework paths, explicit CoreFoundation/Security ctypes
signatures, bounded CFData and type checks, exact service/account matching,
`kSecUseAuthenticationUIFail`, and helper-local user-interaction disabling. Missing
items alone permit insert; locked/denied statuses refuse, with no automatic unlock
or UI. Reads release CF results; helper-owned allocation references are released.

Windows uses secure system32 DLL loading and explicit pointer/32-bit field signatures
for CredReadW/CredWriteW/CredDeleteW/CredFree. Only generic current-user persistent
records and bounded blobs are accepted. A per-binding named mutex serializes XDOG
helper transactions; a locked/busy binding refuses, not unbounded waiting.

Full existing credential records can be64KiB, beyond a single native WinCred blob.
The implementation stages immutable2048-byte chunks and publishes one versioned
manifest with generation/length/chunk-count/SHA-256. Reads verify every chunk and
recheck the manifest; absent/corrupt chunks never restore partial credentials.
Replacement publishes before removing the previous generation. A failed manifest
write has uncertain effects and does not delete potentially admitted chunks. Forget
revokes the manifest before removing named chunks. Orphan chunks are inert and are
not automatically swept from a user's vault. No external exactly-once claim.

## Current Linux evidence

- `packages/agent/tests/test_oauth_platform_contract.py`: full1..65536-byte chunked
  roundtrip/replace/forget; missing/corrupt/staged/uncertain publication; actual
  owned subprocess pipe protocol for both selections at success/stall/oversized/
  pre/post-admission cancellation; no plaintext/secret argv; manager review,
  restart-without-load, explicit restore/forget and foreign lease rejection.
- ABI contract tests call production Windows native methods with owned ctypes
  callbacks to verify structure widths, blob bounds/free and mutex lifetime; macOS
  production query/read/update/add/delete methods verify noninteractive options,
  type/size/release and denial handling. These do **not** execute native OS APIs.
- `packages/coding/tests/test_platform_credential_ui.py`: actual Coding session
  and Claw private gateway/TUI for both selections; fully painted review required,
  save/restore/forget, secret-free notice, parent draft preserved and no model/file/
  network admission. Vault transport is an explicitly owned contract double.
- Existing current Linux native Secret Service service test and both original OAuth
  UI groups retained in the integrated gate. No dependency install, paid/auth/live
  provider call, publication or user-service restart.

Final six-file integrated gate: **66 passed, no skips,18.58s**,
`/tmp/ui045-final-integrated.log`, handle21010 exit0. Root Ruff/seven-namespace strict
mypy **488 source files clean**, `/tmp/ui045-final-static.log`, handle36314 exit0.
Current agent wheel/sdist built `/tmp/ui045-artifacts`, handle3664 exit0; three
production modules byte-compared against both artifacts,
`/tmp/ui045-source-receipt.json`. Early lint/type errors corrected; initial Linux
contract gates were green, not native-platform proof. Primary documentation fetch
attempts returned no usable content, so no newly verified upstream/API-version claim.

UI-045 named implementation acceptance complete. UI-062 macOS/Windows native
credential validation remains deferred/not passed; UI-050 additional Linux vault,
UI-055 final frozen full-root and UI-064 final safety remain independent unfinished
requirements. No scope is silently waived by these Linux contract tests.
