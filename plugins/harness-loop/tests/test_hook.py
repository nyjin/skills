import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import hook
import paths

PLUGIN = Path(__file__).resolve().parents[1]
HOOK = PLUGIN / "scripts" / "hook.py"
FIXTURES = Path(__file__).parent / "fixtures" / "payloads"
SID = "abcd1234-0000-0000-0000-000000000000"
NOTICE2 = "harness-loop: 판정하지 않은 근거 2건이 있습니다. /harness-loop:retro 로 확인하세요."


@pytest.fixture
def session(tmp_projects):
    p = tmp_projects / "-proj" / f"{SID}.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n")
    return p


def prompt_payload(session, text="안녕", **kw):
    data = json.loads((FIXTURES / "UserPromptSubmit.json").read_text())
    data.update(hook_event_name="UserPromptSubmit", transcript_path=str(session), session_id=SID, cwd=str(session.parent), prompt=text, **kw)
    return json.dumps(data)


def evidence_file(session):
    return paths.harness_path(session) / "evidence.jsonl"


def start(source="startup"):
    return json.dumps({"hook_event_name": "SessionStart", "source": source, "session_id": "s", "transcript_path": "/x"})


@pytest.fixture
def evidence_pending(tmp_projects):
    h = tmp_projects / "-p" / "sess1" / "harness"
    h.mkdir(parents=True)
    (h / "evidence.jsonl").write_text('{"id":"a"}\n{"id":"b"}\n{"id":"c"}\n')
    (h / "evidence.cursor").write_text("1")
    return h


def test_prompt_event_records_evidence(session):
    assert hook.run(prompt_payload(session)) is None
    [line] = evidence_file(session).read_text().splitlines()
    assert json.loads(line)["text"] == "안녕"


def test_guard_agent_and_retro_env_record_nothing(session, monkeypatch):
    assert hook.run(prompt_payload(session, agent_id="a1")) is None
    monkeypatch.setenv("HARNESS_RETRO", "1")
    assert hook.run(prompt_payload(session)) is None
    assert not evidence_file(session).exists()


def test_worker_session_records_but_no_notice(session, evidence_pending, monkeypatch):
    monkeypatch.setenv("HERDR_PW_WORKER", "1")
    hook.run(prompt_payload(session))
    assert evidence_file(session).exists()
    assert hook.run(start()) is None
    assert not (paths.data_dir() / "notice-day").exists()


def test_pending_count_subtracts_cursor_and_skips_skip(evidence_pending, tmp_projects):
    other = tmp_projects / "-p" / "sess2" / "harness"
    other.mkdir(parents=True)
    (other / "evidence.jsonl").write_text('{"id":"x"}\n')
    assert hook.pending_count() == 3
    (other / "skip").touch()
    assert hook.pending_count() == 2


def test_session_start_notice_once_per_day(evidence_pending):
    out = json.loads(hook.run(start()))
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert out["hookSpecificOutput"]["additionalContext"] == NOTICE2
    assert hook.run(start()) is None


def test_session_start_silent_when_nothing_pending(tmp_projects):
    assert hook.run(start()) is None


def test_new_hook_errors_appended_and_marked_seen(evidence_pending):
    d = paths.data_dir()
    (d / "hook-errors.log").write_text("a\nb\n")
    text = json.loads(hook.run(start()))["hookSpecificOutput"]["additionalContext"]
    assert text == NOTICE2 + " (훅 오류 2건, hook-errors.log)"
    assert (d / "hook-errors.seen").read_text() == "2"
    (d / "notice-day").unlink()
    (d / "hook-errors.log").write_text("a\nb\nc\n")
    text = json.loads(hook.run(start()))["hookSpecificOutput"]["additionalContext"]
    assert text.endswith(" (훅 오류 1건, hook-errors.log)")
    (d / "notice-day").unlink()
    assert json.loads(hook.run(start()))["hookSpecificOutput"]["additionalContext"] == NOTICE2


