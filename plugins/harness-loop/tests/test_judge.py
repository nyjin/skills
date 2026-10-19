import json
import shutil
from datetime import datetime, timedelta
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
    assert judge.score({**item, "sessions": 1}, cfg) == (pytest.approx(2.2), "propose")   # 0.4·1+1.2+0.6
    assert judge.score({**item, "sessions": 1, "actionability": 2}, cfg) == (pytest.approx(2.0), "watch")   # 0.4+1.2+0.4
    assert judge.score({**item, "sessions": 1, "impact": 1, "actionability": 1}, cfg) == (pytest.approx(1.0), "discarded")  # 0.4+0.4+0.2


def test_recurrence_bands():
    cfg = state.load_config()
    item = {"sig": "C1:a", "impact": 1, "actionability": 1, "evidence": EV, "target": {"path": "/r/x.md"}}
    assert judge.score({**item, "sessions": 2}, cfg)[0] == pytest.approx(1.4)   # 0.4·2+0.4+0.2
    assert judge.score({**item, "sessions": 3}, cfg)[0] == pytest.approx(1.4)
    assert judge.score({**item, "sessions": 5}, cfg)[0] == pytest.approx(1.8)   # 0.4·3+0.4+0.2


def test_global_target_bumps_threshold():
    cfg = state.load_config()
    item = {"sig": "C7:a", "sessions": 2, "impact": 3, "actionability": 2, "evidence": EV}   # 0.8+1.2+0.4=2.4
    assert judge.score({**item, "target": {"path": "/r/x.md"}}, cfg)[1] == "propose"
    assert judge.score({**item, "target": {"path": "~/.claude/CLAUDE.md"}}, cfg)[1] == "watch"   # 기준 2.2+0.3=2.5
    assert judge.score({**item, "target": {"path": str(Path.home() / ".claude/CLAUDE.md")}}, cfg)[1] == "watch"
    assert judge.score({**item, "target": {"path": "/h/.claude/projects/p/memory/x.md"}}, cfg)[1] == "watch"
    low = {**item, "sessions": 1, "impact": 2, "actionability": 1}   # 0.4+0.8+0.2=1.4 < 1.6
    assert judge.score({**low, "sessions": 2, "target": {"path": "/r/x.md"}}, cfg)[1] == "watch"   # 0.8+0.8+0.2=1.8
    assert judge.score({**low, "sessions": 2, "target": {"path": "~/.claude/CLAUDE.md"}}, cfg)[1] == "discarded"   # 1.8 < 1.9


def test_duplicate_rule_discarded_unless_unenforced():
    cfg = state.load_config()
    item = {"sig": "C7:a", "sessions": 4, "impact": 3, "actionability": 3, "evidence": EV, "target": {"path": "/r/x.md"}, "duplicate_of_rule": "/r/x.md: 묻지 말 것"}
    assert judge.score({**item, "rule_unenforced": False}, cfg) == (pytest.approx(3.0), "discarded")
    assert judge.score({**item, "rule_unenforced": True}, cfg) == (pytest.approx(3.0), "propose")


def test_duplicate_rule_only_discarded_when_in_target_file(tmp_path):
    cfg = state.load_config()
    home = Path.home()
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".claude/CLAUDE.md").write_text("x")
    item = {"sig": "C7:a", "sessions": 1, "impact": 3, "actionability": 3, "evidence": EV, "rule_unenforced": False}
    # 대상 파일에 같은 규칙 → 폐기 (원문 `~` 형태·펼친 형태 모두)
    glob_target = {"target": {"path": "~/.claude/CLAUDE.md"}}
    assert judge.score({**item, **glob_target, "duplicate_of_rule": "~/.claude/CLAUDE.md: 묻지 말 것"}, cfg)[1] == "discarded"
    assert judge.score({**item, **glob_target, "duplicate_of_rule": f"{home}/.claude/CLAUDE.md 의 '묻지 말 것'"}, cfg)[1] == "discarded"
    local = {"target": {"path": "/r/skills/x/SKILL.md"}}
    assert judge.score({**item, **local, "duplicate_of_rule": "/r/skills/x/SKILL.md: 묻지 말 것"}, cfg)[1] == "discarded"
    # 다른 범위(다른 메모리 파일)에만 있는 규칙 → 정상 채점
    other = "/h/.claude/projects/p/memory/feedback.md: 묻지 말 것"
    assert judge.score({**item, **local, "duplicate_of_rule": other}, cfg) == (pytest.approx(2.2), "propose")   # 0.4+1.2+0.6
    assert judge.score({**item, **glob_target, "duplicate_of_rule": other}, cfg) == (pytest.approx(2.2), "watch")   # 기준 2.2+0.3=2.5


