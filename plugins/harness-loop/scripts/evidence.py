#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""훅 입력을 근거 레코드로 바꿔 세션 harness/evidence.jsonl 에 덧붙인다(프롬프트·스킬 상태·가드)."""
import json
import os
import re
import time
from pathlib import Path

import paths
import transcript as tr
import versions

TEXT_MAX = 2000
TAIL = 64 * 1024
BASE_SCAN = 256 * 1024
SLASH_SKILL = re.compile(r"^/([\w.-]+:[\w.-]+)")
SLASH_ANY = re.compile(r"^/([^\s/]+)")
BASE_DIR = re.compile(r"Base directory for this skill: ([^\s\\\"]+)")
GUIDELINE_NAMES = ("CLAUDE.md", "AGENTS.md", ".claude/CLAUDE.md")


def skill_state(hdir) -> dict | None:
    try:
        state = json.loads((Path(hdir) / "skill-state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def _save_state(hdir, state: dict) -> None:
    paths.atomic_write(Path(hdir) / "skill-state.json", json.dumps(state, ensure_ascii=False))


def set_skill(hdir, name: str, path: str | None, pos: int) -> None:
    _save_state(hdir, {"name": name, "path": path or "", "pos": pos, "prompts_since": 0})


def skill_base_dir(transcript, start_pos: int) -> str | None:
    try:
        with open(transcript, "rb") as f:
            f.seek(start_pos)
            data = f.read(BASE_SCAN).decode("utf-8", "ignore")
    except OSError:
        return None
    m = BASE_DIR.search(data)
    return m.group(1) if m else None


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def after_rejection(transcript, pos: int) -> bool:
    try:
        start = max(0, pos - TAIL)
        with open(transcript, "rb") as f:
            f.seek(start)
            data = f.read(pos - start)
    except OSError:
        return False
    lines = data.decode("utf-8", "ignore").splitlines()
    if start > 0:
        lines = lines[1:]
    last = None
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        content = (row.get("message") or {}).get("content") if isinstance(row, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                last = block
    return bool(last and last.get("is_error") and _result_text(last.get("content")).startswith(tr.REJECTED))


def _append(hdir, session: str, rec: dict) -> dict:
    """락 안에서 줄 수 + 1 로 id 를 정해 덧붙인다. 락을 못 잡으면 최대 0.5초 재시도 후 시각 기반 id 를 쓴다."""
    prefix = session[:8]
    ev = Path(hdir) / "evidence.jsonl"
    deadline = time.monotonic() + 0.5
    while True:
        if paths.acquire_lock(hdir):
            try:
                try:
                    n = ev.read_bytes().count(b"\n")
                except OSError:
                    n = 0
                rec["id"] = f"{prefix}-{n + 1}"
                paths.append_line(ev, rec)
            finally:
                paths.release_lock(hdir)
            return rec
        if time.monotonic() >= deadline:
            rec["id"] = f"{prefix}-t{time.time_ns()}"
            paths.append_line(ev, rec)
            return rec
        time.sleep(0.002)


def _skill_field(hdir, state: dict | None, transcript_path: str, hash_of) -> dict | None:
    if not state:
        return None
    if not state.get("path") and not state.get("base_dir_checked"):
        start = state.get("pos", 0)
        base = skill_base_dir(transcript_path, start)
        if base:
            state["path"] = base
            _save_state(hdir, state)
        else:
            try:
                size = os.path.getsize(transcript_path)
            except OSError:
                size = 0
            if size > start + BASE_SCAN:
                state["base_dir_checked"] = True
                _save_state(hdir, state)
    path = state.get("path") or ""
    return {
        "name": state["name"],
        "path": path,
        "hash": hash_of(Path(path) / "SKILL.md") if path else None,
        "prompts_since": state.get("prompts_since", 0),
    }


def _guidelines(hdir, cwd: str, hash_of) -> list[dict]:
    """지침 파일 해시. 세션 harness/guideline-cache.json 에 (mtime, size)별 해시와 cwd 의 git 루트를 캐시한다."""
    cache_path = Path(hdir) / "guideline-cache.json"
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    files, roots = cache.setdefault("files", {}), cache.setdefault("roots", {})
    dirty = False
    found: list[Path] = []
    start = Path(cwd).resolve()
    for d in (start, *start.parents):
        found += [d / n for n in GUIDELINE_NAMES]
    if cwd not in roots:
        roots[cwd] = versions.git_root(start)
        dirty = True
    if roots[cwd]:
        found += sorted((Path(roots[cwd]) / ".claude" / "rules").glob("*.md"))
    found.append(Path.home() / ".claude" / "CLAUDE.md")
    out, seen = [], set()
    for p in found:
        if p in seen or not p.is_file():
            continue
        seen.add(p)
        key = os.path.realpath(p)
        st = p.stat()
        hit = files.get(key)
        if not hit or hit["mtime_ns"] != st.st_mtime_ns or hit["size"] != st.st_size:
            hit = {"mtime_ns": st.st_mtime_ns, "size": st.st_size, "hash": hash_of(p)}
            files[key] = hit
            dirty = True
        out.append({"path": str(p), "hash": hit["hash"]})
    if dirty:
        paths.atomic_write(cache_path, json.dumps(cache, ensure_ascii=False))
    return out


def _record(kind, data, hdir, pos, at, skill, text, meta=None, flags=None) -> dict:
    return {
        "id": "",
        "kind": kind,
        "at": at,
        "session": data.get("session_id", ""),
        "transcript": data.get("transcript_path", ""),
        "pos": pos,
        "cwd": data.get("cwd", ""),
        "skill": skill,
        "text": text[:TEXT_MAX],
        "file": None,
        "flags": flags or {},
        "meta": meta or {},
    }


def _start_skill(data, hdir, name, pos, at, hash_of) -> dict:
    set_skill(hdir, name, None, pos)
    skill = _skill_field(hdir, skill_state(hdir), data.get("transcript_path", ""), hash_of)
    return _record("skill_start", data, hdir, pos, at, skill, name)


def _prompt(data, hdir, pos, at, hash_of, cfg) -> list[dict]:
    text = data.get("prompt")
    if not isinstance(text, str) or text.startswith("<") or text.startswith(tr.CONTINUED):
        return []
    if any(m in text for m in cfg["eval_markers"]):
        (Path(hdir) / "skip").write_text(at)
        return []
    started = False
    m = SLASH_SKILL.match(text)
    if m:
        set_skill(hdir, m.group(1), None, pos)
        started = True
    state = skill_state(hdir)
    meta = {}
    if state:
        if not started:
            state["prompts_since"] = state.get("prompts_since", 0) + 1
            _save_state(hdir, state)
    else:
        meta["guidelines"] = _guidelines(hdir, data.get("cwd") or os.getcwd(), hash_of)
    skill = _skill_field(hdir, state, data.get("transcript_path", ""), hash_of)
    flags = {"after_rejection": after_rejection(data.get("transcript_path", ""), pos)}
    return [_record("prompt", data, hdir, pos, at, skill, text, meta, flags)]


def handle(event: str, data: dict, *, pos: int | None = None, at: str | None = None, hash_of=versions.remember) -> list[dict]:
    if os.environ.get("HARNESS_RETRO") == "1" or data.get("agent_id"):
        return []
    transcript_path = data.get("transcript_path")
    if not transcript_path or not data.get("session_id"):
        return []
    hdir = paths.harness_dir(transcript_path)
    if (hdir / "skip").exists():
        return []
    if pos is None:
        try:
            pos = os.path.getsize(transcript_path)
        except OSError:
            pos = 0
    at = at or paths.now()
    cfg = paths.load_config()
    records: list[dict] = []
    if event == "UserPromptSubmit":
        records = _prompt(data, hdir, pos, at, hash_of, cfg)
    elif event == "PreToolUse" and data.get("tool_name") == "Skill":
        name = (data.get("tool_input") or {}).get("skill")
        if isinstance(name, str) and name:
            records = [_start_skill(data, hdir, name, pos, at, hash_of)]
    elif event == "UserPromptExpansion":
        name = data.get("command_name")
        if not name:
            m = SLASH_ANY.match(data.get("prompt") or "")
            name = m.group(1) if m else None
        if isinstance(name, str) and name:
            records = [_start_skill(data, hdir, name.lstrip("/"), pos, at, hash_of)]
    return [_append(hdir, data["session_id"], r) for r in records]
