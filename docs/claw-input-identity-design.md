# Claw 身份、工具权限与渠道账号

日期：2026-10-30。当前方案保留 group 共享上下文，不实现按受众隔离历史或记忆。
验证与平台实测边界见 `claw-input-identity-verification.md`。

## 核心边界

- 人类主体角色为 admin / user / external_user；非人类输入为 system。
- 角色、工具权限、消息来源、回复目的地相互独立；标签不是模型协议 system role。
- 本地 TUI 与已验证的管理员微信可以映射同一个 owner；渠道名字本身不授予身份。
- 同一 group 使用现有历史、daily log、长期记忆和普通 compaction/handoff；不新增受众存储、
  conversation_scope、历史投影或独立公开摘要。不同 group 的数据仍由各自宿主管理。
- 模型根据 system prompt 中的当前宿主身份决定回复：外部回复不得披露管理员资料、其他人的
  私人交流、凭据、内部指令或宿主信息；使用原有 group 人格，不要求 `PUBLIC.md`。
- **这是模型行为规则，不是上下文隔离或硬保密保证。** 模型可以看到共享上下文；限制工具
  不能阻止它在文字回复中泄露已经看到的信息。外部文本不能改变身份、工具授权或回复绑定。
- 工具权限由宿主硬校验；admin/system 同样受配置约束。ACP 不取得人类确认权或任意聊天收件人。

## 职责

| 组件 | 职责 |
|---|---|
| `core/identity` | 冻结主体/来源、精确身份绑定、配置的工具上限、模型身份说明 |
| Channel | 平台连接、账号核实、评论/帖子引用与收发，不决定 group 权限 |
| InputEnvelope | 持久化真实宿主身份、原输入及回复目标，不信任正文中的角色声明 |
| Orchestrator / Session | 原输入所有权、身份复验、模型清单与工具执行门禁 |
| Transcript / memory / compaction | 现有 group 历史、日志、长期事实和单一交接摘要 |
| Output | 冻结目的地与内容、记录真实发送结果；unknown 不自动重发 |

Claw 不因当前发言者改变 ToolRepo/MCP 注册表或建立第二个 Agent。模型可见清单与执行检查
使用同一个身份工具策略。历史身份记录是归属证据，不是新授权。

## 配置

```yaml
# 宿主实际提供的最大工具集合；profile 不注册、安装或激活工具。
enabled_tools: [filesystem, current_time, message, xhs_post]
identity_bindings:
  - group_id: main
    channel: weixin
    account_id: actual-bot-account
    sender_id: actual-owner-user-id
    principal_id: owner
    role: admin
groups:
  main:
    permission_profiles:
      owner:
        tools: [filesystem, current_time, message]
      automation:
        tools: [current_time, message]
      reader:
        tools: [current_time]
      public:
        tools: [xhs_post]
    principals:
      - id: owner
        permissions: owner
    defaults:
      admin: owner
      system: automation
      user: reader
      external_user: public
```

profile 名字（例如 public）只是配置名称，不声明消息的可见性。

- 主体显式赋值优先，其他主体使用角色 defaults；角色仍在真实身份绑定里，不重复保存。
- `[]` 禁止全部工具；`["*"]` 继承 group 已有上限，不能与其他工具名混写。
- 没有自定义配置时 admin/system 继承 group，user/external_user 无工具。
- 四类 defaults 必须齐全，profile 引用必须存在；重复主体/工具、错误格式拒绝加载。
- 工具名可以包含原有 MCP 工具名；未提供的工具不因 profile 中出现其名字而获得注册。
- 当前回合启动时冻结上限，与最新宿主授权取交集；收窄约束后续执行，放宽不扩大当前回合。
- 子 Agent 只能委派原输入已有的真实工具子集；原身份撤权后不得继续借旧授权执行。
  后来的不同发言者不替换已经接受的子 Agent 原始授权。
- `user_tools` 已移除，遗留配置明确报错，不偷偷降级为默认授权。
- 配置在启动时读取，修改后需重启；不增加热加载服务。

工具名单不是数据沙箱。授予 filesystem/bash/memory 等通用工具，不意味着自动限制其可访问
的数据或动作；提供范围明确的业务工具仍是宿主/工具实现的职责。

## 共享上下文、日志和压缩

每次模型请求使用原有 group prompt 与已选技能、MEMORY.md、现有历史/摘要；额外附带
当前真实输入身份和来源。Host actor 标记只保存 identity/input_id，没有 visibility 标签。
不按 channel/account/post 裁剪历史，也不隐藏其他人的原始工具结果。

