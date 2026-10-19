import re
from pathlib import Path

import yaml

PLUGIN = Path(__file__).resolve().parents[1]
SKILL = PLUGIN / "skills/retro/SKILL.md"
TRIGGERS = ("회고", "action item", "이 세션 돌아보기", "개선점 정리", "retro", "검토 대기")
CONFIG_KEYS = ("weights", "propose", "watch", "global_bump", "queue_limit", "max_background_judges_per_day", "eval_markers")


def _parts():
    text = SKILL.read_text(encoding="utf-8")
    _, front, body = text.split("---\n", 2)
    return text, yaml.safe_load(front), body


def test_frontmatter_name_and_triggers():
    _, front, _ = _parts()
    assert front["name"] == "retro"
    for phrase in TRIGGERS:
        assert phrase in front["description"], phrase


def test_at_most_500_lines():
    assert len(SKILL.read_text(encoding="utf-8").splitlines()) <= 500


def test_backticked_plugin_paths_exist():
    _, _, body = _parts()
    paths = [p for span in re.findall(r"`([^`]+)`", body) for p in re.findall(r"(?:scripts|defaults)/[\w.-]+", span)]
    assert paths
    for p in paths:
        assert (PLUGIN / p).exists(), p


def test_script_paths_use_plugin_root():
    _, _, body = _parts()
    for m in re.finditer(r"(?:scripts|defaults)/", body):
        assert body[: m.start()].endswith("${CLAUDE_PLUGIN_ROOT}/"), body[max(0, m.start() - 40): m.end() + 20]


def test_no_choice_question_tool():
    text, _, _ = _parts()
    assert "AskUserQuestion" not in text


def test_config_values_referenced_by_name_only():
    _, _, body = _parts()
    assert "queue_limit" in body
    assert "max_background_judges_per_day" in body
    for key in CONFIG_KEYS:
        assert not re.search(rf"{key}`?\s*[:=(]?\s*\d", body), key
    for value in ("2.2", "1.6", "0.3", "0.4", "0.2"):
        assert value not in body, value


def test_merge_reads_judge_json_from_heredoc():
    _, _, body = _parts()
    assert "harness-judge.json" not in body
    assert re.search(r'judge\.py" merge "<transcript>" --mode explicit <<\'JSON\'\n.*\nJSON\n', body, re.S)


def test_lock_exit_code_rule_for_collect_and_merge():
    _, _, body = _parts()
    line = next(l for l in body.splitlines() if "종료 코드 3" in l and "collect.py" in l)
    assert "judge.py merge" in line and "백그라운드" in line and "다시" in line


def test_korean_output_rule():
    _, _, body = _parts()
    assert any("한국어" in l and "리포트" in l for l in body.splitlines() if l.startswith("- "))


def test_korean_rule_leads_procedure_and_report_format():
    _, _, body = _parts()
    common = body.split("## 공통 규칙", 1)[1].split("\n## ", 1)[0]
    first = next(l for l in common.splitlines() if l.startswith("- "))
    assert "한국어" in first and "영어" in first
    report_format = body.split("## 리포트 형식", 1)[1]
    assert any("한국어" in l for l in report_format.splitlines() if l.startswith("- "))
    rules = body.split("### 판정 JSON 작성 규칙", 1)[1].split("\n## ", 1)[0]
    assert any("proposal" in l and "한국어" in l for l in rules.splitlines())
