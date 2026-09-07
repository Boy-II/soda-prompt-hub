# Soda Prompt Hub 用户指南

Soda Prompt Hub 是一套以 Mac 为中心的 AI 绘图创作与数据集整理工具。它在本地保存提示词、角色、
参考图、创作项目、结果图和数据集记录；需要出图时，可以把任务交给同一局域网内的 Windows
电脑，由 Windows 上的 ComfyUI 执行。

这不是一个云端网站。网页只是操作界面，程序和资料仍在自己的电脑上。

## 1. 可以怎么使用

### 只使用 Mac

没有 Windows 也可以使用以下功能：

- 管理提示词库、视觉参考和网页收藏；
- 导入 OC Manager 角色资料；
- 整理 Anima 与 Krea 2 提示词；
- 使用 LM Studio 辅助检索、整理 Prompt 和分析图片；
- 使用 WD14 生成 Anima 标签草稿；
- 检查、审核并冻结数据集；
- 使用 CLIP 在本地以图找图。

这种情况下，出图仍需手工把 Prompt 或 workflow 交给其他生成工具。

### Mac 与 Windows 配合使用

Windows 可以承担 ComfyUI 出图，并把图片送回 Mac。Prompt Hub 还可以读取 Windows 上的 LoRA、
Checkpoint、UNet、VAE、Text Encoder、放大模型和 ControlNet 清单。

训练仍在 Windows 的 AnimaLoraStudio 或其他训练工具中完成。Prompt Hub 负责把数据准备好，不直接
管理 CUDA、Torch 或训练进程。

## 2. 软件中的主要页面

| 页面 | 用途 |
|---|---|
| 创作台 | 从一句想法、OC 或参考资料开始组织 Prompt，并保存项目版本 |
| 智能检索 | 搜索提示词、参考图、数据集图片、LoRA 示例图和底模示例图 |
| 角色库 | 导入并检索 OC Manager 导出的角色资料 |
| 数据集 | 检查图片、准备 Caption、人工审核并生成交付版本 |
| LoRA 项目 | 确定 Trigger、训练目标、选图规则和数据覆盖范围 |
| 资料管理 | 管理本地 Git 提示词库、网页收藏、来源和许可信息 |
| 设备连接 | 检查 Windows Worker、接收任务结果并同步模型清单 |

## 3. 绘图工作流

一次完整的创作通常按下面的顺序进行：

```text
灵感、OC 或参考图
        ↓
从本地资料库取材
        ↓
整理角色、服装、动作、构图、场景、灯光、画风
        ↓
生成 Anima 或 Krea 2 Prompt
        ↓
选择 workflow、底模、LoRA 和生成参数
        ↓
在 Windows ComfyUI 出图
        ↓
图片返回 Mac，复盘并建立下一版
        ↓
选择满意图片进入数据集
```

创作台使用七个槽位保存内容：角色、服装、动作、构图、场景、灯光和画风。重要内容可以锁定，
本地模型补充资料时不会覆盖锁定项。

### Anima 与 Krea 2

同一个项目可以保留两种 Prompt：

- Anima 使用英文 canonical tags；
- Krea 2 使用英文自然语言描述。

二者是独立 Profile。切换格式不会用一份内容覆盖另一份。

### 项目版本

结果不满意时，可以由当前图片建立 V2、V3。新版本会保留父项目、参考图、参数和修改建议，旧版本
不会被覆盖。界面中的 `V` 表示创作轮次，`R` 表示同一轮的自动保存 revision。

## 4. 数据集工作流

数据集页按照五步工作：

1. 读取一个本地图片文件夹；
2. 检查坏图、完全重复、近似重复和已有 `.txt`；
3. 准备 Anima 或 Krea 2 Caption；
4. 人工决定保留、待复查或排除，并确认 Caption；
5. 运行交付前检查，生成冻结版本。

Prompt Hub 不会移动、改名或覆盖来源文件夹中的图片和 `.txt`。扫描结果、标签草稿和审核状态保存
在自己的资料目录中。

