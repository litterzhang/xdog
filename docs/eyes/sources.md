# Eyes source 配置指南

Source 只采集内容；detector 判断变化，route 选择 handler，handler 执行投递。
以下集中说明 HTTP/JSON、GitHub、HTML、Feed 与时间源；文件与应用推送事件见
[观察契约](observation-contracts.md)。统一运维见 [daemon/tool](daemon-and-tool.md)
及 [传输和历史覆盖恢复](transport-recovery.md)。

采集恢复不等于已补齐历史；不得以空结果、部分分页或当前 HTTP 200 掩盖历史缺口。
原分批实施和测试记录保留在归档中，不是当前配置入口。


## Eyes 外部观察：HTTP 与 GitHub

### 范围与职责

HTTP/JSON 与 GitHub 采集配合
`record.change`、`value.condition`、`availability` 三种确定性 detector。
HTML/CSS、RSS/Atom、Releases 和 scheduler 配置也在本页。
PR review、浏览器、公网 webhook 仍未实现；本地 event 与 Source/Detector 组合见
[观察契约](observation-contracts.md)。
没有 shell source、模型调用、业务优先级规则、公共 Python EyesClient、插件安装器或额外服务。

```text
HTTP/GitHub source：有限采集、协议处理、规范化稳定记录
    → detector：变化/条件/可用性
    → 原 EventRouter / handler：由 JSON 定义投递地址
    → Agent：判断业务意义、是否通知、是否行动
```

Claw/Coding/Goal 接入无需修改；Eyes 不感知 group、session 的业务含义，不管理 Agent 输出。
来源、正文、作者、链接都不是执行权限。owner 仍仅为管理命名空间。

### 内部边界

- `sources/records.py`：冻结的 Record / RecordSnapshot / PollResult 与 JSON Pointer；不做 IO。
- `sources/http.py`：有界只读传输和 HTTP 投影。一个 observation 复用一个连接池。
- `sources/github.py`：仓库资源、分页、窗口与稳定 ID；不使用 Events API 模拟完整日志。
- `sources/remote.py`：原 observation 拥有的资源与脱敏错误，返回候选 state，不写数据库。
- `detectors/records.py`：纯变化与条件逻辑，不调用模型、不访问网络。
- `executor.py`：继续使用原循环、路由、不可变 delivery 与单一 checkpoint 提交点。

Source 不自行启动永久轮询 task/service，不写 journal。候选 source state、detector state 和
待投递 delivery 必须一起提交后才调用 handler。保存失败不会发送，重启只能从原 durable state 恢复。
旧 file/scheduler 的模型和语义保留；新增 evidence 显式解码，不能将“非 scheduler”一律解释成 file。
checkpoint 仍为数据，不允许数据指定导入代码。

### HTTP

```json
{
  "id": "announcements",
  "type": "http",
  "url": "https://example.invalid/announcements.json",
  "mode": "json",
  "pointer": "/items",
  "key_pointer": "/id",
  "poll_interval_seconds": 300
}
```

- `mode=json` 默认比较所选值整个规范化 JSON，key 为 `$`。
- 设置 `key_pointer` 时，`pointer` 必须选出数组，每项必须有唯一 string/int 稳定 key。
  数组顺序不作为成员变化；每项对象的 key 顺序也不影响比较。
- JSON Pointer 按 RFC 6901；不存在、类型不符、重复对象 key、重复记录 key、非有限值均为失败。
- `mode=status` 只记录 `status_code`，不记录响应正文或不断波动的耗时，避免每轮都产生变化。
  2xx/有已提交基线的 304 是健康；已跟随的安全重定向不另外作为失败。
- JSON 模式保存 ETag / Last-Modified 并发送条件请求。304 不清空记录，初次无基线的 304 是错误。
- 单次请求含重定向与读取的总 timeout；限制解压后正文大小。
- 只支持 GET；没有请求体、自定义认证 header、浏览器、整站抓取或任意脚本。
- URL 可以访问调用方选择的 HTTP(S) 地址，包括明确的内网服务。这不是网络沙箱或 SSRF 隔离器。

#### 公共采集预算

| 字段 | 默认 | 范围 |
| --- | --- | --- |
| poll_interval_seconds | 60 | 1..86400 |
| timeout_seconds | 10 | 0.1..120 |
| max_bytes | 1 MiB | 1 byte..16 MiB，GitHub 为整轮总量 |
| max_records | 256 | 1..4096，含安全去重保留所需记录 |
| max_pages（GitHub） | 10 | 1..100，含额外 active run 读取 |
| lookback_seconds（GitHub） | 86400 | 60..2592000 |
| overlap_seconds（GitHub） | 120 | 1..lookback_seconds |

