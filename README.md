# Soda Prompt Hub

Soda Prompt Hub 是一套本机优先的 AI 绘图创作与数据集整理工具。Mac 保存提示词、视觉参照、OC、
创作项目、结果图、Caption、审核记录和冻结版本；Windows Worker 把经过确认的任务交给 Windows
本机 ComfyUI，并把结果送回 Mac 校验。LoRA 正式训练仍在 Windows 的训练工具中完成。

当前源码版本为 `1.1.0rc2`，已发布的候选版也是 `1.1.0rc2`。稳定版、升级说明和版本规则见
[正式版本体系](docs/RELEASES.md)。

## 五分钟开始

1. 从 GitHub Release 下载完整源码 ZIP 并解压。
2. 打开 `deploy/mac`，双击 `首次安装-Soda-Prompt-Hub.command`。
3. 安装完成后，双击 `$HOME/Applications/Soda Prompt Hub/启动-Prompt-Hub.command`。
4. 浏览器打开 <http://127.0.0.1:8765/>。
5. 首页提示缺少资料库时，确认来源和许可证后再安装。

程序默认只监听本机 `127.0.0.1`。新用户的个人资料默认保存在：

```text
$HOME/Documents/Soda Prompt Hub/prompt-library
```

程序和个人资料彼此独立。重新安装或更新程序不会主动移动、删除提示词、图片、数据库或模型。

## 它能做什么

- 从灵感、OC 或参考图建立绘图项目，并输出 Anima tags 与 Krea 2 自然语言 Prompt。
- 检索本地提示词库、视觉参照、网页收藏和 OC Manager JSON；可按来源快速切换，并只查看带图资料。
- 使用 AnimaDex 的角色、画师和作品缩略图作视觉参照；完整目录仍由用户使用自己的导出 token 下载到本机。
- 使用 LM Studio 或可选的 OpenAI-compatible 模型辅助整理；模型结果先作为建议或草稿。
- 使用 WD14 生成 Anima 标签草稿，人工审核后冻结为带哈希的版本化数据集。
- 通过 SMB + Windows Worker 运行 ComfyUI、回收图片，并同步 LoRA/底模名称、分类、来源和预览图。
- 在 Mac 管理 Workflow Profile、底模、LoRA、尺寸、Steps、CFG、Sampler 和 Scheduler 的选择。

## Mac 与 Windows 的分工

```text
Mac Prompt Hub（事实、审核、版本）
        ↓ 写任务与冻结数据集
SMB 共享文件夹（运输通道）
        ↓ Worker 领取
Windows ComfyUI（出图）
        ↓ 返回图片与运行记录
Mac 校验 SHA-256 后导入
```

Windows 模型权重不会被复制到 Mac；Mac 上选择的是 Worker 回传的只读清单。SMB 密码由 Finder 与
macOS 钥匙串保存，Prompt Hub 不读取密码。更完整的文件关系见[设备与文件关系](docs/ARCHITECTURE.md)。

## 文档导航

| 我现在要做什么 | 应该看哪里 |
|---|---|
| 第一次安装，先把页面打开 | [快速开始](docs/QUICK_START.md) |
| 从灵感到出图、复盘和数据集 | [核心工作流](docs/WORKFLOWS.md) |
| 启动、停止、备份、恢复和安全更新 | [Mac 使用与维护](docs/MAC_GUIDE.md) |
| 在 Windows 安装、自检和启动 Worker | [Windows Worker 完整指南](docs/WINDOWS_WORKER.md) |
| 弄清两台设备和文件怎样关联 | [设备与文件关系](docs/ARCHITECTURE.md) |
| 页面打不开、共享盘断开或任务不动 | [常见问题与排错](docs/TROUBLESHOOTING.md) |
| 版本号、更新通道和发布检查 | [正式版本体系](docs/RELEASES.md) |
| 查看长期开发边界和历史路线 | [开发计划](DEVELOPMENT_PLAN.md) |
| 按情景进行人工验收 | [人工验收清单](MANUAL_ACCEPTANCE_GUIDE.md) |

## 当前边界

- Prompt Hub 交付打好标、经过人工审核的冻结数据集，但不自动开始正式训练。
- 标签终筛、正则、CUDA/Torch 环境、训练参数和长时间训练由 Windows 工具负责。
- OC Manager 通过 JSON 导入，Prompt Hub 不回写它的数据库。
- 外部数据集按只读方式扫描；缩略图、草稿和审核状态保存在 Prompt Hub 自己的资料目录。
- 公共仓库不包含模型权重、个人图片、数据库、API Key、SMB 密码或真实 `worker-config.json`。

## 开发者启动

```bash
git clone https://github.com/cOkieeman/soda-prompt-hub.git
cd soda-prompt-hub
uv sync
uv run --no-sync prompt-hub serve --host 127.0.0.1 --port 8765
```

自定义个人资料位置时使用 `PROMPT_HUB_LIBRARY_ROOT`；自定义模型目录时使用
`PROMPT_HUB_MODELS_ROOT`。开发检查与贡献约定见[开发计划](DEVELOPMENT_PLAN.md)和
[正式版本体系](docs/RELEASES.md)。

## 许可证

代码采用 [MIT License](LICENSE)。第三方提示词、图片、模型、工作流和数据集继续遵循各自来源的
许可证或使用条款，不因本项目采用 MIT 而被重新授权。
