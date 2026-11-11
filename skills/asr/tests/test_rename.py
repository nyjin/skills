import json

import pytest

from asrlib import transcript
from rename import main, rename


def make_json(tmp_path, name="a.json"):
    doc = {
        "source": {"file": "a.m4a", "duration": 10.0},
        "enhanced": None,
        "chunks": [],
        "speakers": {
            "S1": {"name": None, "label_no": 1},
            "S2": {"name": None, "label_no": 2},
            "S3": {"name": None, "label_no": 3},
            "S4": {"merged_into": "S1"},
        },
        "links": [],
        "segments": [
            {"start": 1.0, "end": 2.0, "speaker": "S1", "text": "안녕하세요."},
            {"start": 3.0, "end": 4.0, "speaker": "S2", "text": "네."},
            {"start": 5.0, "end": 6.0, "speaker": "S4", "text": "반갑습니다."},
        ],
        "failed_ranges": [],
    }
    json_path = tmp_path / name
    transcript.save(doc, json_path, json_path.with_suffix(".md"))
    return json_path


def test_rename_updates_md(tmp_path):
    json_path = make_json(tmp_path)
    result = rename(json_path, {1: "김철수"})
    md = json_path.with_suffix(".md").read_text(encoding="utf-8")
    assert "**[00:00:01] 김철수:** 안녕하세요." in md
    assert "**[00:00:05] 김철수:** 반갑습니다." in md  # 통합된 화자도 따라간다
    assert transcript.load(json_path)["speakers"]["S1"]["name"] == "김철수"
    assert result["md"] == str(json_path.with_suffix(".md"))


def test_partial_rename_keeps_default(tmp_path):
    json_path = make_json(tmp_path)
    result = rename(json_path, {1: "김철수"})
    md = json_path.with_suffix(".md").read_text(encoding="utf-8")
    assert "**[00:00:03] 화자 2:** 네." in md
    assert result["speakers"] == [
        {"label_no": 1, "name": "김철수"},
        {"label_no": 2, "name": None},
        {"label_no": 3, "name": None},
    ]


def test_same_name_merges_paragraphs(tmp_path):
    json_path = make_json(tmp_path)
    rename(json_path, {1: "김철수", 2: "김철수"})
    md = json_path.with_suffix(".md").read_text(encoding="utf-8")
    assert md.count("김철수:**") == 1
    assert "안녕하세요. 네. 반갑습니다." in md


def test_unknown_label_errors(tmp_path):
    json_path = make_json(tmp_path)
    before = json_path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="화자 9 이 없습니다"):
        rename(json_path, {1: "김철수", 9: "누구"})
    assert json_path.read_text(encoding="utf-8") == before


def test_rename_does_not_touch_segments(tmp_path):
    json_path = make_json(tmp_path)
    before = transcript.load(json_path)
    rename(json_path, {2: "박지민"})
    after = transcript.load(json_path)
    assert after["segments"] == before["segments"]
    assert after["links"] == before["links"]


def test_suffix_with_space_paren(tmp_path):
    json_path = make_json(tmp_path, "a (2).json")
    rename(json_path, {1: "김철수"})
    assert "김철수" in (tmp_path / "a (2).md").read_text(encoding="utf-8")


def test_cli_success_and_errors(tmp_path, capsys):
    json_path = make_json(tmp_path)
    assert main([str(json_path), "--set", "1=김철수", "--set", "3=박지민"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [s["name"] for s in out["speakers"]] == ["김철수", None, "박지민"]
    assert main([str(json_path), "--set", "9=누구"]) == 2
    assert "화자 9 이 없습니다" in capsys.readouterr().err
    assert main([str(json_path), "--set", "abc"]) == 2
    assert main([str(json_path), "--set", "1="]) == 2
    assert main([str(json_path), "--set", "x=이름"]) == 2
