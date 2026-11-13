import json
import os
from pathlib import Path

import pytest

import groups
import observe
import paths

NOW = "2026-11-06T12:00:00+09:00"


def group(gid, target_type, obs, finding="missing", target_path="/x/skills/a/SKILL.md", **extra):
    g = {"id": gid, "target_type": target_type, "target_path": target_path, "problem": "문제",
         "observations": obs,
         "verified": {"checked_path": target_path, "finding": finding},
         "specific_facts": ["skills/a/SKILL.md 3단계"], "failure_ref": obs[0],
         "proposal": {"scope": "3단계", "opinion": "의견", "diff": "+ 줄"}}
    g.update(extra)
    return g


def result(*gs):
    return {"groups": list(gs)}


def ids(out, key):
    return [x if isinstance(x, str) else x["id"] for x in out[key]]


def test_script_reports_at_two_in_two_sessions(obs_factory):
    obs = [obs_factory("repeat", session="s1") for _ in range(2)]
    groups.merge(result(group("g", "script", obs)))
    out = groups.gate(NOW)
    assert ids(out, "held") == ["g"]
    obs2 = [obs_factory("repeat", session="s2")]
    groups.merge(result(group("g", "script", obs2)))
    out = groups.gate(NOW)
    assert ids(out, "report") == ["g"]
    rep = out["report"][0]
    assert rep["count"] == 3 and rep["sessions"] == 2 and len(rep["observations"]) == 3


def test_script_two_observations_in_two_sessions_reports(obs_factory):
    obs = [obs_factory("repeat", session="s1"), obs_factory("repeat", session="s2")]
    groups.merge(result(group("g", "script", obs)))
    assert ids(groups.gate(NOW), "report") == ["g"]


def test_correction_supplement_needs_two_sessions(obs_factory):
    a = obs_factory("correction", session="s1")
    groups.merge(result(group("g", "skill_supplement", [a], finding="no_instruction")))
    assert ids(groups.gate(NOW), "held") == ["g"]
    b = obs_factory("correction", session="s1")
    groups.merge(result(group("g", "skill_supplement", [b], finding="no_instruction")))
    assert ids(groups.gate(NOW), "held") == ["g"]
    b = obs_factory("correction", session="s2")
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
    old = obs_factory("correction", session="s1", at="2026-10-06T11:00:00+09:00")
    new = obs_factory("correction", session="s2", at="2026-11-05T10:00:00+09:00")
    groups.merge(result(group("g", "skill_supplement", [old, new], finding="no_instruction")))
    out = groups.gate(NOW)
    assert ids(out, "held") == ["g"]


def test_gate_has_no_notices_or_rework(obs_factory):
    a = obs_factory("failure")
    groups.merge(result(group("f", "fix", [a], finding="wrong", target_path="/nowhere/SKILL.md")))
    out = groups.gate(NOW)
    assert set(out) == {"report", "held", "dropped"}
    assert ids(out, "report") == ["f"]


def test_merge_rejects_false_or_unknown_observations(obs_factory):
    t = obs_factory("failure")
    f = obs_factory("failure", verdict=False)
    with pytest.raises(ValueError):
        groups.merge(result(group("g", "fix", [t, f], finding="wrong")))
    with pytest.raises(ValueError):
        groups.merge(result(group("g", "fix", [t, "없는-id"], finding="wrong")))
    with pytest.raises(ValueError):
        groups.merge(result({"id": "g", "target_type": "fix"}))
    assert groups.load() == {}


def test_merge_rejects_rework_target(obs_factory):
    t = obs_factory("failure")
    with pytest.raises(ValueError):
        groups.merge(result(group("g", "rework", [t], rework_kind="recurrence", fix_version="abc")))
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


def test_any_decision_drops_group_as_decided(obs_factory, monkeypatch):
    a, b = obs_factory("failure"), obs_factory("failure")
    groups.merge(result(group("r", "fix", [a], finding="wrong"), group("t", "fix", [b], finding="wrong")))
    monkeypatch.setattr(groups, "_last_decisions", lambda: {
        "r": {"group": "r", "decision": "reject", "kind": "trivial", "at": NOW},
        "t": {"group": "t", "decision": "accept", "at": NOW}})
    out = groups.gate(NOW)
    assert out["dropped"] == [{"id": "r", "reason": "decided"}, {"id": "t", "reason": "decided"}]
    assert out["held"] == [] and out["report"] == [] and "waiting" not in out


def test_merge_rejects_empty_group_id(obs_factory):
    a = obs_factory("failure")
    g = group("x", "fix", [a], finding="wrong", problem="!!!")
    del g["id"]
    with pytest.raises(ValueError, match="id"):
        groups.merge(result(g))
    with pytest.raises(ValueError, match="id"):
        groups.merge(result(group("", "fix", [a], finding="wrong")))
    assert groups.load() == {}


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


def test_decision_pattern_reports_at_two_in_two_sessions(obs_factory):
    a, b = obs_factory("decision_pattern", session="s1"), obs_factory("decision_pattern", session="s2")
    groups.merge(result(group("g", "skill_supplement", [a, b], finding="missing")))
    assert ids(groups.gate(NOW), "report") == ["g"]


