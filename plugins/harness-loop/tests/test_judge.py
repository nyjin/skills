import json
import shutil
from pathlib import Path

import pytest

import judge
import state

FIX = Path(__file__).parent / "fixtures"
EV = [{"session": "s", "turn": 1, "quote": "q"}]


def copy_fixture(root, name, sid):
    t = root / "proj" / f"{sid}.jsonl"
    t.parent.mkdir(exist_ok=True)
    shutil.copy(FIX / name, t)
    return t


def out_item(sig="C1:app-not-installed", **kw):
    item = {
        "sig": sig,
        "condition": sig.split(":")[0],
        "impact": 3,
        "actionability": 3,
        "evidence": [{"session": "sess-a", "turn": 1, "quote": "Error: the app is not installed for this site"}],
        "target": {"path": "/r/skills/ticket-creator/SKILL.md"},
        "proposal": "403 이면 앱 설치 안내",
        "duplicate_of_rule": None,
        "rule_unenforced": False,
        "skills": {"ticket-creator": 1},
    }
    item.update(kw)
    return item


def other_session(root, sid, signals=(), items=None):
    h = root / "other" / sid / "harness"
    h.mkdir(parents=True)
    if signals:
        (h / "signals.jsonl").write_text("".join(json.dumps({"sig": s, "line": i}) + "\n" for i, s in enumerate(signals)))
    if items is not None:
        state.save_items(h, items)
    return h


def test_score_thresholds():
    cfg = state.load_config()
    item = {"sig": "C1:a", "impact": 3, "actionability": 3, "evidence": EV, "target": {"path": "/r/x.md"}}
    assert judge.score({**item, "sessions": 4}, cfg) == (pytest.approx(3.0), "propose")   # 0.4·3+0.4·3+0.2·3 = 1.2+1.2+0.6
    assert judge.score({**item, "sessions": 1}, cfg) == (pytest.approx(2.2), "watch")     # 0.4·1+1.2+0.6
    assert judge.score({**item, "sessions": 1, "impact": 1, "actionability": 1}, cfg) == (pytest.approx(1.0), "discarded")  # 0.4+0.4+0.2


def test_recurrence_bands():
    cfg = state.load_config()
    item = {"sig": "C1:a", "impact": 1, "actionability": 1, "evidence": EV, "target": {"path": "/r/x.md"}}
    assert judge.score({**item, "sessions": 2}, cfg)[0] == pytest.approx(1.4)   # 0.4·2+0.4+0.2
    assert judge.score({**item, "sessions": 3}, cfg)[0] == pytest.approx(1.4)
    assert judge.score({**item, "sessions": 5}, cfg)[0] == pytest.approx(1.8)   # 0.4·3+0.4+0.2


def test_global_target_bumps_threshold():
    cfg = state.load_config()
    item = {"sig": "C7:a", "sessions": 4, "impact": 2, "actionability": 3, "evidence": EV}   # 1.2+0.8+0.6=2.6
    assert judge.score({**item, "target": {"path": "/r/x.md"}}, cfg)[1] == "propose"
    assert judge.score({**item, "target": {"path": "~/.claude/CLAUDE.md"}}, cfg)[1] == "watch"   # 기준 2.4+0.3=2.7
    assert judge.score({**item, "target": {"path": str(Path.home() / ".claude/CLAUDE.md")}}, cfg)[1] == "watch"
    assert judge.score({**item, "target": {"path": "/h/.claude/projects/p/memory/x.md"}}, cfg)[1] == "watch"
    low = {**item, "sessions": 1, "impact": 2, "actionability": 1}   # 0.4+0.8+0.2=1.4 < 1.6
    assert judge.score({**low, "sessions": 2, "target": {"path": "/r/x.md"}}, cfg)[1] == "watch"   # 0.8+0.8+0.2=1.8
    assert judge.score({**low, "sessions": 2, "target": {"path": "~/.claude/CLAUDE.md"}}, cfg)[1] == "discarded"   # 1.8 < 1.9


