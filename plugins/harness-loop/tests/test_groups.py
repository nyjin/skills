import json
import os
import subprocess
from pathlib import Path

import pytest

import groups
import observe
import paths

NOW = "2026-11-06T12:00:00+09:00"


def git(repo, *args, env=None):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
                       env={**os.environ, **(env or {})})
    return r.stdout


def commit(repo, msg, when):
    git(repo, "commit", "-qam", msg, env={"GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when})


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "skills/a").mkdir(parents=True)
    git(r, "init", "-q")
    git(r, "config", "user.email", "t@t")
    git(r, "config", "user.name", "t")
    (r / "skills/a/SKILL.md").write_text("v1\n")
    git(r, "add", ".")
    commit(r, "init", "2026-10-20T00:00:00+00:00")
    return r


def group(gid, target_type, obs, finding="missing", target_path="/x/skills/a/SKILL.md", **extra):
    g = {"id": gid, "target_type": target_type, "target_path": target_path, "problem": "문제",
         "observations": obs,
         "verified": {"checked_path": target_path, "hash": "h", "finding": finding},
         "specific_facts": ["skills/a/SKILL.md 3단계"], "failure_ref": obs[0],
         "proposal": {"scope": "3단계", "opinion": "의견", "diff": "+ 줄"}}
    g.update(extra)
    return g


def result(*gs):
    return {"groups": list(gs)}


def ids(out, key):
    return [x if isinstance(x, str) else x["id"] for x in out[key]]


def test_script_needs_three_in_two_sessions(obs_factory):
    obs = [obs_factory("repeat", session="s1") for _ in range(3)]
    groups.merge(result(group("g", "script", obs)))
    out = groups.gate(NOW)
    assert ids(out, "held") == ["g"]
    obs2 = [obs_factory("repeat", session="s2")]
    groups.merge(result(group("g", "script", obs2)))
    out = groups.gate(NOW)
    assert ids(out, "report") == ["g"]
    rep = out["report"][0]
    assert rep["count"] == 4 and rep["sessions"] == 2 and len(rep["observations"]) == 4


def test_correction_supplement_needs_two(obs_factory):
    a = obs_factory("correction")
    groups.merge(result(group("g", "skill_supplement", [a], finding="no_instruction")))
    assert ids(groups.gate(NOW), "held") == ["g"]
    b = obs_factory("correction")
    groups.merge(result(group("g", "skill_supplement", [b], finding="no_instruction")))
    assert ids(groups.gate(NOW), "report") == ["g"]


def test_approval_question_reports_on_one(obs_factory):
    a = obs_factory("approval_question")
    b = obs_factory("approval_question")
    groups.merge(result(group("s", "skill_supplement", [a], finding="no_instruction"),
                        group("f", "fix", [b], finding="asks_here")))
    assert sorted(ids(groups.gate(NOW), "report")) == ["f", "s"]


def test_unverified_group_is_dropped(obs_factory):
    a = obs_factory("failure")
    groups.merge(result(group("g", "fix", [a], verified=None)))
    assert groups.gate(NOW)["dropped"] == [{"id": "g", "reason": "unverified"}]


def test_generic_proposal_without_specific_facts_dropped(obs_factory):
    a = obs_factory("failure")
    b = obs_factory("failure")
    groups.merge(result(group("g", "fix", [a], finding="wrong", specific_facts=[]),
                        group("h", "fix", [b], finding="wrong", failure_ref="없는-id")))
    dropped = {d["id"]: d["reason"] for d in groups.gate(NOW)["dropped"]}
    assert dropped == {"g": "not_specific", "h": "no_failure_ref"}


def test_guideline_global_needs_two_projects(obs_factory):
    home_md = str(Path.home() / ".claude/CLAUDE.md")
    a = obs_factory("correction", session="s1", cwd="/w/org/proj-a")
    b = obs_factory("correction", session="s2", cwd="/w/org/proj-a")
    groups.merge(result(group("g", "guideline_supplement", [a, b], target_path=home_md)))
    assert groups.gate(NOW)["dropped"] == [{"id": "g", "reason": "scope"}]
    c = obs_factory("correction", session="s3", cwd="/w/org/proj-b")
    groups.merge(result(group("g", "guideline_supplement", [c], target_path=home_md)))
    out = groups.gate(NOW)
    assert ids(out, "report") == ["g"] and out["report"][0]["projects"] == 2


def test_guideline_project_needs_two_sessions(obs_factory):
    a = obs_factory("correction", session="s1")
    b = obs_factory("correction", session="s1")
    groups.merge(result(group("g", "guideline_supplement", [a, b], target_path="/w/org/proj-a/CLAUDE.md")))
    assert ids(groups.gate(NOW), "held") == ["g"]


def test_window_excludes_old_observations(obs_factory):
    old = obs_factory("correction", at="2026-10-06T11:00:00+09:00")
    new = obs_factory("correction", at="2026-11-05T10:00:00+09:00")
    groups.merge(result(group("g", "skill_supplement", [old, new], finding="no_instruction")))
    out = groups.gate(NOW)
    assert ids(out, "held") == ["g"]


def _skill(repo):
    path = repo / "skills/a"
    return {"name": "a", "path": str(path), "hash": git(repo, "hash-object", str(path / "SKILL.md")).strip()}


def test_rework_recurrence_only_after_fix_version(repo, obs_factory):
    target = str(repo / "skills/a/SKILL.md")
    v1 = obs_factory("correction", at="2026-10-25T10:00:00+09:00", skill=_skill(repo))
    groups.merge(result(group("g", "rework", [v1], target_path=target, rework_kind="recurrence",
                              fix_version="0" * 40)))
    (repo / "skills/a/SKILL.md").write_text("v2\n")
    commit(repo, "fix", "2026-11-01T00:00:00+00:00")
    v2_hash = git(repo, "rev-parse", "HEAD:skills/a/SKILL.md").strip()
    groups.merge(result(group("g", "rework", [v1], target_path=target, rework_kind="recurrence",
                              fix_version=v2_hash)))
    assert ids(groups.gate(NOW), "held") == ["g"]
    v2 = obs_factory("correction", at="2026-11-03T10:00:00+09:00", skill=_skill(repo))
    groups.merge(result(group("g", "rework", [v2], target_path=target, rework_kind="recurrence",
                              fix_version=v2_hash)))
    out = groups.gate(NOW)
    assert ids(out, "report") == ["g"]
    assert out["report"][0]["recurrence"] == [v2]


def test_rework_repeat_edit_needs_edits_in_history(repo, obs_factory):
    target = str(repo / "skills/a/SKILL.md")
    hashes = [git(repo, "rev-parse", "HEAD:skills/a/SKILL.md").strip()]
    for i, day in ((2, "2026-10-21"), (3, "2026-10-22")):
        (repo / "skills/a/SKILL.md").write_text(f"v{i}\n")
        commit(repo, f"v{i}", f"{day}T00:00:00+00:00")
        hashes.append(git(repo, "rev-parse", "HEAD:skills/a/SKILL.md").strip())
    a = obs_factory("correction")
    groups.merge(result(group("g", "rework", [a], target_path=target, rework_kind="repeat_edit", edits=hashes[:2])))
    assert ids(groups.gate(NOW), "held") == ["g"]
    groups.merge(result(group("g", "rework", [a], target_path=target, rework_kind="repeat_edit", edits=hashes)))
    assert ids(groups.gate(NOW), "report") == ["g"]


def test_rework_untracked_dropped_with_notice(tmp_path, obs_factory):
    root = tmp_path / "plain/skills/a"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("x\n")
    a = obs_factory("correction")
    groups.merge(result(group("g", "rework", [a], target_path=str(root / "SKILL.md"), rework_kind="recurrence",
                              fix_version="abc", skill_root=str(root))))
    out = groups.gate(NOW)
    assert out["dropped"] == [{"id": "g", "reason": "untracked"}]
    assert len(out["notices"]) == 1
    assert out["notices"][0].startswith(f"버전 추적 불가: {root} 는 git 저장소가 아니라")
    assert out["notices"][0].endswith("해당 스킬: a")


def test_report_and_held_untracked_notice_once_per_root(tmp_path, obs_factory):
    root = tmp_path / "plain/skills/a"
    root.mkdir(parents=True)
    a = obs_factory("failure")
    b = obs_factory("correction")
    groups.merge(result(group("f", "fix", [a], finding="wrong", target_path=str(root / "SKILL.md"), skill_root=str(root)),
                        group("s", "skill_supplement", [b], finding="no_instruction",
                              target_path=str(root / "SKILL.md"), skill_root=str(root))))
    out = groups.gate(NOW)
    assert ids(out, "report") == ["f"] and out["held"] == ["s"]
    assert len(out["notices"]) == 1 and str(root) in out["notices"][0]


def test_merge_rejects_false_or_unknown_observations(obs_factory):
    t = obs_factory("failure")
    f = obs_factory("failure", verdict=False)
    with pytest.raises(ValueError):
        groups.merge(result(group("g", "fix", [t, f], finding="wrong")))
    with pytest.raises(ValueError):
        groups.merge(result(group("g", "fix", [t, "없는-id"], finding="wrong")))
    with pytest.raises(ValueError):
        groups.merge(result({"id": "g", "target_type": "fix"}))
    with pytest.raises(ValueError):
        groups.merge(result(group("g", "rework", [t])))
    assert groups.load() == {}


def test_merge_unions_observations_and_returns_changed_ids(obs_factory):
    a, b = obs_factory("failure"), obs_factory("failure")
    assert groups.merge(result(group("g", "fix", [a], finding="wrong"))) == ["g"]
    created = groups.load()["g"]["created"]
    assert groups.merge(result(group("g", "fix", [a], finding="wrong"))) == []
    assert groups.merge(result(group("g", "fix", [b], finding="wrong", problem="새 문제"))) == ["g"]
    g = groups.load()["g"]
    assert g["observations"] == [a, b] and g["problem"] == "새 문제" and g["created"] == created


def test_merge_without_id_uses_slug(obs_factory):
    a = obs_factory("failure")
    g = group("x", "fix", [a], finding="wrong", problem="Release ADR number")
    del g["id"]
    assert groups.merge(result(g)) == ["release-adr-number"]
    assert groups.slug("  Release ADR  번호!! ") == "release-adr-번호"


def test_inconsistent_finding_and_target_dropped(obs_factory):
    a, b, c = obs_factory("failure"), obs_factory("repeat"), obs_factory("correction")
    groups.merge(result(group("m", "fix", [a], finding="missing"),
                        group("h", "script", [b], finding="has_script"),
                        group("w", "skill_supplement", [c], finding="wrong")))
    dropped = {d["id"]: d["reason"] for d in groups.gate(NOW)["dropped"]}
    assert dropped == {"m": "inconsistent", "h": "inconsistent", "w": "inconsistent"}


def test_decisions_reject_and_ticket(obs_factory, monkeypatch):
    a, b = obs_factory("failure"), obs_factory("failure")
    groups.merge(result(group("r", "fix", [a], finding="wrong"), group("t", "fix", [b], finding="wrong")))
    monkeypatch.setattr(groups, "_last_decisions", lambda: {
        "r": {"group": "r", "decision": "reject", "kind": "trivial", "at": NOW},
        "t": {"group": "t", "decision": "ticket", "at": NOW}})
    out = groups.gate(NOW)
    assert out["dropped"] == [{"id": "r", "reason": "rejected"}] and out["held"] == ["t"]


def test_proceed_counts_only_later_observations(obs_factory, monkeypatch):
    a = obs_factory("failure", at="2026-11-01T10:00:00+09:00")
    groups.merge(result(group("g", "fix", [a], finding="wrong")))
    monkeypatch.setattr(groups, "_last_decisions", lambda: {
        "g": {"group": "g", "decision": "proceed", "at": "2026-11-02T00:00:00+09:00", "baseline_hash": "h"}})
    assert ids(groups.gate(NOW), "held") == ["g"]
    b = obs_factory("failure", at="2026-11-03T10:00:00+09:00")
    groups.merge(result(group("g", "fix", [b], finding="wrong")))
    assert ids(groups.gate(NOW), "report") == ["g"]


def test_last_decisions_placeholder_is_empty():
    assert groups._last_decisions() == {}


def test_guideline_project_target_with_two_projects_is_scope(obs_factory):
    a = obs_factory("correction", session="s1", cwd="/w/org/proj-a")
    b = obs_factory("correction", session="s2", cwd="/w/org/proj-b")
    groups.merge(result(group("g", "guideline_supplement", [a, b], target_path="/w/org/proj-a/CLAUDE.md")))
    assert groups.gate(NOW)["dropped"] == [{"id": "g", "reason": "scope"}]


def test_skill_supplement_counts_only_corrections(obs_factory):
    a, b = obs_factory("failure"), obs_factory("failure")
    groups.merge(result(group("g", "skill_supplement", [a, b], finding="no_instruction")))
    assert ids(groups.gate(NOW), "held") == ["g"]


def test_repeat_edit_counts_distinct_edits(repo, obs_factory):
    target = str(repo / "skills/a/SKILL.md")
    h1 = git(repo, "rev-parse", "HEAD:skills/a/SKILL.md").strip()
    (repo / "skills/a/SKILL.md").write_text("v2\n")
    commit(repo, "v2", "2026-10-21T00:00:00+00:00")
    h2 = git(repo, "rev-parse", "HEAD:skills/a/SKILL.md").strip()
    a = obs_factory("correction")
    groups.merge(result(group("g", "rework", [a], target_path=target, rework_kind="repeat_edit", edits=[h1, h1, h2])))
    assert ids(groups.gate(NOW), "held") == ["g"]


def test_rework_tilde_target_is_expanded(obs_factory):
    home_repo = Path.home() / "r"
    (home_repo / "skills/a").mkdir(parents=True)
    git(home_repo, "init", "-q")
    git(home_repo, "config", "user.email", "t@t")
    git(home_repo, "config", "user.name", "t")
    (home_repo / "skills/a/SKILL.md").write_text("v1\n")
    git(home_repo, "add", ".")
    commit(home_repo, "init", "2026-10-20T00:00:00+00:00")
    v1 = git(home_repo, "rev-parse", "HEAD:skills/a/SKILL.md").strip()
    a = obs_factory("correction", skill=_skill(home_repo))
    groups.merge(result(group("g", "rework", [a], target_path="~/r/skills/a/SKILL.md", rework_kind="recurrence",
                              fix_version=v1)))
    out = groups.gate(NOW)
    assert ids(out, "report") == ["g"] and out["report"][0]["recurrence"] == [a] and out["notices"] == []


def test_untracked_notice_expands_tilde(obs_factory):
    (Path.home() / "plain").mkdir(parents=True)
    a = obs_factory("failure")
    groups.merge(result(group("g", "fix", [a], finding="wrong", target_path="~/plain/SKILL.md")))
    out = groups.gate(NOW)
    assert ids(out, "report") == ["g"] and str(Path.home() / "plain") in out["notices"][0]


def test_rework_recurrence_after_proceed_decision(repo, obs_factory, monkeypatch):
    # v1 → v2(baseline) → v1 로 되돌림: history 는 v1 을 첫 위치로만 남기므로 순서 규칙이면 재발을 놓친다
    target = str(repo / "skills/a/SKILL.md")
    (repo / "skills/a/SKILL.md").write_text("v2\n")
    commit(repo, "v2", "2026-10-25T00:00:00+00:00")
    base = git(repo, "rev-parse", "HEAD:skills/a/SKILL.md").strip()
    (repo / "skills/a/SKILL.md").write_text("v1\n")
    commit(repo, "revert", "2026-10-28T00:00:00+00:00")
    before = obs_factory("correction", at="2026-11-01T10:00:00+09:00", skill=_skill(repo))
    groups.merge(result(group("g", "rework", [before], target_path=target, rework_kind="recurrence")))
    monkeypatch.setattr(groups, "_last_decisions", lambda: {
        "g": {"group": "g", "decision": "proceed", "at": "2026-11-02T00:00:00+09:00", "baseline_hash": base}})
    assert ids(groups.gate(NOW), "held") == ["g"]
    later = obs_factory("correction", at="2026-11-03T10:00:00+09:00", skill=_skill(repo))
    groups.merge(result(group("g", "rework", [later], target_path=target, rework_kind="recurrence")))
    out = groups.gate(NOW)
    assert ids(out, "report") == ["g"] and out["report"][0]["recurrence"] == [later]
