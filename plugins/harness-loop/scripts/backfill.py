#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""지난 세션 기록(transcript)을 줄 단위로 읽어 훅 입력을 재구성하고 evidence.handle 로 근거를 만든다."""
import json
import os
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
LIVE_SEC = 600


def transcripts(days: int | None) -> list[Path]:
    """처리할 transcript. --days 가 있으면 수정 시각이 그 안의 것만. evidence.jsonl·skip 이 있거나, 서브에이전트 기록이거나,
    LIVE_SEC 안에 수정돼 진행 중일 수 있는 세션은 뺀다."""
    now = time.time()
    cut = now - days * 86400 if days is not None else None
    out = []
    for t in sorted(paths.projects_root().glob("*/*.jsonl")):
        if "/subagents/" in t.as_posix():
            continue
        hdir = paths.harness_path(t)
        if (hdir / "evidence.jsonl").exists() or (hdir / "skip").exists():
            continue
        mtime = t.stat().st_mtime
        if mtime <= now - LIVE_SEC and (cut is None or mtime >= cut):
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
                    # live 훅은 슬래시 명령도 UserPromptSubmit(`/x args`)을 먼저 내고 UserPromptExpansion 을 낸다
                    yield "UserPromptSubmit", {**base, "prompt": text}, pos, at
                if slash is not None:
                    yield "UserPromptExpansion", {**base, "command_name": text.split()[0][1:], "prompt": text}, pos, at
            elif kind == "attachment":
                text = tr._queued_text(d.get("attachment"))
                if text is not None and not text.startswith((tr.CONTINUED, tr.INTERRUPT)):
                    # 사람이 친 대기 입력이라 기록한다. live 훅이 이 입력에 UserPromptSubmit 을 내는지는 확인하지 않았다
                    turns.append(tr.Turn(len(turns) + 1, d.get("cwd"), 0, text, queued=True))
                    yield "UserPromptSubmit", {**base, "prompt": text}, pos, at


def run(days: int | None = None) -> dict:
    """HARNESS_RETRO 가드는 훅 재귀 방지용이라 백필 동안에는 환경에서 잠시 뺀다."""
    sessions = records = 0
    saved = os.environ.pop("HARNESS_RETRO", None)
    try:
        for path in transcripts(days):
            sessions += 1
            for event, data, pos, at in events(path):
                records += len(evidence.handle(event, data, pos=pos, at=at, cache=False,
                                               hash_of=lambda p, at=at: versions.hash_at(p, at)))
    finally:
        if saved is not None:
            os.environ["HARNESS_RETRO"] = saved
    return {"sessions": sessions, "records": records}