def test_digest_says_duplicate_only_for_target_file(tmp_projects):
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    text = judge.digest(t, [])
    assert "duplicate_of_rule" in text and "대상 파일" in text and "다른 범위" in text


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
    assert it["status"] == "propose"   # 재발 1 → 0.4+1.2+0.6 = 2.2
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


def test_merge_accepts_c7_item(tmp_projects):
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    items = judge.merge(t, {"items": [out_item("C7:memory-rule-ignored")]}, "explicit")
    assert items["C7:memory-rule-ignored"]["condition"] == "C7"


def test_merge_keeps_first_of_c2_c7_same_topic(tmp_projects):
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    out = {"items": [out_item("C7:skipped-epic", proposal="첫째"), out_item("C2:other"), out_item("C2:skipped-epic", proposal="둘째"), out_item("C1:skipped-epic"), out_item("C7:skipped-epic", proposal="셋째")]}
    items = judge.merge(t, out, "explicit")
    assert sorted(items) == ["C1:skipped-epic", "C2:other", "C7:skipped-epic"]
    assert items["C7:skipped-epic"]["proposal"] == "셋째"   # 같은 sig 는 기존처럼 뒤 항목이 덮어쓴다


def test_merge_recomputes_status_unless_kept(tmp_projects):
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    judge.merge(t, {"items": [out_item()]}, "explicit")   # watch 2.2
    items = judge.merge(t, {"items": [out_item(impact=1, actionability=2)]}, "sweep")   # 0.4+0.4+0.4=1.2
    assert items["C1:app-not-installed"]["status"] == "discarded"
    assert (state.harness_dir(t) / "runs" / "002-sweep.json").exists()


def test_merge_drops_nothing_to_fix_items(tmp_projects):
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    out = {"items": [out_item("C1:lsd", impact=1, actionability=1, proposal="고칠 것 없음"), out_item("C1:low", impact=1, actionability=2), out_item("C7:x", impact=1, actionability=1), out_item("C2:x")]}
    items = judge.merge(t, out, "explicit")
    assert sorted(items) == ["C1:low", "C2:x"]
    run = json.loads((state.harness_dir(t) / "runs" / "001-explicit.json").read_text())
    assert run["output"] == out


def test_nothing_to_fix_floor_uses_user_scores(tmp_projects):
    other_session(tmp_projects, "s1", items={"C1:edited": {"sig": "C1:edited", "status": "watch", "scores": {"impact": 3, "actionability": 3}}})
    state.append_decision("C1:edited", "edit-score", impact=2)
    state.append_decision("C1:lowered", "edit-score", impact=1, actionability=1)
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    out = {"items": [out_item("C1:edited", impact=1, actionability=1), out_item("C1:lowered", impact=3, actionability=2)]}
    items = judge.merge(t, out, "explicit")
    assert sorted(items) == ["C1:edited"]
    assert items["C1:edited"]["scores"]["impact"] == 2


def test_digest_says_items_only_when_something_to_change(tmp_projects):
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    assert judge.ITEM_RULE in judge.digest(t, [])
    assert "고칠 것" in judge.ITEM_RULE


def test_merge_records_signals_upto(tmp_projects):
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    hdir = state.harness_dir(t)
    (hdir / "signals.jsonl").write_text('{"sig": "C1:x"}\n{"sig": "C2:?"}\n{"sig": "C2:?"}\n')
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
    judge.merge(t, {"items": [out_item(), out_item("C1:other", impact=1, actionability=2)]}, "explicit")
    hdir = state.harness_dir(t)
    assert state.load_items(hdir)["C1:app-not-installed"]["status"] == "propose"   # 3.0
    assert judge.rescore() == 0
    cfg = state.data_dir() / "scoring.yaml"
    cfg.write_text(cfg.read_text().replace("propose: 2.2", "propose: 9"))
    assert judge.rescore() == 1
    items = state.load_items(hdir)
    assert items["C1:app-not-installed"]["status"] == "watch"
    assert items["C1:other"]["status"] == "discarded"   # 0.4+0.4+0.4 = 1.2 그대로


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
    assert judge.C2_C7_RULE in md
    assert "C2" in judge.C2_C7_RULE and "C7" in judge.C2_C7_RULE and "하나" in judge.C2_C7_RULE


