from asrlib.chunking import keep_windows, plan_chunks


def test_short_single_chunk():
    chunks = plan_chunks(300.0, [(0, 300)])
    assert len(chunks) == 1
    assert (chunks[0].start, chunks[0].end) == (0, 300.0)


def test_every_chunk_within_10min():
    speech = [(t, t + 29) for t in range(0, 7200, 30)]
    chunks = plan_chunks(7200.0, speech)
    assert all(c.end - c.start <= 600 for c in chunks)
    assert chunks[-1].end == 7200


def test_cut_at_silence():
    chunks = plan_chunks(1000.0, [(0, 560), (562, 1000)])
    assert chunks[0].end == 561.0


def test_no_silence_hard_cut():
    chunks = plan_chunks(1000.0, [(0, 1000)])
    assert chunks[0].end == 600.0


def test_overlap():
    speech = [(t, t + 29) for t in range(0, 3000, 30)]
    chunks = plan_chunks(3000.0, speech)
    assert len(chunks) > 2
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.start == prev.end - 45


def test_keep_windows_partition():
    speech = [(t, t + 29) for t in range(0, 7200, 30)]
    chunks = plan_chunks(7200.0, speech)
    wins = keep_windows(chunks)
    assert wins[0][0] == 0 and wins[-1][1] == 7200
    for (_, e), (s, _) in zip(wins, wins[1:]):
        assert e == s
    for c, (s, e) in zip(chunks, wins):
        assert s < e and c.start <= s and e <= c.end
