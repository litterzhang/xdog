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

`scripts/test.py` 读取所有配置的 testpaths，按 CPU 预算
最多并行四个 package；较大包内部使用两个 worker，其余包不再层层扩张并发。
直接 pytest 使用根配置的默认 worker。两者不通过过滤失败/慢测试来满足时长目标。
并行度、冷启动、安装和机器负载都会影响测量。

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
- `test_support/`：真实宿主共用的子执行契约、隔离 Eyes daemon 和时钟/定义辅助；
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
