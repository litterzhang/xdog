# 开发与测试

从仓库根目录执行，使用根 `pyproject.toml`。安装环境和测试时间是不同指标；不承诺
全量测试固定在一分钟内。旧的裁剪计数和单次测量保留在历史归档，不作为当前覆盖或时延保证。

## 环境与 CI

```bash
uv sync --locked
# 与当前 CI 一致：包含可选 XHS Python SDK，不下载或启动真实浏览器。
uv sync --locked --all-packages --extra xhs
```

XHS 的下游 SDK 固定公开 Git 提交，不依赖本机兄弟目录，详见
[XHS 接入](../claw/xhs-sdk.md)。CI 和发布流程分别见 `.github/workflows/ci.yml`、
`.github/workflows/publish.yml`；修改时以实际工作流为准。

## 全量和定向测试

```bash
uv run python scripts/test.py
uv run pytest -q
uv run pytest -n 0 packages/agent/tests/test_agent_loop.py -q
uv run pytest -n 0 packages/eyes/tests -q
```

`scripts/test.py` 使用根配置的同一组 testpaths，只启动一次 pytest、两个 worker；
直接 pytest 默认也是两个 worker。不要同时运行多套全量测试，也不要把 package 池与
pytest worker 相乘。两种入口均没有 CI 专用子集、自动重跑、失败过滤或慢测试排除。
并行度、冷启动、安装和机器负载都会影响测量。

自动化环境建议显式设置 `TERM=xterm-256color`，以匹配 Unicode 布局测试的终端能力；
`TERM=dumb` 会正常切换生产渲染为 ASCII，不能把这个差异误报为 Unicode 布局回归。
测试用的终端能力不是宿主权限或运行时配置。

## 静态、示例与构建

```bash
uv run ruff check packages
uv run mypy --strict \
  -p xdog.ai -p xdog.agent -p xdog.tui -p xdog.flow \
  -p xdog.site -p xdog.claw -p xdog.coding -p xdog.eyes -p xdog.skills
uv run xdog-flow test packages/flow/examples/ --allow-script-stub
uv build --package xdog-eyes --out-dir dist
python scripts/verify_eyes_artifact.py dist
uvx twine check dist/*
```

构建和安装后验收不同：editable import 不能证明 wheel 的资源、依赖和安装入口正确。

## scripts 与 test_support

- `scripts/test.py`：CI 和发布使用的全量入口。
- `scripts/verify_eyes_artifact.py`：CI、发布和打包测试共用的独立 wheel 策略。
- `scripts/verify_acp_artifacts.py`：手动 wheel-only ACP 验收入口，并调用 Claw 的
  Eyes、Native、output 子探针。普通测试读取部分探针，不等于完整执行隔离验收。
- `scripts/verify_{goal,coding_goal,claw_goal}_artifact.py`：手动安装后 Goal 验收。
- `test_support/`：保留真实宿主共用的隔离 Eyes daemon fixture；
  不是生产 runtime，也不是第二套 SDK。

手动探针并未全部接入 CI。要声称安装后链路通过，必须实际执行对应探针，不能仅凭源码、
compile 或历史报告推断。临时目录较大时可设置 `TMPDIR=/var/tmp`；不能清理不属于本次测试的环境。

## 有价值的回归

优先在拥有行为的最小边界断言协议、权限、取消、ownership、持久化、代码生成一致性。
跨包共享契约用于验证不同真实宿主；少量代表性集成测试覆盖 socket/worker/文件/终端交互。
避免为普通 dataclass 默认值、标准库复制行为或同一全链路的每个表现重复构造大型矩阵。

异步行为使用明确事件、barrier、原 receipt 或有界 readiness 检查，不以任意 sleep、重跑、
增加超时、接受错误状态或跳过测试来掩盖竞态。失败诊断应保留最后的真实状态。

## 历史证据

[归档](../archive/README.md) 保存当时的源码基线、审计结论、测试数、未验证环境和发布记录。
状态 complete、某次绿色 CI 或历史 wheel 验收都不自动覆盖后来修改。


## 风险驱动的主套件（2026-11-05）

以 `acbe424` 为精简前基线。计数来自同一个根配置下
`uv run pytest -c pyproject.toml --collect-only -n 0 -q` 的**参数化展开后**节点；
不是文件数或测试函数数。没有更改 testpaths、文件匹配规则、收集插件或排除目录。
原始精简保留 1,099 cases；2.6.1 新增一条真实 session 恢复回归，当前为 1,100。

| 包 | 精简前 | 保留 |
| --- | ---: | ---: |
| agent | 2,239 | 179 |
| ai | 777 | 100 |
| claw | 2,257 | 166 |
| coding | 2,621 | 166 |
| eyes | 574 | 131 |
| flow | 892 | 154 |
| site | 64 | 11 |
| skills | 702 | 94 |
| tui | 816 | 99 |
| **总计** | **10,942** | **1,100** |

