# Eyes 外部观察：HTTP 与 GitHub

## 范围与职责

第一批只支持 HTTP GET（status/JSON）、GitHub Issues / 普通评论 / Actions，及
`record.change`、`value.condition`、`availability` 三种确定性 detector。
HTML/CSS、Feed、Release/PR review、浏览器、event ingress/webhook 均未实现，不作为本次支持承诺。
没有 shell source、模型调用、业务优先级规则、公共 Python EyesClient、插件安装器或额外服务。

```text
HTTP/GitHub source：有限采集、协议处理、规范化稳定记录
    → detector：变化/条件/可用性
    → 原 EventRouter / handler：由 JSON 定义投递地址
    → Agent：判断业务意义、是否通知、是否行动
```

Claw/Coding/Goal 接入无需修改；Eyes 不感知 group、session 的业务含义，不管理 Agent 输出。
来源、正文、作者、链接都不是执行权限。owner 仍仅为管理命名空间。

## 内部边界

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

## HTTP

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

### 公共采集预算

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

## GitHub

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
  不将不完整历史冒充新基线。需要全历史审计/更强实时性时，应后续设计 webhook/event ingress。
- 严格处理分页；总 max_bytes、max_pages、max_records 是整次采集预算，不是每页预算。
  超预算、后续页失败、循环/不安全链接均不提交部分集合或推进成功水位。
- Actions filtered search 的服务端 1000 条上限也是缺口，不声称已完整采集。
- 增量结果 `complete=false`，不推断移除。去重只淘汰明确安全 overlap 窗口以前的旧版本；
  不因容量静默淘汰仍需要的记录。容量不足明确失败、保留游标并要求收窄范围/显式重建。
- 第一版 GitHub **不使用跨页 ETag 快捷判断**；没有持久化各页完整缓存前，第一页 304 不能证明整个集合不变。
- `api_url` 默认 GitHub API，允许显式企业 API base；分页和重定向不得逃出原仓库路径。

## 凭据、传输和退避

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

## Detector

### record.change

```json
{"id": "changes", "type": "record.change", "source": "announcements", "removals": false}
```

输出 `record.created` / `record.updated`；这是本观察保留范围内的 key 增加/值更新，
不是远端对象全生命周期的创建证明。增量窗口淘汰后再次出现的旧 key 会被视为新观察记录；
判断“新公告/新 Issue”时还应核对 evidence 中的实际创建/更新时间，不把有界去重冒充永久实体索引。
仅在 `removals=true` 且本轮是完整集合时输出 `record.removed`。
未变化、304、失败都不清空基线。正文 JSON 字符串是 evidence，不是自动执行的消息。

### value.condition

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

### availability

```json
{"id": "health", "type": "availability", "source": "api", "failures": 3,
 "for_seconds": 30, "initial_abnormal": true}
```

连续异常次数和持续时间必须同时满足，只在进入异常/实际恢复时输出
`availability.unavailable` / `availability.recovered`。无先前故障不伪造恢复。
`initial_abnormal=false` 可要求先有健康基线。计数和故障开始时间随 checkpoint 恢复；
持续时间基于观察 UTC，时钟校准可能影响时长判断，不是高精度监控 SLA。

## 状态与运维

- `source_health`：每个外部源的 last_success / last_status / failures / next_poll_at / gap / error。
- `source_errors`：当前源错误。恢复后清除，不覆盖 handler 的独立不确定/暂停状态。
- `degraded` 表示注册仍在运行但源不健康，**不是暂停**。已完成一次有界外部采集的 degraded 也可作为
  start 的就绪结果；成功注册不等于源健康，调用方必须查看状态。
- pause 停止采集，resume 保留原 checkpoint。停机/暂停超过 GitHub lookback 不自动消除 gap。
- 若需重建基线，用新的显式 key 创建观察，确认范围后取消旧观察；不清 journal、不自动改旧状态。
- 旧 daemon 可以继续管理 file/scheduler。新 CLI 在其不支持 `remote-sources-v1` 时，提交前拒绝外部观察，
  提示显式 stop/start；不会自动杀进程、替换实例、迁移或重放。现有格式的 file/scheduler checkpoint 无需改写。
- Input intake ACK、Agent 执行、用户渠道送达仍是三个不同事实，Eyes 只负责自身观察和 handler 投递。

## 示例与验收

完整可验证的 JSON 位于 `packages/eyes/src/xdog/eyes/examples/`：
`http-announcements.json`、`http-service-health.json`、`github-feedback.json`、`github-actions.json`。
先替换 URL/repository 和实际 ACP endpoint/session，再通过原 Eyes tool 或 CLI 管理。
不因安装、读技能或列工具自动创建观察。

测试覆盖配置、基线/去重、304、失败/恢复、部分分页、预算、游标提交、认证脱敏、路径越界、
超时/限流、未知投递不重放，以及真实本地 HTTP → daemon journal → 事件读取/ACK → daemon 重启。
本地确定性测试不是 GitHub 生产账号或真实 Agent/微信的长时间端到端验收。

## 验证记录（2026-10-28）

- `uv run python scripts/test.py`：九个测试目录全部 exit 0，**10,460 passed、34 skipped**，459.31 秒。
- 最终补充采集/权限与闭环用例后：
  `uv run pytest -c pyproject.toml packages/eyes/tests -q -n 0 --timeout=120 --timeout-method=thread`
  （独立 cache_dir），**442 passed**，124.65 秒。两轮有重叠，不相加冒充独立总数。
- `uv run ruff check packages` 通过；九包严格 mypy 通过，**657 个源文件**。
- 四份新示例逐一通过 `xdog-eyes validate`；Flow 示例 CLI 测试 **17 passed**。
- Eyes wheel/sdist 构建成功，检查包含全部 6 个新增采集/配置/detector 模块、4 份示例与更新后的技能。
- 真实本地 HTTP → 独立 daemon → 原 Coding owner 自动消费测试通过；原 ask 权限模式不变，
  远端内容只在消息证据中，未进入 system prompt。
- 模拟 Issues/Actions 响应 → 原 executor → 真实第三方 ACP 进程的 enqueue 测试通过；
  只断言 intake receipt，不假称模型执行或用户渠道送达。
- 真实本地 HTTP → daemon journal → events/ACK → daemon 重启测试通过；原 delivery 身份保持。
- 配置了自动多 worker 的一次额外复测在并行全仓负载下触发原有 3 秒启动断言超时；
  未放宽该断言。显式限制 Eyes 为 `-n 0` 后完整复测通过；CI 脚本本来就对 Eyes 使用该并发设置。

没有访问真实 GitHub 账号、创建用户观察、调用真实模型或向用户渠道发测试消息。
没有提交、推送、发布或重启用户正在使用的 Eyes/gateway。测试不能替代生产网络与长期运行验收。
