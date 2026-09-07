# Prompt Hub Windows Worker

这个目录是可以独立放到 Windows 上运行的 Worker 发布包。它通过 SMB 共享目录接收 Mac 发来的
任务，只访问 Windows 本机的 ComfyUI，不需要把 ComfyUI 开放到局域网。

当前 Worker 版本和通信协议写在 `RELEASE.json`。请完整保留解压后的目录，不要只下载或复制一只
启动脚本；正式发行包还使用 `MANIFEST.sha256` 检查文件是否完整。

## 先理解四类路径

这些路径不要求位于同一块硬盘：

| 配置 | 用途 | 示例 |
|---|---|---|
| `bridge_root` | Mac 与 Windows 交换任务和结果的共享目录 | `D:\PromptHub-Bridge\prompt-hub` |
| ComfyUI 安装目录 | 运行 ComfyUI，本字段不直接填写到 Prompt Hub 的 Mac 页面 | `C:\AI\ComfyUI` |
| `lora_roots` | 一个或多个 LoRA 文件夹 | `E:\AI-Models\loras` |
| `model_roots` | Checkpoint、UNet、VAE 等各自的文件夹 | `F:\AI-Models\checkpoints` |

例如 ComfyUI、数据集和模型分别放在三块硬盘时，不需要移动文件。只需建立一个容量足够的共享
文件夹作为 `bridge_root`，再在 `worker-config.json` 中分别填写 LoRA 和模型的真实路径。

Windows 可以共享 `D:\PromptHub-Bridge`；Mac 通过 Finder 挂载后可能显示为
`/Volumes/PromptHub-Bridge`。Mac 页面填写的是 `/Volumes/PromptHub-Bridge`，程序会自动使用其下的
`prompt-hub`。它不是 ComfyUI、dataset 或 models 路径。

## 第一次运行

1. 从 GitHub Release 下载 `Soda-Prompt-Hub-Windows-Worker-<版本>.zip`，完整解压后再运行。
2. 右键 `校验发行包.ps1`，选择“使用 PowerShell 运行”；看到绿色 `[OK]` 后继续。
3. 双击 `0-首次配置.bat`。它会保留已有配置，或从示例建立新的 `worker-config.json`。
4. 按当前电脑的盘符修改 `bridge_root`、`lora_roots` 和 `model_roots`；不使用的模型类型可以删除。
5. 启动 ComfyUI，并确认浏览器能打开 `http://127.0.0.1:8188`。
6. 确认已安装 Python 3.12；当前实测版本是 3.12.10。
7. 双击 `1-先自检.bat`。
8. 看到 `[OK] Worker and local ComfyUI are ready.` 后，双击 `2-启动Worker.bat`。
9. 保持 Worker 黑色窗口开启。窗口显示“等待任务”时，Mac 才能投递真实任务。

自检写出的 `worker-status.json` 包含 `worker_build_sha256`，代表实际启动脚本的 SHA-256。任务成功
或失败时，结果信封也会回显同一字段。它可以确认当前接任务的是刚同步的新版 Worker，而不是仍在
后台运行的旧窗口。如果该值与共享目录当前 Worker 脚本的 SHA-256 不一致，请关闭所有旧 Worker
窗口，再重新双击 `2-启动Worker.bat`。

需要接入 ComfyUI LoRA Manager 时，可在 PowerShell 运行 `3-检查LoRAManager.ps1`。脚本只读取插件
源码、metadata/预览文件清单、ComfyUI 本机公开接口和 LoRA 目录统计，结果写入共享目录
`diagnostics/lora-manager-inspection.json`；它不会修改插件、下载模型或读取权重内容。

Worker 只使用 Python 标准库，不需要安装 pip 包，也不需要 Windows 密码或 API Key。`worker-config.json` 中的
`lora_roots` 只登记允许扫描的 LoRA 根目录；`model_roots` 分别登记 Checkpoint、Diffusion Model、
VAE、Text Encoder、放大模型和 ControlNet 目录。Mac 任务只能使用配置中的 `root_id`，不能传入
任意 Windows 路径。复制示例配置后，必须把示例盘符改成这台电脑的真实 ComfyUI 目录。

## 日常顺序

1. 启动 ComfyUI。
2. 双击 `2-启动Worker.bat`。
3. 在 Mac Prompt Hub 投递任务。
4. Worker 串行执行；图片与记录回到共享目录 `inbox`。
5. Mac 校验源文件和回传文件的 SHA-256，之后再进入结果审核。

## 升级 Worker

