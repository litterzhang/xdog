# 构建、发布与 Trusted Publishing

所有 workspace distributions 共享版本。公开库/客户端由同一个
`.github/workflows/publish.yml` 构建和发布；不要为 Eyes 或 Skills 新建平行发布工作流。
本页描述仓库配置，不声称任何外部 PyPI 账号或 publisher 已配置成功。

## 发布范围和触发

默认发布八个 distributions：AI、TUI、Agent、Flow、Claw、Coding、Eyes、Skills。
Site 和 workspace umbrella 不在默认发布矩阵内。

- `main` 普通 push 运行 CI，不发布。
- `v*` tag 触发发布。
- 手动 `workflow_dispatch` 的 `packages` 输入是 JSON 数组，可选择一个包。
- 上传由独立 publish job 完成，依赖构建和测试结果，不是本机直接上传。

具体版本、触发策略、权限和 package 列表以实际工作流和各 `pyproject.toml` 为准。

## GitHub / PyPI 授权合同

| 项目 | 值 |
| --- | --- |
| GitHub owner / repository | `litterzhang` / `xdog` |
| workflow 文件名 | `publish.yml`，不是显示名或完整路径 |
| GitHub environment | `pypi-<distribution>` |
| Eyes environment | `pypi-xdog-eyes` |
| Skills environment | `pypi-xdog-skills` |
| OIDC 权限 | 上传 job 的 `id-token: write` |

PyPI 新项目使用 pending publisher，已有项目使用其 Publishing 配置。每包对应自己的
GitHub environment；分支发布和 tag 发布需各自被 environment policy 允许。
成功构建、environment 存在和 dispatch 被接受都不等于 OIDC 授权或上传成功。
不把 PyPI token、账号密码或浏览器凭据写进源码、日志和工作流参数。

## 发布前检查

1. 更新共享版本、内部依赖下限和 lock，确认工作区/构建来源。
2. 执行 [开发测试](testing.md) 中的全量、静态和 Flow 示例检查。
3. 构建 wheel/sdist 并检查 metadata、package data 和打包后的导入。
4. Eyes 使用 `scripts/verify_eyes_artifact.py` 验证 skill、示例、模块、typing marker、版本/tag
   和精确依赖策略。允许依赖由脚本和包元数据共同维护，不能沿用旧的“仅 ACP + croniter”白名单。
5. Skills 的 package data 应包含 Goal/Plan skill、Goal runtime 与只读 UI extension，
   不复制 Flow/Eyes 实现。CI 的实际 artifact 断言是维护入口。
6. 如声称安装后协议/生命周期已通过，实际执行对应 wheel-only 探针；参见 [测试指南](testing.md)。

## 明确选择单包重试

以下命令会启动真实发布；只有获得发布授权、确认来源 ref 和 publisher 后才执行：

```bash
# REF 替换为已审查的 tag 或 commit 对应分支。
gh workflow run publish.yml --ref REF -f 'packages=["xdog-eyes"]'
gh workflow run publish.yml --ref REF -f 'packages=["xdog-skills"]'
```

不要因单个包 `invalid-publisher` 而移动 tag、复用同版本不同产物或盲目重新上传所有兄弟包。
先核对 PyPI owner、repository、workflow 文件名和 environment，修复授权后只选择失败包。
最终核对 Actions 上传结果及实际包版本，而不是只报告“已触发”。

## XHS 下游依赖边界

Claw 的可选 `xiaohongshu-cli==0.6.4+xdog.1` 由 workspace 固定公开 Git 提交，尚未作为该
下游版本上传 PyPI。不能声称独立 `xdog-claw[xhs]` 仅凭索引便能完整安装；需要显式提供
固定 fork，详见 [XHS 接入](../claw/xhs-sdk.md)。发布前不得静默换回缺少新 API 的上游版本。

## 历史记录

旧的 Eyes 首次上传失败、Skills onboarding、当时的授权状态和版本号保留在
[归档](../archive/README.md) 中；它们不是对今天的外部服务状态的证明。
