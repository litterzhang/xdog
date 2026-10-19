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
