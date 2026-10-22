# Host-owned tool registry

Agent 提供 `ToolRegistry` SDK，不维护进程全局工具字典、宿主 runtime 或自动发现线程。
Coding session、Claw group、CLI 和 Flow 执行方自行创建 registry、绑定资源并关闭资源。
导入一个宿主不能改变另一个宿主的工具集合。

## 定义、绑定与发布

```python
from pathlib import Path
from xdog.agent.tools import ToolContext, ToolRegistry

registry = ToolRegistry()
# Agent 的默认工厂：bash / filesystem / current_time。
# submit_result 已有工厂，但不是普通会话默认工具。
tools = registry.create_tools(context=ToolContext(cwd=Path('/project')))
registration = registry.replace_source('builtin', tools)

# 在原宿主执行 owner 上；不会启动模型或增加 active-tools 显式选择。
registry.publish(agent)

# 只解除这次 source 绑定，不关闭工具资源，也不撤销已执行操作。
registration.close()
registry.publish(agent)
```

- 每次 `create_tools` 创建独立实例，bash cwd 不跨会话共享。
- `create_tools(None)` 使用 SDK 默认集合；`create_tools([])` 不创建工具。
- 定义新工厂用 `register_factory(name, factory, default=False)`；上下文是明确的
  `ToolContext`，不用反射函数签名或按工具名称猜参数。
- `replace_source(source, actual_tools)` 原子校验所有绑定来源的同名冲突，失败不改变
  原 inventory。同一 source 可以更新，其他 source 不能被静默覆盖。
- `replace_source` 返回版本绑定的释放句柄；旧句柄不能删除替换后的注册。
- 注册不授权。权限、审批、父子委派、原 owner/executor 身份检查仍由宿主和原工具执行器落实。
- 生命周期由调用方管理；registry 不连接 MCP、不启动 daemon、不创建 worker，也不关闭这些资源。
- 快照不保存 registry、factory、callable、连接或释放句柄。宿主重新装配后恢复会话数据。

## 来源边界

| 来源 | 负责 | Registry 接收 |
| --- | --- | --- |
| Agent 内置 | SDK 提供默认工厂，宿主实例化 | 当前 owner 的工具实例 |
| 宿主工具 | 产品自身依赖与配置 | 当前 owner 的工具实例 |
| Capability | 安装发现、scope、tool/skill/command/runtime 等组合 | provider 提供的实际工具 |
| MCP | 独立 McpRegistry 管理外部服务、Schema 和调用代理 | 不进入普通 ToolRegistry，Agent 直接消费 |
| Extension | hooks、事件、commands、UI、provider 等宿主扩展 | **不贡献模型工具** |

Extension 不再支持 `api.tool()`、`ExtensionRuntime.tools()/sync_tools()` 或 worker
`tools/list` / `tools/call`。提供模型工具的安装包应声明 Capability；真正的外部工具服务使用
MCP。Extension 必需的内部通信、只读工具清单查询和 pre/post-tool hooks 并未因此移除。

Flow 的工作流级名称解析保留，但实际 inventory 使用 Agent Registry SDK；显式 workflow
manifest 的同名替换限于该次执行的解析表。解释器和生成代码使用相同语义。工作流模块引用
是可信代码执行，不是从会话快照导入任意代码的入口。

## MCP 是独立来源

```text
普通工具 / Capability → 宿主 ToolRegistry → Agent.local_tools ─┐
MCP 配置 / 连接       → 宿主 McpRegistry  ─────────────────────┤
                                                            ↓
                                              Agent.state.tools / active_tools
                                                            ↓
                                              同一模型循环、权限检查与工具执行
```

Agent 的 `set_tools` 只更新普通工具；`set_mcp_registry` 绑定 MCP；`sync_mcp_tools`
在原 owner 上同步可见清单（无 IO），每次模型请求也会同步。宿主的连接/认证命令不再
提取代理塞进普通 registry。Agent 关闭不关闭借用的宿主 MCP registry；宿主负责最后关闭。
子 Agent 只绑定父 Agent 明确授予的代理清单，不复制连接、凭据或整个父 registry。
