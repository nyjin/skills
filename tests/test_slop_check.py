import json
import subprocess

from conftest import ROOT, SCRIPTS, load_script

slop = load_script("slop_check")

CLEAN = "# 정산 배치\n\n정산 배치는 매일 새벽 전날 주문을 가맹점별로 묶는다.\n\n송금액은 다음 날 확정한다.\n"
SLOPPY = (
    "# 정산 배치\n\n## 1. 흐름\n\n**정산은 하루 한 번 확정한다.** 그래서 송금이 늦다.\n\n"
    "정산 확정 ≠ 송금 완료다.\n"
)


def run(text, judgments=(), baseline=None):
    now, _ = slop.measure(text)
    first, lost = ({}, [])
    if baseline is not None:
        first, _ = slop.measure(baseline)
        lost = slop.preservation(baseline, text)
    return slop.render("doc.md", now, first, list(judgments), lost, baseline is not None)


def test_clean_document_passes():
    report, verdict = run(CLEAN)
    assert verdict == "통과"
    assert "판정: 통과" in report


def test_gate_items_fail_and_are_named_in_plain_words():
    report, verdict = run(SLOPPY)
    assert verdict == "미통과"
    assert "굵은 결정 문장으로 여는 문단" in report and "기호로 말을 이은 문장" in report
    assert "A3" not in report and "bold-verdict" not in report
    assert "1. 흐름 · " in report


def test_before_after_counts_and_fixed_examples():
    fixed = SLOPPY.replace("**정산은 하루 한 번 확정한다.**", "정산은 하루 한 번 확정한다.").replace(
        "정산 확정 ≠ 송금 완료다.", "정산이 확정돼도 송금이 끝난 것은 아니다.")
    judgments = [{"item": "A1", "outcome": "고침", "before": "정산 확정 ≠ 송금 완료다.",
                  "after": "정산이 확정돼도 송금이 끝난 것은 아니다."}]
    report, verdict = run(fixed, judgments, baseline=SLOPPY)
    assert verdict == "통과"
    assert "| 1 → 0 |" in report
    assert '"정산 확정 ≠ 송금 완료다." → "정산이 확정돼도 송금이 끝난 것은 아니다."' in report


def test_lost_facts_fail_unless_explained():
    before = "# 제목\n\n재시도는 `retry.max` 로 3번까지 한다. [런북](https://wiki.example.com/r)\n"
    after = "# 제목\n\n재시도는 몇 번까지 한다.\n"
    report, verdict = run(after, baseline=before)
    assert verdict == "미통과"
    assert "사실 보존: " in report and "식별자 `retry.max`" in report and "수치 3" in report
    _, verdict2 = run(after, [{"item": "D1", "outcome": "고침", "reason": "중복이라 지웠다"}], baseline=before)
    assert verdict2 == "통과"


def test_judgments_left_or_waiting_make_partial_verdict():
    judgments = [
        {"item": "C2", "outcome": "남김", "section": "4. 남은 일", "quote": "확인 필요", "reason": "결정 전이라 남겼다"},
        {"item": "B1", "outcome": "결정 대기", "reason": "목록과 표를 합칠까요? 추천: 합친다"},
        {"item": "C3", "outcome": "해당 없음"},
    ]
    report, verdict = run(CLEAN, judgments)
    assert verdict == "손볼 곳 남음"
    assert "남긴 것\n- 본문을 차지한 작업 메모 · 4. 남은 일: 결정 전이라 남겼다" in report
    assert "결정 대기\n- 목록과 표를 합칠까요?" in report
    assert "AI가 읽고 해당 없다고 본 항목: 본문에 남은 작성 과정" in report
    assert "|  · \"\" |" not in report


def test_unexplained_advisory_is_flagged():
    text = "# 제목\n\n" + "".join(f"**강조{i}** 문장이다. **또** 강조다.\n\n" for i in range(6))
    report, verdict = run(text)
    assert verdict == "손볼 곳 남음"
    assert "(아직 판단하지 않았다)" in report
    _, verdict2 = run(text, [{"item": "A4", "outcome": "남김", "reason": "용어 강조"}])
    assert verdict2 == "손볼 곳 남음"


def test_hold_items_are_not_reported(monkeypatch):
    items = json.loads(json.dumps(slop.RULES["items"]))
    items["A1"]["status"] = "hold"
    monkeypatch.setitem(slop.RULES, "items", items)
    report, _ = run("# 제목\n\n정산 확정 ≠ 송금 완료다.\n")
    assert "기호로 말을 이은 문장" not in report