预算不足时明确报错，不静默截断或放大限制。URL 由 source 配置选择，不从宿主 group 推导。

### GitHub

```json
{
  "id": "feedback",
  "type": "github",
  "repository": "OWNER/REPOSITORY",
  "resource": "issues",
  "poll_interval_seconds": 120,
  "lookback_seconds": 86400,
  "overlap_seconds": 120
}
```

`resource` 必须明确：

| 值 | 范围 | key |
| --- | --- | --- |
| `issues` | Issues，过滤 issues API 混入的 PR | resource ID |
| `issue_comments` | Issue/PR 会话中的普通评论，不含 review/行内评论 | comment ID |
| `workflow_runs` | Actions runs，含 attempt | run ID + attempt |

- Issues/comments 用 since + 更新时间排序，重叠窗口重复读取后去重，能发现旧条目的编辑。
- 首次只建立 lookback 范围基线，不播报历史记录。source 不为“真实用户反馈”做语义判断。
- Actions 使用 created 滚动窗口，继续单独读取已知未完成 run，直到其结束；重新运行用 attempt 区分。
- **不承诺检测窗口外已完成老 run 后来才发生的 rerun**。长停机超出 lookback 或时钟倒退时暴露 gap，
  但不再阻断当前采集：保留原 detector 去重状态与已知 active runs，自动采集配置窗口内的记录。
  `gap=true` 为持久历史警告，`coverage_gap` 有界保存首次/最近一次中断的原因、原成功时间、
  恢复时间、查询窗口起点及中断计数；正常轮询、后续请求失败和 daemon 重启都不清除它。
  时钟倒退时窗口以当前时间计算，不能用未来的旧水位构造 since。恢复不等于已补齐历史，
  不将不完整历史冒充新基线。需要全历史审计/更强实时性时，应后续设计 webhook/event ingress。
- 严格处理分页；总 max_bytes、max_pages、max_records 是整次采集预算，不是每页预算。
  超预算、后续页失败、循环/不安全链接均不提交部分集合或推进成功水位。
- Actions filtered search 的服务端 1000 条上限也是缺口，不声称已完整采集。
- 增量结果 `complete=false`，不推断移除。去重只淘汰明确安全 overlap 窗口以前的旧版本；
  不因容量静默淘汰仍需要的记录。容量不足明确失败、保留游标并要求收窄范围/显式重建。
- 第一版 GitHub **不使用跨页 ETag 快捷判断**；没有持久化各页完整缓存前，第一页 304 不能证明整个集合不变。
- `api_url` 默认 GitHub API，允许显式企业 API base；分页和重定向不得逃出原仓库路径。

### 凭据、传输和退避

可选 `bearer_env` 只填写环境变量名字：

```json
{"bearer_env": "GITHUB_READ_TOKEN"}
```

凭据从 **daemon 进程环境** 读取；不会自动借用 gh、Claw 或 Coding 的登录。
验证定义不解析凭据，也不发请求。缺少/非法变量是明确采集错误，不安装工具、不执行命令绕过。
Bearer 仅允许 HTTPS 和标准 token 字符。凭据不进入定义、候选 state、错误或事件；已知 bearer 在远端回显中也会脱敏。
连接池拒绝保存响应 cookie，不能因共享连接池让一个 source 借用另一个 source 的隐式登录。
URL 禁止 userinfo/已知密钥参数。跨 origin 重定向一律拒绝，同 origin 也有次数上限。
HTTPX 使用 daemon 环境的代理与证书设置；有代理需求时在明确启动 daemon 前配置，
不能认为调用方 shell 后来的 proxy_on 会修改已运行 daemon 的环境。

Retry-After（秒或日期）、GitHub remaining/reset 优先于指数退避，不能截短服务端要求的等待。
下次允许轮询的 UTC 时间持久保存，重启不绕过退避。超时/解析/容量/认证错误不带库异常正文或响应体，
防止 credential/URL 泄漏；已有成功游标保留。连接池随原 observation 关闭，不影响共享 daemon。

### Detector

#### record.change

```json
{"id": "changes", "type": "record.change", "source": "announcements", "removals": false}
```

