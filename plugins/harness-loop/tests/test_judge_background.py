import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

import collect
import judge
import state

FIX = Path(__file__).parent / "fixtures"


def copy_fixture(root, name):
    t = root / "proj" / name
    t.parent.mkdir(exist_ok=True)
    shutil.copy(FIX / name, t)
    return t


def make_fake(tmp_path, name, body):
    exe = tmp_path / name
    log = tmp_path / f"{name}.calls"
    exe.write_text(f'#!/bin/sh\nprintf "%s\\0" "$HARNESS_RETRO $*" >> "{log}"\ncat > "{tmp_path}/{name}.stdin"\n{body}\n')
    exe.chmod(0o755)
    return str(exe)


def calls(exe):
    log = Path(exe + ".calls")
    return log.read_text().split("\0")[:-1] if log.exists() else []


ITEM = {
    "sig": "C1:app-not-installed",
    "condition": "C1",
    "impact": 3,
    "actionability": 3,
    "evidence": [{"session": "basic", "turn": 1, "quote": "Error: the app is not installed for this site"}],
    "target": {"path": "/r/skills/ticket-creator/SKILL.md"},
    "proposal": "403 이면 앱 설치 안내",
    "duplicate_of_rule": None,
    "rule_unenforced": False,
    "skills": {"ticket-creator": 1},
}


def reply(**kw):
    return "cat <<'EOF'\n" + json.dumps({"type": "result", "is_error": False, **kw}) + "\nEOF"


@pytest.fixture
def fake_claude_ok(tmp_path):
    return make_fake(tmp_path, "fake-claude", reply(result="", structured_output={"items": [ITEM]}))


@pytest.fixture
def fake_claude_fail(tmp_path):
    return make_fake(tmp_path, "fake-claude-fail", "echo boom >&2\nexit 1")


def all_run_lines():
    path = state.data_dir() / "judge-runs.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def run_lines():
    return [x for x in all_run_lines() if not x.get("reserved")]


def test_background_judge_merges_fake_output(tmp_projects, fake_claude_ok, tmp_path):
    t = copy_fixture(tmp_projects, "basic.jsonl")
    assert judge.background(t, "session-end", claude_bin=fake_claude_ok) is True
    hdir = state.harness_dir(t)
    items = state.load_items(hdir)
    assert items["C1:app-not-installed"]["status"] in ("propose", "watch")
    runs = sorted((hdir / "runs").glob("*.json"))
    assert [p.name for p in runs] == ["001-session-end.json"]
    assert json.loads(runs[0].read_text())["signals_upto"] == len((hdir / "signals.jsonl").read_text().splitlines())
    assert not (hdir / "lock").exists()
    [call] = calls(fake_claude_ok)
    assert call.startswith("1 -p --no-session-persistence --tools  --output-format json --json-schema {")
    assert "harness-loop judge 입력" in (tmp_path / "fake-claude.stdin").read_text()
    [line] = run_lines()
    assert line["ok"] is True and line["mode"] == "session-end" and line["session"] == "basic" and line["transcript"] == str(t)
    assert "error" not in line


def test_background_judge_parses_result_when_no_structured_output(tmp_projects, tmp_path):
    exe = make_fake(tmp_path, "fake-claude-result", reply(result=json.dumps({"items": [ITEM]})))
    t = copy_fixture(tmp_projects, "basic.jsonl")
    assert judge.background(t, "sweep", claude_bin=exe) is True
    assert "C1:app-not-installed" in state.load_items(state.harness_dir(t))


def test_background_judge_failure_leaves_state_untouched(tmp_projects, fake_claude_fail):
    t = copy_fixture(tmp_projects, "basic.jsonl")
    assert judge.background(t, "session-end", claude_bin=fake_claude_fail) is False
    hdir = state.harness_dir(t)
    assert not (state.harness_dir(t) / "action-items.json").exists()
    assert "basic" in (state.data_dir() / "hook-errors.log").read_text()
    assert not (hdir / "runs").exists() or not list((hdir / "runs").glob("*.json"))
    assert not (hdir / "lock").exists()
    [line] = run_lines()
    assert line["ok"] is False and line["error"]


@pytest.mark.parametrize("body", [
    "echo not-json",
    reply(result="still not json"),
    reply(result="", structured_output={"items": [{**ITEM, "sig": "C1:?"}]}),
    reply(is_error=True, result="api down", structured_output=None),
], ids=["not-json", "result-not-json", "invalid-items", "is-error"])
def test_bad_output_is_failure(tmp_projects, tmp_path, body):
    exe = make_fake(tmp_path, "fake-claude-bad", body)
    t = copy_fixture(tmp_projects, "basic.jsonl")
    assert judge.background(t, "session-end", claude_bin=exe) is False
    assert not (state.harness_dir(t) / "action-items.json").exists()
    assert str(t) in (state.data_dir() / "hook-errors.log").read_text()


