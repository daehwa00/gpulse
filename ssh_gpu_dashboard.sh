#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  echo "Usage: gpulse-ssh <ssh-host> [gpu_dashboard.py args...]"
  echo "Example: gpulse-ssh gpu01 --bar-width 20 --max-jobs 12"
  exit 0
fi

if [ "$#" -lt 1 ]; then
  echo "Usage: gpulse-ssh <ssh-host> [gpu_dashboard.py args...]" >&2
  echo "Example: gpulse-ssh gpu01 --bar-width 20 --max-jobs 12" >&2
  exit 2
fi

HOST="$1"
shift
DASHBOARD_SCRIPT="${GPU_DASHBOARD_SCRIPT:-$SCRIPT_DIR/gpu_dashboard.py}"
SSH_KEEPALIVE_OPTS="${GPU_DASH_SSH_OPTS:--o ServerAliveInterval=10 -o ServerAliveCountMax=2 -o ConnectTimeout=10}"

if [ ! -r "$DASHBOARD_SCRIPT" ]; then
  echo "gpu dashboard script not readable: $DASHBOARD_SCRIPT" >&2
  exit 1
fi

quote_args() {
  local q out=""
  for arg in "$@"; do
    printf -v q '%q' "$arg"
    out+=" $q"
  done
  printf '%s' "$out"
}

shell_q() {
  local q
  printf -v q '%q' "$1"
  printf '%s' "$q"
}

script_body="$(cat "$DASHBOARD_SCRIPT")"
args_q="$(quote_args "$@")"
sample_q="$(shell_q "${GPU_DASH_SAMPLE_INTERVAL:-1.0}")"
frame_q="$(shell_q "${GPU_DASH_FRAME_INTERVAL:-0.125}")"
smoothing_q="$(shell_q "${GPU_DASH_SMOOTHING:-0.32}")"
bar_q="$(shell_q "${GPU_DASH_BAR_WIDTH:-24}")"
max_jobs_q="$(shell_q "${GPU_DASH_MAX_JOBS:-10}")"
job_interval_q="$(shell_q "${GPU_DASH_JOB_INTERVAL:-3.0}")"
history_q="$(shell_q "${GPU_DASH_HISTORY_LEN:-24}")"
ascii_q="$(shell_q "${GPU_DASH_ASCII:-0}")"
no_jobs_q="$(shell_q "${GPU_DASH_NO_JOBS:-0}")"
prog_q="$(shell_q "${GPU_DASH_PROG:-gpulse-ssh}")"

remote_command="$(cat <<REMOTE
export TERM=\"\${TERM:-xterm-256color}\"
export GPU_DASH_SAMPLE_INTERVAL=$sample_q
export GPU_DASH_FRAME_INTERVAL=$frame_q
export GPU_DASH_SMOOTHING=$smoothing_q
export GPU_DASH_BAR_WIDTH=$bar_q
export GPU_DASH_MAX_JOBS=$max_jobs_q
export GPU_DASH_JOB_INTERVAL=$job_interval_q
export GPU_DASH_HISTORY_LEN=$history_q
export GPU_DASH_ASCII=$ascii_q
export GPU_DASH_NO_JOBS=$no_jobs_q
export GPU_DASH_PROG=$prog_q
if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo '[gpulse] nvidia-smi not found on remote host' >&2
  exit 127
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo '[gpulse] python3 not found on remote host' >&2
  exit 127
fi
python3 -$args_q <<'PY_DASHBOARD'
$script_body
PY_DASHBOARD
REMOTE
)"

# shellcheck disable=SC2086
exec ssh $SSH_KEEPALIVE_OPTS -tt "$HOST" "$remote_command"
