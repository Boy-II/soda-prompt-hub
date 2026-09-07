# 设备与文件关系

## 一句话理解

Mac 是资料和审核中枢，SMB 是运输通道，Windows Worker 是取件员，ComfyUI 是绘图执行器。

## 两台设备保存什么

| 位置 | 保存内容 | 是否事实源 |
|---|---|---|
| Mac 个人资料目录 | 项目、Prompt、OC、结果、Caption、审核、冻结版本、任务记录 | 是 |
| Windows 模型目录 | Checkpoint、UNet、VAE、Text Encoder、LoRA 等权重 | 对模型文件是 |
| Windows 训练目录 | 正则、训练配置、训练日志和训练结果 | 对训练过程是 |
| SMB 共享目录 | 任务、回传图片、只读模型清单和冻结数据集副本 | 否，只是通道 |
| GitHub 仓库 | 程序代码、示例配置和公开文档 | 否，不含个人数据 |

## 通信回路

```text
Mac 建立任务 JSON + 源文件 SHA-256
  → 写入 SMB/prompt-hub/outbox
  → Worker 原子移动到 processing
  → Worker 调用 Windows 本机 ComfyUI
  → 图片、workflow、日志写入 inbox/<task_id>
  → 结果信封写入 inbox/<task_id>.json
  → Mac 校验任务编号、类型、大小和 SHA-256
  → 验收后写入 Mac 结果库与 completed
```

断网、关闭浏览器或 Windows 重启不会让任务失去编号。Worker 会根据共享目录中的持久记录恢复。

## 为什么 Mac 页面不保存 Windows 密码

SMB 登录由 Finder 完成，密码由 macOS 钥匙串保管。Prompt Hub 只保存：

- Windows 主机名或局域网 IP；
- 用户给设备取的显示名；
- Mac 已挂载的共享目录路径；
- 允许使用的能力列表。

Worker 配置只保存 Windows 本机路径和 ComfyUI loopback URL，也不需要登录密码。

## 为什么模型只同步清单

Checkpoint 和 LoRA 文件很大，也应由 Windows 的模型管理工具维护。Worker 回传的清单只包含选择
工作流所需的信息：名称、相对路径、类型、metadata、来源和预览图。Mac 保存这个清单后，可以在
创作台选择模型和参数；真正加载权重仍发生在 Windows。

## 数据集怎样交付

Mac 从只读来源图片建立审核记录，冻结时创建独立版本副本并生成 `hashes.sha256`。复制到 Windows
时先写临时目录、校验完整目录后再原子改名。Windows 收到的是可训练输入，不会反向修改 Mac 的
项目或原始数据集。

## 更新怎样隔离数据

- 程序更新：替换 `$HOME/Applications/Soda Prompt Hub` 中的代码和依赖。
- 个人数据：保留在 Documents 下的 `prompt-library`。
- 提示词 Git 来源：由“资料管理”独立更新。
- Windows Worker：使用独立 ZIP 升级并保留真实 `worker-config.json`。
- Windows 模型与训练：不随 Prompt Hub 代码升级。
