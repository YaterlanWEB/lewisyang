from __future__ import annotations

import ctypes
from dataclasses import dataclass
from difflib import SequenceMatcher
import os
from pathlib import Path
import re
import statistics
import sys
from typing import Callable

from core import LyricLine, save_srt


SKIP_PREFIXES = (
    "作词",
    "作曲",
    "编曲",
    "词：",
    "曲：",
    "编：",
    "演唱",
    "歌手",
    "制作",
    "南音创作",
)

MIN_MATCH_SCORE = 0.34
QUALITY_ACCURATE = "accurate"
QUALITY_FAST = "fast"
DEVICE_CPU = "cpu"
DEVICE_CUDA = "cuda"
MODEL_ENHANCED = "large-v3-turbo"
MODEL_LIGHTWEIGHT = "small"

_DLL_DIRECTORY_HANDLES: list[object] = []


@dataclass(frozen=True)
class TimedChar:
    text: str
    start: float
    end: float


def _app_root() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def _cuda_directories(app_root: Path) -> list[Path]:
    return [app_root / "cuda", app_root / "_internal", app_root]


def _prepare_cuda_runtime(app_root: Path) -> None:
    for directory in _cuda_directories(app_root):
        if not (directory / "cublas64_12.dll").exists():
            continue
        os.environ["PATH"] = f"{directory}{os.pathsep}{os.environ.get('PATH', '')}"
        if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
            _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(directory)))
        return


def gpu_runtime_status(app_root: Path | None = None) -> tuple[bool, str]:
    root = app_root or _app_root()
    _prepare_cuda_runtime(root)
    try:
        import ctranslate2

        count = ctranslate2.get_cuda_device_count()
    except Exception as exc:
        return False, f"无法检查 GPU：{exc}"
    if count < 1:
        return False, "没有检测到可用的 NVIDIA CUDA 显卡。"
    if not any((directory / "cublas64_12.dll").exists() for directory in _cuda_directories(root)):
        return False, "缺少 GPU 运行库 cublas64_12.dll。"
    return True, f"检测到 {count} 块 NVIDIA CUDA 显卡。"


def _to_simplified_chinese(text: str) -> str:
    """Use Windows' built-in locale mapping so the portable build needs no dictionary."""
    if os.name != "nt" or not text:
        return text
    try:
        mapper = ctypes.windll.kernel32.LCMapStringEx
        flag = 0x02000000  # LCMAP_SIMPLIFIED_CHINESE
        needed = mapper("zh-CN", flag, text, len(text), None, 0, None, None, 0)
        if needed <= 0:
            return text
        target = ctypes.create_unicode_buffer(needed)
        written = mapper("zh-CN", flag, text, len(text), target, needed, None, None, 0)
        return target.value if written > 0 else text
    except (AttributeError, OSError):
        return text


def _norm_text(text: str) -> str:
    text = _to_simplified_chinese(text).lower()
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"[（(].*?[）)]", "", text)
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)
    return text


def _similarity(left: str, right: str) -> float:
    a = _norm_text(left)
    b = _norm_text(right)
    if not a or not b:
        return 0.0
    ratio = SequenceMatcher(None, a, b, autojunk=False).ratio()
    if a in b or b in a:
        ratio = max(ratio, min(len(a), len(b)) / max(len(a), len(b)))
    return ratio


def read_txt_lyrics(path: str | Path) -> list[str]:
    text = Path(path).read_text(encoding="utf-8-sig", errors="ignore")
    rows: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        line = re.sub(r"^\[.*?\]\s*", "", line).strip()
        if not line:
            continue
        if line.startswith(("《", "“")) and line.endswith(("》", "”")):
            continue
        if any(line.startswith(prefix) for prefix in SKIP_PREFIXES):
            continue
        rows.append(line)
    return rows


def _segment_chars(segment) -> list[TimedChar]:
    result: list[TimedChar] = []
    words = list(segment.words or [])
    for word in words:
        text = _norm_text(word.word or "")
        if not text or word.start is None or word.end is None:
            continue
        start = float(word.start)
        end = max(float(word.end), start + 0.02)
        step = (end - start) / len(text)
        for index, char in enumerate(text):
            result.append(TimedChar(char, start + index * step, start + (index + 1) * step))

    if result:
        return result

    text = _norm_text(segment.text or "")
    if not text:
        return result
    start = float(segment.start)
    end = max(float(segment.end), start + 0.02)
    step = (end - start) / len(text)
    return [TimedChar(char, start + index * step, start + (index + 1) * step) for index, char in enumerate(text)]


