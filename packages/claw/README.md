# xdog-claw

**Agent orchestration runtime.**

Runs and supervises long-lived agent sessions: scheduling, a gateway, tool
memory, and persistence backed by SQLite.

## Interactive terminal

`uv run xdog-claw tui` connects to the configured gateway using the main terminal
buffer and native scrollback. It shares Coding's Unicode multiline editor and
bounded layout. Goals/todos summarize retained state; queued messages keep their
full text while only a compact preview appears.

Ctrl+O opens recent tool/reasoning details. Left/Right changes entries and Page
Up/Page Down scrolls. Escape closes autocomplete/details before cancelling a
run. Cancellation and busy rejection restore submitted and queued text ahead
of a newer draft; input submitted during cancellation stays editable. Ctrl+Z
suspends on POSIX. Remote coding and fullscreen application modes are excluded.
See [terminal verification](../../docs/tui-pi-parity.md) for tests and limitations.

## Messaging replies

Messaging channels such as WeChat send each completed assistant text message as
it becomes available, including progress messages between tool calls. They do
not wait for the whole agent turn or resend an aggregated response at the end.
Reasoning, tool arguments/results, and empty messages are not sent as replies.
Long messages still use the normal channel chunking. WeChat's typing indicator
stays active until the turn finishes.

This is message-level delivery, not token streaming. WeChat polls for new
messages independently while the agent works. A FIFO inbox holds up to 50
pending messages, and a single worker dispatches them in arrival order through
the orchestrator. A later arrival does not change the active message's reply
recipient or typing indicator. When the inbox fills, polling pauses until space
is available rather than dropping messages.

The inbox is in-memory, not a durable job queue: stopping/restarting the gateway
cancels the active handler and discards pending messages. Wait for replies
before restarting; messages left pending may need to be resent.

## Image generation with xdog-ai

The `generate_image` agent tool and `xdog-claw generate-image` command generate
local image assets through `xdog.ai.Runtime.image_generation()`. Authentication,
model capabilities, and wire protocols belong to `xdog.ai`; claw only confines
paths to its workspace and saves the returned images. The image model is
independent of the agent's main chat model. Neither model has a hardcoded default;
choose both explicitly during onboarding.

Configure the tool and its model:

```bash
uv run xdog-ai login antigravity
uv run xdog-claw onboard
```

After selecting the primary model, **Tool Setup** lists the registered tools.
Enter their names or numbers separated by commas, `all`, or `none`.
If you enable `generate_image`, the same step requires an image model chosen
from the synced catalogue. Only models whose image-output support is explicitly
true (`image_generation`) are offered; image-reading support alone is not enough.
If none are available, leave the tool disabled and authenticate/sync a supporting
provider before rerunning onboarding.

Onboarding saves `enabled_tools` and `image_model` in `config.yaml`. For example,
after explicitly selecting these values:

```yaml
model: copilot/gpt-6-astra
enabled_tools:
  - filesystem
  - bash
  - generate_image
image_model: antigravity/gemini-3.1-flash-image
```

`enabled_tools: []` disables every agent tool. Old configurations without the
field retain the previous ordinary tool set, but image generation is opt-in.
The wizard preserves existing channel configuration and unrelated groups.
Both the agent tool and `xdog-claw generate-image` use the configured image
model. The LLM cannot override it in tool arguments. Image support is checked
again before generation; no arbitrary chat model is used as a fallback.

No proxy server, proxy key, or `CLIPROXY_*` environment variables are used.
The gateway and login command must run as the same OS user to use the same
XDOG credential store. Restart an existing gateway to load this tool change.
Reference images and prompts are sent to the selected provider.

Generate a UI mockup from a shell or another coding agent's shell tool:

```bash
uv run xdog-claw generate-image \
  "Design a Chinese WeChat coffee shop home screen with warm neutral colors" \
  --workspace . --output-dir design --aspect-ratio 9:16 --image-size 1K
```

Rerun onboarding to select a different image model. Use `--config PATH` to
choose another configured profile. For an explicit one-off provider/model
selection independent of claw's tool settings, the standalone command is
`xdog-ai image antigravity gemini-3.1-flash-image "your prompt" --output-dir design`.

Edit or use an existing image as a reference:

```bash
uv run xdog-claw generate-image \
  "Keep this layout, improve spacing, and change the accent color to green" \
  --workspace . --reference design/home.png --output-dir design/variants
```

The command prints JSON with absolute `paths` and optional accompanying `text`.
Actual files have unique names and extensions matching PNG, JPEG, or WebP data;
existing assets are not overwritten. The agent tool takes the same options,
with `reference_images` as an array. It resolves paths against the group's
workspace. All reference and output paths must stay within that workspace.

Defaults are `generated-images`, portrait `9:16`, and `1K`. Supported sizes are
`1K`, `2K`, and `4K`, subject to the model's capabilities. Up to four local
reference images are accepted, each at most 10 MiB. Requests have a 180-second
deadline; claw saves at most four images and 32 MiB of output per call.
Failed requests are not retried
automatically because image generation may consume quota.

This saves assets locally; it does **not** upload them as WeChat picture
messages. A missing provider login, unsupported image model, or upstream
refusal is reported as a tool error rather than falling back to text chat.

## Part of xdog

This package is one piece of [xdog](https://github.com/litterzhang/xdog), a
local-first toolkit for building, running and scheduling LLM workflows. The
centrepiece is [`xdog-flow`](https://pypi.org/project/xdog-flow/) — a typed
workflow format and compiler.

Documentation: **https://xdog.942295.xyz**

## Licence

Copyright (c) 2026 HugeMan <942295.xyz>

GNU Affero General Public License v3.0 or later — see
[LICENSE](https://github.com/litterzhang/xdog/blob/main/LICENSE).
