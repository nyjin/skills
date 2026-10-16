import json
import os
from datetime import datetime, timedelta

import pytest

import judge
import report
import state

T0 = datetime(2026, 10, 1, 9, 0, 0).astimezone()


def transcript(root, sid, project="proj", lines=3, start=None, mtime=None):
    path = root / project / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for n in range(lines):
        row = {"type": "user", "message": {"role": "user", "content": f"발화 {n}"}}
        if start is not None:
            row["timestamp"] = (start + timedelta(seconds=n)).isoformat()
        rows.append(json.dumps(row, ensure_ascii=False))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime.timestamp(), mtime.timestamp()))
    return path


def hdir_of(t):
    return state.harness_dir(t)


def item(sig, status="propose", score=2.6, **kw):
    it = {
        "sig": sig,
        "condition": sig.split(":")[0],
        "status": status,
        "scores": {"recurrence": 1, "impact": 3, "actionability": 3},
        "score": score,
        "sessions": 1,
        "target": {"path": "/r/skills/x/SKILL.md", "owner": "repo:/r"},
        "proposal": f"{sig} 고치기\n둘째 줄",
        "evidence": [],
        "skills": {},
        "duplicate_of_rule": None,
        "rule_unenforced": False,
    }
    it.update(kw)
    return it


def put_items(t, *its):
    state.save_items(hdir_of(t), {i["sig"]: i for i in its})


def set_installed(when):
    (state.data_dir() / "installed_at").write_text(when.isoformat())


def test_pending_respects_limit_and_order(tmp_projects):
    t1 = transcript(tmp_projects, "s1")
    t2 = transcript(tmp_projects, "s2")
    put_items(t1, item("C1:a", score=2.5), item("C1:b", score=3.0), item("C1:w", status="watch", score=9))
    put_items(t2, item("C1:c", score=2.8), item("C1:d", status="regressed", score=2.9), item("C1:e", score=2.4))
    got = report.pending(3)
    assert [i["sig"] for _, i in got] == ["C1:b", "C1:d", "C1:c"]
    assert got[0][0] == hdir_of(t1)


def test_reported_items_not_repeated(tmp_projects):
    t = transcript(tmp_projects, "s1")
    put_items(t, item("C1:a"), item("C1:b", score=3.0))
    first = report.pending(1)
    report.mark_reported(first)
    assert state.load_items(hdir_of(t))["C1:b"]["reported_at"]
    assert [i["sig"] for _, i in report.pending(5)] == ["C1:a"]


def test_regression_after_approval(tmp_projects):
    old = transcript(tmp_projects, "old", start=T0)
    put_items(old, item("C1:a", status="approved", proposal="원래 제안"))
    state.append_decision("C1:a", "approved", target="/r/skills/x/SKILL.md")
    after = datetime.now().astimezone() + timedelta(hours=1)
    new = transcript(tmp_projects, "new", start=after)
    (hdir_of(new) / "signals.jsonl").write_text(json.dumps({"sig": "C1:a", "line": 3}) + "\n")
    before = transcript(tmp_projects, "before", start=T0 - timedelta(days=1))
    (hdir_of(before) / "signals.jsonl").write_text(json.dumps({"sig": "C1:a", "line": 3}) + "\n")

    created = report.regressions()
    assert [c["sig"] for c in created] == ["C1:a"]
    got = state.load_items(hdir_of(new))["C1:a"]
    assert got["status"] == "regressed" and got["proposal"] == "원래 제안"
    assert "C1:a" not in state.load_items(hdir_of(before))
    assert report.regressions() == []


def test_sweep_skips_skip_marker_and_preinstall(tmp_projects):
    set_installed(T0)
    later = T0 + timedelta(days=1)
    pre = transcript(tmp_projects, "pre", mtime=T0 - timedelta(days=1))
    fresh = transcript(tmp_projects, "fresh", mtime=later)
    skipped = transcript(tmp_projects, "skipped", mtime=later)
    (hdir_of(skipped) / "skip").touch()
    done = transcript(tmp_projects, "done", mtime=later)
    state.write_cursor(hdir_of(done), 3)
    waiting = transcript(tmp_projects, "waiting", mtime=later)
    state.write_cursor(hdir_of(waiting), 3)
    (hdir_of(waiting) / "signals.jsonl").write_text(json.dumps({"sig": "C1:a", "line": 2}) + "\n")

    got = report.sweep(datetime.now().astimezone().isoformat())
    assert sorted(p.stem for p in got) == ["fresh", "waiting"]
    assert not (tmp_projects / "proj" / "fresh").exists()
    assert pre not in got