Anima 可以使用 WD14 生成标签草稿。Krea 2 可以使用视觉模型生成自然语言草稿。草稿必须经过确认
才会成为正式 Caption。

冻结版本包含图片副本、同名 `.txt`、`manifest.json`、`audit.json` 和 `hashes.sha256`。每次冻结
都会建立新版本，旧版本保持不变。

## 5. LoRA 数据准备

LoRA 项目页用于回答四个问题：

- 要训练角色、服装、角色与固定服装，还是画风；
- Trigger 是什么；
- 哪些特征必须固定，哪些可以变化；
- 当前图片是否覆盖了足够的角度、姿态、表情、服装和背景。

完成选图和 Caption 审核后，Prompt Hub 可以生成 Anima 与 Krea 2 数据集副本。标签终筛、正则、
smoke test 和正式训练仍在 Windows 完成。

## 6. Mac 与 Windows 如何关联

两台电脑之间有三个组成部分：

```text
Mac 上的 Prompt Hub
        ↓ 写入任务
SMB 共享文件夹
        ↓ Windows Worker 领取
Windows 本机 ComfyUI
        ↓ 生成结果
SMB 共享文件夹
        ↓ Mac 校验并导入
Mac 上的创作项目和结果库
```

### SMB 共享文件夹

Windows 先共享一个普通文件夹，Mac 再通过 Finder 挂载。两边看到的是同一批文件，只是路径写法
不同。例如：

```text
Windows：D:\PromptHub-Bridge
Mac：    /Volumes/PromptHub-Bridge
```

设备连接页面填写 Mac 看到的共享根目录：

```text
/Volumes/PromptHub-Bridge
```

程序会自动使用它下面的 `prompt-hub`，所以不要填写成：

```text
/Volumes/PromptHub-Bridge/prompt-hub
```

这个字段不是 ComfyUI、dataset 或 models 路径。ComfyUI、数据集和模型放在不同硬盘也没有问题。

### Windows Worker

Worker 是一只运行在 Windows 上的小程序。它负责：

1. 从共享目录领取任务；
2. 在 Windows 本机调用 `http://127.0.0.1:8188`；
3. 等待 ComfyUI 完成；
4. 把图片、workflow 和运行记录放回共享目录；
5. 在异常重启后恢复尚未完成的任务。

Mac 不需要直接访问 Windows 的 ComfyUI 端口。ComfyUI 可以只监听 Windows 本机，减少局域网暴露。

Worker 的安装与配置见 [Windows Worker 教程](deploy/windows-worker/README-WINDOWS.md)。

### 共享目录的结构

| 目录 | 含义 |
|---|---|
| `outbox` | Mac 新提交的任务 |
| `processing` | Worker 已领取、正在执行的任务 |
| `inbox` | Windows 已返回，等待 Mac 接收的结果 |
| `completed` | 已经完成并保留的任务记录 |
| `failed` | 失败、取消或可重试的任务记录 |
| `packages` | ComfyUI API Format workflow 包 |
| `datasets` | Mac 复制给 Windows 的冻结数据集版本 |
| `worker` | Windows Worker 文件 |
| `diagnostics` | 自检和诊断结果 |

### 一个任务怎样找到自己的结果

每个任务都有唯一的 `task_id`。任务信封、结果信封、图片和日志都使用同一个编号。`project_id`、
`workspace_id` 和 `run_id` 再把它关联回 Mac 上的创作项目、数据集或运行记录。

Mac 会核对任务编号、任务类型、文件大小和 SHA-256。返回文件必须位于该任务自己的目录中，校验
通过后才会导入。

### 为什么不让 Mac 直接 curl ComfyUI

直接调用 ComfyUI API 适合临时测试。Worker 仍然使用同一套 ComfyUI API，只是在外面增加了持久
任务队列。这样即使 Mac 页面关闭、网络短暂中断或 Windows 重启，任务记录仍然存在；图片、日志和
模型清单也有固定的回传位置。

## 7. LoRA 和底模清单如何同步

