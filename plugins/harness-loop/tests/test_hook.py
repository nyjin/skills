import io
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import hook
import judge
import state

HOOK = str(Path(__file__).resolve().parents[1] / "scripts" / "hook.py")


@pytest.fixture
def spawned(monkeypatch):
    calls = []
    monkeypatch.setattr(judge, "spawn_background", lambda t, mode: calls.append((Path(t), mode)))
    for name in ("HARNESS_RETRO", "CLAUDE_CODE_CHILD_SESSION", "HERDR_PW_WORKER"):
        monkeypatch.delenv(name, raising=False)
    return calls


def run_hook(monkeypatch, capsys, event, payload):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(SystemExit) as e:
        hook.main(["hook.py", event])
    assert e.value.code == 0
    return capsys.readouterr().out


def make_transcript(root, sid, lines=3):
    t = root / "proj" / f"{sid}.jsonl"
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text("".join(json.dumps({"type": "user", "message": {"role": "user", "content": f"말 {n}"}}) + "\n" for n in range(lines)))
    return t


def propose_item(sig, score=2.6):
    return {"sig": sig, "condition": sig.split(":")[0], "status": "propose", "score": score, "sessions": 1, "scores": {"recurrence": 1, "impact": 3, "actionability": 3}, "target": {"path": "/r/x.md"}, "proposal": f"{sig} 제안"}


def unswept_after_install(root, sid):
    (state.data_dir() / "installed_at").write_text((datetime.now().astimezone() - timedelta(days=1)).isoformat())
    return make_transcript(root, sid)


def test_hook_guard_harness_retro_writes_skip(tmp_projects, monkeypatch, capsys, spawned):
    t = make_transcript(tmp_projects, "s1")
    monkeypatch.setenv("HARNESS_RETRO", "1")
    out = run_hook(monkeypatch, capsys, "session-end", {"session_id": "s1", "transcript_path": str(t)})
    assert out == ""
    assert (tmp_projects / "proj" / "s1" / "harness" / "skip").exists()
    assert spawned == []


def test_hook_guard_agent_id(tmp_projects, monkeypatch, capsys, spawned):
    t = make_transcript(tmp_projects, "s1")
    out = run_hook(monkeypatch, capsys, "session-start", {"session_id": "s1", "transcript_path": str(t), "source": "startup", "agent_id": "a1"})
    assert out == ""
    assert (tmp_projects / "proj" / "s1" / "harness" / "skip").exists()
    assert spawned == []


def test_hook_guard_child_session(tmp_projects, monkeypatch, capsys, spawned):
    t = make_transcript(tmp_projects, "s1")
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    out = run_hook(monkeypatch, capsys, "session-end", {"session_id": "s1", "transcript_path": str(t)})
    assert out == "" and spawned == []
    assert (tmp_projects / "proj" / "s1" / "harness" / "skip").exists()


def test_hook_session_end_spawns_judge(tmp_projects, monkeypatch, capsys, spawned):
    t = make_transcript(tmp_projects, "s1")
    out = run_hook(monkeypatch, capsys, "session-end", {"session_id": "s1", "transcript_path": str(t), "reason": "exit"})
    assert out == ""
    assert spawned == [(t, "session-end")]


def test_hook_bad_input_exits_zero_and_logs(tmp_path):
    home = tmp_path / "isolated-home"
    empty = tmp_path / "empty-projects"
    empty.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PLUGIN_DATA", "HARNESS_RETRO", "CLAUDE_CODE_CHILD_SESSION", "HERDR_PW_WORKER")}
    env.update(HOME=str(home), HARNESS_PROJECTS_ROOT=str(empty))
    r = subprocess.run([sys.executable, HOOK, "session-start"], input="not json", capture_output=True, text=True, env=env)
    assert r.returncode == 0 and r.stdout == ""
    fallback_data_dir = home / ".claude/plugins/data/harness-loop-local"
    assert "session-start" in (fallback_data_dir / "hook-errors.log").read_text()


def test_hook_session_start_emits_additional_context(tmp_projects, monkeypatch, capsys, spawned):
    t = make_transcript(tmp_projects, "s1")
    state.save_items(state.harness_dir(t), {"C1:a": propose_item("C1:a"), "C1:b": propose_item("C1:b", 3.0)})
    out = run_hook(monkeypatch, capsys, "session-start", {"session_id": "now", "source": "startup"})
    payload = json.loads(out)
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    text = payload["hookSpecificOutput"]["additionalContext"]
    assert text.splitlines()[0] == "harness-loop: 검토 대기 action item 2건 — /harness-loop:retro review"
    assert all(i.get("reported_at") for i in state.load_items(state.harness_dir(t)).values())
    assert run_hook(monkeypatch, capsys, "session-start", {"session_id": "now", "source": "resume"}) == ""


def test_hook_session_start_ignores_compact_source(tmp_projects, monkeypatch, capsys, spawned):
    t = unswept_after_install(tmp_projects, "s1")
    state.save_items(state.harness_dir(t), {"C1:a": propose_item("C1:a")})
    out = run_hook(monkeypatch, capsys, "session-start", {"session_id": "now", "source": "compact"})
    assert out == "" and spawned == []
    assert "reported_at" not in state.load_items(state.harness_dir(t))["C1:a"]


def test_hook_session_start_skips_worker_env(tmp_projects, monkeypatch, capsys, spawned):
    t = unswept_after_install(tmp_projects, "s1")
    state.save_items(state.harness_dir(t), {"C1:a": propose_item("C1:a")})
    monkeypatch.setenv("HERDR_PW_WORKER", "w1")
    out = run_hook(monkeypatch, capsys, "session-start", {"session_id": "now", "source": "startup"})
    assert out == ""
    assert spawned == [(t, "sweep")]
    assert "reported_at" not in state.load_items(state.harness_dir(t))["C1:a"]


def test_hook_session_start_reports_hook_errors_once(tmp_projects, monkeypatch, capsys, spawned):
    (state.data_dir() / "hook-errors.log").write_text("x session-end boom\n")
    out = run_hook(monkeypatch, capsys, "session-start", {"session_id": "now", "source": "startup"})
    assert json.loads(out)["hookSpecificOutput"]["additionalContext"].startswith("harness-loop: 훅 오류 1건: ")
    assert run_hook(monkeypatch, capsys, "session-start", {"session_id": "now", "source": "startup"}) == ""


def test_hooks_json_shape():
    cfg = json.loads((Path(HOOK).parents[1] / "hooks" / "hooks.json").read_text())
    start = cfg["hooks"]["SessionStart"][0]
    assert start["matcher"] == "startup|resume"
    assert start["hooks"][0] == {"type": "command", "command": 'uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/hook.py" session-start', "timeout": 10}
    end = cfg["hooks"]["SessionEnd"][0]["hooks"][0]
    assert end == {"type": "command", "command": 'uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/hook.py" session-end', "timeout": 10}
