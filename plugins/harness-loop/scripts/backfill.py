#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""지난 세션 기록(transcript)을 줄 단위로 읽어 훅 입력을 재구성하고 evidence.handle 로 근거를 만든다."""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evidence
import paths
import transcript as tr
import versions

PRE_TOOLS = ("Skill", "Bash")
POST_TOOLS = ("AskUserQuestion", *evidence.EDIT_TOOLS)


def transcripts(days: int | None) -> list[Path]:
    """처리할 transcript. --days 가 있으면 수정 시각이 그 안, 없으면 installed_at 전. evidence.jsonl·skip 이 있거나 서브에이전트 기록이면 뺀다."""
    if days is not None:
        cut = time.time() - days * 86400
        keep = lambda mtime: mtime >= cut  # noqa: E731
    else:
        installed = paths.aware((paths.data_dir() / "installed_at").read_text().strip()).timestamp()
        keep = lambda mtime: mtime < installed  # noqa: E731
    out = []
    for t in sorted(paths.projects_root().glob("*/*.jsonl")):
        if "/subagents/" in t.as_posix():
            continue
        hdir = paths.harness_path(t)
        if (hdir / "evidence.jsonl").exists() or (hdir / "skip").exists():
            continue
        if keep(t.stat().st_mtime):
            out.append(t)
    return out


def _line_at(d: dict) -> str | None:
    att = d.get("attachment")
    return d.get("timestamp") or (att.get("timestamp") if isinstance(att, dict) else None)


def events(path: Path):
    """(event, data, pos, at) 를 기록 순서대로. pos 는 그 줄 끝 바이트, at 은 줄 timestamp(없으면 앞 줄 것, 그것도 없으면 파일 수정 시각)."""
    session = path.stem
    tools: dict[str, tuple[str, dict]] = {}
    turns: list[tr.Turn] = []
    at = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat()
    pos = 0
    with open(path, "rb") as f:
        for raw in f:
            pos += len(raw)
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            at = _line_at(d) or at
            if d.get("isSidechain") or d.get("isMeta"):
                continue
            base = {"session_id": session, "transcript_path": str(path), "cwd": d.get("cwd") or ""}
            content = (d.get("message") or {}).get("content")
            kind = d.get("type")
            if kind == "assistant" and isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict) or b.get("type") != "tool_use":
                        continue
                    name, inp = b.get("name", ""), b.get("input") or {}
                    tools[b.get("id")] = (name, inp)
                    if name in PRE_TOOLS:
                        yield "PreToolUse", {**base, "tool_name": name, "tool_input": inp, "tool_use_id": b.get("id")}, pos, at
            elif kind == "user":
                if isinstance(content, list):
                    for b in content:
                        if not isinstance(b, dict) or b.get("type") != "tool_result":
                            continue
                        body = evidence._result_text(b.get("content"))
                        if body.startswith(tr.REJECTED):
                            continue
                        name, inp = tools.get(b.get("tool_use_id"), ("", {}))
                        common = {**base, "tool_name": name, "tool_input": inp, "tool_use_id": b.get("tool_use_id")}
                        if b.get("is_error"):
                            yield "PostToolUseFailure", {**common, "error": body, "is_interrupt": body.startswith(tr.INTERRUPT)}, pos, at
                        elif name in POST_TOOLS:
                            resp = d.get("toolUseResult")
                            yield "PostToolUse", {**common, "tool_response": resp if isinstance(resp, dict) else {}}, pos, at
                slash = tr._slash_text(content) if (d.get("origin") or {}).get("kind", "human") == "human" else None
                text = slash if slash is not None else tr._human_text(content)
                if text is None or text.startswith((tr.CONTINUED, tr.INTERRUPT)):
                    continue
                resent = tr._resends_queued(turns, text)
                if not resent:
                    turns.append(tr.Turn(len(turns) + 1, d.get("cwd"), 0, text, slash=slash is not None))
                if slash is not None:
                    yield "UserPromptExpansion", {**base, "command_name": text.split()[0][1:], "prompt": text}, pos, at
                elif not resent:
                    yield "UserPromptSubmit", {**base, "prompt": text}, pos, at
            elif kind == "attachment":
                text = tr._queued_text(d.get("attachment"))
                if text is not None and not text.startswith((tr.CONTINUED, tr.INTERRUPT)):
                    turns.append(tr.Turn(len(turns) + 1, d.get("cwd"), 0, text, queued=True))
                    yield "UserPromptSubmit", {**base, "prompt": text}, pos, at


def run(days: int | None = None) -> dict:
    sessions = records = 0
    for path in transcripts(days):
        sessions += 1
        for event, data, pos, at in events(path):
            records += len(evidence.handle(event, data, pos=pos, at=at, cache=False,
                                           hash_of=lambda p, at=at: versions.hash_at(p, at)))
    return {"sessions": sessions, "records": records}
