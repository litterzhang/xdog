# UI-043 — explicit images in idle extension-requested user messages

Date: 2026-10-11 (+08). Source/test freeze confirmed after the narrow projected-
generation repair at17:02, before main's17:03 complete-root snapshot. This is the
idle `request_message` contract, not busy queued input, custom next-turn context,
provider auth, or after-run compaction. Those implementations were left intact.

## Implemented contract

- `request_message(text="", *, images=(), timeout=300)` accepts an explicit tuple
  of typed `ImageContent` values, including image-only input. Empty text **and** no
  images still refuses. The existing nonempty `message_request.text()` validator
  used by other callers is unchanged.
- `message_request.proposal/prepare` validates inline PNG/JPEG/WebP image data,
  at most4images,4MiB each and6MiB serialized proposal budget. No URI/file/upload/
  composer references, other input scopes or caller-supplied host reviews are
  accepted. `PreparedMessage` retains typed bytes privately with immutable content
  and digest; its review carries only labeled text, MIME/byte-count/SHA256 summaries,
  a host token, and a digest. Changes to the reviewed text/descriptors refuse.
- Both native and RPC hosts validate the original current model before review and
  revalidate ownership/model suitability before ordinary producer admission.
  Native Coding converts only those private validated bytes into native
  `Attachment` objects, off the UI loop; it does not inspect or consume composer
  images. RPC/Claw retain typed images in their original private message grants.
- The existing command-success and complete-result-delivery fences remain. A
  failed/canceled command, stale session/model, withdrawn capability or failed
  result write cannot start a model. Queued acknowledgment is not execution.
- Native/RPC dispatch retains `input_origin='extension'`; Claw canonical input
  likewise carries that metadata. It therefore does not consume pending custom
  next-turn context. Main's separately owned producer-origin guards prevent failed
  extension admission from restoring either extension text or images into the
  user's composer. This worker did not edit queue/context/compaction code.
- Remote `extension_message_images:1` is independently negotiated and default-off,
  in addition to ordinary message support. Old text-only clients are refused before
  image review. Image-generation withdrawal retires pending image review/grants and
  cancels an owned active image producer, without silently downgrading to text.
- Shared `ExtensionNavigation` renders the complete host-derived text and image
  descriptors in its existing full-paint confirmation flow. Image bytes are absent
  from ordinary broker review/native terminal/RPC UI output, while the approved
  canonical model input and saved user message retain the typed images.

## Changed paths and ownership

Owned production changes:

- `packages/agent/src/xdog/agent/extensions/message_request.py`
- `packages/agent/src/xdog/agent/extensions/api.py` — only the explicitly authorized
  `request_message` method; no changes to other API methods.
- `packages/coding/src/xdog/coding/modes/interactive/message_inputs.py` — new
  private grant/validation adapter.
- `packages/coding/src/xdog/coding/modes/interactive/interactive_mode.py` — only
  message review/admission wiring; preserves main's producer-origin flags.
- `packages/coding/src/xdog/coding/modes/rpc/extensions.py` — image capability and
  idle-message-only review/admission branches.
- `packages/tui/src/xdog/tui/components/extension_navigation.py` — messaging
  descriptor rendering only; other operation branches retained.

Own tests: agent `test_idle_message_images.py`, Coding
`test_idle_message_images.py` / `test_idle_message_images_terminal.py`; updated
`test_extension_message_request.py` and the `messagerequest/main.py` fixture.
Explicit empty image lists are now valid “no images,” replacing the old blanket
rejection assertion; invalid images/references/foreign scopes remain rejected.
The Claw owner changed its three host files/private grant path and supplied
`test_extension_message_images.py` / `test_extension_message_images_terminal.py`.
Main owns origin-isolation queue/error restoration changes and their tests.

## Confirmed regression and narrow repair

The initial stricter codec rejected Claw's `message_generation` and
`message_images_generation`, which the gateway adds **after** preparing the
binary-free host review. This made the actual Claw TUI reject valid dialogs. The
Claw owner confirmed the exact fields; the repair was communicated before freeze.
Only host-projected reviews may carry these fields, as exact nonnegative integers
below2^53. Raw worker proposals still cannot supply them, and no image-body or
ownership validation was relaxed. The repaired codec/Claw gate passed21tests in
13.09s (`/tmp/ui043-claw-projection-regression.log`).

Other red evidence was retained: the first native image run exposed the actual
`Attachment` versus `ImageContent` producer boundary, fixed in the new private
adapter; native form tests initially omitted the transition into review; physical
fixtures initially typed before terminal readiness and expected the text fixture
reply rather than the image fixture's `IMAGE-RECEIVED-1`. Corrected fixtures retain
full review, typed image, raw-byte privacy, cursor/main-buffer/termios assertions.
`/tmp/ui043-idle-images-final.log` retains the old mixed failure snapshot; it is not
represented as passing. Main's origin-restoration guard is independently exercised
by the new admission-failure case, **1passed**, with composer identity unchanged.

## Frozen evidence

Final current group: **56passed, no skips,64.86s**,
`/tmp/ui043-idle-images-frozen.log`. It includes:

- explicit image-only and text+image codec, malformed/ref/forged review rejection;
- actual Coding and Claw native full-descriptor review, denial/callback failure,
  model change, old remote capability, withdrawal and full-result write failure;
- RPC private image retention and delayed model admission behind result delivery;
- main's failed-admission origin guard, preserving the original composer;
- both hosts' actual Linux physical image-only approve/deny paths with typed saved
  input, parent editor/cursor/main-buffer/termios restoration and no base64 image
  body in terminal output; previous text-only message regressions.

Focused Ruff and strict **6production modules** mypy are clean in
`/tmp/ui043-idle-frozen-static.log`. The11 owned source/test paths are hashed in
`/tmp/ui043-idle-images-source.json`. Main's authoritative full-root snapshot is
`/tmp/ui-final-20261011/source-before.json` (1415source/test files); its ongoing root
run and final artifacts are **not** claimed complete by this worker.

No known finite feature gap remains in this image slice after the repaired
56-case gate. Full-root/global lifecycle/static/artifact/examples acceptance remains
main-owned and pending. No installs, live provider/auth calls, external deployment,
composer/upload consumption, user-service restart or new platform deferral was used.
Non-Linux native and authorized live-provider evidence remain their explicitly
separate goal requirements, not silently passed by these local tests.