def test_decide_reject_blocks_future_merge(tmp_projects):
    t = transcript(tmp_projects, "s1")
    put_items(t, item("C1:app"))
    report.decide("C1:app", "rejected")
    assert state.load_items(hdir_of(t))["C1:app"]["status"] == "rejected"
    assert state.load_decisions()["C1:app"]["decision"] == "rejected"
    assert state.load_decisions()["C1:app"]["target"] == "/r/skills/x/SKILL.md"

    t2 = transcript(tmp_projects, "s2")
    out = {"items": [{"sig": "C1:app", "condition": "C1", "impact": 3, "actionability": 3, "evidence": [{"session": "s2", "turn": 1, "quote": "q"}], "target": {"path": "/r/skills/x/SKILL.md"}, "proposal": "p", "duplicate_of_rule": None, "rule_unenforced": False, "skills": {}}]}
    judge.merge(t2, out, "explicit")
    assert state.load_items(hdir_of(t2))["C1:app"]["status"] == "rejected"


def test_decide_edit_score_rescores(tmp_projects):
    t = transcript(tmp_projects, "s1")
    put_items(t, item("C1:a", score=2.6))
    got = report.decide("C1:a", "edit-score", session="s1", impact=1, actionability=1)
    it = state.load_items(hdir_of(t))["C1:a"]
    assert it["scores"]["impact"] == 1 and it["scores"]["actionability"] == 1
    assert it["score"] == pytest.approx(0.4 * 1 + 0.4 * 1 + 0.2 * 1)
    assert it["status"] == "discarded"
    assert got["sig"] == "C1:a"


def test_decide_unknown_sig_raises(tmp_projects):
    with pytest.raises(ValueError):
        report.decide("C1:nope", "approved")


def test_list_items_scopes(tmp_projects):
    t1 = transcript(tmp_projects, "s1")
    t2 = transcript(tmp_projects, "s2")
    put_items(t1, item("C1:a"), item("C1:w", status="watch"))
    put_items(t2, item("C1:b", status="approved"))
    assert {i["sig"] for i in report.list_items("pending")} == {"C1:a"}
    assert {i["sig"] for i in report.list_items("session:s1")} == {"C1:a", "C1:w"}
    assert {i["sig"] for i in report.list_items("all")} == {"C1:a", "C1:w", "C1:b"}


def test_session_start_text_format(tmp_projects):
    when = datetime(2026, 10, 15, 12, 0).astimezone()
    t = transcript(tmp_projects, "s1", project="-Users-me-src-app", mtime=when)
    h = hdir_of(t)
    pairs = [(h, item("C1:a", proposal="x" * 100 + "\n둘째")), (h, item("C4:b", proposal="짧은 제안"))]
    text = report.session_start_text(pairs, 2)
    lines = text.splitlines()
    assert lines[0] == "harness-loop: 검토 대기 action item 2건 — /harness-loop:retro review"
    assert lines[1] == f"- [C1] {'x' * 80} (2026-10-15 -Users-me-src-app)"
    assert lines[2] == "- [C4] 짧은 제안 (2026-10-15 -Users-me-src-app)"
    assert lines[3] == f"- 훅 오류 2건: {state.data_dir() / 'hook-errors.log'}"
    assert report.session_start_text([], 0) == ""
    assert report.session_start_text([], 1) == f"harness-loop: 훅 오류 1건: {state.data_dir() / 'hook-errors.log'}"


def test_hook_error_count_since_last_report(tmp_projects):
    log = state.data_dir() / "hook-errors.log"
    log.write_text("a\nb\n")
    assert report.new_hook_errors() == 2
    report.mark_hook_errors_seen()
    assert report.new_hook_errors() == 0
    with open(log, "a") as f:
        f.write("c\n")
    assert report.new_hook_errors() == 1


def test_where_cli(tmp_projects, monkeypatch, capsys):
    t = transcript(tmp_projects, "sid-1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-1")
    with pytest.raises(SystemExit) as e:
        report.main(["where"])
    assert e.value.code in (0, None)
    assert capsys.readouterr().out.strip() == str(t)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "missing")
    with pytest.raises(SystemExit) as e:
        report.main(["where"])
    assert e.value.code == 2
