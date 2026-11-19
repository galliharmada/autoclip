"""Web import in stream mode: options, uploaded chat, and no transcript gate."""
import json

import pytest

from backend.services.studio import jobs, store
from backend.tests.test_studio import client, root, source  # noqa: F401

CHAT = '\n'.join(json.dumps({'t': t, 'text': text}) for t, text in [(1, 'KEKW'), (2, 'no way'), (5, 'W')]) + '\n'


@pytest.fixture
def captured(monkeypatch):
    seen = {}

    def inspect(pid, options, url, browser, **kwargs):
        seen.update(pid=pid, options=options, url=url)
        return 'run1'
    monkeypatch.setattr(jobs, 'inspect_project', inspect)
    return seen


def _post(client, source, **data):
    files = {'video': ('vod.mp4', source.read_bytes(), 'video/mp4')}
    if 'chat' in data:
        files['chat'] = ('chat.jsonl', data.pop('chat'), 'application/json')
    return client.post('/studio/import', data=data, files=files)


def test_stream_import_carries_options_and_stages_chat(client, source, captured):
    r = _post(client, source, stream='true', stream_top='5', stream_layout='stack', chat=CHAT.encode(), auto_start='true')
    assert r.status_code == 200, r.text
    o = captured['options']
    assert o.stream and o.stream_top == 5 and o.stream_layout == 'stack'
    raw = store.directory(captured['pid']) / 'raw'
    from backend.services.studio import stream_chat
    assert [t for t, _ in stream_chat.load_chat(raw / 'chat.jsonl')] == [1.0, 2.0, 5.0]


def test_stream_import_is_not_blocked_by_missing_local_transcription(client, source, captured, monkeypatch):
    monkeypatch.setattr('backend.services.studio.readiness.transcription_check',
                        lambda: {'ok': False, 'code': 'whisper_not_installed', 'repair': 'install_whisper'})
    assert _post(client, source, goal='content').status_code != 200   # the gate is real for a normal import
    assert not captured
    assert _post(client, source, stream='true').status_code == 200


def test_ytdlp_live_chat_json_is_accepted(client, source, captured):
    line = {'replayChatItemAction': {'videoOffsetTimeMsec': '4000', 'actions': [{'addChatItemAction': {'item': {
        'liveChatTextMessageRenderer': {'message': {'runs': [{'text': 'LUL'}]}}}}}]}}
    r = _post(client, source, stream='true', chat=json.dumps(line).encode())
    assert r.status_code == 200, r.text
    from backend.services.studio import stream_chat
    assert stream_chat.load_chat(store.directory(captured['pid']) / 'raw' / 'chat.jsonl') == [(4.0, 'LUL')]


@pytest.mark.parametrize('bad', [b'', b'not chat at all\n', b'{"t": -1, "text": "x"}\n'])
def test_unusable_chat_is_rejected_before_any_project(client, source, captured, bad):
    r = _post(client, source, stream='true', chat=bad)
    assert r.status_code == 422 and not captured


def test_oversized_chat_is_rejected(client, source, captured, monkeypatch):
    from backend.services import quick_output_runner
    monkeypatch.setattr(quick_output_runner, 'MAX_CHAT_BYTES', 10)
    assert _post(client, source, stream='true', chat=CHAT.encode()).status_code == 422 and not captured


@pytest.mark.parametrize('field,value', [('stream_top', '0'), ('stream_top', '21'), ('stream_layout', 'blur')])
def test_stream_option_bounds(client, source, captured, field, value):
    assert _post(client, source, stream='true', **{field: value}).status_code == 422 and not captured


def test_chat_without_stream_is_ignored(client, source, captured):
    r = _post(client, source, chat=CHAT.encode())
    assert r.status_code == 200 and not (store.directory(captured['pid']) / 'raw' / 'chat.jsonl').exists()