输出 `record.created` / `record.updated`；这是本观察保留范围内的 key 增加/值更新，
不是远端对象全生命周期的创建证明。增量窗口淘汰后再次出现的旧 key 会被视为新观察记录；
判断“新公告/新 Issue”时还应核对 evidence 中的实际创建/更新时间，不把有界去重冒充永久实体索引。
仅在 `removals=true` 且本轮是完整集合时输出 `record.removed`。
未变化、304、失败都不清空基线。正文 JSON 字符串是 evidence，不是自动执行的消息。

#### value.condition

```json
{
  "id": "failed",
  "type": "value.condition",
  "source": "runs",
  "all": [
    {"pointer": "/status", "op": "eq", "value": "completed"},
    {"pointer": "/conclusion", "op": "eq", "value": "failure"}
  ]
}
```

1..16 个 AND 谓词，只支持 `eq/ne/gt/ge/lt/le` 与有限 scalar，顺序比较必须为数值。
布尔值不是数字，缺失字段不满足任何比较（包括 ne）。按 key 记录状态边沿，输出
`condition.met` / `condition.cleared`；新 key 默认从未满足开始，但首次整批只建立基线。
这不是表达式语言，不支持用户代码。

#### availability

```json
{"id": "health", "type": "availability", "source": "api", "failures": 3,
 "for_seconds": 30, "initial_abnormal": true}
```

连续异常次数和持续时间必须同时满足，只在进入异常/实际恢复时输出
`availability.unavailable` / `availability.recovered`。无先前故障不伪造恢复。
`initial_abnormal=false` 可要求先有健康基线。计数和故障开始时间随 checkpoint 恢复；
持续时间基于观察 UTC，时钟校准可能影响时长判断，不是高精度监控 SLA。

### 状态与运维

- `source_health`：每个外部源的 last_success / last_status / failures / next_poll_at / gap / coverage_gap / error。
- `coverage_warnings`：历史覆盖不完整的 source；与当前 `source_errors` 独立。
  当前采集和投递正常时可以是 `watching`，同时保留历史覆盖警告；不能解释为历史已补齐。
- `source_errors`：当前源错误。恢复后清除，不覆盖 handler 的独立不确定/暂停状态。
- `degraded` 表示注册仍在运行但源不健康，**不是暂停**。已完成一次有界外部采集的 degraded 也可作为
  start 的就绪结果；成功注册不等于源健康，调用方必须查看状态。
- pause 停止采集，resume 保留原 checkpoint。停机/暂停超过 GitHub lookback 不自动消除 gap。
- 若需重建基线，用新的显式 key 创建观察，确认范围后取消旧观察；不清 journal、不自动改旧状态。
- 旧 daemon 可以继续管理 file/scheduler。新 CLI 在其不支持 `remote-sources-v1` 时，提交前拒绝外部观察，
  提示显式 stop/start；不会自动杀进程、替换实例、迁移或重放。现有格式的 file/scheduler checkpoint 无需改写。
- Input intake ACK、Agent 执行、用户渠道送达仍是三个不同事实，Eyes 只负责自身观察和 handler 投递。

### 示例与验收

完整可验证的 JSON 位于 `packages/eyes/src/xdog/eyes/examples/`：
`http-announcements.json`、`http-service-health.json`、`github-feedback.json`、`github-actions.json`。
先替换 URL/repository 和实际 ACP endpoint/session，再通过原 Eyes tool 或 CLI 管理。
不因安装、读技能或列工具自动创建观察。

测试覆盖配置、基线/去重、304、失败/恢复、部分分页、预算、游标提交、认证脱敏、路径越界、
超时/限流、未知投递不重放，以及真实本地 HTTP → daemon journal → 事件读取/ACK → daemon 重启。
本地确定性测试不是 GitHub 生产账号或真实 Agent/微信的长时间端到端验收。

## Eyes 第二批：HTML、Feed 与 GitHub Releases

### 支持范围

本批补齐静态内容采集，继续使用原 HTTP 传输、RecordSnapshot、detector、checkpoint、router 和 handler。
不新增 daemon/service、浏览器、脚本执行、页面爬虫、宿主适配或 Agent 循环。
Claw/Coding/Goal 不导入或感知这些新 source 实现。管理入口仍为原 CLI / 已注册 eyes tool。

- HTTP `mode=html`：CSS item/字段选择、文本/属性、相对链接、稳定 key。
- `feed`：RSS **2.0** 与 Atom **1.0**，不是所有历史 RSS 方言。
- GitHub `resource=releases`：已发布 Release，默认排除预发布，永不包含 draft。
- Detector 的 `compare_fields`：有限 JSON Pointer 投影，只影响 record.change，不截断原始证据或屏蔽 value.condition。

