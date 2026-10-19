# xdog-goal

Optional host-neutral Goal contracts and storage. Direct dependencies are
`xdog-agent` and Python's standard library; installing/importing the package does
not start a model, receiver, observer, worker or UI.

This package is being implemented under the approved assistant-core design.
The independent kernel precedes the explicit Claw/Coding adapter and shared UI
cutover; it does not claim those hosts have already adopted this implementation.
No legacy Goal data is imported or upgraded. A host supplies an opaque owner and
workspace; the package owns its private versioned storage and refuses a different
owner, corruption, unsupported format or concurrent writer.

The kernel provides explicit GoalService operations, bound worker reporting,
independent evidence-backed completion review, original role context and
disposable workspace views. Parent actions cannot submit a reviewer verdict.
Task submission is not acceptance, and acceptance is not Goal completion.
Only explicit request_complete starts review; status/idle/turn end do not.

Packaged session-scoped `goals` skill and worker/reviewer roles describe the
explicit workflow. Tool operation authority and readonly boundaries are supplied
by the host; instruction text or source labels do not grant permissions.
See `docs/goal-kernel-verification.md` in the repository for verification and
remaining actual host/UI adoption work.
