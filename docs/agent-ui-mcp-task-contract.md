# UI-048 legacy task acceptance matrix

Updated 2026-10-11. These rows refine **UI-048**, not additional requirement IDs.
Scope: explicitly armed, connection-local legacy 2025-11-25 reverse sampling and
elicitation tasks. Not OS sandboxing, durable jobs, rollback or a second agent loop.
Modern applicability is a separate named UI-048 reconciliation, not waived by this file.

| Contract | Current behavior / evidence | Conclusion |
| --- | --- | --- |
| Explicit negotiation | Manager tasks=True; Coding j/Claw j sampling tasks, k form tasks. Ordinary b/f remain task-off. Older legacy negotiation withdraws registry; modern reverse admission refuses. task_creation, task_hosts, task_boundary tests | Implemented / exercised |
| Creation and identity | Fresh original-loop registry task ID; CreateTaskResult contains state only. Exact augmented request retained in human review. Related-task peer forgery and foreign worker cannot borrow consent. task_creation tests | Implemented / exercised |
| Input/output consent | Sampling input→optional context→output; form request→exact reply. Public input_required while waiting; approved execution works without transport envelope only after original task consent. task_hosts, sampling_context_hosts, task_creation | Implemented / exercised |
| Control wire schemas | Actual xdog create/get/list/cancel/result and status frames validated through locally pinned official core2.3.1 named schemas; result also original sampling/elicitation result schema and RelatedTaskMetadataSchema | Schema evidence, not official task runtime conformance |
| Result metadata | Result preserves original validated response plus registry-assigned related-task.taskId. Callback cannot assign task correlation. No result before final private-output approval. task_official_contract, task_creation, task_hosts | Implemented / exercised; parent/nested related-task conversations remain a separate scope reconciliation |
| Bounded retention | 16 records, 256KiB result, 1MiB aggregate, finite TTL1..600000ms. Null/zero/out-of-ceiling requests refuse without execution rather than silently granting unlimited retention. Single list page; unsupported cursor refused. task_registry/task_creation/task_controls | Explicit supported ceiling; not arbitrary retention/pagination conformance |
| Automatic expiry | Original-loop deadline independently signals siblings, waits actual finally before capacity removal, deletes completed private bytes, cancels timers/awaits cleanup on disconnect. No polling required. task_registry/task_hosts/task_terminal | Implemented / exercised |
| Status telemetry | Post-successful-creation-delivery only; public state,16 monitors,1 pending snapshot each; optional coalescing/write timeout, polling canonical; terminal/family/expiry/close retires. task_notifications; native stdio and actual HTTP posts | Implemented / exercised |
| Result-read cancel/timeout | Ends only read; original operation stays live; fresh reverse request ID may retrieve completed reviewed result, old request ID cannot replay. task_controls | Implemented / exercised |
| Cancel original creation request | Before successful delivery, own creation cleanup. After successful creation response and original request exit, requestId cancellation does not cancel detached task; tasks/cancel required. task_controls/task_creation | Implemented / exercised |
| Task cancel/timeout | tasks/cancel signals original task and waits actual finally; timed-out caller cannot release slot or late private result. Cancel before worker first step never executes. task_controls/task_registry | Implemented / exercised |
| Failed creation delivery | Writer error/timeout/cancel retires original task/private bytes/timer only after cleanup. Actual HTTP500 response-POST evidence; no automatic retry/replay. task_creation | Implemented / exercised |
| Family withdrawal / close | Original family tasks/private results retire after cleanup; sibling family not revoked, all workers signaled before waits; repeated caller cancellation does not abandon finally. task_registry/task_creation/task_notifications/task_hosts | Implemented / exercised |
| Terminal behavior | Both actual native clients sampling/form × release/expire/peer_cancel; fully painted release, resize, no unapproved execution or private reply, parent editor/cursor/main buffer/termios recovery. task_terminal | Current Linux physical evidence; non-Linux native validation deferred UI-060–063 |
| Early cancellation UI | Late connect/list/refresh catalog cannot erase review-ended notice. Next explicit operator action supersedes it. task_terminal + mcp_panel deterministic regression | Implemented / exercised |

## Pinned independent source and scope limits

Source is an existing isolated installation of @modelcontextprotocol/core2.3.1 at
`XDOG_MCP_OFFICIAL_SDK_ROOT`, package exports `./internal`. Named schemas are imported
through package metadata, not reimplemented as an xdog validator. Package source labels
TaskSchema/Create/Get/List/Cancel/status vocabulary as deprecated legacy interoperability
and supplies no task runtime. Do not claim a serving SDK task execution test.
Primary web lookup returned no usable content this checkpoint; no fresh latest claim.

Finite remaining task/protocol reconciliation under UI-048:
1. Match actual pinned modern discover/capability/subscription contracts to reverse
   legacy operations; retain applicable modern work without inventing unsolicited APIs.
2. Reconcile related-task conversation scope/notification metadata against pinned
   protocol source, including any applicable task-related server→client follow-ups;
   current forged related IDs refuse and are not silently downgraded.
3. Reconcile modality/tool/schema/context-scope contracts in the same UI-048 matrix.
   Finite supported ceilings above are explicit limits, not evidence of every valid
   wire shape or third-party deployment. External live deployment remains UI-057.

No repeated historical task OPEN is an extra requirement or reason to re-count these
rows. Task mechanics above have named evidence; remaining questions are those three
reconciliations, not a vague reimplementation of already verified cancellation/TTL.
UI-048 stays待实现 until its complete applicability/interoperability matrix closes.
Final full-root/static/artifact/example and Linux safety/terminal requirements remain.

## Sampling subcapability/choice checkpoint

2026-10-11: original manager-installed owner advertises tools, context only with an
installed disclosure provider; arbitrary callbacks/default connections remain off.
Official optional toolChoice.mode is retained/default auto; explicit none prevents a
provider tool_use response even when a named definition was reviewed. Red2 regression
cases retained, actual host and pinned-schema assertions added.293 current integrated
pass/no skips45.29s with12 physical Linux task paths. This closes these named gaps;
remaining modality/model representations and modern/related-task scope are not waived.

## Required tool-choice boundary

2026-10-11: required selection with zero approved tools fails before context capture
or any executor; required replies need at least one selected schema-valid tool_use.
Custom/provider text-only replies cannot reach output review. Existing none rejects
any proposal, auto permits text or approved proposal. Actual both native task hosts
verify zero model calls on unselected required request and original result refusal;
299 current integrated tests passed/no skips45.81s. No tool execution loop added.
