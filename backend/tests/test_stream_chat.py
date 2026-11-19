"""Stream chat: canonical chat.jsonl, YouTube live_chat parsing, best-effort fetch."""
import json
from pathlib import Path

import pytest

from backend.services.studio import stream_chat


def _write(path: Path, lines):
    path.write_text('\n'.join(l if isinstance(l, str) else json.dumps(l) for l in lines) + '\n', encoding='utf-8')


def test_load_chat_sorts_and_skips_bad_lines(tmp_path):
    f = tmp_path / 'chat.jsonl'
    _write(f, [{'t': 5, 'text': 'b'}, 'not json', {'t': 1.5, 'text': 'a'}, {'t': -1, 'text': 'neg'},
               {'t': float('inf'), 'text': 'inf'}, {'t': 2, 'text': ''}, {'t': 'x', 'text': 'str'}, {'text': 'no t'}])
    assert stream_chat.load_chat(f) == [(1.5, 'a'), (5.0, 'b')]


def test_load_chat_missing_or_empty(tmp_path):
    assert stream_chat.load_chat(tmp_path / 'none.jsonl') == []
    (tmp_path / 'e.jsonl').write_text('', encoding='utf-8')
    assert stream_chat.load_chat(tmp_path / 'e.jsonl') == []


def test_write_then_load_roundtrip(tmp_path):
    f = tmp_path / 'chat.jsonl'
    stream_chat.write_chat(f, [(2.0, 'KEKW'), (1.0, 'hi')])
    assert stream_chat.load_chat(f) == [(1.0, 'hi'), (2.0, 'KEKW')]


def _yt_line(offset_ms, runs, renderer='liveChatTextMessageRenderer'):
    action = {'addChatItemAction': {'item': {renderer: {'message': {'runs': runs}}}}}
    return {'replayChatItemAction': {'actions': [action], 'videoOffsetTimeMsec': str(offset_ms)}}


def test_parse_youtube_live_chat(tmp_path):
    f = tmp_path / 'x.live_chat.json'
    _write(f, [
        _yt_line(12500, [{'text': 'no way '}, {'emoji': {'emojiId': 'UCabc/xyz', 'shortcuts': [':kekw:']}}]),
        _yt_line(3000, [{'emoji': {'emojiId': '💀', 'shortcuts': [':skull:']}}]),
        _yt_line(-4000, [{'text': 'pre-roll'}]),
        _yt_line(9000, [{'text': 'sc'}], renderer='liveChatPaidMessageRenderer'),
        _yt_line(9500, [{'text': 'ignored'}], renderer='liveChatMembershipItemRenderer'),
        '{broken',
        {'replayChatItemAction': {'actions': [], 'videoOffsetTimeMsec': '1'}},
    ])
    assert stream_chat.parse_youtube_live_chat(f) == [(3.0, '💀'), (9.0, 'sc'), (12.5, 'no way :kekw:')]


def test_parse_chat_downloader_items():
    items = [{'time_in_seconds': 4.0, 'message': 'LUL'}, {'time_in_seconds': -2, 'message': 'x'},
             {'time_in_seconds': 1, 'message': None}, {'message': 'no time'}]
    assert stream_chat.parse_chat_downloader(items) == [(4.0, 'LUL')]


def test_fetch_chat_youtube_writes_canonical_file(tmp_path, monkeypatch):
    def fake_download(url, opts):
        _write(tmp_path / 'chatsrc.live_chat.json', [_yt_line(1000, [{'text': 'hello'}])])
    monkeypatch.setattr(stream_chat, '_download', fake_download)
    info = {'subtitles': {'live_chat': [{'ext': 'json'}]}}
    result = stream_chat.fetch_chat('https://www.youtube.com/watch?v=abc', info, {}, tmp_path)
    assert result == {'source': 'youtube', 'messages': 1}
    assert stream_chat.load_chat(tmp_path / 'chat.jsonl') == [(1.0, 'hello')]
    assert not (tmp_path / 'chatsrc.live_chat.json').exists()


def test_fetch_chat_youtube_without_live_chat_is_none(tmp_path):
    assert stream_chat.fetch_chat('https://youtu.be/abc', {'subtitles': {}}, {}, tmp_path) == {'source': 'none', 'messages': 0}


