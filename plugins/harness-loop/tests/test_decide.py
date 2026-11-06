import json
import os

import pytest

import decide
import groups
import paths
from .test_groups import NOW, commit, git, group, repo, result  # noqa: F401


def merge(gid, target_type, obs, target_path, **extra):
    groups.merge(result(group(gid, target_type, obs, target_path=target_path, **extra)))


@pytest.fixture
def g(obs_factory):
    a = obs_factory("failure")
    merge("g", "fix", [a], "/x/skills/a/SKILL.md", finding="wrong")
    return "g"


@pytest.fixture
def group_in_repo(repo, obs_factory):
    skill = repo / "skills/a/SKILL.md"
    h = decide.versions.blob_hash(skill)
    a = obs_factory("failure", skill={"path": str(skill.parent), "hash": h})
    merge("g", "fix", [a], str(skill), finding="wrong")
    return "g", skill, h


def test_reject_requires_kind(g):
    with pytest.raises(ValueError):
        decide.record(g, "reject")
    with pytest.raises(ValueError):
        decide.record(g, "reject", kind="bogus")
    assert decide.record(g, "reject", kind="trivial", reason="사소")["kind"] == "trivial"


def test_proceed_records_baseline_hash_and_archives(group_in_repo):
    gid, skill, h = group_in_repo
    rec = decide.record(gid, "proceed")
    assert rec["baseline_hash"] == h and rec["rubric_version"] == 1
    assert decide.last_decisions()[gid]["decision"] == "proceed"
    assert (paths.data_dir() / "replay" / gid / "input.json").exists()


def test_rubric_version_from_data_rubric(g):
    (paths.data_dir() / "rubric.md").write_text("<!-- rubric-version: 4 -->\n본문\n")
    assert decide.record(g, "ticket", ticket="IBS-1")["rubric_version"] == 4


def test_plugin_cache_target_cannot_proceed(obs_factory):
    target = os.path.expanduser("~/.claude/plugins/cache/m/p/skills/a/SKILL.md")
    merge("c", "fix", [obs_factory("failure")], target, finding="wrong")
    with pytest.raises(ValueError):
        decide.record("c", "proceed")
    assert decide.record("c", "ticket")["decision"] == "ticket"


def test_labels_and_precision(obs_factory):
    for gid, choice, kind in [("a", "proceed", None), ("b", "ticket", None),
                              ("c", "reject", "trivial"), ("d", "reject", "wont-fix")]:
        merge(gid, "fix", [obs_factory("failure")], "/x/skills/a/SKILL.md", finding="wrong")
        decide.record(gid, choice, kind=kind)
    assert {k: v["label"] for k, v in decide.labels().items()} == {
        "a": "positive", "b": "positive", "c": "negative", "d": "excluded"}
    p = decide.precision()
    assert (p["positive"], p["negative"]) == (2, 1) and p["precision"] == pytest.approx(2 / 3)


def test_precision_none_without_labels():
    assert decide.precision()["precision"] is None and decide.last_decisions() == {}


def test_split_is_stable_and_two_valued():
    assert {decide.split(f"g{i}") for i in range(40)} == {"train", "test"}
    assert decide.split("g1") == decide.split("g1")


def test_archive_contains_no_decision_and_file_versions(group_in_repo, obs_factory):
    gid, skill, h = group_in_repo
    decide.record(gid, "ticket", ticket="IBS-1")
    data = json.loads(decide.archive(gid).read_text(encoding="utf-8"))
    assert "decision" not in data["group"] and "ticket" not in data["group"]
    assert data["file_versions"] == {h: "v1\n"}
    assert data["rubric"]["version"] == 1 and "판정 기준" in data["rubric"]["text"]
    assert data["observations"][0]["evidence"]["id"] == data["group"]["observations"][0]


def test_archive_uses_snapshot_outside_git(obs_factory, tmp_path):
    skill = tmp_path / "loose/SKILL.md"
    skill.parent.mkdir()
    skill.write_text("로컬\n")
    h = decide.versions.remember(skill)
    a = obs_factory("failure", skill={"path": str(skill.parent), "hash": h})
    merge("l", "fix", [a], str(skill), finding="wrong")
    assert json.loads(decide.archive("l").read_text())["file_versions"] == {h: "로컬\n"}


def test_ticket_file_embeds_contents_not_paths(obs_factory, tmp_path):
    script = tmp_path / "s.txt"
    script.write_text("echo UNIQUE_SCRIPT_BODY\n")
    skill = tmp_path / "skills/a/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("\n".join(f"줄{i}" for i in range(1, 60)) + "\n")
    a = obs_factory("repeat")
    groups.merge(result(group("t", "script", [a], target_path=str(skill),
                              verified={"checked_path": str(skill), "hash": "h", "finding": "missing", "line": 30})))
    # 관찰의 file 을 스크립트 사본으로 지정
    obs_file = paths.data_dir() / "observations.jsonl"
    row = json.loads(obs_file.read_text())
    row["file"] = str(script)
    obs_file.write_text(json.dumps(row) + "\n")
    out = decide.ticket_file("t", tmp_path / "out")
    assert out == tmp_path / "out" / "t.md"
    body = out.read_text(encoding="utf-8")
    assert body.startswith("# [결정 대상] a — 문제")
    assert "UNIQUE_SCRIPT_BODY" in body and "줄15" in body and "줄45" in body and "줄46" not in body
    assert "+ 줄" in body and "이유" in body


def test_gate_reflects_decisions(g):
    decide.record(g, "reject", kind="not-defect")
    assert groups.gate(NOW)["dropped"] == [{"id": "g", "reason": "rejected"}]
