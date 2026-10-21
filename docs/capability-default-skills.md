# 已安装 capability 的默认技能说明（2026-10-21）

## 现场问题

技能发现、技能索引、技能正文放置是三个不同步骤。Coding 原先能自动发现安装的
`goal` skill，也能通过 capability 提供真实 Goal 工具；但宿主的显式技能选择默认为空，
所以模型只收到可用技能简介，没有 Goal 的完整使用流程。索引不包含直接读取路径，
模型可能不得不用 filesystem 搜索仓库。工具出现不等于它的配套说明已经加载。

## 机制与边界

- 可信已安装 provider 可声明 `default_skills: tuple[str, ...]`；旧 provider 缺省为空。
- Agent capability discovery 校验名称和重复项，不知道任何 Goal schema 或业务规则。
- `CapabilitySet.default_skills` 只汇总已完整启动、仍绑定且未关闭的 provider。
  查询它不读技能文件、不启动 capability、模型或 worker；启动失败/关闭返回空集合。
- GoalProvider 在 `xdog.skills.goal.runtime` 自己声明 `('goal',)`。
- Coding 和 Claw 在实际主 Agent 的 prompt preparation 中，用通用元数据合并
  capability 默认说明与用户显式选择，再复用 Agent 的 `SkillPlacement`。
  两个宿主均不按 Goal 名称分支，不创建第二套技能解析器。
- 文件读取放在宿主原有 owned IO 路径；已加载正文使用既有 session 前缀放置，
  不追加伪造用户消息，也不把默认说明说成用户授权。
- 默认说明不写入用户显式技能选择。当前 capability 仍有效时，清理用户选择不会
  去掉其默认配套说明；capability 生命周期结束则不再贡献默认说明。
- 加载 skill 不创建 Goal/任务、模型、子 Agent 或 Eyes 观察，不提高工具/权限。
  快照不能指定动态导入的 provider；仍只发现当前安装的可信 entry point。
- 子 Agent 的工具、skill 子集仍由各宿主现有父权限/配置捕获；本机制不自动把
  主 Agent 的 capability 默认说明复制给子 Agent。

## 验证

真实 Coding/Claw 宿主测试捕获第一次模型请求，确认已含
`# Goal: main-Agent-owned work` 正文，不依赖模型先调用 filesystem。
第二轮只有一份正文，加载后仍没有当前 Goal、子 Agent 或额外模型调用。
单元测试覆盖多个 provider、缺省兼容、无效元数据、启动/失败/关闭的惰性边界。

同轮补齐共享 ThinkingMessage 的灰色 `•`、统一 gutter 与最近宽度缓存，
保留完整 reasoning 详情/导出、空 stream、Unicode 与极窄终端保护。

相关 Goal/skills/capability/TUI 两宿主回归：1110 passed（18.09s）。
Ruff、diff-check 和五包 strict mypy（443 源文件）通过。
日志：`/tmp/xdog-thinking-goal-default-regression.log`、
`/tmp/xdog-thinking-goal-default-mypy.log`。模型传输使用测试 stub，未启动或重启用户服务。
