"""Stream clips from the CLI and MCP: options reach the import, chat is staged, no transcript preflight."""
import json

import pytest

from backend import cli
from backend.services import quick_output_runner as quick
from backend.services.studio import jobs, store, stream_clips
from backend.services.studio.models import ImportOptions
from backend.tests.test_cli import data_dir, isolated_db  # noqa: F401


@pytest.fixture
def imported(data_dir, isolated_db, tmp_path, monkeypatch):
    import backend.utils.thumbnail_generator as thumbnail
    monkeypatch.setattr(thumbnail, 'generate_project_thumbnail', lambda *a, **kw: None)
    source = tmp_path / 'vod.mp4'
    source.write_bytes(b'video')
    calls = []

    def inspect(pid, options, url, browser, **kwargs):
        calls.append((pid, options, url, browser))
        store.write(pid, {'generation': {'status': 'screening'}, 'jobs': [], 'drafts': [], 'output_variants': []})
    monkeypatch.setattr(jobs, 'inspect_project', inspect)
    return source, calls


def test_import_options_stream_fields_defaults_and_bounds():
    o = ImportOptions(stream=True)
    assert o.stream_top == 8 and o.stream_layout == 'auto'
    for bad in ({'stream_top': 0}, {'stream_top': 21}, {'stream_layout': 'blur'}):
        with pytest.raises(ValueError):
            ImportOptions(stream=True, **bad)


def test_local_stream_start_skips_transcript_preflight_and_stages_chat(imported, tmp_path, monkeypatch):
    source, calls = imported
    from backend.services.studio import preflight
    monkeypatch.setattr(preflight, 'check', lambda *a, **k: (_ for _ in ()).throw(AssertionError('no transcript preflight for streams')))
    chat = tmp_path / 'chat.jsonl'
    chat.write_text(json.dumps({'t': 1, 'text': 'KEKW'}) + '\n', encoding='utf-8')
    pid = quick.start(str(source), ['douyin'], stream=True, stream_top=5, stream_layout='crop', chat_path=str(chat))
    options = calls[0][1]
    assert options.stream and options.stream_top == 5 and options.stream_layout == 'crop' and options.auto_start
    assert (store.directory(pid) / 'raw' / 'chat.jsonl').read_bytes() == chat.read_bytes()


def test_twitch_vod_url_is_accepted_for_streams(imported):
    _, calls = imported
    quick.start('https://www.twitch.tv/videos/123456', ['douyin'], stream=True)
    assert calls[0][2] == 'https://www.twitch.tv/videos/123456'


def test_twitch_url_is_rejected_without_stream(imported):
    with pytest.raises(ValueError):
        quick.start('https://www.twitch.tv/videos/123456', ['douyin'])


def test_missing_or_oversized_chat_file_is_rejected_before_any_project(imported, tmp_path, data_dir):
    source, calls = imported
    with pytest.raises(ValueError):
        quick.start(str(source), ['douyin'], stream=True, chat_path=str(tmp_path / 'nope.jsonl'))
    big = tmp_path / 'big.jsonl'
    big.write_bytes(b'x' * (quick.MAX_CHAT_BYTES + 1))
    with pytest.raises(ValueError):
        quick.start(str(source), ['douyin'], stream=True, chat_path=str(big))
    assert not calls and not list((data_dir / 'projects').glob('*'))


def test_cli_stream_subcommand_maps_arguments(monkeypatch, capsys):
    seen = {}

    def fake_start(source, platforms, **kw):
        seen.update(source=source, platforms=platforms, **kw)
        return 'pid1'
    monkeypatch.setattr(quick, 'start', fake_start)
    monkeypatch.setattr(quick, 'wait', lambda pid, timeout: {'status': 'completed', 'project_id': pid})
    code = cli.main(['stream', 'https://www.twitch.tv/videos/1', '--top', '4', '--layout', 'stack', '--chat', 'c.jsonl', '--json'])
    assert code == 0 and seen['stream'] is True and seen['stream_top'] == 4 and seen['stream_layout'] == 'stack'
    assert seen['chat_path'] == 'c.jsonl' and seen['platforms'] == ['douyin']
    assert json.loads(capsys.readouterr().out)['ok'] is True


def test_stream_drafts_honour_top_and_layout_overrides(monkeypatch, tmp_path):
    from backend.services.studio.stream_signals import Window
    seen = {}

    def score(video, chat, duration, top_n=8, **k):
        seen['top_n'] = top_n
        return [Window(10.0, 40.0, 20.0, 3.0, ['audio'])]
    monkeypatch.setattr(stream_clips.stream_signals, 'score_source', score)
    monkeypatch.setattr(stream_clips.stream_transcript, 'transcribe_windows', lambda *a, **k: [])
    monkeypatch.setattr(stream_clips, '_duration', lambda v: 600.0)
    monkeypatch.setattr(stream_clips.facecam, 'detect_facecam', lambda v, d: (.7, .05, .28, .27))
    plan = {'preferences': {'aspect': 'portrait'}, 'overrides': {'stream': True, 'stream_top': 3, 'stream_layout': 'crop'}}
    out = stream_clips.drafts(plan, tmp_path / 'input.mp4')
    assert seen['top_n'] == 3 and out[0]['layout'] == 'crop' and out[0]['facecam'] is None
    plan['overrides']['stream_layout'] = 'auto'
    assert stream_clips.drafts(plan, tmp_path / 'input.mp4')[0]['layout'] == 'stack'


def test_mcp_start_quick_output_passes_stream_options(monkeypatch):
    from backend import mcp_server
    captured = {}

    def fake_start(source, platforms, **kw):
        captured.update(kw)
        return 'pidx'
    monkeypatch.setattr(quick, 'start', fake_start)
    from backend.services import mcp_jobs
    monkeypatch.setattr(mcp_jobs, 'write_job', lambda *a, **k: None)
    monkeypatch.setattr(mcp_jobs, 'read_job', lambda *a, **k: {})
    result = mcp_server.start_quick_output('https://www.twitch.tv/videos/1', stream=True, stream_top=6, stream_layout='stack')
    assert result['ok'] and captured['stream'] is True and captured['stream_top'] == 6 and captured['stream_layout'] == 'stack'