def test_digest_hides_errors_with_empty_text(tmp_projects):
    t = copy_fixture(tmp_projects, "signals.jsonl", "sess-a")   # 1번 턴에 "Exit code 1" 단독 오류(빈 토픽)가 있다
    md = judge.digest(t, [{"sig": "C4:bare-yes", "condition": "C4", "turn": 1, "line": 1, "skill": None, "quote": "q", "needs_judge": False}])
    errors = md.split("오류:")[1].split("AI 응답 꼬리:")[0]
    assert "error: unknown flag --json" in errors
    assert all(l.split("` ", 1)[-1].strip() for l in errors.splitlines() if l.startswith("- `"))


def test_digest_hides_whitespace_only_errors(tmp_projects, monkeypatch):
    import transcript
    from transcript import ToolError, Turn
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    turn = Turn(1, None, 1, "해줘", errors=[ToolError("a", "Bash", "   "), ToolError("b", "Read", "real")])
    monkeypatch.setattr(transcript, "parse", lambda path, start=0: ([turn], 1))
    md = judge.digest(t, [{"sig": "C1:real", "condition": "C1", "turn": 1, "line": 1, "skill": None, "quote": "q", "needs_judge": False}])
    errors = md.split("오류:")[1].split("AI 응답 꼬리:")[0]
    assert "- `Read` real" in errors and "`Bash`" not in errors


def test_merge_c2_c7_guard_uses_condition_field(tmp_projects):
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    items = judge.merge(t, {"items": [out_item("C2:x"), out_item("C2:x", condition="C7"), out_item("C9:y", condition="C7"), out_item("C2:y")]}, "explicit")
    assert items["C2:x"]["condition"] == "C2"
    assert sorted(items) == ["C2:x", "C9:y"]


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


def test_merge_keeps_watch_left_by_plugin_cache_regression(tmp_projects):
    for sid in ("s1", "s2", "s3"):
        other_session(tmp_projects, sid, signals=["C1:app-not-installed", "C1:plain"])
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    watch = {"sig": "C1:app-not-installed", "condition": "C1", "status": "watch", "regressed_after": "2026-10-01T09:00:00+09:00",
             "scores": {"recurrence": 1, "impact": 3, "actionability": 3}, "score": 2.6, "sessions": 1, "target": {"path": "/c/SKILL.md", "owner": "plugin_cache"}, "evidence": [], "skills": {}}
    plain = {**watch, "sig": "C1:plain"}
    del plain["regressed_after"]
    state.save_items(state.harness_dir(t), {"C1:app-not-installed": watch, "C1:plain": plain})
    judge.merge(t, {"items": [out_item(), out_item("C1:plain")]}, "sweep")
    got = state.load_items(state.harness_dir(t))
    assert got["C1:app-not-installed"]["status"] == "watch"
    assert got["C1:plain"]["status"] == "propose"


def test_rescore_keeps_watch_left_by_plugin_cache_regression(tmp_projects):
    base = {"condition": "C1", "status": "watch", "scores": {"recurrence": 1, "impact": 3, "actionability": 3}, "score": 2.2, "sessions": 1,
            "target": {"path": "/c/SKILL.md", "owner": "plugin_cache"}, "evidence": EV, "skills": {}, "duplicate_of_rule": None, "rule_unenforced": False}
    h = other_session(tmp_projects, "s1", items={
        "C1:a": {**base, "sig": "C1:a", "regressed_after": "2026-10-01T09:00:00+09:00"},
        "C1:b": {**base, "sig": "C1:b"}})
    state.data_dir().joinpath("scoring.yaml").write_text("propose: 1.0\nwatch: 0.5\n")
    judge.rescore()
    got = state.load_items(h)
    assert got["C1:a"]["status"] == "watch"
    assert got["C1:b"]["status"] == "propose"


def test_merge_cli_holds_session_lock_and_releases(tmp_projects, capsys, monkeypatch):
    import io
    import sys

    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    hdir = state.harness_dir(t)
    seen = []
    real = judge.merge
    monkeypatch.setattr(judge, "merge", lambda *a: seen.append((hdir / "lock").is_dir()) or real(*a))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"items": [out_item()]})))
    judge.main(["merge", str(t), "--mode", "explicit"])
    assert seen == [True]
    assert "C1:app-not-installed" in json.loads(capsys.readouterr().out)
    assert not (hdir / "lock").exists()