Release/网页内容是否与业务有关仍由 Agent 判断。新公告详情、公告类型和有效期仍属于解析服务/Flow，
不放进 Eyes。systemd/journal、metrics、webhook 和 PR review 不在本批范围内。
第三批本地 event 和共用契约见 [观察契约](observation-contracts.md)。

### HTML

```json
{
  "id": "announcements",
  "type": "http",
  "mode": "html",
  "url": "https://example.invalid/announcements/",
  "items_selector": ".announcement-list li",
  "fields": {
    "title": {"selector": "a", "text": true},
    "url": {"selector": "a", "attribute": "href"}
  },
  "key_field": "url"
}
```

字段选择相对于每个 item；空 selector 表示 item 自身。每个字段必须恰好匹配一个元素。
`text=true` 与 `attribute` 二选一。文本规范化空白，去掉 script/style/noscript/template 内容；
属性必须为非空 scalar。href/src 解析为 HTTP(S) URL，拒绝 javascript/file/userinfo/控制字符。
链接基于最终安全重定向后的 URL，尊重唯一有效的 HTML base 元素，但不会抓取 base 或提取的链接。
缓存验证器只复用在直接响应的同一资源上，不向重定向目标转发；重定向页面重新读取正文，避免相同 ETag 掩盖基址变化。
HTTP Content-Type charset 和页面自身编码参与解码，中文页面不依靠模型猜测。

- items_selector 不匹配默认报错，不把页面改版当成“公告全部删除”。
- 若确实允许空列表，显式 `allow_empty=true`；这不保证 selector 不会写错。
- 缺字段、多个匹配、空值、重复 key、超预算都失败，保留上次基线与缓存验证器。
- HTML 配置不接受 JSON pointer/key_pointer；JSON/status 不接受 HTML 配置。
- CSS selector 在验证阶段编译，最多 2048 UTF-8 bytes；最多 32 字段，字段名/属性名都有界。
- 不执行 JS、不登录、不支持浏览器、不自动翻页或进入详情页。

### Feed

```json
{
  "id": "updates",
  "type": "feed",
  "url": "https://example.invalid/feed.xml"
}
```

公共超时、字节和记录预算、认证环境引用、重定向限制、条件请求、代理/证书设置与第一批一致。
Feed 默认间隔 300 秒；其他公共预算保持默认 1 MiB / 256 records / 10 秒。

稳定 key：Atom entry id；RSS guid；RSS 无 guid 时才使用规范化链接。没有稳定身份明确失败，
不使用标题、发布日期或随机 ID。正文/日期只是证据，不假设发布日期能做可靠增量游标。

投影字段固定为 id/title/url/summary/content/published/updated；Atom 外置正文 src 只保存为
content_url，不会额外抓取。Atom xml:base 按层继承；RSS 有效 permalink guid 可以提供 evidence URL。
无条目链接时 evidence URL 回到订阅源本身，不伪造文章地址；多个 Atom alternate 链接先报告歧义，
不默默选择任意语言/格式。

- 初次建立基线，不播报历史文章；相同 ID 修订正文产生 updated。
- 源是有界条目窗口，`complete=false`。条目离开窗口不输出 removed，配置 removals=true 会在加载阶段拒绝。
- 去重仅保留当前窗口，不维护无限增长的永久 ID 数据库；很久以前离开后重新出现的条目是新观察记录，
  不能由此推断远端刚刚创建。record.created 不是远端全生命周期创建证明。
- 真正的空 feed 可以建立空窗口；HTML/登录页、畸形 XML、重复 ID、超记录预算不是空 feed。
- DTD、实体、外部引用全部禁止；不执行 XInclude、不抓取 Atom 外置 content。
- XML 在解析过程中限制 100,000 nodes / 128 层；正文仍受 HTTP 解压后字节上限约束。

### Releases

```json
{
  "id": "versions",
  "type": "github",
  "repository": "OWNER/REPOSITORY",
  "resource": "releases",
  "include_prerelease": false
}
```

分页读取 releases，不发送 Issues 的 since/sort 参数，不使用 latest 端点冒充完整列表。
key 为 Release ID，不是 tag；tag/说明修改可以检测。draft 永不观察；未发布条目不报告；
预发布必须显式 include_prerelease=true。不会下载 assets、安装依赖或执行发布内容。
Release 不等于普通 Git tag，也不覆盖 PR review。

