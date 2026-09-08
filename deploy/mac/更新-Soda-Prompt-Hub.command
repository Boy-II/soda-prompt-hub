#!/bin/zsh
# Soda Prompt Hub —— macOS 安全更新

emulate -L zsh
setopt no_unset pipe_fail

INSTALL_ROOT="${PROMPT_HUB_INSTALL_ROOT:-$HOME/Applications/Soda Prompt Hub}"
PROGRAM_BACKUP_ROOT="${PROMPT_HUB_PROGRAM_BACKUP_ROOT:-$HOME/Library/Application Support/Soda Prompt Hub/program-backups}"
DATA_BACKUP_ROOT="${PROMPT_HUB_DATA_BACKUP_ROOT:-$HOME/Documents/Soda Prompt Hub/backups/prompt-hub}"
ASSUME_YES="${PROMPT_HUB_UPDATE_ASSUME_YES:-0}"
SKIP_START="${PROMPT_HUB_UPDATE_SKIP_START:-0}"
SKIP_STOP="${PROMPT_HUB_UPDATE_SKIP_STOP:-0}"
PORT="${PROMPT_HUB_PORT:-8765}"

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
UV_BIN="${PROMPT_HUB_UV_BIN:-$(command -v uv 2> /dev/null)}"

pause_before_close() {
  if [[ -t 0 ]]; then
    print -r -- ""
    read -k 1 -s "?按任意键关闭这个窗口…"
  fi
}

fail() {
  print -r -- ""
  print -r -- "更新没有完成：$1"
  print -r -- "个人资料不会因为程序目录替换而被删除。"
  pause_before_close
  exit 1
}

version_from_pyproject() {
  sed -n 's/^version = "\([^"]*\)"/\1/p' "$1" | head -1
}

version_from_release() {
  sed -n 's/.*"product_version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$1" | head -1
}

copy_program() {
  local source="$1"
  local destination="$2"
  rsync -a \
    --exclude '.git' \
    --exclude '.git/' \
    --exclude '.github/' \
    --exclude '.venv/' \
    --exclude '.coverage' \
    --exclude '.pytest_cache/' \
    --exclude '.ruff_cache/' \
    --exclude '.mypy_cache/' \
    --exclude '.planning/' \
    --exclude '.playwright-cli/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.env' \
    --exclude 'tests/' \
    --exclude 'task_plan.md' \
    --exclude 'findings.md' \
    --exclude 'progress.md' \
    --exclude 'dist/' \
    --exclude 'build/' \
    "$source/" "$destination/"
}

print -r -- "── Soda Prompt Hub · 安全更新 ─────────────────────"

script_path="${0:A}"
source_root=""
dir="${script_path:h}"
while [[ "$dir" != "/" ]]; do
  if [[ -f "$dir/pyproject.toml" && -f "$dir/RELEASE.json" && -d "$dir/src/prompt_hub" ]]; then
    source_root="$dir"
    break
  fi
  dir="${dir:h}"
done
[[ -n "$source_root" ]] || fail "请先完整解压新版源码包，再从 deploy/mac 目录运行本更新器"
[[ -d "$INSTALL_ROOT/src/prompt_hub" && -f "$INSTALL_ROOT/pyproject.toml" ]] || fail "没有找到已安装程序，请先运行首次安装器"
[[ "${source_root:A}" != "${INSTALL_ROOT:A}" ]] || fail "更新器必须从新下载的源码包运行，不能在已安装目录内更新自己"

source_version="$(version_from_pyproject "$source_root/pyproject.toml")"
release_version="$(version_from_release "$source_root/RELEASE.json")"
installed_version="$(version_from_pyproject "$INSTALL_ROOT/pyproject.toml")"
[[ -n "$source_version" && "$source_version" == "$release_version" ]] || fail "新版源码的版本声明不一致，请重新下载完整发行包"
for required in src/prompt_hub/api.py deploy/mac/启动-Prompt-Hub.command deploy/mac/诊断-Prompt-Hub.command; do
  [[ -f "$source_root/$required" ]] || fail "新版源码缺少必要文件：$required"