def test_cli_exit_code_and_json(tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text(SLOPPY, encoding="utf-8")
    res = subprocess.run(["uv", "run", str(SCRIPTS / "slop_check.py"), str(doc), "--json"],
                         capture_output=True, text=True, cwd=ROOT)
    assert res.returncode == 1
    data = json.loads(res.stdout)
    assert data["verdict"] == "미통과" and data["found"]["A3"]


def test_values_moved_into_backticks_are_not_lost():
    before = "# 제목\n\n존재 확인은 SELECT 1 로 하고 del=0 인 행만 본다. 대상은 api_key 다.\n"
    after = "# 제목\n\n존재 확인은 `SELECT 1` 로 하고 `del=0` 인 행만 본다. 대상은 `api_key` 다.\n"
    assert slop.preservation(before, after) == []
    lost = slop.preservation("# 제목\n\n한도는 `retry.max` 로 3번이다.\n", "# 제목\n\n한도를 둔다.\n")
    assert "식별자 `retry.max`" in lost and "수치 3" in lost


def test_shortened_headings_and_section_refs_are_not_lost_facts():
    before = "# 제목\n\n## 3. 정산 방식 결정 — 3.A 회차 배치 vs 3.B 스트리밍 집계\n\n상위 정책 §3 을 따른다. 재시도는 2회다.\n"
    after = "# 제목\n\n## 3. 정산 방식: 회차 배치와 스트리밍 집계\n\n상위 정책의 확정 시각 규칙을 따른다. 재시도는 2회다.\n"
    assert slop.preservation(before, after) == []
    lost = slop.preservation("# 제목\n\n## 하나\n\n## 둘\n\n본문\n", "# 제목\n\n## 하나\n\n본문\n")
    assert lost == ["제목 1개"]


def test_finding_without_home_section_does_not_crash(monkeypatch):
    fake = {"findings": [{"kind": "diagram-transcribed", "home": None,
                          "where": [{"line": 5, "section": "흐름", "layer": "목록", "excerpt": "주문 API 가 토픽에 싣는다"}],
                          "action": "그림을 옮겼다"}], "metrics": {}}
    monkeypatch.setattr(slop.redundancy, "analyze", lambda text: fake)
    found, _ = slop.measure("# 제목\n\n본문이다.\n")
    assert found["B3"][0]["section"] == "흐름"
    assert "그림" in found["B3"][0]["action"]


def test_restated_lists_ignore_numbered_and_one_letter_keys():
    numbered = ("# 제목\n\n## 팝업\n\n| # | 문구 |\n|---|---|\n| 1 | 저장할까요 |\n| 2 | 닫을까요 |\n| 3 | 버릴까요 |\n\n"
                "## 순서\n\n| # | 동작 |\n|---|---|\n| 1 | 저장 |\n| 2 | 닫기 |\n| 3 | 버리기 |\n")
    assert slop.restated_lists(numbered) == []
    letters = ("# 제목\n\n## 후보\n\n| 후보 | 열 |\n|---|---|\n| a | x |\n| d | y |\n| e | z |\n\n"
               "## 결과\n\n| 후보 | 시간 |\n|---|---|\n| a | 1 |\n| d | 2 |\n| e | 3 |\n")
    assert slop.restated_lists(letters) == []
    named = ("# 제목\n\n## 역할\n\n| 라이브러리 | 역할 |\n|---|---|\n| querydsl-apt | 생성 |\n| querydsl-core | 타입 |\n| querydsl-jpa | 실행 |\n\n"
             "## 시점\n\n| 라이브러리 | 시점 |\n|---|---|\n| querydsl-apt | 빌드 |\n| querydsl-core | 컴파일 |\n| querydsl-jpa | 실행 |\n")
    assert len(slop.restated_lists(named)) == 1


def test_report_shows_prior_false_positive_and_human_stats(tmp_path):
    sdb = load_script("slop_db")
    con = sdb.connect(tmp_path / "t.duckdb")
    sdb.add_doc(con, "file:a.md", "slop", "AI")
    now, _ = slop.measure(SLOPPY)
    run = sdb.record(con, "file:a.md", {"verdict": "미통과", "rubric_version": 1, "found": now}, [], SLOPPY)
    fid = next(f["finding_id"] for f in sdb.findings(con, "file:a.md", run=run) if f["item"] == "A1")
    sdb.judge(con, fid, "오탐", note="수식 설명")
    report, _ = slop.render("a.md", now, {}, [], [], False, db=(con, "file:a.md"))
    assert "전에 사람이 오탐으로 판정한 곳" in report and "기호로 말을 이은 문장 · 1. 흐름" in report and "수식 설명" in report
    assert "사람 판정 누적: 기호로 말을 이은 문장 1건 중 오탐 1건" in report


def test_document_level_finding_keeps_id_when_numbers_change():
    text = "# 제목\n\n" + "".join(f"**강조{i}** 문장이다. **또** 강조다.\n\n" for i in range(6))
    more = text + "평범한 문장이다.\n\n또 평범한 문장이다.\n"
    a, _ = slop.measure(text)
    b, _ = slop.measure(more)
    assert a["A4"][0]["quote"] != b["A4"][0]["quote"]
    assert a["A4"][0]["text"] == b["A4"][0]["text"] == "문서 전체"
