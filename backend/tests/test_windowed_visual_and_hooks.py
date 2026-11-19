"""Windowed visual scan (T2) and the frame-derived opening lines.

These pin the two boundaries the UI now exposes: a chosen range samples only itself
(nothing outside is sent to the model or billed), and opening lines come from an
existing draft's frames without touching its scenes.
"""
import json
from pathlib import Path

import pytest

from backend.services.studio import intelligence
from backend.services.studio.models import Preferences


def _stub_vision(monkeypatch, replies):
    """Record every frame request; return the queued replies in order."""
    seen = []

    def fake_sample(video, times, folder, width=640):
        seen.append(list(times))
        return [{'type': 'text', 'text': f't={stamp}'} for stamp in times]

    queue = list(replies)

    def fake_call(phase, content):
        seen.append(('phase', phase))
        return queue.pop(0) if queue else {'events': []}

    monkeypatch.setattr(intelligence, 'sample', fake_sample)
    monkeypatch.setattr(intelligence, 'vision_call_at', fake_call)
    monkeypatch.setattr(intelligence, '_probe', lambda video: {'duration': 600.0})
    return seen


def _event(**changes):
    data = {'id': 'event-1', 'label': '片段', 'start': 300.0, 'end': 320.0,
            'evidence': '画面里有人开门', 'event_type': 'gameplay',
            'watch_score': 80, 'selection_reason': '可见动作'}
    data.update(changes)
    return data


def test_analyze_without_window_keeps_sampling_the_whole_source(monkeypatch):
    seen = _stub_vision(monkeypatch, [{'events': [_event()]}, {'events': [_event()]}])
    intelligence.analyze(Path('unused.mp4'), Preferences(goal='highlight'))
    times = seen[0]
    assert times[0] == 0 and times[-1] < 600
    assert max(times) > 550


def test_window_samples_only_inside_the_requested_range(monkeypatch):
    seen = _stub_vision(monkeypatch, [{'events': [_event()]}, {'events': [_event()]}])
    _, coverage = intelligence.analyze(Path('unused.mp4'), Preferences(goal='highlight'),
                                       window={'start': 300, 'end': 360})
    scan_times = seen[0]
    assert min(scan_times) >= 300 and max(scan_times) < 360
    assert coverage['window'] == {'start': 300.0, 'end': 360.0}


def test_window_keeps_timestamps_on_the_source_timeline(monkeypatch):
    """A window must not renumber: drafts are cut from the original file."""
    _stub_vision(monkeypatch, [{'events': [_event(start=302.0, end=318.0)]},
                               {'events': [_event(start=303.0, end=317.0)]}])
    events, _ = intelligence.analyze(Path('unused.mp4'), Preferences(goal='highlight'),
                                     window={'start': 300, 'end': 360})
    assert events[0].start >= 300
    assert events[0].start > 100


def test_window_drops_candidates_the_model_placed_outside_it(monkeypatch):
    _stub_vision(monkeypatch, [{'events': [_event(id='inside', start=400, end=420),
                                            _event(id='outside', start=10, end=30)]},
                               {'events': [_event(id='inside', start=401, end=419)]}])
    events, coverage = intelligence.analyze(Path('unused.mp4'), Preferences(goal='highlight'),
                                            window={'start': 380, 'end': 440})
    assert [e.id for e in events] == ['inside']
    assert any(item.get('window_clamped') for item in coverage['selection'])


def test_window_outside_the_source_is_refused_with_a_sentence(monkeypatch):
    _stub_vision(monkeypatch, [])
    with pytest.raises(ValueError, match='所选时间段不在素材范围内'):
        intelligence.analyze(Path('unused.mp4'), Preferences(goal='highlight'),
                             window={'start': 900, 'end': 1200})


def test_window_with_no_usable_frame_is_reported_not_silently_full(monkeypatch):
    _stub_vision(monkeypatch, [{'events': [_event(start=10, end=30)]}])
    with pytest.raises(ValueError, match='所选时间段内没有找到可用画面'):
        intelligence.analyze(Path('unused.mp4'), Preferences(goal='highlight'),
                             window={'start': 300, 'end': 360})


