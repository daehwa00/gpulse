#!/usr/bin/env python3
"""Generate a sanitized terminal demo GIF for the GPU dashboard README.

The frames intentionally use synthetic numbers/commands so the public README does
not leak real users, PIDs, paths, or experiments while preserving the actual UI
shape and workflow.
"""
from __future__ import annotations

import html
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "gpulse-demo.gif"
WIDTH = 1120
HEIGHT = 650
FONT_SIZE = 15
LINE_H = 23
CHAR_W = 8.7
LEFT = 28
TOP = 34
FPS_DELAY = 10  # ImageMagick delay unit = 1/100 sec. 10 => 10fps.
FRAMES = 67
COMMAND = "gpulse gpu01"
ENTER_FRAME = len(COMMAND) + 6
OUTPUT_FRAME = ENTER_FRAME + 7
DASHBOARD_FRAME = OUTPUT_FRAME + 5

BG = "#090d18"
PANEL = "#0f172a"
PANEL_2 = "#111c31"
TEXT = "#e5edf7"
DIM = "#8793a8"
CYAN = "#5eead4"
BLUE = "#60a5fa"
GREEN = "#4ade80"
YELLOW = "#facc15"
RED = "#fb7185"
MAGENTA = "#c084fc"
BORDER = "#26344f"

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPARK = "▁▂▃▄▅▆▇█"


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def text(x: float, y: float, value: str, fill: str = TEXT, weight: int = 400, size: int = FONT_SIZE, opacity: float = 1.0) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" fill="{fill}" font-size="{size}" '
        f'font-family="Menlo, Consolas, DejaVu Sans Mono, monospace" '
        f'font-weight="{weight}" opacity="{opacity}">{esc(value)}</text>'
    )


def mono_segments(x: float, y: float, segments: list[tuple[str, str, int | None]]) -> str:
    parts: list[str] = []
    cursor = x
    for value, fill, weight in segments:
        parts.append(text(cursor, y, value, fill, 400 if weight is None else weight))
        cursor += len(value) * CHAR_W
    return "\n".join(parts)


def col_x(col: int) -> float:
    return LEFT + col * CHAR_W


def line_y(row: int) -> float:
    return TOP + 52 + row * LINE_H


def col_segments(row: int, segments: list[tuple[int, str, str, int | None]]) -> str:
    y = line_y(row)
    return "\n".join(text(col_x(col), y, value, fill, 400 if weight is None else weight) for col, value, fill, weight in segments)


def pct_bar(pct: float, width: int = 18) -> str:
    filled = max(0, min(width, round(width * pct / 100)))
    return "█" * filled + "░" * (width - filled)


def sparkline(seed: int, frame: int, width: int = 18) -> str:
    chars = []
    for i in range(width):
        v = (math.sin((frame * 0.42) + i * 0.75 + seed) + 1) / 2
        idx = max(0, min(len(SPARK) - 1, int(v * (len(SPARK) - 1))))
        chars.append(SPARK[idx])
    return "".join(chars)


def row_rect(row: int, fill: str, x_col: int = 0, width_cols: int = 122) -> str:
    return (
        f'<rect x="{col_x(x_col) - 4:.1f}" y="{line_y(row) - 16:.1f}" '
        f'width="{width_cols * CHAR_W:.1f}" height="21" rx="3" fill="{fill}"/>'
    )