def test_new_hook_errors_alone_trigger_notice(tmp_projects):
    d = paths.data_dir()
    (d / "hook-errors.log").write_text("a\n")
    text = json.loads(hook.run(start()))["hookSpecificOutput"]["additionalContext"]
    assert text == "harness-loop: 판정하지 않은 근거 0건이 있습니다. /harness-loop:retro 로 확인하세요. (훅 오류 1건, hook-errors.log)"


def subprocess_env(tmp_path):
    home = tmp_path / "isolated-home"
    empty = tmp_path / "empty-projects"
    empty.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PLUGIN_DATA", "HARNESS_RETRO", "HERDR_PW_WORKER")}
    env.update(HOME=str(home), HARNESS_PROJECTS_ROOT=str(empty))
    return env, home


def test_hook_bad_input_exits_zero_and_logs(tmp_path):
    env, home = subprocess_env(tmp_path)
    r = subprocess.run([sys.executable, HOOK], input="{", capture_output=True, text=True, env=env)
    assert r.returncode == 0 and r.stdout == ""
    log = (home / ".claude/plugins/data/harness-loop-local/hook-errors.log").read_text().splitlines()
    assert len(log) == 1


def test_hook_internal_error_exits_zero_and_logs(session, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(hook.evidence, "handle", boom)
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(prompt_payload(session)))
    with pytest.raises(SystemExit) as e:
        hook.main()
    assert e.value.code == 0
    assert capsys.readouterr().out == ""
    assert "boom" in (paths.data_dir() / "hook-errors.log").read_text()


def test_hook_does_not_import_old_modules():
    src = HOOK.read_text()
    for name in ("judge", "report", "state"):
        assert f"import {name}" not in src


def test_hooks_json_events_and_commands():
    spec = json.loads((PLUGIN / "hooks/hooks.json").read_text())["hooks"]
    assert set(spec) == {"SessionStart", "UserPromptSubmit", "UserPromptExpansion", "PreToolUse", "PostToolUse", "PostToolUseFailure"}
    matchers = {k: [g.get("matcher") for g in v] for k, v in spec.items()}
    assert matchers == {
        "SessionStart": ["startup|resume"],
        "UserPromptSubmit": [None],
        "UserPromptExpansion": [None],
        "PreToolUse": ["Skill|Bash"],
        "PostToolUse": ["AskUserQuestion|Edit|Write|MultiEdit"],
        "PostToolUseFailure": [None],
    }
    for groups in spec.values():
        for g in groups:
            for h in g["hooks"]:
                assert h["command"].startswith('uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/hook.py"')
                assert h["timeout"] == 5


@pytest.mark.skipif(os.environ.get("HARNESS_PERF") != "1", reason="uv 기동 시간 측정은 HARNESS_PERF=1 일 때만")
def test_hook_p95_under_100ms(session, tmp_path):
    env, _ = subprocess_env(tmp_path)
    env["HARNESS_PROJECTS_ROOT"] = os.environ["HARNESS_PROJECTS_ROOT"]
    env["CLAUDE_PLUGIN_DATA"] = os.environ["CLAUDE_PLUGIN_DATA"]
    data = json.loads((FIXTURES / "PreToolUse-Bash.json").read_text())
    data.update(hook_event_name="PreToolUse", transcript_path=str(session), session_id=SID, cwd=str(session.parent))
    raw = json.dumps(data)
    times = []
    for _ in range(40):
        t = time.perf_counter()
        subprocess.run(["uv", "run", "--script", str(HOOK)], input=raw, capture_output=True, text=True, env=env, check=True)
        times.append(time.perf_counter() - t)
    times = sorted(times[1:])
    assert times[37] < 0.1, f"p95 {times[37]:.3f}s"


def test_truncated_hook_errors_log_resets_seen(evidence_pending):
    d = paths.data_dir()
    (d / "hook-errors.seen").write_text("5")
    (d / "hook-errors.log").write_text("a\n")
    assert hook.run(start()) is not None
    assert (d / "hook-errors.seen").read_text() == "1"
    (d / "notice-day").unlink()
    (d / "hook-errors.log").write_text("a\nb\n")
    text = json.loads(hook.run(start()))["hookSpecificOutput"]["additionalContext"]
    assert text.endswith(" (훅 오류 1건, hook-errors.log)")
