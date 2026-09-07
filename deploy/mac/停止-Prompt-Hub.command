#!/bin/zsh
# Soda Prompt Hub —— 双击停止本机服务
#
# 放在仓库里的是原件；也可以把它拷贝或软链到
# $HOME/Documents/Codex/soda-person/ 下双击（见 MAC_OPERATIONS_GUIDE.md）。
# 仓库不在默认位置时，设置环境变量 PROMPT_HUB_REPO 指向仓库根目录。
# 端口不是 8765 时，设置环境变量 PROMPT_HUB_PORT。

emulate -L zsh
setopt no_unset

HOST="${PROMPT_HUB_HOST:-127.0.0.1}"
PORT="${PROMPT_HUB_PORT:-8765}"
BASE_URL="http://${HOST}:${PORT}"
TERM_WAIT_SECONDS=10

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
  curl -fsS --max-time 2 "${BASE_URL}/api/health" 2> /dev/null \
    | grep -Eq '"service"[[:space:]]*:[[:space:]]*"soda-prompt-hub"'
}

# 列出仍持有端口的进程是否全部退出
all_stopped() {
  local pid
  for pid in "${pids[@]}"; do
    if kill -0 "$pid" 2> /dev/null; then
      return 1
    fi
  done
  return 0
}

print -r -- "── Soda Prompt Hub · 停止服务 ─────────────────────"

# 1. 找出监听端口的进程
pids=(${(@f)"$(lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2> /dev/null)"})
if (( ${#pids[@]} == 0 )); then
  print -r -- "服务没有在运行（${PORT} 端口没有进程监听）。"
  pause_before_close
  exit 0
fi

if ! is_prompt_hub; then
  print -r -- "不会停止：${PORT} 端口上的程序不是可确认身份的 Prompt Hub。"
  print -r -- "请在「活动监视器」中确认程序身份；这个停止器不会结束未知进程。"
  pause_before_close
  exit 1
fi

print -r -- "已确认 Prompt Hub，端口 ${PORT} 上的服务进程："
ps -p "${(j:,:)pids}" -o pid=,command= | while IFS= read -r line; do
  print -r -- "  $line"
done

# 2. 定位仓库：先从脚本自身位置向上找，找不到再用 PROMPT_HUB_REPO / 默认路径
#    （查询后台任务状态需要借用仓库的 Python 环境解析接口返回）
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
  repo="${PROMPT_HUB_REPO:-/Volumes/Data/Hub/soda-prompt-hub}"
fi

# 3. 停止前确认没有「扫描中 / 打标中 / 生成草稿中」的后台任务
#    退出码：0 = 有进行中的任务（内容打印到标准输出）；3 = 没有；其他 = 无法确认
jobs_rc=0
active_jobs_report=""
if [[ -d "$repo/src/prompt_hub" ]] && command -v uv > /dev/null 2>&1; then
  active_jobs_report="$(cd "$repo" && uv run --no-sync python - "$BASE_URL" <<'PY'
import json
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1] + "/api/jobs?limit=100", timeout=5) as response:
        jobs = json.loads(response.read().decode("utf-8"))
except Exception:
    sys.exit(1)

active = [job for job in jobs if job.get("status") in {"queued", "running"}]
if not active:
    sys.exit(3)
for job in active:
    job_type = job.get("job_type", "?")
    status = job.get("status", "?")
    current = job.get("progress_current", 0)
    total = job.get("progress_total", 0)
    message = str(job.get("progress_message") or "").strip()
    progress = f"{current}/{total}" if total else ""
    print(f"- {job_type}（{status}）{progress} {message}".rstrip())
PY
)"
  jobs_rc=$?
else
  jobs_rc=1
fi

if (( jobs_rc == 0 )); then
  print -r -- "警告：检测到以下正在进行的后台任务："
  print -r -- "$active_jobs_report"
  print -r -- "强制退出会中断这些任务（已写入的进度会保留，但正在处理的部分要重来）。"
  print -r -- "建议：先在页面取消任务，等状态停止后再关闭服务。"
  if [[ -t 0 ]]; then
    read "answer?仍要停止服务吗？输入 yes 继续，其他输入取消："
    if [[ "$answer" != "yes" ]]; then
      print -r -- "已取消，服务保持运行。"
      pause_before_close
      exit 0
    fi
  else
    print -r -- "当前是非交互式运行，无法向你确认，为安全起见服务保持运行。"
    exit 1
  fi
elif (( jobs_rc == 3 )); then
  print -r -- "后台任务确认：没有排队或运行中的任务，可以安全停止。"
else
  print -r -- "无法确认后台任务状态（服务接口没有响应，或本机环境不完整）。"
  if [[ -t 0 ]]; then
    read "answer?仍要停止服务吗？输入 yes 继续，其他输入取消："
    if [[ "$answer" != "yes" ]]; then
      print -r -- "已取消，服务保持运行。"
      pause_before_close
      exit 0
    fi
  else
    print -r -- "当前是非交互式运行，无法向你确认，为安全起见服务保持运行。"
    exit 1
  fi
fi

# 4. 先 TERM 优雅退出，等待若干秒
print -r -- "正在停止服务（TERM）……"
kill -TERM "${pids[@]}" 2> /dev/null

for (( i = 0; i < TERM_WAIT_SECONDS; i++ )); do
  if all_stopped; then
    break
  fi
  sleep 1
done

if all_stopped; then
  print -r -- "服务已停止。"
  pause_before_close
  exit 0
fi

# 5. TERM 没有生效时，询问后才 KILL，不直接 -9
print -r -- "等了 ${TERM_WAIT_SECONDS} 秒，服务还没有退出。"
if [[ -t 0 ]]; then
  read "answer?要强制结束吗（kill -9）？输入 yes 强制结束，其他输入保留运行："
  if [[ "$answer" == "yes" ]]; then
    kill -KILL "${pids[@]}" 2> /dev/null
    sleep 1
    if all_stopped; then
      print -r -- "服务已被强制结束。"
      pause_before_close
      exit 0
    fi
    print -r -- "强制结束没有成功，请打开「活动监视器」手动结束进程。"
    pause_before_close
    exit 1
  fi
  print -r -- "已取消，服务保持运行。"
  pause_before_close
  exit 0
fi
print -r -- "当前是非交互式运行，不自动强制结束；服务仍在运行。"
exit 1
