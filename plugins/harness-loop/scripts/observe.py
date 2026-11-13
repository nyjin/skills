#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""미판정 근거를 판정자에게 넘길 묶음으로 만들고, 판정 결과를 observations.jsonl 에 저장한다."""
import json
from pathlib import Path

import paths

JUDGEABLE = ("prompt", "bash", "failure", "ask")
META_KINDS = ("skill_start",)
COPY_FIELDS = ("session", "at", "cwd", "skill", "file", "text", "meta")
WINDOW = 256 * 1024
FORWARD = 64 * 1024
TAIL_CHARS = 1500
SCHEMA = Path(__file__).resolve().parents[1] / "defaults" / "observe-schema.json"
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int, "number": (int, float)}


def validate(obj, schema: dict, path: str = "$") -> None:
    """JSON Schema 부분집합(type·required·enum·properties·items·minItems·minLength) 검증. 어긋나면 ValueError."""
    t = schema.get("type")
    if t:
        ok = isinstance(obj, _TYPES[t])
        if t in ("integer", "number") and isinstance(obj, bool):
            ok = False
        if not ok:
            raise ValueError(f"{path}: {t} 이어야 합니다")
    if "enum" in schema and obj not in schema["enum"]:
        raise ValueError(f"{path}: {schema['enum']} 중 하나여야 합니다")
    if isinstance(obj, str) and len(obj) < schema.get("minLength", 0):
        raise ValueError(f"{path}: 길이 {schema['minLength']} 이상이어야 합니다")
    if isinstance(obj, dict):
        for key in schema.get("required", []):
            if key not in obj:
                raise ValueError(f"{path}.{key}: 필수 항목이 없습니다")
        for key, sub in schema.get("properties", {}).items():
            if key in obj:
                validate(obj[key], sub, f"{path}.{key}")
    if isinstance(obj, list):
        if len(obj) < schema.get("minItems", 0):
            raise ValueError(f"{path}: 항목 {schema['minItems']} 개 이상이어야 합니다")
        if "items" in schema:
            for i, item in enumerate(obj):
                validate(item, schema["items"], f"{path}[{i}]")


def check_rule(e: dict, path: str) -> None:
    """참 decision_pattern 관찰은 rule 이 필요하다."""
    if e["type"] == "decision_pattern" and e["verdict"] is True and not e.get("rule"):
        raise ValueError(f"{path}.rule: 참 decision_pattern 은 rule 이 필요합니다")


def _rows(path) -> list[dict | None]:
    """줄마다 dict, 깨진 줄은 None(줄 번호를 유지한다)."""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            row = None
        out.append(row if isinstance(row, dict) else None)
    return out


def _cursor(hdir) -> int:
    try:
        return max(0, int((Path(hdir) / "evidence.cursor").read_text().strip()))
    except (OSError, ValueError):
        return 0


