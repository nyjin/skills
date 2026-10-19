"""설계 §9 흐름 end-to-end: 명시 호출 두 번 → SessionEnd → SessionStart 보고 → 거절 → 승인 뒤 재발.

기본 scoring.yaml 에서 한 세션에만 나온 sig 는 영향·해결 가능성이 모두 3 이어야 propose 다. X 는 해결 가능성 2 라
한 세션에서는 watch 이고, 다른 세션에서 다시 나와야 보고된다: Y 는 이전 세션 S0 의 신호, X 는 S1 의 항목이 재발 근거다.
sweep 은 최근 OPEN_SESSION_SEC 안에 바뀐 기록을 건너뛰므로 갓 쓴 기록들은 sweep 대상이 아니다(S0 은 설치 전 기록).
"""
import io
import json
import os
import sys
from datetime import datetime, timedelta

import pytest

import collect
import hook
import judge
import report
import state

Y = "C1:Error: deploy failed N"
X = "C2:scope-creep"
Y_PROPOSAL = "배포 실패 시 재시도 전에 원인을 확인한다"
X_PROPOSAL = "요청한 환경 밖으로 배포하지 않는다"


class Transcript:
    """합성 세션 기록을 줄 단위로 덧붙인다. 모든 줄에 timestamp 를 넣는다."""

    def __init__(self, root, sid):
        self.sid = sid
        self.path = root / "proj" / f"{sid}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _add(self, at, typ, content):
        line = {"type": typ, "isSidechain": False, "cwd": "/tmp/proj", "sessionId": self.sid, "timestamp": at, "message": {"role": typ, "content": content}}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    def user(self, at, text):
        self._add(at, "user", text)

    def bash(self, at, call_id, command, result, error=False):
        self._add(at, "assistant", [{"type": "tool_use", "id": call_id, "name": "Bash", "input": {"command": command}}])
        self._add(at, "user", [{"type": "tool_result", "tool_use_id": call_id, "content": result, "is_error": error}])

    def say(self, at, text):
        self._add(at, "assistant", [{"type": "text", "text": text}])

    def failed_deploy(self, at, env, code):
        """배포가 실패한 뒤 같은 턴에서 다른 명령으로 재시도 → 결정적 C1 신호 Y."""
        self.bash(at, f"d{code}", f"make deploy ENV={env}", f"Error: deploy failed {code}", error=True)
        self.bash(at, f"r{code}", f"make release ENV={env}", "배포 완료")


def item(sig, session, turn, quote, proposal, target, actionability=3):
    return {
        "sig": sig,
        "condition": sig.split(":")[0],
        "impact": 3,
        "actionability": actionability,
        "evidence": [{"session": session, "turn": turn, "quote": quote}],
        "target": {"path": target},
        "proposal": proposal,
        "duplicate_of_rule": None,
        "rule_unenforced": False,
        "skills": {},
    }


def y_item(session, turn):
    return item(Y, session, turn, "Error: deploy failed", Y_PROPOSAL, "/r/skills/deploy/SKILL.md")


def x_item(session, turn):
    return item(X, session, turn, "아니 그게 아니라", X_PROPOSAL, "/r/proj/CLAUDE.md", actionability=2)


@pytest.fixture
def fake_claude(tmp_path):
    """judge-reply.json 을 structured_output 으로 돌려주는 가짜 claude. 호출마다 .calls 에 한 줄, 입력은 .stdin 에 남긴다."""
    exe = tmp_path / "fake-claude"
    reply = tmp_path / "judge-reply.json"
    exe.write_text(f'#!/bin/sh\nprintf "%s\\0" "$*" >> "{exe}.calls"\ncat > "{exe}.stdin"\ncat "{reply}"\n')
    exe.chmod(0o755)

    class Fake:
        path = str(exe)

        def answer(self, *items):
            reply.write_text(json.dumps({"type": "result", "is_error": False, "result": "", "structured_output": {"items": list(items)}}, ensure_ascii=False))

        def calls(self):
            log = tmp_path / "fake-claude.calls"
            return len(log.read_text().split("\0")[:-1]) if log.exists() else 0

        def stdin(self):
            return (tmp_path / "fake-claude.stdin").read_text()

    return Fake()


@pytest.fixture
def spawned(monkeypatch, fake_claude):
    """spawn_background 를 같은 프로세스에서 동기로 도는 background(claude_bin=가짜) 로 바꾼다."""
    seen = []

    def run_now(t, mode):
        seen.append((t.stem, mode))
        assert judge.background(t, mode, claude_bin=fake_claude.path) is True

    monkeypatch.setattr(judge, "spawn_background", run_now)
    for name in ("HARNESS_RETRO", "CLAUDE_CODE_CHILD_SESSION", "HERDR_PW_WORKER"):
        monkeypatch.delenv(name, raising=False)
    return seen


def run_hook(monkeypatch, capsys, event, payload):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(SystemExit) as e:
        hook.main(["hook.py", event])
    assert e.value.code == 0
    assert not (state.data_dir() / "hook-errors.log").exists()
    return capsys.readouterr().out


