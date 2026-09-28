from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import json
import re
import shutil
from typing import Any


@dataclass
class LyricLine:
    start: float
    end: float
    text: str


@dataclass
class VideoStyle:
    width: int = 1080
    height: int = 1440
    fps: int = 60
    active_font_size: int = 48
    near_font_size: int = 38
    far_font_size: int = 32
    title_font_size: int = 58
    credit_font_size: int = 22
    active_color: tuple[int, int, int] = (184, 48, 24)
    near_color: tuple[int, int, int] = (35, 75, 100)
    far_color: tuple[int, int, int] = (75, 90, 100)
    title_y: int = 358
    credit_y: int = 466
    lyrics_center_y: int = 738
    line_gap: int = 96
    max_lines_above: int = 3
    transition_seconds: float = 0.85
    info_appear_seconds: float = 2.0
    info_fade_seconds: float = 1.4


@dataclass
class Project:
    audio_path: str = ""
    background_path: str = ""
    txt_path: str = ""
    srt_path: str = ""
    output_path: str = ""
    ffmpeg_path: str = ""
    title: str = "我的歌曲"
    credit_line: str = ""
    lines: list[LyricLine] | None = None
    style: VideoStyle | None = None

    def normalized(self) -> "Project":
        if self.lines is None:
            self.lines = []
        if self.style is None:
            self.style = VideoStyle()
        return self


def parse_timestamp(value: str) -> float:
    value = value.strip().replace(".", ",")
    match = re.fullmatch(r"(\d{1,2}):(\d{2}):(\d{2}),(\d{1,3})", value)
    if not match:
        raise ValueError(f"Invalid timestamp: {value}")
    hours, minutes, seconds, millis = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis.ljust(3, "0")) / 1000


def format_timestamp(seconds: float) -> str:
    ms_total = max(0, round(seconds * 1000))
    hours, rem = divmod(ms_total, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def load_srt(path: str | Path) -> list[LyricLine]:
    text = Path(path).read_text(encoding="utf-8-sig").strip()
    if not text:
        return []
    rows: list[LyricLine] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        start_text, end_text = [part.strip() for part in lines[1].split("-->", 1)]
        rows.append(LyricLine(parse_timestamp(start_text), parse_timestamp(end_text), " ".join(lines[2:])))
    return rows


def save_srt(path: str | Path, lines: list[LyricLine]) -> None:
    blocks = []
    for index, line in enumerate(lines, start=1):
        blocks.append(
            f"{index}\n{format_timestamp(line.start)} --> {format_timestamp(line.end)}\n{line.text.strip()}\n"
        )
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(blocks), encoding="utf-8-sig")


def project_to_dict(project: Project) -> dict[str, Any]:
    project = project.normalized()
    data = asdict(project)
    if project.style:
        data["style"] = asdict(project.style)
    return data


def project_from_dict(data: dict[str, Any]) -> Project:
    lines = [LyricLine(**row) for row in data.get("lines", [])]
    style_data = data.get("style") or {}
    if isinstance(style_data.get("active_color"), list):
        for key in ("active_color", "near_color", "far_color"):
            style_data[key] = tuple(style_data[key])
    style = VideoStyle(**style_data)
    return Project(
        audio_path=data.get("audio_path", ""),
        background_path=data.get("background_path", ""),
        txt_path=data.get("txt_path", ""),
        srt_path=data.get("srt_path", ""),
        output_path=data.get("output_path", ""),
        ffmpeg_path=data.get("ffmpeg_path", ""),
        title=data.get("title", "我的歌曲"),
        credit_line=data.get("credit_line", ""),
        lines=lines,
        style=style,
    ).normalized()


def load_project(path: str | Path) -> Project:
    return project_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def save_project(path: str | Path, project: Project) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(project_to_dict(project), ensure_ascii=False, indent=2), encoding="utf-8")


def default_project(base_dir: str | Path) -> Project:
    base = Path(base_dir)
    output_dir = base / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)

    bundled_ffmpeg = base / "ffmpeg" / "ffmpeg.exe"
    system_ffmpeg = shutil.which("ffmpeg") or ""
    ffmpeg = str(bundled_ffmpeg) if bundled_ffmpeg.exists() else system_ffmpeg

    sample_dir = base / "samples"
    audio = sample_dir / "demo.wav"
    background = sample_dir / "background.png"
    txt = sample_dir / "lyrics.txt"
    srt = sample_dir / "lyrics.srt"

    return Project(
        audio_path=str(audio) if audio.exists() else "",
        background_path=str(background) if background.exists() else "",
        txt_path=str(txt) if txt.exists() else "",
        srt_path=str(srt) if srt.exists() else str(output_dir / "lyrics.srt"),
        output_path=str(output_dir / "lyrics_video.mp4"),
        ffmpeg_path=ffmpeg,
        lines=load_srt(srt) if srt.exists() else [],
        style=VideoStyle(),
    ).normalized()
