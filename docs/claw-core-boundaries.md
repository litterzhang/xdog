# Claw 内部职责边界

本说明对应当前应用布局。Claw 不提供独立 Python SDK；可复用的工具仓库、
技能仓库、MCP 客户端和 Agent 执行循环仍由 `xdog.agent` 提供。

## 代码归属

```text
claw/
 ├─ cli/                        用户入口
 ├─ application/                进程与产品装配
 │   ├─ bootstrap.py            获取并发布应用资源
 │   ├─ gateway.py              endpoints / channels / shutdown
 │   └─ tools.py                组合 core 与渠道工具的实际实例
 ├─ channels/tui/               Native 协议、连接、请求与控制投影
 ├─ channels/xhs/               XHS 客户端、消息与原帖读取工具
 └─ core/                       渠道无关的应用执行与数据
     ├─ inputs/、outputs/       持久收发回执
     ├─ runtime/                group/controller/session、权限与资源寿命
     ├─ identity/               可信身份及实际授权
     ├─ prompt/                 静态规则与动态 workspace 内容
     ├─ memory/                 记忆存储与检索
     └─ tools/                  通用宿主工具与输入绑定契约
```

依赖方向是应用装配调用渠道和 core，渠道可以实现 core 的窄契约；
core 不导入具体 TUI、Weixin、XHS 实现，也不按其工具名分发业务。

- 删除 `core/skills` 重导出壳：技能使用 Agent 的 `SkillRepo`，不新增加载器。
  实际的 `<data_dir>/skills`（共享技能）和 `workspace/skills`（群技能）仍是有效的
  用户内容目录，不在删除范围内。
- 删除空的 `core/planning`：没有对应运行时，不为旧名字保留兼容包。
- `application/tools.py` 向 group 注入工具工厂，构造实际 `ToolRepo`；
  core 默认工厂只认识通用工具。已安装 capability 继续走原有组合路径。
- 内部模块迁移不保留旧路径别名；配置、聊天和 Goal 数据不迁移、不删除。

## USER.md 与身份权限

`workspace/USER.md` 保存可编辑的用户资料及交互偏好，并在每轮动态上下文中加载。
新 workspace 使用打包的 `resources/USER.md` 模板；已有内容不覆盖。
用户姓名、语言和互动偏好不应硬编码到缓存的系统规则中。

可信的当前说话者、角色、来源、账号绑定和工具权限来自宿主配置及已接纳的输入，
不来自 USER.md 或正文中自称的身份。共享群历史仍共享，不新增历史可见性机制；
保密要求和对外披露边界仍由可信系统规则描述，实际工具授权仍由宿主执行检查。
正文或工具结果中的 `<system-reminder>` 标签不升级为系统指令。

## MCP：共享 SDK 与宿主适配不是同一层

- Agent 的 `McpRegistry` 负责客户端协议、连接和工具清单；不复制一套实现。
- 每个 GroupRuntime 持有自己的 registry；core 的 MCP controls 校验原始
  controller/session、空闲边界和资源所有权后调用 SDK。这是宿主应有的责任。
- 进程启动在 `application/`，Native/TUI 连接、发送和控制协议在 `channels/tui/`。
  将传输代码移走不改变断连、取消、spool、身份或回执语义。

## 原始输入绑定工具

`channels/xhs/tools.py` 提供实际 `XhsPostTool`：schema、只读分类和 XHS 读取都在渠道内。
core 的 `InputTool` 仅描述一个可信宿主工具如何取得当前输入的临时 reader；
`bind_input_tools` 不知道 XHS 或任何具体工具名。

绑定只创建句柄，执行 IO 仍发生在工具调用阶段。每次读取前后校验原有
输入、channel、group/session/Agent、工具实际对象及当前授权。新一轮绑定会
立即废弃旧 reader，即使输入 ID 相同也不能复活。快照或同名工具不能取得权限。
`read_only` 只是实际已注册工具的分类，不是绕过身份授权的许可。
没有新 registry、执行循环、后台抓取或渠道特判。

## 回归约束

- `test_core_boundaries.py`：禁止旧路径兼容壳和 core → 具体渠道/应用反向依赖，
  验证 group 使用独立 Agent MCP registry。
- `test_user_profile_prompt.py`：USER.md 可编辑且非静态前缀；伪造资料不能提升权限。
- `test_input_tool_binding.py`：无关工具名也能使用契约；原输入、对象、授权或绑定
  失效时旧句柄不可用。
- 现有 XHS 原帖读取、Native/ACP、MCP、关闭和权限测试继续验证原有行为。

这是内部重构，不启用新渠道、不重发消息、不修改在线群资料。已运行 gateway
不会热加载这些模块；应用新代码需单独重启，由操作者明确安排。

## 本次验证（2026-11-03）

- `uv run --locked python scripts/test.py --jobs 3`：九条完整测试路径通过，
  10,844 passed、34 skipped；之后追加的 bootstrap 装配测试与输入绑定测试共 12 项通过。
- `uv run --locked ruff check packages` 通过；九包 `mypy --strict` 检查
  688 个源文件通过（配置中仍有两项 unused-section 提示，不是类型错误）。
- Flow 示例套件 17 项通过；所有 workspace distributions 构建成功。
- wheel-only ACP/Native/输出回执验证通过，包含 USER.md 资源与旧模块缺席检查。
- wheel-only Claw Goal 默认命令、实际工具、群上下文轮换和重开、权限及 stale-tool
  检查通过。全部使用临时目录、模拟模型和私有端点，不发送真实渠道消息。