def _recognize_segments(
    audio_path: str,
    model_name: str,
    quality_mode: str,
    device: str,
    progress: Callable[[str], None] | None,
) -> tuple[list[tuple[float, float, str]], float, list[TimedChar]]:
    app_root = _app_root()
    bundled_model = app_root / "models" / model_name
    bundled_cache = app_root / "models" / "huggingface"
    if bundled_cache.exists():
        os.environ.setdefault("HF_HOME", str(bundled_cache))
        os.environ.setdefault("HF_HUB_CACHE", str(bundled_cache / "hub"))

    from faster_whisper import WhisperModel

    accurate = quality_mode != QUALITY_FAST
    use_cuda = device == DEVICE_CUDA
    if use_cuda:
        available, reason = gpu_runtime_status(app_root)
        if not available:
            raise RuntimeError(f"GPU 模式不可用：{reason}\n请改选 CPU，或使用包含 CUDA 运行库的完整绿色版。")

    if progress:
        progress(f"载入识别模型：{model_name}")
        device_text = "NVIDIA GPU / float16" if use_cuda else "CPU / int8"
        progress(f"{'高精度歌词约束' if accurate else '快速兼容'}模式（{device_text}，离线）")

    model_source = str(bundled_model) if (bundled_model / "model.bin").exists() else model_name
    try:
        model = WhisperModel(
            model_source,
            device=DEVICE_CUDA if use_cuda else DEVICE_CPU,
            compute_type="float16" if use_cuda else "int8",
        )
    except Exception as exc:
        if use_cuda:
            raise RuntimeError(f"GPU 模型载入失败：{exc}\n请检查显存、NVIDIA 驱动，或改选 CPU。") from exc
        raise
    options = dict(
        language="zh",
        task="transcribe",
        beam_size=8 if accurate else 5,
        patience=1.2 if accurate else 1.0,
        temperature=0.0,
        vad_filter=True,
        word_timestamps=accurate,
        condition_on_previous_text=False,
    )
    if accurate:
        options["no_speech_threshold"] = 0.5
        options["log_prob_threshold"] = -1.2

    segments, info = model.transcribe(audio_path, **options)

    anchors: list[tuple[float, float, str]] = []
    timed_chars: list[TimedChar] = []
    try:
        for segment in segments:
            text = segment.text.strip()
            if text:
                anchors.append((float(segment.start), float(segment.end), text))
                if accurate:
                    timed_chars.extend(_segment_chars(segment))
                if progress:
                    progress(f"{segment.start:7.2f}-{segment.end:7.2f} {text}")
    except Exception as exc:
        if use_cuda:
            raise RuntimeError(f"GPU 推理失败：{exc}\n请检查显存和驱动，或改选 CPU。") from exc
        raise
    return anchors, float(info.duration), timed_chars


def _allocate_by_count(lyrics: list[str], anchors: list[tuple[float, float, str]], duration: float) -> list[LyricLine]:
    if not lyrics:
        return []
    if not anchors:
        avg = max(1.5, duration / max(1, len(lyrics)))
        return [LyricLine(i * avg, min(duration, (i + 1) * avg), text) for i, text in enumerate(lyrics)]

    if len(anchors) == len(lyrics):
        return [LyricLine(start, max(end, start + 0.5), text) for text, (start, end, _heard) in zip(lyrics, anchors)]

    first_start = anchors[0][0]
    last_end = max(anchors[-1][1], first_start + len(lyrics) * 1.5)
    weights = [max(1.0, len(re.sub(r"\s+", "", line)) / 8.0) for line in lyrics]
    total_weight = sum(weights)
    span = max(1.0, last_end - first_start)

    lines: list[LyricLine] = []
    cursor = first_start
    for index, (text, weight) in enumerate(zip(lyrics, weights)):
        share = span * weight / total_weight
        end = cursor + share
        if index == len(lyrics) - 1:
            end = last_end
        lines.append(LyricLine(cursor, max(end, cursor + 0.5), text))
        cursor = end
    return lines


