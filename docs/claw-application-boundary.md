# Claw 应用边界：移除独立 SDK 定位

## 目标

Claw 是 Linux 上的持久助手应用，不再提供独立的公共 Python SDK。
可复用的 Agent SDK 仍是 `xdog.agent`，不在删除范围内。

## 删除与保留

- 删除 `xdog.claw` 根级配置、渠道、会话、编排器等公共重导出。
  不提供兼容转发、延迟 `__getattr__` 或另一套 SDK facade。
- 根包导入不再隐式加载运行时、provider、渠道或工具注册。
  应用通过显式内部模块导入完成装配；`xdog.claw.core` 的工具注册保留。
- 删除文档中的 Claw Top-level API 与独立 SDK 调用承诺；包元数据定位聊天应用。
- 保留内部模块、类型标注、依赖注入及测试入口。这些是应用实现，
  不承诺外部嵌入 API 稳定性，也不增加拦截内部导入的运行时代码。
- 保留 CLI、配置文件、gateway、TUI、微信、MCP、扩展 worker 与私有 ACP/Eyes
  所需协议。移除 SDK 定位不等于移除产品控制或通信。
- 保留 group 唯一主 Agent、Goal 验收、task 委派、权限、取消、关闭、压缩和恢复。
  不更改 Coding、Flow 或通用 Agent SDK 的公共能力。

## 兼容性说明

`from xdog.claw import Orchestrator` 等旧根级导入不再支持。
这是本次明确授权的 Python 公共入口删除，不是重命名出另一套公共入口。
需要可嵌入 Agent 的宿主使用 `xdog.agent`；需要运行 Claw 使用 `xdog-claw`
及文档列明的产品接口。第三方如自行导入 Claw 内部模块，需自行适配内部变更。
已持久化的聊天、Goal、observer 与用户配置不因这次清理被迁移或删除。

## 验收与交付

稳定需求编号和状态见 `claw-application-boundary-requirements.json`。
当前 Linux 全八包回归、静态检查、锁检查、示例与构建验证保留。
先前已完成但未提交的 Eyes/Goal 功能单独提交，本次收口随后提交，
只推送正常 main，不打 tag、不发布版本、不重启 gateway。
无关 `docs/agent-ui-features.md` 与 `workflows/` 保留，不加入提交。
