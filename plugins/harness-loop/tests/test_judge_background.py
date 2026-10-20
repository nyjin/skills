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
    assert call.startswith("1 -p --model opus --setting-sources project --strict-mcp-config --no-session-persistence --tools  --output-format json --json-schema {")
    assert "harness-loop judge 입력" in (tmp_path / "fake-claude.stdin").read_text()
    run = json.loads(runs[0].read_text())
    assert run["digest"] == (tmp_path / "fake-claude.stdin").read_text() and run["rubric_version"] == 1
    [line] = run_lines()
    assert line["ok"] is True and line["mode"] == "session-end" and line["session"] == "basic" and line["transcript"] == str(t)
    assert "error" not in line


def test_background_judge_runs_isolated_from_user_settings(tmp_projects, tmp_path):
    exe = make_fake(tmp_path, "fake-claude-cwd", f'pwd -P > "{tmp_path}/cwd.txt"\n' + reply(result="", structured_output={"items": [ITEM]}))
    t = copy_fixture(tmp_projects, "basic.jsonl")
    assert judge.background(t, "session-end", claude_bin=exe) is True
    [call] = calls(exe)
    argv = call.split(" --json-schema ")[0].split(" ")
    assert argv == ["1", "-p", "--model", "opus", "--setting-sources", "project", "--strict-mcp-config", "--no-session-persistence", "--tools", "", "--output-format", "json"]
    cwd = Path((tmp_path / "cwd.txt").read_text().strip())
    assert cwd.name.startswith("harness-judge-")
    home = Path(os.environ["HOME"]).resolve()
    assert home not in cwd.parents and cwd != home
    assert not cwd.exists()


def test_judge_cwd_falls_back_to_tmp_when_tempdir_under_home(tmp_projects, tmp_path, monkeypatch):
    import tempfile

    under_home = Path(os.environ["HOME"]) / "tmp"
    under_home.mkdir(parents=True)
    monkeypatch.setenv("TMPDIR", str(under_home))
    monkeypatch.setattr(tempfile, "tempdir", None)
    assert Path(judge._judge_tmp_parent()).resolve() == Path("/tmp").resolve()
    exe = make_fake(tmp_path, "fake-claude-cwd", f'pwd -P > "{tmp_path}/cwd.txt"\n' + reply(result="", structured_output={"items": [ITEM]}))
    t = copy_fixture(tmp_projects, "basic.jsonl")
    assert judge.background(t, "session-end", claude_bin=exe) is True
    cwd = Path((tmp_path / "cwd.txt").read_text().strip())
    assert cwd.name.startswith("harness-judge-") and cwd.parent == Path("/tmp").resolve()
    assert not cwd.exists()


def test_judge_cwd_uses_tempdir_outside_home(tmp_projects, tmp_path, monkeypatch):
    import tempfile

    outside = tmp_path / "t"
    outside.mkdir()
    monkeypatch.setenv("TMPDIR", str(outside))
    monkeypatch.setattr(tempfile, "tempdir", None)
    assert Path(judge._judge_tmp_parent()).resolve() == outside.resolve()


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


def replay_item(sig):
    return {**ITEM, "sig": sig, "evidence": [{"session": "basic", "turn": 1, "quote": f"인용 {sig}"}]}


def snapshot(*roots):
    return {str(p): p.read_bytes() for root in roots for p in sorted(Path(root).rglob("*")) if p.is_file()}


