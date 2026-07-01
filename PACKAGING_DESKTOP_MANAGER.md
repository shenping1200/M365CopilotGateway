# M365 Copilot 桌面管理器打包说明

## 入口
- 开发运行：`python gui_launcher_v2.py`
- 服务入口：`python app_launcher.py`
- 打包脚本：双击 `build_desktop_manager.bat`
- 打包产物：`dist\M365CopilotManager\M365CopilotManager.exe`

## 当前桌面管理器能力
- 一键启动 `app_launcher.py`
- 自动拉起 Flask API 与 Gradio 控制面板
- 原生浅色管理面板，不依赖内嵌 Gradio 页面渲染
- 实时显示 stdout / stderr 日志
- 检测已有运行实例，避免重复启动导致端口冲突
- 查看账号状态、令牌倒计时、调用统计、输入/输出 Token
- 登录选中账号、批量登录/刷新 Token、刷新状态
- 模型测试、请求日志查看、配置管理
- 打开 API 状态页、外部浏览器打开 Web 面板、打开日志目录
- 停止/重启由本启动器拉起的服务

## 配置与敏感文件
建议不要把账号、Token 写死进 exe。以下文件建议放在 exe 同目录，便于替换和迁移：
- `accounts.json`
- `dashboard_config.json`
- `token_cache.json`
- `substrate_token.txt`
- `logs/`

## 注意
如果已有旧服务占用了 API 或面板端口，启动器会识别为外部实例并直接接管状态，不会重复拉起。

详细运行机制见 `PROJECT_GUIDE.md`。
