from __future__ import annotations

import json
from pathlib import Path
import math
import re
import shutil
import subprocess
import sys
import wave
from typing import Callable

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from core import Project, LyricLine


FONT_PATHS = [
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    Path(r"C:\Windows\Fonts\simsun.ttc"),
]

MAX_AUDIO_DURATION_LOSS = 0.25


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


def _estimated_output_bytes(width: int, height: int, fps: int, duration: float) -> int:
    # Allow for unusually detailed backgrounds and MP4 finalization overhead.
    video_bits_per_second = min(16_000_000, max(2_500_000, width * height * fps * 0.06))
    media_bytes = (video_bits_per_second + 192_000) * max(0.0, duration) / 8
    return math.ceil(media_bytes * 1.25 + 128 * 1024 * 1024)


def _is_no_space_error(detail: str) -> bool:
    normalized = detail.casefold()
    return any(
        marker in normalized
        for marker in (
            "no space left on device",
            "not enough space on the disk",
            "there is not enough space",
            "磁盘空间不足",
        )
    )


def ease(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3 - 2 * value)


def pick_font() -> Path:
    for path in FONT_PATHS:
        if path.exists():
            return path
    raise FileNotFoundError("找不到中文字体，请在 Windows Fonts 中安装微软雅黑/黑体/宋体。")


def load_fonts(project: Project):
    style = project.normalized().style
    font_path = pick_font()
    assert style is not None
    return {
        "active": ImageFont.truetype(str(font_path), style.active_font_size),
        "near": ImageFont.truetype(str(font_path), style.near_font_size),
        "far": ImageFont.truetype(str(font_path), style.far_font_size),
        "title": ImageFont.truetype(str(font_path), style.title_font_size),
        "credit": ImageFont.truetype(str(font_path), style.credit_font_size),
    }


def fit_background(project: Project) -> Image.Image:
    project = project.normalized()
    style = project.style
    assert style is not None
    src = Image.open(project.background_path).convert("RGB")
    scale = min(style.width / src.width, style.height / src.height)
    fit = src.resize((round(src.width * scale), round(src.height * scale)), Image.Resampling.LANCZOS)

    cover_scale = max(style.width / src.width, style.height / src.height)
    cover = src.resize((round(src.width * cover_scale), round(src.height * cover_scale)), Image.Resampling.LANCZOS)
    left = (cover.width - style.width) // 2
    top = (cover.height - style.height) // 2
    base = cover.crop((left, top, left + style.width, top + style.height)).filter(ImageFilter.GaussianBlur(22))
    base = Image.blend(base, Image.new("RGB", (style.width, style.height), (248, 239, 223)), 0.34)

    canvas = base.convert("RGBA")
    x = (style.width - fit.width) // 2
    y = (style.height - fit.height) // 2
    canvas.alpha_composite(fit.convert("RGBA"), (x, y))
    draw_gradient_overlay(canvas, project)
    return canvas


def draw_gradient_overlay(frame: Image.Image, project: Project) -> None:
    style = project.style
    assert style is not None
    overlay = Image.new("RGBA", (style.width, style.height), (0, 0, 0, 0))
    pix = overlay.load()
    start_y = int(style.height * 0.40)
    for y in range(start_y, style.height):
        alpha = int(8 + 150 * ((y - start_y) / (style.height - start_y)) ** 1.35)
        color = (255, 247, 232, alpha)
        for x in range(style.width):
            pix[x, y] = color
    frame.alpha_composite(overlay)


def draw_centered(draw: ImageDraw.ImageDraw, width: int, y: int, text: str, font, fill, stroke, stroke_width=1):
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    x = (width - (bbox[2] - bbox[0])) // 2
    draw.text((x, y), text, font=font, fill=fill, stroke_width=stroke_width, stroke_fill=stroke)


def fit_credit_font(draw: ImageDraw.ImageDraw, project: Project, fonts: dict):
    style = project.style
    assert style is not None
    font_path = pick_font()
    for size in range(style.credit_font_size, 13, -1):
        font = ImageFont.truetype(str(font_path), size)
        bbox = draw.textbbox((0, 0), project.credit_line, font=font, stroke_width=1)
        if bbox[2] - bbox[0] <= style.width - 100:
            return font
    return fonts["credit"]