@pytest.fixture
def replay_world(tmp_projects, tmp_path):
    """test 분할 positive C1:c·negative C1:e, train 분할 negative C1:a. 결정 전 run 001 의 digest rubric 은 OLD-RUBRIC 이고 살아 있는·결정된 sig 절에 세 sig 가 있다.
    결정 뒤 run 002 도 세 sig 와 C1:f(test 분할, 결정 전 run 없음)를 담는다. 지금 rubric 은 NOW-RUBRIC."""
    assert [state.split(s) for s in ("C1:c", "C1:e", "C1:a", "C1:f")] == ["test", "test", "train", "test"]
    t = copy_fixture(tmp_projects, "basic.jsonl")
    hdir = state.harness_dir(t)
    signals = [{"sig": s, "condition": "C1", "turn": 1, "line": 2, "skill": None, "quote": f"인용 {s}", "needs_judge": False} for s in ("C1:c", "C1:e", "C1:a")]
    (hdir / "signals.jsonl").write_text("".join(json.dumps(s) + "\n" for s in signals))
    state.data_dir().joinpath("rubric.md").write_text("OLD-RUBRIC\n")
    text = judge.digest(t, signals)
    text = text.replace("## 살아 있는 sig\n\n- 없음", "## 살아 있는 sig\n\n- C1:a (watch)\n- C1:c (approved)\n- C1:e (propose, 이 세션)")
    text = text.replace("## 결정된 sig\n\n- 없음", "## 결정된 sig\n\n- C1:a: rejected\n- C1:c: approved\n- C1:e: rejected")
    judge.merge(t, {"items": [replay_item("C1:c"), replay_item("C1:e"), replay_item("C1:a")]}, "sweep", text)
    state.append_decision("C1:c", "approved")
    state.append_decision("C1:e", "rejected", kind="not-defect")
    state.append_decision("C1:a", "rejected", kind="trivial")
    state.append_decision("C1:f", "approved")
    later = signals + [{"sig": "C1:f", "condition": "C1", "turn": 1, "line": 2, "skill": None, "quote": "인용 C1:f", "needs_judge": False}]
    judge.merge(t, {"items": [replay_item(s) for s in ("C1:c", "C1:e", "C1:a", "C1:f")]}, "sweep", judge.digest(t, later))
    state.data_dir().joinpath("rubric.md").write_text("<!-- rubric-version: 3 -->\nNOW-RUBRIC\n")
    state.data_dir().joinpath("scoring.yaml").write_text("replay_min_labels: 3\n")
    cand = tmp_path / "candidate.md"
    cand.write_text("<!-- rubric-version: 4 -->\nNOW-RUBRIC\n\n## 항목으로 만들지 않는 경우\n\n- CANDIDATE-RULE\n")
    both = json.dumps({"items": [replay_item("C1:c"), replay_item("C1:e")]})
    only_pos = json.dumps({"items": [replay_item("C1:c")]})
    body = (
        f'cat "{tmp_path}/fake-replay.stdin" >> "{tmp_path}/stdins.txt"; printf "\\n=====\\n" >> "{tmp_path}/stdins.txt"\n'
        f'if grep -q CANDIDATE-RULE "{tmp_path}/fake-replay.stdin"; then\n'
        + reply(result="", structured_output=json.loads(only_pos)) + "\nelse\n"
        + reply(result="", structured_output=json.loads(both)) + "\nfi"
    )
    exe = make_fake(tmp_path, "fake-replay", body)
    return {"t": t, "cand": cand, "exe": exe, "tmp": tmp_path}


