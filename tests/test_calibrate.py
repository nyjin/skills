import json
from pathlib import Path

from conftest import SCRIPTS, load_script

cal = load_script("calibrate")

CLEAN = "# 정산\n\n정산 배치는 매일 새벽 주문을 묶는다. 송금액은 다음 날 정한다.\n"


sdb = load_script("slop_db")


def setup(tmp_path, refs, slops, judgments=()):
    """표본을 판정 저장소에 넣는다. judgments 는 (항목, 판정) 목록."""
    d = tmp_path / "cal"
    (d / "files").mkdir(parents=True)
    con = sdb.connect(d / "slop.duckdb")
    for i, text in enumerate(refs):
        (d / "files" / f"r{i}.md").write_text(text, encoding="utf-8")
        sdb.add_doc(con, f"file:files/r{i}.md", "approved", "기준")
    for i, text in enumerate(slops):
        (d / "files" / f"s{i}.md").write_text(text, encoding="utf-8")
        sdb.add_doc(con, f"file:files/s{i}.md", "slop", "AI")
    for item, verdict in judgments:
        con.execute("INSERT INTO judgments VALUES ('x', 'file:files/s0.md', ?, 'A ≠ B', '', ?, '', '', 1, now())",
                    (item, verdict))
    con.close()
    return d


def slop_doc(i):
    return f"# 문서{i}\n\n**결정{i}을 내린다.** 근거가 있다.\n\n정산 확정 ≠ 송금 완료{i}다.\n"


def test_rule_with_no_false_positive_and_three_hits_stays_gate(tmp_path):
    d = setup(tmp_path, [CLEAN] * 3, [slop_doc(i) for i in range(6)])
    result = cal.run(d, holdout_every=0)
    a1 = result["items"]["A1"]
    assert a1["proposal"]["status"] == "gate"
    assert a1["reference_false_lines"] == 0 and a1["slop_docs_hit"] == 6


def test_false_positive_in_reference_demotes_to_advisory(tmp_path):
    d = setup(tmp_path, [CLEAN, "# 기준\n\n비용 = 시간이다.\n"], [slop_doc(i) for i in range(4)])
    a1 = cal.run(d, holdout_every=0)["items"]["A1"]
    assert a1["reference_false_lines"] == 1
    assert a1["proposal"]["status"] == "advisory"


def test_too_few_hits_holds(tmp_path):
    d = setup(tmp_path, [CLEAN] * 2, [slop_doc(0), CLEAN, CLEAN])
    assert cal.run(d, holdout_every=0)["items"]["A1"]["proposal"]["status"] == "hold"


def test_user_false_positive_record_demotes_gate(tmp_path):
    d = setup(tmp_path, [CLEAN] * 3, [slop_doc(i) for i in range(6)], judgments=[("A1", "오탐")])
    result = cal.run(d, holdout_every=0)
    assert result["items"]["A1"]["proposal"]["status"] == "advisory"
    assert result["judgments"] == {"오탐": 1, "놓침": 0}


def test_switch_threshold_is_one_above_the_lowest_clean_value(tmp_path):
    mixed = "# 문서\n\n" + "가a" * 7 + "다.\n"  # 한 문장에 전환 7번
    heavy = "# 문서\n\n" + "가a" * 12 + "다.\n"
    d = setup(tmp_path, [CLEAN, mixed], [heavy] * 4)
    a2 = cal.run(d, holdout_every=0)["items"]["A2"]
    assert a2["proposal"]["threshold"] == 9


def test_holdout_is_stable_and_recorded(tmp_path):
    d = setup(tmp_path, [CLEAN] * 3, [slop_doc(i) for i in range(6)])
    cal.run(d, holdout_every=3)
    first = [x["holdout"] for x in sdb.doc_list(sdb.connect(d / "slop.duckdb"))]
    cal.run(d, holdout_every=3)
    second = [x["holdout"] for x in sdb.doc_list(sdb.connect(d / "slop.duckdb"))]
    assert first == second and all(h is not None for h in first)


def test_writes_proposal_and_report_without_touching_live_registry(tmp_path):
    live = (SCRIPTS / "slop_rules.json").read_text(encoding="utf-8")
    d = setup(tmp_path, [CLEAN] * 3, [slop_doc(i) for i in range(6)])
    result = cal.run(d, holdout_every=0)
    report = cal.render(result)
    cal.write_outputs(d, result, report)
    assert (SCRIPTS / "slop_rules.json").read_text(encoding="utf-8") == live
    proposed = json.loads((d / "slop_rules.proposed.json").read_text(encoding="utf-8"))
    assert proposed["items"]["A1"]["status"] == "gate"
    assert "### 기준 점검 (표본 9편" in report
    assert "| 기호로 말을 이은 문장 | 게이트 | 유지 |" in report
    assert "A1" not in report.split("\n", 2)[2]


def test_policy_items_keep_status_but_report_numbers(tmp_path):
    d = setup(tmp_path, [CLEAN, "# 기준\n\n이 결정은 ADR 0067 을 따른다.\n"],
              [f"# 문서{i}\n\nPAY-{i}00 을 따른다.\n" for i in range(4)])
    result = cal.run(d, holdout_every=0)
    c1 = result["items"]["C1"]
    assert c1["reference_false_lines"] == 1
    assert c1["proposal"]["status"] == "gate"
    assert "| 독자가 풀 수 없는 번호 참조 | 게이트 | 유지 (사용자 규칙) |" in cal.render(result)


def test_evidence_lists_actual_hits_and_reference_lines(tmp_path):
    d = setup(tmp_path, [CLEAN, "# 기준\n\n비용 = 시간이다.\n"], [slop_doc(i) for i in range(4)])
    result = cal.run(d, holdout_every=0)
    ev = cal.render_evidence(result)
    assert "## 기호로 말을 이은 문장" in ev
    assert "기준 문서에서 걸린 줄" in ev and "files/r1.md" in ev and "비용 = 시간이다" in ev
    assert "AI 문서에서 걸린 줄" in ev and "files/s0.md" in ev and "정산 확정 ≠ 송금 완료0다" in ev


def test_apply_changes_only_approved_items_and_reports_diff(tmp_path):
    live = tmp_path / "slop_rules.json"
    live.write_text((SCRIPTS / "slop_rules.json").read_text(encoding="utf-8"), encoding="utf-8")
    proposed = json.loads(live.read_text(encoding="utf-8"))
    proposed["items"]["B2"]["status"] = "advisory"
    proposed["items"]["A1"]["status"] = "hold"
    prop = tmp_path / "slop_rules.proposed.json"
    prop.write_text(json.dumps(proposed, ensure_ascii=False), encoding="utf-8")
    con = sdb.connect(tmp_path / "c.duckdb")
    diff = cal.apply(prop, live, ["B2"], con=con, by="tester")
    after = json.loads(live.read_text(encoding="utf-8"))
    assert after["items"]["B2"]["status"] == "advisory"
    assert after["items"]["A1"]["status"] == "gate"
    assert "구조만 바꿔 다시 쓴 목록: 상태 보류 → 지시" in diff
    assert "기호로 말을 이은 문장" not in diff
    logged = sdb.changes(con)
    assert [(c["item"], c["field"], c["before"], c["after"]) for c in logged] == [("B2", "status", "hold", "advisory")]
