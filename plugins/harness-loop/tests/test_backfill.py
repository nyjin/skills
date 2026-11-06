import hashlib
import json
import os
import time

import pytest

import backfill
import evidence
import paths

from .conftest import SESSION_A


def blob(text):
    data = text.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def records(transcript):
    p = paths.harness_path(transcript) / "evidence.jsonl"
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines()]


def summary(recs):
    return [(r["kind"], r["text"], r["flags"]) for r in recs]


@pytest.fixture
def repo(redesign_sessions):
    return redesign_sessions["repo"]


def test_backfill_matches_live_records(redesign_sessions, tmp_projects):
    src, repo = redesign_sessions["a"], redesign_sessions["repo"]
    lines = src.read_text(encoding="utf-8").splitlines(keepends=True)
    live = tmp_projects / "-live" / f"{SESSION_A}.jsonl"
    live.parent.mkdir()
    base = {"session_id": SESSION_A, "transcript_path": str(live), "cwd": str(repo)}
    skill_md = f"{repo}/skills/a/SKILL.md"
    q = {"question": "ADR 번호를 어디서 찾을까요?", "options": [{"label": "docs/adr"}, {"label": "위키"}]}
    hooks = {  # 훅이 그 줄을 쓴 직후 받는 입력(줄 번호는 1부터)
        1: ("UserPromptSubmit", {"prompt": "릴리스 노트 써줘"}),
        2: ("PreToolUse", {"tool_name": "Skill", "tool_input": {"skill": "release"}, "tool_use_id": "a1"}),
        5: ("PreToolUse", {"tool_name": "Bash", "tool_input": {"command": f"bash {repo}/skills/a/run.sh"}, "tool_use_id": "a2"}),
        6: ("PostToolUseFailure", {"tool_name": "Bash", "tool_input": {"command": f"bash {repo}/skills/a/run.sh"},
                                   "error": "Exit code 1\nfatal: no ADR found", "tool_use_id": "a2"}),
        9: ("UserPromptSubmit", {"prompt": "아니, ADR 번호는 docs/adr 에서 다음 번호를 찾아야 해"}),
        11: ("PostToolUse", {"tool_name": "AskUserQuestion", "tool_input": {"questions": [q]}, "tool_use_id": "a4",
                             "tool_response": {"questions": [q], "answers": {q["question"]: "docs/adr"}}}),
        16: ("PostToolUse", {"tool_name": "Write", "tool_input": {"file_path": skill_md, "content": "v1\n"},
                             "tool_response": {}, "tool_use_id": "a5"}),
        18: ("UserPromptSubmit", {"prompt": "그리고 번호는 세 자리로 맞춰줘"}),
        20: [("UserPromptSubmit", {"prompt": "/harness-loop:retro review"}),
             ("UserPromptExpansion", {"command_name": "harness-loop:retro", "prompt": "/harness-loop:retro review"})],
    }
    with open(live, "w", encoding="utf-8") as f:
        for i, line in enumerate(lines, 1):
            f.write(line)
            f.flush()
            at = json.loads(line)["timestamp"]
            calls = hooks.get(i, [])
            for event, extra in calls if isinstance(calls, list) else [calls]:
                evidence.handle(event, {**base, "hook_event_name": event, **extra}, at=at, hash_of=lambda p: None)
    assert backfill.run() == {"sessions": 2, "records": 13}
    got, want = records(src), records(live)
    assert summary(got) == summary(want)
    assert [r["kind"] for r in got] == ["prompt", "skill_start", "bash", "failure", "prompt", "ask",
                                        "skill_edit", "prompt", "prompt", "skill_start"]
    assert [r["pos"] for r in got] == [r["pos"] for r in want]
    correction = got[4]
    assert correction["flags"]["after_rejection"] is True
    assert correction["at"] == "2026-10-10T09:00:09.000Z"
    assert correction["pos"] == sum(len(ln.encode()) for ln in lines[:9])
    assert got[5]["meta"] == {"tool_use_id": "a4", "project_root": str(repo.resolve())} and "답: docs/adr" in got[5]["text"]
    assert got[2]["skill"]["path"] == f"{repo}/skills/a"