1. 在旧 Worker 窗口按 `Ctrl+C`，不要直接覆盖仍在运行的目录。
2. 把新版 ZIP 解压到新目录，先运行 `校验发行包.ps1`。
3. 将旧目录中真实的 `worker-config.json` 复制到新版目录。
4. 运行新版 `1-先自检.bat`，通过后再运行 `2-启动Worker.bat`。
5. 在 Mac“设备连接”重新检查，确认页面显示的 Worker 版本和协议兼容。

发行 ZIP 不包含真实 `worker-config.json`、任务、模型、预览图或登录信息。新版稳定运行后再归档
旧目录，可以在配置错误时快速切回。

## 同步 LoRA 清单

1. 保持 ComfyUI 和 Worker 运行。
2. 在 Mac Prompt Hub 的“设备连接”页点击“从 Windows 同步”。
3. Worker 读取 ComfyUI `LoraLoader` 名称、模型文件属性、同名 `.metadata.json` 和关联的封面/示例图。
4. 任务返回后，在任务卡点击“验收并导入 LoRA 清单”。
5. Mac 逐文件校验 SHA-256，保存可检索清单与独立预览缓存；不会复制 `.safetensors`。

metadata 中有可靠的 Civitai model/version ID 或模型页 URL 时，Mac 会显示“查看 Civitai / 提示词”。
本地路径、下载 API 和其他网站不会作为来源链接同步。

快照不会读取权重内容，也不会为大模型计算文件 SHA-256。清单 JSON 与每张预览图分别校验；
图片限制为受支持扩展名、单张不超过 32 MiB、最多 1024 张且总计不超过 2 GiB。

## 同步模型资产清单

1. 在 `worker-config.json` 的 `model_roots` 中填写这台电脑真实的六类 ComfyUI 模型目录；没有的
   类型可以删除对应条目。
2. 重新运行 `1-先自检.bat`，确认输出的 `model_roots` 中所需目录为 `exists: true`。
3. 启动 Worker，在 Mac“设备连接 → ComfyUI 模型资产”点击“刷新模型清单”。
4. 任务返回后，在任务卡点击“验收并导入模型清单”。
5. Mac 会显示类型、文件夹、名称、大小、修改时间和可用的同名示例图，不会复制模型权重。

模型清单支持 `.safetensors`、`.ckpt`、`.pt`、`.pth`、`.bin`、`.gguf` 与 `.onnx`。Worker 只调用
文件系统属性，不打开权重，也不计算大权重 SHA-256。模型旁边与权重同名或以 `.civitai_bak` / `.preview`
结尾的 PNG/JPEG/WebP/GIF 会作为独立预览输出；Mac 对清单 JSON 与每张图片分别做 SHA-256 验收。
同名 `.metadata.json`、`.civitai.info`、`.info.json` 或 `.json` 中的可靠 Civitai ID/模型页也会进入清单。

## ComfyUI workflow 要求

Worker 接收的是 ComfyUI 的 **API Format workflow**，不是网页中普通的 UI workflow JSON。

生成包格式：

```json
{
  "format": "soda-comfyui-package-v1",
  "workflow_id": "anima-smoke-v1",
  "api_prompt": {
    "这里放 ComfyUI API Format 导出的完整节点对象": {}
  },
  "metadata": {
    "note": "可选说明"
  }
}
```

生成包放在：

```text
D:\PromptHub-Bridge\prompt-hub\packages\
```

Mac 投递时会在任务 manifest 中记录生成包的 SHA-256。Worker 校验一致后才会执行。

## 目录含义

- `outbox`：Mac 新投递的任务。
- `processing`：Worker 已领取或正在执行的任务。
- `inbox`：执行成功、等待 Mac 验收的结果。
- `completed`：Mac 已确认完成的任务。
- `failed`：失败或取消的任务及错误记录。

## 停止与恢复

- 正常停止：在 Worker 窗口按 `Ctrl+C`。
- 意外关机：重新启动 ComfyUI，再双击 `2-启动Worker.bat`。Worker 会恢复 `processing` 中的任务。
- 已拿到 `prompt_id` 的任务会继续查询原任务，不重新排队。
- 单机锁会阻止同时打开两个 Worker，避免一张 GPU 重复领取。

## 安全边界

- ComfyUI URL 只允许 `127.0.0.1`、`localhost` 或 `::1`。
- 任务中的相对路径不能越过共享目录。
- 所有 manifest 与输出文件都做 SHA-256 校验。
- 配置、任务、日志都不保存 SMB 密码、API Key 或 token。
- 当前执行 `comfyui_generate`、只读 `lora_catalog_snapshot` 与只读 `model_catalog_snapshot`；训练、VLM 和 Embedding 接口保留，但尚不会被这个 Worker 误执行。
