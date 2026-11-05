# Extension：只读视图与用户命令

本文合并同一主题的接口与约束。实际权限和资源生命周期仍由原宿主负责；
文中的历史测试数字只对应其注明的版本，不代表本次重新验收。


## Owner-bound extension views

The generic Agent extension protocol supports explicitly granted **read-only**
JSON snapshots. This is not a plugin service locator, UI approval, tool execution
interface, event broker, source handler or permission grant. It starts no Agent,
model, receiver, provider or extension worker by itself.

### Trusted host registration

An existing `ExtensionRuntime` owns its own `views` registry. A host component
registers a scoped read callback and an explicit owner-validity predicate:

```python
async def read_status(query):
    return {"revision": "1", "items": ["owner-projected data"]}

remove = runtime.views.bind(
    extension_id="status_ui",
    name="work",
    read=read_status,
    valid=lambda: original_owner_is_still_current,
)
```

`extension_id` is a trusted configured Python extension ID, not model/source JSON.
The actual process/client supplies request identity; the worker cannot select a
client, session, provider, method or owner through view routing fields. A name
alone grants nothing. The reader validates any view-specific query and returns
only data belonging to its bound owner. Registration is not worker initialization.

`remove()` revokes only that original binding, not a replacement later registered
under the same name. Registry close fences all bindings. Neither operation closes
the business service or cancels its tasks. Providers must remain read-only; trusted
Python code is not an OS sandbox, and the protocol cannot make an arbitrary host
callback harmless.

### Worker API

Configured command/event callbacks can use:

```python
value = await api.read_view("work", query={"limit": 1})
if value is None:
    api.status("work", "Unavailable")
else:
    api.status("work", str(value["items"]))
```

Views work without a TUI or interactive dialog handler. The existing bounded
pending-callback transport carries the read and its reply, but a view never enters
`DialogBroker` or invokes its approval handler. A worker's forged UI/view flag
cannot create a server grant. Other callback methods do not borrow this capability.
`None` means unavailable/denied, not a valid empty dataset; consumers must not turn
it into “zero tasks” or a completion claim.

Identifiers use the existing1..32 ASCII letter/digit/underscore bound. Requests
contain only name/query data; unknown routing fields are rejected. Query objects
are finite JSON <=4096 UTF-8 bytes. Snapshot objects are finite JSON <=65536 bytes,
with maximum nesting depth32; no runtime objects/key coercion, NaN or Infinity.
Copies protect original query/result data from subsequent mutation. A malformed
read fails unavailable rather than granting another owner or invoking tools.

### Awaited owner changes and publication

The registry checks the same binding identity and actual owner before and after
an awaited read. Transport also retains its real process InvocationLease. Every
view binding used by a callback is pinned until that callback settles: a valid
first read cannot be published after its grant changes while a later read awaits.
Late UI/entries/registration snapshots are dropped, uncommitted deferred actions
are discarded, and a stale command returns explicit unavailable text. This does
not roll back previously committed facts or revoke data already legitimately
observed by trusted code. No business mutation is retried or replayed.

### Change hints and cleanup

A host can call `runtime.notify_view_changed("work", revision)` after a committed
change. The hint has no business truth and carries no operation/owner selector.
It is routed on the existing actual worker owner loop, guarded by original client,
context/generation and binding. At most one pending hint per binding is retained
while a callback is running. An interested extension's `view_changed` callback
re-reads its authorized snapshot. Before initialization, after unload or after
close, hints do not start/reload a worker. No polling timer/model loop is created.

Unload clears display state and settles owned hint jobs; extension-runtime close
fences views and joins/cancels its actual hint callbacks before worker teardown.
A business adapter must revoke and settle its admitted data reads before releasing
its own service storage; the UI registry does not own that storage lifetime.

### Scope of this milestone

This generic foundation contains no Goal imports/schema or TUI renderer. The
shared packaged Goal projection/extension and actual two-host binding follow in
`docs/archive/superpowers/plans/2026-10-19-shared-goal-extension-views.md`; they are not
claimed implemented by this document. Real worker tests are in
`packages/agent/tests/test_extension_views.py`, including headless reads, forged
flags, cross-extension refusal, bounded codecs, in-flight revocation, callback
publication after binding replacement, dormant/unloaded lifecycle and coalescing.

### Foundation verification (2026-10-19)

- Actual view registry/worker tests17 passed; combined existing dialog/command/
  pending-message/hook regression38 passed. RED→GREEN includes missing API/grant,
  absent hint transport, and a real two-read callback publishing an earlier value
  after binding replacement. The actual latter disclosure is now refused before
  callback UI/result/entry publication and deferred action settlement.