### 保留的风险，而不是覆盖率目标

- **AI / Agent**：原生模型能力与失败契约、两种主要 wire 协议、工具调用/结果身份、
  实际执行入口授权、只读扩展 grant/回调撤销、repo/capability/MCP 生命周期、取消结算、压缩及 session 标题。
- **Flow**：有类型私有端口、错误图拒绝、解释器/代码生成的分支/循环/并行/结构化输出
  一致性、继承、checkpoint 漂移拒绝和不重复执行、节点取消与工作区约束。
- **Coding / Claw**：真实宿主队列与控制器、权限模式和信任来源、原 owner/receipt 栅栏、
  历史提交与中断不重放、子 Agent 显式续跑与授权上限、轮换交接、输出未知状态、
  微信图片落盘/游标原子顺序，以及 XHS 单次写入/凭据隔离。
- **Eyes / Goal**：真实本机 ACP 链路、断开自动重连与 namespace 拒绝、ACK 丢失的同 ID
  恢复、source/detector/router 解耦、GitHub 长缺口、HTTP 密钥隔离、调度时钟/暂停，
  Goal 的单 current、任务文件权威、CAS/事务、持久提醒合并和 user wait/pause 不被自动解除。
- **TUI / Site**：输入/粘贴及 Unicode 完整性、终端清理/标题恢复、可见审批与确认、
  导出/草稿的取消和落盘、主要站点入口、演示单槽和真实示例装载。

### 真正删除与合并的内容

- 删除每个 helper/字段/default/导出名都各写一条的测试，和以源码字符串、内部类布局、
  计数器/缓存位置或旧接口“不存在”为结果的结构检查。没有要求每个模块都留测试。
- 删除两宿主各 441 cases 的完整共享子执行矩阵及其无用测试支架；以真实宿主的核心
  spawn/权限、显式继续、取消、队列及未知效果不重放场景替代，未把原矩阵移到别处。
- 权限效果原有 295 cases 把路径表示、操作别名和所有模式做全笛卡尔积；改为边界明确的
  read/write、相对路径/符号链接逃逸、unknown fail-closed、tool_data 审批和四种模式代表。
  manager 场景保留原审批/拒绝/调用断言，删除同一策略的重复纯函数矩阵。
- 收窄重复的无效值枚举、同一异常处理分支的异常类型、布局宽度/颜色/等价编码组合。
  有独立语义的空 fan-out、并发上限、协议/时钟和权限区别仍分开断言。
- 测试函数已物理删除，未增加 skip/xfail、未把参数化改成循环、未把测试移到手动套件。
  少量仍被引用的 fixture/helper 提取到 `*_helpers.py`，其中没有挪过去的测试函数。
  保留测试中的关键 assert 与既有循环未改写；资源测试的 CAS 进程池限为两个 spawn 进程，避免 fork 多线程 pytest worker。

### 主动接受的缺口

这不是完整兼容性或安全证明。主套件不再穷举 MCP/OAuth 的每一种可选交互、扩展 UI
表单与主题、第三方代理的每种响应字段、child worker 的每个崩溃写盘切点、所有损坏 JSON
形态、所有 Unicode/终端组合，以及每个调度安装/网站内容排版变体。保留了核心拒绝和
不重放边界，但局部细节回归可能更晚才暴露。新增真实 bug 时优先加入最小回归，重新评估
已有重复项，而不是把 1,000 当永久硬上限或用覆盖率堆回矩阵。

保留测试均为本机/离线或受控替身验收，不证明真实模型服务、微信/小红书平台送达、浏览器
验证码或掉电持久性。原有独立 wheel 安装探针未挪入或冒充本次 pytest 结果。

最终验收（2026-11-05）：

```bash
TMPDIR=/var/tmp TERM=xterm-256color uv run python scripts/test.py
# 1100 passed in 151.70s (0:02:31); 0 skipped, 0 xfailed, 0 warnings

uv run ruff check packages test_support scripts/test.py
# All checks passed!
git diff --check
# clean
```

最终保留套件完整执行，不是增量/定向结果；同一时间只有这一套全量测试、两个 pytest
worker。AST 比较确认保留测试的 assert 未改变，没有增加 for/while 来承接删除的参数化；
宿主/模型/第三方平台未执行线上验证。精简提交 `84f9de4` 没有生产源码改动；2.6.1
另修复未知 host metadata 导致的 session 恢复误拒绝，并新增该条回归。已将安装能力的
冷启动移到两条 receipt 测试的 setup，避免把无关启动 IO 算进两到三秒的结算期限；没有
提高超时或放宽结算断言。
