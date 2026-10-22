# SDK capability 装配

`xdog.agent.capabilities.CapabilityAssembly(*capability_sets)` 将调用方已有的
`CapabilitySet` 组合成 SDK Agent 的工具与默认 skill 输入。它是装配助手，不是运行时、
Agent wrapper 或应用框架。`create_agent(...)` 返回原生 `xdog.agent.Agent`；
事件流、历史、工具循环、取消和关闭仍使用 Agent 本身的接口。

普通 `Agent(...)` **不会自动发现或开启 capability provider**。需要这些能力的 SDK
宿主应显式创建、启动 Set，再用 Assembly 装配；只想使用现有工具的代码无需改动。

## 公开接口与职责

```python
from xdog.agent.capabilities import ApplicationPort, CapabilityAssembly, CapabilitySet
```

Assembly 可以聚合多个 Set。application Set 使用无业务上下文的 `ApplicationPort`；
其他 scope 的 Set 仍由宿主提供对应的实际 owner port，不应拿 application marker
冒充 session/group owner。

| 接口 | 用途 |
| --- | --- |
| `tools` | 聚合各 Set 当前发布的实际工具对象。 |
| `default_skills` | 聚合已成功启动的 provider binding 的默认 skill。 |
| `declared_skills` | 聚合 provider 的可发现 skill 名称，不激活正文。 |
| `declared_tools` | 聚合 provider 声明的工具名；声明不等于工具已可执行。 |
| `active_skills(selected=())` | 合并调用方显式选择的 skill 与 provider 默认 skill。 |
| `compose_tools(existing, enabled_tools=None)` | 将宿主工具与 capability 工具按同一选择、合并规则组合。 |
| `publish_tools(agent, enabled_tools=None)` | 向已有 Agent 发布合并后的工具；宿主仍负责 idle 状态与权限策略。 |
| `tool_effect(tool, args)` | 按实际工具对象查询原 binding 的效果分类；不是授权决定。 |
| `create_agent(...)` | 使用相同工具合并、默认 skill 逻辑创建原生 Agent。 |
| `restore_agent(agent, snapshot, allowed_models=...)` | 原子恢复快照并重新绑定当前 capability 默认 session skill，保留工具、权限与 turn-skill 生命周期。 |
| `restore_agent_parts(...)` | 对分离存储的元数据和结构化历史应用相同恢复语义。 |

构造入口为：

```text
assembly.create_agent(
    stream_fn,
    *,
    skills: SkillManager,
    config=None,
    tools=(),
    active_skills=(),
    enabled_tools=None,
    **Agentkwargs,
)
```

这里的签名展示关键字参数约定，不是可直接执行的调用片段。`skills` 必须由宿主提供；
`config` 是原生 `AgentConfig`，`tools` 是宿主显式提供的额外工具，
`active_skills` 是显式激活的 skill，`enabled_tools` 用于选择装配的 capability 工具。
`before_tool_call` 等原生 Agent 参数经 `Agentkwargs` 传入，不需要另一个执行 API。
`tools=()` 不表示禁用 capability 工具；要限制它们，应显式设置 `enabled_tools`。

默认 skill 和工具选择是两件事：provider 可以声明默认 skill，但 Flow／Eyes
均不默认激活正文；需要时显式选择 `active_skills`。skill 不会扩大
`enabled_tools`，也不会绕过工具审批。仅挑选工具而不显式激活其 skill 也是允许的。
`publish_tools` 不是 permission hook，也不会把默认 skill 自动变成工具授权。

工具合并按真实对象归属处理：只替换这些 Set 拥有的旧 capability 工具，保留宿主额外
传入的工具；不同对象的同名冲突会被拒绝，而不是覆盖别人的工具。
`enabled_tools=None` 纳入全部当前 capability 工具，空集合不纳入任何 capability 工具；
它**不筛掉宿主额外提供的工具**，这些工具的授权仍由宿主负责。`publish_tools` 保留
Agent 原有的 active-tool 选择机制，不等于重新授予所有已配置工具。

`tool_effect` 查询应传入本次实际发布的工具对象，而不是根据同名字符串推断其来源。
无分类或未知效果不能视为自动放行；只读策略、审批和拒绝执行由宿主判断。

## 生命周期与安装边界

1. 宿主安装并信任需要的 provider。`CapabilitySet("application")` 发现受信任的已安装
   `xdog.capabilities` entry point，不从持久化快照中的模块名加载实现。
   安装、metadata discovery 与显式 `start` 是不同阶段；发现不等于启动。
2. 调用方在自己的事件循环上执行 `await capabilities.start(application_port)`。
   Assembly 不替调用方启动 provider，不创建事件循环、后台执行 owner 或第二个 Agent loop。
3. 同一个 application Set 可以供多个 Agent 使用，每个 Agent 分别选择工具及注入审批
   hook；共享 provider binding 不等于共享全部工具授权、历史或审批缓存。
