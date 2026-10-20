# Coding ownership, trust and preparation reliability

## Intent / constraints

Coding stays a user-directed, project-scoped, recoverable coding client. All entry
modes reuse one session execution owner and xdog.agent.Agent. Keep executable
branches/checkpoints, bounded independent batch inputs, native steer/follow-up,
automatic Inbox consumption and fail-closed tool permission gates. Goal storage,
roles and extension UI remain capability-owned with exact session/branch/workspace
bindings. No second tool loop, generic multi-session framework, new daemon,
user-data migration, uncertain tool replay or automatic publishing is introduced.

Work in the current checkout (base 99a15e6), not a worktree. User approved autonomous
design/implementation and related simplification; do not add repeat approval gates
or auto-commit/push/version/tag changes. All experiments use temporary state.

Coding supports its own configuration and the generic AGENTS.md convention.
Automatic project context is exactly these files relative to the current working
directory, nonrecursive:

- AGENTS.md
- .coding/INSTRUCTIONS.md
- .coding/instructions.md

Each file is limited to 64 KiB. No parent-directory walk and no symbolic-link
following for automatic instructions. Missing files are optional; an existing
unreadable, nonregular, invalid UTF-8 or oversized configured instruction file
must produce an explicit preparation error, not silently drop constraints. This
is configuration discovery, not a file-access restriction or filename denylist.
Other harness configuration files are ordinary files: explicit tool reads or user
attachments follow normal file-access and permission rules. Tests verify supported
source selection and explicit ordinary-file access, without global open interception.

## Approach

Choose targeted repairs and consolidation of existing owners over (a) rewriting
Coding around a new session framework or (b) patching only individual UI callers.
The former increases state machinery; the latter leaves alternate entry paths
unsafe. Keep product policy out of the generic TUI and ACP adapters.

### 1. One extension activation path

Delete CLI's legacy eager discovery/import path and demonstrably caller-free
legacy loader/runner code. Only explicitly selected global trusted IDs activate
through the existing owned extension worker. Directory presence, project config,
provider text and imported metadata never authorize Python execution. Retain the
actual extension worker, MCP and ACP protocols.

### 2. Storage identity is the authority

Writer identity is canonical session-store + session ID, anchored at stable
private store-owned lock files, independent of TMPDIR. Keep short Unix socket
placement/discovery separate from the authoritative lease. A differing temporary
environment must neither create another writer nor redirect a session to another
store; discovery must preserve validated workspace/generation and private inode
checks. Never unlink an active lock inode.

Before any writable adoption, requested ID, filename and embedded session ID must
match. SDK resume obeys the same boundary as navigation and Inbox. A corrupt A
must not acquire lease A then write B, including when B already has its own writer.
Missing is not invalid/unavailable. Lists isolate malformed entries and expose
useful diagnostics without replacing history or guessing an executable session.

### 3. Admission, navigation and shutdown retain original ownership

Every admission captures its original store, session, branch, generation and lease
scope. Navigation first fences new admissions, settles already admitted/persisting
work in that original scope, then replaces bindings and releases retired leases.
No completion path looks up a mutable successor store for an old request. Do not
wait on an operation that depends on the current control reservation: settlement
must avoid controller self-deadlock. Failure fences the original owner and cannot
poison an unrelated successor or replay tools.

Use one awaitable, idempotent close contract for all entry modes. Close admission,
join admitted work/children/capability publication and callbacks, settle every
Goal/extension/MCP resource despite earlier cleanup errors, then release leases.
Repeated cancellation detaches a waiter, not owned cleanup. Aggregate failures
after all remaining resources settle. Keep sync dispose as an explicitly limited
adapter if actual callers require it, not a competing shutdown policy. Consolidate
SessionRuntime/SDK lifecycle implementation around the existing controller.

### 4. Preparation and history commit

Build detached project/skill/prompt inputs on owned workers; construct/publish the
Agent only on its existing owner. Project context reaches the actual model Context
once and cannot change tool permission or extension trust. Agent alone places the
skill index and fixed/turn bodies; Coding adds product instructions, not another
index. Keep cacheable session scope separate from transient turn skill messages.

Ordinary history commits capture immutable messages/settings/model/metadata and
original context ownership, perform actual IO on an owned worker, and publish
state/receipts only after commit. Remove duplicate AgentEnd/turn-end full saves,
without weakening failed/interrupted tail preservation. Cancellation waits for the
actual commit; uncertain effects never enter a retry/append/model fallback.
Use directory fsync consistently for acknowledged managed file replacements;
verify crash recovery without claiming hardware power-loss exactly-once.

### 5. Capacity and configuration are executable contracts

InputStore's accepted output must be reopenable under its 64 MiB reader bound.
Use explicit pre-admission refusal when capacity is exhausted, preserving all old
execution facts; reserve bounded space for already accepted requests' terminal
status/batch/error growth. Bound diagnostics deliberately. Do not silently drop
receipts or ACK a request that cannot later settle. No new archival engine is
required for this repair; users can explicitly start another session.

Resolve configuration once into an effective session configuration. Defaults are
for genuinely missing files, not a way to hide invalid owner settings. Model and
thinking precedence is explicit SDK option / CLI override, then saved session,
then project, global and documented defaults. Explicit `off` is a value, not
absence. Keep explicit SDK fields and override mappings consistent.

`allowed_tools` becomes an explicit initial active-tool selection, **not a
permission policy or sandbox**: absent/null means configured defaults; [] means
no active tools. For a restored session, a saved explicit selection wins over
project/global defaults; an explicit caller override wins over saved selection.
Validate names and retain existing tool-retirement and dynamic-inventory fences.
Never union conflicting selections into broader authority. Tool execution still
uses access scope + PermissionManager and extension/MCP trust gates.

### 6. Simplification serves these boundaries

Extract focused lifecycle, immutable preparation and persistence operations from
AgentSession where they have real shared callers. Consolidate native presenters
around the existing session event adapter; product session/queue policy stays in
Coding, not xdog.tui. Remove verified dead modules/fields and stale message-approval
help. Do not mechanically split methods into a new framework, delete supported SDK
entry points, or mistake composer state for a second execution queue.

## Verification / acceptance

For each defect: capture a failing regression, implement the boundary, then run
its focused suite. Test actual CLI/SDK routes and actual model Context, not just
helpers. Exercise cross-process leases with different TMPDIR, delayed admission
through supported navigation, actual extension worker cleanup failures, malformed
session snapshots while another writer is held, exact capacity/terminal receipt
settlement, and duplicate-skill/preparation/commit thread identity.

Run Coding and related Agent/Claw/Skills/TUI regressions, Ruff packages, strict
workspace typing, full unfiltered workspace tests and wheel/sdist inspection.
Independent final review must inspect original-owner IO, cleanup errors,
configuration trust/empty selections, supported project-context discovery and all new files.
Record exact commands, intermediate failures, decisions and requirement evidence
in docs/coding-owner-reliability-ledger.md. Completion requires all original
requirements, not merely a green subset. Existing user data remains untouched.