done

print -r -- "当前版本：${installed_version:-无法识别}"
print -r -- "准备更新：$source_version"
print -r -- "程序位置：$INSTALL_ROOT"
print -r -- "个人资料备份：$DATA_BACKUP_ROOT"

if [[ "$ASSUME_YES" != "1" && -t 0 ]]; then
  read "answer?输入 yes 开始安全更新，其他输入取消："
  if [[ "$answer" != "yes" ]]; then
    print -r -- "已取消，程序和个人资料均未改变。"
    pause_before_close
    exit 0
  fi
fi

if [[ "$SKIP_STOP" != "1" ]] && curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/api/health" 2> /dev/null | grep -q 'soda-prompt-hub'; then
  print -r -- "正在安全停止旧版服务…"
  "$INSTALL_ROOT/停止-Prompt-Hub.command" || fail "旧版服务没有安全停止"
fi

[[ -n "$UV_BIN" && -x "$UV_BIN" ]] || fail "没有找到运行组件 uv，请先重新运行首次安装器修复"
timestamp="$(date '+%Y%m%d-%H%M%S')"
data_backup="$DATA_BACKUP_ROOT/pre-update-${installed_version:-unknown}-$timestamp"
mkdir -p "$DATA_BACKUP_ROOT" || fail "无法建立个人资料备份目录"
print -r -- "正在备份个人资料…"
(cd "$INSTALL_ROOT" && "$UV_BIN" run --no-sync prompt-hub backup --destination "$data_backup") || fail "个人资料备份失败，程序尚未替换"

install_parent="${INSTALL_ROOT:h}"
mkdir -p "$install_parent" "$PROGRAM_BACKUP_ROOT" || fail "无法建立程序更新目录"
staging="$(mktemp -d "$install_parent/.soda-prompt-hub-update.XXXXXX")" || fail "无法建立临时更新目录"
copy_program "$source_root" "$staging" || { rm -rf "$staging"; fail "复制新版程序失败"; }

for launcher in "$staging"/deploy/mac/*.command; do
  chmod u+x "$launcher"
  cp -f "$launcher" "$staging/${launcher:t}"
  chmod u+x "$staging/${launcher:t}"
done

print -r -- "正在准备新版运行环境…"
(cd "$staging" && "$UV_BIN" sync --python 3.12 --no-default-groups) || { rm -rf "$staging"; fail "新版依赖准备失败，旧程序保持不变"; }

program_backup="$PROGRAM_BACKUP_ROOT/${installed_version:-unknown}-$timestamp"
mv "$INSTALL_ROOT" "$program_backup" || { rm -rf "$staging"; fail "无法建立旧程序快照"; }
if ! mv "$staging" "$INSTALL_ROOT"; then
  mv "$program_backup" "$INSTALL_ROOT" 2> /dev/null
  fail "无法启用新版程序，旧程序已恢复"
fi

print -r -- "正在检查新版数据库兼容性…"
if ! (cd "$INSTALL_ROOT" && "$UV_BIN" run --no-sync prompt-hub init); then
  failed_program="$PROGRAM_BACKUP_ROOT/failed-$source_version-$timestamp"
  mv "$INSTALL_ROOT" "$failed_program" 2> /dev/null
  mv "$program_backup" "$INSTALL_ROOT" 2> /dev/null
  fail "新版初始化失败，旧程序已恢复；个人资料备份位于 $data_backup"
fi

print -r -- ""
print -r -- "更新完成：$source_version"
print -r -- "个人资料备份：$data_backup"
print -r -- "旧程序快照：$program_backup"
print -r -- "提示词资料库和模型没有在本次更新中改变。"

if [[ "$SKIP_START" != "1" ]]; then
  open "$INSTALL_ROOT/启动-Prompt-Hub.command"
fi
pause_before_close
