# XDOG 文档索引

这里是当前设计和维护指南的入口。用户用法从各 package README 开始；
历史需求单、阶段验收、发布调查和已撤销方案集中在 [archive](archive/README.md)，
**不是当前接口、待办或绿色测试的证明**。

## 先读这些

- [架构与跨包边界](architecture.md)
- [Agent SDK 与能力装配](agent/capabilities.md)
- [Claw 架构与数据所有者](claw/architecture.md)
- [Goal 当前生命周期](goal/design.md)
- [开发与测试](development/testing.md)
- [构建与发布](development/publishing.md)

## Agent SDK

工具、技能、能力、Extension 与宿主拥有的子执行。

- [SDK 能力装配](agent/capabilities.md)
- [子 Agent：宿主生命周期与窄工具委派](agent/children.md)
- [Extension：只读视图与用户命令](agent/extensions.md)
- [宿主自有 ToolRepo](agent/tool-repo.md)

## Claw

应用边界、共享上下文、身份、渠道和消息。

- [Claw ↔ Eyes through ACP](claw/acp.md)
- [Claw 内部职责边界](claw/architecture.md)
- [Claw 会话轮换与记忆连续性](claw/context-continuity.md)
- [Claw 身份、工具权限与渠道账号](claw/input-identity.md)
- [Claw 主动消息与 Goal 交互收口](claw/messaging.md)
- [Claw system prompt 的职责](claw/system-prompt.md)
- [Claw XHS：本地改造 CLI 与公开 Python SDK](claw/xhs-sdk.md)

## Coding

当前会话权限、输入和提示词。

- [Coding 队列、会话权限及工具效果分类（2026-10-21）](coding/permissions.md)
- [Coding 提示词与技能选择边界](coding/system-prompt.md)

## Eyes

独立 daemon、采集源、检测、投递与恢复。

- [Eyes: daemon, JSON and thin tool only](eyes/daemon-and-tool.md)
- [Eyes observation contracts and pushed events](eyes/observation-contracts.md)
- [Eyes source 配置指南](eyes/sources.md)
- [Eyes transport recovery](eyes/transport-recovery.md)

## Goal

目标、任务、等待、进度及原主 Agent 自动续跑。

- [Goal：目标记录、任务与自动续跑](goal/design.md)

## 跨包协议

ACP、自动输入和原始 owner 围栏。

- [ACP session routing, automatic inbox and batched turns](protocols/acp.md)

## TUI

共享组件样式；应用权限不属于组件层。

- [Shared auxiliary-panel styling](tui/panels.md)

## Flow 场景指南

引擎 API/配置文档仍以 package 内 docs 为准。

- [An unattended workflow that builds a service from an idea](flow/service-builder.md)

## 开发维护

测试、wheel-only 验收、版本与发布。

- [构建、发布与 Trusted Publishing](development/publishing.md)
- [开发与测试](development/testing.md)

## Package 文档

- [AI](../packages/ai/README.md) · [Agent](../packages/agent/README.md) · [Flow](../packages/flow/README.md)
- [Coding](../packages/coding/README.md) · [Claw](../packages/claw/README.md) · [TUI](../packages/tui/README.md)
- [Eyes](../packages/eyes/README.md) · [Skills](../packages/skills/README.md) · [Site](../packages/site/README.md)
- [Flow 引擎专题文档](../packages/flow/docs/)

## 文档状态与变更

当前指南合并了子执行/委派、Extension 视图/命令、Eyes 分批 source、Goal 生命周期和发布说明。
原计划、验收时间、需求编号和失败证据仍可从归档追溯；合并不表示重新执行了历史测试。
新增说明优先归入现有主题，不再为每轮修复创建新的顶层 design/plan/verification 三件套。