def test_merge_cli_releases_lock_on_validation_error(tmp_projects, monkeypatch):
    import io
    import sys

    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"items": [out_item(impact=4)]})))
    with pytest.raises(ValueError):
        judge.main(["merge", str(t), "--mode", "explicit"])
    assert not (state.harness_dir(t) / "lock").exists()


def test_merge_cli_exits_3_when_session_locked(tmp_projects, capsys, monkeypatch):
    import io
    import sys

    monkeypatch.setattr(state.time, "sleep", lambda s: None)
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    hdir = state.harness_dir(t)
    assert state.acquire_lock(hdir)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"items": [out_item()]})))
    with pytest.raises(SystemExit) as e:
        judge.main(["merge", str(t), "--mode", "explicit"])
    assert e.value.code == 3
    assert "잠겨" in capsys.readouterr().err
    assert state.load_items(hdir) == {} and (hdir / "lock").exists()


def test_recurrence_counts_c2_c7_twins_as_one_sig(tmp_projects):
    other_session(tmp_projects, "s1", items={"C2:uv-run": {"sig": "C2:uv-run", "status": "watch"}})
    other_session(tmp_projects, "s2", items={"C7:uv-run": {"sig": "C7:uv-run", "status": "watch"}})
    other_session(tmp_projects, "s3", signals=["C2:uv-run"])
    other_session(tmp_projects, "s4", items={"C7:other": {"sig": "C7:other", "status": "watch"}})
    assert judge.recurrence("C7:uv-run", "me") == 4
    assert judge.recurrence("C2:uv-run", "me") == 4
    assert judge.recurrence("C1:uv-run", "me") == 1


def test_rejected_twin_sig_scores_rejected(tmp_projects):
    cfg = state.load_config()
    item = {"sig": "C7:uv-run", "sessions": 1, "impact": 3, "actionability": 3, "evidence": EV, "target": {"path": "/r/x.md"}}
    state.append_decision("C2:uv-run", "rejected")
    assert judge.score(item, cfg)[1] == "rejected"
    state.append_decision("C7:uv-run", "approved")
    assert judge.score({**item, "sig": "C2:uv-run"}, cfg)[1] == "propose"


def stamped(root, sid, start):
    """첫 줄 timestamp 가 start 인 세션 기록."""
    t = root / "proj" / f"{sid}.jsonl"
    t.parent.mkdir(exist_ok=True)
    rows = [{"type": "user", "timestamp": start.isoformat(), "cwd": "/tmp/proj", "message": {"role": "user", "content": "발화"}}]
    t.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return t


def approved_source(root, sig, owner="repo:/r"):
    other_session(root, "old", items={sig: {"sig": sig, "condition": sig.split(":")[0], "status": "approved", "target": {"path": "/r/x.md", "owner": owner}}})
    state.append_decision(sig, "approved", target="/r/x.md")
    return datetime.fromisoformat(state.load_decisions()[sig]["at"])


def test_merge_marks_approved_sig_recurring_after_decision_regressed(tmp_projects):
    at = approved_source(tmp_projects, "C1:app-not-installed")
    t = stamped(tmp_projects, "after", at + timedelta(seconds=5))
    hdir = state.harness_dir(t)
    state.save_items(hdir, {"C1:app-not-installed": {"sig": "C1:app-not-installed", "status": "watch", "reported_at": "2026-10-16T00:00:00+09:00"}})
    it = judge.merge(t, {"items": [out_item()]}, "explicit")["C1:app-not-installed"]
    assert it["status"] == "regressed"
    assert it["regressed_after"] == at.isoformat()
    assert "reported_at" not in it
    assert state.load_items(hdir)["C1:app-not-installed"]["status"] == "regressed"
    import report
    assert report.regressions() == []


def test_merge_before_decision_is_not_regressed(tmp_projects):
    at = approved_source(tmp_projects, "C1:app-not-installed")
    t = stamped(tmp_projects, "before", at - timedelta(hours=1))
    it = judge.merge(t, {"items": [out_item()]}, "explicit")["C1:app-not-installed"]
    assert it["status"] == "propose" and "regressed_after" not in it