def _find_text_anchors(
    lyrics: list[str],
    anchors: list[tuple[float, float, str]],
) -> list[tuple[int, int, float]]:
    n = len(lyrics)
    m = len(anchors)
    if not n or not m:
        return []

    scores = [[_similarity(lyrics[i], anchors[j][2]) for j in range(m)] for i in range(n)]
    dp = [[0.0 for _ in range(m + 1)] for _ in range(n + 1)]
    choice = [["" for _ in range(m + 1)] for _ in range(n + 1)]

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            skip_lyric = dp[i - 1][j] - 0.015
            skip_anchor = dp[i][j - 1] - 0.015
            score = scores[i - 1][j - 1]
            match = dp[i - 1][j - 1] + score if score >= MIN_MATCH_SCORE else -1_000_000.0

            best = skip_lyric
            marker = "up"
            if skip_anchor > best:
                best = skip_anchor
                marker = "left"
            if match > best:
                best = match
                marker = "match"
            dp[i][j] = best
            choice[i][j] = marker

    matches: list[tuple[int, int, float]] = []
    i = n
    j = m
    while i > 0 and j > 0:
        marker = choice[i][j]
        if marker == "match":
            matches.append((i - 1, j - 1, scores[i - 1][j - 1]))
            i -= 1
            j -= 1
        elif marker == "left":
            j -= 1
        else:
            i -= 1
    matches.reverse()
    return matches


def _weighted_spans(texts: list[str], start: float, end: float) -> list[tuple[float, float]]:
    if not texts:
        return []
    if end <= start:
        avg = 1.8
        return [(start + i * avg, start + (i + 1) * avg) for i in range(len(texts))]

    weights = [max(1.0, len(_norm_text(text)) / 8.0) for text in texts]
    total = sum(weights)
    cursor = start
    spans: list[tuple[float, float]] = []
    used_weight = 0.0
    for index, weight in enumerate(weights):
        used_weight += weight
        next_cursor = start + (end - start) * used_weight / total
        if index == len(weights) - 1:
            next_cursor = end
        spans.append((cursor, max(next_cursor, cursor + 0.5)))
        cursor = next_cursor
    return spans


def _semi_global_char_map(lyric_text: str, heard: list[TimedChar]) -> tuple[list[int | None], int]:
    """Map known lyric characters to a monotonic substring of recognized characters."""
    n = len(lyric_text)
    m = len(heard)
    if not n or not m:
        return [None] * n, 0

    delete_score = -0.72
    insert_score = -0.52
    mismatch_score = -0.82
    exact_score = 2.4

    previous = [0.0] * (m + 1)  # A recognized prefix is free: music may have an intro.
    directions = [bytearray(m + 1) for _ in range(n + 1)]
    for j in range(1, m + 1):
        directions[0][j] = 2  # left

    for i, lyric_char in enumerate(lyric_text, start=1):
        current = [i * delete_score] + [0.0] * m
        directions[i][0] = 1  # up
        for j in range(1, m + 1):
            is_exact = lyric_char == heard[j - 1].text
            diagonal = previous[j - 1] + (exact_score if is_exact else mismatch_score)
            up = previous[j] + delete_score
            left = current[j - 1] + insert_score
            if diagonal >= up and diagonal >= left:
                current[j] = diagonal
                directions[i][j] = 3  # diagonal
            elif up >= left:
                current[j] = up
                directions[i][j] = 1
            else:
                current[j] = left
                directions[i][j] = 2
        previous = current

    j = max(range(m + 1), key=previous.__getitem__)  # A recognized suffix is free too.
    i = n
    mapping: list[int | None] = [None] * n
    exact_matches = 0
    while i > 0:
        direction = directions[i][j] if j >= 0 else 1
        if direction == 3 and j > 0:
            mapping[i - 1] = j - 1
            if lyric_text[i - 1] == heard[j - 1].text:
                exact_matches += 1
            i -= 1
            j -= 1
        elif direction == 2 and j > 0:
            j -= 1
        else:
            i -= 1
    return mapping, exact_matches


