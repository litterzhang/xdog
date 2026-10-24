# SDK 能力装配

SDK 使用两个宿主自有集合：`xdog.agent.ToolRepo` 保存**实际工具对象**，
`xdog.agent.SkillRepo` 保存可发现的技能来源。它们不是执行器、权限系统或后台服务。
原生 `Agent(stream_fn, tools=..., skills=...)` 仍可独立使用，不自动开启 capability。

## 先选择，再打开

```python
from xdog.agent import SkillRepo, ToolRepo
from xdog.agent.capabilities import CapabilitySet

catalog = CapabilitySet(
    "application",
    tool_repo=ToolRepo(),
    skill_repo=SkillRepo(),
)
selected = catalog.select(("flow",))
```

`CapabilitySet` 的 repo 参数配置宿主工具和技能；`select` 选择的是 **provider 名称**，
不是工具名或技能名。目录发现和 metadata import 不启动资源；在 `.open()` 前选子集，
打开时**只启动所选 provider**。上例不会打开 Eyes binding 或启动其 daemon。
原 catalog 不被打开，选择结果有自己的 binding 生命周期，但借用传入的 repo。
也可在构造时传 `names=("flow",)`，避免导入未选择的 provider metadata。

`async with selected.open() as assembly` 返回 `CapabilityAssembly`，不创建 Agent。
application scope 可省略 host，此时使用 `ApplicationPort`；session/group scope 必须
提供真实宿主 owner port，不能用 application marker 冒充。高级宿主仍可显式
`start(host)` / `aclose()`，用 `CapabilityAssembly(*sets)` 聚合已经打开的 Set。

## 集合与冲突

| 接口 | 语义 |
| --- | --- |
| `ToolRepo(*tools, builtin=True, cwd=None)` | 默认创建独立的 bash/filesystem/current_time 实例；`builtin=False` 为空。`submit_result` 需显式构造。 |
| `repo.register(*tools)` | 原子注册实际 `AgentTool`；同名冲突报错，不隐式覆盖。返回有 `close()` 的注册收据。 |
| `repo.unregister(name)` | 明确注销；旧收据不能移除后来注册的同名替代对象。注销不关闭工具资源、不撤销效果。 |
| `repo.names()` / `repo.get(name)` | 返回 `frozenset` 名称 / 实际工具；缺失 `get` 抛 `ValueError`。 |
| `repo.select(names).tools` | 新子集保留实际工具身份，`tools` 是 tuple；不增加权限。 |
| `repo.publish(agent)` | 宿主在原 owner 的 idle 时点发布普通库存；不扩大显式 active-tool 选择。 |
| `SkillRepo(builtin=True, packaged=None)` | 默认发现已安装 packaged skill；`builtin=False` 关闭默认发现，可显式提供 packaged 映射。 |
| `skills.register(directory=path, name="host")` | 注册目录，返回自身，可链式调用；不会创建目录或默认注入正文。 |
| `skills.select(names)` / `skills.merge(other)` | 选择 / 合并技能目录，保留来源和子集边界。 |
| `SkillRepo.from_packaged(names)` | 只包含指定已安装技能；缺失或无效定义明确失败。 |

目录名不能悄悄改绑到另一目录，同一技能名对应不同定义也会报错；没有
packaged → shared → group 的隐式覆盖优先级。`shared` / `group` 可作为明确的目录名，
供保留的保存方法选择目标；它们不是更高优先级或授权等级。

工具替换必须先校验新工具，再明确注销和注册：

```python
from xdog.agent import AgentTool, ToolRepo

original = AgentTool(name="lookup", description="Original host tool")
replacement = AgentTool(name="lookup", description="Replacement host tool")
repo = ToolRepo(builtin=False)
receipt = repo.register(original)
repo.unregister("lookup")
repo.register(replacement)
receipt.close()  # 不能移除 replacement。
assert repo.select(("lookup",)).get("lookup") is replacement
```

## create_agent 是附加 union，不是第二层 enable 开关

```text
assembly.create_agent(stream_fn, *, tool_repo=None, skill_repo=None, config=None, **Agentkwargs)
```

最终集合为所选 capability 的贡献、Set 上配置的 repo 和这次传入的附加 repo 的
union。不同对象的同名工具、不同定义的同名技能均报错，不覆盖其他 owner。
附加 repo 即使 `.select(())` 也**不能删除 capability 自带贡献**。要避免开启某能力，
在 `.open()` 前选择 provider 子集，而不是在 Agent 构造时筛工具。