def test_decision_pattern_one_session_is_held(obs_factory):
    a, b = obs_factory("decision_pattern", session="s1"), obs_factory("decision_pattern", session="s1")
    groups.merge(result(group("g", "skill_supplement", [a, b], finding="missing")))
    assert ids(groups.gate(NOW), "held") == ["g"]


def test_decision_pattern_fix_asks_user_uses_pattern_threshold(obs_factory):
    a = obs_factory("decision_pattern", session="s1")
    groups.merge(result(group("g", "fix", [a], finding="asks_user")))
    assert ids(groups.gate(NOW), "held") == ["g"]
    b = obs_factory("decision_pattern", session="s2")
    groups.merge(result(group("g", "fix", [b], finding="asks_user")))
    assert ids(groups.gate(NOW), "report") == ["g"]


def test_conflicting_choice_drops(obs_factory):
    a, b = obs_factory("decision_pattern", session="s1"), obs_factory("decision_pattern", session="s2")
    c = obs_factory("decision_pattern", session="s3")
    groups.merge(result(group("g", "skill_supplement", [a, b], finding="missing", conflicts=[c])))
    assert groups.gate(NOW)["dropped"] == [{"id": "g", "reason": "conflicting_choice"}]


def test_conflicts_with_only_false_observation_does_not_drop(obs_factory):
    a, b = obs_factory("decision_pattern", session="s1"), obs_factory("decision_pattern", session="s2")
    group_ = group("g", "skill_supplement", [a, b], finding="missing", conflicts=[])
    groups.merge(result(group_))
    assert ids(groups.gate(NOW), "report") == ["g"]


def test_merge_rejects_conflicts_not_true_observations(obs_factory):
    a = obs_factory("decision_pattern")
    f = obs_factory("decision_pattern", verdict=False)
    for bad in (f, "없는-id"):
        with pytest.raises(ValueError, match="conflicts"):
            groups.merge(result(group("g", "skill_supplement", [a], finding="missing", conflicts=[bad])))
    assert groups.load() == {}


def test_environment_finding_drops(obs_factory):
    a = obs_factory("failure")
    groups.merge(result(group("g", "fix", [a], finding="environment")))
    assert groups.gate(NOW)["dropped"] == [{"id": "g", "reason": "environment"}]


def test_general_decision_pattern_two_projects_project_target_is_scope(obs_factory):
    a = obs_factory("decision_pattern", session="s1", cwd="/w/org/proj-a")
    b = obs_factory("decision_pattern", session="s2", cwd="/w/org/proj-b")
    groups.merge(result(group("g", "guideline_supplement", [a, b], target_path="/w/org/proj-a/CLAUDE.md")))
    assert groups.gate(NOW)["dropped"] == [{"id": "g", "reason": "scope"}]


def test_decision_pattern_guideline_opposite_is_fix_only(obs_factory):
    a, b = obs_factory("decision_pattern", session="s1"), obs_factory("decision_pattern", session="s2")
    groups.merge(result(group("g", "fix", [a, b], finding="opposite"),
                        group("h", "guideline_supplement", [a, b], finding="opposite",
                              target_path="/w/org/proj-a/CLAUDE.md")))
    out = groups.gate(NOW)
    assert ids(out, "report") == ["g"] and out["dropped"] == [{"id": "h", "reason": "inconsistent"}]


def test_asks_user_and_opposite_are_allowed_for_fix_only():
    assert groups.ALLOWED["asks_user"] == {"fix"} and groups.ALLOWED["opposite"] == {"fix"}
    assert groups.ALLOWED["environment"] == set()


def test_conflict_outside_window_does_not_drop(obs_factory):
    a, b = obs_factory("decision_pattern", session="s1"), obs_factory("decision_pattern", session="s2")
    old = obs_factory("decision_pattern", session="s3", at="2026-10-06T11:00:00+09:00")
    groups.merge(result(group("g", "skill_supplement", [a, b], finding="missing", conflicts=[old])))
    assert ids(groups.gate(NOW), "report") == ["g"]


def test_decision_pattern_counts_by_rule(obs_factory):
    a = obs_factory("decision_pattern", session="s1", rule="A 이면 X")
    b = obs_factory("decision_pattern", session="s2", rule="B 이면 Y")
    groups.merge(result(group("g", "skill_supplement", [a, b], finding="missing")))
    assert ids(groups.gate(NOW), "held") == ["g"]
    c = obs_factory("decision_pattern", session="s2", rule="A 이면 X")
    groups.merge(result(group("g", "skill_supplement", [c], finding="missing")))
    assert ids(groups.gate(NOW), "report") == ["g"]


def test_merge_dedupes_observations_on_new_group(obs_factory):
    a = obs_factory("failure")
    groups.merge(result(group("g", "fix", [a, a], finding="wrong")))
    assert groups.load()["g"]["observations"] == [a]


def test_decided_checked_before_unverified(obs_factory, monkeypatch):
    a = obs_factory("failure")
    groups.merge(result(group("g", "fix", [a], verified=None)))
    monkeypatch.setattr(groups, "_last_decisions", lambda: {"g": {"group": "g", "decision": "accept", "at": NOW}})
    assert groups.gate(NOW)["dropped"] == [{"id": "g", "reason": "decided"}]
