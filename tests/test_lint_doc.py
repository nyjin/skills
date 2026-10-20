import json
import subprocess
from pathlib import Path

from conftest import ROOT, SCRIPTS, load_script

lint_doc = load_script("lint_doc")


def rules(text):
    return [(f.line, f.rule, f.level) for f in lint_doc.lint(text)]


def test_clean_document_has_no_findings():
    text = "# 배포 가이드\n\n개요.\n\n## 사전 준비\n\n```bash\nmake build\n```\n"
    assert rules(text) == []


def test_code_block_without_language_is_error():
    assert rules("# 제목\n\n```\nls\n```\n") == [(3, "code-lang", "error")]


def test_heading_inside_fence_ignored():
    text = "# 제목\n\n```bash\n# 이건 주석\n#### 이것도 주석\n```\n"
    assert rules(text) == []


def test_tilde_fence_and_longer_backtick_fence():
    text = "# 제목\n\n~~~yaml\n# a\n~~~\n\n````markdown\n```\n# 안쪽\n```\n````\n"
    assert rules(text) == []


def test_unclosed_fence_is_error():
    assert ("unclosed-fence", "error") in [(r, l) for _, r, l in rules("# 제목\n\n```bash\nls\n")]


def test_h4_warns_depth():
    text = "# 제목\n## 둘\n### 셋\n#### 넷\n"
    assert (4, "heading-depth", "warn") in rules(text)


def test_title_length_counts_visible_chars():
    ok = "# " + "가" * 30
    long = "# " + "가" * 31
    code = "# `" + "a" * 28 + "` 설정"  # 보이는 글자 31 → 경고
    link = "# [짧은 제목](https://example.com/very/long/url/that/should/not/count)"
    assert rules(ok) == []
    assert rules(long) == [(1, "heading-length", "warn")]
    assert rules(code) == [(1, "heading-length", "warn")]
    assert rules(link) == []


def test_question_or_exclamation_heading_warns():
    assert rules("# 에러를 해결하는 방법은?") == [(1, "heading-punct", "warn")]
    assert rules("# 지금 시작하세요!") == [(1, "heading-punct", "warn")]


def test_multiple_h1_warns():
    assert (3, "multiple-h1", "warn") in rules("# 하나\n\n# 둘\n")


def test_heading_skip_warns():
    assert rules("# 제목\n### 건너뜀\n") == [(2, "heading-skip", "warn")]


def test_html_comment_lines_ignored():
    assert rules("# 제목\n<!-- E-1: 개요 -->\n\n개요.\n") == []


