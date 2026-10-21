# Goal 中文结果与审批说明样式（2026-10-21）

- 普通 Goal tool 结果采用 `ensure_ascii=False` 的有效 JSON，中文和 emoji 直接可读。
  不对任意字符串做 `unicode_escape` 解码，字面 `\\uXXXX`、引号和换行仍能 JSON round-trip。
- 存储、revision hash 与显式/超大数据的 ASCII byte-page 协议保持原样，
  不改分页偏移/摘要，不把显示修复变成持久格式迁移。
- 结果大小继续按 UTF-8 字节约束。未分页的小结果可读；分页数据仍按原有 codec 精确拼接。
- 共享 PermissionPanel 的每行调用摘要（包括折行和滚动后的行）使用主题 dim，
  标题、选项、拒绝/确认、可见性保护和事件处理未变。
- 回归：1122 passed（14.00s），4 条已有 multiprocessing fork 弃用警告。
  Ruff、diff-check 和 skills/tui strict mypy（110 源文件）通过。
  日志：`/tmp/xdog-permission-dim-and-unicode-regression.log`、
  `/tmp/xdog-permission-dim-and-unicode-mypy.log`。

本次没有新增队列到 steer 的转换或运行中权限模式切换，也没有调整用户权限配置。

## 原生 `/goal` 状态展示修复

此前 Unicode 修复只覆盖模型工具结果；原生 slash 命令仍直接用默认 ASCII codec 返回整份
summary。这不是 `goal_ui` worker 是否开启的问题：默认 `/goal` 本来就是 owner-bound 原生命令，
可选 worker 的 `/goals` 是另一个入口。

| ID | 范围 | 可验证验收条件 | 状态 |
| --- | --- | --- | --- |
| GP-01 | 默认状态 | `/goal`、`/goal status` 显示目标、真实状态/等待原因、任务计数及最多 6 项预览、主 Agent 最近进度、下一步和工作目录；不倾倒 criteria/evidence/收据 | 已完成 |
| GP-02 | 完整数据 | `/goal status --json`（别名 `/goal --json`）返回完整 Unicode JSON；不改变模型工具、磁盘格式或分页协议 | 已完成 |
| GP-03 | 历史及操作反馈 | `/goal history [OFFSET] [--json]` 保留 16 项分页；create/pause/resume/clear 使用紧凑真实结果，不虚构 worker/执行成功 | 已完成 |
| GP-04 | 安全及宿主集成 | 展示逻辑在 skills，不在 Coding/Claw 引入 Goal schema；过滤终端控制字符，长文字显式省略；waiting/user 读取不改状态、不续跑；实际两宿主命令路径验证 | 已完成 |
| GP-05 | Coding 多行对齐 | 系统/原生命令首行、后续行与自动折行使用统一 3 列 gutter；任务自身缩进与原消息留白保留 | 已完成 |

- 剩余需求 **0 项**：待实现 0、待验证 0、缺外部环境 0、已完成 5、延期 0。
- 原生命令使用纯 stdlib `goal/presentation.py`。中文/emoji 保留，不盲目解码字面 `\uXXXX`。
  Automation/Delivery 是记录状态，不当作模型已经执行或 Goal 已完成的证明。
- 新测试包含 33 项 formatter 测试和 10 项实际原生命令路由测试；真实 Coding/Claw
  宿主测试同时检查 human 输出、显式 JSON 与存储的真实状态，不将旧 JSON 断言简单删除。
- skills 全部、Coding/Claw Goal 相关及 TUI 回归：**1153 passed，22.74s**；
  4 条已有 multiprocessing fork 弃用警告。日志 `/tmp/xdog-goal-native-ui-regression.log`。
- 修改文件 Ruff 通过；skills strict mypy **22 source files** 通过，
  日志 `/tmp/xdog-goal-native-ui-mypy.log`。
- 未对用户现场 `goal-d45ea4c5db174b3aa91e71b5564e2e29` 发出任何命令，
  未重放子任务/扩大权限，也未声称其 Eyes 端到端验收成功。正在运行的客户端需要重新启动后
  才会加载这次源码修改；没有重启用户客户端或 gateway。

- 对齐后追加回归：91 passed，12.08s，`/tmp/xdog-goal-system-alignment.log`；
  原因是 Coding add_system 仅在首行文本拼接空格，现改为 Text 整块 padding。
