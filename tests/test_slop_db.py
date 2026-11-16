import json
import subprocess

from conftest import ROOT, SCRIPTS, load_script

db = load_script("slop_db")

CHECK = {
    "verdict": "미통과", "rubric_version": 1,
    "found": {
        "A1": [{"line": 3, "section": "1. 흐름", "quote": "정산 확정 ≠ 송금…", "text": "정산 확정 ≠ 송금 완료다."}],
        "A3": [{"line": 5, "section": "1. 흐름", "quote": "정산은 하루 한…", "text": "**정산은 하루 한 번 확정한다.** 그래서 늦다."}],
        "A4": [],
    },
}
JUDG = [{"item": "C2", "outcome": "남김", "section": "4. 남은 일", "quote": "확인 필요 목록", "reason": "결정 전"},
        {"item": "C3", "outcome": "해당 없음"}]


def fresh(tmp_path):
    con = db.connect(tmp_path / "t.duckdb")
    db.add_doc(con, "confluence:100", "slop", "AI 초안", title="정산 배치")
    return con


def test_record_stores_run_and_findings_with_stable_ids(tmp_path):
    con = fresh(tmp_path)
    run1 = db.record(con, "confluence:100", CHECK, JUDG, doc_text="v1")
    run2 = db.record(con, "confluence:100", CHECK, JUDG, doc_text="v2")
    assert run1 != run2
    f1 = db.findings(con, "confluence:100", run=run1)
    f2 = db.findings(con, "confluence:100", run=run2)
    assert [f["item"] for f in f1] == ["A1", "A3", "C2"]
    assert [f["finding_id"] for f in f1] == [f["finding_id"] for f in f2]
    assert f1[0]["means"] == "스크립트" and f1[2]["means"] == "AI 판단"


def test_judge_miss_history_and_stats(tmp_path):
    con = fresh(tmp_path)
    run = db.record(con, "confluence:100", CHECK, JUDG, doc_text="v1")
    fs = db.findings(con, "confluence:100", run=run)
    db.judge(con, fs[0]["finding_id"], "오탐", note="수식 설명이다", by="tester")
    db.judge(con, fs[1]["finding_id"], "동의", by="tester")
    db.miss(con, "confluence:100", "C4", "부분 환불을 재처리할 수 있나", section="3. 재처리", by="tester")
    pending = db.findings(con, "confluence:100", run=run, pending=True)
    assert [f["item"] for f in pending] == ["C2"]
    hist = db.history(con, "confluence:100")
    assert {(h["item"], h["verdict"]) for h in hist} == {("A1", "오탐"), ("A3", "동의"), ("C4", "놓침")}
    st = {s["item"]: s for s in db.stats(con)}
    assert st["A1"]["오탐"] == 1 and st["A3"]["동의"] == 1 and st["C4"]["놓침"] == 1


def test_prior_false_positive_is_found_by_finding_id(tmp_path):
    con = fresh(tmp_path)
    run = db.record(con, "confluence:100", CHECK, [], doc_text="v1")
    fid = db.findings(con, "confluence:100", run=run)[0]["finding_id"]
    db.judge(con, fid, "오탐")
    assert db.prior_false(con, "confluence:100") == {fid}


def test_judge_rejects_unknown_finding_and_bad_verdict(tmp_path):
    con = fresh(tmp_path)
    for args in (("없는id", "오탐"), ):
        try:
            db.judge(con, *args)
            raise AssertionError("예외가 나야 한다")
        except SystemExit as e:
            assert "찾은 곳" in str(e)
    run = db.record(con, "confluence:100", CHECK, [], doc_text="v1")
    fid = db.findings(con, "confluence:100", run=run)[0]["finding_id"]
    try:
        db.judge(con, fid, "몰라")
        raise AssertionError("예외가 나야 한다")
    except SystemExit as e:
        assert "동의" in str(e)


def test_changes_are_logged(tmp_path):
    con = fresh(tmp_path)
    db.log_change(con, "B2", "status", "hold", "advisory", "검증용에서도 적중", by="tester")
    rows = db.changes(con)
    assert rows[0]["item"] == "B2" and rows[0]["after"] == "advisory"


def test_cli_findings_and_judge(tmp_path):
    path = tmp_path / "c.duckdb"
    check = tmp_path / "check.json"
    check.write_text(json.dumps(CHECK, ensure_ascii=False), encoding="utf-8")
    cli = lambda *a: subprocess.run(["uv", "run", str(SCRIPTS / "slop_db.py"), "--db", str(path), *a],
                                    capture_output=True, text=True, cwd=ROOT)
    assert cli("doc-add", "--source", "file:a.md", "--label", "slop", "--reason", "AI").returncode == 0
    out = cli("record", "--source", "file:a.md", "--check", str(check))
    assert out.returncode == 0, out.stderr
    listing = cli("findings", "--source", "file:a.md", "--pending")
    assert "기호로 말을 이은 문장" in listing.stdout and "| 판정 |" in listing.stdout
    fid = listing.stdout.split("\n")[2].split("|")[1].strip()
    assert cli("judge", "--finding", fid, "--verdict", "오탐").returncode == 0
    assert "오탐" in cli("history", "--source", "file:a.md").stdout
