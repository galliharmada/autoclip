"""Facecam detection and the 'stack' layout (facecam on top, gameplay below)."""
import random

import pytest
from pydantic import ValidationError

from backend.services.studio import facecam, jobs, packaging_render, stream_clips
from backend.services.studio.models import Draft, Scene
from backend.services.studio.stream_signals import Window

ASPECT = 16 / 9


def _box(cx, cy, w, h=None, score=.9):
    return facecam.Box(cx=cx, cy=cy, w=w, h=h or w * ASPECT, score=score)


def _frames(stable=None, n=30, present=24, noise=True):
    rng = random.Random(3)
    frames = []
    for i in range(n):
        boxes = []
        if stable and i < present:
            boxes.append(_box(stable[0] + rng.uniform(-.004, .004), stable[1] + rng.uniform(-.004, .004), stable[2]))
        if noise:
            boxes.append(_box(rng.uniform(.2, .8), rng.uniform(.2, .8), rng.uniform(.04, .2)))  # a character walking around
        frames.append(boxes)
    return frames


def test_pick_facecam_finds_stationary_corner_face():
    rect = facecam.pick_facecam(_frames(stable=(.86, .18, .09)), ASPECT)
    x, y, w, h = rect
    assert 0 <= x and 0 <= y and x + w <= 1.0001 and y + h <= 1.0001
    assert x < .86 < x + w and y < .18 < y + h
    assert abs((h * 9) / (w * 16) - 768 / 1080) < .02   # same aspect as the 1080x768 top panel


@pytest.mark.parametrize('frames', [
    _frames(stable=None),                                  # only moving faces
    _frames(stable=(.86, .18, .09), present=8),            # seen in <40% of frames
    [[] for _ in range(30)],                               # nobody
    [],
])
def test_pick_facecam_none(frames):
    assert facecam.pick_facecam(frames, ASPECT) is None


def test_pick_facecam_ignores_big_faces():
    assert facecam.pick_facecam(_frames(stable=(.5, .5, .4), noise=False), ASPECT) is None


def test_pick_facecam_clamps_at_corner():
    x, y, w, _ = facecam.pick_facecam(_frames(stable=(.97, .04, .08), noise=False), ASPECT)
    assert x + w <= 1.0001 and y >= 0 and x >= 0


def test_stack_filter_shape():
    g = facecam.stack_filter((.7, .05, .28, .27), .5, 1080, 1920)
    assert g.startswith('[0:v]split=2') and 'vstack=inputs=2[base]' in g
    assert 'scale=1080:768' in g and 'scale=1080:1152' in g and 'iw*0.70000' in g


def test_draft_stack_requires_facecam_else_crop():
    base = {'id': 'd1', 'title': 't', 'scenes': [Scene(id='s1', start=0, end=5)]}
    assert Draft(**base, layout='stack', facecam=(.7, .05, .28, .27)).layout == 'stack'
    assert Draft(**base, layout='stack').layout == 'crop'


def test_draft_old_payload_without_facecam_loads():
    d = Draft.model_validate({'id': 'd1', 'title': 't', 'scenes': [{'id': 's', 'start': 0, 'end': 5}], 'layout': 'crop'})
    assert d.facecam is None


@pytest.mark.parametrize('bad', [(0, 0, 0, .3), (.8, 0, .4, .3), (0, .9, .3, .3), (-.1, 0, .2, .2)])
def test_draft_rejects_bad_facecam(bad):
    with pytest.raises((ValidationError, ValueError)):
        Draft(id='d1', title='t', scenes=[Scene(id='s1', start=0, end=5)], layout='stack', facecam=bad)


def test_picture_only_stack_uses_stack_chain():
    d = Draft(id='d1', title='t', scenes=[Scene(id='s1', start=0, end=5)], layout='stack', facecam=(.7, .05, .28, .27), crop_x=.3)
    chain, label = packaging_render.picture_only(d, d.scenes[0], 1080, 1920)
    assert label == 'base' and 'vstack' in chain and "(iw-ow)*0.3" in chain.replace('0.30000', '0.3')


def test_apply_strategy_keeps_stack_on_portrait_platform():
    value = Draft(id='d1', title='t', scenes=[Scene(id='s1', start=0, end=5)], layout='stack', facecam=(.7, .05, .28, .27), aspect='portrait').model_dump()
    out = jobs._apply_strategy(value, 'douyin', burned_subtitles=False, layout='crop', output_captions=True)
    assert out.layout == 'stack' and out.facecam == (.7, .05, .28, .27)


def test_apply_framing_skips_stack_drafts():
    value = Draft(id='d1', title='t', scenes=[Scene(id='s1', start=0, end=5)], layout='stack', facecam=(.7, .05, .28, .27), aspect='portrait').model_dump()
    out, framed = jobs._apply_framing('p', value, 'douyin', 'v.mp4', False, {})
    assert out is value and framed is None


def _wins():
    return [Window(start=580.0, end=610.0, peak=600.0, score=5.0, reasons=['audio'])]


def test_stream_drafts_use_stack_when_facecam_found(monkeypatch, tmp_path):
    monkeypatch.setattr(stream_clips.stream_signals, 'score_source', lambda *a, **k: _wins())
    monkeypatch.setattr(stream_clips.stream_transcript, 'transcribe_windows', lambda *a, **k: [])
    monkeypatch.setattr(stream_clips, '_duration', lambda v: 1200.0)
    monkeypatch.setattr(stream_clips.facecam, 'detect_facecam', lambda v, d: (.7, .05, .28, .27))
    out = stream_clips.drafts({'preferences': {'aspect': 'portrait'}}, tmp_path / 'input.mp4')[0]
    assert out['layout'] == 'stack' and out['facecam'] == (.7, .05, .28, .27)


def test_stream_drafts_fall_back_to_crop_without_facecam_or_on_error(monkeypatch, tmp_path):
    monkeypatch.setattr(stream_clips.stream_signals, 'score_source', lambda *a, **k: _wins())
    monkeypatch.setattr(stream_clips.stream_transcript, 'transcribe_windows', lambda *a, **k: [])
    monkeypatch.setattr(stream_clips, '_duration', lambda v: 1200.0)
    monkeypatch.setattr(stream_clips.facecam, 'detect_facecam', lambda v, d: None)
    assert stream_clips.drafts({'preferences': {'aspect': 'portrait'}}, tmp_path / 'input.mp4')[0]['layout'] == 'crop'
    def boom(v, d):
        raise RuntimeError('cv2')
    monkeypatch.setattr(stream_clips.facecam, 'detect_facecam', boom)
    assert stream_clips.drafts({'preferences': {'aspect': 'portrait'}}, tmp_path / 'input.mp4')[0]['layout'] == 'crop'
    assert stream_clips.drafts({'preferences': {'aspect': 'original'}}, tmp_path / 'input.mp4')[0]['layout'] == 'fit'


def test_facecam_crop_covers_an_overlay_not_just_the_face():
    """Real stream (IShowSpeed, 720p): the face is ~4% of the frame wide; the overlay ~20%. A face-tight crop upscaled to 1080 is mush."""
    x, y, w, h = facecam.pick_facecam(_frames(stable=(.1, .55, .04), noise=False), ASPECT)
    assert w >= .15 and x <= .1 <= x + w
