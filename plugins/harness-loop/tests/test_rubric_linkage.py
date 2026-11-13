"""분류 기준(rubric.md)과 스키마·관문 코드가 어긋나지 않는지 본다."""
import json
import re
from pathlib import Path

import groups
import observe
import rubric

ROOT = Path(__file__).resolve().parents[1]
RUBRIC = (ROOT / "defaults" / "rubric.md").read_text(encoding="utf-8")
OBSERVE_SCHEMA = json.loads((ROOT / "defaults" / "observe-schema.json").read_text(encoding="utf-8"))
GROUPS_SCHEMA = json.loads((ROOT / "defaults" / "groups-schema.json").read_text(encoding="utf-8"))
GROUP_PROPS = GROUPS_SCHEMA["properties"]["groups"]["items"]["properties"]
TARGET_ENUM = GROUP_PROPS["target_type"]["enum"]
FINDING_ENUM = GROUP_PROPS["verified"]["properties"]["finding"]["enum"]
TYPE_ENUM = OBSERVE_SCHEMA["properties"]["observations"]["items"]["properties"]["type"]["enum"]


def _codes(enum):
    return [f"`{v}`" for v in enum]


def parse_finding_table(text: str) -> dict[str, set[str]]:
    """"## 파일 검증" 절 표의 행: 첫 칸 코드 -> 셋째 칸의 백틱 코드 집합."""
    section = text.split("\n## 파일 검증\n", 1)[1].split("\n## ", 1)[0].split("\n### ", 1)[0]
    out = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        m = re.fullmatch(r"`(\w+)`", cells[0]) if len(cells) == 3 else None
        if m:
            out[m.group(1)] = set(re.findall(r"`(\w+)`", re.sub(r"\(.*?\)", "", cells[2])))
    return out


def test_observation_types_and_evidence_kinds_in_rubric():
    assert all(c in RUBRIC for c in _codes(TYPE_ENUM) + _codes(observe.JUDGEABLE))


def test_targets_findings_drop_reasons_and_config_in_rubric():
    for enum in (TARGET_ENUM, FINDING_ENUM, groups.DROP_REASONS,
                 ("pattern_min_count", "pattern_min_sessions", "window_days")):
        assert all(c in RUBRIC for c in _codes(enum)), enum


def test_finding_table_matches_allowed():
    assert parse_finding_table(RUBRIC) == groups.ALLOWED
    assert set(FINDING_ENUM) == set(groups.ALLOWED)


def test_rule_sections_exist():
    assert all(h in RUBRIC.split("\n") for h in rubric.SECTIONS.values())


def test_observation_enum_and_schema_fields():
    assert TYPE_ENUM == ["correction", "repeat", "failure", "approval_question", "decision_pattern"]
    assert "rule" in OBSERVE_SCHEMA["properties"]["observations"]["items"]["properties"]
    assert GROUP_PROPS["conflicts"]["type"] == "array"
    assert "hash" not in GROUP_PROPS["verified"]["properties"]
    assert "hash" not in GROUP_PROPS["verified"]["required"]


def test_drop_reasons_value():
    assert groups.DROP_REASONS == ("unverified", "not_specific", "no_failure_ref", "decided", "environment",
                                   "inconsistent", "conflicting_choice", "scope")
