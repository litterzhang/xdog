# Independent MCP serving-SDK interoperability peer

The peer imports the independently published `@modelcontextprotocol/server` and
`@modelcontextprotocol/core` **2.3.1**, not XDOG's own wire implementation. Stdio
uses `serveStdio`; HTTP uses `createMcpHandler` on an owned loopback listener.
The schema corpus is generated through the SDK's public validators.

No application or normal test invocation downloads this dependency. To run the
opt-in checks, create an isolated temporary installation (outside the workspace):

```sh
root=$(mktemp -d)
npm install --prefix "$root" --ignore-scripts --no-audit --no-fund --save-exact \
  @modelcontextprotocol/server@2.3.1
XDOG_MCP_OFFICIAL_SDK_ROOT="$root" uv run pytest -q \
  packages/agent/tests/test_mcp_official_sdk.py \
  packages/coding/tests/test_mcp_resource_watch_ui.py \
  packages/claw/tests/test_mcp_resource_watch_ui.py
XDOG_MCP_OFFICIAL_SDK_ROOT="$root" uv run pytest -q \
  packages/coding/tests/test_terminal_acceptance.py \
  -k 'actual_mcp_resource_watch_notice and official-sdk'
```

Without an explicit SDK root or Node runtime, those cases skip; skips are **not**
interop evidence. The temporary SDK is not vendored, added to production metadata,
or used for model/authentication calls. Tests cover discovery/catalogs, explicit
watches, notices, unwatch ownership, graceful teardown and actual client UIs.
They do not establish conformance for every MCP feature, OAuth deployment or OS.

The peer additionally registers a completable prompt and resource template. Its
prompt returns a standards-valid resource link with annotations/vendor metadata;
its callbacks record explicit reads/retrievals so the tests distinguish completion
and preview from automatic content redemption. `test_mcp_official_sdk.py` exercises
these paths over both transports. The real-terminal SDK case also walks prompt
completion, distinct preview retrieval, full-metadata search and unsubmitted draft
insertion in both clients.

`content_cases.mjs` constructs text/audio/embedded-text/blob/resource-link cases
through the independent SDK's `ContentBlockSchema`, including annotations and
opaque metadata, and checks invalid members with `safeParse`. The owned peer's
mixed-content tool exercises the actual serving SDK over both transports, records
one explicit call, and proves there is no implied resource/template/prompt read.
These are bounded content/metadata checks, not audio playback or full conformance.

## Current named SDK matrix (UI-051, 2026-10-10)

The two native owner tests now explicitly cover **both stdio and HTTP** against
this independent SDK, not just the transport manager. Coding exercises watch /
notice / stop / explicit restart / server end / view close; Claw exercises the
same owned gateway notice/end/close paths. Both retain the parent draft, reject
late view notices, and perform no resource read or model turn. The HTTP peer is
an async fixture with cleanup even if application setup/assertions fail; test-only
control calls use required `Mcp-Name` mirrors. No production policy is loosened.

Reusing the existing isolated installation, the current complete named suite
(manager discovery, schema/content, prompt/template completion, errors, inert
metadata, URI/catalog/combined watches, both client owners) reports **26 passed,
no skips** in `/tmp/ui051-sdk-final.log`. Actual Linux terminal SDK watch/unwatch,
explicit prompt completion/preview/insertion, native parent cursor/termios recovery
in Coding and Claw reports **2 passed,239 deselected** in
`/tmp/ui051-terminal-final.log`. These terminal cases use stdio; HTTP native-owner
behavior is tested above, not claimed as an actual HTTP terminal case.

This closes that bounded current SDK evidence requirement only: it does **not**
close UI-048 remaining capabilities, UI-057 external OAuth/MCP deployments,
UI-049 final terminal matrix or UI-055 final full workspace regression, and does
not claim other SDK versions, all providers, or non-Linux native verification.
