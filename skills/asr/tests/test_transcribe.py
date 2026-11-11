import hashlib
import json
import shutil
import subprocess

import numpy as np
import pytest
from conftest import FIXTURE

from asrlib.engine import TranscriptionFailed
from asrlib.transcript import display_name, load
from transcribe import main, run

DIM = 192


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _print(person: int) -> np.ndarray:
    v = np.zeros(DIM, dtype=np.float32)
    v[person] = 1.0
    return v


class FakeEngine:
    """조각 순서(호출 횟수)대로 미리 정한 segments 를 돌려준다.

    script[i] 는 i 번째 조각의 [(start, end, local_id, person, text)] (조각 기준 초)
    이거나, 예외 인스턴스(그 조각에서 raise)다. embed 는 넘겨받은 배열이 조각 배열의
    어느 위치인지(같은 버퍼의 view) 계산해, 그 시각을 덮는 구간의 person 지문을 돌려준다.
    """

    def __init__(self, script):
        self.script = script
        self.calls = 0
        self._chunk_audio = None
        self._chunk_items = []

    def speech_intervals(self, audio):
        return []

    def transcribe(self, audio):
        item = self.script[self.calls]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        self._chunk_audio = audio
        self._chunk_items = item
        return [{"start": float(s), "end": float(e), "speaker_id": local, "text": text}
                for s, e, local, _, text in item]

    def embed(self, audio):
        offset = (audio.__array_interface__["data"][0]
                  - self._chunk_audio.__array_interface__["data"][0])
        t = offset / self._chunk_audio.itemsize / 16000
        for s, e, _, person, _ in self._chunk_items:
            if s <= t < e:
                return _print(person)
        raise AssertionError(f"embed 위치 {t:.2f}s 에 해당하는 구간이 없다")


@pytest.fixture(scope="module")
def long_zeros(tmp_path_factory):
    """1500초 디지털 무음 wav. 조각 3개 [0,600] [555,1155] [1110,1500] 으로 나뉜다."""
    path = tmp_path_factory.mktemp("long") / "long.wav"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
         "anullsrc=r=8000:cl=mono", "-t", "1500", str(path)],
        check=True,
    )
    return path


@pytest.fixture
def long_copy(long_zeros, tmp_path):
    dst = tmp_path / "long.wav"
    shutil.copy(long_zeros, dst)
    return dst


@pytest.fixture
def short_copy(tmp_path):
    dst = tmp_path / "test_ko.wav"
    shutil.copy(FIXTURE, dst)
    return dst


def _labels_by_text(doc):
    return {s["text"]: display_name(doc["speakers"], s["speaker"]) for s in doc["segments"]}


def test_short_file_single_chunk(short_copy, tmp_path):
    before = _sha256(short_copy)
    engine = FakeEngine([[
        (0.5, 4.0, 0, 0, "안녕하세요. 첫 번째 화자입니다."),
        (4.5, 9.0, 1, 1, "네 반갑습니다."),
        (9.5, 13.0, 0, 0, "다시 첫 번째입니다."),
    ]])
    summary = run(str(short_copy), str(tmp_path), engine)

    assert _sha256(short_copy) == before
    assert _sha256(FIXTURE) == _sha256(short_copy)
    assert summary["md"].endswith(".md") and (tmp_path / "test_ko.md").exists()
    assert summary["json"].endswith(".json")
    assert summary["chunks"] == 1
    assert summary["no_speech"] is False
    assert summary["enhanced"] is None
    assert summary["merged"] == []
    assert summary["failed_ranges"] == []
    assert summary["speakers"] == [
        {"label": "화자 1", "first_lines": "안녕하세요. 첫 번째 화자입니다."},
        {"label": "화자 2", "first_lines": "네 반갑습니다."},
    ]
    md = (tmp_path / "test_ko.md").read_text(encoding="utf-8")
    assert "**[00:00:00] 화자 1:** 안녕하세요. 첫 번째 화자입니다." in md
    doc = load(summary["json"])
    assert doc["source"] == {"file": "test_ko.wav", "duration": doc["source"]["duration"]}
    assert 20 < doc["source"]["duration"] < 25
    assert doc["chunks"] == [{"index": 0, "start": 0.0, "end": doc["source"]["duration"]}]
    assert [s["start"] for s in doc["segments"]] == [0.5, 4.5, 9.5]
    assert doc["speakers"]["S1"]["speech_sec"] == pytest.approx(7.0)
    assert len(doc["speakers"]["S1"]["voiceprint"]) == DIM


