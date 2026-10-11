# Agent CLI reference baseline and remaining applicable gaps

> **2026-10-10 用户范围更新 / 当前计数权威入口**
> 当前范围、完成条件、稳定需求编号及分类数量以
> `agent-ui-requirements.json` / `agent-ui-requirements.md` 为准。
> 本文以下内容是保留的历史审计/证据，不再把重复 OPEN 当当前独立需求。
> 仅非当前 Linux 的原生验证延期且非阻塞；不声称通过。
> Linux 功能、安全、生命周期、传输、终端与最终完整回归不缩减；
> 非 Linux 后端实现与原生验证分开记录。复算：`python tools/agent_ui_checklist.py --check`。


Inspected **2026-10-09**. This is a documentation/source snapshot, not a claim of
conformance to an installed/upstream latest release. Keep the original goal's
union of applicable features intact; OS-enforced sandboxing remains excluded.

## Retrieved primary references

- Official Codex CLI features: <https://developers.openai.com/codex/cli/features/>
  Covers interactive terminal work, model selection, images, sessions and approval
  behavior. Approval policy is not equivalent to an OS sandbox.
- Pi's author-maintained extension documentation:
  <https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/extensions.md>
  and its raw document at
  <https://raw.githubusercontent.com/badlogic/pi-mono/main/packages/coding-agent/docs/extensions.md>.
  Describes hook/provider/tool/command/UI APIs and different interactive/RPC/headless
  behavior, including extension dialog support in RPC and unsupported custom terminal
  components there. Use the documented supported surface, not assumed identical APIs.

The earlier failed fetches remain historical failures. The above pages WERE fetched
successfully in pass 134; that does not retroactively turn an older unavailable page
into evidence, pin a commit/release version, or verify XDOG interoperability.

## Local reference installations (read-only version commands)

- `codex --version`: **codex-cli 0.160.0**.
- `claude --version`: **2.1.276 (Claude Code)**.
- `command -v pi`: no installed Pi executable found on the current PATH.

These are installed versions, not claims that they are the latest upstream releases.
No model/login/authentication/session call, dependency install or user daemon restart
was performed to inspect them.

## Actual XDOG source findings, not upstream assumptions

- Coding's RPC adapter had its own pre-turn sequence and skipped the canonical
  `AgentSession.send_message` lifecycle. Pass 134 routes RPC through the canonical
  session and adapts its existing event stream by subscription; no second tool loop.
- Coding CLI startup used ordinary piped-stdin ingestion before the RPC reader,
  consuming/blocking the JSON-lines control stream. Pass 134 routes RPC first.
- RPC EOF did not forcibly settle a still-running producer/pre-turn hook. Pass 134
  gives EOF/quit/abort the same actual owned cancellation path and retains reader
  recovery after an explicit abort. Invalid setting requests report errors rather
  than terminating the protocol; partial failures remain errors with retained data.
- Those Coding RPC dialogs/controls were absent at the pass-134 source snapshot.
  Pass 135 now wires opt-in questions/typed forms/command management/answers through
  Coding's actual JSONL reader and existing worker/broker, with real subprocess
  evidence. This does not prove Pi wire compatibility, arbitrary terminal rendering,
  remote navigation mutation or independent-child RPC UI. Pass 140 adds scoped
  command-only client-owned editor CAS; pass 141 adds native RPC navigation
  primitives. Pass142 adds actually accepted reviewed RPC navigation; pass144 adds
  combined staged cross-session checkpoint selection; pass145 adds full retained
  display sources/nonblocking POSIX output; passes146–148 add scoped real child
  supervision/questions/approvals/live queues/stopped recovery. Richer raw/custom/
  terminal/native-platform requirements remain OPEN, not supplied by Claw's gateway
  or a successful source checkpoint by implication.
- Further public active-tool/provider/host/render/input capabilities, native encrypted
  credential storage, native desktop/platform checks and general MCP interoperability
  remain on the original completion audit. Neither reference documentation nor a
  selected green test suite establishes goal-wide completion.

Use this file alongside `agent-ui-completion-audit.md` and `agent-ui-gap-plan.md`.
Do not mark the goal complete until current authoritative app/transport/runtime and
required platform evidence satisfies every original requirement.
