import json
import subprocess
from pathlib import Path

import pytest

import groups
import paths
import retro

from .test_groups import group
from .test_rubric import decision, ids, labels_mix  # noqa: F401

PLUGIN = Path(__file__).resolve().parents[1]
NOW = "2026-11-06T12:00:00+09:00"


def cli(capsys, *argv):
    retro.main([str(a) for a in argv])
    return json.loads(capsys.readouterr().out)


def cli_fail(capsys, *argv):
    with pytest.raises(SystemExit) as e:
        retro.main([str(a) for a in argv])
    return e.value.code, capsys.readouterr()


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return path


def test_cli_pipeline_end_to_end(redesign_sessions, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(paths, "now", lambda: NOW)
    repo = redesign_sessions["repo"]
    assert cli(capsys, "backfill") == {"sessions": 2, "records": 13}

    batch = tmp_path / "batch.json"
    assert cli(capsys, "pending", "--out", batch, "--limit", 0)["remaining"] == 9
    out = cli(capsys, "pending", "--out", batch, "--limit", 5)
    assert out == {"count": 5, "file": str(batch.resolve()), "remaining": 4}
    out = cli(capsys, "pending", "--out", batch)
    assert out["count"] == 9 and out["remaining"] == 0
    data = json.loads(batch.read_text())
    assert data["examples"] == []
    by_text = {e["text"]: e["id"] for e in data["evidence"]}
    first = by_text["아니, ADR 번호는 docs/adr 에서 다음 번호를 찾아야 해"]
    second = by_text["ADR 번호가 또 틀렸어, 다음 번호를 써야지"]
    entries = [{"evidence_id": e["id"], "type": "correction", "verdict": e["id"] in (first, second),
                "reason": "ADR 번호 교정" if e["id"] in (first, second) else "결함 아님"} for e in data["evidence"]]
    assert cli(capsys, "observe", "--file", write(tmp_path / "obs.json", {"observations": entries})) == {"saved": 9, "skipped": 0}

    ctx = cli(capsys, "context")
    assert sorted(o["evidence_id"] for o in ctx["observations"]) == sorted([first, second])
    assert ctx["groups"] == {} and ctx["decisions"] == {} and ctx["examples"] == []
    assert Path(ctx["rubric"]).is_absolute() and Path(ctx["rubric"]).read_text().startswith("<!-- rubric-version:")

    target = str(repo / "skills/a/SKILL.md")
    g = group("release-adr-number", "fix", [first, second], finding="wrong", target_path=target)
    assert cli(capsys, "groups", "--file", write(tmp_path / "groups.json", {"groups": [g]})) == {"changed": ["release-adr-number"]}

    gate = cli(capsys, "gate")
    assert gate["report"][0]["id"] == "release-adr-number"
    assert gate["report"][0]["apply_target"]["kind"] == "repo"

    rec = cli(capsys, "decide", "release-adr-number", "accept", "--reason", "맞다")
    assert rec["decision"] == "accept" and rec["note"] == "맞다"
    metrics = cli(capsys, "metrics")
    assert set(metrics) == {"overall", "by_target", "by_rubric_version"}
    assert metrics["overall"] == {"accepted": 1, "rejected": 0, "rate": 1.0}
    replay = cli(capsys, "replay-inputs")
    assert replay == [str(paths.data_dir() / "replay" / "release-adr-number" / "input.json")]


def test_cli_validation_error_exit_1(tmp_path, capsys):
    bad = write(tmp_path / "bad.json", {"observations": [{"evidence_id": "x"}]})
    code, cap = cli_fail(capsys, "observe", "--file", bad)
    assert code == 1 and cap.out == "" and "필수 항목" in cap.err
    code, cap = cli_fail(capsys, "decide", "없는-묶음", "accept")
    assert code == 1 and "알 수 없는 묶음" in cap.err
    code, cap = cli_fail(capsys, "decide", "g", "proceed")
    assert code == 1 and "invalid choice" in cap.err
    code, cap = cli_fail(capsys, "ticket-file", "g", "--out-dir", tmp_path)
    assert code == 1 and cap.out == "" and "invalid choice" in cap.err
    code, cap = cli_fail(capsys, "decide", "g", "maybe")
    assert code == 1 and "invalid choice" in cap.err
    code, cap = cli_fail(capsys, "observe", "--file", tmp_path / "없음.json")
    assert code == 1


def test_cli_lock_contention_exit_3(tmp_path, capsys):
    d = paths.data_dir()
    assert paths.acquire_lock(d)
    try:
        code, cap = cli_fail(capsys, "groups", "--file", write(tmp_path / "g.json", {"groups": []}))
    finally:
        paths.release_lock(d)
    assert code == paths.LOCKED_EXIT and cap.out == ""
    assert cli(capsys, "groups", "--file", tmp_path / "g.json") == {"changed": []}
    assert not (d / "lock").exists()


def test_context_label_examples_alternate(labels_mix, capsys):
    train, _ = labels_mix
    pos = ids("train", 2, "p")
    for gid in pos:
        decision(gid, "accept")
    decision(ids("train", 1, "w")[0], "reject", "wont-fix")
    ex = cli(capsys, "context")["examples"]
    assert [e["label"] for e in ex] == ["positive", "negative", "positive", "negative", "negative", "negative"]
    assert {e["group"] for e in ex} == set(pos) | set(train)
    assert set(ex[0]) == {"group", "label", "kind", "problem", "note"}
    (paths.data_dir() / "config.yaml").write_text("label_examples: 3\n")
    assert [e["label"] for e in cli(capsys, "context")["examples"]] == ["positive", "negative", "positive"]


def test_pending_includes_not_defect_examples(labels_mix, obs_factory, tmp_path, capsys):
    train, _ = labels_mix
    eid = obs_factory("correction")
    groups.merge({"groups": [group(train[3], "fix", [eid], finding="wrong")]})
    decision(ids("test", 3, "x")[2], "reject", "not-defect")
    out = cli(capsys, "pending", "--out", tmp_path / "p.json", "--limit", 0)
    assert out == {"count": 0, "file": str((tmp_path / "p.json").resolve()), "remaining": 0}
    data = json.loads((tmp_path / "p.json").read_text())
    assert data["examples"] == [{"group": train[3], "problem": "문제", "reason": "이유"}]


def test_rubric_commands(labels_mix, tmp_path, capsys):
    train, test = labels_mix
    assert cli(capsys, "rubric-candidates")["ready"] == {"not-defect": False, "trivial": True}
    out = tmp_path / "rubric.md"
    res = cli(capsys, "rubric-apply", "--kind", "trivial", "--rule", "사소한 표현 차이", "--evidence", *train[:3],
              "--out", out)
    assert res == {"version": 2, "out": str(out.resolve())} and "- 사소한 표현 차이" in out.read_text()
    code, cap = cli_fail(capsys, "rubric-apply", "--kind", "trivial", "--rule", "r", "--evidence", *train[:3],
                         "--dropped", test[1])
    assert code == 1 and "사용자 확인" in cap.err
    assert cli(capsys, "rubric-apply", "--kind", "trivial", "--rule", "r", "--evidence", *train[:3],
               "--dropped", test[1], "--confirmed") == {"version": 2}
    side = {"rubric_version": 1, "results": [{"group": test[1], "surfaces": True}]}
    score = cli(capsys, "replay-score", "--current", write(tmp_path / "a.json", side),
                "--candidate", write(tmp_path / "b.json", {**side, "results": []}))
    assert score["dropped_positives"] == [test[1]] and score["eligible"] is False


def test_cli_subprocess_smoke():
    r = subprocess.run(["uv", "run", "--script", str(PLUGIN / "scripts" / "retro.py"), "metrics"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["overall"] == {"accepted": 0, "rejected": 0, "rate": None}


def test_observe_pending_requires_every_evidence_id(tmp_path, capsys):
    hdir = paths.projects_root() / "p" / "s9" / "harness"
    hdir.mkdir(parents=True)
    recs = [{"id": f"e{i}", "kind": "prompt", "at": NOW, "session": "s9", "transcript": "", "pos": 0, "cwd": "/w/o/p",
             "skill": None, "text": "t", "file": None, "flags": {}, "meta": {}} for i in (1, 2)]
    (hdir / "evidence.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    pend = write(tmp_path / "pending.json", {"evidence": recs, "examples": []})
    only_second = write(tmp_path / "o.json", {"observations": [
        {"evidence_id": "e2", "type": "correction", "verdict": False, "reason": "아님"}]})
    code, cap = cli_fail(capsys, "observe", "--file", only_second, "--pending", pend)
    assert code == 1 and cap.out == "" and cap.err.rstrip().endswith("판정하지 않은 근거 1건: e1")
    assert not (paths.data_dir() / "observations.jsonl").exists()
    assert not (hdir / "evidence.cursor").exists()
    both = write(tmp_path / "o2.json", {"observations": [
        {"evidence_id": e, "type": "correction", "verdict": False, "reason": "아님"} for e in ("e1", "e2")]})
    assert cli(capsys, "observe", "--file", both, "--pending", pend) == {"saved": 2, "skipped": 0}


def _archive(gid, eid, at="2025-01-01T00:00:00+09:00"):
    ev = {"id": eid, "kind": "prompt", "at": at, "session": "s1", "cwd": "/w/org/proj-a", "skill": None,
          "text": "t", "file": None, "meta": {}}
    inp = paths.data_dir() / "replay" / gid / "input.json"
    inp.parent.mkdir(parents=True, exist_ok=True)
    write(inp, {"group": group(gid, "fix", [eid], finding="wrong"),
                "observations": [{"evidence_id": eid, "observation": None, "evidence": ev, "excerpt": ""}],
                "file_versions": {}, "rubric": {"version": 1, "text": ""}})


def test_replay_gate_applies_gate_to_rejudged(tmp_path, capsys):
    _archive("g1", "a1")
    _archive("g2", "b1")
    entry = {"type": "failure", "reason": "r"}
    data = {"rubric_version": 2, "results": [
        {"group": "g1", "observations": [{**entry, "evidence_id": "a1", "verdict": True}]},
        {"group": "g2", "observations": [{**entry, "evidence_id": "b1", "verdict": False}]}]}
    out = tmp_path / "gated.json"
    res = cli(capsys, "replay-gate", "--file", write(tmp_path / "rj.json", data), "--out", out)
    assert res == {"rubric_version": 2,
                   "results": [{"group": "g1", "surfaces": True}, {"group": "g2", "surfaces": False}]}
    assert json.loads(out.read_text()) == res
    data["results"][0]["group_fields"] = {"verified": None}
    res = cli(capsys, "replay-gate", "--file", write(tmp_path / "rj.json", data), "--out", out)
    assert res["results"][0]["surfaces"] is False


def test_replay_gate_rejects_bad_input(tmp_path, capsys):
    _archive("g1", "a1")
    bad = {"rubric_version": 1, "results": [{"group": "g1", "observations": [
        {"evidence_id": "zz", "type": "failure", "verdict": True, "reason": "r"}]}]}
    out = tmp_path / "o.json"
    code, cap = cli_fail(capsys, "replay-gate", "--file", write(tmp_path / "b.json", bad), "--out", out)
    assert code == 1 and "zz" in cap.err
    bad["results"][0]["group"] = "없음"
    code, cap = cli_fail(capsys, "replay-gate", "--file", write(tmp_path / "b.json", bad), "--out", out)
    assert code == 1 and "보관본" in cap.err
    code, cap = cli_fail(capsys, "replay-gate", "--file", write(tmp_path / "b.json", {"results": []}), "--out", out)
    assert code == 1 and "rubric_version" in cap.err
    assert not out.exists()


def test_replay_gate_requires_every_archived_evidence(tmp_path, capsys):
    _archive("g1", "a1")
    inp = paths.data_dir() / "replay" / "g1" / "input.json"
    arch = json.loads(inp.read_text())
    arch["observations"].append({**arch["observations"][0], "evidence_id": "a2"})
    write(inp, arch)
    entry = {"type": "failure", "verdict": True, "reason": "r"}
    data = {"rubric_version": 2, "results": [{"group": "g1", "observations": [{**entry, "evidence_id": "a1"}]}]}
    out = tmp_path / "gated.json"
    code, cap = cli_fail(capsys, "replay-gate", "--file", write(tmp_path / "rj.json", data), "--out", out)
    assert code == 1 and cap.err.rstrip().endswith("다시 판정하지 않은 근거 1건: a2")
    assert not out.exists()
    data["results"][0]["observations"].append({**entry, "evidence_id": "a2"})
    assert cli(capsys, "replay-gate", "--file", write(tmp_path / "rj.json", data), "--out", out)["results"] == [
        {"group": "g1", "surfaces": True}]


def test_observe_rejects_non_object_before_pending_check(tmp_path, capsys):
    pend = write(tmp_path / "pending.json", {"evidence": [{"id": "e1"}], "examples": []})
    code, cap = cli_fail(capsys, "observe", "--file", write(tmp_path / "o.json", ["x"]), "--pending", pend)
    assert code == 1 and "object" in cap.err and "Traceback" not in cap.err


def test_pending_adds_repeat_stats_and_recent_purposes(obs_factory, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(paths, "now", lambda: NOW)
    obs_factory("repeat", at="2026-11-05T10:00:00+09:00")
    obs_factory("repeat", at="2025-01-01T10:00:00+09:00")  # window_days 밖
    obs_factory("repeat", verdict=False)
    lines = paths.data_dir() / "observations.jsonl"
    rows = [json.loads(ln) for ln in lines.read_text().splitlines()]
    rows[1]["purpose"], rows[2]["purpose"] = "오래된 목적", "거짓 목적"
    lines.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))

    def bash(session, n, name):
        hdir = paths.projects_root() / "q" / session / "harness"
        hdir.mkdir(parents=True, exist_ok=True)
        rec = {"id": f"{session}-{n}", "kind": "bash", "at": "2026-11-05T11:00:00+09:00", "session": session,
               "transcript": "", "pos": 0, "cwd": "/w/o/p", "skill": None, "text": "python3 - <<EOF",
               "file": str(hdir / "scripts" / f"{name}.txt") if name else None, "flags": {}, "meta": {}}
        with open(hdir / "evidence.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    bash("t1", 1, "abc")
    bash("t1", 2, "abc")
    bash("t2", 1, "abc")
    bash("t2", 2, "def")
    bash("t2", 3, None)
    cli(capsys, "pending", "--out", tmp_path / "p.json")
    data = json.loads((tmp_path / "p.json").read_text())
    stats = {e["id"]: e.get("repeat_stats") for e in data["evidence"] if e["kind"] == "bash"}
    assert stats == {"t1-1": {"count": 3, "sessions": 2}, "t1-2": {"count": 3, "sessions": 2},
                     "t2-1": {"count": 3, "sessions": 2}, "t2-2": {"count": 1, "sessions": 1}, "t2-3": None}
    assert data["recent_purposes"] == ["같은 목적"]
