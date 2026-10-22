> 更新：Goal 正文不再默认激活；以下默认注入描述为历史实现。当前仅发现技能，按需激活，续跑复用已激活正文。

# Goal 生命周期细化：skill 与展示契约（2026-10-21）

## 范围与验收口径

本轮覆盖 Goal 生命周期、原生展示、默认技能使用、scoped child 工具和两宿主接线。
领域逻辑在 xdog.skills.goal；Agent/Coding/Claw 只增加通用权限受限委派接口，
不增加 Goal 业务分支或新执行循环。工作区无关 dirty 保留。
以下清单按可验证行为计数，局部测试不替代最终全回归/发布验证。

## 稳定验收清单

ID 保持稳定，新增验收项另加 ID，不因排序或验证次数重编号。

| ID | 验收标准 | 本轮状态 | 证据 / 尚需验证 |
| --- | --- | --- | --- |
| GLR-01 | 模型 `activate(goal_id, version=observed, reason=...)` 在等待条件成立后将 waiting 改为 active；CAS 冲突须重读判断 | 已完成 | test_lifecycle_refinement、真实两宿主与 scoped Eyes/ACP 链路通过 |
| GLR-02 | active 上 activate 幂等，不增 revision、不制造 wake；waiting→active 也不额外 wake；不能解除 paused 或复活 completed/cleared | 已完成 | 幂等与 wake/epoch 测试通过；activate 不新增 wake cause |
| GLR-03 | resume 仅供显式用户恢复 paused，任何 wait 均不用 resume 释放 | 已完成 | service/native/model 与受保护状态测试通过；resume 只恢复 paused |
| GLR-04 | waiting(tasks) 由 main 判断实际任务条件后 activate；waiting(user/host) 依赖真实用户回复/授权恢复事实，自动 task 事件无授权 | 已完成 | user/host 授权与任务条件测试、实际原主 Agent 链路通过 |
| GLR-05 | waiting(tasks) 仅 task cause 触发；idle 只延后重检已有 cause，不能凭空产生提醒或形成盲循环 | 已完成 | idle 无 cause 测试、三种 deferral/Eyes/receipt 案例通过 |
| GLR-06 | completed 保留 current，可 `/goal` 查看，同时停止该 Goal observer/reminders；只有显式 clear 才无替代地解除绑定 | 已完成 | completed/current 重开、observer 关闭、原生命令与状态展示通过 |
| GLR-07 | 新建可原子替换 completed binding，旧历史仍为 completed；不能覆盖 active/waiting/paused 等未完成 Goal | 已完成 | 同一事务新建替换 completed、原历史和未完成冲突测试通过 |
| GLR-08 | worker 只维护自己任务；main 维护自己的执行/验收任务和 Goal 报告，不替 worker 写状态；接管须显式重分配 | 已完成 | 两宿主实际 scoped task/CAS 与主验收/报告职责测试通过 |
| GLR-09 | formatter 接到 completed current + next=create 时仍展示该 Goal，不说无 current；提示创建是替换、clear 是解除，不修改输入或生成操作 | 已完成 | `test_presentation.py` 的 completed current 测试；不代表 GLR-06/07 已验收 |
| GLR-10 | 模型 activate provenance 展示为 activate/model_tool/agent，而非用户 resume；即使 user_authorized=true 也不冒充原生用户命令 | 已完成 | `test_transition_presentation.py` 的 model activation 参数化测试；只消费提供的事实 |
| GLR-11 | generic capability 默认注入 skill，续跑不重复 filesystem 读手册；正文缺失走已有技能机制、不依赖 repo 路径；reminder 无强制 ReadSkill | 已完成 | 默认正文注入测试及不再要求重读 skill 的实际提醒链路通过 |
| GLR-12 | 内部 child 首选受限 Goal tool，原 parent 显式授予；task_cli 仅外部/无委派宿主 fallback，不作内部默认或权限绕过 | 已完成 | SDK/host 广泛 1501 passed；实际 Goal scope 两宿主 22 passed |

| GLR-13 | 最终九包回归、静态/构建/冷安装验证；版本更新并正常推送 main 和精确 tag，核对远端 SHA、CI 与发布实际结果 | 待验证 | 2.4.5 锁文件已更新；发布前不得把 tag 推送当作发布成功 |

**需求总计 13；已完成 12；待实现 0；待验证 1；缺外部环境 0；延期 0。剩余 1。**
这不代表用户现场 Goal 已完成；现场业务状态未被本轮代码修改操作。

## 模型工作流程

1. 以真实 owner-bound status 为准核对 current ID、status、wait_for、version。
   completed 虽不再可执行，仍然可以是 current。paused/terminal/stale 不执行。