4. 先停止运行并关闭使用该 Set 的全部调用方 Agent，再 `await capabilities.aclose()`。
   关闭一个 Agent 不会替宿主关闭共享 Set。Assembly 不接管任何一个对象的生命周期。
   若宿主使用不同线程/事件循环，还须自行满足 provider 的 owner-loop 约束；共享 Set
   不是任意跨循环执行的承诺。

当前 application provider 的差异：

| Provider | 显式启动 Set 的效果 | Skill 与关闭语义 |
| --- | --- | --- |
| Flow | 建立 CLI-only 工具 binding；不启动 daemon，也不运行 workflow。实际工具调用才执行 CLI。 | packaged skill 可被发现；需要时显式 `active_skills=("flow",)`，不默认加载正文。关闭 binding 清理其拥有的调用资源。 |
| Eyes | 启动或复用同一用户的共享 Eyes daemon；不因此创建 observation 或调用模型。 | 当前 provider **没有** `default_skills=("eyes",)`；需要正文时显式 `active_skills=("eyes",)`。关闭 binding 只是 detach，不停止共享 daemon，也不取消已有 registration。 |

因此，即使某 Agent 仅选择 `flow`，只要 Set 中安装并启动了 Eyes provider，Eyes
binding 仍会按自身启动约定确保 daemon 可用。**工具选择不是 provider 启停开关**。
需要限制 provider 集合时，应由宿主控制受信任的安装环境，而不是让 skill 或模型决定。
装配本身不安装包、创建 observer、提供模型认证或启动模型请求。

## 可运行示例：共享 application Set，独立工具选择

从仓库根目录 `uv sync` 后，可将以下代码保存成 Python 文件运行；独立 SDK 环境需安装
`xdog-agent`、`xdog-flow`、`xdog-eyes` 及其依赖。示例会显式启动 application Set，
因此安装了 Eyes 时会启动或复用其共享 daemon。示例不会创建 observation 或运行 workflow。

`run_sdk` 将 `stream_fn` 和审批 hook 作为函数参数：真实模型适配器和授权 UI 均由宿主注入，
没有隐含的全局 provider。文件底部提供离线 stream 和拒绝所有工具调用的 hook，
便于直接执行装配路径；它不是生产审批策略，也不验证实际模型或工具互操作性。

```python
import asyncio
from contextlib import AsyncExitStack
from pathlib import Path

from xdog.agent import (
    AgentConfig,
    BeforeToolCallFn,
    BeforeToolCallResult,
    StreamFn,
)
from xdog.agent.capabilities import ApplicationPort, CapabilityAssembly, CapabilitySet
from xdog.agent.skills import SkillManager
from xdog.ai.utils.event_stream import EventStream
from xdog.ai.types import AssistantMessage, TextContent


async def run_sdk(
    stream_fn: StreamFn,
    *,
    workspace: Path,
    before_tool_call: BeforeToolCallFn,
) -> None:
    workspace = workspace.resolve()
    # 路径由宿主显式选择；Assembly 不猜测 cwd、全局或项目 skill 目录。
    skills = SkillManager(
        shared_dir=workspace / "shared-skills",
        group_dir=workspace / "project-skills",
    )
    # packaged=None（默认）使用已安装包的 skill；packaged={} 可关闭此发现。
    application = ApplicationPort()
    capabilities = CapabilitySet("application")
    try:
        # start 也在 try 内；启动失败同样执行 finally。
        await capabilities.start(application)
        assembly = CapabilityAssembly(capabilities)
        async with AsyncExitStack() as agents:
            flow_agent = assembly.create_agent(
                stream_fn,
                skills=skills,
                config=AgentConfig(model="host-model"),
                enabled_tools=("flow",),
                active_skills=("flow",),  # 按需激活正文，而非默认注入。
                before_tool_call=before_tool_call,
            )
            agents.push_async_callback(flow_agent.aclose)

            eyes_agent = assembly.create_agent(
                stream_fn,
                skills=skills,
                config=AgentConfig(model="host-model"),
                enabled_tools=("eyes",),
                active_skills=("eyes",),  # Eyes 目前不是 provider 默认 skill。
                before_tool_call=before_tool_call,
            )
            agents.push_async_callback(eyes_agent.aclose)

            for agent in (flow_agent, eyes_agent):
                stream = await agent.prompt("说明本次可用工具，不要执行工具。")
                async for event in stream:
                    print(event.type)
        # 清理栈先关闭所有已构造的 Agent；构造/运行/关闭异常也会退出栈。
    finally:
        # 即便 start、create_agent 或 prompt 失败，仍释放本调用方的 Set。
        # 对 Eyes 这是 detach，不是 daemon stop 或 observation cancel。
        await capabilities.aclose()


def offline_stream(model, context, options):
    names = ", ".join(tool.name for tool in context.tools)
    return EventStream.empty(
        AssistantMessage(
            content=(TextContent(text=f"Available tools: {names}"),),
            stop_reason="stop",
        )
    )


async def deny_tools(context, cancel):
    return BeforeToolCallResult(block=True, reason="Offline example denies tool execution")


if __name__ == "__main__":
    asyncio.run(
        run_sdk(
            offline_stream,
            workspace=Path.cwd(),
            before_tool_call=deny_tools,
        )
    )
```

