# xdog-eyes

A local, declarative observation runtime. Sources collect evidence; detectors
produce events; explicit routes deliver them to registered handlers. Eyes does
not execute an agent loop or grant tool permissions.

The first source observes regular-file content changes with bounded polling.
Activity belongs to a live host session; it does not persist after that session
ends. Polling cannot capture every transient change between scans, and evidence
contains fingerprints rather than historical file contents.