这是有界全量枚举，不套用 Issues 增量 lookback gap 规则，也不要求 Release 返回 updated_at。
分页/响应/记录预算在过滤前约束，不能靠过滤 draft/prerelease 无限制读取；后续页失败不提交部分结果。
不推断移除；取消发布/过滤变化不能冒充远端删除。容量不足时明确报错，不静默只看第一页。

### 比较字段与完整证据

```json
{"id":"content-change","type":"record.change","source":"announcements","compare_fields":["/title","/url"]}
```

**此配置属于 Detector，不属于 Source。**同一次采集可以由不同 Detector 比较不同字段。
最多 32 个唯一 RFC 6901 JSON Pointer。默认缺省/空数组仍比较完整规范化值，与第一批行为一致。
比较投影必须都能在每条记录找到；不存在时报错，不把缺字段当作 None 或悄悄忽略。
key 和 evidence URL 独立于投影。record.change 比较投影，证据和 checkpoint 保留完整最新记录。
因此元数据变化可以不唤醒 Agent，但下次真实内容变化时，before/after 仍对应最新事实。
value.condition 始终使用完整记录，不会因字段未被选入 compare_fields 而漏掉条件变化。
比较缓存不再存在于 Source 记录中；第一批旧 checkpoint 仍可恢复。未发布的 source.compare_fields 已移除，
不保留别名；配置应显式移动到各个需要比较的 Detector。

### 执行、持久化和兼容

- HTML/Feed 与 HTTP JSON 文档解析复用原 observation，放在有归属的 worker 上，不阻塞控制 socket。
  请求与解析共享总 timeout；取消会等待实际有界 parser 结算，不留可能迟到发布的后台任务。
- 这不是恶意代码沙箱，也不是可抢占 CPU/内存配额。HTML 除字节上限还检查树深度/节点数；
  复杂 CSS 来自受信配置，不能声称任意 CSS 都有严格毫秒级硬截止。
- Source 只返回候选 state，detector 与不可变待投递记录仍在原 checkpoint 提交后才发送。
  失败、超时、取消不提交“成功的新基线”；未知投递仍不自动重放。
- 新 source 使用 content-sources-v1 协商。老 daemon 在提交注册前明确拒绝，提示显式重启；
  不自动杀进程、替换实例、迁移或重放。默认新字段不改变旧定义的规范化表示，
  旧 file/scheduler/HTTP JSON/GitHub Issues/Actions 注册可在新版本恢复。
- 只增加 beautifulsoup4、defusedxml 运行依赖（版本有界）；类型桩只放仓库开发依赖。
  没有引入 xdog-agent/Claw/Coding/Flow 运行依赖。

## Eyes scheduler source

A scheduler is an observation source, not another agent scheduler or event
handler. It uses the existing version-2 definition and data path:

```text
scheduler source → scheduler.due detector → scheduler.due event
                  → EventRouter → configured EventHandler (including ACP)
```

A scheduler source does not import Coding/Claw/Flow, run tools, create an agent
loop or install a system cron job. Observation lifecycle belongs to the shared
Eyes daemon; use the ordinary CLI/tool to manage its registration.

### Three mutually exclusive schedules

Exactly one mode is required inside a scheduler source's `schedule` object.

#### One-off

```json
{"id": "reminder", "type": "scheduler", "schedule": {"at": "2030-01-01T09:00:00+08:00"}}
```

`at` is an ISO-8601 datetime with an explicit UTC offset or `Z`. It emits at most
one occurrence in that registration. Validation is time-independent, but a fresh
**start** rejects a past/equal deadline before constructing any handler. A one-off
admitted on time retains its absolute deadline while receivers connect; delivery
may be late, and is never promised to be real-time. Repeating start on an already
active identical definition returns the existing registration, not another event.

#### Fixed interval

```json
{"id": "heartbeat", "type": "scheduler", "schedule": {"every_seconds": 300}}
```

The interval is a finite number, at least `0.001` seconds; booleans are rejected.
Its first period starts **after all handlers connect**. It never fires immediately
just because startup/receiver negotiation was slow. Intervals use a monotonic
clock, so wall-clock adjustments do not accelerate or reset them. Observations
still run at Eyes' approximately 50-ms executor polling cadence: this is not a
high-frequency/real-time timer. A late poll emits only the latest elapsed tick,
not every missed period; the original interval phase is retained.

