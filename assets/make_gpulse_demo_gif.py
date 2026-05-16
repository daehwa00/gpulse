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
OUTPUT_FRAME = ENTER_FRAME + 5
DASHBOARD_FRAME = OUTPUT_FRAME + 7

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

    y = TOP + 52
    typed = COMMAND[: max(0, min(len(COMMAND), frame))]
    cursor = "▌" if frame < ENTER_FRAME and frame % 4 != 3 else ""
    # Terminal Enter normally leaves no glyph behind. The demo shows a brief
    # explicit marker so the animation does not look like GPulse auto-started
    # before the command was submitted.
    enter_marker = "  ⏎ Enter" if ENTER_FRAME <= frame < OUTPUT_FRAME else ""
    parts.append(mono_segments(LEFT, y, [("$ ", GREEN, 700), (typed, TEXT, 700), (cursor, CYAN, None), (enter_marker, CYAN, 700)]))
    y += LINE_H

    if frame < OUTPUT_FRAME:
        parts.append("</svg>")
        return "\n".join(parts)

    if frame >= OUTPUT_FRAME:
        parts.append(text(LEFT, y, "[gpulse connecting via gpu01]", DIM))
        y += LINE_H
    if frame < DASHBOARD_FRAME:
        parts.append(text(LEFT, y + 14, "creating tmux session, opening SSH, checking GPUs...", DIM, size=13))
        parts.append("</svg>")
        return "\n".join(parts)

    # Dashboard header panel.
    dash_y = y + 8
    parts.append(f'<rect x="28" y="{dash_y - 22}" width="1064" height="504" rx="14" fill="{PANEL_2}" stroke="{BORDER}"/>')
    parts.append(mono_segments(LEFT + 16, dash_y, [
        (spin + " ", CYAN, 700),
        ("GPU LIVE", TEXT, 700),
        ("  gpu01", DIM, None),
        ("  8x NVIDIA GPU", BLUE, 700),
        ("  safe demo", DIM, None),
    ]))
    y = dash_y + LINE_H

    avg_util = sum(util_base) / len(util_base) + math.sin(frame / 3) * 3
    avg_mem = sum(mem_base) / len(mem_base) + math.cos(frame / 4) * 2
    jobs = 5 + (frame // 12) % 3
    summary = f"UTIL {avg_util:04.1f}% │ VRAM {avg_mem:04.1f}% │ TEMP 54°C │ JOBS {jobs} │ sample 1.0s/render 8fps"
    parts.append(text(LEFT + 16, y, summary, DIM))
    y += LINE_H
    parts.append(text(LEFT + 16, y, "─" * 120, BORDER))
    y += LINE_H

    header = "gpu  state  util %                  vram used/total      %     temp   power draw/limit    trend"
    parts.append(text(LEFT + 16, y, header, CYAN, 700))
    y += LINE_H

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
        parts.append(f'<rect x="40" y="{y - 16}" width="1038" height="21" rx="5" fill="{row_bg}"/>')
        parts.append(mono_segments(LEFT + 18, y, [
            (f"{idx:<4}", BLUE, 700),
            (f"{state:<7}", state_color, 700),
            (f"{util:5.1f} ", util_color, 700),
            (pct_bar(util, 16) + "  ", util_color, None),
            (f"{mem * 1.8:5.1f}/180.0 GiB ", TEXT, None),
            (f"{mem:5.1f} ", mem_color, 700),
            (f"{temp:4d}°C ", temp_color, 700),
            (f"{power:4d}/1000 W      ", TEXT, None),
            (sparkline(idx, frame, 18), MAGENTA if util > 60 else CYAN, None),
        ]))
        y += LINE_H

    y += 10
    parts.append(text(LEFT + 16, y, "Active GPU jobs", CYAN, 700))
    y += LINE_H
    parts.append(text(LEFT + 16, y, "pid     gpu   vram      user     time      cwd          command", CYAN, 700))
    y += LINE_H
    job_rows = [
        ("42420", "0", "31.4 GiB", "user", "02:13", "~/SRA", "omx --madmax"),
        ("43118", "1", "96.8 GiB", "user", "00:48", "~/project", "python train.py --config gpu.yaml"),
        ("43991", "4", "82.1 GiB", "team", "05:02", "~/exp", "torchrun --nproc_per_node=8"),
    ]
    visible = 2 + (frame // 16) % 2
    for row in job_rows[:visible]:
        parts.append(text(LEFT + 16, y, f"{row[0]:<7} {row[1]:<5} {row[2]:<9} {row[3]:<8} {row[4]:<9} {row[5]:<12} {row[6]}", TEXT))
        y += LINE_H

    parts.append(text(LEFT + 16, 606, "Tip: close the terminal anytime; run gpulse gpu01 again to reattach.", DIM, size=13))
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