真实调用时，将 `offline_stream` 换成宿主已配置认证和模型元数据的 `StreamFn`，并将
`AgentConfig.model` 设置为该适配器实际支持的模型。创建 Assembly 不会为宿主选择模型。

`SkillManager` 的目录优先级是 packaged → shared → group，后者覆盖前者；宿主还应审查
覆盖内容的信任边界。provider 的默认 skill 名并不包含 skill 正文，也不决定目录。
缺少 skill 内容时应检查宿主提供的 manager 和安装内容，不能通过补发工具权限“修复”。

## 已有 Agent、子 Agent 与恢复

- **已有 Agent**：宿主可用 `compose_tools`/`publish_tools` 装配工具，使用 `active_skills`
  取得同一默认 skill 合并结果。工具发布应在宿主控制的 idle 时点进行；不要在活动工具
  调用中替换库存或将同名对象当作原授权对象。普通 `Agent(...)` 始终保留显式构造语义。
- **审批**：在创建入口注入 `before_tool_call`，由宿主落实只读、审批及实际工具身份检查。
  `ApplicationPort`、provider 名、skill 正文与 `ToolEffect` 都不能授予执行权限；SDK
  不会替调用方提供审批 UI 或安全沙箱。
- **子 Agent**：不要给子 Agent 直接装配整个应用 Set。子 Agent 不自动继承全应用能力；
  应使用宿主既有子 Agent 授权/委托路径，限定为原父 Agent 实际授权的工具子集，并维持
  原始 owner、身份与执行入口校验。共享安装环境不构成 delegation grant。
- **Restore**：快照不是 capability 对象的序列化格式。宿主应重建并显式启动所需 Set，
  用当前授权重新装配 Agent，再恢复允许的会话数据。`stream_fn`、`SkillManager`、工具
  executor、审批 hooks、owner ports 与 capability binding 等非序列化对象必须由宿主
  重新注入；保存的工具名、默认 skill 或来源标签都不能恢复执行权限，也不能触发旧操作重放。


## Capability 可以提供哪些贡献

Capability 是可组合的能力单元，不是“必须有后台进程”的同义词。受信任的
provider 可以声明 `skill_names`、`tool_names`、默认 skill 和宿主要求；binding
可以提供工具、可关闭的 runtime 资源、原有扩展注册 hook，或这些贡献的子集。
空工具集合是合法的。`skill_names` 仅说明该能力附带哪些可发现技能，不代替
SkillManager 的 packaged 发现，也不把正文加入上下文。Flow／Eyes／Goal 均声明技能名，
但均不默认激活正文。Goal 的 session/group scope、工具和命令注册不依赖正文激活。扩展和 runtime 仍使用真实 scope、原有宿主 owner 与
权限机制；没有另一套 Agent loop，也不能按快照中的模块名加载代码。

## 明确激活与恢复

发现可选 skill 的索引失败，可以报告诊断而不等同于授权。**明确选择激活的
skill 必须成功解析并具有有效正文**；不存在、损坏或 prepared placement 与选择
不一致会明确失败，不能仍显示 active 却没有操作说明。

恢复时使用 `assembly.restore_agent(agent, snapshot, allowed_models=...)`，或者
分离历史版本 `restore_agent_parts`。SDK 首先验证原快照的技能绑定、模型白名单
和历史状态，再原子合并当前 capability 缺失的默认 session skills。普通
`Agent.restore` 不自动发现 provider；需要重装配时由宿主显式选择此入口。

恢复不会扩大工具选择、接管 provider 资源或调用模型。已消费的 turn skill
不会复活，正在恢复的 turn skill 状态不会被简单 `set_active_skills()` 覆盖。
模型/技能绑定或当前默认正文验证失败时，原 Agent 保持不变。工具 callable、
网络客户端、scope binding、审批 hook 和 runtime 继续由当前宿主重新注入。

## 工具 Registry 的所有权

工具注册 SDK 位于 `xdog.agent.tools.ToolRegistry`，实例归宿主，不存在进程全局
registry。Coding session、Claw group 和 CLI/Flow 执行方各自维护运行时 inventory。
Capability 负责发现、scope 和业务生命周期，提供的实际工具由宿主通过 Registry
SDK 装配；Extension 不再通过 `api.tool()` 或 `tools()/sync_tools()` 贡献模型工具。
MCP 的正常远程工具连接、Extension 的 hooks/commands/UI/providers 不受此删除影响。
详见 `docs/host-owned-tool-registry.md`。