def test_merge_regression_follows_plugin_cache_once_rule(tmp_projects):
    at = approved_source(tmp_projects, "C1:app-not-installed", owner="plugin_cache")
    other_session(tmp_projects, "first", items={"C1:app-not-installed": {"sig": "C1:app-not-installed", "status": "regressed"}})
    t = stamped(tmp_projects, "after", at + timedelta(seconds=5))
    it = judge.merge(t, {"items": [out_item()]}, "explicit")["C1:app-not-installed"]
    assert it["status"] == "watch" and it["regressed_after"] == at.isoformat()


def test_merge_regresses_c7_twin_of_approved_c2(tmp_projects):
    at = approved_source(tmp_projects, "C2:uv-run")
    t = stamped(tmp_projects, "after", at + timedelta(seconds=5))
    it = judge.merge(t, {"items": [out_item("C7:uv-run")]}, "explicit")["C7:uv-run"]
    assert it["status"] == "regressed" and it["regressed_after"] == at.isoformat()


def test_digest_requires_korean_prose_with_verbatim_quotes(tmp_projects):
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    text = judge.digest(t, [])
    rule = next(l for l in text.splitlines() if "한국어" in l)
    assert "proposal" in rule and "인용" in rule and "그대로" in rule


def test_digest_decided_section_forbids_new_sig_for_decided_problem(tmp_projects):
    state.append_decision("C2:python-without-uv-run-with", "rejected")
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    section = judge.digest(t, []).split("## 결정된 sig", 1)[1].split("\n## ", 1)[0]
    assert "- C2:python-without-uv-run-with: rejected" in section
    assert "같은 문제" in section and "재사용" in section and "새 sig" in section
    for word in ("범위", "문구", "C2", "C7"):
        assert word in section, word


def test_score_override_applies_to_c2_c7_twin_in_merge_and_rescore(tmp_projects):
    state.append_decision("C2:uv-run", "edit-score", impact=1, actionability=3)
    state.append_decision("C7:uv-run", "edit-score", actionability=2)
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    items = judge.merge(t, {"items": [out_item("C7:uv-run", impact=3, actionability=3)]}, "explicit")
    assert (items["C7:uv-run"]["scores"]["impact"], items["C7:uv-run"]["scores"]["actionability"]) == (1, 2)
    other_session(tmp_projects, "s2", items={"C7:uv-run": {"sig": "C7:uv-run", "condition": "C7", "status": "propose", "sessions": 1, "target": {"path": "/r/x.md"},
                                                          "scores": {"recurrence": 1, "impact": 3, "actionability": 3}, "score": 2.2}})
    judge.rescore()
    it = state.load_items(tmp_projects / "other" / "s2" / "harness")["C7:uv-run"]
    assert (it["scores"]["impact"], it["scores"]["actionability"]) == (1, 2)


def test_merge_folds_c7_onto_existing_c2_in_session(tmp_projects):
    t = copy_fixture(tmp_projects, "basic.jsonl", "sess-a")
    judge.merge(t, {"items": [out_item("C2:uv-run", skills={"a": 2})]}, "explicit")
    ev2 = [{"session": "sess-a", "turn": 2, "quote": "둘째"}]
    items = judge.merge(t, {"items": [out_item("C7:uv-run", impact=2, evidence=ev2, skills={"a": 1, "b": 1})]}, "explicit")
    assert list(items) == ["C2:uv-run"]
    it = items["C2:uv-run"]
    assert it["sig"] == "C2:uv-run" and it["condition"] == "C2"
    assert [e["turn"] for e in it["evidence"]] == [1, 2]
    assert it["skills"] == {"a": 2, "b": 1}
    assert it["scores"]["impact"] == 2


def test_merge_twin_after_regression_keeps_one_regressed_item(tmp_projects):
    at = approved_source(tmp_projects, "C2:uv-run")
    t = stamped(tmp_projects, "after", at + timedelta(seconds=5))
    judge.merge(t, {"items": [out_item("C2:uv-run")]}, "explicit")
    items = judge.merge(t, {"items": [out_item("C7:uv-run")]}, "explicit")
    assert list(items) == ["C2:uv-run"] and items["C2:uv-run"]["status"] == "regressed"
    import report
    assert [i["sig"] for _, i in report.pending(5)] == ["C2:uv-run"]
