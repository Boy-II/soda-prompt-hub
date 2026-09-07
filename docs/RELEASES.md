# 正式版本体系

## 版本号

Soda Prompt Hub 使用 `主版本.次版本.修订版本`：

- 主版本：出现需要迁移使用习惯或接口的重大不兼容变化；
- 次版本：增加向后兼容的功能；
- 修订版本：修复问题，不增加新的使用流程。

开发过程使用 PEP 440 后缀：

- `1.1.0.dev1`：开发版；
- `1.1.0rc1`：候选版；
- `1.1.0`：正式版。

开发版和候选版不得以正式版名义发布。首页、健康接口和 `/api/system/version` 都从 Python 包元数据
读取同一程序版本。

## 四类独立版本

| 版本 | 来源 | 作用 |
|---|---|---|
| 程序版本 | `pyproject.toml` 包元数据 | Mac 应用功能 |
| 发布通道 | 由程序版本推导 | 正式、候选或开发 |
| 数据结构版本 | SQLite `schema_migrations` | 个人数据库迁移状态 |
| Worker 版本与协议 | Worker `RELEASE.json` | Windows 执行器及通信兼容性 |

提示词资料库 revision、WD14/CLIP 模型版本和 Windows 模型版本不绑定程序版本，分别由各自系统管理。

## 发布元数据

根目录 `RELEASE.json` 描述 Mac 程序版本、Python 版本和配套 Worker。`deploy/windows-worker/RELEASE.json`
描述 Worker 版本、发布通道和协议。自动测试要求它们与 `pyproject.toml` 保持一致。

## Windows Worker 发行包

构建命令：

```bash
uv run python scripts/build_windows_worker_release.py
```

产物位于 `dist/Soda-Prompt-Hub-Windows-Worker-<版本>.zip`。构建器使用固定白名单，只包含 Worker
程序、脚本、示例配置、说明、许可证和 `MANIFEST.sha256`；不包含真实配置、任务、模型、图片或凭据。
ZIP 内文件时间和权限使用固定值；同一提交、同一工具链重复构建应得到相同 SHA-256，便于核对发布包。

发布前必须解压并重新计算清单中每个文件的 SHA-256，还要扫描绝对个人路径、token 和真实配置。

## Mac 更新策略

正式 Release 提供完整源码 ZIP。用户从新版解压目录运行 `更新-Soda-Prompt-Hub.command`：更新器
先备份个人资料和旧程序，再准备依赖和初始化；失败时恢复旧程序。提示词来源和模型不随代码更新。

## 发布检查清单

1. 将版本从开发版切换为候选版并同步两份 `RELEASE.json`。
2. 更新 `CHANGELOG.md`，写清新增、修复、升级步骤和兼容性。
3. 运行格式、lint、类型、锁文件、全量测试和覆盖率检查。
4. 检查全部页面脚本语法与 Mac `.command` 语法。
5. 构建 Worker ZIP，解压并验证 `MANIFEST.sha256`。
6. 扫描发行文件中的凭据、真实配置、个人绝对路径和大文件。
7. 在桌面和手机视口检查首页版本、数据结构版本与设备兼容状态。
8. 人工验收安全更新的正常路径和失败回滚。
9. 候选版验收通过后，切换为不带后缀的正式版本，再重复全部检查。
10. 经维护者明确确认后才 commit、push、合并、打 tag 和创建 GitHub Release。

## Git 标签与 Release

正式版本使用 `v<版本>` 标签，例如 `v1.1.0`。标签必须指向通过检查的正式版本提交。Worker ZIP 的
SHA-256 应随 GitHub Release 说明一起公布。已发布标签不移动；后续修复使用新的修订版本。