def _blocks(row) -> list[dict]:
    content = (row.get("message") or {}).get("content") if isinstance(row, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def _window(transcript, pos: int, forward: int = 0) -> list[dict]:
    """pos 앞 WINDOW 바이트(+ 뒤 forward 바이트)의 완전한 JSON 줄들. 창 첫 줄(잘렸을 수 있음)과 깨진 줄은 버린다."""
    try:
        start = max(0, pos - WINDOW)
        with open(transcript, "rb") as f:
            f.seek(start)
            data = f.read(pos - start + forward)
    except OSError:
        return []
    lines = data.decode("utf-8", "ignore").split("\n")
    if start > 0:
        lines = lines[1:]
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def excerpt(transcript, pos: int, kind: str) -> str:
    """prompt·ask: pos 앞 마지막 assistant 텍스트 끝 1500자 + 도구 호출 이름. 그 밖의 kind 는 빈 문자열."""
    if kind not in ("prompt", "ask"):
        return ""
    rows = _window(transcript, pos)
    idx = None
    for i in range(len(rows) - 1, -1, -1):
        if rows[i].get("type") == "assistant" and any(b.get("type") == "text" and b.get("text") for b in _blocks(rows[i])):
            idx = i
            break
    if idx is None:
        return ""
    mid = (rows[idx].get("message") or {}).get("id")
    group = [rows[idx]]
    if mid:
        group = [r for r in rows if r.get("type") == "assistant" and (r.get("message") or {}).get("id") == mid]
    text = "".join(b.get("text", "") for r in group for b in _blocks(r) if b.get("type") == "text")
    tools = [b.get("name", "") for r in group for b in _blocks(r) if b.get("type") == "tool_use"]
    parts = [text[-TAIL_CHARS:]]
    if tools:
        parts.append("도구 호출: " + ", ".join(tools))
    return "\n".join(parts)


def _ask_result(transcript, pos: int, tool_use_id: str | None = None) -> str:
    """ask 의 tool_result. tool_use_id 가 있으면 뒤 FORWARD 까지 찾고, 없으면 pos 이전 가장 가까운 AskUserQuestion 의 결과."""
    rows = _window(transcript, pos, FORWARD)
    ask_id = tool_use_id
    if not ask_id:
        for r in _window(transcript, pos):
            for b in _blocks(r):
                if b.get("type") == "tool_use" and b.get("name") == "AskUserQuestion":
                    ask_id = b.get("id")
    if not ask_id:
        return ""
    for r in rows:
        for b in _blocks(r):
            if b.get("type") == "tool_result" and b.get("tool_use_id") == ask_id:
                return _result_text(b.get("content"))
    return ""


def project_of(rec: dict) -> str:
    """기록 때 남긴 meta.project_root(같은 저장소의 하위 디렉터리·worktree 는 한 프로젝트), 없으면 cwd 의 마지막 두 단계."""
    root = (rec.get("meta") or {}).get("project_root")
    if root:
        return root
    cwd = rec.get("cwd") or ""
    return "/".join(Path(cwd).parts[-2:]) if cwd else ""


def _unchanged(hdir) -> bool:
    """마지막 커서 기록 뒤 evidence.jsonl 이 바뀌지 않았는지."""
    try:
        return (hdir / "evidence.jsonl").stat().st_mtime_ns <= (hdir / "evidence.cursor").stat().st_mtime_ns
    except OSError:
        return False


def _session_judgeable(hdir) -> list[dict]:
    """커서 뒤 판정 대상 근거. 하나도 없으면 커서를 마지막 완전한 줄 뒤로 옮겨 판정할 것 없는 줄이 남지 않게 한다."""
    rows = _rows(hdir / "evidence.jsonl")
    cur = _cursor(hdir)
    recs = [r for r in rows[cur:] if r and r.get("kind") in JUDGEABLE and r.get("at")]
    end = len(rows) - 1  # split("\n") 의 마지막 조각은 개행으로 끝나지 않은 줄(또는 빈 문자열)
    if not recs and end > cur:
        paths.atomic_write(hdir / "evidence.cursor", str(end))
    return recs


def _judgeable(skip_unchanged: bool = False) -> list[dict]:
    """skip 아닌 세션들의 커서 뒤 판정 대상 근거, 시각순. skip_unchanged 면 커서 기록 뒤 바뀌지 않은 세션은 읽지 않는다(훅용)."""
    recs = []
    for hdir in paths.all_harness_dirs():
        if (hdir / "skip").exists() or (skip_unchanged and _unchanged(hdir)):
            continue
        recs += _session_judgeable(hdir)
    recs.sort(key=lambda r: paths.aware(r["at"]))
    return recs


def pending_count(skip_unchanged: bool = False) -> int:
    return len(_judgeable(skip_unchanged))


def pending(limit: int) -> list[dict]:
    items = []
    for rec in _judgeable()[:limit]:
        ex = excerpt(rec.get("transcript", ""), rec.get("pos") or 0, rec["kind"])
        if rec["kind"] == "ask" and (rec.get("meta") or {}).get("answer_missing"):
            res = _ask_result(rec.get("transcript", ""), rec.get("pos") or 0, (rec.get("meta") or {}).get("tool_use_id"))
            if res:
                ex = (ex + "\n" if ex else "") + "응답: " + res
        items.append({**rec, "excerpt": ex, "project": project_of(rec)})
    return items


def observations(since: str | None = None) -> list[dict]:
    rows = [r for r in _rows(paths.data_dir() / "observations.jsonl") if r]
    if since:
        cut = paths.aware(since)
        rows = [r for r in rows if r.get("at") and paths.aware(r["at"]) >= cut]
    return rows


def _advance(hdir, ids: set) -> None:
    rows = _rows(hdir / "evidence.jsonl")
    last = max((i for i, r in enumerate(rows) if r and r.get("id") in ids), default=None)
    if last is None:
        return
    end = last + 1
    while end < len(rows) and rows[end] and rows[end].get("kind") in META_KINDS:
        end += 1
    if end > _cursor(hdir):
        paths.atomic_write(hdir / "evidence.cursor", str(end))


def save(result: dict) -> dict:
    validate(result, json.loads(SCHEMA.read_text(encoding="utf-8")))
    entries = result["observations"]
    seen = set()
    for i, e in enumerate(entries):
        if e["evidence_id"] in seen:
            raise ValueError(f"$.observations[{i}].evidence_id: 중복된 evidence_id {e['evidence_id']}")
        seen.add(e["evidence_id"])
        if e["type"] == "repeat" and not e.get("purpose"):
            raise ValueError(f"$.observations[{i}].purpose: repeat 는 purpose 가 필요합니다")
        check_rule(e, f"$.observations[{i}]")
    wanted = {e["evidence_id"] for e in entries}
    records = {}
    dirs = paths.all_harness_dirs()
    for hdir in dirs:
        for r in _rows(hdir / "evidence.jsonl"):
            if r and r.get("id") in wanted:
                records[r["id"]] = r
    unknown = sorted(wanted - set(records))
    if unknown:
        raise ValueError(f"$.observations: 알 수 없는 evidence_id {unknown[0]}")
    out = paths.data_dir() / "observations.jsonl"
    done = {r.get("evidence_id") for r in _rows(out) if r}
    saved = skipped = 0
    for e in entries:
        if e["evidence_id"] in done:
            skipped += 1
            continue
        rec = records[e["evidence_id"]]
        obs = {**e, **{f: rec.get(f) for f in COPY_FIELDS}, "project": project_of(rec)}
        paths.append_line(out, obs)
        saved += 1
    for hdir in dirs:
        _advance(hdir, wanted)
    return {"saved": saved, "skipped": skipped}
