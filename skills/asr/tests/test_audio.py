import hashlib
import shutil
import subprocess
import tempfile

import numpy as np
import pytest
from conftest import FIXTURE

from asrlib.audio import (
    ENHANCE_FILTERS,
    SR,
    AudioError,
    decode,
    decode_mapped,
    integrated_lufs,
    needs_enhancement,
)


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rms(x):
    return float(np.sqrt(np.mean(np.square(x))))


@pytest.fixture
def quiet(tmp_path):
    out = tmp_path / "quiet.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(FIXTURE), "-af", "volume=-30dB", str(out)],
        check=True,
    )
    return out


def test_decode_fixture():
    x = decode(str(FIXTURE))
    assert x.dtype == np.float32
    assert abs(len(x) / SR - 22.2) <= 0.1


def test_decode_mapped_matches_decode_and_leaves_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    x = decode_mapped(str(FIXTURE), ENHANCE_FILTERS)
    assert isinstance(x, np.memmap)
    assert np.array_equal(x, decode(str(FIXTURE), ENHANCE_FILTERS))
    assert list(tmp_path.iterdir()) == []  # 매핑한 뒤 임시 파일은 바로 지운다


def test_decode_mapped_bad_file_leaves_no_file(tmp_path, monkeypatch):
    p = tmp_path / "not_audio.txt"
    p.write_text("audio 아님")
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    with pytest.raises(AudioError):
        decode_mapped(str(p))
    assert list(tmp.iterdir()) == []


def test_decode_path_with_space(tmp_path):
    p = tmp_path / "녹음 파일 1.wav"
    shutil.copy(FIXTURE, p)
    assert len(decode(str(p))) > 0


def test_decode_bad_file_raises(tmp_path):
    p = tmp_path / "not_audio.txt"
    p.write_text("audio 아님")
    with pytest.raises(AudioError):
        decode(str(p))


def test_lufs_fixture():
    assert integrated_lufs(str(FIXTURE)) == pytest.approx(-17.6, abs=1.0)


def test_quiet_needs_enhancement(quiet):
    lufs = integrated_lufs(str(quiet))
    assert lufs < -40.0
    assert needs_enhancement(lufs)


def test_enhance_raises_level(quiet):
    assert _rms(decode(str(quiet), ENHANCE_FILTERS)) >= 5 * _rms(decode(str(quiet)))


def test_original_untouched(quiet):
    before = _sha256(FIXTURE)
    decode(str(FIXTURE))
    integrated_lufs(str(FIXTURE))
    decode(str(FIXTURE), ENHANCE_FILTERS)
    assert _sha256(FIXTURE) == before
