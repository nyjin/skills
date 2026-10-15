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