def test_malformed_window_falls_back_to_the_whole_source(monkeypatch):
    seen = _stub_vision(monkeypatch, [{'events': [_event()]}, {'events': [_event()]}])
    _, coverage = intelligence.analyze(Path('unused.mp4'), Preferences(goal='highlight'),
                                       window={'start': 500, 'end': 100})
    assert min(seen[0]) == 0
    assert 'window' not in coverage


# --- opening lines from an existing draft -------------------------------------------

DRAFT = {'id': 'draft-1', 'title': '街头打斗', 'hook': '',
         'scenes': [{'id': 's1', 'label': '街头打斗', 'start': 56.0, 'end': 77.0}]}


def test_suggest_hooks_reads_the_drafts_own_frames(monkeypatch):
    seen = _stub_vision(monkeypatch, [{'hooks': [
        {'hook': '他一脚就把人放倒', 'reason': '第 2 帧有人倒地'},
        {'hook': '三秒结束的街头冲突', 'reason': '第 3 帧冲突结束'},
    ]}])
    result = intelligence.suggest_hooks(Path('unused.mp4'), [DRAFT])
    frame_times = seen[0]
    assert min(frame_times) >= 56 and max(frame_times) < 77
    assert [item['hook'] for item in result['drafts'][0]['hooks']] == ['他一脚就把人放倒', '三秒结束的街头冲突']
    assert result['drafts'][0]['draft_id'] == 'draft-1'


def test_suggest_hooks_never_saves_or_rewrites_the_draft(monkeypatch):
    _stub_vision(monkeypatch, [{'hooks': [{'hook': '一句片头', 'reason': '可见依据'}]}])
    before = json.dumps(DRAFT, sort_keys=True)
    intelligence.suggest_hooks(Path('unused.mp4'), [DRAFT])
    assert json.dumps(DRAFT, sort_keys=True) == before


def test_suggest_hooks_rejects_over_long_and_empty_lines(monkeypatch):
    _stub_vision(monkeypatch, [{'hooks': [{'hook': '', 'reason': 'x'},
                                          {'hook': '长' * 200, 'reason': 'x'},
                                          {'hook': '可用的一句', 'reason': '可见依据'}]}])
    result = intelligence.suggest_hooks(Path('unused.mp4'), [DRAFT])
    assert [item['hook'] for item in result['drafts'][0]['hooks']] == ['可用的一句']


def test_suggest_hooks_skips_a_draft_the_model_could_not_answer(monkeypatch):
    other = {**DRAFT, 'id': 'draft-2'}
    _stub_vision(monkeypatch, [{}, {'hooks': [{'hook': '第二个片段的片头', 'reason': '可见'}]}])
    result = intelligence.suggest_hooks(Path('unused.mp4'), [DRAFT, other])
    assert [item['draft_id'] for item in result['drafts']] == ['draft-2']


def test_suggest_hooks_refuses_when_nothing_usable_came_back(monkeypatch):
    _stub_vision(monkeypatch, [{}, {}])
    with pytest.raises(ValueError, match='没有生成可用的片头文字'):
        intelligence.suggest_hooks(Path('unused.mp4'), [DRAFT, {**DRAFT, 'id': 'draft-2'}])


def test_suggest_hooks_needs_a_draft(monkeypatch):
    _stub_vision(monkeypatch, [])
    with pytest.raises(ValueError, match='没有可用的成片片段'):
        intelligence.suggest_hooks(Path('unused.mp4'), [])

def test_drafts_never_bleed_outside_the_window(monkeypatch):
    """The context the editor adds must grow inward at a window boundary."""
    from backend.services.studio import intelligence as vision
    events = [vision.Scene(id='e1', label='边界事件', start=300.0, end=320.0)]
    drafts = vision.make_drafts(events, Preferences(goal='highlight', duration=30),
                                source_duration=600.0, window={'start': 300, 'end': 420})
    scene = drafts[0]['scenes'][0]
    assert scene['start'] >= 300 and scene['end'] <= 420
    assert scene['end'] > scene['start']


def test_drafts_still_get_context_when_no_window_is_set():
    from backend.services.studio import intelligence as vision
    events = [vision.Scene(id='e1', label='中间事件', start=300.0, end=320.0)]
    drafts = vision.make_drafts(events, Preferences(goal='highlight', duration=30), source_duration=600.0)
    assert drafts[0]['scenes'][0]['start'] == 298.5  # 1.5 s of lead, unchanged for full-file runs
