#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""훅 입력을 근거 레코드로 바꿔 세션 harness/evidence.jsonl 에 덧붙인다(프롬프트·스킬 상태·가드)."""
import hashlib
import json
import os
import re
import time
from pathlib import Path

import apply_target
import paths
import transcript as tr
import versions

TEXT_MAX = 2000
TAIL = 64 * 1024
BASE_SCAN = 256 * 1024
SLASH_SKILL = re.compile(r"^/([\w.-]+:[\w.-]+)")
SLASH_ANY = re.compile(r"^/([^\s/]+)")
BASE_DIR = re.compile(r"Base directory for this skill: ([^\s\\\"]+)")
HEREDOC = re.compile(r"<<(-?)[ \t]*(?:'(\w+)'|\"(\w+)\"|(\w+))[^\n]*\n")
INLINE_ARG = re.compile(r"\b(?:python3?|bash|node|ruby|perl)\s+-[ce]\s+(\"(?:[^\"\\]|\\.)*\"|'[^']*')", re.S)
PATH_TOKEN = re.compile(r"[^\s'\"<>|;&()=]+")
EDIT_TOOLS = ("Edit", "Write", "MultiEdit")
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
    lines = data.decode("utf-8", "ignore").split("\n")
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


class _Cache:
    """세션 harness/guideline-cache.json: 파일 realpath 별 (mtime_ns, size, 해시)와 cwd 별 git 루트·프로젝트 루트.
    on=False 면 읽지도 쓰지도 않는다(백필처럼 hash_of 가 시각에 따라 달라질 때)."""

    def __init__(self, hdir, hash_of, on: bool = True):
        self.path, self.hash_of, self.on, self.dirty = Path(hdir) / "guideline-cache.json", hash_of, on, False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8")) if on else {}
        except (OSError, ValueError):
            data = {}
        self.data = data if isinstance(data, dict) else {}
        self.files = self.data.setdefault("files", {})
        self.roots = self.data.setdefault("roots", {})
        self.projects = self.data.setdefault("projects", {})

    def hash(self, p):
        """파일이 (mtime_ns, size) 그대로면 저장한 해시, 아니면 hash_of 로 새로 계산한다."""
        if not self.on:
            return self.hash_of(p)
        try:
            st = os.stat(p)
        except OSError:
            return self.hash_of(p)
        key = os.path.realpath(p)
        hit = self.files.get(key)
        if not hit or hit["mtime_ns"] != st.st_mtime_ns or hit["size"] != st.st_size:
            h = self.hash_of(p)
            if h is None:
                return None
            hit = {"mtime_ns": st.st_mtime_ns, "size": st.st_size, "hash": h}
            self.files[key] = hit
            self.dirty = True
        return hit["hash"]

    def _roots(self, cwd: str) -> None:
        if cwd not in self.roots or cwd not in self.projects:
            self.roots[cwd], self.projects[cwd] = versions.repo_roots(cwd, versions.HOOK_TIMEOUT)
            self.dirty = True

    def git_root(self, cwd: str) -> str | None:
        self._roots(cwd)
        return self.roots[cwd]

    def project_root(self, cwd: str) -> str:
        self._roots(cwd)
        return self.projects[cwd]

    def save(self) -> None:
        if self.dirty and self.on:
            paths.atomic_write(self.path, json.dumps(self.data, ensure_ascii=False))


def _guidelines(cache: _Cache, cwd: str) -> list[dict]:
    """cwd 에서 위로 올라가며 찾은 지침 파일, git 루트의 .claude/rules/*.md, ~/.claude/CLAUDE.md 의 해시."""
    found: list[Path] = []
    start = Path(cwd).resolve()
    for d in (start, *start.parents):
        found += [d / n for n in GUIDELINE_NAMES]
    root = cache.git_root(cwd)
    if root:
        found += sorted((Path(root) / ".claude" / "rules").glob("*.md"))
    found.append(Path.home() / ".claude" / "CLAUDE.md")
    out, seen = [], set()
    for p in found:
        if p in seen or not p.is_file():
            continue
        seen.add(p)
        out.append({"path": str(p), "hash": cache.hash(p)})
    return out


def inline_body(command: str) -> str | None:
    """명령에 들어 있는 heredoc·`-c`/`-e` 인라인 스크립트 본문. 없으면 None."""
    m = HEREDOC.search(command)
    if m:
        dash, delim = m.group(1), m.group(2) or m.group(3) or m.group(4)
        body = []
        for line in command[m.end():].splitlines(keepends=True):
            stripped = line.rstrip("\r\n")
            if (stripped.lstrip("\t") if dash else stripped) == delim:
                break
            body.append(line)
        return "".join(body)
    m = INLINE_ARG.search(command)
    return m.group(1)[1:-1] if m else None


def skill_scripts(command: str, skill_path: str | None, cwd: str | None = None) -> list[str]:
    """명령 안의 경로 중 skill_path 아래 실제 파일(realpath). 상대 경로는 cwd 기준, heredoc 본문은 보지 않는다."""
    if not skill_path:
        return []
    m = HEREDOC.search(command)
    head = command[:m.end()] if m else command
    root = os.path.realpath(skill_path) + os.sep
    out: list[str] = []
    for tok in PATH_TOKEN.findall(head):
        p = os.path.expanduser(tok)
        if not os.path.isabs(p):
            p = os.path.join(cwd or os.getcwd(), p)
        real = os.path.realpath(p)
        if real not in out and real.startswith(root) and os.path.isfile(real):
            out.append(real)
    return out


