"""Authored mobile introduction; commands are shared with the HTML homepage."""
from __future__ import annotations

QUICKSTART_COMMANDS = (
    'pip install xdog-coding',
    'xdog-ai login copilot',
    'xdog-coding --permission-mode ask',
)
OPTIONAL_INSTALL = 'pip install xdog-flow xdog-eyes xdog-claw'

SUMMARIES_ZH: dict[str, str] = {
    'coding': '在代码仓库中阅读、修改和验证，让权限与会话留在你的掌控中。',
    'flow': '把脚本、Agent 和人工步骤组成可验证、可恢复的工作流。',
    'claw': '通过终端或微信与持续可用的助手协作。',
    'ai': '统一的模型接口、流式响应与 Provider 认证。',
    'agent': '可组合的 Agent SDK，由宿主控制工具、权限和状态。',
    'tui': '用于终端应用的渲染、输入和消息组件。',
    'eyes': '观察文件变化或时间计划，把事件送到指定处理入口。',
    'skills': '共享 Goal、计划与领域技能，按需发现和使用。',
}

QUICKSTART_MARKDOWN = '''## 准备环境

当前支持 Linux、Python 3.12+。在你自己的电脑或服务器上操作，小程序只帮助阅读和复制命令。

## 安装 Coding

```bash
{install}
```

## 登录模型 Provider

```bash
{login}
```

真实模型调用需要 Provider 账户和认证。示例使用 Copilot；模型以你配置的目录为准，不能保证每个模型都可用。

## 在代码仓库中开始

进入希望处理的代码仓库，然后运行：

```bash
{run}
```

`ask` 模式默认允许只读调用，修改操作需要批准。建议先让助手了解仓库，再给出具体任务。

## 按需增加能力

```bash
{optional}
```

安装到同一 Python 环境，才能发现相关能力。能力被发现不等于自动激活，也不等于获得工具权限。

## 继续阅读

- [Coding 概览](/packages/coding/overview)
- [Flow 概览](/packages/flow/overview)
- [全部组件](/packages/all)
- [常见问题](/faq)
'''.format(install=QUICKSTART_COMMANDS[0], login=QUICKSTART_COMMANDS[1],
           run=QUICKSTART_COMMANDS[2], optional=OPTIONAL_INSTALL)
