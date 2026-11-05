> **历史归档，不是当前接口、待办或执行授权。**
> 原日期、结论和验证范围保留；当前入口见 [文档索引](../../README.md)。

# One agent, many channels — separating who you are talking to from how

Status: **design, not built** · Prompted by: a WeChat message creating a second
agent with a second memory and a second persona

## The problem, precisely

`group_id` does two jobs at once:

| Job | Used by |
|---|---|
| **Conversation identity** — which session, workspace, MEMORY.md, IDENTITY.md, goals, scheduled tasks | `GroupRuntime`, `SessionManager`, everything under `groups/<id>/` |
| **Delivery address** — which person on which channel gets the reply | `WeixinChannel._user_id_map: group_id → wechat user id` |

Because they are the same string, a new channel or a new peer *necessarily*
creates a new conversation. `_weixin_user_id_to_group_id("abc@im.wechat")`
returns `weixin:abc-im-wechat`, `auto_register_group` creates it, and
`init_workspace` seeds it with default identity files — which is why the agent
answered "my identifier is weixin:o9cq…" and offered to be given a better name.
Nothing was misconfigured. The name it read was the routing key.

## The design

**A group is a conversation. A channel is a way to reach it.**

An inbound message carries both, and they are no longer the same field:

```python
@dataclass(frozen=True)
class InboundMessage:
    group_id: str        # which conversation this belongs to
    origin: Origin       # where it came from, and where a reply goes back
    text: str

@dataclass(frozen=True)
class Origin:
    channel: str         # "weixin", "tui", ...
    peer: str            # the channel's own address for that person
```

Three consequences:

1. **The session is keyed by `group_id` alone.** TUI and WeChat land in the same
   history, the same MEMORY.md, the same persona. Asking on your phone continues
   what you said at your desk, which is the point.
2. **A reply follows the message it answers**, via `origin` — not by looking the
   group up in a channel-owned map. `_send_to_channels(group_id, text)` becomes
   `reply(origin, text)`. This is what makes many channels per group possible at
   all: the current broadcast has to guess, and guesses right only because each
   group has exactly one channel today.
3. **`_user_id_map` stops being routing state** and becomes what it always was:
   the channel's own address book, consulted by `origin.peer`.

## The decision this needs, and why it is not mine to make

If every WeChat sender maps to the same group, **anyone who messages the bot is
talking to your assistant, with your memory**. They can ask it what it knows
about you and it will answer, because that is one conversation now.

So a peer has to be *admitted* to a group. Three ways, in increasing safety:

| | How | Cost |
|---|---|---|
| **First sender wins** | the first peer to talk on a channel is bound to `main`; later peers get their own group | zero setup; whoever messages first owns it, and on a public bot that is not necessarily you |
| **Explicit bind** | `xdog-claw channel bind weixin <peer> --group main` | one command per channel; you have to find your own peer id first |
| **Allowlist in config** | `weixin_owner_ids: [...]`, unknown senders refused | editing a file; safest, and unknown peers get a clear refusal rather than a private group |

Unbound peers should get **their own group, or nothing** — never the owner's.
The failure to avoid is silent: a stranger's message answered from your memory,
with no signal that it happened.

### Status: shipped without an answer, deliberately

The channel→group binding landed (`channel login --group`, default `main`) and
**no admission check came with it**. Every sender on a channel reaches the bound
group. This was an explicit decision, not an oversight: the bot id is private
and unshared, so the exposure is theoretical today.

It stops being theoretical the moment that id is given to anyone, or the bot is
listed anywhere. The mitigation is already half-built — the peer-change log line
in `_on_inbound` fires whenever a different sender appears, so the arrival of a
stranger is visible in the journal even though it is not refused. If this is
picked up later, "allowlist in config" above is the row to implement.

## What else moves with the group

Merging channels into one group merges everything keyed by it, which is the
intent but worth stating: memory, conversation history, goals, scheduled tasks,
and the group's `skills/`. A scheduled task created "in WeChat" then fires for
the one conversation, and its output goes wherever the reply for that turn is
addressed.

## Migration

`groups/weixin:o9cq…-im-wechat/` already holds real history. Two honest options:

- **Abandon it.** Simplest, and loses whatever was said there.
- **Merge it into `main`.** Requires deciding how two conversation histories
  interleave, which is a real question with no obvious answer — timestamps
  produce a transcript that never happened.

For a few turns of history the first is better. Say so in the release note
rather than quietly dropping a directory.

## Effort

| Step | Work |
|---|---|
| `Origin` on inbound/outbound, `reply(origin, …)` replacing broadcast | 1d |
| Channel resolves peer → group through a binding table, default `main` | 0.5d |
| Bind command + config allowlist, and a refusal path for unknown peers | 0.5d |
| Tests: two channels one session; a reply goes only to its origin; an unbound peer is refused | 0.5d |

The third row is the one that must not be skipped. Without it this design is a
privacy hole with better ergonomics.
