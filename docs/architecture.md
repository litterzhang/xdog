# XDOG 架构与文档边界

XDOG 是 local-first Python workspace；产品用法以各 package README 为入口，
这里描述跨包职责。示例、计划和验证记录不自动构成公共 API 或权限承诺。

```text
AI       模型/鉴权/路由与协议适配
 ↑
Agent    唯一可复用模型/工具循环、仓库、MCP、能力装配和持久化
 ↑
Flow     私有 typed ports + 显式映射、执行器与 codegen
Coding   会话/分支、权限、输入持久化、交互终端
Claw     group 主 Agent、渠道/身份、输入输出回执、历史与记忆

Skills   Agent capability：Goal 记录、命令、报告与自动提醒
Eyes     独立观察 daemon / CLI：source → detector → route → handler
TUI      无宿主业务策略的终端组件
Site     文档、营销页面与可信本地 Flow demo
```

## SDK 与宿主

- Agent 提供 SDK，不替宿主管理应用身份、渠道或子 Agent 生命周期。
- ToolRepo 保存宿主拥有的实际工具；MCP 使用独立 McpRegistry，仍进入同一工具循环和权限钩子。
- SkillRepo 发现 packaged/宿主技能，能力发现不默认激活正文。选择 provider 后再打开资源。
- Extension 保留 hooks、用户命令、UI、providers、显式授权的只读查询；不再用另一套 tools 发现。
- 每个真实 owner 保留准入、取消和资源结算责任；快照、来源标签、工具名字和 role 不授予权限。

详见 [能力装配](agent/capabilities.md)、[工具仓库](agent/tool-repo.md)、
[子执行](agent/children.md) 和 [Extension](agent/extensions.md)。

## 产品职责

Claw 每 group 一个逻辑主 Agent，session 是上下文历史，不是新的执行负责人。
Coding 精确绑定 session/branch，自动输入不构造第二个 Agent。接收、执行、历史提交、
渠道送达是不同事实；未知效果不自动重放。详见 [Claw 架构](claw/architecture.md)
和 [ACP 协议](protocols/acp.md)。

Goal 保存业务记录和提醒，由主 Agent 作规划和完成判断；Eyes 只观察与投递，不理解 Goal。
自动输入不解除用户等待、用户暂停或工具权限；宿主异常不能被伪装成新的业务授权。
详见 [Goal](goal/design.md) 与 [Eyes 契约](eyes/observation-contracts.md)。

Flow 的解释器/codegen 必须保持语义一致，节点没有共享全局状态。
workflow 脚本/CLI 执行是可信代码；permission gate、workflow guard 和 confined 检查
都不能被声称为通用 OS 沙箱。详见 [Flow README](../packages/flow/README.md)
及 [生成产物许可例外](../LICENSE-EXCEPTION.md)。

## 维护方式

- 当前指南集中在本目录的主题文件夹；完整入口见 [文档索引](README.md)。
- [archive](archive/README.md) 保存历史计划、审计和验收，不参与当前功能/完成状态推断。
- 更新功能时直接更新当前主题文档；只有需要保留版本基线/故障证据时才新增历史记录。
- 不为旧文档路径创建兼容文件；调用者、README、站点链接随重组一起更新。
- 测试、发布与安装后验证的边界见 [开发测试](development/testing.md)
  和 [发布指南](development/publishing.md)。
