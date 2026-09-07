#!/bin/zsh
# Soda Prompt Hub —— 双击诊断本机环境
#
# 首次安装器会把这个脚本放到程序目录根部。
# 程序不在默认位置时，设置环境变量 PROMPT_HUB_REPO 指向程序根目录。
# 端口不是 8765 时，设置环境变量 PROMPT_HUB_PORT。

emulate -L zsh
setopt no_unset

HOST="${PROMPT_HUB_HOST:-127.0.0.1}"
PORT="${PROMPT_HUB_PORT:-8765}"
URL="http://${HOST}:${PORT}"

# Finder 启动的 Terminal 未必带上 uv 所在目录
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

# 双击运行时窗口会停在这里等一下按键；非交互执行则直接返回
pause_before_close() {
  if [[ -t 0 ]]; then
    print -r -- ""
    read -k 1 -s "?按任意键关闭这个窗口…"
  fi
}

print -r -- "── Soda Prompt Hub · 环境诊断 ─────────────────────"

# 1. 定位程序：先从脚本自身位置向上找，找不到再用 PROMPT_HUB_REPO / 默认路径
script_path="${0:A}"
repo=""
dir="${script_path:h}"
while [[ "$dir" != "/" ]]; do
  if [[ -f "$dir/pyproject.toml" && -d "$dir/src/prompt_hub" ]]; then
    repo="$dir"
    break
  fi
  dir="${dir:h}"
done
if [[ -z "$repo" ]]; then
  repo="${PROMPT_HUB_REPO:-$HOME/Applications/Soda Prompt Hub}"
fi

if [[ ! -d "$repo/src/prompt_hub" ]]; then
  print -r -- "找不到 Prompt Hub 程序：$repo"
  print -r -- "请先双击“首次安装-Soda-Prompt-Hub.command”；"
  print -r -- "如果程序装在其他位置，请设置 PROMPT_HUB_REPO 后重试。"
  pause_before_close
  exit 1
fi
cd "$repo" || exit 1
print -r -- "程序位置：$repo"

# 2. 检查 uv
if ! command -v uv > /dev/null 2>&1; then
  print -r -- "没有找到运行组件 uv。请重新运行首次安装器修复。"
  pause_before_close
  exit 1
fi

# 3. 其余检查（数据库、索引、WD14、磁盘、服务连通）交给 doctor 原样输出
print -r -- "诊断地址：$URL"
print -r -- "─────────────────────────────────────────────────"
print -r -- ""

uv run --no-sync prompt-hub doctor --url "$URL"

print -r -- ""
print -r -- "以上是 prompt-hub doctor 的原始输出，各项 ok=false 的就是需要处理的问题。"
pause_before_close
