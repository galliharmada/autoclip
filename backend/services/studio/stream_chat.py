"""Stream chat as a moment signal.

Canonical file: `raw/chat.jsonl`, one `{"t": seconds_from_video_start, "text": "..."}` per line.
YouTube replay chat comes from yt-dlp's `live_chat` subtitle; Twitch from `chat-downloader`,
installed on demand (yt-dlp has no Twitch chat). Every failure degrades to "no chat": the
import must never fail because chat is missing.
"""
from __future__ import annotations

import json
import logging
import math
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

CHAT_FILE = 'chat.jsonl'
TWITCH_PACKAGE = 'chat-downloader>=0.2.8'
INSTALL_TIMEOUT = 300
NONE = {'source': 'none', 'messages': 0}


def _valid(t, text) -> bool:
    return (isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t) and t >= 0
            and isinstance(text, str) and bool(text.strip()))


def load_chat(path: Path) -> list[tuple[float, str]]:
    """Messages sorted by time. Missing file or bad lines give fewer messages, never an error."""
    rows: list[tuple[float, str]] = []
    try:
        lines = Path(path).read_text(encoding='utf-8').splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            item = json.loads(line)
            t, text = item['t'], item['text']
        except (ValueError, KeyError, TypeError):
            continue
        if _valid(t, text):
            rows.append((float(t), text.strip()))
    rows.sort(key=lambda row: row[0])
    return rows


def load_any(path: Path) -> list[tuple[float, str]]:
    """A user-supplied chat file: our canonical JSONL, or yt-dlp's `live_chat` JSON of a finished stream."""
    return load_chat(path) or parse_youtube_live_chat(path)


def write_chat(path: Path, messages: list[tuple[float, str]]) -> None:
    rows = sorted(messages, key=lambda row: row[0])
    Path(path).write_text(''.join(json.dumps({'t': t, 'text': text}, ensure_ascii=False) + '\n' for t, text in rows), encoding='utf-8')


def _runs_text(runs) -> str:
    parts = []
    for run in runs or []:
        if 'text' in run:
            parts.append(str(run['text']))
        elif isinstance(run.get('emoji'), dict):
            emoji = run['emoji']
            ident = str(emoji.get('emojiId') or '')
            # A unicode emoji has the character as its id; a channel emote has an opaque id.
            parts.append(ident if ident and '/' not in ident else (emoji.get('shortcuts') or [''])[0])
    return ''.join(parts).strip()


_RENDERERS = ('liveChatTextMessageRenderer', 'liveChatPaidMessageRenderer')


def parse_youtube_live_chat(path: Path, recording_start: float | None = None) -> list[tuple[float, str]]:
    """Messages from yt-dlp's `live_chat` file, as seconds from the video start.

    A finished stream carries `videoOffsetTimeMsec`. A capture of a stream that is still running has
    0 on every line; its only clock is each message's `timestampUsec`, so it needs `recording_start`
    (epoch seconds of the video's first frame). Without it a live capture yields nothing rather than
    a pile of messages at 0.
    """
    entries = []
    try:
        lines = Path(path).read_text(encoding='utf-8').splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            action = json.loads(line)['replayChatItemAction']
            offset = float(action.get('videoOffsetTimeMsec') or 0) / 1000
        except (ValueError, KeyError, TypeError):
            continue
        for sub in action.get('actions') or []:
            item = ((sub.get('addChatItemAction') or {}).get('item')) or {}
            for name in _RENDERERS:
                if name in item:
                    renderer = item[name]
                    entries.append((offset, renderer.get('timestampUsec'), _runs_text((renderer.get('message') or {}).get('runs'))))
    replay = any(offset > 0 for offset, _, _ in entries)
    rows: list[tuple[float, str]] = []
    for offset, stamp, text in entries:
        if replay:
            t = offset
        elif recording_start is not None and stamp is not None:
            try:
                t = float(stamp) / 1e6 - recording_start
            except (TypeError, ValueError):
                continue
        else:
            continue
        if _valid(t, text):
            rows.append((t, text))
    rows.sort(key=lambda row: row[0])
    return rows


def parse_chat_downloader(items) -> list[tuple[float, str]]:
    rows = []
    for item in items or []:
        t, text = item.get('time_in_seconds'), item.get('message')
        if _valid(t, text):
            rows.append((float(t), text.strip()))
    rows.sort(key=lambda row: row[0])
    return rows


def _download(url: str, opts: dict) -> None:
    from backend.utils.download_recovery import download_with_recovery
    download_with_recovery(url, opts)


def _host(url: str) -> str:
    return (urlparse(url).hostname or '').lower()


def _is_youtube(url: str) -> bool:
    host = _host(url)
    return host in ('youtu.be', 'youtube.com') or host.endswith('.youtube.com')


def _is_twitch(url: str) -> bool:
    host = _host(url)
    return host == 'twitch.tv' or host.endswith('.twitch.tv')


def _runtime_dir() -> Path:
    from backend.services.whisper_runtime import _data_dir
    path = _data_dir() / 'chat-runtime'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _import_chat_downloader():
    runtime = str(_runtime_dir())
    if runtime not in sys.path:
        sys.path.append(runtime)  # after the other runtimes: chat-downloader needs no numpy/cv2
    try:
        from chat_downloader import ChatDownloader
        return ChatDownloader
    except ImportError:
        return None


def _install_chat_downloader() -> bool:
    from backend.services import pip_install
    for _label, index in pip_install.index_plan(None):
        try:
            done = subprocess.run(pip_install.command([TWITCH_PACKAGE], str(_runtime_dir()), index), capture_output=True, timeout=INSTALL_TIMEOUT, check=False)
        except (OSError, subprocess.SubprocessError) as error:
            logger.warning('Twitch chat component install failed: %s', type(error).__name__)
            continue
        if done.returncode == 0:
            return True
    return False


def _twitch_items(url: str):
    """Chat items of a Twitch VOD, or None when the component or the chat is unavailable."""
    downloader = _import_chat_downloader()
    if downloader is None and _install_chat_downloader():
        downloader = _import_chat_downloader()
    if downloader is None:
        return None
    return list(downloader().get_chat(url))


def fetch_chat(url: str, info: dict, options: dict, folder: Path) -> dict:
    """Write `folder/chat.jsonl` when the source has chat. Returns {'source', 'messages'}; never raises."""
    folder = Path(folder)
    try:
        if _is_youtube(url):
            if 'live_chat' not in (info.get('subtitles') or {}):
                return dict(NONE)
            _download(url, {**{k: v for k, v in options.items() if k not in ('format', 'merge_output_format', 'progress_hooks', 'postprocessor_hooks')},
                            'skip_download': True, 'writesubtitles': True, 'subtitleslangs': ['live_chat'],
                            'outtmpl': str(folder / 'chatsrc.%(ext)s')})
            raw = folder / 'chatsrc.live_chat.json'
            messages, source = parse_youtube_live_chat(raw), 'youtube'
            raw.unlink(missing_ok=True)
        elif _is_twitch(url):
            items = _twitch_items(url)
            messages, source = parse_chat_downloader(items), 'twitch'
        else:
            return dict(NONE)
        if not messages:
            return dict(NONE)
        write_chat(folder / CHAT_FILE, messages)
        return {'source': source, 'messages': len(messages)}
    except Exception as error:  # noqa: BLE001 - chat is optional; a failure here must not fail the import
        logger.warning('Chat download skipped: %s', type(error).__name__)
        return dict(NONE)
