# Mac 使用与维护

Mac 是事实源：项目、审核、Caption、任务记录和冻结版本都以 Mac 保存的内容为准。

## 启动、停止与诊断

安装后的日常入口位于：

```text
$HOME/Applications/Soda Prompt Hub
```

- `启动-Prompt-Hub.command`：启动本地服务并打开 `127.0.0.1:8765`。
- `停止-Prompt-Hub.command`：检查后台任务后安全停止。
- `诊断-Prompt-Hub.command`：检查程序、数据库、视觉索引、WD14、磁盘和服务。
- `更新-Soda-Prompt-Hub.command`：从新下载的完整源码包执行安全更新。

有扫描、打标或草稿任务运行时，停止脚本会先提示。优先在页面取消或等待完成，再关闭服务。

## 安全更新顺序

1. 从 GitHub Release 下载并完整解压新版源码 ZIP。
2. 不要在已安装目录里覆盖文件。
3. 在新版解压目录的 `deploy/mac` 双击 `更新-Soda-Prompt-Hub.command`。
4. 更新器核对 `pyproject.toml` 与 `RELEASE.json` 的版本一致性。
5. 更新器先停止旧服务，再建立个人资料备份和旧程序快照。
6. 新版在临时目录准备依赖；初始化成功后才正式启用。
7. 新版初始化失败时，更新器会保存失败副本并恢复旧程序。

更新只替换程序。提示词 Git 来源、模型、个人数据库、图片和外部数据集不会随代码包更新。

默认个人资料备份位置：

```text
$HOME/Documents/Soda Prompt Hub/backups/prompt-hub
```

默认旧程序快照位置：

```text
$HOME/Library/Application Support/Soda Prompt Hub/program-backups
```

## 备份与恢复

开发者或维护人员可以在安装目录运行：

```bash
uv run --no-sync prompt-hub backup
uv run --no-sync prompt-hub verify-backup /绝对路径/备份目录
```

恢复必须先写入一个不存在或为空的新目录：

```bash
uv run --no-sync prompt-hub restore /绝对路径/备份目录 \
  --destination "$HOME/Documents/Soda Prompt Hub/restore-tests/prompt-hub-YYYYMMDD"
```

核对新目录正确后，再单独决定是否切换正式资料位置。程序不提供覆盖当前资料库的一键恢复。

外部导入的数据集原图仍在原文件夹，需要使用移动硬盘、NAS 或其他方式另行备份。

## 自定义位置

- `PROMPT_HUB_LIBRARY_ROOT`：个人资料目录。
- `PROMPT_HUB_MODELS_ROOT`：本地模型目录。
- `PROMPT_HUB_INSTALL_ROOT`：首次安装和更新时的程序目录。
- `PROMPT_HUB_PORT`：本地页面端口，默认 `8765`。

普通用户不需要设置这些变量。自定义后应让启动、停止、诊断和更新使用同一组设置。

## 手机访问

默认 `127.0.0.1` 仅限 Mac 本机，最安全。若主动使用局域网模式，确保 Wi-Fi 是可信网络，并在
使用后恢复本机监听。手机只是在浏览 Mac 上的页面；数据仍保存在 Mac，Windows 连接方式不变。