def test_duplicate_rule_discarded_unless_unenforced():
    cfg = state.load_config()
    item = {"sig": "C7:a", "sessions": 4, "impact": 3, "actionability": 3, "evidence": EV, "target": {"path": "/r/x.md"}, "duplicate_of_rule": "CLAUDE.md: 묻지 말 것"}
    assert judge.score({**item, "rule_unenforced": False}, cfg) == (pytest.approx(3.0), "discarded")
    assert judge.score({**item, "rule_unenforced": True}, cfg) == (pytest.approx(3.0), "propose")


def test_rejected_sig_stays_rejected(tmp_projects):
    cfg = state.load_config()
    item = {"sig": "C1:a", "sessions": 4, "impact": 3, "actionability": 3, "evidence": EV, "target": {"path": "/r/x.md"}}
    state.append_decision("C1:a", "rejected")
    assert judge.score(item, cfg) == (pytest.approx(3.0), "rejected")
    state.append_decision("C1:a", "approved")
    assert judge.score(item, cfg) == (pytest.approx(3.0), "propose")


def test_merge_twice_same_sig_appends_evidence_keeps_approved(tmp_projects):
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    items = judge.merge(t, {"items": [out_item()]}, "explicit")
    it = items["C1:app-not-installed"]
    assert it["status"] == "watch"   # 재발 1 → 0.4+1.2+0.6 = 2.2
    assert it["score"] == pytest.approx(2.2)
    assert it["scores"] == {"recurrence": 1, "impact": 3, "actionability": 3}
    assert it["sessions"] == 1
    assert it["target"] == {"path": "/r/skills/ticket-creator/SKILL.md", "owner": "new"}
    assert it["duplicate_of_rule"] is None and it["rule_unenforced"] is False

    hdir = state.harness_dir(t)
    stored = state.load_items(hdir)
    stored["C1:app-not-installed"]["status"] = "approved"
    stored["C1:app-not-installed"]["reported_at"] = "2026-10-16T00:00:00+09:00"
    state.save_items(hdir, stored)

    second = out_item(impact=2, proposal="새 제안", evidence=[
        {"session": "sess-a", "turn": 1, "quote": "Error: the app is not installed for this site"},
        {"session": "sess-a", "turn": 2, "quote": "아니 에픽 밑으로 넣어줘"},
    ], skills={"ticket-creator": 2, "jira": 1})
    items = judge.merge(t, {"items": [second]}, "explicit")
    it = items["C1:app-not-installed"]
    assert it["status"] == "approved"
    assert it["reported_at"] == "2026-10-16T00:00:00+09:00"
    assert [e["turn"] for e in it["evidence"]] == [1, 2]
    assert it["skills"] == {"ticket-creator": 2, "jira": 1}   # 건수별 max(1, 2)
    assert it["proposal"] == "새 제안"
    assert it["scores"] == {"recurrence": 1, "impact": 2, "actionability": 3}
    assert it["score"] == pytest.approx(1.8)   # 0.4+0.8+0.6
    assert state.load_items(hdir) == items

    runs = sorted(p.name for p in (hdir / "runs").iterdir())
    assert runs == ["001-explicit.json", "002-explicit.json"]
    run = json.loads((hdir / "runs" / "002-explicit.json").read_text())
    assert run["mode"] == "explicit" and run["output"] == {"items": [second]} and run["signals_upto"] == 0 and run["at"]


def test_merge_recomputes_status_unless_kept(tmp_projects):
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    judge.merge(t, {"items": [out_item()]}, "explicit")   # watch 2.2
    items = judge.merge(t, {"items": [out_item(impact=1, actionability=1)]}, "sweep")   # 0.4+0.4+0.2=1.0
    assert items["C1:app-not-installed"]["status"] == "discarded"
    assert (state.harness_dir(t) / "runs" / "002-sweep.json").exists()