Daily log 和长期记忆保持现有文件/工具机制。重要事实应保留“谁说的”、来源与是否已核实，
不把多人的事实写成同一个“用户”的偏好。轮换/压缩前继续检查值得保存的新事实；没有新事实
不强制写日志，不复制 Goal/task 的权威状态。日志不替代会话交接。

Compaction 保留一份普通 group 摘要及近期完整消息，原文检查点/归档保持不变；切点保留与
用户消息关联的 actor 标记。轮换使用原有单一 handoff，含摘要、近期原文引用和维护状态。
不新增 audience capsule，也不让压缩摘要授予权限或宣称未经验证的工作已完成。

历史检索保持现有 group 范围，不增加受众索引。是否可调用 memory 等工具仍由当前身份的
工具 profile 决定。历史、摘要、网站内容和工具结果都不能提升当前输入的身份或执行权限。

不同权限的输入不能隐式混成一个执行范围；普通入队不等于 steering。管理控制和子 Agent
授权仍验证原输入身份，不能靠共享上下文中的旧指令、工具结果或消息标签扩大权限。

## 微信管理身份与回复

本地 QR 保存的确切 user_id 仅在实际 token 与保存 token 一致时作为 owner 默认绑定。
显式身份绑定优先，不按最近发言者、昵称或“我是管理员”的正文猜身份。

普通回复固定原 sender/context；主动通知只选择 group/channel/account 下唯一的已验证 owner。
歧义不发，原目的地撤销后旧 pending 输出不重定向。旧 user-id-map 文件保持原样但不再读写。

## 小红书

独立 HTTP 后端由管理员安装/启动；Claw 不代为登录或绕过验证码。当前适配器使用 proposal
核实的固定上游协议，只接受 loopback HTTP，从配置的环境变量读取可选认证令牌。

```yaml
xhs_enabled: false
xhs_group_id: main
xhs_base_url: http://127.0.0.1:18060
xhs_token_env: XHS_API_TOKEN
xhs_poll_seconds: 60
xhs_replies_enabled: false
xhs_publish_enabled: false
```

```bash
uv run xdog-claw channel login --xhs --group main --base-url http://127.0.0.1:18060
```

登录账号 ID/昵称/revision 独立于发言者与 group 人格保存；重新登录使旧账号绑定失效。
公开评论入口角色 ceiling 为 external_user，配置 admin 映射也不能把评论入口提升为管理入口。

通知只产生评论输入，不自动补抓原帖。首次扫描建立基线，稳定 comment ID 去重，Core Inbox
提交后才推进连接器记录。编辑、自评论、无准确评论目标的 @ 不生成新自动任务；有界窗口
和缺口可见，不承诺完整抓取任意历史。ACK 丢失复用原输入角色/reply policy，不重新授权。

### 原帖读取

`xhs_post({})` 为 opt-in 工具；纳入宿主 enabled_tools 后再由身份 profile 授予。

- 仅显式调用时读取当前评论的原帖，固定原 group/account/revision/comment/feed。
- 不接受模型传入目标或 token；重新核实准确通知及账号，从宿主内取得 feed token。
- 仅返回有界标题、正文、feed_id、untrusted 标记，不返回后端 token、原始响应或评论列表。
- 目标缺失/歧义、缺 token、账号切换、详情帖子不匹配均返回不可用，不猜目标。
- IO 在原应用 loop 上结算，返回前复验原输入权限；旧 reader 在原输入结束后失效。
- 不支持任意帖子、图片/视频或全量评论，不发帖、不回复，不另建缓存或后台任务。
- 返回结果进入普通 group 历史；不是只有当前主体能看见的隔离结果。

### 回复与发布

replies 开启时只发送一条最终文本，不发进度/思考、不把长回复拆成多条；关闭时不创建未来
自动发送的评论积压。模式随原输入冻结，日后打开也不发送关闭期间的历史草稿。

`message.channels` 列出本 group 当前已绑定目的地；`message.publish` 仅允许 admin/system
在原有工具权限内选择发行的发布账号标识，冻结标题、正文及 workspace 图片。普通 send
不能冒充 publish，无任意 group/recipient/multicast。标题≤20字符、正文≤1000，1–9张
PNG/JPEG/WebP，单张≤20MiB，总计≤40MiB；拒绝 URL、越界引用与变更资源。

图片冻结到内容寻址 publications spool。容器后端须以同绝对路径只读挂载该目录，不暴露
整个 workspace。复用现有 output journal，无独立发送队列。上游无可独立核实的送达回执，
HTTP 成功仍是 unknown，绝不自动重发或冒充 delivered。没有私信、视频/定时发布或审核 UI。

默认测试使用合成模型与 MockTransport；不代表真实登录、平台限制、浏览器环境和投递可用。
