# UI-049 / UI-055 child-image test readiness repair

Checkpoint: October 11, 2026. This is a test-only receipt correction, not an image
transport or production permission change.

The current root identified
`test_actual_child_file_review_start_queue_persistence_and_parent_draft_ownership[68-claw]`.
The exact unchanged test independently reproduced its first-start timeout. A private
trace established the sequence:

- Opening the child browser issued roster and preset requests and marked it busy.
- The tiny image loaded quickly. The test sent its fully painted final Enter at
  about8ms while that initial browser work was still pending.
- The existing busy guard correctly refused submission. No child start, image
  upload, queue operation or model call occurred.
- The original roster receipt arrived around50ms. The test did not submit another
  Enter, so it remained in confirmation until its deadline.

The test had awaited file loading, but not the independent initial roster/presets
receipts. Automatically replaying that earlier Enter or removing the busy guard
would be an incorrect production fix.

## Correction and evidence

Only `packages/coding/tests/test_child_image_submission.py` changed: after opening
and capturing the child panel, the positive workflow explicitly awaits both a
non-busy panel and no pending child-control receipts before entering the new-child
form. All subsequent file bytes, full-review, unpainted input refusal, independent
child/parent ownership, persistence, queue and recovery assertions remain unchanged.
The existing wait deadline is unchanged.

A private copy with this one readiness barrier passed all20 module cases under a
controlled150ms delay in the real gateway's roster/presets handlers. The original
node failed naturally and with the delay. After the approved change was applied to
the repository, all20 cases passed in12.40s:
`/tmp/claw-child-image-diagnostic/repository-green.log`.

Causal traces, the original failures, private delayed verification, proposed diff
and hashes are retained in `/tmp/claw-child-image-diagnostic/HANDOFF.md` and its
neighboring artifacts. No production code or guard was changed, no timeout was
increased, and the running full-root test process was not interrupted. Its imported
old test and now-invalidated source manifest are diagnostic evidence, not a passing
current-source gate. A fresh complete-root invocation remains required.