def test_merge_records_signals_upto(tmp_projects):
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    hdir = state.harness_dir(t)
    (hdir / "signals.jsonl").write_text('{"sig": "C1:x"}\n{"sig": "C2:?"}\n{"sig": "C7:?"}\n')
    judge.merge(t, {"items": [out_item()]}, "session-end")
    assert json.loads((hdir / "runs" / "001-session-end.json").read_text())["signals_upto"] == 3


def test_merge_uses_recurrence_across_sessions(tmp_projects):
    for sid in ("s1", "s2", "s3"):
        other_session(tmp_projects, sid, signals=["C1:app-not-installed"])
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    it = judge.merge(t, {"items": [out_item()]}, "explicit")["C1:app-not-installed"]
    assert it["sessions"] == 4
    assert it["scores"]["recurrence"] == 3
    assert it["score"] == pytest.approx(3.0) and it["status"] == "propose"


def test_recurrence_counts_distinct_sessions(tmp_projects):
    for sid in ("s1", "s2", "s3"):
        other_session(tmp_projects, sid, signals=["C1:a", "C1:a"])
    other_session(tmp_projects, "s4", signals=["C1:ab"])
    assert judge.recurrence("C1:a", "s1") == 3
    assert judge.recurrence("C1:a", "new") == 4
    other_session(tmp_projects, "s5", items={"C1:a": {"sig": "C1:a", "status": "watch"}})
    assert judge.recurrence("C1:a", "s1") == 4


def test_sig_ending_with_question_mark_is_not_placeholder(tmp_projects):
    sig = "C1:File has not been read yet. Read it first?"
    other_session(tmp_projects, "s1", signals=[sig])
    assert judge.recurrence(sig, "me") == 2
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    it = judge.merge(t, {"items": [out_item(sig)]}, "explicit")[sig]
    assert it["sessions"] == 2 and it["scores"]["recurrence"] == 2


def test_recurrence_question_sig_ignores_signals(tmp_projects):
    other_session(tmp_projects, "s1", signals=["C2:?"])
    other_session(tmp_projects, "s2", signals=["C2:?"])
    assert judge.recurrence("C2:?", "me") == 1


def test_rescore_after_threshold_change(tmp_projects):
    for sid in ("s1", "s2", "s3"):
        other_session(tmp_projects, sid, signals=["C1:app-not-installed"])
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    judge.merge(t, {"items": [out_item(), out_item("C1:other", impact=1, actionability=1)]}, "explicit")
    hdir = state.harness_dir(t)
    assert state.load_items(hdir)["C1:app-not-installed"]["status"] == "propose"   # 3.0
    assert judge.rescore() == 0
    cfg = state.data_dir() / "scoring.yaml"
    cfg.write_text(cfg.read_text().replace("propose: 2.4", "propose: 9"))
    assert judge.rescore() == 1
    items = state.load_items(hdir)
    assert items["C1:app-not-installed"]["status"] == "watch"
    assert items["C1:other"]["status"] == "discarded"   # 0.4+0.4+0.2 = 1.0 그대로


def test_rescore_keeps_approved(tmp_projects):
    h = other_session(tmp_projects, "s1", items={"C1:a": {
        "sig": "C1:a", "condition": "C1", "status": "approved", "scores": {"recurrence": 1, "impact": 1, "actionability": 1},
        "score": 1.0, "sessions": 1, "target": {"path": "/r/x.md", "owner": "unknown"}, "evidence": EV, "skills": {},
        "duplicate_of_rule": None, "rule_unenforced": False}})
    state.data_dir().joinpath("scoring.yaml").write_text("weights: {recurrence: 1, impact: 1, actionability: 1}\n")
    assert judge.rescore() == 1
    it = state.load_items(h)["C1:a"]
    assert it["score"] == pytest.approx(3.0) and it["status"] == "approved"


