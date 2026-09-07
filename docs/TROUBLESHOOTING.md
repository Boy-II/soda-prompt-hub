# 常见问题与排错

## 页面打不开

1. 双击安装目录中的 `诊断-Prompt-Hub.command`。
2. 确认诊断显示程序目录、Python 和数据库可用。
3. 若端口被其他程序占用，先关闭占用者，或用一致的 `PROMPT_HUB_PORT` 启动。
4. 页面地址默认是 <http://127.0.0.1:8765/>，不是 Windows 的 `8188`。

## 首页显示开发版或候选版

这是版本通道，不是错误：

- 正式版：适合普通用户长期使用；
- 候选版：准备发布，仍需要验收；
- 开发版：正在开发，不应当冒充稳定发布。

程序版本、`RELEASE.json` 和 Worker 版本应保持一致。数据结构版本独立显示，升级时由程序迁移。

## Mac 看不到 Windows 共享目录

1. 在 Finder 选择“前往 → 连接服务器”。
2. 输入 `smb://设备地址/共享名`。
3. 让 Finder 和 macOS 钥匙串保存登录信息。
4. 在 Prompt Hub 填写 Finder 实际挂载的路径，例如 `/Volumes/PromptHub-Bridge`。

不要把 Windows 密码填进 Prompt Hub，也不要填写 Windows 的 `D:\...` 路径。

## 设备显示“需要创建任务文件夹”

SMB 已挂载且可访问，但交换目录尚未准备。点“创建任务文件夹”，程序会建立 `outbox`、`inbox`、
`processing`、`completed` 和 `failed`。这不会创建或移动模型文件。

## 设备能连接，但提示建议更新 Worker

协议仍兼容，因此任务通道可用；只是 Windows Worker 版本比 Mac 内置版本旧或无法识别。完成手头任务
后按[Windows Worker 安全升级](WINDOWS_WORKER.md#安全升级-worker)更新即可。

## 显示“Worker 需要更新”

这是协议不兼容，不是 SMB 断线。停止旧 Worker，下载与当前 Mac 版本配套的 Worker ZIP，校验、复制
旧 `worker-config.json`、重新自检并启动。

## 任务一直等待 Windows 接收

- 确认 Windows 已开机；
- 确认 ComfyUI 可以在 Windows 本机打开 `127.0.0.1:8188`；
- 确认 Worker 黑色窗口仍开启；
- 在 Mac 设备页重新检查；
- 检查两端是否指向同一个 `bridge_root`。

## `inbox` 已有图片，但页面没有结果

图片仍需由 Mac 验收任务编号、文件大小和 SHA-256。在“任务状态”找到“结果待接收”的卡片，点接收。
不要手工把 `inbox` 文件塞进结果数据库。

## 数据集冻结失败

按交付前检查逐项处理：坏图、来源变化、未审核图片、缺失 Caption、非英文格式、完全重复和同名
`.txt` 冲突会阻止冻结；近似重复通常只作提醒。磁盘空间不足时先释放目标磁盘空间再重试。

## 安全更新失败

更新器在替换程序前会先备份个人资料，在新版本初始化失败时恢复旧程序。保留终端窗口中的失败信息，
再运行旧程序的诊断脚本。失败的新程序副本与旧程序快照默认位于：

```text
$HOME/Library/Application Support/Soda Prompt Hub/program-backups
```

不要直接删除个人资料目录。需要进一步排查时提供诊断输出和版本号，不要提供密码或 API Key。
