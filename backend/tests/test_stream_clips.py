"""Stream route: windows become drafts the existing pipeline can consume."""
import pytest

from backend.core.failure_codes import UserFacingError
from backend.services.studio import jobs, stream_clips
from backend.services.studio.models import Draft, ImportOptions
from backend.services.studio.stream_signals import Window


def _wins():
    return [Window(start=580.0, end=610.0, peak=600.0, score=5.0, reasons=['chat_rate', 'audio']),
            Window(start=100.0, end=130.0, peak=110.0, score=2.0, reasons=['chat_hits'])]


def test_drafts_from_windows_are_valid_and_scored(monkeypatch, tmp_path):
    monkeypatch.setattr(stream_clips.stream_signals, 'score_source', lambda *a, **k: _wins())
    monkeypatch.setattr(stream_clips, '_duration', lambda video: 1200.0)
    monkeypatch.setattr(stream_clips.stream_transcript, 'transcribe_windows', lambda *a, **k: [])
    plan = {'preferences': {'goal': 'highlight', 'language': 'source', 'aspect': 'portrait', 'duration': 30}, 'overrides': {'stream': True}}
    out = stream_clips.drafts(plan, tmp_path / 'input.mp4')
    assert [d[jobs.SCORE_KEY] for d in out] == [5.0, 2.0]
    d = Draft.model_validate({k: v for k, v in out[0].items() if k != jobs.SCORE_KEY})
    assert d.origin == 'auto-stream' and d.layout == 'crop' and d.scenes[0].start == 580.0 and '聊天' in d.scenes[0].evidence


def test_landscape_stream_uses_fit(monkeypatch, tmp_path):
    monkeypatch.setattr(stream_clips.stream_signals, 'score_source', lambda *a, **k: _wins()[:1])
    monkeypatch.setattr(stream_clips, '_duration', lambda video: 1200.0)
    monkeypatch.setattr(stream_clips.stream_transcript, 'transcribe_windows', lambda *a, **k: [])
    plan = {'preferences': {'goal': 'highlight', 'language': 'source', 'aspect': 'original', 'duration': 30}}
    assert stream_clips.drafts(plan, tmp_path / 'input.mp4')[0]['layout'] == 'fit'


def test_no_windows_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(stream_clips.stream_signals, 'score_source', lambda *a, **k: [])
    monkeypatch.setattr(stream_clips, '_duration', lambda video: 1200.0)
    monkeypatch.setattr(stream_clips.stream_transcript, 'transcribe_windows', lambda *a, **k: [])
    with pytest.raises(UserFacingError):
        stream_clips.drafts({'preferences': {'goal': 'highlight', 'aspect': 'portrait'}}, tmp_path / 'input.mp4')


def test_import_options_stream_flag_roundtrips():
    assert ImportOptions().stream is False
    assert ImportOptions(stream=True).model_dump(exclude_none=True)['stream'] is True


def test_content_drafts_routes_stream_without_models(monkeypatch, tmp_path):
    monkeypatch.setattr(stream_clips, 'drafts', lambda plan, video, **k: [{'id': 'x', jobs.SCORE_KEY: 1.0}])
    plan = {'recommended_analysis': 'subtitle', 'preferences': {'goal': 'highlight'}, 'overrides': {'stream': True}}
    assert jobs._content_drafts('p1', plan, tmp_path / 'input.mp4') == [{'id': 'x', jobs.SCORE_KEY: 1.0}]


def test_window_edges_snap_to_sentences(monkeypatch, tmp_path):
    monkeypatch.setattr(stream_clips.stream_signals, 'score_source', lambda *a, **k: _wins()[:1])
    monkeypatch.setattr(stream_clips, '_duration', lambda video: 1200.0)
    cues = [{'start': 575.0, 'end': 584.0, 'text': 'a'}, {'start': 606.0, 'end': 615.0, 'text': 'b'}]
    monkeypatch.setattr(stream_clips.stream_transcript, 'transcribe_windows', lambda *a, **k: cues)
    scene = stream_clips.drafts({'preferences': {'aspect': 'portrait'}}, tmp_path / 'input.mp4')[0]['scenes'][0]
    assert (scene['start'], scene['end']) == (575.0, 615.0)


def test_transcription_crash_still_yields_drafts(monkeypatch, tmp_path):
    monkeypatch.setattr(stream_clips.stream_signals, 'score_source', lambda *a, **k: _wins()[:1])
    monkeypatch.setattr(stream_clips, '_duration', lambda video: 1200.0)
    def boom(*a, **k):
        raise RuntimeError('asr')
    monkeypatch.setattr(stream_clips.stream_transcript, 'transcribe_windows', boom)
    assert stream_clips.drafts({'preferences': {'aspect': 'portrait'}}, tmp_path / 'input.mp4')[0]['scenes'][0]['start'] == 580.0