def draw_info(frame: Image.Image, project: Project, t: float, fonts: dict) -> None:
    style = project.style
    assert style is not None
    if t < style.info_appear_seconds:
        return
    alpha_factor = ease((t - style.info_appear_seconds) / style.info_fade_seconds)
    draw = ImageDraw.Draw(frame)
    title_alpha = int(240 * alpha_factor)
    credit_alpha = int(220 * alpha_factor)
    draw_centered(
        draw,
        style.width,
        style.title_y,
        project.title,
        fonts["title"],
        (177, 42, 23, title_alpha),
        (255, 247, 232, min(230, title_alpha)),
        2,
    )
    credit_font = fit_credit_font(draw, project, fonts)
    draw_centered(
        draw,
        style.width,
        style.credit_y,
        project.credit_line,
        credit_font,
        (38, 75, 98, credit_alpha),
        (255, 247, 232, int(195 * alpha_factor)),
        1,
    )


def current_index(lines: list[LyricLine], t: float) -> int:
    idx = 0
    for i, line in enumerate(lines):
        if line.start <= t:
            idx = i
        else:
            break
    return idx


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    rows: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
        else:
            if current:
                rows.append(current)
            current = char
    if current:
        rows.append(current)
    return rows[:2]


def draw_text_centered(draw, x_center, y_center, lines, font, fill, stroke, stroke_width, alpha):
    line_height = int(font.size * 1.26)
    total_h = line_height * len(lines)
    y = y_center - total_h // 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
        x = x_center - (bbox[2] - bbox[0]) // 2
        draw.text(
            (x, y),
            line,
            font=font,
            fill=(*fill, alpha),
            stroke_width=stroke_width,
            stroke_fill=(*stroke, min(230, alpha + 30)),
        )
        y += line_height


def render_frame(project: Project, base: Image.Image, fonts: dict, t: float) -> Image.Image:
    project = project.normalized()
    style = project.style
    lines = project.lines or []
    assert style is not None

    frame = base.copy()
    draw_info(frame, project, t, fonts)
    if not lines or t < lines[0].start:
        return frame.convert("RGB")

    idx = current_index(lines, t)
    start = lines[idx].start
    if idx > 0 and t < start + style.transition_seconds:
        offset = idx - 1 + ease((t - start) / style.transition_seconds)
    else:
        offset = idx

    draw = ImageDraw.Draw(frame)
    for i in range(max(0, idx - style.max_lines_above), min(len(lines), idx + 5)):
        distance = i - offset
        y = style.lyrics_center_y + distance * style.line_gap
        if y < style.credit_y + 70 or y > style.height - 190:
            continue
        abs_d = abs(distance)
        if abs_d < 0.45:
            font = fonts["active"]
            fill = style.active_color
            alpha = 255
            stroke_width = 3
        elif abs_d < 1.6:
            font = fonts["near"]
            fill = style.near_color
            alpha = int(205 - 55 * min(1.0, abs_d - 0.45))
            stroke_width = 2
        else:
            font = fonts["far"]
            fill = style.far_color
            alpha = max(70, int(135 - 22 * abs_d))
            stroke_width = 1
        if idx == 0:
            alpha = int(alpha * ease((t - lines[0].start) / 1.2))
            if alpha <= 0:
                continue
        wrapped = wrap_text(draw, lines[i].text, font, style.width - 180)
        draw_text_centered(
            draw,
            style.width // 2,
            int(y),
            wrapped,
            font,
            fill,
            (255, 247, 232),
            stroke_width,
            alpha,
        )

    return frame.convert("RGB")


def _ffprobe_path(ffmpeg_path: str | Path) -> Path | None:
    ffmpeg = Path(ffmpeg_path)
    names = ("ffprobe.exe", "ffprobe") if sys.platform == "win32" else ("ffprobe", "ffprobe.exe")
    for name in names:
        candidate = ffmpeg.with_name(name)
        if candidate.exists():
            return candidate
    return None


