# Claw XHS：本地改造 CLI 与公开 Python SDK

## 当前依赖与发布边界

Claw 的 xhs/all extras 使用 `xiaohongshu-cli[browser]==0.6.4+xdog.1`。
根目录 `tool.uv.sources` 固定到公开 fork：

```toml
xiaohongshu-cli = { git = "https://github.com/litterzhang/xiaohongshu-cli.git", rev = "dc38b54b46087c705384e160f63ea0e1f823e16c" }
```

独立检出只需这个仓库，不依赖 `../xiaohongshu-cli` 或任何本机 editable 路径。
上游浏览器改造 PR：`jackwener/xiaohongshu-cli#89`；上游 PR 不含 XDOG 版本号。
固定提交位于 fork 的 `xdog` 分支，但安装按完整提交哈希，不跟踪可变分支。

**发布边界：** `0.6.4+xdog.1` 尚未上传 PyPI，不能声称单独从 PyPI 安装
`xdog-claw[xhs]` 就能获取它。仓库安装由 uv 固定源码；安装独立 Claw wheel 时也须
显式提供相同 fork（不允许回落到缺少新 API 的上游 `0.6.4`）：

```bash
uv pip install 'xiaohongshu-cli[browser] @ git+https://github.com/litterzhang/xiaohongshu-cli.git@dc38b54b46087c705384e160f63ea0e1f823e16c'
# 在同一环境安装需要的 Claw wheel / xhs extra。
```

## 使用

```bash
# 从 xdog 仓库根目录：
uv sync --all-packages --extra xhs
# CloakBrowser 的许可证与浏览器由用户在原始 HOME 准备：
uv run --package xdog-claw --extra xhs cloakbrowser login
uv run --package xdog-claw --extra xhs cloakbrowser install
# 从有权访问桌面的终端扫码；maotao group 须已配置：
uv run --package xdog-claw --extra xhs xdog-claw channel login --xhs --group maotao
```

SDK 使用 Python CloakBrowser，固定 `headless=False, humanize=True`，不自写 Node.js 桥接。
这是有界面模式；直接调用 SDK，不检查 DISPLAY/Wayland、不因 SSH 提前拒绝、不自动注入
桌面环境变量，由 SDK/操作系统处理实际启动。Claw 不创建 Xvfb、不切换回 headless。
只有显式登录使用浏览器，gateway 启动/轮询不登录。

## 登录职责

- `channels/xhs/login.py`：独占登录租约、候选 HOME、子进程创建/超时/取消结算，候选验证
  和最终 group 绑定。失败保留原凭据；绑定写入失败回滚 Cookie。无虚拟显示资源。
- `channels/xhs/scan.py`：同一 Python 环境的短生命周期进程，只调用公开
  `qrcode_login(timeout_s=240)`，收到 `QrLoginResult` 后用 SDK 的公开保存函数写候选 HOME。
  不运行 CLI 子命令，不调用私有 QR 函数、不识别网页协议、不解析 SDK 异常正文。
- 改造 CLI：唯一浏览器/QR 流程，输出无凭据的阶段错误；手机确认后仍须完整会话和账号验证。
- 父进程：收到成功后再次独立验证候选实际 user_id，再发布 0600 Cookie 和公开账号绑定。

SDK 默认库调用不保存 Cookie；候选导出是 Claw 的显式存储操作。最终路径仍为
`<data_dir>/xhs/accounts/<group-hash>/.xiaohongshu-cli/cookies.json`，不读取 CLI 的全局账号 Cookie。

### CloakBrowser 缓存和许可证

子进程 HOME/XDG 配置/数据/状态目录隔离到候选目录，但 `CLOAKBROWSER_CACHE_DIR` 显式保留
调用方路径（默认调用方 `~/.cloakbrowser`；自定义路径在改变 HOME 前解析）。SDK 自己读取
该缓存中的许可证及浏览器，或继承调用方的 `CLOAKBROWSER_LICENSE_KEY`/binary override。
不把许可证复制进 group，不放 argv、模型消息、普通日志或公开账号记录。

SDK 可以在显式登录时下载/升级其管理的二进制；不要把它称为“完全无下载”。提前准备
SDK 许可证及浏览器，避免登录时才发现环境缺失。人类行为模拟不授予额外权限，也不保证
避开平台验证或审核。原有接口权限和收发安全边界不变。

## 收发和所有权保持

- 适配 API 的同步调用走 owned worker；SDK client 每次明确关闭，写 max_retries=1。
- 账号验证、通知扫描、原帖读取、实际写入期间持有原有凭据租约，登录不能中途换号。
- 分页通知上限、基线、不完整目标、缺口和去重仍由 channel 管理；Core Inbox 接受后推进。
- 回复固定入站 post/comment；原帖按原输入显式读取，不依赖移动的通知窗口。
- 发布仍要求冻结的 workspace 图片；不增加任意收件人或全局账号写入入口。
- SDK 写成功不证明公众可见，输出仍是 unknown，不自动重发。
- 不恢复 HTTP 后端、二维码 PNG、手动 Enter 确认、旧配置迁移或额外服务。

## 验证边界

测试覆盖公开 SDK 结果导出、阶段错误、异常隐藏、候选失败回滚、许可缓存与 XHS HOME
分离、子进程创建中取消结算、原凭据租约和单次写，以及 wheel 的 optional browser 依赖。
真实 SDK 的签名/QR 逻辑配合隔离浏览器与 HTTP IO 验证，不访问实际平台。
未激活许可证、下载/启动真实 CloakBrowser、实际扫码、发帖/评论、重启 gateway 或推送。

## 本次本地验证

- `xiaohongshu-cli==0.6.4+xdog.1` 固定到公开 Git 提交，不再依赖兄弟仓库；
  CloakBrowser 0.7.0 已安装，未激活许可证或下载/启动其浏览器。
- Claw 完整回归：2,218 passed。XHS/打包定向回归：75 passed。包含真实子进程读取调用方测试许可证缓存，确认不把
  许可证复制到候选 HOME；该测试不做许可证激活或网络请求。
- 兄弟 CLI 回归：147 passed、11 platform smoke deselected。
- 全仓 Ruff、九包严格 mypy（687 source files）、冻结离线 lock 校验及 diff whitespace 通过。
- 公开 fork 已可用于独立检出；PyPI 上尚无该下游发行版，独立 wheel 使用者仍须显式提供 Git 依赖。
