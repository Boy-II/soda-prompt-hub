#!/bin/zsh
# Soda Prompt Hub —— 双击启动本机服务
#
# 首次安装器会把这个脚本放到程序目录根部。
# 程序不在默认位置时，设置环境变量 PROMPT_HUB_REPO 指向程序根目录。

emulate -L zsh
setopt no_unset

HOST="${PROMPT_HUB_HOST:-127.0.0.1}"
PORT="${PROMPT_HUB_PORT:-8765}"
URL="http://${HOST}:${PORT}/"

# Finder 启动的 Terminal 未必带上 uv 所在目录
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

# 双击运行时窗口会停在这里等一下按键；非交互执行则直接返回
pause_before_close() {
  if [[ -t 0 ]]; then
    print -r -- ""
    read -k 1 -s "?按任意键关闭这个窗口…"
  fi
}

is_prompt_hub() {
  curl -fsS --max-time 2 "${URL}api/health" 2> /dev/null \
    | grep -Eq '"service"[[:space:]]*:[[:space:]]*"soda-prompt-hub"'
}

print -r -- "── Soda Prompt Hub ──────────────────────────────"

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

# 3. 已经在跑就先确认身份，不把其他占用端口的程序当成 Prompt Hub
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN > /dev/null 2>&1; then
  if is_prompt_hub; then
    print -r -- "Prompt Hub 已经在 ${PORT} 端口运行，直接打开页面。"
    open "$URL"
    pause_before_close
    exit 0
  fi
  print -r -- "无法启动：${PORT} 端口正被其他程序占用。"
  print -r -- "Prompt Hub 不会打开这个程序，也不会自动结束它。请先关闭占用端口的程序，或换一个 PROMPT_HUB_PORT。"
  pause_before_close
  exit 1
fi

# 4. 服务起来之后再打开浏览器
{
  for _ in {1..90}; do
    if is_prompt_hub; then
      open "$URL"
      exit 0
    fi
    sleep 1
  done
} &
browser_pid=$!

trap 'kill "$browser_pid" 2> /dev/null' EXIT INT TERM

print -r -- "页面地址：$URL"
print -r -- "关闭服务：在这个窗口按 Control + C（先在页面把扫描/打标/草稿任务停下来）"
print -r -- "─────────────────────────────────────────────────"
print -r -- ""

uv run --no-sync prompt-hub serve --host "$HOST" --port "$PORT"
status=$?

print -r -- ""
if [[ $status -eq 0 || $status -eq 130 ]]; then
  print -r -- "服务已停止。"
else
  print -r -- "服务异常退出（退出码 $status）。先看上面的错误信息；也可以双击诊断脚本排查。"
fi
pause_before_close
