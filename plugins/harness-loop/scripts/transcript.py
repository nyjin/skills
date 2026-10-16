#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""세션 기록(jsonl)을 사람 발화 단위 턴으로 나누고 세션 종류를 판정한다."""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

INTERRUPT = "[Request interrupted by user"
CONTINUED = "This session is being continued from a previous conversation"
REJECTED = "The user doesn't want to proceed"
PASTED = "<pasted_content"
WORKER_PREFIXES = ("[ibs-", "executor", "Read the brief", "export HERDR_PW_WORKER", "## 격리")


@dataclass
class Call:
    id: str
    name: str
    input: dict
    skill: str | None


@dataclass
class ToolError:
    call_id: str
    tool: str
    text: str


@dataclass
class Turn:
    index: int
    cwd: str | None
    line: int
    user: str
    calls: list[Call] = field(default_factory=list)
    errors: list[ToolError] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    interrupted: bool = False
    ai_tail: str = ""
    api_error: bool = False


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _human_text(content):
    """사람이 친 발화 텍스트. 사람 발화가 아니면 None."""
    if isinstance(content, list):
        if not any(b.get("type") == "text" for b in content) or any(b.get("type") == "tool_result" for b in content):
            return None
    elif not isinstance(content, str):
        return None
    text = _text(content)
    stripped = text.lstrip()
    if stripped.startswith("<") and not stripped.startswith(PASTED):
        return None
    return text


def _first_line(text):
    return next((l.strip() for l in text.splitlines() if l.strip()), "")[:200]


SKIP_LINE = re.compile(r"Exit code \d+$|#!|#{1,6}\s|[d\-lcbps][rwx\-]{9}")
TRACEBACK = "Traceback (most recent call last):"
EXCEPTION_LINE = re.compile(r"\w[\w.]*(Error|Exception|Exit|Interrupt)\b.*")


def _skippable(line):
    return bool(SKIP_LINE.match(line)) or not any(ch.isalnum() for ch in line)


def _error_line(text):
    """오류 주제 줄. Traceback 이면 마지막 예외 줄, 아니면 `Exit code N`·셔뱅·마크다운 제목·`ls -l` 줄·글자나 숫자가 없는 줄을 건너뛴 첫 줄. 다 건너뛰면 빈 문자열."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if TRACEBACK in lines:
        exc = [l for l in lines if EXCEPTION_LINE.match(l)]
        if exc:
            return exc[-1][:200]
    return next((l for l in lines if not _skippable(l)), "")[:200]


def parse(path: Path, start_line: int = 0) -> tuple[list[Turn], int]:
    """파일 전체를 읽어 턴을 만들고, start_line 이후 줄이 하나라도 있는 턴만 돌려준다. 마지막으로 완전히 읽은 줄 번호를 함께 돌려준다."""
    turns: list[Turn] = []
    tools: dict[str, str] = {}
    skill = None
    last = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for lineno, raw in enumerate(f, 1):
            try:
                d = json.loads(raw)
            except ValueError:
                if not raw.endswith("\n"):
                    break
                last = lineno
                continue
            last = lineno
            if d.get("isSidechain") or d.get("isMeta"):
                continue
            content = (d.get("message") or {}).get("content")
            turn = turns[-1] if turns else None
            if d.get("type") == "user":
                if isinstance(content, list) and turn:
                    for b in content:
                        if b.get("type") != "tool_result":
                            continue
                        body = _text(b.get("content"))
                        if body.startswith(REJECTED):
                            turn.rejected.append(b.get("tool_use_id"))
                        elif b.get("is_error"):
                            cid = b.get("tool_use_id")
                            turn.errors.append(ToolError(cid, tools.get(cid, ""), _error_line(body)))
                text = _human_text(content)
                if text is None or text.startswith(CONTINUED):
                    continue
                if text.startswith(INTERRUPT):
                    if turn:
                        turn.interrupted = True
                    continue
                skill = None
                turns.append(Turn(len(turns) + 1, d.get("cwd"), lineno, text))
            elif d.get("type") == "assistant" and turn and isinstance(content, list):
                for b in content:
                    if b.get("type") == "tool_use":
                        name, inp = b.get("name", ""), b.get("input") or {}
                        if name == "Skill":
                            skill = inp.get("skill")
                        tools[b.get("id")] = name
                        turn.calls.append(Call(b.get("id"), name, inp, skill))
                    elif b.get("type") == "text":
                        text = b.get("text", "")
                        if d.get("isApiErrorMessage") or text.startswith("API Error"):
                            turn.api_error = True
                        elif text.strip():
                            turn.ai_tail = text[-500:]
    ends = [t.line - 1 for t in turns[1:]] + [last]
    return [t for t, end in zip(turns, ends) if end > start_line], last


def session_kind(turns: list[Turn], cfg: dict) -> str:
    """start_line=0 으로 파싱한 세션 전체 턴 목록을 받는다."""
    markers = cfg.get("eval_markers") or []
    if any(m in e.text for t in turns for e in t.errors for m in markers):
        return "eval"
    if turns:
        first = re.sub(r"^<pasted_content[^>]*>", "", turns[0].user.lstrip()).lstrip()
        if first.startswith(WORKER_PREFIXES):
            return "worker"
    return "normal"