def is_tracked_file(path: str) -> bool:
    """스킬 파일 또는 지침 파일인지."""
    p = str(path).replace(os.sep, "/")
    name = p.rsplit("/", 1)[-1]
    if name == "SKILL.md":
        return True
    for d in Path(p).parents:
        if d.parent.name == "skills" and (d / "SKILL.md").is_file():
            return True
    if any(p == r or p.endswith("/" + r) for r in apply_target.RULE_FILES):
        return True
    return "/.claude/rules/" in p and name.endswith(".md")


def _save_inline(hdir, body: str) -> str:
    data = body.encode("utf-8", "surrogatepass")
    f = Path(hdir) / "scripts" / f"{hashlib.sha1(data).hexdigest()}.txt"
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
        with open(tmp, "wb") as out:
            out.write(data)
        os.replace(tmp, f)
    return str(f)


def _cur_skill(data, hdir, hash_of):
    state = skill_state(hdir)
    return state, _skill_field(hdir, state, data.get("transcript_path", ""), hash_of)


def _bash(data, hdir, pos, at, hash_of) -> list[dict]:
    command = (data.get("tool_input") or {}).get("command")
    if not isinstance(command, str):
        return []
    state, skill = _cur_skill(data, hdir, hash_of)
    spath = state.get("path") if state else None
    scripts = [{"path": p, "hash": hash_of(p)} for p in skill_scripts(command, spath, data.get("cwd"))]
    rec = _record("bash", data, hdir, pos, at, skill, command, {"scripts": scripts})
    body = inline_body(command)
    if body is not None:
        rec["file"] = _save_inline(hdir, body)
    return [rec]


def _failure(data, hdir, pos, at, hash_of, cfg) -> list[dict]:
    err = data.get("error")
    err = err if isinstance(err, str) else ""
    if any(m in err for m in cfg["eval_markers"]):
        (Path(hdir) / "skip").write_text(at)
        return []
    if data.get("is_interrupt"):
        return []
    text = str(data.get("tool_name") or "") + "\n" + "\n".join(err.splitlines()[:20])
    meta = {"input": json.dumps(data.get("tool_input"), ensure_ascii=False)[:600]}
    return [_record("failure", data, hdir, pos, at, _cur_skill(data, hdir, hash_of)[1], text, meta)]


def _answers(data) -> dict | None:
    resp = data.get("tool_response")
    for src in (resp, data.get("tool_input")):
        ans = src.get("answers") if isinstance(src, dict) else None
        if isinstance(ans, dict):
            return ans
    return None


def _ask(data, hdir, pos, at, hash_of) -> list[dict]:
    questions = (data.get("tool_input") or {}).get("questions")
    if not isinstance(questions, list):
        return []
    answers = _answers(data)
    missing = answers is None
    parts = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        lines = [f"Q: {q.get('question', '')}"]
        for o in q.get("options") or []:
            lines.append(f"- {o.get('label', '') if isinstance(o, dict) else o}")
        ans = (answers or {}).get(q.get("question"))
        if ans is None:
            missing = True
        else:
            lines.append("답: " + (", ".join(map(str, ans)) if isinstance(ans, list) else str(ans)))
        parts.append("\n".join(lines))
    meta = {"answer_missing": True} if missing else {}
    if data.get("tool_use_id"):
        meta["tool_use_id"] = data["tool_use_id"]
    return [_record("ask", data, hdir, pos, at, _cur_skill(data, hdir, hash_of)[1], "\n".join(parts), meta)]


def _skill_edit(data, hdir, pos, at, hash_of) -> list[dict]:
    path = (data.get("tool_input") or {}).get("file_path")
    if not isinstance(path, str) or not is_tracked_file(path):
        return []
    rec = _record("skill_edit", data, hdir, pos, at, _cur_skill(data, hdir, hash_of)[1], path,
                  {"hash_after": hash_of(path)})
    rec["file"] = path
    return [rec]


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


def _prompt(data, hdir, pos, at, cache: _Cache, cfg) -> list[dict]:
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
    if state and not started:
        state["prompts_since"] = state.get("prompts_since", 0) + 1
        _save_state(hdir, state)
    meta = {"guidelines": _guidelines(cache, data.get("cwd") or os.getcwd())}
    skill = _skill_field(hdir, state, data.get("transcript_path", ""), cache.hash)
    flags = {"after_rejection": after_rejection(data.get("transcript_path", ""), pos)}
    return [_record("prompt", data, hdir, pos, at, skill, text, meta, flags)]


def handle(event: str, data: dict, *, pos: int | None = None, at: str | None = None, hash_of=versions.remember,
           cache: bool = True) -> list[dict]:
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
    c = _Cache(hdir, hash_of, cache)
    hash_of = c.hash
    records: list[dict] = []
    if event == "UserPromptSubmit":
        records = _prompt(data, hdir, pos, at, c, cfg)
    elif event == "PreToolUse" and data.get("tool_name") == "Bash":
        records = _bash(data, hdir, pos, at, hash_of)
    elif event == "PostToolUseFailure":
        records = _failure(data, hdir, pos, at, hash_of, cfg)
    elif event == "PostToolUse" and data.get("tool_name") == "AskUserQuestion":
        records = _ask(data, hdir, pos, at, hash_of)
    elif event == "PostToolUse" and data.get("tool_name") in EDIT_TOOLS:
        records = _skill_edit(data, hdir, pos, at, hash_of)
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
    if records:
        project = c.project_root(data.get("cwd") or os.getcwd())
        for r in records:
            r["meta"]["project_root"] = project
    c.save()
    return [_append(hdir, data["session_id"], r) for r in records]
