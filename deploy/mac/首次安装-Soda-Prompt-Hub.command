#!/bin/zsh
# Soda Prompt Hub —— macOS 首次安装 / 修复安装

emulate -L zsh
setopt errexit no_unset pipe_fail

INSTALL_ROOT="${PROMPT_HUB_INSTALL_ROOT:-$HOME/Applications/Soda Prompt Hub}"
SKIP_START="${PROMPT_HUB_INSTALL_SKIP_START:-0}"
ASSUME_YES="${PROMPT_HUB_INSTALL_ASSUME_YES:-0}"
PUBLIC_LIBRARY_ROOT="$HOME/Documents/Soda Prompt Hub/prompt-library"
LEGACY_LIBRARY_ROOT="$HOME/Documents/Codex/soda-person/prompt-library"
if [[ -n "${PROMPT_HUB_LIBRARY_ROOT:-}" ]]; then
  DISPLAY_LIBRARY_ROOT="$PROMPT_HUB_LIBRARY_ROOT"
elif [[ -d "$PUBLIC_LIBRARY_ROOT" ]]; then
  DISPLAY_LIBRARY_ROOT="$PUBLIC_LIBRARY_ROOT"
elif [[ -d "$LEGACY_LIBRARY_ROOT" ]]; then
  DISPLAY_LIBRARY_ROOT="$LEGACY_LIBRARY_ROOT"
else
  DISPLAY_LIBRARY_ROOT="$PUBLIC_LIBRARY_ROOT"
fi

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

pause_before_close() {
  if [[ -t 0 ]]; then
    print -r -- ""
    read -k 1 -s "?按任意键关闭这个窗口…"
  fi
}

fail() {
  print -r -- ""
  print -r -- "安装没有完成：$1"
  print -r -- "已有个人资料没有被移动或删除。处理上面的提示后，可以再次运行本安装器。"
  pause_before_close
  exit 1
}

trap 'fail "第 $LINENO 行执行失败"' ZERR

if [[ "$(uname -s)" != "Darwin" ]]; then
  fail "这个安装器只适用于 macOS"
fi

script_path="${0:A}"
source_root=""
dir="${script_path:h}"
while [[ "$dir" != "/" ]]; do
  if [[ -f "$dir/pyproject.toml" && -d "$dir/src/prompt_hub" ]]; then
    source_root="$dir"
    break
  fi
  dir="${dir:h}"
done
if [[ -z "$source_root" ]]; then
  fail "请先完整解压 Soda Prompt Hub 源码包，再从 deploy/mac 目录运行本安装器"
fi

if [[ ! -f "$source_root/RELEASE.json" ]]; then
  fail "源码包缺少 RELEASE.json，请重新下载完整发行包"
fi
project_version="$(sed -n 's/^version = "\([^"]*\)"/\1/p' "$source_root/pyproject.toml" | head -1)"
release_version="$(sed -n 's/.*"product_version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$source_root/RELEASE.json" | head -1)"
if [[ -z "$project_version" || "$project_version" != "$release_version" ]]; then
  fail "源码包版本声明不一致，请重新下载完整发行包"
fi
for required in src/prompt_hub/api.py deploy/mac/启动-Prompt-Hub.command deploy/windows-worker/RELEASE.json; do
  if [[ ! -f "$source_root/$required" ]]; then
    fail "源码包缺少必要文件：$required"
  fi
done

print -r -- "── Soda Prompt Hub · 首次安装 ─────────────────────"
print -r -- "版本：$project_version"
print -r -- "程序将安装到：$INSTALL_ROOT"
print -r -- "本次使用的个人资料：$DISPLAY_LIBRARY_ROOT"
if [[ -z "${PROMPT_HUB_LIBRARY_ROOT:-}" && ! -d "$PUBLIC_LIBRARY_ROOT" && -d "$LEGACY_LIBRARY_ROOT" ]]; then
  print -r -- "检测到旧版个人资料，将继续沿用原位置，不搬移、不复制。"
fi

if [[ -d "$INSTALL_ROOT/src/prompt_hub" && "$ASSUME_YES" != "1" && -t 0 ]]; then
  read "answer?这里已经安装过。输入 yes 进行修复安装，其他输入取消："
  if [[ "$answer" != "yes" ]]; then
    print -r -- "已取消，现有程序和个人资料保持不变。"
    pause_before_close
    exit 0
  fi
fi

if [[ "${source_root:A}" != "${INSTALL_ROOT:A}" ]]; then
  print -r -- "正在复制程序文件…"
  mkdir -p "$INSTALL_ROOT"
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
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.env' \
    --exclude 'tests/' \
    --exclude 'task_plan.md' \
    --exclude 'findings.md' \
    --exclude 'progress.md' \
    --exclude 'dist/' \
    --exclude 'build/' \
    "$source_root/" "$INSTALL_ROOT/"
fi

for launcher in "$INSTALL_ROOT"/deploy/mac/*.command; do
  chmod u+x "$launcher"
  cp -f "$launcher" "$INSTALL_ROOT/${launcher:t}"
  chmod u+x "$INSTALL_ROOT/${launcher:t}"
done

if ! command -v uv > /dev/null 2>&1; then
  if ! command -v curl > /dev/null 2>&1; then
    fail "系统里没有 curl，无法下载运行组件 uv"
  fi
  print -r -- "正在安装运行组件 uv…"
  installer="$(mktemp -t soda-prompt-hub-uv.XXXXXX)"
  trap 'rm -f "$installer" 2> /dev/null' EXIT
  curl -LsSf https://astral.sh/uv/install.sh -o "$installer"
  sh "$installer"
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
command -v uv > /dev/null 2>&1 || fail "uv 安装后仍不可用，请关闭窗口并重新运行安装器"

cd "$INSTALL_ROOT"
print -r -- "正在准备 Python 3.12 和程序依赖，第一次可能需要几分钟…"
uv sync --python 3.12 --no-default-groups

print -r -- "正在建立个人资料目录和本地数据库…"
uv run --no-sync prompt-hub init

print -r -- ""
print -r -- "安装完成。以后双击程序目录中的“启动-Prompt-Hub.command”即可使用。"
print -r -- "程序位置：$INSTALL_ROOT"

trap - ZERR
if [[ "$SKIP_START" != "1" ]]; then
  open "$INSTALL_ROOT/启动-Prompt-Hub.command"
fi
pause_before_close
