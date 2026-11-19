"""Moment signals for long streams: chat speed, emote/phrase hits, loudness spikes.

Every signal is a per-second series turned into a spike score (how far above its own recent
baseline it is). `combine` merges the signals that exist, `find_windows` picks the best
non-overlapping windows. A missing signal (no chat, ffmpeg failure) is skipped and the rest
carry on; with no signal at all there are simply no windows.
"""
from __future__ import annotations

import logging
import math
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from backend.services.studio import stream_chat

logger = logging.getLogger(__name__)

CAP = 6.0                                   # one signal never counts for more than this many sigmas
WEIGHTS = {'chat_rate': .45, 'chat_hits': .35, 'audio': .20}
SILENCE = -70.0                             # LUFS floor for silent or unreadable seconds
LOUDNESS_TIMEOUT = 3600.0
REASON_Z = 1.5                              # a signal "fired" for a window when it reached this near the peak

LEXICON_WORDS = frozenset({
    'kekw', 'lul', 'lulw', 'omegalul', 'lmao', 'lmfao', 'pog', 'poggers', 'pogchamp', 'wtf', 'omg', 'w', 'sheesh',
    'insane', 'crazy', 'holy', '💀', '😂', '🤣', '🔥', '😭', '🤯',
})
LEXICON_PHRASES = ('no way', 'clip it', 'clip that', "let's go", 'oh my god', 'bro what')
_TOKEN = re.compile(r"[\w']+|[^\w\s]")


@dataclass
class Window:
    start: float
    end: float
    peak: float
    score: float
    reasons: list[str] = field(default_factory=list)


def _prefix(values):
    out = [0.0]
    for value in values:
        out.append(out[-1] + value)
    return out


def spike(series: list[float], *, smooth: int = 5, base: int = 300, floor: float = 1.0, agg: str = 'sum') -> list[float]:
    """Sigmas above the rolling baseline (never negative). `floor` stops a quiet baseline from exploding."""
    n = len(series)
    if n == 0:
        return []
    half = smooth // 2
    pre = _prefix(series)
    smoothed = []
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        total = pre[hi] - pre[lo]
        # Edge windows are shorter; scale so a flat series stays flat right up to the ends.
        smoothed.append(total / (hi - lo) if agg == 'mean' else total * (2 * half + 1) / (hi - lo))
    p1, p2 = _prefix(smoothed), _prefix([v * v for v in smoothed])
    out = []
    window = base // 2
    for i in range(n):
        lo, hi = max(0, i - window), min(n, i + window + 1)
        mean = (p1[hi] - p1[lo]) / (hi - lo)
        var = max((p2[hi] - p2[lo]) / (hi - lo) - mean * mean, 0.0)
        out.append(max(0.0, (smoothed[i] - mean) / max(math.sqrt(var), floor)))
    return out


def _bins(messages, duration: float, keep=lambda text: True) -> list[float]:
    counts = [0.0] * max(0, math.ceil(duration))
    for t, text in messages:
        index = int(t)
        if 0 <= index < len(counts) and keep(text):
            counts[index] += 1
    return counts


def is_hit(text: str) -> bool:
    low = text.lower()
    return any(token in LEXICON_WORDS for token in _TOKEN.findall(low)) or any(phrase in low for phrase in LEXICON_PHRASES)


def chat_hit_counts(messages, duration: float) -> list[float]:
    return _bins(messages, duration, is_hit)


def chat_rate(messages, duration: float) -> list[float]:
    return spike(_bins(messages, duration))


def chat_hits(messages, duration: float) -> list[float]:
    return spike(chat_hit_counts(messages, duration))


def combine(signals: dict[str, list[float]], weights: dict[str, float] = WEIGHTS) -> list[float]:
    present = {name: series for name, series in signals.items() if series}
    if not present:
        return []
    total = sum(weights[name] for name in present)
    length = max(len(series) for series in present.values())
    out = [0.0] * length
    for name, series in present.items():
        share = weights[name] / total
        for i, value in enumerate(series):
            out[i] += share * min(value, CAP)
    return out


def _reasons(signals, peak: int) -> list[str]:
    lo, hi = max(0, peak - 5), peak + 6
    return [name for name, series in (signals or {}).items() if series and max(series[lo:hi] or [0.0]) >= REASON_Z]