def _probe_duration(
    media_path: str | Path,
    ffmpeg_path: str | Path,
    stream_selector: str | None = None,
) -> float | None:
    ffprobe = _ffprobe_path(ffmpeg_path)
    if ffprobe is None or not Path(media_path).exists():
        return None

    command = [str(ffprobe), "-v", "error"]
    if stream_selector:
        command.extend(["-select_streams", stream_selector])
    command.extend(
        [
            "-show_entries",
            "stream=duration:format=duration",
            "-of",
            "json",
            str(media_path),
        ]
    )
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=30,
            check=True,
        )
        payload = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None

    durations: list[float] = []
    for stream in payload.get("streams", []):
        try:
            value = float(stream.get("duration"))
        except (TypeError, ValueError):
            continue
        if value > 0:
            durations.append(value)
    if durations:
        return max(durations)

    try:
        value = float((payload.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def audio_duration(project: Project) -> float:
    audio = Path(project.audio_path)
    ffmpeg = Path(project.ffmpeg_path)
    probed = _probe_duration(audio, ffmpeg, "a:0")
    if probed is not None:
        return probed

    if ffmpeg.exists() and audio.exists():
        probe = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-i", str(audio)],
            capture_output=True,
            text=True,
            errors="ignore",
        )
        output = f"{probe.stdout}\n{probe.stderr}"
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
        if match:
            hours, minutes, seconds = match.groups()
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    if audio.suffix.lower() == ".wav":
        try:
            with wave.open(str(audio), "rb") as wav:
                return wav.getnframes() / float(wav.getframerate())
        except wave.Error:
            pass
    if project.lines:
        return max(line.end for line in project.lines) + 20
    return 0.0


def render_preview(project: Project, out_path: str | Path, timestamp: float) -> Path:
    base = fit_background(project)
    fonts = load_fonts(project)
    frame = render_frame(project, base, fonts, timestamp)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.save(out)
    return out


def render_video(project: Project, progress: Callable[[str], None] | None = None) -> Path:
    project = project.normalized()
    style = project.style
    assert style is not None
    if not project.lines:
        raise ValueError("没有歌词时间轴，请先载入 SRT。")
    if not Path(project.audio_path).exists():
        raise FileNotFoundError(f"音频不存在：{project.audio_path}")
    if not Path(project.background_path).exists():
        raise FileNotFoundError(f"背景图不存在：{project.background_path}")
    if not Path(project.ffmpeg_path).exists():
        raise FileNotFoundError(f"FFmpeg 不存在：{project.ffmpeg_path}")

    duration = audio_duration(project)
    if duration <= 0:
        raise RuntimeError("无法读取音频时长，请检查音频文件是否完整。")
    frame_count = math.ceil(duration * style.fps)
    base = fit_background(project)
    fonts = load_fonts(project)
    output_text = project.output_path.strip().strip('"')
    if not output_text:
        raise ValueError("请先选择 MP4 输出路径。")
    out_path = Path(output_text)
    if out_path.suffix == "":
        out_path = out_path.with_suffix(".mp4")
    elif out_path.suffix.lower() != ".mp4":
        raise ValueError(f"输出文件必须使用 .mp4 扩展名：{out_path}")
    if out_path.exists() and out_path.is_dir():
        raise ValueError(f"MP4 输出路径不能是文件夹：{out_path}")
    if out_path.name.rstrip(" .") != out_path.name:
        raise ValueError(f"输出文件名不能以空格或句点结尾：{out_path.name}")
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(f"无法创建视频输出目录：{out_path.parent}\n{exc}") from exc

    estimated_bytes = _estimated_output_bytes(style.width, style.height, style.fps, duration)
    existing_bytes = out_path.stat().st_size if out_path.exists() and out_path.is_file() else 0
    try:
        disk_free = shutil.disk_usage(out_path.parent).free
    except OSError as exc:
        raise RuntimeError(f"无法读取输出磁盘的剩余空间：{out_path.parent}\n{exc}") from exc
    effective_free = disk_free + existing_bytes
    if effective_free < estimated_bytes:
        raise RuntimeError(
            "输出磁盘空间不足，尚未开始渲染。\n"
            f"输出路径：{out_path}\n"
            f"当前可用：{_format_bytes(effective_free)}\n"
            f"建议至少保留：{_format_bytes(estimated_bytes)}\n"
            "请清理该磁盘，或把 MP4 输出路径改到空间充足的磁盘。"
        )

    project.output_path = str(out_path)
    if progress:
        progress(f"原音频时长：{duration:.3f}s；将完整保留前奏和尾奏。")
        progress(
            f"输出盘可用空间：{_format_bytes(effective_free)}；"
            f"本次建议至少保留：{_format_bytes(estimated_bytes)}"
        )

    cmd = [
        project.ffmpeg_path,
        "-y",
        "-loglevel",
        "warning",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{style.width}x{style.height}",
        "-r",
        str(style.fps),
        "-i",
        "-",
        "-i",
        project.audio_path,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(out_path),
    ]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
    except OSError as exc:
        raise RuntimeError(f"无法启动 FFmpeg：{project.ffmpeg_path}\n{exc}") from exc
    assert proc.stdin is not None
    pipe_error: OSError | None = None
    try:
        for frame_idx in range(frame_count):
            t = frame_idx / style.fps
            proc.stdin.write(render_frame(project, base, fonts, t).tobytes())
            if progress and frame_idx and frame_idx % (style.fps * 10) == 0:
                progress(f"已渲染 {frame_idx // style.fps}s / {int(duration)}s")
    except (BrokenPipeError, OSError) as exc:
        pipe_error = exc
    finally:
        try:
            proc.stdin.close()
        except OSError as exc:
            pipe_error = pipe_error or exc
    stderr = proc.stderr.read().decode("utf-8", errors="ignore") if proc.stderr else ""
    code = proc.wait()
    if code:
        detail = stderr.strip() or str(pipe_error or "没有返回详细错误")
        if _is_no_space_error(detail):
            partial_bytes = out_path.stat().st_size if out_path.exists() and out_path.is_file() else 0
            cleanup_note = "未发现残缺输出文件。"
            if partial_bytes:
                try:
                    out_path.unlink()
                    cleanup_note = f"已自动清理残缺文件（{_format_bytes(partial_bytes)}）。"
                except OSError as exc:
                    cleanup_note = f"残缺文件未能自动删除，请手动删除：{out_path}\n{exc}"
            try:
                remaining = _format_bytes(shutil.disk_usage(out_path.parent).free)
            except OSError:
                remaining = "无法读取"
            raise RuntimeError(
                "输出磁盘空间已耗尽，视频未能完成。\n"
                f"输出路径：{out_path}\n"
                f"当前可用：{remaining}\n"
                f"{cleanup_note}\n"
                "请释放空间，或改到空间充足的磁盘后重新导出。"
            )
        raise RuntimeError(
            f"FFmpeg 导出失败（退出代码 {code}）。\n"
            f"输出路径：{out_path}\n"
            f"详细信息：{detail}"
        )
    if pipe_error:
        raise RuntimeError(f"向 FFmpeg 写入画面时失败：{pipe_error}")

    source_audio_duration = _probe_duration(project.audio_path, project.ffmpeg_path, "a:0")
    output_audio_duration = _probe_duration(out_path, project.ffmpeg_path, "a:0")
    if source_audio_duration is not None and output_audio_duration is not None:
        lost = source_audio_duration - output_audio_duration
        if lost > MAX_AUDIO_DURATION_LOSS:
            raise RuntimeError(
                "视频已经生成，但音频轨不完整，已阻止把它当作成功结果。\n"
                f"原音频：{source_audio_duration:.3f}s\n"
                f"成品音频轨：{output_audio_duration:.3f}s\n"
                f"缺失：{lost:.3f}s\n"
                f"问题文件：{out_path}"
            )
        if progress:
            progress(f"音频完整性检查通过：{output_audio_duration:.3f}s")
    if progress:
        progress(f"完成：{out_path}")
    return out_path


if __name__ == "__main__":
    print("render_engine is a library; run app.py to start the UI.")
    sys.exit(0)