def frame_svg(frame: int) -> str:
    spin = SPINNER[frame % len(SPINNER)]
    util_base = [71, 92, 48, 12, 86, 63, 5, 77]
    mem_base = [24, 82, 35, 8, 71, 44, 5, 66]
    temp_base = [46, 73, 51, 38, 68, 54, 34, 62]
    power_base = [316, 742, 228, 84, 618, 370, 55, 501]

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1120" height="650" viewBox="0 0 1120 650">',
        f'<rect width="100%" height="100%" fill="{BG}"/>',
        f'<rect x="18" y="18" width="1084" height="614" rx="20" fill="{PANEL}" stroke="{BORDER}"/>',
        f'<circle cx="44" cy="43" r="6" fill="{RED}"/><circle cx="66" cy="43" r="6" fill="{YELLOW}"/><circle cx="88" cy="43" r="6" fill="{GREEN}"/>',
        text(104, 49, "gpulse demo", DIM, 700, 13),
    ]

    typed = COMMAND[: max(0, min(len(COMMAND), frame))]
    cursor = "▌" if frame < OUTPUT_FRAME and frame % 4 != 3 else ""
    parts.append(col_segments(0, [(0, "$", GREEN, 700), (2, typed, TEXT, 700), (2 + len(typed), cursor, CYAN, None)]))

    if frame < OUTPUT_FRAME:
        parts.append("</svg>")
        return "\n".join(parts)

    parts.append(col_segments(1, [(0, "[gpulse connecting via gpu01]", DIM, None)]))
    if frame < DASHBOARD_FRAME:
        parts.append(text(col_x(0), line_y(2) + 14, "creating tmux session, opening SSH, checking GPUs...", DIM, size=13))
        parts.append("</svg>")
        return "\n".join(parts)

    avg_util = sum(util_base) / len(util_base) + math.sin(frame / 3) * 3
    avg_mem = sum(mem_base) / len(mem_base) + math.cos(frame / 4) * 2
    total_used = avg_mem / 100 * 8 * 180
    jobs = 5 + (frame // 12) % 3
    row = 3
    parts.append(col_segments(row, [
        (0, spin, CYAN, 700),
        (2, "GPU LIVE", TEXT, 700),
        (12, "gpu01", DIM, None),
        (19, "8x NVIDIA GPU", BLUE, 700),
        (34, "demo data", DIM, None),
        (48, "2026-05-16 21:20:00", DIM, None),
    ]))
    row += 1
    parts.append(col_segments(row, [
        (0, "UTIL", TEXT, 700),
        (5, f"{avg_util:4.1f}%", GREEN if avg_util < 70 else YELLOW, 700),
        (12, "│", BORDER, None),
        (15, "VRAM", TEXT, 700),
        (20, f"{total_used:5.1f}/1440.0 GiB", YELLOW if avg_mem >= 70 else GREEN, 700),
        (39, f"{avg_mem:4.1f}%", YELLOW if avg_mem >= 70 else GREEN, 700),
        (46, "│", BORDER, None),
        (49, "TEMP", TEXT, 700),
        (54, "54°C", GREEN, 700),
        (60, "│", BORDER, None),
        (63, "JOBS", TEXT, 700),
        (68, str(jobs), TEXT, 700),
        (72, "│", BORDER, None),
        (75, "sample 1.0s · render 8fps · jobs 3s", DIM, None),
    ]))
    row += 1
    parts.append(col_segments(row, [(0, "─" * 122, BORDER, None)]))
    row += 1
    parts.append(col_segments(row, [
        (1, "gpu", CYAN, 700),
        (6, "state", CYAN, 700),
        (15, "util %", CYAN, 700),
        (42, "vram used/total %", CYAN, 700),
        (74, "temp", CYAN, 700),
        (82, "power draw/limit", CYAN, 700),
        (98, "trend", CYAN, 700),
    ]))
    row += 1
    parts.append(col_segments(row, [
        (1, "───", BORDER, None),
        (6, "──────", BORDER, None),
        (15, "────────────────────────", BORDER, None),
        (42, "──────────────────────", BORDER, None),
        (74, "────", BORDER, None),
        (82, "────────────────", BORDER, None),
        (98, "──────────────────", BORDER, None),
    ]))
    row += 1

    for idx in range(8):
        util = max(0, min(100, util_base[idx] + math.sin(frame * 0.45 + idx) * 8))
        mem = max(0, min(100, mem_base[idx] + math.cos(frame * 0.33 + idx) * 4))
        temp = int(temp_base[idx] + math.sin(frame * 0.25 + idx) * 3)
        power = int(power_base[idx] + math.sin(frame * 0.38 + idx) * 32)
        state = "IDLE" if util < 15 else ("MAX " if util > 90 else "RUN ")
        state_color = DIM if state.strip() == "IDLE" else (RED if state.strip() == "MAX" else GREEN)
        util_color = RED if util > 90 else (YELLOW if util > 70 else GREEN)
        mem_color = RED if mem > 90 else (YELLOW if mem > 70 else GREEN)
        temp_color = RED if temp > 78 else (YELLOW if temp > 65 else GREEN)
        row_bg = "#0b1324" if idx % 2 == 0 else "#101a2e"
        parts.append(row_rect(row, row_bg, 0, 122))
        parts.append(col_segments(row, [
            (1, f"{idx:<3}", BLUE, 700),
            (6, f"{state.strip():<6}", state_color, 700),
            (15, pct_bar(util, 16), util_color, None),
            (32, f"{util:5.1f}%", util_color, 700),
            (42, pct_bar(mem, 10), mem_color, None),
            (53, f"{mem * 1.8:5.1f}/180.0G", TEXT, None),
            (66, f"{mem:5.1f}%", mem_color, 700),
            (74, f"{temp:3d}°C", temp_color, 700),
            (82, f"{power:4d}/1000 W", TEXT, None),
            (98, sparkline(idx, frame, 18), MAGENTA if util > 60 else CYAN, None),
        ]))
        row += 1

    row += 1
    parts.append(col_segments(row, [(0, "Active GPU jobs", CYAN, 700), (18, "pid / gpu / vram / user / cwd / command", DIM, None)]))
    row += 1
    parts.append(col_segments(row, [
        (1, "pid", CYAN, 700),
        (10, "gpu", CYAN, 700),
        (18, "vram", CYAN, 700),
        (30, "user", CYAN, 700),
        (42, "time", CYAN, 700),
        (54, "cwd", CYAN, 700),
        (76, "command", CYAN, 700),
    ]))
    row += 1
    parts.append(col_segments(row, [
        (1, "───────", BORDER, None),
        (10, "───────", BORDER, None),
        (18, "───────", BORDER, None),
        (30, "──────────", BORDER, None),
        (42, "──────────", BORDER, None),
        (54, "──────────────────", BORDER, None),
        (76, "────────────────────────────────────────", BORDER, None),
    ]))
    row += 1
    job_rows = [
        ("42420", "0", "31.4 GiB", "user", "02:13", "~/SRA", "omx --madmax"),
        ("43118", "1", "96.8 GiB", "user", "00:48", "~/project", "python train.py --config gpu.yaml"),
        ("43991", "4", "82.1 GiB", "team", "05:02", "~/exp", "torchrun --nproc_per_node=8"),
    ]
    visible = 2 + (frame // 16) % 2
    for job in job_rows[:visible]:
        parts.append(col_segments(row, [
            (1, f"{job[0]:<7}", TEXT, None),
            (10, f"{job[1]:<7}", TEXT, None),
            (18, f"{job[2]:<7}", TEXT, None),
            (30, f"{job[3]:<10}", TEXT, None),
            (42, f"{job[4]:<10}", TEXT, None),
            (54, f"{job[5]:<18}", TEXT, None),
            (76, job[6], TEXT, None),
        ]))
        row += 1

    parts.append(text(col_x(0), 606, "Ctrl-C: stop dashboard   |   tmux detach: Ctrl-b d   |   run gpulse gpu01 again to reattach", DIM, size=13))
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> int:
    if not shutil.which("magick"):
        raise SystemExit("ImageMagick 'magick' command is required to build the GIF")

    with tempfile.TemporaryDirectory(prefix="gpulse-gif-") as tmp:
        tmpdir = Path(tmp)
        pngs = []
        for i in range(FRAMES):
            svg = tmpdir / f"frame-{i:03d}.svg"
            png = tmpdir / f"frame-{i:03d}.png"
            svg.write_text(frame_svg(i), encoding="utf-8")
            subprocess.run(["magick", str(svg), str(png)], check=True)
            pngs.append(str(png))
        subprocess.run(
            ["magick", "-delay", str(FPS_DELAY), "-loop", "0", *pngs, "-layers", "Optimize", str(OUT)],
            check=True,
        )
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
