# Coding 提示词与技能选择边界

## 一条提示词构建路径

`core/system_prompt.py` 只接受提示词实际需要的工作目录、平台快照、配置指令、
已发现的项目指令、可选只读工具清单和只读状态。返回 `SystemPromptBlock` 元组：

1. 固定宿主契约，可缓存：Coding 产品身份、宿主权限与信任边界、如实报告、
   当前事实按需查询；没有强制工作风格或每次都调用某个工具的要求。
2. 动态上下文，不缓存：环境、用户配置、带原始来源路径的项目指令、可选
   纯文本模型清单和实际只读状态。用户/项目配置都不能扩大宿主权限。

不再临时构造完整 RuntimeConfig，不接收无用的模型/thinking/权限/session_id 参数。
会话事实由当前真正暴露的 `ext_coding_session` 工具提供，其 schema 描述查询方式；
不因提示词拼装而重新启用被关闭的工具。副作用、批准和会话所有权仍归原控制器。

自动项目指令只读取当前目录的 AGENTS.md，保持原有 no-follow、UTF-8、
64 KiB 上限和显式失败行为。不扫描 .coding 目录里的指令 Markdown；
.coding/settings.json 仍是配置文件，不受本次指令入口清理影响。
已验证来源通过 `ContextFile` 进入构建器，带路径 JSON 编码，不再混入通用的
file/image/reference 字典入口。用户后来读取的普通文件和外部事件不会自动升级成
项目或宿主指令。此重构不自动创建或改写项目文件。

## 原生工具与只读清单

以 Agent 的实际能力字段为准：

| supports_tool_calls | 原生 API schema | 额外文本清单 |
| --- | --- | --- |
| True | 是 | 无 |
| None | 是，沿用 Agent 语义 | 无 |
| False | 无 | 只读检查，明确不能通过文本执行 |

清单只包含实际选中的工具，空清单不宣称具备工具能力。
工具参数以 JSON 保留，不再用丢失 schema 约束的手工格式化器。
没有另一个原生能力判断辅助函数或兼容默认分支。

## 技能选择

技能发现、索引和 session/turn 正文放置继续由 Agent SkillRepo/SkillPlacement 完成。
Coding 不重复生成活跃技能标题、正文或 /unload 的模型说明；UI 命令仍正常可用。

显式 activate/deactivate：先构建候选基础提示词，通过 SDK 准备/校验技能选择，
成功应用后才发布 Coding 的显式选择。文件读取失败、选择校验失败或提示词准备
失败会直接返回错误，不再吞异常并报告“已激活”。过程在现有 idle 控制边界内
同步完成；不增加第二个执行循环、技能管理器或授予权限的机制。

下一轮准备仍通过 owned worker 读取项目指令和共享 SDK 的技能 placement，
保留原始 session/branch/workspace 校验。turn 级技能继续由 SDK 在实际执行后退休。

## 直接删除的旧入口

- ProjectResources / load_project_resources / reload_resources
- get_package_resource_dir / load_text_resource / load_prompt_fragment
- SESSION_RESUME_TEMPLATE 及旧通用 File Context/工具文本格式模板
- build_runtime_section / build_tool_section / build_file_context_section / _format_parameters
- _native_tool_calls / _push_skills / _skills_context
- 构建器的旧 RuntimeConfig、tools、file_entries、extra_context、native_tool_calls、session_id 参数

不提供别名、转发或旧参数分支。当前仍在使用的 COMPACTION_SUMMARY_TEMPLATE 保留；
不借本次提示词改造替换压缩执行或其扩展/恢复协议。

## 验证要点

- None/False/True 三种能力下，检查实际模型 Context 的工具 schema 与 system 块。
- 固定前缀不随目录/配置/只读状态/模型查询结果变化，动态内容不写缓存标记。
- Anthropic 实际协议转换保留固定块的 cache_control，不缓存动态块。
- 项目指令刷新与来源隔离，普通文件不自动加载。
- 技能激活/撤销在加载、应用、提示词准备失败时状态不变；slash 命令不谎报成功。
- session/turn 级技能单次放置、显式卸载、压缩、重开、权限、子 Agent 和安装后行为回归。

## 验证记录（2026-11-03）

- 完整 Coding 套件：2,601 passed、12 skipped；其余八包全部通过。
  九包合计 10,855 passed、34 skipped。
- 针对性补测覆盖原生工具三态、缓存块的实际 Anthropic 协议转换、项目指令刷新、
  技能激活/撤销失败原子性、slash 命令失败不报成功、正文单次放置与显式卸载。
- 首轮完整 Coding 发现两个 /plan UI 测试使用了没有 SkillRepo 的旧夹具，过去
  吞异常后只检查主机标签而未验证 SDK 正文；夹具现与真实 SDK 装配一致并验证
  实际 Agent 选择。另一 UI 测试现等待实际结算回执被处理，而非假定一帧 poll
  就处理所有事件。生产代码未加入缺失技能的隐式 fallback。
- Ruff 和九包严格 mypy 通过（687 个源文件）；Flow 示例 17 项通过。
- 全部 distributions 构建成功；wheel-only ACP/Native/输出回执验证和 Coding Goal
  默认命令、实际工具生命周期、权限/只读边界及 owner 轮换验证通过。
  冷安装同时断言新缓存块结构和旧资源/技能提示入口缺席。
- 保留已有未提交修改；未提交推送、未改版本或重启在线服务。
