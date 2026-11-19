"""Stream route: turn the best chat/sound windows of a long source into drafts.

No model call is needed to find the moments. The drafts then travel the same derive, framing,
packaging and render path as every other import.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from backend.core.failure_codes import UserFacingError
from backend.services.studio import (
    facecam,
    stream_chat,
    stream_signals,
    stream_transcript,
)
from backend.services.studio.models import Draft, Scene

logger = logging.getLogger(__name__)

REASON_TEXT = {'chat_rate': '聊天刷屏', 'chat_hits': '观众刷表情/关键词', 'audio': '音量突增'}


def _duration(video: Path) -> float:
    from backend.services.publish_export import _probe
    return float(_probe(video).get('duration') or 0)


def _clock(seconds: float) -> str:
    total = int(seconds)
    return f'{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}'


def drafts(plan: dict, video: Path, *, top_n: int = 8) -> list[dict]:
    from backend.services.studio.jobs import SCORE_KEY
    prefs = plan.get('preferences') or {}
    overrides = plan.get('overrides') or {}
    top_n = int(overrides.get('stream_top') or top_n)
    duration = _duration(video)
    windows = stream_signals.score_source(video, Path(video).parent / stream_chat.CHAT_FILE, duration, top_n=top_n)
    if not windows:
        raise UserFacingError('没有找到明显的直播高光：聊天记录和音量都没有突出的片段。请确认已下载聊天，或换一段素材')
    try:
        cues = stream_transcript.transcribe_windows(video, windows, Path(video).parent)
    except Exception as error:  # noqa: BLE001 - captions are optional; the clips still render
        logger.warning('Stream transcription skipped: %s', type(error).__name__)
        cues = []
    rect = None
    if prefs.get('aspect') == 'portrait' and overrides.get('stream_layout', 'auto') != 'crop':
        try:
            rect = facecam.detect_facecam(video, duration)
        except Exception as error:  # noqa: BLE001 - no facecam means a plain vertical crop
            logger.warning('Facecam detection skipped: %s', type(error).__name__)
    layout = 'stack' if rect else ('crop' if prefs.get('aspect') == 'portrait' else 'fit')
    out = []
    for window in windows:
        start, end = stream_transcript.snap(window.start, window.end, cues, duration)
        why = '、'.join(REASON_TEXT[r] for r in window.reasons if r in REASON_TEXT) or '综合热度'
        scene = Scene(id=uuid.uuid4().hex, label=f'直播高光 {_clock(window.peak)}'[:120], start=start,
                      end=end, evidence=f'依据：{why}（热度 {window.score:.1f}）')
        out.append({**Draft(
            id=uuid.uuid4().hex, title=scene.label, scenes=[scene], origin='auto-stream',
            language=prefs.get('language', 'source'), aspect=prefs.get('aspect', 'original'),
            layout=layout, facecam=rect, subtitles=True,
        ).model_dump(), SCORE_KEY: window.score})
    return out
