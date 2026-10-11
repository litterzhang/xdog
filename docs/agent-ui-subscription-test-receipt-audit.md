# UI-054 / UI-055 subscription cleanup receipt audit

Checkpoint: October 11, 2026. No production MCP behavior is changed here.

## Observation and reproduced ordering defect

The running presentation-final root reported a failure in
`test_owned_stdio_watch_changes_shared_legacy_and_exact_cleanup_no_resource_read[modern]`.
Its complete traceback is still pending; the following is independently reproduced
against that same test rather than inferred solely from the progress marker.

`StdioClient.stop_resource_watch` removes the owned local watch and writes a
`notifications/cancelled` notification. A completed local pipe write does not prove
that the peer has processed and logged the notification. The test immediately read
the peer's log after detaching the final watch, then required exactly two cancellation
records. That ordering can observe only the first record.

A `/tmp`-only copy of the real fixture, delayed by30ms before processing cancellation
notifications, reproduced the unchanged exact test's count assertion at line49:
`/tmp/ui-subscription-receipt/red.log`. The final peer log contains both cancellations
once cleanup has settled; that later log is not misrepresented as the earlier read.
The original isolated fast-peer test also passed, demonstrating why that pass alone
did not establish the absence of this race.

## Stronger test contract

The owned fixture now supports a read-only `fixture/status` request. Because it is
processed after preceding notification lines, its response establishes actual peer
processing, and reports both active modern and legacy subscription counts.

Tests now require both counts to be zero before taking the final log snapshot. They
retain the exact subscribe/listen/unsubscribe/cancel counts, original-owner checks,
local empty-watch assertions, and no-resource-read/no-implicit-catalog-read checks.
A deliberately slow-cancellation peer is an additional parameterization, not a
longer timeout or a retry. Combined and catalog-watch cleanup tests use the same
explicit remote-state evidence; unsupported acknowledgement cleanup is also checked.

Changed files:

- `packages/agent/tests/fixtures/mcp_subscription_peer.py`
- `packages/agent/tests/test_mcp_subscriptions.py`
- `packages/agent/tests/test_mcp_combined_watch.py`
- `packages/agent/tests/test_mcp_catalog_watch.py`

The three affected test modules pass **88 tests, no skips** in3.27s:
`/tmp/ui-subscription-receipt/related-green.log`. Focused Ruff passes. No production
API, timeout, cancellation policy, permission, or resource-read behavior was changed.
The running full-root process was not interrupted. Its source freeze is invalidated
by these test/fixture changes, so it remains diagnostic and does not satisfy the
required fresh complete-root gate. Actual completed failures must still be reconciled.
