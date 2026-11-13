import json
from pathlib import Path

import pytest

import decide
import groups
import paths
import rubric
from .test_groups import NOW, group  # noqa: F401

PLUGIN = Path(__file__).resolve().parents[1]


def ids(split, n, prefix="g"):
    out, i = [], 0
    while len(out) < n:
        i += 1
        if decide.split(f"{prefix}{i}") == split:
            out.append(f"{prefix}{i}")
    return out


def decision(gid, choice, kind=None, at="2026-11-01T00:00:00+09:00"):
    paths.append_line(paths.data_dir() / "decisions.jsonl",
                      {"group": gid, "decision": choice, "kind": kind, "note": f"사유 {gid}", "at": at})


@pytest.fixture
def labels_mix():
    train = ids("train", 4, "t")
    test = ids("test", 2, "x")
    for g in train[:3]:
        decision(g, "reject", "trivial")
    decision(train[3], "reject", "not-defect")
    decision(test[0], "reject", "trivial")
    decision(test[1], "accept")
    return train, test


def last_line(p):
    return p.read_text().splitlines()[-1]


def test_version_default_one():
    assert rubric.version("본문") == 1
    assert rubric.version("<!-- rubric-version: 5 -->\n") == 5


def test_candidates_train_only_and_ready_flag(labels_mix):
    train, test = labels_mix
    out = rubric.candidates()
    assert [c["group"] for c in out["groups"]["trivial"]] == train[:3]
    assert [c["group"] for c in out["groups"]["not-defect"]] == [train[3]]
    assert out["groups"]["trivial"][0]["note"] == f"사유 {train[0]}"
    assert out["ready"] == {"not-defect": False, "trivial": True}
    rubric.apply("trivial", "규칙", train[:3], dropped_positives=[], confirmed=False)
    assert rubric.candidates()["groups"]["trivial"] == []


def test_apply_rejects_test_split_and_reused_evidence(labels_mix):
    train, test = labels_mix
    with pytest.raises(ValueError, match=test[0]):
        rubric.apply("trivial", "규칙", train[:2] + [test[0]], dropped_positives=[], confirmed=False)
    assert rubric.apply("trivial", "규칙", train[:3], dropped_positives=[], confirmed=False) == 2
    with pytest.raises(ValueError, match=train[0]):
        rubric.apply("trivial", "또 규칙", train[:3], dropped_positives=[], confirmed=False)


def test_apply_needs_enough_evidence_and_matching_kind(labels_mix):
    train, _ = labels_mix
    with pytest.raises(ValueError, match="3"):
        rubric.apply("trivial", "규칙", train[:2], dropped_positives=[], confirmed=False)
    with pytest.raises(ValueError, match=train[3]):
        rubric.apply("trivial", "규칙", train, dropped_positives=[], confirmed=False)


def test_apply_writes_section_and_log(labels_mix):
    train, _ = labels_mix
    rubric.apply("trivial", "규칙 하나", train[:3], dropped_positives=[], confirmed=False)
    text = (paths.data_dir() / "rubric.md").read_text()
    assert text.startswith("<!-- rubric-version: 2 -->\n")
    assert text.rstrip().endswith("## 결정 대상으로 올리지 않는 경우\n\n- 규칙 하나")
    assert "## 관찰로 보지 않는 경우\n\n## 결정 대상" in text
    rec = json.loads(last_line(paths.data_dir() / "rubric-changes.jsonl"))
    assert rec["version"] == 2 and rec["evidence"] == train[:3] and rec["confirmed"] is False


def test_apply_inserts_into_middle_section():
    ev = ids("train", 3, "nd")
    for g in ev:
        decision(g, "reject", "not-defect")
    rubric.apply("not-defect", "규칙 가", ev, dropped_positives=[], confirmed=False)
    text = (paths.data_dir() / "rubric.md").read_text()
    assert "## 관찰로 보지 않는 경우\n\n- 규칙 가\n\n## 결정 대상" in text


def test_apply_requires_confirmation_when_positives_drop(labels_mix):
    train, _ = labels_mix
    with pytest.raises(ValueError, match="확인"):
        rubric.apply("trivial", "규칙", train[:3], dropped_positives=["g1"], confirmed=False)
    assert rubric.apply("trivial", "규칙", train[:3], dropped_positives=["g1"], confirmed=True) == 2
    rec = json.loads(last_line(paths.data_dir() / "rubric-changes.jsonl"))
    assert rec["confirmed"] is True and rec["dropped_positives"] == ["g1"]


def test_apply_out_writes_only_there(labels_mix, tmp_path):
    train, _ = labels_mix
    out = tmp_path / "cand.md"
    assert rubric.apply("trivial", "규칙", train[:3], dropped_positives=[], confirmed=False, out=out) == 2
    assert out.read_text().startswith("<!-- rubric-version: 2 -->")
    assert (paths.data_dir() / "rubric.md").read_text().startswith("<!-- rubric-version: 1 -->")
    assert not (paths.data_dir() / "rubric-changes.jsonl").exists()


def test_replay_score_regression_and_eligibility(labels_mix):
    train, test = labels_mix
    pt = ids("train", 1, "pt")[0]
    decision(pt, "accept")
    pos = [pt, test[1]]
    cur = {"rubric_version": 1, "results": [{"group": g, "surfaces": True} for g in pos + [test[0]]]}
    cand = {"rubric_version": 2, "results": [{"group": test[1], "surfaces": False}, {"group": pt, "surfaces": True},
                                              {"group": test[0], "surfaces": False}]}
    out = rubric.replay_score(cur, cand)
    assert out["dropped_positives"] == [test[1]]
    assert out["eligible"] is False
    assert out["current"] == {"tpr": 1.0, "tnr": 0.0, "n_pos": 1, "n_neg": 1}
    assert out["candidate"] == {"tpr": 0.0, "tnr": 1.0, "n_pos": 1, "n_neg": 1}


def test_replay_score_eligible_at_min_labels():
    for g in ids("test", 30, "e"):
        decision(g, "accept")
    assert rubric.replay_score({"rubric_version": 1, "results": []},
                               {"rubric_version": 2, "results": []})["eligible"] is True


def test_replay_score_validates_schema():
    with pytest.raises(ValueError):
        rubric.replay_score({"rubric_version": 1, "results": [{"group": "g"}]}, {"rubric_version": 1, "results": []})


def test_gate_one_uses_rejudged_without_labels(obs_factory):
    a = obs_factory("failure", at="2025-01-01T00:00:00+09:00")
    archived = group("old", "fix", [a], finding="wrong")
    obs = {"evidence_id": a, "type": "failure", "verdict": True, "reason": "r", "at": "2025-01-01T00:00:00+09:00",
           "session": "s1", "project": "p"}
    assert rubric.gate_one(archived, {"group": {}, "observations": [obs]}) is True
    assert rubric.gate_one(archived, {"group": {}, "observations": [{**obs, "verdict": False}]}) is False
    assert rubric.gate_one(archived, {"group": {"verified": None}, "observations": [obs]}) is False
    decision("old", "reject", "trivial")
    assert rubric.gate_one(archived, {"group": {}, "observations": [obs]}) is True


def test_default_rubric_has_sections_and_exclusions():
    text = (PLUGIN / "defaults/rubric.md").read_text()
    assert text.startswith("<!-- rubric-version: 1 -->\n")
    for s in ("## 관찰로 보지 않는 경우", "## 결정 대상으로 올리지 않는 경우", "push", "티켓 생성", "환경 원인",
              "바꿀 파일이 없는 것", "정리 제안", "구체성", "실패 근거", "수정 방식 재검토"):
        assert s in text
