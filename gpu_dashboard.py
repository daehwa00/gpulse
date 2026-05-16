#!/usr/bin/env python3
"""A dependency-free live GPU dashboard for terminals.

Works locally on any Linux host with `nvidia-smi`, or over SSH by piping this
file into `python3 -`. Designed to be readable in tmux and safe to leave running.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import shlex
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Any

CSI = "\033["
RESET = CSI + "0m"
BOLD = CSI + "1m"
DIM = CSI + "2m"
CYAN = CSI + "36m"
GREEN = CSI + "32m"
YELLOW = CSI + "33m"
RED = CSI + "31m"
MAGENTA = CSI + "35m"
BLUE = CSI + "34m"
CLEAR = CSI + "2J" + CSI + "H"
HOME = CSI + "H"
CLEAR_EOL = CSI + "K"
CLEAR_BELOW = CSI + "J"
HIDE = CSI + "?25l"
SHOW = CSI + "?25h"

GPU_FIELDS_PRIMARY = [
    "index",
    "uuid",
    "name",
    "utilization.gpu",
    "memory.used",
    "memory.total",
    "temperature.gpu",
    "power.draw",
    "power.limit",
]
GPU_FIELDS_FALLBACK = [
    "index",
    "uuid",
    "name",
    "utilization.gpu",
    "memory.used",
    "memory.total",
    "temperature.gpu",
]
JOB_FIELDS_PRIMARY = ["gpu_uuid", "pid", "process_name", "used_memory"]
JOB_FIELDS_FALLBACK = ["pid", "process_name", "used_memory"]


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def bounded_float(minimum: float, maximum: float):
    def parse(value: str) -> float:
        parsed = positive_float(value)
        if parsed < minimum or parsed > maximum:
            raise argparse.ArgumentTypeError(f"must be between {minimum:g} and {maximum:g}")
        return parsed

    return parse


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def env_positive_float(name: str, default: float) -> float:
    value = env_float(name, default)
    return value if value > 0 else default


def env_positive_int(name: str, default: int) -> int:
    value = env_int(name, default)
    return value if value > 0 else default


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=os.environ.get("GPU_DASH_PROG") or os.path.basename(sys.argv[0]),
        description="GPulse live nvidia-smi terminal dashboard",
    )
    parser.add_argument("--sample-interval", type=positive_float, default=env_positive_float("GPU_DASH_SAMPLE_INTERVAL", 1.0))
    parser.add_argument("--frame-interval", type=positive_float, default=env_positive_float("GPU_DASH_FRAME_INTERVAL", 0.125))
    parser.add_argument("--smoothing", type=bounded_float(0.01, 1.0), default=min(1.0, max(0.01, env_float("GPU_DASH_SMOOTHING", 0.32))))
    parser.add_argument("--bar-width", type=positive_int, default=env_positive_int("GPU_DASH_BAR_WIDTH", 24))
    parser.add_argument("--max-jobs", type=positive_int, default=env_positive_int("GPU_DASH_MAX_JOBS", 10))
    parser.add_argument("--history-len", type=positive_int, default=env_positive_int("GPU_DASH_HISTORY_LEN", 24))
    parser.add_argument("--job-interval", type=positive_float, default=env_positive_float("GPU_DASH_JOB_INTERVAL", 3.0))
    parser.add_argument("--no-jobs", action="store_true", default=env_bool("GPU_DASH_NO_JOBS", False))
    parser.add_argument("--ascii", action="store_true", default=env_bool("GPU_DASH_ASCII", False))
    return parser.parse_args()


ARGS = parse_args()
FILLED = "#" if ARGS.ascii else "█"
EMPTY = "." if ARGS.ascii else "░"
HLINE = "-" if ARGS.ascii else "─"
SPINNER = "-|/\\" if ARGS.ascii else "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPARK_CHARS = ".:-=+*#@" if ARGS.ascii else "▁▂▃▄▅▆▇█"
VSEP = "|" if ARGS.ascii else "│"


def color_for_pct(pct: float) -> str:
    if pct >= 90:
        return RED
    if pct >= 70:
        return YELLOW
    return GREEN


def temp_color(temp: float) -> str:
    if temp >= 80:
        return RED
    if temp >= 65:
        return YELLOW
    return GREEN


def parse_float(value: str) -> float:
    value = value.strip().replace(" MiB", "").replace(" W", "")
    if value in {"", "N/A", "[N/A]", "Not Supported"}:
        return 0.0
    try:
        return float(value)
    except ValueError:
        match = re.search(r"-?\d+(?:\.\d+)?", value)
        return float(match.group(0)) if match else 0.0


def query_csv(kind: str, fields: list[str]) -> list[list[str]]:
    flag = "--query-gpu" if kind == "gpu" else "--query-compute-apps"
    cmd = ["nvidia-smi", f"{flag}={','.join(fields)}", "--format=csv,noheader,nounits"]
    raw = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
    return [[cell.strip() for cell in row] for row in csv.reader(raw.splitlines()) if row]


def fetch_gpus() -> list[dict[str, Any]]:
    fields = GPU_FIELDS_PRIMARY
    try:
        raw_rows = query_csv("gpu", fields)
        has_power = True
    except subprocess.CalledProcessError:
        fields = GPU_FIELDS_FALLBACK
        raw_rows = query_csv("gpu", fields)
        has_power = False

    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        if len(raw) < len(fields):
            continue
        data = dict(zip(fields, raw))
        used = parse_float(data.get("memory.used", "0"))
        total = parse_float(data.get("memory.total", "0"))
        rows.append(
            {
                "idx": data.get("index", "?"),
                "uuid": data.get("uuid", ""),
                "name": data.get("name", "GPU"),
                "util": parse_float(data.get("utilization.gpu", "0")),
                "used": used,
                "total": total,
                "mem_pct": used / total * 100.0 if total else 0.0,
                "temp": parse_float(data.get("temperature.gpu", "0")),
                "pdraw": parse_float(data.get("power.draw", "0")) if has_power else 0.0,
                "plimit": parse_float(data.get("power.limit", "0")) if has_power else 0.0,
            }
        )
    return rows


def safe_ps(pid: str, field: str) -> str:
    try:
        return subprocess.check_output(["ps", "-ww", "-p", pid, "-o", f"{field}="], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def proc_cwd(pid: str) -> str:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except Exception:
        return ""


def compact_command(command: str) -> str:
    """Keep the runnable/script/config visible without flooding the table."""
    if not command:
        return ""
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    if not parts:
        return command

    compacted: list[str] = []
    for i, part in enumerate(parts[:12]):
        if i == 0 and "/" in part:
            compacted.append(os.path.basename(part) or part)
            continue
        if "/" in part and len(part) > 34:
            bits = [b for b in part.split("/") if b]
            if len(bits) >= 2:
                compacted.append("…/" + "/".join(bits[-2:]))
            else:
                compacted.append("…/" + bits[-1])
        else:
            compacted.append(part)
    if len(parts) > 12:
        compacted.append("…")
    return " ".join(compacted)


def fetch_jobs(gpus: list[dict[str, Any]], max_jobs: int) -> tuple[list[dict[str, Any]], str | None]:
    uuid_to_idx = {row.get("uuid", ""): str(row.get("idx", "?")) for row in gpus if row.get("uuid")}
    fields = JOB_FIELDS_PRIMARY
    try:
        raw_rows = query_csv("jobs", fields)
        has_gpu_uuid = True
    except subprocess.CalledProcessError as exc:
        try:
            fields = JOB_FIELDS_FALLBACK
            raw_rows = query_csv("jobs", fields)
            has_gpu_uuid = False
        except subprocess.CalledProcessError as fallback_exc:
            return [], fallback_exc.output.strip() or str(exc)

    grouped: dict[str, dict[str, Any]] = {}
    for raw in raw_rows:
        if len(raw) < len(fields):
            continue
        data = dict(zip(fields, raw))
        pid = data.get("pid", "").strip()
        if not pid or pid in {"N/A", "[N/A]"}:
            continue
        job = grouped.setdefault(
            pid,
            {
                "pid": pid,
                "gpus": set(),
                "mem": 0.0,
                "process_name": data.get("process_name", ""),
            },
        )
        if has_gpu_uuid:
            job["gpus"].add(uuid_to_idx.get(data.get("gpu_uuid", ""), "?"))
        else:
            job["gpus"].add("?")
        job["mem"] += parse_float(data.get("used_memory", "0"))
        if data.get("process_name"):
            job["process_name"] = data["process_name"]

    jobs = list(grouped.values())
    for job in jobs:
        pid = job["pid"]
        job["user"] = safe_ps(pid, "user") or "?"
        job["etime"] = safe_ps(pid, "etime") or "?"
        args = safe_ps(pid, "args")
        comm = safe_ps(pid, "comm")
        job["cmd"] = compact_command(args or comm or job.get("process_name", "") or "?")
        job["cwd"] = proc_cwd(pid)
        job["gpus"] = ",".join(sorted(job["gpus"], key=lambda x: int(x) if x.isdigit() else 999))
    jobs.sort(key=lambda row: (-float(row.get("mem", 0.0)), row.get("pid", "")))
    return jobs[:max_jobs], None


def clone_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def blend_rows(current: list[dict[str, Any]], target: list[dict[str, Any]], alpha: float) -> list[dict[str, Any]]:
    if not current or len(current) != len(target):
        return clone_rows(target)
    by_idx = {row["idx"]: row for row in current}
    blended = []
    for target_row in target:
        cur = by_idx.get(target_row["idx"])
        if not cur:
            blended.append(dict(target_row))
            continue
        row = dict(target_row)
        for key in ("util", "used", "mem_pct", "temp", "pdraw", "plimit"):
            row[key] = cur.get(key, target_row[key]) + (target_row[key] - cur.get(key, target_row[key])) * alpha
        blended.append(row)
    return blended


def table_layout(cols: int) -> dict[str, int]:
    status_w = 6
    util_w = max(8, ARGS.bar_width)
    mem_w = max(8, ARGS.bar_width)
    trend_w = 10 if cols >= 120 else 8

    def total_width(u: int, m: int, trend: int) -> int:
        widths = [3, status_w, u + 7, m + 20, 6, 19]
        if trend:
            widths.append(trend)
        return 1 + sum(widths) + 2 * (len(widths) - 1)

    limit = max(78, cols - 1)

    # First, shrink gracefully for narrow panes.
    while total_width(util_w, mem_w, trend_w) > limit and (util_w > 10 or mem_w > 10):
        if mem_w >= util_w and mem_w > 10:
            mem_w -= 1
        elif util_w > 10:
            util_w -= 1
        else:
            break
    while total_width(util_w, mem_w, trend_w) > limit and trend_w > 0:
        trend_w -= 1
    while total_width(util_w, mem_w, trend_w) > limit and (util_w > 8 or mem_w > 8):
        if mem_w >= util_w and mem_w > 8:
            mem_w -= 1
        elif util_w > 8:
            util_w -= 1
        else:
            break

    # Then, expand to consume available horizontal space. Keep trend compact;
    # use most extra space for the util/vram bars where it improves readability.
    extra = max(0, limit - total_width(util_w, mem_w, trend_w))
    if trend_w and extra:
        add = min(extra, max(0, 14 - trend_w))
        trend_w += add
        extra -= add
    toggle = 0
    while extra > 0:
        if toggle % 3 == 0:
            util_w += 1
        else:
            mem_w += 1
        extra -= 1
        toggle += 1

    return {"status": status_w, "util": util_w, "mem": mem_w, "trend": trend_w}


def bar(pct: float, width: int) -> str:
    pct = max(0.0, min(100.0, pct))
    filled = round(width * pct / 100.0)
    return color_for_pct(pct) + FILLED * filled + DIM + EMPTY * (width - filled) + RESET


def fmt_power(draw: float, limit: float) -> str:
    if limit <= 0:
        return f"{draw:.0f}W".ljust(19)
    pct = draw / limit * 100.0
    return f"{draw:.0f}/{limit:.0f}W {pct:.1f}%".ljust(19)


def fmt_mem(mib: float) -> str:
    return f"{mib / 1024:.1f}G" if mib >= 1024 else f"{mib:.0f}M"


def elide_middle(text: str, width: int) -> str:
    text = str(text or "")
    if len(text) <= width:
        return text.ljust(width)
    if width <= 1:
        return text[:width]
    left = max(1, (width - 1) // 2)
    right = max(1, width - 1 - left)
    return (text[:left] + "…" + text[-right:])[:width]



def model_summary(rows: list[dict[str, Any]]) -> str:
    names = [str(row.get("name", "GPU")) for row in rows]
    unique = []
    for name in names:
        if name not in unique:
            unique.append(name)
    if not unique:
        return "0 GPUs"
    if len(unique) == 1:
        return f"{len(rows)}x {unique[0]}"
    joined = ", ".join(unique[:3]) + ("..." if len(unique) > 3 else "")
    return f"{len(rows)} GPUs: {joined}"


def attach_histories(rows: list[dict[str, Any]], histories: dict[str, list[float]], max_len: int) -> list[dict[str, Any]]:
    live = set()
    max_len = max(2, max_len)
    for row in rows:
        idx = str(row.get("idx", "?"))
        live.add(idx)
        hist = histories.setdefault(idx, [])
        hist.append(float(row.get("util", 0.0)))
        del hist[:-max_len]
        row["history"] = list(hist)
    for idx in list(histories):
        if idx not in live:
            histories.pop(idx, None)
    return rows


def status_badge(row: dict[str, Any]) -> str:
    util = float(row.get("util", 0.0))
    mem = float(row.get("mem_pct", 0.0))
    temp = float(row.get("temp", 0.0))
    if temp >= 80:
        label, color = " HOT ", RED
    elif mem >= 92:
        label, color = "FULL", RED
    elif util >= 92:
        label, color = " MAX ", RED
    elif util >= 70:
        label, color = "BUSY", YELLOW
    elif mem >= 60:
        label, color = "VRAM", YELLOW
    elif util < 5 and mem < 5:
        label, color = "IDLE", DIM
    elif util < 10:
        label, color = "LOW ", DIM
    else:
        label, color = " RUN ", GREEN
    return color + label.center(6) + RESET


def sparkline(values: list[float], width: int) -> str:
    if width <= 0:
        return ""
    if not values:
        return " " * width
    vals = [max(0.0, min(100.0, float(v))) for v in values[-width:]]
    if len(vals) < width:
        vals = [vals[0]] * (width - len(vals)) + vals
    max_idx = len(SPARK_CHARS) - 1
    text = "".join(SPARK_CHARS[round(v / 100.0 * max_idx)] for v in vals)
    return color_for_pct(vals[-1]) + text + RESET



def render_jobs(lines: list[str], jobs: list[dict[str, Any]], job_error: str | None, cols: int) -> None:
    lines.append("")
    title = "Active GPU jobs"
    if job_error:
        lines.append(BOLD + title + RESET + DIM + f"  nvidia-smi process query unavailable: {job_error[:90]}" + RESET)
        return
    lines.append(BOLD + title + RESET + DIM + "  pid / gpu / vram / user / cwd / command" + RESET)
    if not jobs:
        lines.append(DIM + " none detected by nvidia-smi compute-apps" + RESET)
        return

    pid_w, gpu_w, vram_w, user_w, time_w = 7, 7, 7, 10, 10
    fixed = 1 + pid_w + 2 + gpu_w + 2 + vram_w + 2 + user_w + 2 + time_w + 2
    cwd_w = max(18, min(42, cols - fixed - 28))
    cmd_w = max(20, cols - fixed - cwd_w - 2)
    header = (
        f" {'pid':^{pid_w}}  {'gpu':^{gpu_w}}  {'vram':^{vram_w}}  "
        f"{'user':^{user_w}}  {'time':^{time_w}}  {'cwd':^{cwd_w}}  {'command':^{cmd_w}}"
    )
    sep = (
        f" {'-' * pid_w}  {'-' * gpu_w}  {'-' * vram_w}  "
        f"{'-' * user_w}  {'-' * time_w}  {'-' * cwd_w}  {'-' * cmd_w}"
    )
    lines.append(DIM + header + RESET)
    lines.append(DIM + sep + RESET)
    for job in jobs:
        pid = str(job.get("pid", "?"))[-pid_w:].ljust(pid_w)
        gpus = elide_middle(str(job.get("gpus", "?")), gpu_w).strip().ljust(gpu_w)
        vram = fmt_mem(float(job.get("mem", 0.0))).ljust(vram_w)
        user = elide_middle(str(job.get("user", "?")), user_w).strip().ljust(user_w)
        etime = elide_middle(str(job.get("etime", "?")), time_w).strip().ljust(time_w)
        cwd = elide_middle(str(job.get("cwd", "")) or "?", cwd_w).strip().ljust(cwd_w)
        cmd = elide_middle(str(job.get("cmd", "?")), cmd_w).strip().ljust(cmd_w)
        lines.append(f" {pid}  {gpus}  {vram}  {user}  {etime}  {cwd}  {cmd}")


def write_frame(lines: list[str]) -> None:
    # Avoid full-screen clear on every frame; redraw in-place line by line and
    # clear stale tail only after the new frame has been written. This greatly
    # reduces visible flicker over SSH/tmux.
    physical_lines: list[str] = []
    for line in lines:
        split = str(line).splitlines()
        physical_lines.extend(split if split else [""])
    body = "\n".join(line + CLEAR_EOL for line in physical_lines)
    sys.stdout.write(HOME + body + CLEAR_BELOW)
    sys.stdout.flush()

def render(rows: list[dict[str, Any]], jobs: list[dict[str, Any]], job_error: str | None, *, spinner: str, last_sample_at: float, error: str | None = None) -> None:
    cols = shutil.get_terminal_size((120, 30)).columns
    host = socket.gethostname()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sample_age = max(0.0, time.monotonic() - last_sample_at) if last_sample_at else 0.0
    lines: list[str] = []
    title = f" {spinner} GPU LIVE  {host}  {model_summary(rows)}  {now} "
    lines.append(BOLD + CYAN + title.center(min(cols, 160), HLINE) + RESET)
    if error:
        lines.append(RED + f"\n  nvidia-smi error: {error}" + RESET)
        lines.append(DIM + "  retrying..." + RESET)
        write_frame(lines)
        return
    if not rows:
        lines.append(YELLOW + "\n  Waiting for GPU rows from nvidia-smi..." + RESET)
        write_frame(lines)
        return

    total_used = sum(float(r["used"]) for r in rows)
    total_mem = sum(float(r["total"]) for r in rows)
    avg_util = sum(float(r["util"]) for r in rows) / len(rows)
    avg_temp = sum(float(r["temp"]) for r in rows) / len(rows)
    mem_pct = total_used / total_mem * 100.0 if total_mem else 0.0
    lines.append(
        f"{BOLD}UTIL{RESET} {color_for_pct(avg_util)}{avg_util:.1f}%{RESET}  {DIM}{VSEP}{RESET}  "
        f"{BOLD}VRAM{RESET} {color_for_pct(mem_pct)}{total_used/1024:.1f}/{total_mem/1024:.1f} GiB {mem_pct:.1f}%{RESET}  {DIM}{VSEP}{RESET}  "
        f"{BOLD}TEMP{RESET} {temp_color(avg_temp)}{avg_temp:.0f}°C{RESET}  {DIM}{VSEP}{RESET}  "
        f"{BOLD}JOBS{RESET} {len(jobs)}  {DIM}{VSEP}{RESET}  "
        f"{DIM}sample {sample_age:.1f}s · render {1 / max(0.03, ARGS.frame_interval):.0f}fps · jobs {ARGS.job_interval:g}s{RESET}\n"
    )

    layout = table_layout(cols)
    util_w = layout["util"]
    mem_w = layout["mem"]
    trend_w = layout["trend"]
    util_col_w = util_w + 7
    mem_col_w = mem_w + 20
    headers: list[tuple[str, int]] = [
        ("gpu", 3),
        ("state", layout["status"]),
        ("util %", util_col_w),
        ("vram used/total %", mem_col_w),
        ("temp", 6),
        ("power draw/limit", 19),
    ]
    if trend_w:
        headers.append(("trend", trend_w))
    lines.append(DIM + " " + "  ".join(label.center(width) for label, width in headers) + RESET)
    lines.append(DIM + " " + "  ".join(HLINE * width for _, width in headers) + RESET)

    for row in rows:
        util_text = f"{float(row['util']):.1f}%".ljust(6)
        util_s = f"{bar(float(row['util']), util_w)} {util_text}"
        mem_text = f"{float(row['used'])/1024:.1f}/{float(row['total'])/1024:.1f}G {float(row['mem_pct']):.1f}%".ljust(20)
        mem_s = f"{bar(float(row['mem_pct']), mem_w)} {mem_text}"
        temp_text = f"{float(row['temp']):.0f}°C".ljust(6)
        temp_s = temp_color(float(row["temp"])) + temp_text + RESET
        power_s = MAGENTA + fmt_power(float(row.get("pdraw", 0.0)), float(row.get("plimit", 0.0))) + RESET
        parts = [
            str(row["idx"]).ljust(3),
            status_badge(row),
            util_s,
            mem_s,
            temp_s,
            power_s,
        ]
        if trend_w:
            parts.append(sparkline(row.get("history", []), trend_w))
        lines.append(" " + "  ".join(parts))

    if not ARGS.no_jobs:
        render_jobs(lines, jobs, job_error, cols)
    lines.append(DIM + "\n Ctrl-C: stop dashboard   |   tmux detach: Ctrl-b d   |   commands: gpulse-local / gpulse-ssh <host>" + RESET)
    write_frame(lines)


def sampler_loop(state: dict[str, Any], lock: threading.Lock, stop_event: threading.Event) -> None:
    """Poll nvidia-smi away from the render loop so frames stay even."""
    next_sample_at = 0.0
    next_job_at = 0.0
    latest_rows: list[dict[str, Any]] = []
    histories: dict[str, list[float]] = {}
    sample_interval = max(0.2, ARGS.sample_interval)
    job_interval = max(1.0, ARGS.job_interval)

    while not stop_event.is_set():
        now = time.monotonic()
        did_work = False

        if now >= next_sample_at:
            did_work = True
            try:
                rows = attach_histories(fetch_gpus(), histories, ARGS.history_len)
                latest_rows = rows
                with lock:
                    state["target_rows"] = rows
                    state["last_sample_at"] = time.monotonic()
                    state["last_error"] = None
            except Exception as exc:
                with lock:
                    state["last_error"] = str(exc).strip()
            next_sample_at = time.monotonic() + sample_interval

        if latest_rows and not ARGS.no_jobs and now >= next_job_at:
            did_work = True
            jobs, job_error = fetch_jobs(latest_rows, ARGS.max_jobs)
            with lock:
                state["jobs"] = jobs
                state["job_error"] = job_error
            next_job_at = time.monotonic() + job_interval

        if not did_work:
            next_due = next_sample_at
            if latest_rows and not ARGS.no_jobs:
                next_due = min(next_due, next_job_at)
            stop_event.wait(max(0.03, min(0.2, next_due - time.monotonic())))


def main() -> int:
    current_rows: list[dict[str, Any]] = []
    frame = 0
    frame_interval = max(0.03, ARGS.frame_interval)
    smoothing = max(0.01, min(1.0, ARGS.smoothing))
    lock = threading.Lock()
    stop_event = threading.Event()
    state: dict[str, Any] = {
        "target_rows": [],
        "jobs": [],
        "job_error": None,
        "last_sample_at": 0.0,
        "last_error": None,
    }
    sampler = threading.Thread(target=sampler_loop, args=(state, lock, stop_event), daemon=True)

    sys.stdout.write(CLEAR + HIDE)
    sys.stdout.flush()
    sampler.start()
    next_frame_at = time.monotonic()
    try:
        while True:
            with lock:
                target_rows = clone_rows(state["target_rows"])
                jobs = clone_rows(state["jobs"])
                job_error = state["job_error"]
                last_sample_at = state["last_sample_at"]
                last_error = state["last_error"]

            if target_rows:
                current_rows = blend_rows(current_rows, target_rows, smoothing)
            render(current_rows, jobs, job_error, spinner=SPINNER[frame % len(SPINNER)], last_sample_at=last_sample_at, error=last_error)
            frame += 1

            # Keep cadence stable. If output/terminal I/O takes too long, drop the
            # missed frame instead of bursting several fast frames afterward.
            next_frame_at += frame_interval
            sleep_for = next_frame_at - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                next_frame_at = time.monotonic()
    except KeyboardInterrupt:
        return 0
    finally:
        stop_event.set()
        sys.stdout.write(SHOW + RESET + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