def test_backfill_skips_injected_and_continued(redesign_sessions, tmp_projects):
    a = redesign_sessions["a"]
    skipped = tmp_projects / "-w-proj-c" / "cccc3333.jsonl"
    done = tmp_projects / "-w-proj-c" / "dddd4444.jsonl"
    sub = tmp_projects / "-w-proj-a" / SESSION_A / "subagents" / "agent-x.jsonl"
    for p in (skipped, done, sub):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(a.read_text(encoding="utf-8"), encoding="utf-8")
        os.utime(p, (a.stat().st_mtime, a.stat().st_mtime))
    paths.harness_dir(skipped).joinpath("skip").write_text("x")
    paths.harness_dir(done).joinpath("evidence.jsonl").write_text("")

    assert backfill.run()["sessions"] == 2
    prompts = [r["text"] for r in records(a) if r["kind"] == "prompt"]
    assert prompts == ["릴리스 노트 써줘", "아니, ADR 번호는 docs/adr 에서 다음 번호를 찾아야 해", "그리고 번호는 세 자리로 맞춰줘",
                       "/harness-loop:retro review"]
    assert not (paths.harness_path(skipped) / "evidence.jsonl").exists()
    assert paths.harness_path(done).joinpath("evidence.jsonl").read_text() == ""
    assert not any(p.name == "evidence.jsonl" for p in (tmp_projects / "-w-proj-a" / SESSION_A / "subagents").rglob("*"))
    assert backfill.run() == {"sessions": 0, "records": 0}


def test_backfill_marks_eval_sessions_skip(redesign_sessions, tmp_projects):
    a = redesign_sessions["a"]
    ev = tmp_projects / "-w-proj-e" / "eeee5555.jsonl"
    ev.parent.mkdir(parents=True)
    ev.write_text(a.read_text(encoding="utf-8").replace("fatal: no ADR found", "트리거 측정 중"), encoding="utf-8")
    os.utime(ev, (a.stat().st_mtime, a.stat().st_mtime))
    assert backfill.run()["sessions"] == 2
    assert (paths.harness_path(ev) / "skip").exists()
    assert not (paths.harness_path(ev) / "evidence.jsonl").exists()
    assert backfill.run() == {"sessions": 0, "records": 0}


def test_backfill_eval_failure_stops_recording(redesign_sessions, monkeypatch):
    # session_kind 로 못 거른 세션도 재구성한 실패 근거에서 같은 검사로 멈춘다
    monkeypatch.setattr(backfill.tr, "session_kind", lambda turns, cfg: "normal")
    a = redesign_sessions["a"]
    a.write_text(a.read_text(encoding="utf-8").replace("fatal: no ADR found", "트리거 측정 중"), encoding="utf-8")
    os.utime(a, (time.time() - 3600,) * 2)
    backfill.run()
    assert (paths.harness_path(a) / "skip").exists()
    assert [r["kind"] for r in records(a)] == ["prompt", "skill_start", "bash"]


def test_backfill_days_filters_by_mtime(redesign_sessions):
    b = redesign_sessions["b"]
    now = time.time()
    os.utime(b, (now - 3600, now - 3600))
    assert [p.name for p in backfill.transcripts(1)] == [b.name]
    assert backfill.run(1)["sessions"] == 1
    assert not (paths.harness_path(redesign_sessions["a"]) / "evidence.jsonl").exists()


def test_backfill_default_ignores_installed_at_but_skips_live(redesign_sessions):
    a, b = redesign_sessions["a"], redesign_sessions["b"]
    (paths.data_dir() / "installed_at").write_text("2020-01-01T00:00:00+00:00")
    now = time.time()
    os.utime(a, (now - 3600, now - 3600))
    os.utime(b, (now - 60, now - 60))
    assert [p.name for p in backfill.transcripts(None)] == [a.name]


def test_backfill_runs_under_harness_retro(redesign_sessions, monkeypatch):
    monkeypatch.setenv("HARNESS_RETRO", "1")
    assert backfill.run()["records"] == 13
    assert os.environ["HARNESS_RETRO"] == "1"


def test_backfill_uses_git_hash_at_time(repo, redesign_sessions):
    backfill.run()
    a, b = records(redesign_sessions["a"]), records(redesign_sessions["b"])
    start, bash, edit = a[1], a[2], a[6]
    assert start["skill"]["hash"] == blob("v1\n")
    assert bash["meta"]["scripts"] == [{"path": os.path.realpath(repo / "skills/a/run.sh"), "hash": blob("echo run\n")}]
    assert edit["meta"]["hash_after"] == blob("v1\n")

    def guide(rec):
        return [g["hash"] for g in rec["meta"]["guidelines"] if g["path"] == str(repo / "CLAUDE.md")]

    assert guide(b[0]) == [blob("g1\n")]
    assert guide(b[1]) == [blob("g2\n")]
    assert b[2]["kind"] == "skill_start" and b[2]["skill"]["hash"] == blob("v2\n")
    assert not (paths.harness_path(redesign_sessions["b"]) / "guideline-cache.json").exists()
