#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""harness-loop 경로·설정·락 기반: 공용 데이터 디렉터리, 세션별 harness 디렉터리, 원자적 쓰기, 락, 오류 로그."""
import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

import yaml

DEFAULTS = Path(__file__).resolve().parents[1] / "defaults"
LOCKED_EXIT = 3

DEFAULT_CONFIG = {
    "eval_markers": ["트리거 측정 중"],
    "window_days": 30,
    "script_min_count": 3,
    "script_min_sessions": 2,
    "correction_min_count": 2,
    "guideline_min_sessions": 2,
    "label_examples": 10,
    "rubric_min_evidence": 3,
    "replay_min_labels": 30,
    "judge_batch": 40,
}


def now() -> str:
    return datetime.now().astimezone().isoformat()


def repo_roots(cwd, timeout: float = 1) -> tuple[str | None, str]:
    """(작업 트리 루트, 프로젝트 루트). 프로젝트 루트는 worktree 들이 함께 쓰는 .git 의 부모이고,
    공용 디렉터리가 .git 이 아니면(서브모듈 등) 작업 트리 루트, git 밖이면 cwd 다. 훅에서 부르므로 제한 시간은 1초."""
    cmd = ["git", "-C", str(cwd), "rev-parse", "--path-format=absolute", "--git-common-dir", "--show-toplevel"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None, str(cwd)
    lines = (r.stdout if r.returncode == 0 else "").splitlines()
    if len(lines) < 2:
        return None, str(cwd)
    common, top = Path(lines[0].strip()), lines[1].strip() or None
    return top, str(common.parent) if common.name == ".git" else (top or str(cwd))


def aware(text: str) -> datetime:
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.astimezone()


def data_dir() -> Path:
    env = os.environ.get("CLAUDE_PLUGIN_DATA")
    d = Path(env) if env else Path.home() / ".claude/plugins/data/harness-loop-local"
    d.mkdir(parents=True, exist_ok=True)
    for name in ("config.yaml", "rubric.md"):
        if not (d / name).exists():
            shutil.copy(DEFAULTS / name, d / name)
    if not (d / "installed_at").exists():
        (d / "installed_at").write_text(now())
    return d


def log_error(where: str, error: BaseException) -> None:
    """hook-errors.log 에 한 줄 남긴다. 어떤 경우에도 예외를 내지 않는다."""
    try:
        line = f"{now()} {where} {type(error).__name__}: {' '.join(str(error).split())[:200]}\n"
        with open(data_dir() / "hook-errors.log", "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def load_config() -> dict:
    """config.yaml 을 기본값 위에 덮는다. 파싱할 수 없거나 매핑이 아니면 기본값을 쓰고 hook-errors.log 에 한 줄 남긴다."""
    d = data_dir()
    loaded = {}
    try:
        loaded = yaml.safe_load((d / "config.yaml").read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"매핑이 아니다: {type(loaded).__name__}")
    except (OSError, yaml.YAMLError, ValueError) as e:
        log_error("config.yaml 기본값 사용", e)
        loaded = {}
    return {**DEFAULT_CONFIG, **loaded}


def projects_root() -> Path:
    env = os.environ.get("HARNESS_PROJECTS_ROOT")
    return Path(env) if env else Path.home() / ".claude/projects"


def harness_path(transcript) -> Path:
    """세션 기록의 harness 디렉터리 경로. 만들지 않는다."""
    transcript = Path(transcript)
    return projects_root() / transcript.parent.name / transcript.stem / "harness"


def harness_dir(transcript) -> Path:
    d = harness_path(transcript)
    d.mkdir(parents=True, exist_ok=True)
    return d


def all_harness_dirs() -> list[Path]:
    return sorted(p for p in projects_root().glob("*/*/harness") if p.is_dir())


def atomic_write(path, text: str) -> None:
    path = Path(path)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def append_line(path, obj: dict) -> None:
    """JSON 한 줄을 한 번의 os.write 로 O_APPEND 추가한다(동시 기록이 섞이지 않게)."""
    data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def acquire_lock(dir, stale_sec=600) -> bool:
    lock = Path(dir) / "lock"
    for _ in range(2):
        try:
            os.mkdir(lock)
            return True
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime <= stale_sec:
                    return False
                os.rmdir(lock)
            except FileNotFoundError:
                continue
            except OSError:
                return False
    return False


def release_lock(dir) -> None:
    try:
        os.rmdir(Path(dir) / "lock")
    except OSError:
        pass