#### Cron

```json
{"id": "daily", "type": "scheduler", "schedule": {"cron": "0 9 * * *", "timezone": "Asia/Shanghai"}}
```

Cron uses five fields: minute, hour, day of month, month, day of week. An explicit
IANA timezone is required; the host's implicit local timezone is never used.
Numeric fields, standard three-letter month/weekday names, lists, ranges, steps
and wildcards are supported. Seconds/year fields, macros, random/hash expressions
and other nonstandard extensions are rejected. The separate `timezone` field is
only valid for cron; a one-off already contains its own offset.

The directly declared `croniter>=2.0,<7` dependency calculates timezone-aware
occurrences, with bounded (eight-year) matching searches and the normal cron
OR behavior for restricted day-of-month/day-of-week fields. No-match expressions
and invalid/missing timezones fail validation, rather than falling back to an
interval. Linux timezone data must contain the chosen IANA zone.

The first cron deadline is strictly in the future after receiver initialization.
A late active poll emits only the latest matching occurrence. Next deadlines
strictly advance in UTC, so a clock rollback does not rearm an already observed
instant. Local DST behavior follows the timezone-aware cron calculation; two
local clock labels with different UTC offsets are distinct instants.

### Complete definition

Copy the shipped `xdog/eyes/examples/scheduled-review.json` and replace its
endpoint and exact ACP destination. For Claw, an explicit `group:main` address
is valid only when that group and workspace match its gateway configuration.
For coding, use its exact persisted session ID. Eyes does not discover either
harness' private registry.

```json
{
  "version": 2,
  "name": "scheduled-review",
  "sources": [{"id": "daily", "type": "scheduler", "schedule": {
    "cron": "0 9 * * *", "timezone": "Asia/Shanghai"
  }}],
  "detectors": [{"id": "due", "type": "scheduler.due", "source": "daily"}],
  "handlers": [{
    "id": "review", "type": "acp", "delivery": "enqueue",
    "connection": {"transport": "unix", "path": "/tmp/example-private/acp.sock"},
    "session": "EXPLICIT_SESSION_ID",
    "instruction": "Review the project and report findings. Follow target tool permissions."
  }],
  "routes": [{"id": "daily-review", "detector": "due", "handler": "review", "event_types": ["scheduler.due"]}]
}
```

```bash
xdog-eyes validate eyes.json
xdog-eyes start eyes.json
xdog-eyes status REGISTRATION_ID
xdog-eyes stop REGISTRATION_ID
```

File and scheduler sources can coexist. `file.change` must reference a file
source, `scheduler.due` must reference a scheduler source, and each route's event
types must match its detector. A scheduler has no file root/include/exclude fields.
A source can feed several detectors/routes; each occurrence is collected once,
then projected/routed independently.

### Evidence, pause and recovery

Each event retains its source/detector IDs and `type: "scheduler.due"`. Its
structured evidence contains `schedule_id`, a per-occurrence `occurrence_id`,
`scheduled_at`, `observed_at` and `timezone`. Times are UTC Unix seconds;
`timezone` is the cron IANA zone or `UTC` for normalized one-off/interval evidence.
There are no fabricated file paths, digests or before/after file states.

The existing router and handler delivery limits, backoff, stable retry identity,
unknown-effect fences, automatic recoverable enqueue reconnect and destination Inbox permissions
remain in effect. An accepted schedule does not approve any agent tool use.
An enqueue ACK means durable intake, not agent completion. Busy routes may batch
several distinct observed events into one bounded delivery, just as for files;
this does not promise one model call per timer tick.

While the observer is health-suspended or delivery-paused, scheduler cursors skip
elapsed deadlines. Resume does not emit a catch-up burst. A one-off elapsed during
that suspension is completed without delivery. Already queued deliveries retain
their original identities and existing resume/unknown-outcome rules; skipping new
occurrences never silently resends an uncertain prior ACP prompt. A delivery is
bounded at 256 distinct file paths plus scheduled occurrences and the existing
encoded-byte limit. Overflow pauses visibly, rather than growing without bound.

A consumed/skipped one-off is shown as a `completed` source; its registration
remains manageable until explicit stop. Definitions persist, but registrations
and timer cursors are not restored after process restart. Explicit restart starts
a new interval phase or next future cron deadline. An expired one-off cannot be
rearmed by restarting. No persistence, catch-up or exactly-once side-effect
guarantee beyond these existing Eyes lifecycle rules is introduced.
