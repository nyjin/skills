"""ffmpeg 디코딩·음량 측정·보정. 입력 파일은 읽기만 하고 보정은 출력 배열에만 한다."""
import os
import re
import shutil
import subprocess
import tempfile

import numpy as np

SR = 16000
ENHANCE_LUFS_THRESHOLD = -40.0
ENHANCE_FILTERS = "highpass=f=80,loudnorm=I=-23:LRA=11:TP=-2"

_SUMMARY_I = re.compile(r"I:\s+(-?\d+(?:\.\d+)?|-inf)\s+LUFS")


class AudioError(Exception):
    pass


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    if not shutil.which(exe):
        raise AudioError("ffmpeg 를 찾을 수 없습니다. `brew install ffmpeg` 로 설치하세요.")
    return exe


def _run(args: list[str], path: str) -> subprocess.CompletedProcess:
    # 인자 리스트로 호출한다. 경로의 공백·한글이 셸을 거치지 않는다.
    proc = subprocess.run([_ffmpeg(), "-nostdin", "-v", "info", *args], capture_output=True)
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:]
        raise AudioError(f"오디오를 읽을 수 없습니다: {path} ({' '.join(tail)})")
    return proc


def decode(path: str, filters: str | None = None) -> np.ndarray:
    """16kHz 모노 float32 로 디코딩한다. filters 는 ffmpeg -af 문자열이다."""
    args = ["-i", path]
    if filters:
        args += ["-af", filters]
    args += ["-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    proc = _run(args, path)
    return np.frombuffer(proc.stdout, dtype="<f4").astype(np.float32)


def decode_mapped(path: str, filters: str | None = None) -> np.ndarray:
    """decode 와 같은 값을 임시 파일에 쓰고 읽기 전용 memmap 으로 돌려준다.

    파일에 기댄 페이지는 프로세스 메모리(footprint)에 잡히지 않아, 녹음이 길어도 메모리가 늘지 않는다.
    임시 파일은 매핑한 뒤 바로 지운다. 매핑이 남아 있는 동안만 디스크를 쓴다.
    """
    fd, tmp = tempfile.mkstemp(prefix="asr-", suffix=".f32")
    os.close(fd)
    try:
        args = ["-y", "-i", path]
        if filters:
            args += ["-af", filters]
        args += ["-ac", "1", "-ar", str(SR), "-f", "f32le", tmp]
        _run(args, path)
        if os.path.getsize(tmp) == 0:
            return np.zeros(0, dtype=np.float32)
        return np.memmap(tmp, dtype="<f4", mode="r")
    finally:
        os.unlink(tmp)


def integrated_lufs(path: str) -> float:
    """ffmpeg ebur128 요약의 통합 음량(LUFS). 무음이면 -inf."""
    proc = _run(["-i", path, "-af", "ebur128", "-f", "null", "-"], path)
    found = _SUMMARY_I.findall(proc.stderr.decode("utf-8", "replace"))
    if not found:
        raise AudioError(f"음량을 측정할 수 없습니다: {path}")
    return float(found[-1])


def needs_enhancement(lufs: float) -> bool:
    return lufs < ENHANCE_LUFS_THRESHOLD
