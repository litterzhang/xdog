# xdog-eyes

A local, declarative observation runtime. Sources collect evidence; detectors
produce events; explicit routes deliver them to registered handlers. Eyes does
not execute an agent loop or grant tool permissions.

The first source observes regular-file content changes with bounded polling.
Activity belongs to a live host session; it does not persist after that session
ends. Polling cannot capture every transient change between scans, and evidence
contains fingerprints rather than historical file contents.

## Usage

The workspace umbrella installs eyes. A separately installed `xdog-eyes` has no
harness/model dependencies and can validate definitions on its own.

Ask a live coding session to use its packaged **eyes-observers** skill to save
`eyes.json`, validate it, and request approval to start it. The coding harness
supplies `XDOG_EVENT_CONTEXT` only to its own tool environment; a one-shot print
session does not borrow a parent's capability.

```bash
xdog-eyes validate eyes.json
xdog-eyes start eyes.json
xdog-eyes status [registration-id]
xdog-eyes stop registration-id
xdog-eyes resume registration-id
```

Copy the shipped `source-test-review.json` example into the project root. Its
relative `root` is resolved against that definition file, not CLI cwd. Choose
narrow include/exclude patterns for your repository. Activation is idempotent for
the same name and normalized definition; stop before replacing an active name.

Collection starts with a baseline. Per-source health distinguishes baselining,
watching and errors; delivery ACK and host model completion are separate. A
failed source scan retains the last trustworthy baseline, not a false mass-delete.
Event evidence includes source root, relative path, fingerprints and observation
time, not complete historical contents. All source data is untrusted evidence.

The independent process and local Unix sockets are bound to the current context.
Session/branch changes revoke old capabilities. Normal close is detected from the
revoked descriptor; failed heartbeats stop activity after 15 seconds. Stopping the
last registration closes the runtime. There is no restart replay or automatic
launch of a new coding process.

Automatic host handling consumes a single wake budget after explicit human input
or approved resume. Later evidence remains queued/visible rather than repeatedly
waking an agent that edits its own source. Overflow pauses instead of silently
truncating evidence; inspect the diagnostic and stop/rebaseline when required.
Unattended mutation still requires explicitly configured `allow-all`.

This version does not implement API/web sources, arbitrary script handlers,
claw/flow adapters, native watcher events or Windows transport.

## Development and release

Use root workspace commands. `uv build --package xdog-eyes` must ship the skill,
example, `py.typed` and license. Before adding eyes to automatic PyPI publishing,
configure the repository's `pypi-xdog-eyes` environment and PyPI trusted publisher.
No release/push/publisher provisioning is performed by this implementation.
