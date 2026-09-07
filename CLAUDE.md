# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Soda Prompt Hub 是一个本机优先（local-first）的 AI 绘图创作与数据集中枢：Mac 上的 FastAPI 单体服务
管理提示词、OC、视觉参考、创作项目、结果图和 LoRA 数据集，通过 SMB 共享目录把生成任务投递给一台
Windows 5060 Ti 上的 ComfyUI Worker。训练本身不在本项目范围内。

仓库文档以中文为主（README、`docs/`、DEVELOPMENT_PLAN 等），代码标识符与注释用英文，
面向用户的 UI 文案用中文。新增内容请沿用同一约定。

## 常用命令

依赖与运行全部通过 [uv](https://docs.astral.sh/uv/)（Python 3.12+）：

```bash
uv sync                                   # 安装依赖（dev + test 组默认启用）
uv run prompt-hub serve --host 127.0.0.1 --port 8765
uv run prompt-hub stats | sources | import | doctor
uv run prompt-hub search "gothic cathedral" --limit 10
uv run prompt-hub tag-image /abs/path.png --limit 50
uv run prompt-hub backup | verify-backup <path> | restore <path> --destination <new-dir>
uv run prompt-hub mcp                     # 或 uv run prompt-hub-mcp（stdio MCP server）
```

CI（`.github/workflows/ci.yml`）按顺序跑这四条，本地提交前应全部通过：

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check src/
uv run pytest
```

`pytest` 的 `addopts` 固定带 `--cov=prompt_hub --cov-fail-under=80`，所以跑单个测试时要关掉覆盖率门槛：

```bash
uv run pytest tests/test_creative.py::test_name --no-cov
uv run pytest -k "workflow_profile" --no-cov
```

测试永远不碰真实资料库：`tests/conftest.py` 的 `settings` fixture 用 `tmp_path` 构造一个隔离的
`Settings`，`source_tree` fixture 再在里面铺一套假的 Git 来源目录。新测试请复用这两个 fixture。

## 架构

### 分层与装配

```
config.Settings         → 所有磁盘路径的唯一来源（环境变量 PROMPT_HUB_LIBRARY_ROOT / _DATABASE / _MODELS_ROOT）
  ↓
领域 store / service     → database.py, creative.py, dataset_workspace.py, dataset_curation.py,
                          lora_projects.py, remote_nodes.py, workflow_profiles.py, comfy_results.py,
                          embedding_index.py, model_connections.py, background_jobs.py …
  ↓
*_routes.py             → create_*_router(...) 工厂，接收已构造的 store，返回 APIRouter
  ↓
api.create_app()        → 构造全部 store、注册 router、在 lifespan 里 initialize() 并启动 job runner
```

`api.py` 的 `create_app()` 是唯一的组装点：**所有 store 在这里实例化一次并注入 router**，没有全局单例，
也没有 FastAPI `Depends` 容器。新增一个领域时，照抄这个模式——写 `xxx.py`（纯逻辑 + store）、
`xxx_routes.py`（`create_xxx_router(store) -> APIRouter`），再在 `create_app()` 里 new + `include_router`。

`lifespan` 会调用每个 store 的 `initialize()`（幂等建表/建目录），因此 store 的构造函数必须廉价、无副作用。

### 前端：Python 字符串拼出来的单页应用

没有前端构建、没有 npm、没有静态文件目录。整个 UI 是 `GET /` 返回的一个 HTML 字符串：

- `web.py` 持有基础 `INDEX_HTML` 骨架，文件末尾用 `str.replace()` 把各页面模块注入 `</head>` / `</body>`；
- 每个页面模块导出三个常量：`XXX_STYLES`（`<style>`）、`XXX_HTML`（markup）、`XXX_SCRIPT`（`<script>`）；
- 页面模块：`creative_web.py`（+ `creative_web_layout.py`）、`workspace_web.py`、`lora_web.py`、
  `comfy_web.py`、`search_web.py`、`remote_web.py`、`source_center_web.py`。

这些 `*_web.py` 在 ruff 里被 per-file 豁免 `E501` / `RUF001`，所以行很长、含中文标点是正常的。改 UI 时
认准对应的 `*_web.py`，不要在 `web.py` 里堆新页面。

### 持久化：SQLite 与 JSON 文件两条线

**SQLite**（`Settings.database_path`，多个 store 共享同一个文件）：`database.py`（`sources`/`entries`/
`user_marks`/`oc_*` + FTS5 虚表与同步触发器）、`creative.py`、`background_jobs.py`、
`dataset_curation.py`、`dataset_workspace.py`、`embedding_index.py`。

**库目录下的 JSON 文件 + `threading.Lock`**（`remote_nodes.py`、`lora_projects.py`、
`workflow_profiles.py`、`comfy_results.py` 等）：这些是"事实记录"，需要人能直接打开查看、随备份一起
带走。写入统一走各模块的 `_write_json` 并带 `format: "soda-xxx-v1"` 版本字段。

建表一律用 `CREATE TABLE IF NOT EXISTS` 的可重复执行脚本，并通过 `schema_migrations.record_schema_migration()`
登记 `(component, version)`——这是可查询的版本记录，不是迁移执行框架，不要在里面写 `ALTER TABLE` 逻辑。

### 后台任务

长耗时工作（数据集扫描、WD14 批量打标、Krea 2 VLM 草稿、来源同步、视觉索引）不能阻塞请求，一律走
`background_jobs.py`：`BackgroundJobStore` 把队列存进 SQLite，`BackgroundJobRunner` 在后台线程里
`claim_next()`。handler 注册表写死在 `create_app()` 的 `BackgroundJobRunner({...})` 里。

Handler 签名是 `(payload, JobContext) -> dict`，必须**可取消、可恢复**：定期检查 context 以抛出
`JobCancelledError`，并且重启后重跑同一任务时只处理未完成的条目（服务重启、暂停/继续都依赖这一点）。

### Windows Worker 是独立单文件脚本

`src/prompt_hub/windows_worker.py` 会被复制到 Windows 上当作 `prompt_hub_worker.py` 直接运行
（见 `deploy/windows-worker/`）。它**只允许使用标准库**——不能 import `prompt_hub` 的任何模块，也不能用
numpy/Pillow/FastAPI。它和 Mac 端之间唯一的契约是 `compute_bridge.py` 里的 `compute_contract()`
（`GET /api/compute/contract` 暴露），任务通过 SMB 上的 `outbox/inbox/processing/completed/failed`
五个目录交换 JSON，结果靠 SHA-256 回验。改协议时必须同时改 `compute_bridge.py`、`remote_nodes.py`、
`windows_worker.py` 三处。

## 必须守住的边界

这些不是风格偏好，是产品定义的一部分，`README.md` 和 `completion_audit.md` 对用户做了承诺：

- **只读来源**：公共 Git 提示词库、用户的原始数据集目录、OC Manager 导出、Windows 的 `.safetensors`
  一律不得移动、改名、覆盖或写回。派生物（缩略图、报告、冻结版本）只写进 `library_root` 下的自有目录。
- **不猜数据**：没有真实 embedding 就返回空结果，不用颜色特征或随机向量冒充相似度；JPEG 没有 ComfyUI
  metadata 就显示"无生成元数据"，不补写。
- **人工确认才落库**：模型建议（智能取材、复盘、自动初审、VLM 草稿、WD14）先进预览或草稿，用户点确认
  后才写入；确认时只填空白且未锁定的槽位/字段，不覆盖已有内容与人工判断。
- **凭据不落盘、不回显**：外部模型 API Key 只写 `prompt-library/private/model-connections.json`（`0600`），
  接口和页面永不返回明文；不保存任何 Windows 登录凭据；投递给 Worker 的 payload 会拒绝含密码/token 的字段；
  远程模型 Base URL 必须 HTTPS，只有 loopback 允许 HTTP。
- **默认只听 127.0.0.1**。
- **兼容边界**：`BASELINE.md` 列出的 API 路径、SQLite 表和 MCP 工具名不得无迁移地删除或改变返回语义。
  新功能新增自己的表和模块，不重定义 `entries` / `user_marks` / `oc_*` 的原始职责。
- **不做训练**：Prompt Hub 到"带哈希的交付包"为止；LoRA 训练、正则和最终标签筛选由 Windows 的
  AnimaLoraStudio 完成。

## Lint 约定

`[tool.ruff.lint] select = ["ALL"]`，只全局忽略 `COM812`/`CPY001`/`D`/`ISC001`。剩下的豁免全部集中在
`pyproject.toml` 的 `[tool.ruff.lint.per-file-ignores]`，**按文件逐条列出**——仓库里几乎不用行内 `# noqa`。
新增文件如果确实需要豁免（比如 `*_web.py` 的 `E501`、含中文文案的 `RUF001`、大型 store 的 `C901`），
请在那张表里加一行，而不是散落 `# noqa`。

`ty check src/` 只检查 `src/`，测试不做类型检查。运行时导入用 `from __future__ import annotations` +
`if TYPE_CHECKING:` 保持轻量，这是全仓库统一写法。

## 进度类文档

`task_plan.md`、`progress.md`、`findings.md` 是体量很大的历史开发记录（合计 300KB+），不要整份读入上下文；
需要时用 grep 定位。面向当前状态的权威文档是 `completion_audit.md`、`FINAL_TEST_REPORT.md` 和
`CHANGELOG.md`；`BASELINE.md` 记录不可破坏的兼容边界。
