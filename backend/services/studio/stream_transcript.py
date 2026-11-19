"""Transcribe only the chosen stream windows and keep every time on the source timeline.

An 8 h stream is never run through ASR whole: each window is cut to 16 kHz mono audio, recognised
on its own, and its cues and word times are shifted by the window start. The merged result is the
project's `input.srt` (+ word-timing sidecar), so captions render like any other import. Windows
that fail are skipped; with no transcript the clips simply render without captions.
"""
from __future__ import annotations

import copy
import logging
import re
import subprocess
import tempfile
from pathlib import Path

from backend.utils import word_timing

logger = logging.getLogger(__name__)

SNAP_MAX = 8.0          # a window edge moves to a sentence edge only if that edge is this close
CUT_TIMEOUT = 300
_TIME = re.compile(r'(\d+):(\d\d):(\d\d)[,.](\d{3})')


def _stamp(seconds: float) -> str:
    ms = round(seconds * 1000)
    return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'


def _seconds(text: str) -> float:
    h, m, s, ms = map(int, _TIME.search(text).groups())
    return h * 3600 + m * 60 + s + ms / 1000


def write_srt(path: Path, cues: list[dict]) -> None:
    rows = sorted(cues, key=lambda c: c['start'])
    Path(path).write_text(''.join(f"{i}\n{_stamp(c['start'])} --> {_stamp(c['end'])}\n{c['text']}\n\n" for i, c in enumerate(rows, 1)), encoding='utf-8')


def read_srt(path: Path) -> list[dict]:
    try:
        raw = Path(path).read_text(encoding='utf-8-sig')
    except OSError:
        return []
    cues = []
    for block in re.split(r'\n\s*\n', raw.replace('\r\n', '\n')):
        lines = [line for line in block.strip().split('\n') if line.strip()]
        timing = next((i for i, line in enumerate(lines) if '-->' in line), None)
        if timing is None:
            continue
        try:
            left, right = lines[timing].split('-->')
            start, end = _seconds(left), _seconds(right)
        except (AttributeError, ValueError):
            continue
        text = ' '.join(lines[timing + 1:]).strip()
        if text and end > start:
            cues.append({'start': start, 'end': end, 'text': text})
    return sorted(cues, key=lambda c: c['start'])


def shift_segments(segments: list[dict], offset: float) -> list[dict]:
    out = copy.deepcopy(segments)
    for segment in out:
        segment['start'] = round(segment['start'] + offset, 3)
        segment['end'] = round(segment['end'] + offset, 3)
        for word in segment.get('words') or []:
            word['start'] = round(word['start'] + offset, 3)
            word['end'] = round(word['end'] + offset, 3)
    return out


def snap(start: float, end: float, cues: list[dict], duration: float, max_shift: float = SNAP_MAX) -> tuple[float, float]:
    """Move an edge that lands inside a sentence to that sentence's edge, when it is close."""
    for cue in cues:
        if cue['start'] < start < cue['end'] and start - cue['start'] <= max_shift:
            start = cue['start']
            break
    for cue in cues:
        if cue['start'] < end < cue['end'] and cue['end'] - end <= max_shift:
            end = cue['end']
            break
    return max(0.0, start), min(duration, end)


def _cut_audio(video: Path, window, out: Path) -> None:
    from backend.utils.ffmpeg_utils import get_ffmpeg_path
    cmd = [get_ffmpeg_path(), '-hide_banner', '-loglevel', 'error', '-ss', f'{window.start:.3f}', '-t', f'{window.end - window.start:.3f}',
           '-i', str(video), '-vn', '-ac', '1', '-ar', '16000', '-y', str(out)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=CUT_TIMEOUT)


def _recognise(audio: Path, srt: Path) -> None:
    from backend.utils.speech_recognizer import SpeechRecognizer
    SpeechRecognizer().generate_subtitle(audio, srt)


def transcribe_windows(video: Path, windows, folder: Path, *, cut=_cut_audio, recognise=_recognise) -> list[dict]:
    """Cues (source seconds) for the windows. An existing `input.srt` (platform or user) is used as is."""
    folder = Path(folder)
    target = folder / 'input.srt'
    if target.exists():
        return read_srt(target)
    cues: list[dict] = []
    segments: list[dict] = []
    words_everywhere = True
    with tempfile.TemporaryDirectory(prefix='ac-stream-asr-') as tmp:
        for index, window in enumerate(sorted(windows, key=lambda w: w.start)):
            audio, out = Path(tmp) / f'w{index}.wav', Path(tmp) / f'w{index}.srt'
            try:
                cut(video, window, audio)
                recognise(audio, out)
            except Exception as error:  # noqa: BLE001 - one bad window must not lose the others
                logger.warning('Stream window %s transcription skipped: %s', index, type(error).__name__)
                continue
            local = read_srt(out)
            if not local:
                continue
            cues += [{**cue, 'start': round(cue['start'] + window.start, 3), 'end': round(cue['end'] + window.start, 3)} for cue in local]
            words = word_timing.load_word_timing(out)
            if words:
                segments += shift_segments(words, window.start)
            else:
                words_everywhere = False
    if not cues:
        return []
    write_srt(target, cues)
    if words_everywhere and segments:
        try:
            word_timing.write_word_timing(target, segments)
        except ValueError:
            logger.warning('Stream word timing dropped: invalid after shifting')
    return read_srt(target)
