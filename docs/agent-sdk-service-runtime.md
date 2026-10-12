# xdog-agent SDK：会话恢复、Skills、Compaction 与关闭

状态：**实现与当前Linux验收完成；本次发行版本为2.4.0**。范围与验收记录见
[实施清单](agent-sdk-service-runtime-plan.md)。本文API对应2.4.0；发布状态以仓库工作流和包索引为准。

## 1. 基本运行与工具注册

```python
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult
from xdog.ai.types import TextContent

async def lookup(call_id, args, cancel, on_update, *, ctx=None):
    # 由宿主提供业务实现，并在长操作中响应cancel/协程取消。
    value = await ctx["service"].lookup(args["key"])
    return AgentToolResult(content=(TextContent(text=value),))

tool = AgentTool(
    name="lookup", description="Look up a host record",
    parameters={"type": "object", "properties": {"key": {"type": "string"}},
                "required": ["key"], "additionalProperties": False},
    execute=lookup,
)
async with Agent(stream_fn, config=AgentConfig(model=model_id),
                 tools=[tool], tool_ctx={"service": service}) as agent:
    async for event in await agent.prompt("Find the relevant record"):
        consume(event)
    snapshot = agent.dump()
# Agent已经关闭；stream_fn/service等共享宿主对象未被SDK关闭。
```

`stream_fn`、`service`和`consume`由宿主提供。权限仍通过现有宿主hook实施；工具注册不是OS沙箱。
工具返回普通结果，SDK不根据名字或结果内容推断“提交成功”“必须修复”“立即终止”。
不新增RunResult、统一业务结束原因、terminal工具或错误分类协议。原有错误/工具行为继续沿用。

## 2. Skill加载

```python
from xdog.agent.skills import SkillManager, create_skill_tool

manager = SkillManager(shared_dir=trusted_skill_root)
agent = Agent(
    stream_fn, config=AgentConfig(model=model_id),
    skills=manager, active_skills=["flow-workflows"],
    tools=[create_skill_tool(manager, allowed_slugs=["flow-workflows"])],
)
```

- `flow-workflows`由已安装的`xdog-flow`提供；agent不依赖flow才能运行。
- `skills=manager`配置索引及宿主激活；`create_skill_tool()`是另一项显式注册的只读能力。
- 工具只提供`list`/`load`，不执行指令、不激活skill、不授予工具权限。
- 工具allowlist不同时过滤`skills=manager`产生的宿主索引。只想暴露受限工具目录时，
  不必再把整个manager传给Agent。
- session skill保持在稳定system prefix；turn skill作为本次运行的请求上下文投影，
  不伪装成普通历史消息。未实际进入模型的拒绝/准备失败不消耗pending skill。
- 一次运行包括连续工具轮次；已经使用的turn skill在运行结束后退休。
- 快照保留skill身份摘要和生命周期；恢复通过当前宿主manager重绑，同名内容变化明确拒绝。

可运行的离线示例：`packages/agent/examples/load_skills.py`。支持默认session加载、
`--demo-turn`以及`--mode model-load`。

## 3. 跨进程dump / restore

```python
# 进程A：正常运行结束后
saved = agent.dump()
await agent.aclose()
# 宿主将saved保存为JSON；SDK不代管存储。

# 进程B：新的adapter、工具callable、服务对象和权限hook
agent = Agent(new_stream_fn, config=AgentConfig(model=model_id),
              tools=new_tools, skills=trusted_manager)
agent.restore(saved, allowed_models=[model_id])
# 此时没有模型调用、工具执行或自动压缩。
async for event in await agent.prompt("Next task"):
    consume(event)
```

快照是版本化纯JSON数据：结构化消息及工具关联、原始system prompt、生成参数白名单、
skill状态、SDK压缩摘要/边界、session/run标识。不保存callable、认证配置、客户端、锁或任务。
快照中的业务消息仍可能含敏感数据，存储保护由宿主负责。

模型必须由当前adapter支持；`allowed_models`可以进一步限制恢复的模型。工具、权限、
provider和自动压缩开关始终由当前宿主提供，快照不是能力授权。

已有无版本的部分快照仍走明确的legacy校验路径；缺少字段保持原值。老快照已拼接的skill前缀
无法可靠拆解，不能承诺其拥有新格式的skill/压缩恢复语义。新格式保留`system_prompt`、
`thinking`、`temperature`、`max_tokens`兼容读取字段，但它们必须与规范字段一致；冲突会拒绝。

### 安全边界与中断

- 普通`dump()`要求idle且工具关联完整。不能把半个工具批次伪装成可继续的会话。
- `dump(recovery=True)`显式导出诊断快照，标记为不可直接恢复运行。未确认工具即便已有取消
  错误回执，外部副作用仍可能unknown；`restore()`拒绝自动续跑这些数据。
