# 核心工作流

## 场景一：从灵感到一张图

1. 在“创作台”新建项目，写一句中文想法或从 OC Manager 角色开始。
2. 用七个槽位整理角色、服装、动作、构图、场景、灯光和画风；先锁定不能改变的内容。
3. 点“从本地库智能取材”，只选择有真实来源的提示词与视觉参考。
4. 检查 Anima 与 Krea 2 两种输出。Anima 使用英文 canonical tags，Krea 2 使用英文自然语言。
5. 选择 Workflow Profile、底模、LoRA、尺寸、Steps、CFG、Sampler、Scheduler 和 Seed。
6. Windows 在线时发送给 ComfyUI；Windows 离线时导出 JSON，稍后手工使用。

模型只能提供建议。检索候选仍来自本地资料库；锁定槽位不会被模型覆盖。

## 场景二：Windows 出图与结果回流

启动顺序：

```text
Windows ComfyUI → Windows Worker → Mac Prompt Hub → 检查设备连接 → 投递
```

1. 先用低成本测试确认 workflow 能运行。
2. 任务卡依次显示等待领取、执行中、结果待接收、已完成或失败。
3. Windows 返回后，在任务卡点接收。Mac 会核对任务编号、大小和 SHA-256。
4. 图片进入结果库后，再关联项目、记录失败、建立下一版或加入数据集。

`inbox` 中出现文件不等于已经导入；只有 Mac 验收通过后才进入正式记录。

## 场景三：从结果图到打好标的数据集

1. 在结果图中手动勾选满意图片“加入数据集”。
2. 从项目总览点“送入数据集工作区”。
3. 检查坏图、完全重复、近似重复和已有 `.txt`。
4. Anima 使用 WD14 生成标签草稿；Krea 2 使用视觉模型生成自然语言草稿。
5. 人工核对 Caption，并标记保留、待复查或排除。
6. 运行正式交付前检查。
7. 冻结为独立版本，得到图片、同名 `.txt`、`manifest.json`、`audit.json` 和 `hashes.sha256`。
8. 可把冻结版本复制到已挂载的 Windows 共享目录。

到第 8 步就是 Mac 的交付终点。标签终筛、正则和训练在 Windows 的 AnimaLoraStudio 等工具中继续。

## 场景四：整理已有数据集

1. 在“数据集”填写图片文件夹的绝对路径。
2. Prompt Hub 只读扫描，不移动、不改名、不覆盖图片和原 `.txt`。
3. 按“检查问题 → 准备标签 → 人工审核 → 冻结交付”继续。
4. 移除工作区只删除 Prompt Hub 的派生记录，不删除源目录。

## 场景五：建立 LoRA 数据准备项目

1. 选择 `character`、`outfit`、`character_outfit` 或 `style`。
2. 固定 Trigger，说明固定特征、可控特征、允许变化和禁止漂移。
3. 从已审核的数据集引用图片，并检查角度、姿态、表情、服装、背景和构图覆盖。
4. 分别审核 Anima 与 Krea 2 Caption。
5. 生成新的冻结版本并交给 Windows。

Prompt Hub 不自动运行正式训练，也不管理 CUDA、Torch 或训练器进程。

## 场景六：同步 Windows 的 LoRA 和底模

1. 保持 Windows ComfyUI 与 Worker 运行。
2. 在“设备连接”切换到 LoRA 或底模子页面，点“从 Windows 更新清单”。
3. 任务返回后，在“任务状态”验收并导入。
4. Mac 保存名称、分类、相对路径、metadata、Civitai 来源和经校验的预览图。

权重仍留在 Windows，不会复制 `.safetensors`、Checkpoint、UNet 或 VAE 到 Mac。

## 场景七：Windows 关机时继续工作

仍可在 Mac 查资料、写 Prompt、导入 OC、复盘既有结果、整理数据集、运行 WD14、冻结交付版本。
远程出图、刷新 Windows 模型清单和把交付包复制到共享盘，需要等 Windows 再次开机。