def test_fetch_chat_failure_never_raises(tmp_path, monkeypatch):
    def boom(url, opts):
        raise RuntimeError('network')
    monkeypatch.setattr(stream_chat, '_download', boom)
    info = {'subtitles': {'live_chat': [{}]}}
    assert stream_chat.fetch_chat('https://youtu.be/abc', info, {}, tmp_path)['source'] == 'none'
    assert not (tmp_path / 'chat.jsonl').exists()


def test_fetch_chat_twitch_uses_chat_downloader(tmp_path, monkeypatch):
    monkeypatch.setattr(stream_chat, '_twitch_items', lambda url: [{'time_in_seconds': 7, 'message': 'W'}])
    result = stream_chat.fetch_chat('https://www.twitch.tv/videos/123', {}, {}, tmp_path)
    assert result == {'source': 'twitch', 'messages': 1}


def test_fetch_chat_twitch_unavailable_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(stream_chat, '_twitch_items', lambda url: None)
    assert stream_chat.fetch_chat('https://www.twitch.tv/videos/123', {}, {}, tmp_path)['source'] == 'none'


@pytest.mark.parametrize('url', ['https://example.com/v', 'https://notyoutube.com/x', 'file:///a.mp4'])
def test_fetch_chat_other_hosts_none(tmp_path, url):
    assert stream_chat.fetch_chat(url, {}, {}, tmp_path)['source'] == 'none'


def _live_line(ts_usec, text, offset='0'):
    renderer = {'message': {'runs': [{'text': text}]}, 'timestampUsec': str(ts_usec)}
    action = {'addChatItemAction': {'item': {'liveChatTextMessageRenderer': renderer}}}
    return {'replayChatItemAction': {'actions': [action], 'videoOffsetTimeMsec': offset}}


def test_live_capture_uses_message_timestamps_relative_to_recording_start(tmp_path):
    """A running stream has no video offsets (all 0); only timestampUsec is meaningful."""
    f = tmp_path / 'live.json'
    base = 1_700_000_000.0
    _write(f, [_live_line((base + 12.5) * 1e6, 'a'), _live_line((base + 3) * 1e6, 'b'), _live_line((base - 5) * 1e6, 'before')])
    assert stream_chat.parse_youtube_live_chat(f, recording_start=base) == [(3.0, 'b'), (12.5, 'a')]


def test_live_capture_without_recording_start_gives_nothing_not_a_pile_at_zero(tmp_path):
    f = tmp_path / 'live.json'
    _write(f, [_live_line(1_700_000_005 * 1e6, 'a'), _live_line(1_700_000_006 * 1e6, 'b')])
    assert stream_chat.parse_youtube_live_chat(f) == []


def test_replay_with_some_zero_offsets_keeps_them(tmp_path):
    f = tmp_path / 'vod.json'
    _write(f, [_live_line(1_700_000_005 * 1e6, 'first', '0'), _live_line(1_700_000_006 * 1e6, 'later', '4000')])
    assert stream_chat.parse_youtube_live_chat(f) == [(0.0, 'first'), (4.0, 'later')]


def test_running_live_stream_is_rejected_before_downloading(monkeypatch):
    from backend.core.failure_codes import UserFacingError
    from backend.services.studio import jobs
    for status in ('is_live', 'is_upcoming'):
        monkeypatch.setattr(jobs, '_live_status', lambda url, options, s=status: s)
        with pytest.raises(UserFacingError):
            jobs._reject_running_live('https://youtu.be/x', {})
    for status in ('was_live', 'not_live', ''):
        monkeypatch.setattr(jobs, '_live_status', lambda url, options, s=status: s)
        jobs._reject_running_live('https://youtu.be/x', {})


def test_live_status_probe_failure_never_blocks(monkeypatch):
    import yt_dlp

    from backend.services.studio import jobs

    class Boom:
        def __init__(self, *a, **k): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, *a, **k): raise RuntimeError('net')
    monkeypatch.setattr(yt_dlp, 'YoutubeDL', Boom)
    assert jobs._live_status('https://youtu.be/x', {}) == ''


def test_live_capture_lines_without_any_offset_field_still_parse(tmp_path):
    f = tmp_path / 'live.json'
    base = 1_700_000_000.0
    line = _live_line((base + 7) * 1e6, 'hi')
    del line['replayChatItemAction']['videoOffsetTimeMsec']
    _write(f, [line])
    assert stream_chat.parse_youtube_live_chat(f, recording_start=base) == [(7.0, 'hi')]
