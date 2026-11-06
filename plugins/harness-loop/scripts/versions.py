#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""스킬 파일 버전: git blob 해시, 커밋되지 않은 내용의 사본, git 이력 조회."""
import hashlib
import json
import os
import subprocess
from pathlib import Path

import apply_target
import paths


HOOK_TIMEOUT = 1  # 훅에서 부르는 git 의 제한 시간(초). retro 시점 호출은 기본 5초


def _git(cwd, *args, timeout: float = 5) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def blob_hash(path) -> str | None:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def git_root(path, timeout: float = 5) -> str | None:
    p = Path(path)
    d = p if p.is_dir() else p.parent
    out = _git(d, "rev-parse", "--show-toplevel", timeout=timeout)
    return out.strip() or None if out is not None else None


def repo_roots(cwd, timeout: float = 5) -> tuple[str | None, str]:
    """(작업 트리 루트, 프로젝트 루트). 프로젝트 루트는 worktree 들이 함께 쓰는 .git 의 부모이고,
    공용 디렉터리가 .git 이 아니면(서브모듈 등) 작업 트리 루트, git 밖이면 cwd 다."""
    out = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir", "--show-toplevel", timeout=timeout)
    lines = (out or "").splitlines()
    if len(lines) < 2:
        return None, str(cwd)
    common, top = Path(lines[0].strip()), lines[1].strip() or None
    return top, str(common.parent) if common.name == ".git" else (top or str(cwd))


def in_plugin_cache(path) -> bool:
    return apply_target._in_plugin_cache(os.path.realpath(os.path.expanduser(str(path))))


def _rel(path, root) -> str:
    return os.path.relpath(os.path.realpath(path), os.path.realpath(root))


def _index_pairs(snaps) -> list[dict]:
    rows = []
    try:
        for ln in (snaps / "index.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(ln))
            except ValueError:
                continue
    except OSError:
        pass
    return rows


def remember(path) -> str | None:
    """커밋에 없는 내용은 skill-snapshots/<해시> 에 보관하고 index.jsonl 에 (해시, 경로)를 한 번 남긴 뒤 해시를 돌려준다. 예외를 내지 않는다."""
    try:
        try:
            data = Path(path).read_bytes()
        except OSError:
            return None
        h = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        root = git_root(path, HOOK_TIMEOUT)
        if root and _git(root, "cat-file", "-e", h, timeout=HOOK_TIMEOUT) is not None:
            return h
        snaps = paths.data_dir() / "skill-snapshots"
        snaps.mkdir(parents=True, exist_ok=True)
        snap = snaps / h
        if not snap.exists():
            tmp = snap.with_name(snap.name + f".tmp{os.getpid()}")
            tmp.write_bytes(data)
            os.replace(tmp, snap)
        real = os.path.realpath(path)
        if not any(r.get("hash") == h and r.get("path") == real for r in _index_pairs(snaps)):
            paths.append_line(snaps / "index.jsonl", {"hash": h, "path": real, "at": paths.now()})
        return h
    except Exception as e:
        paths.log_error("versions.remember", e)
        return None


def commit_of(path, h) -> dict | None:
    root = git_root(path)
    if not root or not h:
        return None
    out = _git(root, "log", "--all", "--format=%H%x09%cI%x09%s", f"--find-object={h}", "--", _rel(path, root))
    lines = [ln for ln in (out or "").splitlines() if ln.strip()]
    if not lines:
        return None
    commit, at, subject = (lines[-1].split("\t", 2) + ["", ""])[:3]
    return {"commit": commit, "at": at, "subject": subject}


def history(path, since: str) -> list[dict]:
    try:
        since_dt = paths.aware(since)
    except ValueError as e:
        paths.log_error("versions.history", e)
        return []
    entries = []
    root = git_root(path)
    if root:
        rel = _rel(path, root)
        out = _git(root, "log", "--format=%H%x09%cI%x09%s", f"--since={since}", "--", rel) or ""
        for ln in out.splitlines():
            commit, at, subject = (ln.split("\t", 2) + ["", ""])[:3]
            h = (_git(root, "rev-parse", f"{commit}:{rel}") or "").strip()
            if commit and h:
                entries.append({"hash": h, "at": at, "source": "commit", "commit": commit, "subject": subject})
    real = os.path.realpath(path)
    for r in _index_pairs(paths.data_dir() / "skill-snapshots"):
        try:
            if r.get("path") == real and paths.aware(r["at"]) >= since_dt:
                entries.append({"hash": r["hash"], "at": r["at"], "source": "snapshot"})
        except (KeyError, ValueError, TypeError):
            continue
    entries.sort(key=lambda e: paths.aware(e["at"]))
    seen, result = set(), []
    for e in entries:
        if e["hash"] not in seen:
            seen.add(e["hash"])
            result.append(e)
    return result


def hash_at(path, at: str) -> str | None:
    root = git_root(path)
    if not root:
        return None
    rel = _rel(path, root)
    commit = (_git(root, "log", "-1", f"--before={at}", "--format=%H", "--", rel) or "").strip()
    if not commit:
        return None
    return (_git(root, "rev-parse", f"{commit}:{rel}") or "").strip() or None


def untracked_notice(paths_by_root: dict[str, list[str]]) -> list[str]:
    lines = []
    for root, names in paths_by_root.items():
        if in_plugin_cache(root):
            lines.append(f"원본 저장소에 티켓을 내세요: {root}")
        else:
            lines.append(
                f"버전 추적 불가: {root} 는 git 저장소가 아니라 수정 전후 비교와 재발 판정을 할 수 없습니다. "
                f"이 디렉터리를 git 으로 관리하면(git init 후 커밋) 다음 retro 부터 추적합니다. "
                f"해당 스킬: {', '.join(names)}")
    return lines