def session_end(monkeypatch, capsys, t):
    out = run_hook(monkeypatch, capsys, "session-end", {"session_id": t.sid, "transcript_path": str(t.path), "hook_event_name": "SessionEnd", "reason": "exit"})
    assert out == ""


def session_start(monkeypatch, capsys, sid):
    out = run_hook(monkeypatch, capsys, "session-start", {"session_id": sid, "transcript_path": str(state.projects_root() / "proj" / f"{sid}.jsonl"), "hook_event_name": "SessionStart", "source": "startup"})
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""


def items_of(t):
    return state.load_items(state.harness_dir(t.path))


def runs_of(t):
    return sorted(p.name for p in (state.harness_dir(t.path) / "runs").glob("*.json"))


def pending_sigs():
    return [(h.parent.name, it["sig"]) for h, it in report.pending(state.load_config()["queue_limit"])]


def test_spec9_flow(tmp_projects, monkeypatch, capsys, fake_claude, spawned):
    state.data_dir()
    hdir = lambda t: state.harness_dir(t.path)  # noqa: E731

    # 0. 설치 전에 끝난 세션 S0 에 Y 가 이미 한 번 나왔다(수집만 됨). 설치 전 기록이라 sweep 대상이 아니다.
    s0 = Transcript(tmp_projects, "S0")
    s0.user("2026-09-30T09:00:00+09:00", "운영 배포해줘")
    s0.failed_deploy("2026-09-30T09:00:10+09:00", "prod", 41)
    old = (datetime.now() - timedelta(days=1)).timestamp()
    os.utime(s0.path, (old, old))
    assert [s["sig"] for s in collect.run(s0.path)] == [Y]

    # 1. S1: 결정적 C1 재시도 + 교정 턴. 명시 retro #1.
    s1 = Transcript(tmp_projects, "S1")
    s1.user("2026-10-01T10:00:00+09:00", "배포 스크립트 돌려줘")
    s1.failed_deploy("2026-10-01T10:00:10+09:00", "prod", 42)
    s1.say("2026-10-01T10:00:20+09:00", "배포를 마쳤습니다.")
    s1.user("2026-10-01T10:05:00+09:00", "아니 그게 아니라 스테이징에만 배포하라고 했잖아")
    s1.say("2026-10-01T10:05:10+09:00", "알겠습니다. 스테이징 기준으로 진행하겠습니다.")

    new = collect.run(s1.path)
    assert [(s["sig"], s["turn"], s["needs_judge"]) for s in new] == [(Y, 1, False), ("C2:?", 2, True)]
    assert len(judge.pending_signals(hdir(s1))) == 2
    text = judge.digest(s1.path, judge.pending_signals(hdir(s1)))
    assert f"| {Y} | C1 | 1 |" in text and "| C2:? | C2 | 2 |" in text
    judge.merge(s1.path, {"items": [y_item("S1", 1), x_item("S1", 2)]}, "explicit")
    items = items_of(s1)
    assert (items[Y]["status"], items[Y]["sessions"], items[Y]["score"]) == ("propose", 2, 2.6)
    assert (items[X]["status"], items[X]["sessions"], items[X]["score"]) == ("watch", 1, 2.0)
    assert judge.pending_signals(hdir(s1)) == []
    assert pending_sigs() == [("S1", Y)]

    report.main(["list", "--session", "S1", "--mark-reported"])
    listed = json.loads(capsys.readouterr().out)
    assert sorted(i["sig"] for i in listed) == sorted([Y, X])
    assert pending_sigs() == []
    assert items_of(s1)[Y]["reported_at"] and "reported_at" not in items_of(s1)[X]

    # 2. S1 에 턴을 덧붙인다(Y 재발). 명시 retro #2: 새 신호만 처리하고 같은 sig 는 한 항목으로 합친다.
    s1.user("2026-10-01T10:20:00+09:00", "스테이징 배포 이어서 해줘")
    s1.failed_deploy("2026-10-01T10:20:10+09:00", "stage", 43)
    s1.say("2026-10-01T10:20:20+09:00", "스테이징 배포를 마쳤습니다.")
    assert judge.pending_signals(hdir(s1)) == []
    new = collect.run(s1.path)
    assert [(s["sig"], s["turn"]) for s in new] == [(Y, 3)]
    assert [(s["sig"], s["turn"]) for s in judge.pending_signals(hdir(s1))] == [(Y, 3)]
    judge.merge(s1.path, {"items": [y_item("S1", 3)]}, "explicit")
    items = items_of(s1)
    assert sorted(items) == sorted([Y, X])
    assert [e["turn"] for e in items[Y]["evidence"]] == [1, 3]
    assert items[Y]["status"] == "propose" and items[Y]["reported_at"]
    assert judge.pending_signals(hdir(s1)) == []
    assert runs_of(s1) == ["001-explicit.json", "002-explicit.json"]
    assert pending_sigs() == []

    # 3. S1 SessionEnd: 새 신호가 없으니 백그라운드 judge 는 claude 를 부르지 않는다.
    session_end(monkeypatch, capsys, s1)
    assert spawned == [("S1", "session-end")]
    assert fake_claude.calls() == 0
    assert runs_of(s1) == ["001-explicit.json", "002-explicit.json"]
    assert not (state.data_dir() / "judge-runs.jsonl").exists()

    # 4. S2 는 retro 없이 끝난다. SessionEnd judge 가 교정 후보를 기존 sig X 로 판정하고, 다음 SessionStart(S3)가 보고한다.
    s2 = Transcript(tmp_projects, "S2")
    s2.user("2026-10-02T09:00:00+09:00", "오래된 배포 로그 정리해줘")
    s2.bash("2026-10-02T09:00:10+09:00", "c1", "python3 cleanup.py --days 30", "정리 완료")
    s2.say("2026-10-02T09:00:20+09:00", "로그를 정리했습니다.")
    s2.user("2026-10-02T09:05:00+09:00", "아니 그게 아니라 운영 서버 로그는 건드리지 말라고 했잖아")
    s2.say("2026-10-02T09:05:10+09:00", "알겠습니다.")
    fake_claude.answer(x_item("S2", 2))
    session_end(monkeypatch, capsys, s2)
    assert spawned[-1] == ("S2", "session-end")
    assert fake_claude.calls() == 1
    assert f"- {X} (watch)" in fake_claude.stdin() and "| C2:? | C2 | 2 |" in fake_claude.stdin()
    assert runs_of(s2) == ["001-session-end.json"]
    assert (items_of(s2)[X]["status"], items_of(s2)[X]["sessions"]) == ("propose", 2)

    context = session_start(monkeypatch, capsys, "S3")
    assert spawned[-1] == ("S2", "session-end")
    lines = context.splitlines()
    assert lines[0] == "harness-loop: 검토 대기 action item 1건 — /harness-loop:retro review"
    assert len(lines) - 1 == 1 <= state.load_config()["queue_limit"]
    assert lines[1].startswith(f"- [C2] {X_PROPOSAL} (") and lines[1].endswith(" tmp/proj)")
    assert items_of(s2)[X]["reported_at"]
    assert pending_sigs() == []

    # 5. X 거절 → S4 에서 X 재발 → 병합해도 rejected 이고 다음 SessionStart 에 보고되지 않는다.
    assert {i["session"] for i in report.decide(X, "rejected")["items"]} == {"S1", "S2"}
    s4 = Transcript(tmp_projects, "S4")
    s4.user("2026-10-03T09:00:00+09:00", "스테이징 설정 바꿔줘")
    s4.bash("2026-10-03T09:00:10+09:00", "e1", "python3 edit_config.py --env stage", "변경 완료")
    s4.say("2026-10-03T09:00:20+09:00", "설정을 바꿨습니다.")
    s4.user("2026-10-03T09:05:00+09:00", "아니 그게 아니라 운영 설정은 손대지 말라고 했잖아")
    s4.say("2026-10-03T09:05:10+09:00", "알겠습니다.")
    fake_claude.answer(x_item("S4", 2))
    session_end(monkeypatch, capsys, s4)
    assert fake_claude.calls() == 2
    assert f"- {X}: rejected" in fake_claude.stdin()
    assert (items_of(s4)[X]["status"], items_of(s4)[X]["sessions"]) == ("rejected", 3)
    assert all(sig != X for _, sig in pending_sigs())
    assert session_start(monkeypatch, capsys, "S6") == ""
    assert "reported_at" not in items_of(s4)[X]

    # 6. Y 승인 → 승인 뒤 시작한 S5 에서 Y 재발 → 다음 SessionStart 가 regressed 항목을 보고한다.
    report.decide(Y, "approved")
    approved_at = datetime.fromisoformat(state.load_decisions()[Y]["at"])
    s5 = Transcript(tmp_projects, "S5")
    start = approved_at + timedelta(seconds=1)
    s5.user(start.isoformat(), "운영 배포 돌려줘")
    s5.failed_deploy((start + timedelta(seconds=10)).isoformat(), "prod", 44)
    s5.say((start + timedelta(seconds=20)).isoformat(), "배포를 마쳤습니다.")
    fake_claude.answer(y_item("S5", 1))
    session_end(monkeypatch, capsys, s5)
    assert fake_claude.calls() == 3
    assert Y in items_of(s5)

    context = session_start(monkeypatch, capsys, "S7")
    lines = context.splitlines()
    assert lines[0] == "harness-loop: 검토 대기 action item 1건 — /harness-loop:retro review"
    assert lines[1].startswith(f"- [C1·반영 실패] {Y_PROPOSAL} (")
    regressed = items_of(s5)[Y]
    assert regressed["status"] == "regressed"
    assert regressed["regressed_after"] == approved_at.isoformat()
    assert regressed["reported_at"]
    assert items_of(s1)[Y]["status"] == "approved"
    assert session_start(monkeypatch, capsys, "S8") == ""

    # 전 과정에서 sweep 은 한 번도 돌지 않았다(모든 judge 는 SessionEnd 에서).
    assert [mode for _, mode in spawned] == ["session-end"] * 4
    assert not any(p.name.endswith("-sweep.json") for p in state.projects_root().glob("*/*/harness/runs/*.json"))