该入口不接受 `tools`、`skills` 或独立的 enable 名单；底层原生 Agent 仍接受
`tools=repo.tools`、`skills=skills`。`before_tool_call` 等原生参数继续传入原 Agent。
`compose_tools(existing)` / `publish_tools(agent)` 没有额外过滤参数；权限、审批、
active-tool 选择、父子委派和实际执行身份检查仍属于宿主。

## 正文按需读取

Flow、Eyes、Goal 的 packaged skill **不默认激活正文**。索引是可发现元数据，不是
正文注入或工具权限。Assembly 为可见技能提供只读 `skill` 入口（已有同名宿主入口
则保留）：`list` 返回元数据，`load` 返回指令作为工具结果，不执行指令、不激活正文、
不改变权限。独立 Agent 可显式注册 `create_skill_tool(skills)`。

确需 session/turn 正文时由宿主明确使用 `active_skills`；它独立于工具授权。
`SkillPlacement.read(skills, names)` 可在宿主自有 IO worker 上准备正文，不另建 Manager。

## 可运行离线示例

在仓库根目录 `uv sync` 后，用 `uv run --no-sync python example.py` 运行。
独立环境需要先安装 `xdog-agent` 和 `xdog-flow`。示例只打开 Flow binding，不运行
workflow、不调用网络模型、不启动 Eyes；所有工具调用都被示例 hook 拒绝。

```python
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from xdog.agent import AgentConfig, BeforeToolCallResult, SkillRepo, ToolRepo
from xdog.agent.capabilities import CapabilitySet
from xdog.agent.tools import create_submit_result_tool
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream


def offline_stream(model, context, options):
    names = ", ".join(sorted(tool.name for tool in context.tools))
    return EventStream.empty(
        AssistantMessage(content=(TextContent(text=f"Available tools: {names}"),))
    )


async def deny_tools(context, cancel):
    return BeforeToolCallResult(block=True, reason="Offline example denies execution")


async def main():
    with TemporaryDirectory(prefix="xdog-sdk-doc-") as directory:
        workspace = Path(directory)
        tools = ToolRepo(cwd=workspace).select(("current_time",))
        skills = SkillRepo().register(directory=workspace / "skills", name="host")
        selected = CapabilitySet(
            "application", tool_repo=tools, skill_repo=skills
        ).select(("flow",))
        async with selected.open() as assembly:
            # 额外工具是 union；不是对 Flow 工具的筛选。
            extra = ToolRepo(create_submit_result_tool(), builtin=False)
            async with assembly.create_agent(
                offline_stream,
                config=AgentConfig(model="offline"),
                tool_repo=extra.select(("submit_result",)),
                skill_repo=SkillRepo(builtin=False).select(()),
                before_tool_call=deny_tools,
            ) as agent:
                assert {"flow", "current_time", "submit_result", "skill"} <= {
                    tool.name for tool in agent.local_tools
                }
                assert agent.active_skills == ()
                stream = await agent.prompt("List available tools; do not call them.")
                async for _event in stream:
                    pass
                print("Offline assembly OK:", sorted(tool.name for tool in agent.local_tools))
        # Agent 先关闭，所选 capability binding 后关闭。


if __name__ == "__main__":
    asyncio.run(main())
```

换成真实模型时，宿主提供有实际模型元数据和认证的 `StreamFn`，并实现自己的权限策略。
示例拒绝 hook 不是生产审批 UI，也不是 OS sandbox。

## 生命周期、隔离与恢复

- Flow binding 只有实际工具调用时才运行 CLI，不启动 daemon。**Flow CLI 子进程不会
  继承宿主 Agent 的 sandbox、审批 hook 或工具 allowlist**；应把完整 workflow 执行
  视为可信代码能力。Flow 自身 workspace/confined 检查也不是敌对代码沙箱。
- 若选择 Eyes，再 `.open()` 会确保独立 daemon 可用，但不创建 observation。关闭
  binding 只是 detach，不停止共享 daemon，也不取消已接受的注册或下游输入。
- MCP 仍由独立 `McpRegistry` 管理连接和代理，直接传给 `Agent(mcp_registry=...)`，
  不塞进 `ToolRepo`。Extension 仍提供 hooks/commands/UI/providers，不注册模型工具。
- 关闭所有 Agent 后再关闭共享 Set；repo 不拥有借用工具、daemon、连接的生命周期。
  不给子 Agent 直接装配整个应用 Set，沿用宿主实际父授权和 owner fences。
- 快照只保存会话数据，不保存 repo、可执行对象、owner port、连接或审批权限。
  恢复前重建并打开所需 Set、注入当前授权，再用 `restore_agent` /
  `restore_agent_parts` 校验；不会启动模型请求或重放未知副作用。

详见 [工具集合](host-owned-tool-registry.md) 和 [Agent README](../packages/agent/README.md)。
