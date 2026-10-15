#!/usr/bin/env python3
"""Regenerate showcase images, captions and demo video from committed outputs."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
MEDIA = DOCS / "media"
SHOWCASE = ROOT / "showcase-media"
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
FFMPEG = Path(shutil.which("ffmpeg") or "")
EXIFTOOL = Path("/opt/homebrew/bin/exiftool")
STEPS = [
    ("Overview of the static results page.", "/index.html", 0),
    ("Change comparison table for 893 agency and mode pairs.", "/index.html#model-results", 0),
    ("Frozen record coverage by dataset.", "/index.html#coverage", 0),
    ("Reproduce section with technical note links.", "/index.html#reproduce", 0),
    ("Bar chart of raw count flags versus model flags.", "/media/chart-flag-comparison.png", 0),
    ("Held-out predictive coverage below the nominal 95 percent level.", "/media/chart-heldout-coverage.png", 0),
    ("Pipeline from frozen inputs to static page.", "/media/architecture.png", 0),
]


def run(cmd: list[str], **kwargs) -> None:
    subprocess.run(cmd, check=True, **kwargs)


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("Helvetica.ttc", "Arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def page_url(path: str) -> str:
    fragment = ""
    if "#" in path:
        path, fragment = path.split("#", 1)
    target = (DOCS / path.lstrip("/")).resolve()
    url = target.as_uri()
    return f"{url}#{fragment}" if fragment else url


def chrome_screenshot(url: str, dest: Path, window: str = "1400,900") -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            str(CHROME),
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            f"--screenshot={dest}",
            f"--window-size={window}",
            "--hide-scrollbars",
            url,
        ]
    )


def strip_image(path: Path) -> None:
    if EXIFTOOL.is_file():
        run([str(EXIFTOOL), "-all=", "-overwrite_original", str(path)])


def load_summary() -> dict:
    return json.loads((ROOT / "outputs/summary.json").read_text())


def draw_bar_chart(
    dest: Path,
    title: str,
    labels: list[str],
    values: list[int],
    colours: list[str],
    y_label: str,
) -> None:
    width, height = 960, 600
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    title_font = font(22)
    label_font = font(16)
    value_font = font(14)
    draw.text((width // 2, 24), title, fill="#172c3a", font=title_font, anchor="ma")
    draw.text((48, height // 2), y_label, fill="#172c3a", font=label_font, anchor="lm")
    margin_l, margin_b, margin_t = 120, 90, 70
    chart_w = width - margin_l - 40
    chart_h = height - margin_b - margin_t
    ymax = max(values) * 1.15
    bar_w = chart_w // (len(values) * 2)
    for idx, (label, value, colour) in enumerate(zip(labels, values, colours, strict=True)):
        x0 = margin_l + idx * (chart_w // len(values)) + bar_w // 2
        bar_h = int(chart_h * value / ymax)
        y0 = margin_t + chart_h - bar_h
        draw.rectangle([x0, y0, x0 + bar_w, margin_t + chart_h], fill=colour, outline="#172c3a")
        draw.text((x0 + bar_w // 2, y0 - 8), str(value), fill="#172c3a", font=value_font, anchor="mb")
        draw.text((x0 + bar_w // 2, margin_t + chart_h + 12), label, fill="#172c3a", font=value_font, anchor="mt")
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest)


def chart_flag_comparison(dest: Path) -> None:
    summary = load_summary()
    raw_inc = raw_dec = model_inc = model_dec = 0
    for row in summary["baselines"]:
        if row["baseline"] != "raw_count":
            continue
        if row["direction"] == "rate increase":
            raw_inc = row["naive_flags"]
            model_inc = row["credible_flags"]
        else:
            raw_dec = row["naive_flags"]
            model_dec = row["credible_flags"]
    draw_bar_chart(
        dest,
        "Raw count flags versus model flags (2021 to 2022)",
        ["Raw inc", "Model inc", "Raw dec", "Model dec"],
        [raw_inc, model_inc, raw_dec, model_dec],
        ["#c45c26", "#143a4a", "#c45c26", "#143a4a"],
        "Flagged comparisons (of 893)",
    )


def chart_heldout_coverage(dest: Path) -> None:
    held = load_summary()["heldout"]
    covered, total = held["covered"], held["eligible"]
    missed = total - covered
    pct = 100 * covered / total
    draw_bar_chart(
        dest,
        f"Held-out predictive coverage: {pct:.1f}% ({covered} of {total})",
        ["Inside", "Outside"],
        [covered, missed],
        ["#143a4a", "#c45c26"],
        "Held-out outcomes",
    )


def chart_architecture(dest: Path) -> None:
    width, height = 1080, 520
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    title_font = font(20)
    box_font = font(14)
    draw.text((width // 2, 20), "Offline rebuild pipeline", fill="#172c3a", font=title_font, anchor="ma")
    boxes = [
        (40, 80, "Frozen NTD inputs"),
        (400, 80, "Record checks and quality"),
        (760, 80, "Exposure joins and panel"),
        (40, 260, "Peer-group priors (2019)"),
        (400, 260, "2021 to 2022 comparisons"),
        (760, 260, "Held-out evaluation"),
        (400, 400, "CSV outputs, technical note, static page"),
    ]
    for x, y, label in boxes:
        draw.rounded_rectangle([x, y, x + 240, y + 70], radius=8, outline="#143a4a", width=2, fill="#eaf0f3")
        draw.multiline_text((x + 120, y + 35), label, fill="#172c3a", font=box_font, anchor="mm", align="center")
    arrows = [
        (280, 115, 400, 115),
        (640, 115, 760, 115),
        (160, 150, 160, 260),
        (520, 150, 520, 260),
        (880, 150, 880, 260),
        (280, 295, 400, 295),
        (640, 295, 760, 295),
        (520, 330, 520, 400),
    ]
    for x0, y0, x1, y1 in arrows:
        draw.line([x0, y0, x1, y1], fill="#172c3a", width=2)
        draw.polygon(_arrow_head(x0, y0, x1, y1), fill="#172c3a")
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest)


def _arrow_head(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
    if x1 > x0:
        return [(x1, y1), (x1 - 10, y1 - 6), (x1 - 10, y1 + 6)]
    if y1 > y0:
        return [(x1, y1), (x1 - 6, y1 - 10), (x1 + 6, y1 - 10)]
    return [(x1, y1), (x1 + 6, y1 + 10), (x1 - 6, y1 + 10)]


def write_vtt(path: Path) -> None:
    lines = ["WEBVTT", ""]
    t = 0.0
    for caption, _, _hold in STEPS:
        duration = 5.0
        start = format_time(t)
        end = format_time(t + duration)
        lines.extend([f"{start} --> {end}", caption, ""])
        t += duration
    path.write_text("\n".join(lines), encoding="utf-8")


def format_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def build_video(frame_dir: Path, mp4: Path, preview: Path, poster: Path) -> None:
    duration = 5.0
    segments: list[Path] = []
    for idx, (caption, _, _) in enumerate(STEPS):
        frame = frame_dir / f"step-{idx:02d}.png"
        labelled = frame_dir / f"step-{idx:02d}-cap.png"
        segment = frame_dir / f"step-{idx:02d}.mp4"
        run(
            [
                str(FFMPEG),
                "-y",
                "-i",
                str(frame),
                "-vf",
                f"drawtext=text='{escape_drawtext(caption)}':fontsize=28:fontcolor=white:"
                "box=1:boxcolor=black@0.55:boxborderw=12:x=(w-text_w)/2:y=h-90",
                str(labelled),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        run(
            [
                str(FFMPEG),
                "-y",
                "-loop",
                "1",
                "-i",
                str(labelled),
                "-t",
                str(duration),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-map_metadata",
                "-1",
                str(segment),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        segments.append(segment)

    list_file = frame_dir / "concat.txt"
    list_file.write_text("\n".join(f"file '{segment}'" for segment in segments) + "\n", encoding="utf-8")
    mp4.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            str(FFMPEG),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            "-map_metadata",
            "-1",
            str(mp4),
        ]
    )
    shutil.copy(frame_dir / "step-00-cap.png", poster)
    run(
        [
            str(FFMPEG),
            "-y",
            "-i",
            str(mp4),
            "-vf",
            "fps=8,scale=720:-1:flags=lanczos",
            "-map_metadata",
            "-1",
            str(preview),
        ]
    )


def escape_drawtext(text: str) -> str:
    return text.replace(":", "\\:").replace("'", "\\'")


def main() -> int:
    if not CHROME.is_file():
        print("Google Chrome not found for headless screenshots.", file=sys.stderr)
        return 1
    if not FFMPEG or not FFMPEG.is_file():
        print("ffmpeg not found on PATH.", file=sys.stderr)
        return 1

    MEDIA.mkdir(parents=True, exist_ok=True)
    SHOWCASE.mkdir(parents=True, exist_ok=True)
    frames = MEDIA / "demo-frames"
    if frames.exists():
        shutil.rmtree(frames)
    frames.mkdir()

    chart_flag_comparison(MEDIA / "chart-flag-comparison.png")
    chart_heldout_coverage(MEDIA / "chart-heldout-coverage.png")
    chart_architecture(MEDIA / "architecture.png")

    chrome_screenshot(page_url("/index.html"), MEDIA / "hero.png")
    full_page = MEDIA / "_full-page.png"
    chrome_screenshot(page_url("/index.html"), full_page, window="1400,3600")
    page = Image.open(full_page)
    page.crop((0, 520, 1400, 1180)).save(MEDIA / "example-results-table.png")
    page.crop((0, 1180, 1400, 1900)).save(MEDIA / "example-coverage-table.png")
    full_page.unlink(missing_ok=True)
    for idx, (_, path, _) in enumerate(STEPS):
        chrome_screenshot(page_url(path), frames / f"step-{idx:02d}.png")

    for path in list(MEDIA.glob("*.png")) + list(frames.glob("*.png")):
        strip_image(path)

    vtt = MEDIA / "demo.en.vtt"
    write_vtt(vtt)
    mp4 = SHOWCASE / "demo.mp4"
    preview = MEDIA / "demo-preview.gif"
    poster = MEDIA / "demo-poster.png"
    build_video(frames, mp4, preview, poster)
    strip_image(poster)
    shutil.rmtree(frames)

    print("Wrote", MEDIA)
    print("Wrote", mp4, f"({mp4.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