def test_timeout_is_failure(tmp_projects, tmp_path, monkeypatch):
    monkeypatch.setattr(judge, "JUDGE_TIMEOUT", 1)
    exe = make_fake(tmp_path, "fake-claude-slow", "sleep 5")
    t = copy_fixture(tmp_projects, "basic.jsonl")
    assert judge.background(t, "session-end", claude_bin=exe) is False
    assert "timeout" in (state.data_dir() / "hook-errors.log").read_text().lower()


def test_daily_cap_blocks_extra_runs(tmp_projects, fake_claude_ok):
    (state.data_dir() / "scoring.yaml").write_text("max_background_judges_per_day: 1\n")
    t1 = copy_fixture(tmp_projects, "basic.jsonl")
    t2 = copy_fixture(tmp_projects, "partial.jsonl")
    assert judge.background(t1, "session-end", claude_bin=fake_claude_ok) is True
    assert judge.background(t2, "session-end", claude_bin=fake_claude_ok) is False
    assert len(calls(fake_claude_ok)) == 1
    assert len(run_lines()) == 1


def test_yesterday_runs_do_not_count(tmp_projects, fake_claude_ok):
    (state.data_dir() / "scoring.yaml").write_text("max_background_judges_per_day: 1\n")
    (state.data_dir() / "judge-runs.jsonl").write_text(json.dumps({"date": "2000-01-01", "ok": True}) + "\n")
    t = copy_fixture(tmp_projects, "basic.jsonl")
    assert judge.background(t, "session-end", claude_bin=fake_claude_ok) is True


def test_locked_session_is_skipped(tmp_projects, fake_claude_ok):
    t = copy_fixture(tmp_projects, "basic.jsonl")
    hdir = state.harness_dir(t)
    assert state.acquire_lock(hdir)
    assert judge.background(t, "session-end", claude_bin=fake_claude_ok) is False
    assert calls(fake_claude_ok) == []
    assert (hdir / "lock").exists()
    assert run_lines() == []


def test_nothing_new_returns_true_without_calling_claude(tmp_projects, fake_claude_fail):
    t = copy_fixture(tmp_projects, "basic.jsonl")
    collect.run(t)
    judge.merge(t, {"items": []}, "explicit")
    assert judge.background(t, "session-end", claude_bin=fake_claude_fail) is True
    assert calls(fake_claude_fail) == []
    assert run_lines() == []
    assert not (state.data_dir() / "hook-errors.log").exists()
    assert not (state.harness_dir(t) / "lock").exists()


def test_eval_session_returns_true_without_calling_claude(tmp_projects, fake_claude_fail):
    t = copy_fixture(tmp_projects, "eval.jsonl")
    assert judge.background(t, "session-end", claude_bin=fake_claude_fail) is True
    assert (state.harness_dir(t) / "skip").exists()
    assert calls(fake_claude_fail) == []


def test_spawn_background_popen_args(tmp_projects, monkeypatch):
    seen = {}

    def fake_popen(args, **kw):
        seen["args"], seen["kw"] = args, kw

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    t = tmp_projects / "proj" / "s.jsonl"
    assert judge.spawn_background(t, "session-end") is None
    assert seen["args"] == ["uv", "run", "--script", str(Path(judge.__file__).resolve()), "background", str(t), "--mode", "session-end"]
    kw = seen["kw"]
    assert kw["start_new_session"] is True
    assert kw["stdin"] is kw["stdout"] is kw["stderr"] is subprocess.DEVNULL
    assert kw["env"]["HARNESS_RETRO"] == "1"


def test_cli_background_exit_codes(tmp_projects, monkeypatch):
    t = copy_fixture(tmp_projects, "basic.jsonl")
    monkeypatch.setattr(judge, "background_status", lambda tr, mode, claude_bin="claude": 0 if tr == t and mode == "sweep" else 3)
    with pytest.raises(SystemExit) as e:
        judge.main(["background", str(t), "--mode", "sweep"])
    assert e.value.code == 0
    with pytest.raises(SystemExit) as e:
        judge.main(["background", str(t), "--mode", "explicit"])
    assert e.value.code == 3


def test_judge_timeout_is_below_lock_stale_window():
    import inspect

    assert judge.JUDGE_TIMEOUT < inspect.signature(state.acquire_lock).parameters["stale_sec"].default


