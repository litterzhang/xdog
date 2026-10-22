# Claw 主动消息与 Goal 交互收口

## 边界

`message.send` 是一次明确的消息工具调用，不是回复模式切换、不触发新模型轮，
也不修改原输入或 Goal 生命周期。普通回复仍原路返回。Claw 的通用提示描述主动
发送；Goal 不知道微信/TUI，不实现发送器、worker 或第二个调度器。

同一 group 的多个渠道可能属于同一人，因此一次逻辑消息只能选择一个渠道。
TUI 读取同一会话历史、订阅 ACP 执行展示，与实际向用户投递是不同事实；不把
界面显示当成微信已送达，也不因为有多个视图就主动多播。模型仍可能在不同调用
中写出语义相同的文本；工具按实际调用身份去重，不做猜测性的全文语义去重。
Claw 提示明确要求发送后不在另一个普通回复中重复全文。

## 工具

```json
{"action":"channels"}
{"action":"send","channel":"<宿主刚返回的ID>","text":"需要通知的内容"}
```

`channels` 只暴露当前实际输入所绑定 group 的 live 目标 ID/名称；不返回账号、
收件地址、上下文 token 或连接句柄。必须在同一操作中先读取列表，新发送不能
从 event 或历史中直接借用旧目标 ID。`send` 不接受 group_id、recipient 或渠道
数组。选择后重新核对实际目标，目标发生变化就要求重新读取，而非静默改投。
微信捕获固定的 account/peer/context；Native TUI 捕获原连接和当前 group 代次，
断线、切组再切回或新连接都不能继承旧目标。测试用 stdout TUI 不冒充 live 目标。

可选 `reference` 只是业务关联 ID，不是地址或授权。例如宿主无关的 Goal 问题
记录给出 question.id 时，主 Agent 可以通过发送工具携带它。Core 输出日志保留
真实传输回执，Goal 通过可选宿主只读查询查看该关联的送达事实。未关联回执显示
`untracked`，不是证明未发送；`unknown` 不能被自动重试或说成 delivered。
`delivered` 仅表示关联输出得到渠道成功回执，不代表用户已阅读、已回答，
更不代表任务完成；关联文本是否完整表达问题仍由主 Agent 负责。查询
固定当前 group，不根据 event 的 source/recipient 信任或路由；伪造 canonical
provenance 的 reference 不能冒充 host 创建的主动消息工具输出。

旧 `message.send(group_id,text)` 和广播式 `_send_fn` 移除；不是兼容入口。
子 Agent 只有在父 Agent 的实际工具授权中包含 message、存在原操作消息句柄且
权限仍有效时才能调用，仍受原 parent/child readonly、active tool 和权限子集检查。
内部句柄不进入会话或子 Agent 持久化快照。

## 实现与验收清单

| ID | 可验证行为 | 验证 |
|---|---|---|
| CM-01 | 只枚举当前 group 的已绑定 live 渠道，不暴露 route secrets；原生连接有 group 代次围栏 | 定向测试 |
| CM-02 | 单次 send 只投递一个目标；其他 group/recipient/多播参数拒绝，普通回复路由不变 | 工具/真实 Core 输入测试 |
| CM-03 | 发送复用现有 OutputStore/OutputDelivery，原调用身份重复不重发、不改文本/渠道/reference；未知结果不自动重试 | 真实日志/外部 API 替身测试 |
| CM-04 | 目标变化、断线、权限收窄与子 Agent 工具子集不扩大权限，已接受输出不转给新 peer | 围栏/子 Agent 测试 |
| GQ-01 | 新 wait(user) 原子保存明确问题、影响和恢复条件；旧记录明确未记录、不改历史 digest | Goal 模块测试 |
| GQ-02 | /goal 与只读 view 明确下一步责任方，active 不是运行成功或用户已收消息 | 原生展示/共享 view 测试 |
| GQ-03 | 问题 ID 稳定、可关联实际宿主输出，保存≠送达；无回执、ACK 未知和部分投递保持原意 | 两层真实集成测试 |
| GQ-04 | 提醒正文仅包含新增原因，原接受游标不等于处理完成，不重放旧副作用 | coalescer/epoch/原回执测试 |
| GQ-05 | 参数错误定位具体 action/字段，区分硬阻塞、信息限制、用户选择；主 Agent 判断验收，工具权限归宿主 | 参数/skill/权限测试 |

清单共 9 项行为要求；测试数量不替代需求数量。最终验证证据与仍未验证的边界
在交接中明确报告，不把确定性模型/外部 API 替身测试声称为真实微信网络验收。
