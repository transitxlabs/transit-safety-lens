#!/usr/bin/env python3
"""Regenerate showcase images, captions and demo video from committed outputs."""

from __future__ import annotations

import asyncio
import base64
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
MEDIA = DOCS / "media"
SHOWCASE = ROOT / "showcase-media"
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
FFMPEG = Path(shutil.which("ffmpeg") or "")
EXIFTOOL = Path(shutil.which("exiftool") or "")
VIDEO_SIZE = (1280, 720)
CAPTION_FONT_SIZE = 28
CAPTION_PAD = 14
CAPTION_MAX_LINES = 2
STEP_HOLD_SECONDS = 5.0

# caption, page path or media path, scroll selector (page only), wait selector (page only)
STEPS: list[tuple[str, str, str | None, str | None]] = [
    (
        "Overview of the static results page.",
        "/index.html",
        None,
        "main#main h1",
    ),
    (
        "Change comparison table for 893 agency and mode pairs.",
        "/index.html",
        "#model-results",
        "#model-results table.results tbody tr",
    ),
    (
        "Frozen record coverage by dataset.",
        "/index.html",
        "#coverage",
        "#coverage table tbody tr",
    ),
    (
        "Reproduce section with technical note links.",
        "/index.html",
        "#reproduce",
        "#reproduce h2",
    ),
    (
        "Bar chart of raw count flags versus model flags.",
        "/media/chart-flag-comparison.png",
        None,
        None,
    ),
    (
        "Held-out predictive coverage below the nominal 95 percent level.",
        "/media/chart-heldout-coverage.png",
        None,
        None,
    ),
    (
        "Pipeline from frozen inputs to static page.",
        "/media/architecture.png",
        None,
        None,
    ),
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


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class CdpSession:
    def __init__(self) -> None:
        self._msg_id = 0
        self._ws: object | None = None

    async def connect(self, ws_url: str) -> None:
        import websockets

        self._ws = await websockets.connect(ws_url, max_size=None)

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def call(self, method: str, params: dict | None = None) -> dict:
        assert self._ws is not None
        self._msg_id += 1
        req_id = self._msg_id
        await self._ws.send(json.dumps({"id": req_id, "method": method, "params": params or {}}))
        while True:
            raw = await self._ws.recv()
            data = json.loads(raw)
            if data.get("id") != req_id:
                continue
            if "error" in data:
                raise RuntimeError(data["error"])
            return data.get("result") or {}


async def cdp_capture_viewport(
    port: int,
    url: str,
    scroll_selector: str | None,
    wait_selector: str | None,
    settle_s: float,
) -> bytes:
    quoted = urllib.parse.quote(url, safe=":/?#=&%")
    with urllib.request.urlopen(
        urllib.request.Request(f"http://127.0.0.1:{port}/json/new?{quoted}", method="PUT"),
        timeout=30,
    ) as resp:
        target = json.load(resp)
    ws_url = target["webSocketDebuggerUrl"]
    session = CdpSession()
    await session.connect(ws_url)
    try:
        await session.call("Page.enable")
        await session.call("Page.navigate", {"url": url})
        for _ in range(80):
            ready = await session.call(
                "Runtime.evaluate",
                {"expression": "document.readyState", "returnByValue": True},
            )
            if ready.get("result", {}).get("value") == "complete":
                break
            await asyncio.sleep(0.05)
        if wait_selector:
            expr = (
                f"(function() {{"
                f"const el = document.querySelector({json.dumps(wait_selector)});"
                f"return Boolean(el);"
                f"}})()"
            )
            for _ in range(60):
                found = await session.call(
                    "Runtime.evaluate",
                    {"expression": expr, "returnByValue": True},
                )
                if found.get("result", {}).get("value"):
                    break
                await asyncio.sleep(0.05)
        if scroll_selector:
            scroll_expr = (
                f"(function() {{"
                f"const el = document.querySelector({json.dumps(scroll_selector)});"
                f"if (el) el.scrollIntoView({{block: 'start', inline: 'nearest'}});"
                f"return Boolean(el);"
                f"}})()"
            )
            await session.call("Runtime.evaluate", {"expression": scroll_expr})
        await asyncio.sleep(settle_s)
        shot = await session.call("Page.captureScreenshot", {"format": "png", "fromSurface": True})
        data = shot.get("data")
        if not data:
            raise RuntimeError("empty screenshot from Chrome")
        return base64.b64decode(data)
    finally:
        await session.close()
        target_id = target.get("id")
        if target_id:
            urllib.request.urlopen(
                urllib.request.Request(
                    f"http://127.0.0.1:{port}/json/close/{target_id}",
                    method="GET",
                ),
                timeout=10,
            )


def is_near_blank(path: Path) -> bool:
    img = Image.open(path).convert("RGB")
    small = img.resize((160, 90))
    pixels = list(small.getdata())
    if not pixels:
        return True
    mean_r = sum(p[0] for p in pixels) / len(pixels)
    mean_g = sum(p[1] for p in pixels) / len(pixels)
    mean_b = sum(p[2] for p in pixels) / len(pixels)
    within = 0
    for r, g, b in pixels:
        if abs(r - mean_r) <= 8 and abs(g - mean_g) <= 8 and abs(b - mean_b) <= 8:
            within += 1
    return within / len(pixels) >= 0.92


def capture_step_frame(
    port: int,
    caption: str,
    path: str,
    scroll: str | None,
    wait: str | None,
    dest: Path,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = page_url(path)
    for attempt in range(2):
        settle = 0.35 if attempt == 0 else 0.9
        png = asyncio.run(cdp_capture_viewport(port, url, scroll, wait, settle))
        dest.write_bytes(png)
        if not is_near_blank(dest):
            return
    print(f"Warning: frame may be blank after retry: {caption}", file=sys.stderr)


def start_headless_chrome(port: int) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            str(CHROME),
            f"--remote-debugging-port={port}",
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--allow-file-access-from-files",
            "--hide-scrollbars",
            "--window-size=1400,900",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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
    for caption, _, _, _ in STEPS:
        start = format_time(t)
        end = format_time(t + STEP_HOLD_SECONDS)
        lines.extend([f"{start} --> {end}", caption, ""])
        t += STEP_HOLD_SECONDS
    path.write_text("\n".join(lines), encoding="utf-8")


def format_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def wrap_caption(text: str, draw: ImageDraw.ImageDraw, caption_font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=caption_font) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines[:CAPTION_MAX_LINES]


def fit_content_frame(src: Image.Image, content_box: tuple[int, int, int, int]) -> Image.Image:
    x0, y0, x1, y1 = content_box
    canvas = Image.new("RGB", VIDEO_SIZE, "#1a1a1a")
    area_w, area_h = x1 - x0, y1 - y0
    scale = min(area_w / src.width, area_h / src.height)
    new_w = max(1, int(src.width * scale))
    new_h = max(1, int(src.height * scale))
    resized = src.resize((new_w, new_h), Image.Resampling.LANCZOS)
    ox = x0 + (area_w - new_w) // 2
    oy = y0 + (area_h - new_h) // 2
    canvas.paste(resized, (ox, oy))
    return canvas


def add_caption_frame(frame_path: Path, caption: str, dest: Path) -> None:
    src = Image.open(frame_path).convert("RGB")
    caption_font = font(CAPTION_FONT_SIZE)
    caption_h = CAPTION_FONT_SIZE * CAPTION_MAX_LINES + CAPTION_PAD * 3
    content_box = (0, 0, VIDEO_SIZE[0], VIDEO_SIZE[1] - caption_h)
    base = fit_content_frame(src, content_box)
    draw = ImageDraw.Draw(base)
    max_text_w = VIDEO_SIZE[0] - 2 * CAPTION_PAD
    lines = wrap_caption(caption, draw, caption_font, max_text_w)
    line_h = CAPTION_FONT_SIZE + 6
    box_h = len(lines) * line_h + CAPTION_PAD * 2
    box_top = VIDEO_SIZE[1] - box_h
    draw.rectangle([0, box_top, VIDEO_SIZE[0], VIDEO_SIZE[1]], fill="#111111")
    y = box_top + CAPTION_PAD
    for line in lines:
        draw.text((VIDEO_SIZE[0] // 2, y), line, fill="white", font=caption_font, anchor="mt")
        y += line_h
    dest.parent.mkdir(parents=True, exist_ok=True)
    base.save(dest)


def build_video(frame_dir: Path, mp4: Path, preview: Path, poster: Path) -> None:
    segments: list[Path] = []
    for idx, (caption, _, _, _) in enumerate(STEPS):
        frame = frame_dir / f"step-{idx:02d}.png"
        labelled = frame_dir / f"step-{idx:02d}-cap.png"
        segment = frame_dir / f"step-{idx:02d}.mp4"
        add_caption_frame(frame, caption, labelled)
        run(
            [
                str(FFMPEG),
                "-y",
                "-loop",
                "1",
                "-i",
                str(labelled),
                "-t",
                str(STEP_HOLD_SECONDS),
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


def main() -> int:
    if not CHROME.is_file():
        print("Google Chrome not found for headless screenshots.", file=sys.stderr)
        return 1
    if not FFMPEG or not FFMPEG.is_file():
        print("ffmpeg not found on PATH.", file=sys.stderr)
        return 1
    try:
        import websockets  # noqa: F401
    except ImportError:
        print("websockets package required for demo frame capture.", file=sys.stderr)
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

    port = free_port()
    chrome_proc = start_headless_chrome(port)
    try:
        for _ in range(40):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as resp:
                    if resp.status == 200:
                        break
            except OSError:
                pass
            time.sleep(0.1)
        for idx, (caption, path, scroll, wait) in enumerate(STEPS):
            capture_step_frame(
                port,
                caption,
                path,
                scroll,
                wait,
                frames / f"step-{idx:02d}.png",
            )
    finally:
        chrome_proc.terminate()
        chrome_proc.wait(timeout=10)

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