2. waiting(tasks) 的自动任务提醒只要求检查任务，不证明条件已满足。
   main 自己判断真实结果；条件成立才以 observed version 和证据 reason 调 activate。
   未满足则保持 waiting。不能用 resume 释放等待，也不能用 task 事件释放 user/host wait。
3. waiting(user/host) 需核对真实用户回复或授权恢复事实。来源标签、引用回复和
   provenance 不是授权；恢复与未知副作用核对仍是各自的责任，不能自动重放。
4. 可以继续执行时，按既有 skill 读取/比较最新 progress，先保存 main 的评估报告，
   再执行任务。报告 CAS、权限、实际证据与 unknown 来源约束继续有效。
5. 没有进展时评估并调整或等待，不能从 idle 制造任务提醒、从任务数量推断完成。
   pause/completed 停止所属 observation/reminders，不取消已接纳输入、工具或 child。

`activate` 是正在进行的模型轮次对等待条件的判断，不是用户 `/goal resume`，
也不是启动第二个执行循环或再次唤醒自己。原生用户 resume 的既有来源确认仍保留；
unknown 历史来源不让已经 active 的 Goal 再要求授权，也不能解除当前 pause/user/host wait。

## 委派与说明加载

内部 child 的首选授权形状：

```python
tools=['goal']
tool_scopes={'goal': {'goal_id': EXACT_ID, 'task_ids': ['task-1']}}
```

这只描述 Goal grant，不是完整 child 创建请求。Coding/Claw 已接入同一通用接口；
须由原 parent 显式授予、在原有权限边界内限定任务。
需要的执行工具另行授权，不能因提供了 Goal scope 就自动获得工具或 Goal 全局控制。
worker 维护自己的 task，main 在自己的验收 task/Goal report 记录验收。

`task_cli` 保留现有锁/CAS helper 用途，但只作外部 worker 或无 scoped 委派宿主的
已授权 fallback；不能在受限委派失败后偷偷改走 shell/filesystem。
默认 capability 注入的正文已在 session prompt 中，提醒续跑使用已有说明即可。
缺失时调用现有技能发现/加载机制，不能搜索仓库路径作为产品运行前提。
reminder 已改为遵循已加载 skill，仅要求读取实际 Goal/任务/报告。

## 展示与验证记录

- completed + next=create 保留标题/ID/status；把可能误导的无当前 Goal 原因改为
  “Current Goal is completed; create replaces it, /goal clear releases it”。
  真正 current=None 仍显示原有无 Goal 提示；不修改业务事实、revision 或存储。
- waiting(host) 和 waiting(user) 均不被陈旧 next=main_agent_work 提示掩盖。
- activate 复用来源事实展示，不从 route 或 user_authorized 反推原生 user resume。
  缺失来源保持 unavailable；诊断不构成工具授权或修复证明。
- 终端安全、中文/emoji、截断、有界预览、输入不变及原生只读路由继续覆盖。

本轮定向命令：

```sh
uv run pytest packages/skills/tests/goal/test_presentation.py \
  packages/skills/tests/goal/test_transition_presentation.py \
  packages/skills/tests/goal/test_native_presentation.py -q
```

结果：**119 passed（复跑 1.55s）**。其中真实 native 测试使用现有 runtime fixture；
未执行真实模型、部署或用户现场恢复。新生命周期动作、事务、observer 停止与宿主授权
不在这 119 项展示测试的验收结论内，仍按上表待验收。

静态检查：限定 Python 文件 Ruff 通过；`presentation.py` strict mypy 通过
（1 source file）；skill frontmatter 的 quick_validate 通过；本轮文件空白检查通过。

## 集成证据（发布前）

- Goal 生命周期、版本与 provenance、delegate preflight 等契约测试通过；
  `activate` 不新增 wake cause，真实用户恢复 user/host wait 仍更新 authority epoch。
- 子任务域工具 90 项、实际两宿主 Goal scope 22 项、SDK/host 广泛 child/capability
  回归 1501 项均通过，来源由实际绑定校验，未使用 role_tools 权限豁免。
- 内部 child 使用 scope Goal tool 的真实 Eyes/ACP 三种阻塞链路通过；
  主 Agent 按 status → report → activate → complete 执行，完成仍为 current。
- 版本/锁文件/包依赖与核心 Goal 检查：28 passed；workflow examples：17 passed。
- 全回归：`/tmp/xdog-2.4.5-final-regression.log`：9591 passed、7 skipped，九包通过，226.70s。
  静态：`/tmp/xdog-2.4.5-ruff.log`、`/tmp/xdog-2.4.5-mypy.log`。
