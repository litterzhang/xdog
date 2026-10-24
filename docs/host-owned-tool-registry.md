# 宿主自有 ToolRepo

Agent 提供 `xdog.agent.ToolRepo`，保存实际 `AgentTool`，不维护进程全局工具字典、
宿主 runtime 或自动发现线程。Coding session、Claw group、CLI 和 Flow 执行方自行
创建 repo、管理权限和资源。导入一个宿主不能改变另一个宿主的工具集合。

## 构造、注册、选择和发布

```python
from pathlib import Path
from xdog.agent import AgentTool, ToolRepo

repo = ToolRepo(cwd=Path.cwd())  # 实际 bash/filesystem/current_time 实例。
selected = repo.select(("current_time",))
assert selected.get("current_time") is repo.get("current_time")
assert isinstance(selected.tools, tuple)

lookup = AgentTool(name="lookup", description="Host-defined lookup")
registration = selected.register(lookup)
replacement = AgentTool(name="lookup", description="New host-defined lookup")
selected.unregister("lookup")  # 替换必须明确注销；register 不隐式覆盖。
selected.register(replacement)
registration.close()  # 只能释放自己的注册，不能删除后来的替代对象。
assert selected.get("lookup") is replacement
```

示例中的 lookup 仅演示库存身份；实际执行需提供 `execute`。向已有 Agent 发布时，
宿主在原执行 owner 的 idle 时点调用 `selected.publish(agent)`；之后 repo 变化不会
自动发布，也不扩大 Agent 的显式 active-tool 选择。

- `ToolRepo(*actual_tools, builtin=True, cwd=None)` 默认创建独立内置实例，bash cwd
  不跨会话共享。`ToolRepo(builtin=False)` 为空，不创建默认工具。
- `submit_result` 不是默认工具；显式 `create_submit_result_tool()` 后注册实际对象。
  SDK 保留具体工具的构造函数，但没有可变全局工厂目录或工厂注册接口。
- `register(*tools)` 原子校验，冲突时不改变库存；不同对象同名报错。
  返回 `ToolRegistration` 收据，`close()` 只释放原注册；注销不关闭工具资源。
- `unregister(name)` 明确移除；`names()` 返回 frozenset；`get(name)` 返回实际工具，
  缺失抛 ValueError。`select(names)` 返回保留实际身份的新子集，`tools` 是 tuple。
- `merge(other)` 合并集合；相同对象可共用，不同同名对象不覆盖。repo 本身不执行工具。
- 注册、选择和发布不授权；审批、只读策略、父子委派及原 owner/executor 身份检查
  仍由宿主和原执行器落实。生命周期由调用方管理。
- 快照不保存 repo、callable、连接或释放收据。宿主重新装配后恢复会话数据；不重放效果。

## 技能集合

`SkillRepo(builtin=True, packaged=None)` 默认发现已安装 packaged 技能；
`builtin=False` 关闭默认发现，`packaged` 可显式配置映射。
`skills.register(directory=path, name="host")` 返回自身，可链式调用，不创建目录。
目录名改绑到不同路径、不同定义同技能名都报错，不再隐式覆盖。
`SkillRepo.from_packaged(names)`、`skills.select(names)`、`skills.merge(other)`
由 SDK 提供；只读 `skill` 入口按需 list/load，不默认注入正文或增加工具权限。

## 来源边界

| 来源 | 负责 | 接入 |
| --- | --- | --- |
| Agent 内置 | SDK 构造实际工具，宿主选择 | owner 自有 ToolRepo |
| 宿主工具 | 产品自身依赖、配置和执行资源 | 实际 AgentTool 注册 |
| Capability | 可信安装发现、scope、工具/技能/命令/资源 | 所选 provider 实际贡献与 repo union |
| MCP | 独立 McpRegistry 管理服务连接、schema 和代理 | Agent 直接消费，不进入 ToolRepo |
| Extension | hooks、事件、commands、UI、provider | **不贡献模型工具** |

`CapabilitySet(scope, tool_repo=..., skill_repo=...).select(names).open(host)`
只打开所选 provider；select 必须在启动前。Assembly 的
`create_agent(tool_repo=..., skill_repo=...)` 是附加 union，不能用空附加子集去掉
capability 自带贡献。没有第二层 enable 名单；底层 Agent 仍接受 tools/skills。

Extension 不再支持模型工具注册或 worker `tools/list` / `tools/call`。扩展必需的内部
通信、只读库存查询和 pre/post-tool hooks 保留。

Flow 解释器和生成物直接使用同一 SDK ToolRepo，不复制 SDK 实现。workflow manifest
的名称是权威名称；执行前显式注销同名项再注册。模块引用及实际 CLI 执行是可信代码，
**Flow CLI 不继承宿主 Agent 的 sandbox、审批 hook 或工具 allowlist**。

## MCP 独立接入

```text
普通工具 / Capability → 宿主 ToolRepo → Agent.local_tools ─┐
MCP 配置 / 连接       → 宿主 McpRegistry ────────────────┤
                                                       ↓
                                     Agent.state.tools / active_tools
                                                       ↓
                                     同一模型循环、权限检查与工具执行
```

`set_tools` 只更新普通工具；`set_mcp_registry` 绑定 MCP；`sync_mcp_tools` 在原 owner
同步可见清单（无 IO），每次模型请求也会同步。宿主显式连接/关闭 MCP，不能提取代理
塞进普通 repo。Agent 关闭不关闭借用的 MCP registry。子 Agent 只绑定实际授予的代理
清单，不复制父连接、凭据或完整 registry。

## 通用结构化结果

可选 `submit_result` 使用宿主注入的 `result_schema` 和 `result_sink`，不解释 Flow
节点、端口或生命周期。Flow 解释器/codegen 在自身边界内接入该协议，旧 Flow 专用
context 键被显式拒绝。接收结果不表示持久完成，也不自动停止 Agent。

详见 [能力装配与离线示例](sdk-capability-assembly.md)。
