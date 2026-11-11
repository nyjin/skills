import os
from pathlib import Path

import pytest

from asrlib.transcript import (
    display_name, first_lines, load, output_paths, render_markdown, resolve, save,
)


def make_doc(segments, speakers=None, failed=None):
    return {
        "source": {"file": "a.m4a", "duration": 10.0},
        "enhanced": None,
        "chunks": [],
        "speakers": speakers or {
            "S1": {"name": None, "label_no": 1},
            "S2": {"name": None, "label_no": 2},
        },
        "links": [],
        "segments": segments,
        "failed_ranges": failed or [],
    }


def seg(start, end, speaker, text):
    return {"start": start, "end": end, "speaker": speaker, "text": text}


def test_render_basic():
    doc = make_doc([seg(1.2, 3, "S1", "안녕하세요"), seg(5, 6, "S2", "네")])
    assert render_markdown(doc) == "**[00:00:01] 화자 1:** 안녕하세요\n\n**[00:00:05] 화자 2:** 네\n"


def test_render_empty():
    assert render_markdown(make_doc([])) == ""


def test_render_uses_name():
    doc = make_doc([seg(0, 1, "S1", "hi")])
    doc["speakers"]["S1"]["name"] = "김철수"
    assert render_markdown(doc) == "**[00:00:00] 김철수:** hi\n"


def test_render_follows_merged_into():
    speakers = {
        "S1": {"name": None, "label_no": 1},
        "S2": {"name": None, "label_no": 2},
        "S3": {"merged_into": "S1", "first_seen": 9.0},
    }
    doc = make_doc([seg(0, 1, "S2", "a"), seg(2, 3, "S3", "b")], speakers)
    assert resolve(speakers, "S3") == "S1"
    assert display_name(speakers, "S3") == "화자 1"
    assert render_markdown(doc) == "**[00:00:00] 화자 2:** a\n\n**[00:00:02] 화자 1:** b\n"


def test_resolve_follows_chain():
    sp = {"S1": {"label_no": 1}, "S2": {"merged_into": "S1"}, "S3": {"merged_into": "S2"}}
    assert resolve(sp, "S3") == "S1"


def test_same_display_name_consecutive_merged():
    speakers = {
        "S1": {"name": "김철수", "label_no": 1},
        "S2": {"name": "김철수", "label_no": 2},
    }
    doc = make_doc([seg(3661, 3662, "S1", "가"), seg(3663, 3664, "S2", "나"), seg(3665, 3666, "S1", "다")], speakers)
    assert render_markdown(doc) == "**[01:01:01] 김철수:** 가 나 다\n"


def test_segments_sorted_by_time():
    doc = make_doc([seg(5, 6, "S2", "b"), seg(1, 2, "S1", "a")])
    assert render_markdown(doc) == "**[00:00:01] 화자 1:** a\n\n**[00:00:05] 화자 2:** b\n"


def test_failed_range_marker():
    doc = make_doc(
        [seg(1, 2, "S1", "a"), seg(70, 71, "S1", "b")],
        failed=[{"start": 10.5, "end": 65.9, "reason": "x"}],
    )
    assert render_markdown(doc) == (
        "**[00:00:01] 화자 1:** a\n\n"
        "> ⚠ [00:00:10–00:01:05] 이 구간은 인식에 실패했습니다.\n\n"
        "**[00:01:10] 화자 1:** b\n"
    )


def test_failed_range_only():
    doc = make_doc([], failed=[{"start": 0, "end": 5, "reason": "x"}])
    assert render_markdown(doc) == "> ⚠ [00:00:00–00:00:05] 이 구간은 인식에 실패했습니다.\n"


def test_first_lines():
    doc = make_doc([
        seg(0, 1, "S1", "안녕하세요. 반갑습니다. 세 번째 문장."),
        seg(2, 3, "S2", "네"),
        seg(4, 5, "S2", "그렇군요? 좋습니다!"),
        seg(6, 7, "S1", "뒤의 발화."),
    ])
    out = first_lines(doc)
    assert out == {"화자 1": "안녕하세요. 반갑습니다.", "화자 2": "네 그렇군요?"}
    assert first_lines(doc, n=1)["화자 1"] == "안녕하세요."
    long = make_doc([seg(0, 1, "S1", "가" * 300)])
    assert len(first_lines(long)["화자 1"]) == 120


def test_output_paths_same_dir(tmp_path):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    src = tmp_path / "a.m4a"
    src.touch()
    assert output_paths(str(src), str(cwd)) == (tmp_path / "a.json", tmp_path / "a.md")


def test_output_paths_library_goes_to_cwd(tmp_path):
    src = Path.home() / "Library/Group Containers/x/a.m4a"
    assert output_paths(str(src), str(tmp_path)) == (tmp_path / "a.json", tmp_path / "a.md")


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
def test_output_paths_readonly_dir_goes_to_cwd(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    src = ro / "a.m4a"
    src.touch()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    ro.chmod(0o555)
    try:
        assert output_paths(str(src), str(cwd)) == (cwd / "a.json", cwd / "a.md")
    finally:
        ro.chmod(0o755)


def test_output_paths_collision(tmp_path):
    src = tmp_path / "a.m4a"
    src.touch()
    (tmp_path / "a.md").write_text("x")
    assert output_paths(str(src), str(tmp_path)) == (tmp_path / "a (2).json", tmp_path / "a (2).md")
    (tmp_path / "a (2).json").write_text("x")
    assert output_paths(str(src), str(tmp_path)) == (tmp_path / "a (3).json", tmp_path / "a (3).md")


def test_save_load_roundtrip_korean(tmp_path):
    doc = make_doc([seg(1.2, 3, "S1", "안녕하세요")])
    jp, mp = tmp_path / "a.json", tmp_path / "a.md"
    save(doc, jp, mp)
    assert "안녕하세요" in jp.read_text(encoding="utf-8")
    assert mp.read_text(encoding="utf-8") == render_markdown(doc)
    assert load(jp) == doc