模型权重仍留在 Windows。Worker 只读取允许扫描的目录，并返回：

- 名称、类型和相对路径；
- 文件大小和修改时间；
- 可用的 metadata、Trigger 和 Civitai 来源；
- 与模型关联的预览图。

Prompt Hub 不会把 `.safetensors`、Checkpoint 或其他大权重复制到 Mac。用户在 Mac 上选择模型时，
保存的是清单中的名称；实际加载仍发生在 Windows ComfyUI。

## 8. 文件保存在哪里

### Mac

Mac 保存创作项目、提示词索引、OC 导入、收藏、评分、结果图、Caption、数据集审核、冻结版本和
任务原始记录。默认资料目录可以通过 `PROMPT_HUB_LIBRARY_ROOT` 修改。

代码仓库与个人资料目录彼此独立。更新代码不会自动删除个人资料，删除代码仓库也不等于删除数据。

### Windows

Windows 保存 ComfyUI、模型权重、LoRA、训练工具和训练结果。这些目录由用户按自己的硬盘情况安排。

### 共享目录

共享目录用于交换任务、生成结果、模型清单和冻结数据集。它是运输通道，不是 Mac 项目数据库，也
不能代替正常备份。

## 9. 密码、API Key 和隐私

- SMB 用户名和密码由 Finder 与 macOS 钥匙串保存，Prompt Hub 不读取；
- Windows Worker 配置不需要 Windows 登录密码；
- 本地 LM Studio 不会把图片上传到网络；
- 主动选择外部视觉模型时，发送给该服务的图片会离开本机；
- 外部模型的 API Key 保存在个人资料目录，不进入 Git 仓库；
- 公共仓库不包含用户图片、模型权重、数据库或真实 `worker-config.json`。

## 10. 每天怎样启动

只使用 Mac 时，启动 Prompt Hub 即可。

需要 Windows 出图时，顺序是：

```text
启动 Windows ComfyUI
→ 启动 Windows Worker
→ 确认 Worker 显示等待任务
→ 在 Mac 启动 Prompt Hub
→ 检查设备连接
→ 提交出图或模型清单任务
```

Windows 关机不会影响 Mac 已保存的项目、提示词和数据集记录。关机期间不能远程出图或刷新 Windows
模型清单。下次先启动 ComfyUI，再启动 Worker。

## 11. 当前不包含的功能

- 不自动执行 LoRA 正式训练；
- 不管理 CUDA、Torch、显卡驱动或训练器环境；
- 不回写 OC Manager 数据库；
- 不自动覆盖来源数据集；
- 不把 Windows 模型权重同步到 Mac；
- 不提供通用的 Civitai 一键下载到 Windows；
- 视频生成工作流尚未纳入当前版本。

## 12. 常见问题

### 三块硬盘分别放 ComfyUI、数据集和模型，可以使用吗？

可以。共享目录只是任务桥接位置。Worker 的 `lora_roots` 和 `model_roots` 可以分别指向不同盘符；
ComfyUI 只要能在 Windows 本机通过 `127.0.0.1:8188` 访问即可。

### Windows 关机后，Mac 上的软件还能使用吗？

可以继续查资料、写 Prompt、整理图片和冻结数据集。远程出图、同步模型清单和复制交付版本需要等
Windows 再次开机。

### `inbox` 中出现图片，为什么还要在 Mac 接收？

`inbox` 只表示 Windows 已返回文件。Mac 还需要校验任务编号、大小和 SHA-256，确认文件完整后才会
把它放入正式结果库。

### Prompt Hub 会修改原图吗？

不会。来源数据集按只读方式扫描；缩略图、审核记录和 Caption 草稿保存在 Prompt Hub 自己的资料
目录中。冻结时创建新的交付副本。

### 必须使用本地模型吗？

不是。真实资料检索、项目管理和数据集审核不依赖语言模型。LM Studio、WD14 和视觉模型用于生成
建议或草稿，缺少它们时相应的自动辅助功能不可用，已有资料仍可正常查看。
