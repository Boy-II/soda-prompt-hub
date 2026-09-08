# Windows Worker 完整指南

Windows Worker 是 Mac Prompt Hub 与 Windows 本机 ComfyUI 之间的任务执行器。它不需要 Windows
登录密码或 API Key，也不要求把 ComfyUI 的 `8188` 端口开放到局域网。

## 下载与首次配置

1. 从 GitHub Release 下载 `Soda-Prompt-Hub-Windows-Worker-<版本>.zip`。
2. 完整解压到一个固定目录，不要在 ZIP 内直接运行。
3. 右键 `校验发行包.ps1`，选择“使用 PowerShell 运行”。看到绿色 `[OK]` 后继续。
4. 双击 `0-首次配置.bat`。它会建立 `worker-config.json` 并用记事本打开。
5. 修改 `bridge_root`、`lora_roots` 和 `model_roots` 为这台电脑的真实路径。
6. 启动 ComfyUI，确认 <http://127.0.0.1:8188> 能打开。
7. 双击 `1-先自检.bat`。
8. 自检通过后双击 `2-启动Worker.bat`，并保持窗口开启。

建议使用 Python 3.12。Worker 只使用 Python 标准库，不需要额外安装 pip 包。

## 四类路径

| 路径 | 作用 | 是否可在不同硬盘 |
|---|---|---|
| `bridge_root` | 与 Mac 交换任务和结果 | 是 |
| ComfyUI 目录 | Windows 本机执行出图 | 是 |
| `lora_roots` | 允许扫描的 LoRA 文件夹 | 是，可配置多个 |
| `model_roots` | Checkpoint、UNet、VAE 等目录 | 是，可分类配置 |

Windows 可共享 `D:\PromptHub-Bridge`；Mac Finder 挂载后可能是
`/Volumes/PromptHub-Bridge`。Mac 页面填写 Finder 看到的共享根，不填写 ComfyUI 或模型目录。

## 每天启动

```text
先开 ComfyUI → 再双击 2-启动Worker.bat → 最后从 Mac 投递
```

正常停止时在 Worker 窗口按 `Ctrl+C`。意外关机后，先恢复 ComfyUI，再启动 Worker；它会检查
`processing` 中尚未结束的任务。单机锁会阻止同时启动两只 Worker。

## 版本与兼容性

自检生成的 `worker-status.json` 会记录：

- `worker_version`：当前 Worker 版本；
- `release_channel`：正式版、候选版或开发版；
- `protocol_version`：Mac/Windows 通信协议；
- `worker_build_sha256`：实际运行脚本的 SHA-256。

Mac 设备页会分别显示“连接是否成功”和“Worker 版本是否合适”。只要协议兼容，版本较旧通常只会
提示建议更新，不会误报为断线；协议不兼容时必须先更新 Worker。

## 安全升级 Worker

1. 在旧 Worker 窗口按 `Ctrl+C`。
2. 下载并解压新的 Worker ZIP 到新目录。
3. 先运行 `校验发行包.ps1`。
4. 把旧目录的真实 `worker-config.json` 复制到新目录；不要反向覆盖新版示例文件。
5. 双击新版 `1-先自检.bat`。
6. 自检通过后启动新版 Worker。
7. 在 Mac“设备连接”重新检查，确认当前 Worker 版本和协议兼容。
8. 新版稳定运行后再归档旧目录。

发行 ZIP 不包含真实 `worker-config.json`、任务、模型、图片或登录信息。

## LoRA 和底模清单

Worker 只读取配置白名单内的路径。它可以回传名称、类型、相对路径、大小、修改时间、metadata、
Civitai 来源和同名预览图；不会读取权重内容，也不会把大权重复制到 Mac。

模型清单支持 Checkpoint、Diffusion Model/UNet、VAE、Text Encoder、放大模型和 ControlNet。
LoRA Manager 检查可运行 `3-检查LoRAManager.ps1`，结果写入共享目录的 `diagnostics`。

## ComfyUI workflow

Worker 接收 ComfyUI 的 **API Format** workflow，不是普通 UI workflow JSON。仓库附带
`examples/comfyui-smoke-empty-image-v1.json`，只生成 128×128 空白测试图，不加载底模，可用于验证
任务领取、图片返回和 SHA-256 校验。

## 共享目录

| 目录 | 含义 |
|---|---|
| `outbox` | Mac 新投递的任务 |
| `processing` | Worker 已领取或正在执行 |
| `inbox` | Windows 已返回、等待 Mac 验收 |
| `completed` | 已完成任务记录 |
| `failed` | 失败或取消记录 |
| `packages` | ComfyUI API workflow 包 |
| `datasets` | Mac 交付给 Windows 的冻结数据集 |
| `diagnostics` | 自检和插件检查结果 |

## 安全边界

- ComfyUI URL 只允许 `127.0.0.1`、`localhost` 或 `::1`。
- 任务路径不能越过共享目录。
- manifest 和输出文件都做 SHA-256 校验。
- Worker 不保存 SMB 密码、API Key 或 token。
- 当前正式执行 ComfyUI 出图以及 LoRA/模型只读清单；训练仍在 Windows 现有工具中操作。
