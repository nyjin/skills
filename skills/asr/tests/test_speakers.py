import numpy as np

from asrlib.speakers import (
    LocalSpeaker,
    assign_labels,
    consolidate,
    link_chunk,
    local_speakers,
    overlap_votes,
)

DIM = 16
SR = 1000
RNG = np.random.default_rng(0)


def unit(i, noise=0.01):
    v = np.zeros(DIM)
    v[i] = 1.0
    v = v + RNG.normal(0, noise, DIM)
    return v / np.linalg.norm(v)


def mixed(cos):
    """e0 과 코사인 유사도가 cos 인 단위벡터."""
    v = np.zeros(DIM)
    v[0] = cos
    v[1] = np.sqrt(1 - cos**2)
    return v


def entry(first_seen, print_, speech_sec=10.0, **extra):
    return {
        "name": None,
        "first_seen": first_seen,
        "speech_sec": speech_sec,
        "voiceprint": None if print_ is None else [float(x) for x in print_],
        **extra,
    }


def seeded():
    """조각 0 에서 S1(e0), S2(e1) 을 seed 로 만든 명부."""
    speakers, links = {}, []
    link_chunk(speakers, links, 0, {
        0: LocalSpeaker(0, unit(0), 30.0, 0.0),
        1: LocalSpeaker(1, unit(1), 20.0, 3.0),
    }, {})
    return speakers, links


def test_local_speakers_skips_short():
    segs = [{"start": 0.0, "end": 1.5, "speaker_id": 0, "text": "네"}]
    audio = np.zeros(5 * SR, dtype=np.float32)
    result = local_speakers(segs, audio, SR, 100.0, lambda x: unit(0))
    assert result[0].voiceprint is None
    assert result[0].speech_sec == 1.5
    assert result[0].first_start == 100.0


def test_local_speakers_trims_and_caps():
    durs = [25.0, 20.0, 10.0, 8.0, 1.5]
    segs, t = [], 0.0
    for d in durs:
        segs.append({"start": t, "end": t + d, "speaker_id": 0, "text": "x"})
        t += d
    audio = np.zeros(int(t * SR) + SR, dtype=np.float32)
    lengths = []

    def embed(x):
        lengths.append(len(x))
        return unit(0)

    result = local_speakers(segs, audio, SR, 0.0, embed)
    assert sum(lengths) <= 60 * SR
    assert lengths == [int((d - 0.5) * SR) for d in (25.0, 20.0, 10.0)]
    assert result[0].speech_sec == sum(durs)
    assert np.isclose(np.linalg.norm(result[0].voiceprint), 1.0)


def test_seed_first_chunk():
    speakers, links = {}, []
    mapping = link_chunk(speakers, links, 0, {
        0: LocalSpeaker(0, unit(0), 30.0, 0.0),
        1: LocalSpeaker(1, None, 1.0, 3.0),
    }, {})
    assert mapping == {0: "S1", 1: "S2"}
    assert [l["how"] for l in links] == ["seed", "seed"]
    assert speakers["S2"]["voiceprint"] is None


def test_link_by_voice_swapped_ids():
    speakers, links = seeded()
    mapping = link_chunk(speakers, links, 1, {
        0: LocalSpeaker(0, unit(1), 10.0, 600.0),
        1: LocalSpeaker(1, unit(0), 10.0, 601.0),
    }, {})
    assert mapping == {0: "S2", 1: "S1"}
    assert {l["how"] for l in links[2:]} == {"voice"}
    assert speakers["S1"]["speech_sec"] == 40.0


def test_new_speaker_joins_midway():
    speakers, links = seeded()
    mapping = link_chunk(speakers, links, 1, {
        0: LocalSpeaker(0, unit(0), 10.0, 600.0),
        1: LocalSpeaker(1, unit(1), 10.0, 601.0),
        2: LocalSpeaker(2, unit(2), 10.0, 655.5),
    }, {})
    assert mapping[2] == "S3"
    assert speakers["S3"]["first_seen"] == 655.5
    assert links[-1]["how"] == "new"


def test_link_speaker_without_print():
    speakers, links = seeded()
    mapping = link_chunk(speakers, links, 1, {
        0: LocalSpeaker(0, unit(0), 10.0, 600.0),
        1: LocalSpeaker(1, None, 1.2, 610.0),
    }, {})
    assert mapping == {0: "S1", 1: "S3"}
    link = [l for l in links if l["local"] == 1 and l["chunk"] == 1][0]
    assert link["how"] == "new-noprint"
    assert speakers["S3"]["voiceprint"] is None


def test_overlap_votes_mutual_best():
    prev = [
        {"start": 560.0, "end": 580.0, "speaker": "S1", "text": "a"},
        {"start": 580.0, "end": 600.0, "speaker": "S2", "text": "b"},
    ]
    nxt = [
        {"start": 560.5, "end": 579.5, "speaker_id": 1, "text": "a"},
        {"start": 580.5, "end": 599.0, "speaker_id": 0, "text": "b"},
    ]
    assert overlap_votes(prev, nxt, (555.0, 600.0)) == {1: "S1", 0: "S2"}


def test_overlap_vote_below_min_ignored():
    prev = [{"start": 560.0, "end": 562.0, "speaker": "S1", "text": "a"}]
    nxt = [{"start": 560.0, "end": 562.0, "speaker_id": 0, "text": "a"}]
    assert overlap_votes(prev, nxt, (555.0, 600.0)) == {}


def test_one_to_one():
    speakers, links = {}, []
    link_chunk(speakers, links, 0, {0: LocalSpeaker(0, unit(0), 30.0, 0.0)}, {})
    mapping = link_chunk(speakers, links, 1, {
        0: LocalSpeaker(0, mixed(0.95), 10.0, 600.0),
        1: LocalSpeaker(1, mixed(0.8), 10.0, 601.0),
    }, {})
    assert mapping == {0: "S1", 1: "S2"}
    assert links[-1]["how"] == "new"
    assert len(set(mapping.values())) == 2


def test_consolidate_merges_split_person():
    speakers = {
        "S1": entry(0.0, unit(0, 0)),
        "S2": entry(3.0, unit(2, 0)),
        "S3": entry(900.0, mixed(0.9)),
    }
    merged = consolidate(speakers)
    assert len(merged) == 1
    assert merged[0][:2] == ("S3", "S1")
    assert speakers["S3"]["merged_into"] == "S1"
    assert speakers["S1"]["speech_sec"] == 20.0


def test_consolidate_keeps_distinct():
    speakers = {"S1": entry(0.0, unit(0, 0)), "S2": entry(5.0, mixed(0.6))}
    assert consolidate(speakers) == []
    assert "merged_into" not in speakers["S2"]


def test_assign_labels_skip_merged():
    speakers = {
        "S1": entry(0.0, None),
        "S3": entry(50.0, None, merged_into="S1", merge_similarity=0.8),
        "S4": entry(100.0, None),
    }
    assign_labels(speakers)
    assert speakers["S1"]["label_no"] == 1
    assert speakers["S4"]["label_no"] == 2
    assert "label_no" not in speakers["S3"]