- All9 unfiltered paths through `scripts/test.py --jobs 2`: **6784 passed,
  34 skipped**,155.72s. Agent1909/15 skipped, Coding1245/12 skipped, Claw1007,
  Flow813, AI729/7 skipped, TUI686, Eyes290, Goal63, Site42.
- Strict9 mypy588 source files, Ruff packages, Flow17 examples, lock/diff pass.
  Logs `extension-views-{red,hints-red,callback-pin-red,current-focused,root9,strict9,flow17}.log`
  under `/data/t.Vy5SaQ/`. Previous default4-process timing limitations remain
  documented in the Claw cutover verification; no test deadline/deselection change.

This is source/runtime-worker evidence for the generic foundation. Earlier
cutover artifact receipts belong to their own milestone; rebuilding/installing
this new protocol with the actual shared Goal UI is still required by Task4.
No user service/data, paid model, push, merge or publication was used.

### Restricted display contribution profile

A manifest may explicitly declare `display_only: true` (strict boolean). The
host reads valid identity/profile metadata before loading/checking the code entry.
A known display's syntax/module/process/view failure affects display, not business
validation. Unknown/unreadable/invalid identities/profiles still default required
and fail closed; they are not silently assumed to be UI.

A display cannot register tools/providers, pre/post/context/input or non-display
lifecycle hooks, persist conversation entries, return host-action proposals or
open session-control/export dialogs. The allowed lifecycle is session_start/end
and view_changed, with text commands, basic UI and explicitly granted readonly
views. Initial and live declarations are enforced by the host, not SDK goodwill.
Existing/default extensions retain their guard authority/failure vetoes. A failed
display does not disable an actual required guard or change tool permissions.

## Public slash commands from extensions

An extension can expose a user-facing command without exposing its internal
extension ID. This does not register a model tool or change extension trust.

```python
api.command('inspect', 'Read current state', callback,
            public_name='show-state', complete=complete_arguments)
```

Users type `/show-state`. The worker callback is still registered as `inspect`
and belongs to the actual configured extension client. Existing commands without
a public name retain `/ext-<extension>-<command>`. Fully qualified command IDs
remain accepted for internal routing/debugging; they are not extra public menu
entries when a public name is declared. Rich result attribution and keybinding
identities remain fully qualified, not derived from the public spelling.

### One catalog

Command discovery, execution, help, argument completion and shortcuts use the
same live declaration. A public name is lowercase ASCII, starts with a letter,
is at most32 characters and otherwise uses letters, digits, `_` or `-`. Slash,
whitespace, empty names and the internal `ext-` prefix are not accepted.

`public_name` is optional data in a command snapshot. Host snapshots include
`command_owners` when public names exist. Readers validate the declared active
extension and reject attempts to reassign a qualified name to another extension.
Actual callback/client/session binding still enforces authority; metadata labels
alone never grant a host operation or model turn.

Replacing/unregistering commands updates their live revision and completion IDs.
Old public names disappear from discovery; stale argument completions cannot
invoke the renamed callback. Reload/unload uses the existing worker lifecycle.

For shortcuts:

```python
api.shortcut('inspect', 'f6', 'Read current state', callback,
             public_name='show-state')
```

The shortcut displays/runs `/show-state`, but an explicit user keybinding such as
`ext-example-inspect: alt+x` still binds that original extension+command identity.
It cannot override existing editor/approval/exit keys. Command availability or
completion never creates a worker or invokes the business callback by itself.

### Collisions and host integration

Hosts reserve their builtin commands and installed skill names. Discovery of
those filesystem skill names runs in owned preparation IO. Extension public
names may not shadow these names or another registered extension command. Initial
or live conflicting declarations fail explicitly with diagnostics, never overwrite
an existing command or become an implicit skill/model prompt. If a new skill is
added after activation, only the conflicting command is quarantined: help, status,
unload and the rest of the catalog remain usable. Invoking the conflicting name
reports the conflict instead of silently switching to the new skill. Remove the
conflicting configuration or explicitly unload the extension to resolve it. Both Coding and Claw route
public names through this generic catalog; neither hardcodes Goal command logic.

Terminal clients and hosts must understand public command snapshots to offer the
new names; old qualified-only snapshots remain valid. Host snapshot validation
rejects a public command without explicit active-owner attribution.

### Goal display

The packaged display-only `goal_ui` declares:

```text
/goals                 list Goals in the current authorized view
/goals GOAL_ID         filter to a specific Goal
/goals page OFFSET     read from the specified offset (not a page number)
```

`/goal` remains the packaged skill name, not the display command. `/goals` is
available when `goal_ui` is enabled and its worker registered; package installation
or skill discovery does not start a display worker. The command reads the existing
owner-bound view and refreshes status, never starts a model or changes Goal/task/
review/notification state. Goal's model-facing tools are unchanged.
