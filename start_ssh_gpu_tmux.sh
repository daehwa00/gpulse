#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat >&2 <<'EOF'
Usage:
  gpulse <ssh-host-or-host-list> [gpu_dashboard.py args...]

Examples:
  gpulse gpu01
  gpulse user@gpu-box --max-jobs 16
  GPU_TMUX_SESSION=labgpu gpulse "gpu-a gpu-b"

Environment:
  GPU_TMUX_SESSION      tmux session name (default: gpu-<first-host>)
  GPU_TMUX_HOSTS        host fallback when the first argument is omitted
  GPU_TMUX_SSH_OPTS     ssh options for probe/dashboard connection
  SSH_GPU_DASHBOARD     path to ssh_gpu_dashboard.sh
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

if [ "$#" -gt 0 ] && [[ "$1" != -* ]]; then
  HOSTS="$1"
  shift
else
  HOSTS="${GPU_TMUX_HOSTS:-}"
fi

if [ -z "${HOSTS// }" ]; then
  usage
  exit 2
fi

sanitize_session_name() {
  local raw="$1"
  raw="${raw%% *}"
  raw="${raw%%,*}"
  raw="${raw#*@}"
  raw="${raw%%:*}"
  raw="${raw//[^A-Za-z0-9_.-]/-}"
  raw="${raw:-gpu}"
  printf 'gpu-%s' "$raw"
}

if [ -n "${GPU_TMUX_SESSION:-}" ]; then
  SESSION_NAME="$GPU_TMUX_SESSION"
else
  SESSION_NAME="$(sanitize_session_name "$HOSTS")"
fi
SSH_KEEPALIVE_OPTS="${GPU_TMUX_SSH_OPTS:-${GPU_DASH_SSH_OPTS:--o ServerAliveInterval=10 -o ServerAliveCountMax=2 -o ConnectTimeout=10}}"
SSH_GPU_DASHBOARD="${SSH_GPU_DASHBOARD:-$SCRIPT_DIR/ssh_gpu_dashboard.sh}"
SCRIPT_VERSION="gpulse-tmux-v1"
DASHBOARD_ARGS=("$@")

quote_dashboard_args() {
  local q out=""
  if ((${#DASHBOARD_ARGS[@]})); then
    for arg in "${DASHBOARD_ARGS[@]}"; do
      printf -v q '%q' "$arg"
      out+=" $q"
    done
  fi
  printf '%s' "$out"
}

quote_shell() {
  local q
  printf -v q '%q' "$1"
  printf '%s' "$q"
}

ssh_gpu_loop_command() {
  local dashboard_cmd_q hosts_q dashboard_args_q ssh_opts_q version_q
  printf -v dashboard_cmd_q '%q' "$SSH_GPU_DASHBOARD"
  dashboard_args_q="$(quote_dashboard_args)"
  hosts_q="$(quote_shell "$HOSTS")"
  ssh_opts_q="$(quote_shell "$SSH_KEEPALIVE_OPTS")"
  version_q="$(quote_shell "$SCRIPT_VERSION")"
  cat <<EOF_LOOP
SCRIPT_VERSION=$version_q
GPU_TMUX_HOSTS=$hosts_q
GPU_TMUX_SSH_OPTS=$ssh_opts_q
read -r -a GPU_TMUX_SSH_ARGS <<< "\$GPU_TMUX_SSH_OPTS"
while true; do
  selected_host=""
  for host in \$GPU_TMUX_HOSTS; do
    ssh "\${GPU_TMUX_SSH_ARGS[@]}" -o BatchMode=yes -o ConnectionAttempts=1 "\$host" true >/dev/null 2>&1 &
    probe_pid=\$!
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      if ! kill -0 "\$probe_pid" 2>/dev/null; then
        break
      fi
      sleep 1
    done
    if kill -0 "\$probe_pid" 2>/dev/null; then
      kill "\$probe_pid" 2>/dev/null || true
      wait "\$probe_pid" 2>/dev/null || true
      echo "[\$host probe timeout]"
      continue
    fi
    wait "\$probe_pid"
    probe_rc=\$?
    if [ "\$probe_rc" -eq 0 ]; then
      selected_host="\$host"
      break
    fi
    echo "[\$host probe failed: \$probe_rc]"
  done
  if [ -z "\$selected_host" ]; then
    echo '[gpulse all routes failed] retrying in 5s...'
    sleep 5
    continue
  fi
  echo "[gpulse connecting via \$selected_host]"
  $dashboard_cmd_q "\$selected_host"$dashboard_args_q
  echo '[gpulse disconnected] reconnecting in 2s...'
  sleep 2
done
EOF_LOOP
}

ensure_gpu_pane() {
  local start_command loop_command
  if ! tmux list-panes -t "$SESSION_NAME":0 >/dev/null 2>&1; then
    return 1
  fi
  tmux select-pane -t "$SESSION_NAME":0.0 -T "$SESSION_NAME" 2>/dev/null || true
  start_command="$(tmux display-message -p -t "$SESSION_NAME":0.0 '#{pane_start_command}' 2>/dev/null || true)"
  case "$start_command" in
    *SCRIPT_VERSION=$SCRIPT_VERSION*|*SCRIPT_VERSION="$SCRIPT_VERSION"*) ;;
    *)
      loop_command="$(ssh_gpu_loop_command)" || return 1
      tmux respawn-pane -k -t "$SESSION_NAME":0.0 "$loop_command"
      ;;
  esac
}

if [ -n "${TMUX:-}" ]; then
  echo "Run this from a normal shell, not inside tmux." >&2
  exit 1
fi

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux not found. Install tmux first." >&2
  exit 127
fi

if [ ! -x "$SSH_GPU_DASHBOARD" ]; then
  echo "ssh GPU dashboard wrapper not executable: $SSH_GPU_DASHBOARD" >&2
  exit 1
fi

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  ensure_gpu_pane || true
  [ "${REMOTE_SETUP_ONLY:-}" = "1" ] && exit 0
  exec tmux attach -t "$SESSION_NAME"
fi

loop_command="$(ssh_gpu_loop_command)" || exit 1
tmux new-session -d -s "$SESSION_NAME" -n "gpu" "$loop_command"
tmux select-pane -t "$SESSION_NAME":0.0 -T "$SESSION_NAME" 2>/dev/null || true

[ "${REMOTE_SETUP_ONLY:-}" = "1" ] && exit 0
exec tmux attach -t "$SESSION_NAME"
