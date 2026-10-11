# UI-043 canonical custom delivery — integrated contract

Checkpoint: October 11, 2026, after the staged implementation was reviewed and
three-way integrated into the working tree. This is one part of UI-043, not a new
requirement. User-message APIs are **not** used as a substitute for canonical custom
messages. The final frozen whole-workspace gate remains separate.

## Named behavior

- `request_context_message(custom_type, content, display=True, details=None,
  deliver_as='next_turn', trigger_turn=False)` retains the original callback lease,
  original session/branch, explicit false-default whole-content review, and ordinary
  tool permission policy. Text and explicitly supplied typed images are bounded and
  validated against the current model. Drafts/uploads are never borrowed.
- `next_turn` is non-triggering. It captures an admission watermark before user
  lifecycle hooks and waits for the next successfully admitted **user** prompt;
  extension/system input and rejected/handled prompts do not consume it.
- Active `steer` / `follow_up` use the original Agent queues at their existing batch/
  stopping boundaries. The canonical message stays `CustomAgentMessage`, including
  type/display/opaque details. Validation and canonical persistence precede another
  model call. Original-loop admission is explicitly enforced with `settle_on`.
- Idle `steer` / `follow_up` append canonical custom context without a model call
  when `trigger_turn=False`. A true trigger continues canonical history through the
  existing Agent continuation pipeline, without fabricating a user message or
  creating a second tool loop. Claw uses an internal `ContextContinuation`, not an
  externally accepted user/scheduler payload.
- Model projection includes content and extension provenance, not opaque details.
  `display=False` changes UI presentation, not model visibility. Summaries project
  the supported custom type's content only; unrelated custom records remain filtered.
- Remote delivery modes require a separate default-off capability. Complete command
  result delivery and settled canonical persistence precede continuation. Claw grants
  are connection/source/generation bound and single-use, reject extra content and
  images, and cannot adopt a foreign session. Coding assigns a producer family so
  unrelated user-message capability changes cannot cancel a custom continuation.
  Competing reviewed message/navigation and custom triggers cannot overwrite the
  tracked producer or start two model loops.
- Original-context unsupported child/headless callers remain unavailable rather
  than obtaining a parent editor, session, continuation, or permission grant.

## Integrated refinements and evidence

The 23-file staged patch was merged against its preserved baseline rather than
copying whole host files over newer changes. Main's ACK-generation repair, stopped-
writer guard, RPC shutdown repair, image-origin isolation and native initialization
fixes were preserved. The staged base/manifest/patch and pre-merge copies remain in
`/tmp/ui-custom-delivery-stage/`.

Main then added original-loop active admission assertions, RPC producer-family
isolation and competing-trigger checks. Physical testing found a genuine Claw reply
index error: continuation appends no user message, so response extraction must begin
at the prior history length rather than prior length plus one. Both the response
threshold and extraction offset were corrected; normal user turns retain their old
boundary.

Evidence (overlapping suites; do not add these as requirements):

- Staged final new-mode and existing-contract gate:94 passed,74.28s;
  `/tmp/ui-custom-delivery-stage/handoff-final.log`.
- Main integrated modes plus native navigation/initialization:105 passed,58.67s;
  `/tmp/ui-custom-delivery-stage/root-integration-tests.log`.
- RPC custom mode, capability/producer isolation, original reviewed messages and
  nextTurn delivery fences:54 passed,65.20s;
  `/tmp/ui-custom-delivery-stage/root-continuation-isolation.log`.
- Current Linux physical matrix:12 passed,34.61s; both clients × both delivery modes
  × idle non-trigger/idle trigger/active tool. It checks canonical disk before model,
  no fake user history, unchanged nonempty draft, whole review, resize, focus/cursor
  and termios. `/tmp/ui043-physical-custom-final.log` and
  `/tmp/ui043-physical-evidence/`.
- Real Claw socket grant and transport matrix:24 passed,26.63s; forged/foreign,
  stale/group/session/source/generation, replay, withdrawal, extra payload, busy,
  denied/failed callback, legacy/default-off and blocked/failed result/commit
  delivery. `/tmp/claw-custom-grants-repaired.log`.

These results are not an assertion that the final whole-workspace run passed.
The earlier root run found separate regressions and became diagnostic when its
source freeze was explicitly invalidated for confirmed repairs. A fresh complete
run against the final source manifest is mandatory. Native non-Linux validation
remains deferred; live provider/deployment/desktop and physical storage-fault
requirements are not waived or represented by local fixtures.