def test_replay_compares_current_and_candidate_on_test_split(replay_world, tmp_projects):
    w = replay_world
    before = snapshot(tmp_projects, state.data_dir())
    got = judge.replay(w["cand"], claude_bin=w["exe"])
    assert snapshot(tmp_projects, state.data_dir()) == before   # items·runs·judge-runs.jsonl 모두 그대로
    assert not (state.data_dir() / "judge-runs.jsonl").exists()
    assert got["eligible"] is True and got["labels"] == 4
    assert got["current"] == {"n_pos": 1, "n_neg": 1, "tpr": 1.0, "tnr": 0.0}
    assert got["candidate"] == {"n_pos": 1, "n_neg": 1, "tpr": 1.0, "tnr": 1.0}
    assert {(i["sig"], i["label"], i["current"], i["candidate"]) for i in got["items"]} == {("C1:c", "positive", True, True), ("C1:e", "negative", True, False)}
    assert got["errors"] == []
    calls_made = calls(w["exe"])
    assert len(calls_made) == 2 and all(c.startswith("1 -p --model opus --setting-sources project --strict-mcp-config --no-session-persistence --tools  --output-format json --json-schema {") for c in calls_made)
    stdins = (w["tmp"] / "stdins.txt").read_text().split("\n=====\n")[:2]
    assert "OLD-RUBRIC" not in "".join(stdins)
    assert ["CANDIDATE-RULE" in s for s in stdins].count(True) == 1 and all("NOW-RUBRIC" in s for s in stdins)
    assert all("| C1:c | C1 | 1 |" in s for s in stdins)
    assert all(i["run"].endswith("001-sweep.json") for i in got["items"])   # 결정 뒤 run 002 는 쓰지 않는다
    for s in stdins:
        assert "- C1:a (watch)" in s and "- C1:a: rejected" in s   # 판정하지 않는 sig 는 남긴다
        for sig in ("C1:c", "C1:e"):
            assert f"- {sig} (" not in s and f"- {sig}: " not in s


def test_replay_strips_decided_twins():
    text = "- C7:x (approved)\n- C2:x: rejected\n- C2:y: approved"
    digest = f"# h\n\n세션 `s`.\n\nR\n\n## 신호\n\n| sig |\n\n## 살아 있는 sig\n\n- C7:x (approved)\n- C2:y (watch)\n\n## 결정된 sig\n\n- C2:x: rejected\n- C1:Error: boom: approved\n- C2:y: approved\n\n규칙\n\n## sig 규칙\n\n- C2:x 는 그대로"
    got = judge._strip_labels(digest, {"C2:x", "C1:Error: boom"})
    assert "C7:x (approved)" not in got and "C2:x: rejected" not in got and "C1:Error: boom" not in got
    assert "- C2:y (watch)" in got and "- C2:y: approved" in got and "- C2:x 는 그대로" in got and "규칙" in got


def test_run_mentions_falls_back_to_signal_table():
    run = {"output": {"items": []}, "digest": "x\n\n| sig | 조건 |\n|---|---|\n| C1:c | C1 |"}
    assert judge._run_mentions(run, "C1:c", "C1:c") is True
    assert judge._run_mentions(run, "C1:e", "C1:e") is False


def test_replay_limit_and_cli(replay_world, capsys, monkeypatch):
    w = replay_world
    got = judge.replay(w["cand"], limit=1, claude_bin=w["exe"])
    assert len(got["items"]) == 1
    monkeypatch.setattr(judge, "replay", lambda rubric, limit=None, claude_bin="claude": {"rubric": str(rubric), "limit": limit})
    judge.main(["replay", "--rubric", str(w["cand"]), "--limit", "5"])
    assert json.loads(capsys.readouterr().out) == {"rubric": str(w["cand"]), "limit": 5}
    for bad in ("0", "-1"):
        with pytest.raises(SystemExit) as e:
            judge.main(["replay", "--rubric", str(w["cand"]), "--limit", bad])
        assert e.value.code == 2


def test_replay_ineligible_below_min_labels(replay_world):
    w = replay_world
    state.data_dir().joinpath("scoring.yaml").write_text("")
    got = judge.replay(w["cand"], claude_bin=w["exe"])
    assert got == {"eligible": False, "labels": 4, "replay_min_labels": 100}
    assert calls(w["exe"]) == []


def test_replay_judge_failure_is_reported_not_scored(replay_world, tmp_path):
    w = replay_world
    exe = make_fake(tmp_path, "fake-replay-fail", "echo boom >&2\nexit 1")
    got = judge.replay(w["cand"], claude_bin=exe)
    assert got["current"] == {"n_pos": 0, "n_neg": 0, "tpr": None, "tnr": None}
    assert all(i["current"] is None and i["candidate"] is None for i in got["items"])
    assert len(got["errors"]) == 2 and "boom" in got["errors"][0]["error"]
