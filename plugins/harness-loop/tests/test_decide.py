import json

import pytest

import decide
import groups
import paths
from .test_groups import NOW, group, result


def merge(gid, target_type, obs, target_path, **extra):
    groups.merge(result(group(gid, target_type, obs, target_path=target_path, **extra)))


@pytest.fixture
def g(obs_factory):
    a = obs_factory("failure")
    merge("g", "fix", [a], "/x/skills/a/SKILL.md", finding="wrong")
    return "g"


def test_reject_requires_kind(g):
    with pytest.raises(ValueError):
        decide.record(g, "reject")
    with pytest.raises(ValueError):
        decide.record(g, "reject", kind="bogus")
    assert decide.record(g, "reject", kind="trivial", reason="사소")["kind"] == "trivial"


def test_accept_records_target_type_and_archives(g):
    rec = decide.record(g, "accept")
    assert rec["decision"] == "accept" and rec["target_type"] == groups.load()[g]["target_type"]
    assert set(rec) == {"group", "decision", "kind", "note", "target_type", "at", "rubric_version"}
    assert rec["rubric_version"] == 1
    assert decide.last_decisions()[g]["decision"] == "accept"
    data = json.loads((paths.data_dir() / "replay" / g / "input.json").read_text())
    assert set(data) == {"group", "observations", "target_file", "rubric"}
    assert data["target_file"]["path"] == "/x/skills/a/SKILL.md" and data["target_file"]["text"] is None
    assert data["rubric"]["version"] == 1 and "판정 기준" in data["rubric"]["text"]
    assert data["observations"][0]["evidence"]["id"] == data["group"]["observations"][0]


def test_archive_stores_target_text_at_decision_time(obs_factory, tmp_path):
    skill = tmp_path / "skills/a/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("결정 시점\n")
    merge("t", "fix", [obs_factory("failure")], str(skill), finding="wrong")
    decide.record("t", "accept")
    data = json.loads((paths.data_dir() / "replay" / "t" / "input.json").read_text())
    assert data["target_file"] == {"path": str(skill), "text": "결정 시점\n"}


def test_choices_are_accept_and_reject(g):
    assert decide.CHOICES == ("accept", "reject")
    for old in ("proceed", "ticket"):
        with pytest.raises(ValueError):
            decide.record(g, old)


def test_failed_archive_records_no_decision(g, monkeypatch):
    def boom(gid):
        raise OSError("disk")

    monkeypatch.setattr(decide, "archive", boom)
    with pytest.raises(OSError):
        decide.record(g, "accept")
    assert decide.last_decisions() == {}


def test_rubric_version_from_data_rubric(g):
    (paths.data_dir() / "rubric.md").write_text("<!-- rubric-version: 4 -->\n본문\n")
    assert decide.record(g, "accept")["rubric_version"] == 4


def _decide_all(obs_factory, rows):
    for gid, target_type, choice, kind in rows:
        merge(gid, target_type, [obs_factory("repeat" if target_type == "script" else "failure")],
              "/x/skills/a/SKILL.md", finding="missing" if target_type == "script" else "wrong")
        decide.record(gid, choice, kind=kind)


def test_labels(obs_factory):
    _decide_all(obs_factory, [("a", "fix", "accept", None), ("c", "fix", "reject", "trivial"),
                              ("d", "fix", "reject", "wont-fix"), ("e", "fix", "reject", "not-defect")])
    assert {k: v["label"] for k, v in decide.labels().items()} == {
        "a": "positive", "c": "negative", "d": "excluded", "e": "negative"}


def test_metrics_overall_by_target_and_rubric_version(obs_factory):
    _decide_all(obs_factory, [("a", "script", "accept", None), ("b", "fix", "accept", None),
                              ("c", "script", "reject", "not-defect"), ("d", "fix", "reject", "wont-fix")])
    m = decide.metrics()
    assert m["overall"] == {"accepted": 2, "rejected": 1, "wont_fix": 1, "rate": 2 / 3}
    assert m["by_target"]["script"] == {"accepted": 1, "rejected": 1, "wont_fix": 0, "rate": 0.5}
    assert m["by_target"]["fix"] == {"accepted": 1, "rejected": 0, "wont_fix": 1, "rate": 1.0}
    assert m["by_rubric_version"]["1"] == {"accepted": 2, "rejected": 1, "wont_fix": 1, "rate": 2 / 3}


def test_metrics_rate_none_without_labels():
    assert decide.metrics() == {"overall": {"accepted": 0, "rejected": 0, "wont_fix": 0, "rate": None},
                                "by_target": {}, "by_rubric_version": {}}


def test_split_is_stable_and_two_valued():
    assert {decide.split(f"g{i}") for i in range(40)} == {"train", "test"}
    assert decide.split("g1") == decide.split("g1")


@pytest.mark.parametrize("choice,kind", [("accept", None), ("reject", "trivial")])
def test_decided_group_not_reported_even_with_new_observations(g, obs_factory, choice, kind):
    decide.record(g, choice, kind=kind)
    new = obs_factory("failure")
    store = groups.load()
    merge("g", "fix", store[g]["observations"] + [new], "/x/skills/a/SKILL.md", finding="wrong")
    out = groups.gate(NOW)
    assert g not in [r["id"] for r in out["report"]] and g not in out["held"]
    assert {"id": g, "reason": "decided"} in out["dropped"]
