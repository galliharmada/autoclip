"""Stream moment signals: chat rate/hits, loudness, window picking. Pure functions, no media."""

from backend.services.studio import stream_signals as ss


def _chat(base_every=2.0, duration=1200, bursts=()):
    """Steady chatter plus bursts: (start, seconds, msgs_per_second, text)."""
    msgs, t = [], 0.0
    while t < duration:
        msgs.append((t, 'hello there'))
        t += base_every
    for start, length, rate, text in bursts:
        n = int(length * rate)
        msgs += [(start + i / rate, text) for i in range(n)]
    return sorted(msgs)


def test_spike_flat_is_zero():
    assert max(ss.spike([2.0] * 900)) == 0.0


def test_spike_empty():
    assert ss.spike([]) == []


def test_chat_rate_burst_peaks_at_burst():
    z = ss.chat_rate(_chat(bursts=[(600, 5, 10, 'x')]), 1200)
    peak = max(range(len(z)), key=z.__getitem__)
    assert 598 <= peak <= 607 and z[peak] > 3 and z[100] < 1


def test_chat_hits_counts_emotes_and_phrases():
    msgs = [(10.0, 'KEKW'), (10.2, 'no way bro'), (10.4, '💀💀'), (10.6, 'W'), (10.8, 'normal talk'), (11.0, 'wow')]
    hits = ss.chat_hit_counts(msgs, 20)
    assert hits[10] == 4 and hits[11] == 0 and len(hits) == 20


def test_chat_hits_ignores_out_of_range():
    assert sum(ss.chat_hit_counts([(500.0, 'KEKW'), (-1.0, 'KEKW')], 20)) == 0


def test_combine_renormalises_when_signal_missing():
    only_audio = ss.combine({'audio': [0.0, 3.0, 0.0], 'chat_rate': []})
    assert only_audio == [0.0, 3.0, 0.0]
    assert ss.combine({}) == [] and ss.combine({'audio': []}) == []


def test_combine_caps_each_signal():
    assert ss.combine({'audio': [100.0]}) == [ss.CAP]


def test_find_windows_burst_window_covers_burst():
    z = ss.chat_rate(_chat(bursts=[(600, 5, 10, 'x')]), 1200)
    wins = ss.find_windows(z)
    assert len(wins) == 1
    w = wins[0]
    assert w.start <= 585 and w.end >= 608 and w.end - w.start <= 90


def test_find_windows_two_close_bursts_make_one_window():
    z = ss.chat_rate(_chat(bursts=[(600, 4, 10, 'x'), (615, 4, 10, 'x')]), 1200)
    wins = ss.find_windows(z)
    assert len(wins) == 1 and wins[0].start <= 590 and wins[0].end >= 615


def test_find_windows_far_bursts_two_windows_sorted_by_score_and_non_overlapping():
    z = ss.chat_rate(_chat(bursts=[(300, 5, 8, 'x'), (900, 5, 14, 'x')]), 1200)
    wins = ss.find_windows(z)
    assert len(wins) == 2 and wins[0].score >= wins[1].score
    a, b = sorted(wins, key=lambda w: w.start)
    assert a.end <= b.start


def test_find_windows_top_n_and_flat():
    z = ss.chat_rate(_chat(bursts=[(200 + 150 * i, 5, 10, 'x') for i in range(6)]), 1200)
    assert len(ss.find_windows(z, top_n=3)) == 3
    assert ss.find_windows([0.0] * 500) == [] and ss.find_windows([]) == []


def test_find_windows_clamped_to_source_and_max_len():
    z = [0.0] * 100
    z[2] = 6.0
    w = ss.find_windows(z)[0]
    assert w.start == 0 and w.end <= 100
    z = [6.0] * 300
    assert ss.find_windows(z, max_len=60)[0].end - ss.find_windows(z, max_len=60)[0].start <= 60


def test_find_windows_reasons_name_firing_signals():
    sig = {'chat_rate': [0.0] * 50, 'audio': [0.0] * 50}
    sig['audio'][25] = 4.0
    z = ss.combine(sig)
    assert ss.find_windows(z, sig, min_score=0.5)[0].reasons == ['audio']


EBUR = """[Parsed_ebur128_0 @ 0x55] t: 0.099979    TARGET:-23 LUFS    M:-120.7 S:-120.7     I: -70.0 LUFS     LRA:   0.0 LU
[Parsed_ebur128_0 @ 0x55] t: 0.5    TARGET:-23 LUFS    M: -30.0 S:-120.7     I: -70.0 LUFS     LRA:   0.0 LU
[Parsed_ebur128_0 @ 0x55] t: 1.2    TARGET:-23 LUFS    M: -12.5 S:-120.7     I: -70.0 LUFS     LRA:   0.0 LU
[Parsed_ebur128_0 @ 0x55] t: 1.7    TARGET:-23 LUFS    M: -20.0 S:-120.7     I: -70.0 LUFS     LRA:   0.0 LU
unrelated line
[Parsed_ebur128_0 @ 0x55] t: 4.1    TARGET:-23 LUFS    M:-inf S:-120.7     I: -70.0 LUFS     LRA:   0.0 LU
"""


def test_parse_ebur128_per_second_max_and_gaps():
    got = ss.parse_ebur128(EBUR.splitlines(), 6)
    assert got == [-30.0, -12.5, ss.SILENCE, ss.SILENCE, ss.SILENCE, ss.SILENCE]


def test_parse_ebur128_no_lines_is_empty():
    assert ss.parse_ebur128(['nothing'], 10) == []


def test_score_source_chat_and_audio(tmp_path):
    from backend.services.studio import stream_chat
    chat = tmp_path / 'chat.jsonl'
    stream_chat.write_chat(chat, _chat(bursts=[(600, 5, 12, 'KEKW')]))
    quiet = [-30.0] * 1200
    quiet[603] = -8.0
    wins = ss.score_source(tmp_path / 'v.mp4', chat, 1200, loudness=lambda v, d: quiet)
    assert wins and wins[0].start <= 585 and wins[0].end >= 608
    assert set(wins[0].reasons) >= {'chat_rate', 'chat_hits'}


def test_score_source_without_chat_uses_audio_only(tmp_path):
    loud = [-30.0] * 900
    loud[400] = loud[401] = loud[402] = -6.0
    wins = ss.score_source(tmp_path / 'v.mp4', tmp_path / 'missing.jsonl', 900, loudness=lambda v, d: loud)
    assert wins and wins[0].reasons == ['audio']


def test_score_source_no_signals_is_empty(tmp_path):
    assert ss.score_source(tmp_path / 'v.mp4', tmp_path / 'none.jsonl', 900, loudness=lambda v, d: []) == []
