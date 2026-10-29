# Eyes 第二批：HTML、Feed 与 GitHub Releases

## 支持范围

本批补齐静态内容采集，继续使用原 HTTP 传输、RecordSnapshot、detector、checkpoint、router 和 handler。
不新增 daemon/service、浏览器、脚本执行、页面爬虫、宿主适配或 Agent 循环。
Claw/Coding/Goal 不导入或感知这些新 source 实现。管理入口仍为原 CLI / 已注册 eyes tool。

- HTTP `mode=html`：CSS item/字段选择、文本/属性、相对链接、稳定 key。
- `feed`：RSS **2.0** 与 Atom **1.0**，不是所有历史 RSS 方言。
- GitHub `resource=releases`：已发布 Release，默认排除预发布，永不包含 draft。
- Detector 的 `compare_fields`：有限 JSON Pointer 投影，只影响 record.change，不截断原始证据或屏蔽 value.condition。

Release/网页内容是否与业务有关仍由 Agent 判断。新公告详情、公告类型和有效期仍属于解析服务/Flow，
不放进 Eyes。systemd/journal、metrics、webhook 和 PR review 不在本批范围内。
第三批本地 event 和共用契约见 [观察契约](eyes-observation-contracts.md)。

## HTML

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

## Feed

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

## Releases

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

## 比较字段与完整证据

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

## 执行、持久化和兼容

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

## 验收

完整示例位于 packaged examples：html-announcements.json、feed-updates.json、github-releases.json。
需替换来源和实际 ACP endpoint/session，通过原 tool/CLI 验证并显式注册；读技能不自动创建观察。

测试覆盖字段和选择器、中文编码、重定向/base、XML 实体/外部引用、窗口轮换、发布过滤、比较降噪、
旧规范化定义/旧 checkpoint、取消 worker、能力协商和真实本地文档到原 Coding Agent 的 ACP 输入链路。
真实 GitHub 长期运行、真实业务网页模板覆盖不在离线测试保证内。

## 验证记录（2026-10-29）

- `uv run python scripts/test.py`：九个测试目录全部 exit 0，**10,516 passed、34 skipped**，434.35 秒。
- 最终补充编码/示例/缓存边界测试后，按 CI 的受控并发运行 Eyes：
  `uv run pytest -c pyproject.toml packages/eyes/tests -q -n 0 --timeout=120 --timeout-method=thread`
  （独立 cache_dir），**496 passed**，133.56 秒。两轮重叠，不相加冒充独立总数。
- `uv run ruff check packages` 通过；九包严格 mypy 通过，**660 个源文件**。
- 三份新示例逐一通过公开 `xdog-eyes validate`；Flow 示例 CLI 测试 **17 passed**。
- wheel/sdist 构建成功，检查包含三个新增解析/worker 模块、三份示例和更新后的技能。
- 真实本地 JSON/HTML/RSS → 独立 daemon → ACP → 原 Coding owner 自动消费测试通过；
  原 ask 权限模式保持，正文只进入不可信消息证据、不进入 system prompt。
- 覆盖 RSS/Atom 更新与窗口轮换、XML 实体/DTD/外部引用拒绝、Release 过滤/分页/说明编辑、
  中文 charset、HTML base/重定向与 ETag 资源边界、投影元数据降噪、旧规范化定义/旧 checkpoint、
  缺失内容能力时提交前拒绝，以及取消必须等待实际 parser worker 退出。
- 打包测试只将明确新增的 beautifulsoup4 / defusedxml 纳入允许依赖；原隔离导入、无其他 XDOG
  运行依赖和无宿主实现的断言保持。

没有调用真实模型、使用生产 GitHub 账号或向用户渠道发测试消息。没有重启现有 Eyes/gateway、
自动创建用户观察、提交、推送或发布版本。离线/本地测试不能替代真实网页模板和长期生产网络验证。