def test_cli_json_and_exit_code(tmp_path: Path):
    doc = tmp_path / "doc.md"
    doc.write_text("# 제목\n\n```\nls\n```\n")
    proc = subprocess.run(
        ["uv", "run", str(SCRIPTS / "lint_doc.py"), str(doc), "--json"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 1
    data = json.loads(proc.stdout)
    assert data[0]["rule"] == "code-lang"


def test_cli_missing_file_reports_error_without_traceback(tmp_path: Path):
    proc = subprocess.run(
        ["uv", "run", str(SCRIPTS / "lint_doc.py"), str(tmp_path / "없음.md")],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr
    assert "없음.md" in proc.stderr


def test_abbreviation_first_use_without_expansion_warns():
    assert ("abbr-first", "warn") in [(r, l) for _, r, l in rules("# 제목\n\nDLQ를 매일 확인합니다.\n")]


def test_abbreviation_expanded_on_first_use_ok():
    text = "# 제목\n\n실패 메시지 큐(Dead Letter Queue, DLQ)를 봅니다. DLQ는 매일 비웁니다.\n"
    assert rules(text) == []
    assert rules("# 제목\n\nDLQ(Dead Letter Queue)를 봅니다.\n") == []


def test_common_abbreviations_and_code_are_ignored():
    text = "# 제목\n\nAPI와 URL, JSON, SQL은 그대로 씁니다. `XYZQ` 값과\n\n```bash\nFOO=1 BAR=2\n```\n"
    assert rules(text) == []


def test_fence_indented_in_long_ordered_list_is_checked():
    text = "# 제목\n\n10. 실행합니다.\n\n    ```\n    ls\n    ```\n"
    assert ("code-lang", "error") in [(r, l) for _, r, l in rules(text)]


def test_title_length_keeps_underscores_in_identifiers():
    assert lint_doc.visible_len("`max_pool_size` 설정") == len("max_pool_size 설정")
    assert lint_doc.visible_len("**굵게** 쓰기") == len("굵게 쓰기")


def test_code_location_reference_warns():
    text = "# 제목\n\n근거: `identity/config/SecurityConfig.java:47-60`\n"
    assert (3, "code-location", "warn") in rules(text)


def test_code_location_inside_fence_or_plain_filename_ok():
    text = "# 제목\n\n`SecurityConfig.java` 가 체인을 만든다.\n\n```text\nat Foo.java:12\n```\n"
    assert all(r != "code-location" for _, r, _ in rules(text))


def test_unlinked_ticket_and_decision_numbers_warn():
    text = "# 제목\n\n체인을 세웠다(PAY-4553, ADR 0067).\n\n| 행 | 리프레시는 PAY-4065 에서 |\n"
    found = [(n, r) for n, r, _ in rules(text) if r == "unlinked-ref"]
    assert found == [(3, "unlinked-ref"), (5, "unlinked-ref")]


def test_linked_numbers_code_headings_and_standard_names_ok():
    text = (
        "# 제목\n\n## PAY-4521 이후\n\n"
        "[PAY-4521](https://t.example.com/browse/PAY-4521)에서 정한다. [결정 기록 ADR 0063](https://g.example.com/0063.md)\n\n"
        "브랜치 `feature/PAY-4550`, 해시는 SHA-256, 인코딩은 UTF-8, 표준은 ISO-8601 이다.\n"
    )
    assert all(r != "unlinked-ref" for _, r, _ in rules(text))


def test_per_file_source_link_warns_even_in_html_summary():
    text = (
        "# 제목\n\n"
        '<summary><a href="https://git.example.com/pay/pay-api/blob/7d1bc90/src/ApproveHandler.java#L42-L87">ApproveHandler</a></summary>\n\n'
        "[처리기](https://git.example.com/pay/pay-api/blob/main/src/main/java/RetryConfig.java)\n"
    )
    found = [n for n, r, _ in rules(text) if r == "file-link"]
    assert found == [3, 5]


def test_repository_pr_and_document_links_ok():
    text = (
        "# 제목\n\n"
        "[저장소](https://git.example.com/pay/pay-api) · [PR #318](https://git.example.com/pay/pay-api/pull/318) · "
        "[결정 기록](https://git.example.com/pay/pay-api/blob/main/docs/adr/0031-session-store.md)\n"
    )
    assert all(r != "file-link" for _, r, _ in rules(text))


def test_three_bold_lead_bullets_in_a_row_warn():
    text = "# 제목\n\n- **체인을 둔다.** 헤더가 붙는다\n- **CSRF 를 끈다.** 쿠키가 없다\n- **허용 목록으로 연다.** 새 경로는 적어야 열린다\n"
    assert [(n, r) for n, r, _ in rules(text) if r == "bold-lead-list"] == [(3, "bold-lead-list")]


def test_two_bold_lead_bullets_or_plain_bullets_ok():
    text = "# 제목\n\n- **체인을 둔다.** 헤더가 붙는다\n- **CSRF 를 끈다.** 쿠키가 없다\n\n- 그냥 항목\n- 또 다른 항목\n- 세 번째 항목\n"
    assert all(r != "bold-lead-list" for _, r, _ in rules(text))


def test_unlinked_numbers_with_attached_particles_warn():
    text = "# 제목\n\n경로 분담은 ADR 0067이 정했다.\n\n이 결정이 PAY-4065를 막았다.\n"
    assert [n for n, r, _ in rules(text) if r == "unlinked-ref"] == [3, 5]


def test_paragraph_opened_with_bold_verdict_warns():
    text = (
        "# 제목\n\n**등록순을 씁니다.** 조회 필터가 없어 목록이 늘 전체입니다.\n\n"
        "`메모`**를 목록에 꺼냅니다.** 수동 그룹은 단서가 없습니다.\n\n"
        "> **작업자 관점에서도 가능하다.** 순서대로 스캔하면 된다.\n"
    )
    assert [n for n, r, _ in rules(text) if r == "bold-verdict"] == [3, 5, 7]


def test_bold_label_bold_in_list_table_or_mid_sentence_ok():
    text = (
        "# 제목\n\n**주의**: 운영 DB 에는 실행하지 않는다.\n\n"
        "이 값은 **반드시** 바꾼다.\n\n"
        "- **체인을 둔다.** 헤더가 붙는다\n\n"
        "| 결정 | 이유 |\n|---|---|\n| **등록순을 씁니다.** | 자리가 고정된다 |\n"
    )
    assert all(r != "bold-verdict" for _, r, _ in rules(text))


def test_symbol_operators_joining_words_in_prose_warn():
    text = (
        "# 제목\n\n정산 확정 ≠ 송금 완료다.\n\n"
        "- 회차 배치 vs 스트리밍 집계를 고른다\n\n"
        "회차 = 송금 묶음으로 둔다.\n\n"
        "수신자는 가맹점 + 정산 담당자다.\n"
    )
    assert [n for n, r, _ in rules(text) if r == "symbol-operator"] == [3, 5, 7, 9]


def test_symbols_in_code_tables_and_math_ok():
    text = (
        "# 제목\n\n`a = b + c` 로 계산한다.\n\n"
        "```text\n확정 ≠ 송금\n```\n\n"
        "| 기준 | A + B |\n|---|---|\n| 비용 | 낮음 vs 높음 |\n\n"
        "만료는 발급 시각 + 1시간이 아니라 30 + 30분이다.\n"
    )
    assert all(r != "symbol-operator" for _, r, _ in rules(text))


def test_sentence_switching_scripts_ten_times_warns():
    text = (
        "# 제목\n\n"
        "settlement job은 Kafka topic에서 order event를 읽어 DB에 upsert하고 retry는 DLT로 보내며 alert는 Slack으로 보낸다.\n"
    )
    assert [n for n, r, _ in rules(text) if r == "mixed-script"] == [3]


def test_backticked_identifiers_and_few_switches_ok():
    text = (
        "# 제목\n\n"
        "`settlement` 작업은 `Kafka` 토픽에서 `order` 이벤트를 읽어 `DB` 에 `upsert` 하고 재시도는 `DLT` 로 보내며 알림은 `Slack` 으로 보낸다.\n\n"
        "Redis 어댑터는 유저당 키 하나에 세션 ID와 리프레시 토큰 ID를 JSON 문자열로 덮어쓴다.\n"
    )
    assert all(r != "mixed-script" for _, r, _ in rules(text))


def test_slop_rules_follow_registry_status_and_threshold(monkeypatch):
    text = "# 제목\n\n정산 확정 ≠ 송금 완료다.\n\n**정산은 하루 한 번 확정합니다.** 송금 시점을 재현해야 합니다.\n"
    monkeypatch.setattr(lint_doc, "SLOP_RULES", {"symbol-operator": {"status": "hold"}, "bold-verdict": {"status": "gate"}})
    found = {r for _, r, _ in rules(text)}
    assert "symbol-operator" not in found and "bold-verdict" in found
    switch = "# 제목\n\na한b한c한d한e한f한g한h한i한 문장이다.\n"
    monkeypatch.setattr(lint_doc, "SLOP_RULES", {"mixed-script": {"status": "gate", "threshold": 9}})
    assert "mixed-script" in {r for _, r, _ in rules(switch)}
    monkeypatch.setattr(lint_doc, "SLOP_RULES", {"mixed-script": {"status": "gate", "threshold": 11}})
    assert "mixed-script" not in {r for _, r, _ in rules(switch)}


def test_registry_missing_falls_back_to_defaults(tmp_path):
    assert lint_doc.load_slop_rules(tmp_path / "없음.json") == {}


LINKED = "# 조사\n\n- 결론\n\n[A.java#L3](https://git.example.com/pay/pay-api/blob/main/src/A.java#L3)\n"
BASELINE = "- 기준: [pay-api](https://git.example.com/pay/pay-api) `main` 브랜치\n"


def test_investigation_allows_file_links():
    text = LINKED.replace("- 결론\n", "- 결론\n" + BASELINE)
    found = [f.rule for f in lint_doc.lint(text, doc_type="investigation")]
    assert "file-link" not in found and "missing-baseline" not in found


def test_file_links_still_warn_without_type():
    assert "file-link" in [f.rule for f in lint_doc.lint(LINKED)]


def test_investigation_requires_baseline_line():
    found = [(f.rule, f.level) for f in lint_doc.lint(LINKED, doc_type="investigation")]
    assert ("missing-baseline", "warn") in found


def test_baseline_accepts_commit_hash():
    text = LINKED.replace("- 결론\n", "- 결론\n- 기준: pay-api 커밋 `7d1bc90`\n")
    assert "missing-baseline" not in [f.rule for f in lint_doc.lint(text, doc_type="investigation")]


def test_cli_type_option(tmp_path: Path):
    p = tmp_path / "a.md"
    p.write_text(LINKED.replace("- 결론\n", "- 결론\n" + BASELINE), encoding="utf-8")
    proc = subprocess.run(["python3", str(SCRIPTS / "lint_doc.py"), str(p), "--json", "--type", "investigation"],
                          capture_output=True, text=True)
    assert proc.returncode == 0
    assert "file-link" not in proc.stdout
