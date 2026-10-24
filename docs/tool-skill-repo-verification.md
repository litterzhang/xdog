# ToolRepo / SkillRepo 迁移验证

日期：2026-10-24。本文记录工作区迁移结果，不表示已提交、发布或部署。
当前接口与可运行示例见 [SDK 能力装配](sdk-capability-assembly.md)。

## 已实现

- `ToolRepo` 注册实际实例，默认提供独立的 bash/filesystem/current_time；
  隔离宿主使用 `builtin=False`，注册自己的 executor。无工厂目录或隐式来源替换。
- `SkillRepo` 默认发现 packaged skills，宿主通过 register 注册目录或实际 Skill；
  同名不同定义失败，不再依赖来源优先级覆盖。select 返回有限子集。
- CapabilitySet 可配置 repo、选择 provider 并以异步上下文打开；未选 provider 不启动。
- create_agent 的 tool_repo/skill_repo 是附加 union，空子集不会删除已选能力的贡献。
  没有工具/技能 enable 名单。原生 Agent 的低层 tools/skills 接口仍可独立使用。
- 技能索引和正文分开。每个 Agent 的只读技能读取器绑定自己的最终集合，不污染共享 repo。
- Coding、Claw、Flow、Eyes、Goal、代码生成、SDK 示例和 artifact 检查均迁移。
- 删除旧 ToolRegistry/SkillManager 实现模块及导出，没有兼容别名。MCP 仍是独立 McpRegistry。

## 特别覆盖的风险

- 同一 capability owner 创建多个 Agent，不同附加技能子集不会通过复用读取器互相泄漏。
- 普通与委派技能的 direct load、列表、只读读取器、merge 和再委派均遵守 select 的边界。
- capability 增删/替换工具时，只更新实际变更的贡献；宿主工具的注销收据仍有效。
  旧收据不能注销后来注册的同名工具。
- 附加库同名不同工具不能替换能力工具；失败的创建/发布不改变原库存与权限。
- 跨进程快照恢复仍由宿主注入工具、模型和权限，不重放未确认副作用。
- 目录注册不抢先读取 FIFO/不安全内容；实际技能工具仍有 no-follow、有界读取和取消等待。

## 本次验证

1. `uv run python scripts/test.py`：九个测试路径全部通过，**10,261 passed、34 skipped**，
   总用时 376.52 秒。各包结果：

   | 包 | passed | skipped |
   | --- | ---: | ---: |
   | agent | 2211 | 15 |
   | coding | 2597 | 12 |
   | claw | 1930 | 0 |
   | flow | 876 | 0 |
   | eyes | 343 | 0 |
   | skills | 726 | 0 |
   | tui | 783 | 0 |
   | ai | 738 | 7 |
   | site | 57 | 0 |

2. 后补的 SkillRepo 测试及 owner/snapshot 针对性回归共 **29 passed**；与上表有重叠，
   不把它们直接相加宣称为独立全量总数。
3. `uv run ruff check packages` 通过。
4. 九包严格 mypy 通过，检查 632 个源文件。
5. `uv run xdog-flow test packages/flow/examples/ --allow-script-stub`：**17 passed**。
6. `uv build --all`：九包与 umbrella 共 **10 个 wheel** 及对应 sdist 构建成功。
   agent wheel 含两个新 repo 模块，不含已删除的旧 registry/manager 模块；技能资源仍在包内。
7. 在新建临时环境中离线安装本次 wheel，从仓库外执行 SDK smoke：
   只选 Flow、自动获得 flow/skill 工具、附加空子集不删除能力贡献、正文未默认激活、
   Eyes binding 未启动、快照可 JSON 序列化。没有调用模型或运行 workflow。
8. 文档离线示例与 Site 路由测试通过，`git diff --check` 通过。

## 边界与限制

- 测试使用离线模型及本地 fixture，不证明真实模型的语义质量，也不声称完成所有真实服务互操作测试。
- Flow CLI 不会继承宿主 Agent 的沙箱、审批 hook 或工具清单；受限宿主仍需自己的执行边界。
- repo 是 SDK 集合，不是权限系统或运行时；宿主仍负责实际调用校验、并发、资源关闭与持久化。
- repo 注销不回滚已执行效果，也不自动变更现有 Agent；库存发布须在原 owner 的合法时点进行。
- 工具资源由原创建者拥有；关闭 capability 不删除宿主目录，不停止共享 Eyes daemon。
- 旧接口是有意删除的破坏性变更。外部消费者需要迁移；本次没有升级外部 Youju 环境或重启 gateway。

## Codegen 独立复核

同日再次单独验证（均为上面 Flow 全量的相关覆盖，不重复累加总数）：

- `test_codegen.py`、`test_codegen_builder.py`、`test_codegen_sdk_lifecycle.py`：
  **82 passed，8.84 秒**。包含生成源码编译/lint、真实 Agent 执行、结构化提交、
  条件/循环/并行和端口映射一致性、工具清单覆盖、技能/会话继承、取消及关闭失败。
- bundle、subflow、checkpoint、scheduling、inherit、inherit_runtime 六个测试文件：
  **115 passed，6.20 秒**。
- 额外在冷安装 wheel 的独立 Python 进程执行新生成的 SDK workflow，主动阻止导入
  `xdog.flow`：实际使用 SDK ToolRepo、manifest 自定义 reverse 工具、本地 SkillRepo、
  三轮离线模型事件和结构化 submit_result，输出 `{"result":{"value":"cba"}}`；
  真实 Agent 已关闭，导出的 session 可 JSON 序列化，没有真实模型调用。
  本次临时产物在 `/tmp/xdog-codegen-cold-ntqo0v7j/`，不作为永久仓库资源。

上述冷进程案例是不含 subflow 的 SDK workflow，不意味着所有生成物都无需 Flow。
SDK 节点仍依赖 Agent/AI；subflow 的 Flow 运行时依赖保持原有明确边界。

## 2.5.0 发布前复核（2026-10-24）

- 统一升级九包与 umbrella 的版本、内部最低依赖和协议版本标识至 2.5.0，更新 uv.lock。
- `uv run python scripts/test.py`：**10,275 passed、34 skipped**，九个测试路径全部通过，
  总用时 **384.32 秒**；包含后补的 SDK README/隔离回归和首页测试。
- Ruff、九包严格 mypy（632 个源文件）、17 个 Flow 示例测试均通过。
- `uv build --all`：10 个 wheel 与对应 sdist 构建成功。
- 首页只使用现有 hack.css/site.css，基础模板和全站样式未修改；真实 Chromium 检查
  360/390/800/1440 像素宽度，无页面横向溢出，锚点与折叠交互正常。
- 这是本地发布前证据，不把它等同于 GitHub Actions、PyPI 上传或线上部署成功。