def test_long_file_links_across_chunks(long_copy, tmp_path, capsys):
    before = _sha256(long_copy)
    # person 0 = A, 1 = B (내내 등장), 2 = C (조각 index 1 에서 처음 등장)
    engine = FakeEngine([
        [(10, 20, 0, 0, "A0"), (30, 40, 1, 1, "B0")],
        [(100, 110, 0, 1, "B1"), (120, 130, 1, 0, "A1"), (200, 210, 2, 2, "C1")],
        [(50, 60, 2, 0, "A2"), (70, 80, 0, 1, "B2"), (90, 100, 1, 2, "C2")],
    ])
    summary = run(str(long_copy), str(tmp_path), engine)

    assert _sha256(long_copy) == before
    assert summary["chunks"] == 3
    doc = load(summary["json"])
    alive = [sid for sid, e in doc["speakers"].items() if "merged_into" not in e]
    assert len(alive) == 3
    labels = _labels_by_text(doc)
    assert labels["A0"] == labels["A1"] == labels["A2"] == "화자 1"
    assert labels["B0"] == labels["B1"] == labels["B2"] == "화자 2"
    assert labels["C1"] == labels["C2"] == "화자 3"
    assert [s["label"] for s in summary["speakers"]] == ["화자 1", "화자 2", "화자 3"]
    assert [(c["start"], c["end"]) for c in doc["chunks"]] == [
        (0.0, 600.0), (555.0, 1155.0), (1110.0, 1500.0)]
    # 원본 기준 시각
    assert {s["text"]: s["start"] for s in doc["segments"]}["C1"] == pytest.approx(755.0)
    err = capsys.readouterr().err
    assert "조각 1/3 처리 중 (00:00:00–00:10:00)" in err
    assert "조각 2/3 처리 중 (00:09:15–00:19:15)" in err
    assert "조각 3/3 처리 중 (00:18:30–00:25:00)" in err


def test_overlap_not_duplicated(long_copy, tmp_path):
    before = _sha256(long_copy)
    # 겹친 구간 [555, 600] 의 같은 발화가 조각 0 (570~580) 과 조각 1 (15~25) 에 모두 나온다.
    engine = FakeEngine([
        [(10, 20, 0, 0, "A 시작"), (570, 580, 0, 0, "겹친 발화")],
        [(15, 25, 3, 0, "겹친 발화"), (100, 110, 1, 1, "B 등장")],
        [(50, 60, 0, 0, "A 끝")],
    ])
    summary = run(str(long_copy), str(tmp_path), engine)

    assert _sha256(long_copy) == before
    md = (tmp_path / "long.md").read_text(encoding="utf-8")
    assert md.count("겹친 발화") == 1
    doc = load(summary["json"])
    assert [s["text"] for s in doc["segments"]].count("겹친 발화") == 1
    overlap_link = [ln for ln in doc["links"] if ln["chunk"] == 1 and ln["local"] == 3]
    assert overlap_link[0]["how"] == "overlap"
    assert overlap_link[0]["speaker"] == "S1"
    # speech_sec 는 중복 제거 후 남은 구간으로 다시 센다: 10 + 10 + 10
    assert doc["speakers"]["S1"]["speech_sec"] == pytest.approx(30.0)


def test_failed_chunk_marked(long_copy, tmp_path):
    before = _sha256(long_copy)
    engine = FakeEngine([
        [(10, 20, 0, 0, "앞 조각")],
        TranscriptionFailed("전사가 끊겼습니다"),
        [(300, 310, 0, 0, "뒤 조각")],
    ])
    summary = run(str(long_copy), str(tmp_path), engine)

    assert _sha256(long_copy) == before
    assert engine.calls == 3
    assert summary["failed_ranges"] == [
        {"start": 555.0, "end": 1155.0, "reason": "전사가 끊겼습니다"}]
    md = (tmp_path / "long.md").read_text(encoding="utf-8")
    assert "> ⚠ [00:09:15–00:19:15]" in md
    assert "앞 조각" in md and "뒤 조각" in md
    doc = load(summary["json"])
    assert doc["failed_ranges"] == summary["failed_ranges"]
    assert len(summary["speakers"]) == 1


def test_all_silence(long_copy, tmp_path):
    before = _sha256(long_copy)
    engine = FakeEngine([[], [], []])
    summary = run(str(long_copy), str(tmp_path), engine)

    assert _sha256(long_copy) == before
    assert summary["no_speech"] is True
    assert summary["speakers"] == []
    assert summary["enhanced"] is None  # 무음(-70 LUFS 게이트 바닥)은 보정하지 않는다
    assert (tmp_path / "long.md").read_text(encoding="utf-8") == ""
    text = (tmp_path / "long.json").read_text(encoding="utf-8")
    assert "Infinity" not in text and "NaN" not in text
    assert json.loads(text)["segments"] == []


def test_quiet_file_enhanced(tmp_path):
    quiet = tmp_path / "quiet.wav"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(FIXTURE),
         "-af", "volume=-30dB", str(quiet)],
        check=True,
    )
    before = _sha256(quiet)
    engine = FakeEngine([[(0.5, 4.0, 0, 0, "작은 목소리")]])
    summary = run(str(quiet), str(tmp_path), engine)

    assert _sha256(quiet) == before
    assert summary["enhanced"]["lufs_before"] < -40
    assert summary["enhanced"]["lufs_before"] == round(summary["enhanced"]["lufs_before"], 1)
    doc = load(summary["json"])
    assert doc["enhanced"]["lufs_before"] == summary["enhanced"]["lufs_before"]
    assert doc["enhanced"]["filters"]


def test_cli_missing_input_exits_2(tmp_path, capsys):
    assert main([str(tmp_path / "없는 파일.m4a"), "--cwd", str(tmp_path)]) == 2
    assert "파일을 찾을 수 없습니다" in capsys.readouterr().err