def test_digest_contains_rubric_signals_existing_sigs(tmp_projects):
    other_session(tmp_projects, "s1", items={
        "C2:skipped-epic": {"sig": "C2:skipped-epic", "status": "watch"},
        "C9:long-table": {"sig": "C9:long-table", "status": "discarded"},
    })
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    state.save_items(state.harness_dir(t), {"C5:own": {"sig": "C5:own", "status": "propose"}})
    state.append_decision("C4:bare-yes", "rejected")
    sigs = [
        {"sig": "C1:Error: the app is not installed for this site", "condition": "C1", "turn": 1, "line": 2, "skill": "ticket-creator", "quote": "Error: a|b", "needs_judge": False},
        {"sig": "C2:?", "condition": "C2", "turn": 2, "line": 12, "skill": "ticket-creator", "quote": "아니 에픽 밑으로 넣어줘", "needs_judge": True},
    ]
    md = judge.digest(t, sigs)
    assert "# 채점 기준" in md   # rubric.md 본문
    assert "| C2:? | C2 | 2 | ticket-creator | 예 | 아니 에픽 밑으로 넣어줘 |" in md
    assert "Error: a\\|b" in md
    assert "IBS-1 티켓 내용 확인해서 후속 티켓 만들어줘" in md
    assert "mcp__claude_ai_Atlassian__getJiraIssue" in md
    assert "Error: the app is not installed for this site" in md
    assert "티켓 초안을 만들었습니다. 생성할까요?" in md
    assert "에픽 밑으로 옮기겠습니다." in md
    assert "- C2:skipped-epic (watch)" in md
    assert "C9:long-table" not in md
    assert "- C5:own (propose, 이 세션)" in md
    assert "- C4:bare-yes: rejected" in md
    assert "`?` 신호는 기존 sig 목록에서 주제가 같은 것을 재사용하고 없을 때만 `<조건>:<kebab-토픽>` 로 새로 만든다." in md
    assert "`sess-a`" in md
    assert '"rule_unenforced"' in md


def test_digest_cli_uses_pending_signals(tmp_projects, capsys):
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    hdir = state.harness_dir(t)
    (hdir / "signals.jsonl").write_text(json.dumps({"sig": "C1:old", "condition": "C1", "turn": 1, "line": 2, "skill": None, "quote": "old", "needs_judge": False}) + "\n")
    judge.merge(t, {"items": [out_item()]}, "explicit")
    with open(hdir / "signals.jsonl", "a") as f:
        f.write(json.dumps({"sig": "C2:?", "condition": "C2", "turn": 2, "line": 12, "skill": None, "quote": "new", "needs_judge": True}) + "\n")
    judge.main(["digest", str(t)])
    md = capsys.readouterr().out
    assert "| C2:? |" in md and "| C1:old |" not in md


def test_merge_rejects_schema_violation(tmp_projects):
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    before = sorted(p.relative_to(tmp_projects) for p in tmp_projects.rglob("*"))
    for bad in (
        {"items": [out_item(impact=4)]},
        {"items": [out_item(evidence=[])]},
        {"items": [out_item(condition="C12")]},
        {"items": [out_item(target={})]},
        {"items": [out_item(sig="C2:?")]},
        {"items": [out_item(sig="X1:topic")]},
        {"items": [out_item(sig="C12:topic")]},
        {"items": [{k: v for k, v in out_item().items() if k != "proposal"}]},
        {"items": [out_item(skills={"x": "1"})]},
        {},
    ):
        with pytest.raises(ValueError):
            judge.merge(t, bad, "explicit")
    with pytest.raises(ValueError):
        judge.merge(t, {"items": [out_item()]}, "daily")
    assert sorted(p.relative_to(tmp_projects) for p in tmp_projects.rglob("*")) == before


def test_schema_file_matches_condition_set():
    schema = json.loads((Path(judge.__file__).resolve().parents[1] / "defaults" / "judge-schema.json").read_text())
    item = schema["properties"]["items"]["items"]
    assert item["properties"]["condition"]["enum"] == [f"C{i}" for i in range(1, 12)]
    assert item["properties"]["evidence"]["minItems"] == 1
    assert item["properties"]["sig"]["pattern"] == judge.SIG.pattern