def find_windows(score: list[float], signals: dict[str, list[float]] | None = None, *, top_n: int = 8,
                 pre: float = 20, post: float = 10, max_len: float = 90, min_score: float = 1.5) -> list[Window]:
    """Best non-overlapping windows, highest score first. A window spans the peak's whole hot stretch."""
    n = len(score)
    accepted: list[Window] = []
    for peak in sorted((i for i in range(n) if score[i] >= min_score), key=lambda i: score[i], reverse=True):
        if len(accepted) >= top_n:
            break
        if any(w.start <= peak < w.end for w in accepted):
            continue  # already inside a chosen window
        threshold = max(min_score, score[peak] * .5)
        left = right = peak
        while left > 0 and score[left - 1] >= threshold and peak - left < max_len:
            left -= 1
        while right < n - 1 and score[right + 1] >= threshold and right - peak < max_len:
            right += 1
        start = max(0.0, min(peak - pre, left - 5.0))
        end = min(float(n), max(peak + post, right + 6.0))
        if end - start > max_len:
            start = max(0.0, peak - pre)
            end = min(float(n), start + max_len)
        reasons = _reasons(signals, peak)
        touched = [w for w in accepted if start < w.end and end > w.start]
        if touched:
            # A second hot spot next to an accepted window extends it, as long as it stays one clip.
            lo_t, hi_t = min([start] + [w.start for w in touched]), max([end] + [w.end for w in touched])
            if hi_t - lo_t > max_len:
                continue
            best = max(touched, key=lambda w: w.score)
            merged = Window(start=lo_t, end=hi_t, peak=best.peak if best.score >= score[peak] else float(peak),
                            score=max(best.score, score[peak]), reasons=sorted(set(reasons).union(*[w.reasons for w in touched])))
            accepted = [w for w in accepted if w not in touched] + [merged]
            continue
        accepted.append(Window(start=start, end=end, peak=float(peak), score=score[peak], reasons=reasons))
    accepted.sort(key=lambda w: w.score, reverse=True)
    return accepted


_EBUR_LINE = re.compile(r'\bt:\s*([0-9.]+)\s.*?\bM:\s*(-?[0-9.]+|-inf)')


def parse_ebur128(lines, duration: float) -> list[float]:
    """Per-second maximum momentary loudness (LUFS) from ffmpeg ebur128 verbose lines."""
    seconds: dict[int, float] = {}
    for line in lines:
        match = _EBUR_LINE.search(line)
        if not match:
            continue
        value = SILENCE if match.group(2) == '-inf' else max(float(match.group(2)), SILENCE)
        index = int(float(match.group(1)))
        seconds[index] = max(seconds.get(index, SILENCE), value)
    if not seconds:
        return []
    return [seconds.get(i, SILENCE) for i in range(max(math.ceil(duration), max(seconds) + 1))]


def loudness_series(video: Path, duration: float, timeout: float = LOUDNESS_TIMEOUT) -> list[float]:
    """Streamed through ffmpeg so an 8 h file never sits in memory. Empty on any failure."""
    from backend.utils.ffmpeg_utils import get_ffmpeg_path
    cmd = [get_ffmpeg_path(), '-hide_banner', '-nostats', '-i', str(video), '-vn', '-af', 'aresample=16000,ebur128=framelog=info', '-f', 'null', '-']
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors='replace')
    except OSError as error:
        logger.warning('Loudness scan could not start: %s', type(error).__name__)
        return []
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    try:
        series = parse_ebur128(proc.stderr, duration)
        proc.wait()
    finally:
        timer.cancel()
        proc.stderr.close()
    if proc.returncode != 0 and not series:
        logger.warning('Loudness scan failed (exit %s)', proc.returncode)
        return []
    return series


def score_source(video: Path, chat_path: Path, duration: float, *, top_n: int = 8, loudness=loudness_series) -> list[Window]:
    """Windows worth clipping from chat and sound. Either signal alone is enough."""
    messages = stream_chat.load_chat(chat_path)
    signals: dict[str, list[float]] = {}
    if messages:
        signals['chat_rate'] = chat_rate(messages, duration)
        signals['chat_hits'] = chat_hits(messages, duration)
    try:
        audio = loudness(video, duration)
    except Exception as error:  # noqa: BLE001 - audio is one signal of several
        logger.warning('Loudness signal skipped: %s', type(error).__name__)
        audio = []
    if audio:
        signals['audio'] = spike(audio, smooth=3, floor=2.0, agg='mean')
    return find_windows(combine(signals), signals, top_n=top_n)
