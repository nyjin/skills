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

    rec = cli(capsys, "decide", "release-adr-number", "proceed", "--reason", "맞다")
    assert rec["decision"] == "proceed" and rec["note"] == "맞다"
    metrics = cli(capsys, "metrics")
    assert metrics["precision"] == 1.0
    replay = cli(capsys, "replay-inputs")
    assert replay == [str(paths.data_dir() / "replay" / "release-adr-number" / "input.json")]
    ticket = cli(capsys, "ticket-file", "release-adr-number", "--out-dir", tmp_path / "t")
    assert Path(ticket["file"]).read_text().startswith("# [결정 대상] a — 문제")


def test_cli_validation_error_exit_1(tmp_path, capsys):
    bad = write(tmp_path / "bad.json", {"observations": [{"evidence_id": "x"}]})
    code, cap = cli_fail(capsys, "observe", "--file", bad)
    assert code == 1 and cap.out == "" and "필수 항목" in cap.err
    code, cap = cli_fail(capsys, "decide", "없는-묶음", "proceed")
    assert code == 1 and "알 수 없는 묶음" in cap.err
    code, cap = cli_fail(capsys, "ticket-file", "없는-묶음", "--out-dir", tmp_path)
    assert code == 1 and cap.out == ""
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
        decision(gid, "proceed")
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
    assert json.loads(r.stdout) == {"positive": 0, "negative": 0, "precision": None}