def _fill_missing_starts(
    lyrics: list[str],
    starts: list[float | None],
    duration: float,
    raw_ends: list[float | None] | None = None,
) -> list[float]:
    known = [index for index, value in enumerate(starts) if value is not None]
    if not known:
        avg = max(1.5, duration / max(1, len(lyrics)))
        return [index * avg for index in range(len(lyrics))]

    result = list(starts)
    first = known[0]
    if first > 0:
        first_start = float(result[first])
        prefix_start = max(0.0, first_start - 2.2 * first)
        spans = _weighted_spans(lyrics[:first], prefix_start, first_start)
        for index, (start, _end) in enumerate(spans):
            result[index] = start

    for left, right in zip(known, known[1:]):
        if right <= left + 1:
            continue
        left_start = float(result[left])
        right_start = float(result[right])
        estimated_after_left = left_start + max(0.55, len(_norm_text(lyrics[left])) * 0.16)
        if raw_ends and raw_ends[left] is not None:
            estimated_after_left = max(estimated_after_left, float(raw_ends[left]) + 0.08)
        available_start = min(right_start, estimated_after_left)
        spans = _weighted_spans(lyrics[left + 1 : right], available_start, right_start)
        for index, (start, _end) in enumerate(spans, start=left + 1):
            result[index] = start

    last = known[-1]
    if last < len(lyrics) - 1:
        last_start = float(result[last])
        tail_end = max(duration, last_start + 2.2 * (len(lyrics) - last))
        spans = _weighted_spans(lyrics[last + 1 :], last_start + 1.2, tail_end)
        for index, (start, _end) in enumerate(spans, start=last + 1):
            result[index] = start

    filled = [float(value) for value in result]
    for index in range(1, len(filled)):
        filled[index] = max(filled[index], filled[index - 1] + 0.08)
    return filled


def _allocate_by_character_alignment(
    lyrics: list[str],
    heard: list[TimedChar],
    duration: float,
    progress: Callable[[str], None] | None,
) -> list[LyricLine] | None:
    normalized_lines = [_norm_text(line) for line in lyrics]
    lyric_text = "".join(normalized_lines)
    if not lyric_text or len(heard) < 2:
        return None

    mapping, exact_matches = _semi_global_char_map(lyric_text, heard)
    exact_ratio = exact_matches / len(lyric_text)
    if progress:
        progress(f"歌词字符对齐：{exact_matches} / {len(lyric_text)} 字完全匹配（{exact_ratio:.0%}）")
    if exact_matches < max(3, round(len(lyric_text) * 0.08)):
        if progress:
            progress("字符匹配证据不足，改用分段文本锚点。")
        return None

    char_durations = [item.end - item.start for item in heard if 0.015 <= item.end - item.start <= 1.5]
    typical_char_duration = statistics.median(char_durations) if char_durations else 0.28
    starts: list[float | None] = []
    raw_ends: list[float | None] = []
    confidences: list[float] = []
    offset = 0

    for normalized in normalized_lines:
        length = len(normalized)
        line_map = mapping[offset : offset + length]
        mapped = [(local, heard_index) for local, heard_index in enumerate(line_map) if heard_index is not None]
        exact_pairs = [(local, heard_index) for local, heard_index in mapped if normalized[local] == heard[heard_index].text]
        exact = len(exact_pairs)
        confidences.append(exact / max(1, length))
        minimum_evidence = 1 if length <= 3 else 2
        if len(exact_pairs) >= minimum_evidence:
            first_local, first_heard = exact_pairs[0]
            last_local, last_heard = exact_pairs[-1]
            evidence_span = heard[last_heard].end - heard[first_heard].start
            if evidence_span > max(18.0, length * 1.8):
                starts.append(None)
                raw_ends.append(None)
                offset += length
                continue
            starts.append(max(0.0, heard[first_heard].start - first_local * typical_char_duration))
            raw_ends.append(heard[last_heard].end + (length - last_local - 1) * typical_char_duration)
        else:
            starts.append(None)
            raw_ends.append(None)
        offset += length

    filled_starts = _fill_missing_starts(lyrics, starts, duration, raw_ends)
    lines: list[LyricLine] = []
    for index, text in enumerate(lyrics):
        start = filled_starts[index]
        raw_end = raw_ends[index]
        if index + 1 < len(lyrics):
            next_start = filled_starts[index + 1]
            natural_end = float(raw_end) if raw_end is not None else next_start - 0.08
            end = min(next_start - 0.04, max(start + 0.35, natural_end))
            if end <= start:
                end = max(start + 0.08, next_start - 0.02)
        else:
            natural_end = float(raw_end) if raw_end is not None else start + 2.0
            end = max(start + 0.5, natural_end)
            if duration > start:
                end = min(end, duration)
        lines.append(LyricLine(start, end, text))

    low_confidence = [str(index + 1) for index, value in enumerate(confidences) if value < 0.2]
    if progress:
        if low_confidence:
            preview = "、".join(low_confidence[:12])
            suffix = "…" if len(low_confidence) > 12 else ""
            progress(f"建议重点抽查低置信行：第 {preview}{suffix} 行")
        else:
            progress("全部歌词行都取得了稳定的文本时间证据。")
    return lines