- `AgentConfig(emit_snapshots=True)`提供工具批次完整结算后的`SnapshotEvent`。
  `event.snapshot`每次返回独立数据副本，不读取稍后已经变化的Agent状态。
- 活跃run的安全快照可恢复后显式`continue_()`，继续其有效turn skill；`prompt()`明确开始
  新任务，不继承上一活跃run已经使用的turn skill。
- 快照不表示数据库事务已提交。没有持久化事件、checkpoint落盘屏障或事件重放协议。

仅能恢复**实际保存过的数据**。旧快照保存之后发生的外部写入可能完全不在快照里；SDK不会
自动重放记录中的工具，但无法阻止模型重新提出语义相同的操作。业务幂等及对账由宿主负责，
不承诺任意SIGKILL点自动续跑或exactly-once。

完整三进程示例：

```bash
uv run --no-sync python packages/agent/examples/service_runtime.py init /tmp/xdog-demo.json
uv run --no-sync python packages/agent/examples/service_runtime.py build /tmp/xdog-demo.json --compact
uv run --no-sync python packages/agent/examples/service_runtime.py repair /tmp/xdog-demo.json
```

使用一个新的临时文件路径；示例拒绝用init覆盖已有文件。

## 4. Compaction：同一个Coding策略，两种入口

```python
from xdog.agent import CompactionConfig

config = AgentConfig(
    model=model_id,
    compaction=CompactionConfig(
        auto=False,                  # 默认关闭
        context_window=32000,        # 自定义adapter应提供真实容量，不是任意估计
        output_reserve=4096,
        instructions="保留目标、失败原因、待办和材料引用",
    ),
)
agent = Agent(stream_fn, config=config, tools=tools)
# idle时手动压缩，不会开启auto：
result = await agent.compact()
if result is not None:
    archive_data = result.removed_messages
```

- `auto=True`才会在每次主模型请求前检查，包括单次prompt内的长工具循环。
- 有模型元数据的adapter可以直接提供真实容量/输出上限；未知容量不能沿用历史默认200k。
- 复用Coding的旧历史摘要+近期保留窗口，80%触发、50%保留目标作为基线，并受输入预算及
  输出预留约束。50%不是“压缩后完整请求一定小于上下文一半”的保证。
- 切点不拆tool-call/result批次；长user turn可以在已完成的工具批次之间压缩。
- system和有效skill仍保留；失败不安装候选历史。摘要模型不获得业务工具。
- 输入计数包含system、工具schema、请求消息等，但仍是估算，尤其图像不是精确tokenizer计数。
- 只有一个内置策略，不提供策略插件。复杂自定义上下文变换必须能与原历史安全对应；
  无法对应时明确拒绝压缩，而不是猜测应删除哪些消息。
- 手动调用返回原文；auto通过Compaction事件提供原文。SDK不保存归档、不等待落盘ACK。
- 缺少安全切点、输入不可再缩小或摘要无法容纳时明确失败，不静默截断或无限重试。

关闭auto不等于无限模型容量；核心工具循环没有新增固定轮数或任务总token上限。

## 5. 观察事件和背压

事件包含session_id、run_id及本次运行中的sequence；不是持久化日志或跨进程事务序号。
保留run/turn/message/tool事件，增加Usage、Compaction及可选Snapshot观察。
主模型和摘要的UsageEvent通过`source`区分，产品不要把既有TurnEnd usage再重复累计。

高层Agent默认最多缓冲256个普通事件，可通过`event_buffer_size`设置正整数。满时等待消费者，
不无限累计或悄悄丢普通事件。关闭时另保留有界清理观察与终态，不因无人消费永久卡住关闭。

**运行时必须消费返回的事件流**；同步`subscribe()`是额外观察接口，不代替有界流的消费。
`stream.result()`保留原有结果协议，不新增RunResult，也不偷偷吞事件。对于有界流，需要
并发消费事件才能让生产者继续推进；取消某个result等待者不会取消共享结果future。

## 6. 关闭与资源所有权

`await agent.aclose()`关闭准入、取消当前模型/工具并等待SDK拥有的任务和请求流释放。
完成后`agent.closed`为True，不能通过reset/restore重新开启该对象；用新Agent恢复快照。
重复/并发关闭等待同一清理任务，取消一个等待者不会取消底层清理；可再次await aclose。

不要在Agent自己的工具/hook内await它的aclose，否则会等待自身；通知宿主关闭。
共享provider/client不由Agent关闭。任意吞掉取消的callable或不可中断native调用无法由Python
安全强杀；宿主可以设置等待期限，但超时不能视为清理成功，也不能释放仍被使用的工作区。

Coding/Claw缓存会话不应每轮结束就调用aclose；Flow一次性SDK节点则在保存后或失败时关闭
其自有Agent。现有Coding恢复分支、Claw transcript/memory、Flow submit_result语义不迁移进SDK。