def test_lock_mtime_refreshed_before_claude(tmp_projects, tmp_path, monkeypatch):
    t = copy_fixture(tmp_projects, "basic.jsonl")
    lock = state.harness_dir(t) / "lock"
    real_run = collect.run

    def aging_run(path):
        out = real_run(path)
        os.utime(lock, (time.time() - 3000, time.time() - 3000))
        return out

    monkeypatch.setattr(collect, "run", aging_run)
    exe = make_fake(tmp_path, "fake-claude-lock", f'[ -n "$(find "{lock}" -maxdepth 0 -mmin -5)" ] || exit 1\n' + reply(result="", structured_output={"items": [ITEM]}))
    assert judge.background(t, "session-end", claude_bin=exe) is True


def _raise(*a, **kw):
    raise RuntimeError("kaboom")


@pytest.mark.parametrize("breaker", ["collect", "runs-file"])
def test_any_failure_after_lock_is_logged(tmp_projects, fake_claude_ok, monkeypatch, breaker):
    t = copy_fixture(tmp_projects, "basic.jsonl")
    if breaker == "collect":
        monkeypatch.setattr(collect, "run", _raise)
    else:
        (state.data_dir() / "judge-runs.jsonl").mkdir()
    assert judge.background(t, "session-end", claude_bin=fake_claude_ok) is False
    log = (state.data_dir() / "hook-errors.log").read_text().splitlines()
    assert len(log) == 1 and str(t) in log[0]
    assert not (state.harness_dir(t) / "lock").exists()


def test_run_line_write_failure_is_logged_once(tmp_projects, fake_claude_ok, monkeypatch):
    t = copy_fixture(tmp_projects, "basic.jsonl")
    real_open = open

    def guarded_open(file, *a, **kw):
        if str(file).endswith("judge-runs.jsonl") and a and a[0] == "a":
            raise OSError("disk full")
        return real_open(file, *a, **kw)

    monkeypatch.setattr("builtins.open", guarded_open)
    assert judge.background(t, "session-end", claude_bin=fake_claude_ok) is False
    log = (state.data_dir() / "hook-errors.log").read_text().splitlines()
    assert len(log) == 1 and "disk full" in log[0]


def test_reservation_written_before_invoke_and_run_counted_once(tmp_projects, monkeypatch):
    t = copy_fixture(tmp_projects, "basic.jsonl")
    seen = []
    monkeypatch.setattr(judge, "_invoke", lambda *a: seen.append(all_run_lines()))
    assert judge.background(t, "session-end") is True
    [[reservation]] = seen
    assert reservation["reserved"] is True and reservation["ok"] is None and reservation["session"] == "basic"
    [result] = run_lines()
    assert result["ok"] is True and result["at"] == reservation["at"]
    assert judge.runs_today() == 1


def test_unfinished_reservation_counts_toward_cap(tmp_projects, fake_claude_ok):
    (state.data_dir() / "scoring.yaml").write_text("max_background_judges_per_day: 1\n")
    t0 = copy_fixture(tmp_projects, "partial.jsonl")
    now = judge.datetime.now().astimezone()
    (state.data_dir() / "judge-runs.jsonl").write_text(json.dumps({"at": now.isoformat(), "date": now.date().isoformat(), "session": "partial", "transcript": str(t0), "mode": "sweep", "ok": None, "reserved": True}) + "\n")
    t = copy_fixture(tmp_projects, "basic.jsonl")
    assert judge.background(t, "session-end", claude_bin=fake_claude_ok) is False
    assert calls(fake_claude_ok) == []


REAL_BACKGROUND_STATUS = judge.background_status


def cli_exit(t, claude_bin, monkeypatch):
    monkeypatch.setattr(judge, "background_status", lambda path, mode: REAL_BACKGROUND_STATUS(path, mode, claude_bin=claude_bin))
    with pytest.raises(SystemExit) as e:
        judge.main(["background", str(t), "--mode", "sweep"])
    return e.value.code


def test_background_cli_exit_codes(tmp_projects, fake_claude_ok, fake_claude_fail, monkeypatch):
    t = copy_fixture(tmp_projects, "basic.jsonl")
    assert state.acquire_lock(state.harness_dir(t))
    assert cli_exit(t, fake_claude_ok, monkeypatch) == 3
    assert not (state.data_dir() / "hook-errors.log").exists()
    state.release_lock(state.harness_dir(t))
    assert cli_exit(t, fake_claude_fail, monkeypatch) == 1
    assert cli_exit(t, fake_claude_ok, monkeypatch) == 0
    (state.data_dir() / "scoring.yaml").write_text("max_background_judges_per_day: 1\n")
    t2 = copy_fixture(tmp_projects, "partial.jsonl")
    assert cli_exit(t2, fake_claude_ok, monkeypatch) == 2
    assert len(calls(fake_claude_ok)) == 1