def _allocate_by_text_match(
    lyrics: list[str],
    anchors: list[tuple[float, float, str]],
    duration: float,
    progress: Callable[[str], None] | None,
) -> list[LyricLine] | None:
    matches = _find_text_anchors(lyrics, anchors)
    if progress:
        progress(f"文本锚点匹配：{len(matches)} / {len(lyrics)} 行")

    min_required = 2 if len(lyrics) >= 6 else 1
    if len(matches) < min_required:
        if progress:
            progress("可靠文本锚点太少，回退到按总时长分配。")
        return None

    matched_by_line = {line_index: anchors[anchor_index] for line_index, anchor_index, _score in matches}
    matched_indices = [line_index for line_index, _anchor_index, _score in matches]
    lines: list[LyricLine | None] = [None for _ in lyrics]

    for line_index, anchor in matched_by_line.items():
        start, end, _heard = anchor
        lines[line_index] = LyricLine(start, max(end, start + 0.5), lyrics[line_index])

    first = matched_indices[0]
    if first > 0:
        first_start = lines[first].start if lines[first] else anchors[0][0]
        avg = min(3.0, max(1.2, first_start / max(1, first)))
        start = max(0.0, first_start - avg * first)
        for offset, (span_start, span_end) in enumerate(_weighted_spans(lyrics[:first], start, first_start)):
            lines[offset] = LyricLine(span_start, span_end, lyrics[offset])

    for left, right in zip(matched_indices, matched_indices[1:]):
        left_line = lines[left]
        right_line = lines[right]
        if left_line is None or right_line is None or right <= left + 1:
            continue
        start = left_line.end
        end = right_line.start
        for offset, (span_start, span_end) in enumerate(_weighted_spans(lyrics[left + 1 : right], start, end), start=left + 1):
            lines[offset] = LyricLine(span_start, span_end, lyrics[offset])

    last = matched_indices[-1]
    if last < len(lyrics) - 1:
        last_end = lines[last].end if lines[last] else anchors[-1][1]
        tail_end = max(duration, last_end + (len(lyrics) - last - 1) * 1.5)
        for offset, (span_start, span_end) in enumerate(_weighted_spans(lyrics[last + 1 :], last_end, tail_end), start=last + 1):
            lines[offset] = LyricLine(span_start, span_end, lyrics[offset])

    filled = [line for line in lines if line is not None]
    if len(filled) != len(lyrics):
        return None

    for index in range(len(filled) - 1):
        current = filled[index]
        following = filled[index + 1]
        if current.end > following.start - 0.05:
            current.end = max(current.start + 0.5, following.start - 0.05)
    return filled


def generate_srt_from_txt(
    audio_path: str | Path,
    txt_path: str | Path,
    srt_path: str | Path,
    model_name: str = MODEL_ENHANCED,
    progress: Callable[[str], None] | None = None,
    quality_mode: str = QUALITY_ACCURATE,
    device: str = DEVICE_CPU,
) -> list[LyricLine]:
    lyrics = read_txt_lyrics(txt_path)
    if progress:
        progress(f"载入 TXT 歌词：{len(lyrics)} 行")
    if not lyrics:
        raise ValueError("TXT 中没有可用歌词行。")

    anchors, duration, timed_chars = _recognize_segments(
        str(audio_path), model_name, quality_mode, device, progress
    )
    if progress:
        progress(f"识别到时间片段：{len(anchors)} 段，音频约 {duration:.2f}s")

    lines = None
    if quality_mode != QUALITY_FAST:
        lines = _allocate_by_character_alignment(lyrics, timed_chars, duration, progress)
    if lines is None:
        lines = _allocate_by_text_match(lyrics, anchors, duration, progress)
    if lines is None:
        lines = _allocate_by_count(lyrics, anchors, duration)

    save_srt(srt_path, lines)
    if progress:
        progress(f"已生成 SRT 初稿：{srt_path}")
    return lines
