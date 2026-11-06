import argparse
import re
from pathlib import Path

import yaml

import retro

PLUGIN = Path(__file__).resolve().parents[1]
SKILL = PLUGIN / "skills/retro/SKILL.md"
TRIGGERS = ("회고", "retro", "개선점 정리", "이 세션 돌아보기", "판정하지 않은 근거")
PREFIX = 'CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/retro.py"'
SUBCOMMANDS = set(next(a for a in retro.parser()._actions if isinstance(a, argparse._SubParsersAction)).choices)
CONFIG_KEYS = ("eval_markers", "window_days", "script_min_count", "script_min_sessions", "correction_min_count",
               "guideline_min_sessions", "repeat_edit_min", "label_examples", "rubric_min_evidence",
               "replay_min_labels", "judge_batch")


def _parts():
    text = SKILL.read_text(encoding="utf-8")
    _, front, body = text.split("---\n", 2)
    return text, yaml.safe_load(front), body


def test_frontmatter_name_triggers_and_hint():
    _, front, _ = _parts()
    assert front["name"] == "retro"
    for phrase in TRIGGERS:
        assert phrase in front["description"], phrase
    assert front["argument-hint"] == "[backfill [--days N]]"


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


def test_commands_use_prefix_and_known_subcommands():
    _, _, body = _parts()
    found = set()
    for m in re.finditer(r"scripts/retro\.py", body):
        assert body[: m.end() + 1].endswith(PREFIX), body[max(0, m.start() - 80): m.end() + 20]
        sub = re.match(r'"\s+(<하위 명령>|[\w-]+)', body[m.end():])
        assert sub, body[m.start(): m.end() + 30]
        if sub.group(1) == "<하위 명령>":
            continue
        assert sub.group(1) in SUBCOMMANDS, sub.group(1)
        found.add(sub.group(1))
    assert found >= {"pending", "observe", "context", "groups", "gate", "decide", "ticket-file",
                     "rubric-candidates", "rubric-apply", "replay-gate", "replay-score", "replay-inputs", "backfill"}


def test_no_harness_retro_and_no_old_scripts():
    _, _, body = _parts()
    assert "HARNESS_RETRO" not in body
    for old in ("report.py", "judge.py", "collect.py"):
        assert old not in body, old


def test_judges_use_opus_and_rubric_apply_needs_confirmation():
    _, _, body = _parts()
    assert 'model: "opus"' in body
    assert "--confirmed" in body
    assert "--pending" in body
    assert "dropped_positives" in body


def test_no_choice_question_tool():
    text, _, _ = _parts()
    assert "AskUserQuestion" not in text


def test_config_values_referenced_by_name_only():
    _, _, body = _parts()
    for key in CONFIG_KEYS:
        assert not re.search(rf"{key}`?\s*[:=(]?\s*\d", body), key


def test_decision_table_covers_choices():
    _, _, body = _parts()
    for kind in ("not-defect", "trivial", "wont-fix"):
        assert f"--kind {kind}" in body or f"`{kind}`" in body, kind
    assert "ticket-creator" in body
    assert "--ticket" in body


def test_backfill_default_window_and_cli_fields_referenced():
    _, _, body = _parts()
    assert "backfill --days <N>" in body and "인자에 없으면 14" in body
    for field in ("repeat_stats", "recent_purposes", "`waiting`"):
        assert field in body, field


def test_report_shows_notices_verbatim():
    _, _, body = _parts()
    assert any("notices" in l and "그대로" in l for l in body.splitlines())
