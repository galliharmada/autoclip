"""Windowed transcription: only the chosen windows are recognised, times stay on the source timeline."""
from pathlib import Path

from backend.services.studio import stream_transcript as st
from backend.services.studio.stream_signals import Window
from backend.utils import word_timing


def _fake_recognise(texts_by_name):
    """Writes an SRT (+ word sidecar) for the cut audio, with times relative to the window start."""
    def recognise(audio: Path, srt: Path):
        text = texts_by_name[audio.stem]
        srt.write_text(f'1\n00:00:01,000 --> 00:00:03,000\n{text}\n\n', encoding='utf-8')
        words = [{'text': w, 'start': 1.0 + i * .5, 'end': 1.4 + i * .5} for i, w in enumerate(text.split())]
        seg = {'start': 1.0, 'end': 3.0, 'text': text, 'words': words}
        word_timing.write_word_timing(srt, [seg])
    return recognise


def _cut(video, window, out):
    out.write_bytes(b'')


def test_shift_segments_moves_segment_and_word_times():
    seg = [{'start': 1.0, 'end': 3.0, 'text': 'a b', 'words': [{'text': 'a', 'start': 1.0, 'end': 1.5}, {'text': 'b', 'start': 1.5, 'end': 2.0}]}]
    out = st.shift_segments(seg, 100.0)
    assert out[0]['start'] == 101.0 and out[0]['words'][1]['end'] == 102.0 and seg[0]['start'] == 1.0


def test_format_and_parse_srt_roundtrip(tmp_path):
    f = tmp_path / 'x.srt'
    st.write_srt(f, [{'start': 3661.5, 'end': 3663.25, 'text': 'hello'}, {'start': 1.0, 'end': 2.0, 'text': 'first'}])
    assert 'first' in f.read_text().split('hello')[0] and '01:01:01,500' in f.read_text()
    assert st.read_srt(f) == [{'start': 1.0, 'end': 2.0, 'text': 'first'}, {'start': 3661.5, 'end': 3663.25, 'text': 'hello'}]


def test_snap_extends_to_cue_edges_within_limit():
    cues = [{'start': 95.0, 'end': 103.0, 'text': 'a'}, {'start': 125.0, 'end': 134.0, 'text': 'b'}]
    assert st.snap(100.0, 130.0, cues, 1000.0) == (95.0, 134.0)


def test_snap_ignores_far_edges_and_clamps():
    cues = [{'start': 80.0, 'end': 103.0, 'text': 'a'}, {'start': 125.0, 'end': 150.0, 'text': 'b'}]
    assert st.snap(100.0, 130.0, cues, 1000.0) == (100.0, 130.0)
    assert st.snap(2.0, 20.0, [{'start': 0.0, 'end': 5.0, 'text': 'a'}], 1000.0)[0] == 0.0
    assert st.snap(100.0, 130.0, [{'start': 125.0, 'end': 134.0, 'text': 'b'}], 131.0)[1] == 131.0


def test_snap_without_cues_is_identity():
    assert st.snap(10.0, 40.0, [], 100.0) == (10.0, 40.0)


def test_transcribe_windows_offsets_and_writes_merged_files(tmp_path):
    wins = [Window(500.0, 530.0, 510.0, 3.0), Window(100.0, 130.0, 110.0, 5.0)]
    names = {'w0': 'hello chat', 'w1': 'no way'}
    result = st.transcribe_windows(tmp_path / 'input.mp4', wins, tmp_path, cut=_cut, recognise=_fake_recognise(names))
    assert [c['start'] for c in result] == [101.0, 501.0]
    srt = tmp_path / 'input.srt'
    assert srt.exists() and word_timing.load_word_timing(srt) is not None
    segs = word_timing.load_word_timing(srt)
    assert [round(s['start']) for s in segs] == [101, 501]


def test_existing_srt_is_kept_and_no_recognition_runs(tmp_path):
    (tmp_path / 'input.srt').write_text('1\n00:00:05,000 --> 00:00:06,000\nkeep\n\n', encoding='utf-8')
    def boom(audio, srt):
        raise AssertionError('must not run')
    cues = st.transcribe_windows(tmp_path / 'input.mp4', [Window(0, 30, 10, 3)], tmp_path, cut=_cut, recognise=boom)
    assert [c['text'] for c in cues] == ['keep']


def test_failed_window_is_skipped_others_continue(tmp_path):
    good = _fake_recognise({'w1': 'second'})
    def recognise(audio, srt):
        if audio.stem == 'w0':
            raise RuntimeError('asr down')
        good(audio, srt)
    wins = [Window(100.0, 130.0, 110.0, 5.0), Window(500.0, 530.0, 510.0, 3.0)]
    cues = st.transcribe_windows(tmp_path / 'input.mp4', wins, tmp_path, cut=_cut, recognise=recognise)
    assert [c['text'] for c in cues] == ['second']
    assert word_timing.load_word_timing(tmp_path / 'input.srt') is not None


def test_all_windows_fail_writes_nothing_and_returns_empty(tmp_path):
    def recognise(audio, srt):
        raise RuntimeError('asr down')
    assert st.transcribe_windows(tmp_path / 'input.mp4', [Window(0, 30, 10, 3)], tmp_path, cut=_cut, recognise=recognise) == []
    assert not (tmp_path / 'input.srt').exists()


def test_sentence_only_window_skips_sidecar(tmp_path):
    def recognise(audio, srt):
        srt.write_text('1\n00:00:01,000 --> 00:00:02,000\nplain\n\n', encoding='utf-8')
    cues = st.transcribe_windows(tmp_path / 'input.mp4', [Window(10, 40, 20, 3)], tmp_path, cut=_cut, recognise=recognise)
    assert cues[0]['start'] == 11.0 and (tmp_path / 'input.srt').exists()
    assert word_timing.load_word_timing(tmp_path / 'input.srt') is None
