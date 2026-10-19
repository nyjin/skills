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
    queued: bool = False
    slash: bool = False


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


COMMAND_NAME = re.compile(r"<command-name>\s*(/[^<\s]+)\s*</command-name>")
COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)


def _slash_text(content):
    """사람이 친 슬래시 명령 호출(`<command-name>/x</command-name>` 와 선택적 `<command-args>`)을 `/x args` 로. 아니면 None."""
    if not isinstance(content, (str, list)):
        return None
    text = _text(content).lstrip()
    m = COMMAND_NAME.search(text) if text.startswith("<command-") else None
    if not m:
        return None
    args = COMMAND_ARGS.search(text)
    return f"{m.group(1)} {args.group(1).strip()}".strip() if args else m.group(1)


def _queued_text(att):
    """AI 가 일하는 동안 사람이 친 발화(`queued_command` attachment)의 텍스트. 아니면 None."""
    if not isinstance(att, dict) or att.get("type") != "queued_command" or att.get("commandMode") != "prompt":
        return None
    if (att.get("origin") or {}).get("kind", "human") != "human":
        return None
    return _human_text(att.get("prompt"))


def _first_line(text):
    return next((l.strip() for l in text.splitlines() if l.strip()), "")[:200]


SKIP_LINE = re.compile(r"Exit code \d+$|#!|#{1,6}\s|total \d+$")
LS_LINE = re.compile(r"[dlcbps.\-]([rwxsStT\-]{9})[@+.]?\s")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
TRACEBACK = "Traceback (most recent call last):"
EXCEPTION_LINE = re.compile(r"[\w.]*(Error|Exception|Exit|Interrupt)\b.*")
BASH_ERROR = re.compile(r"\b(?:error|exception|fatal|failed|failure|denied|not found|no such file|cannot|can't|invalid|unknown|refused|timed out)\b", re.I)


def _skippable(line):
    ls = LS_LINE.match(line)
    return bool(SKIP_LINE.match(line)) or bool(ls and any(ch in "rwx" for ch in ls.group(1))) or not any(ch.isalnum() for ch in line)


def _error_line(text, tool=""):
    """오류 주제 줄. ANSI 이스케이프를 지운 뒤, Traceback 이면 마지막 예외 줄, 그다음 Bash 오류면 건너뛸 줄을 뺀 나머지 중 오류 낱말(BASH_ERROR)이 있는 마지막 줄, 아니면 `Exit code N`·셔뱅·마크다운 제목·`ls -l` 줄(`total N` 포함)·글자나 숫자가 없는 줄을 건너뛴 첫 줄. 다 건너뛰면 빈 문자열."""
    lines = [l.strip() for l in ANSI.sub("", text).splitlines() if l.strip()]
    if TRACEBACK in lines:
        exc = [l for l in lines if EXCEPTION_LINE.match(l) and not _skippable(l)]
        if exc:
            return exc[-1][:200]
    if tool == "Bash":
        hits = [l for l in lines if BASH_ERROR.search(l) and not _skippable(l)]
        if hits:
            return hits[-1][:200]
    return next((l for l in lines if not _skippable(l)), "")[:200]


RESEND_MIN = 8


def _resends_queued(turns, text) -> bool:
    """사람 발화가 직전 2턴 안의 대기 입력(queued) 턴 텍스트(RESEND_MIN 자 이상)와 같거나, 그것으로 시작하고 바로 뒤가 공백·문장부호(단어 경계)면 다시 보낸 것이다."""
    def resends(q):
        if not q.queued or len(q.user.strip()) < RESEND_MIN or not text.startswith(q.user):
            return False
        rest = text[len(q.user):]
        return not rest or not rest[0].isalnum()
    return any(resends(q) for q in turns[-2:])


def _slash_skill(turn):
    """슬래시 명령 턴의 명령 이름(`/` 뺀 첫 낱말). 슬래시 턴이 아니면 None."""
    return turn.user.split()[0][1:] if turn.slash and turn.user.split() else None


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
                            tool = tools.get(cid, "")
                            turn.errors.append(ToolError(cid, tool, _error_line(body, tool)))
                slash = _slash_text(content) if (d.get("origin") or {}).get("kind", "human") == "human" else None
                text = slash if slash is not None else _human_text(content)
                if text is None or text.startswith(CONTINUED):
                    continue
                if text.startswith(INTERRUPT):
                    if turn:
                        turn.interrupted = True
                    continue
                if _resends_queued(turns, text):
                    continue
                turns.append(Turn(len(turns) + 1, d.get("cwd"), lineno, text, slash=slash is not None))
                # 플러그인 스킬 형식(`/플러그인:스킬`) 슬래시 명령은 그 자체로 스킬 구간을 연다(이름은 `/` 뺀 전체, Skill 호출이 남기는 것과 같다). `/model` 같은 내장 명령은 열지 않는다
                name = _slash_skill(turns[-1])
                skill = name if name and ":" in name else None
            elif d.get("type") == "attachment":
                text = _queued_text(d.get("attachment"))
                if text is not None and not text.startswith((CONTINUED, INTERRUPT)):
                    skill = None
                    turns.append(Turn(len(turns) + 1, d.get("cwd"), lineno, text, queued=True))
            elif d.get("type") == "assistant" and turn and isinstance(content, list):
                for b in content:
                    if b.get("type") == "tool_use":
                        name, inp = b.get("name", ""), b.get("input") or {}
                        if name == "Skill":
                            skill = inp.get("skill")
                            # `/x` 로 연 턴에서 같은 이름의 Skill 호출이 나오면 그 앞 호출도 그 스킬 구간이다
                            slash_name = _slash_skill(turn)
                            if slash_name and skill and slash_name.split(":")[-1] == skill.split(":")[-1]:
                                for c in turn.calls:
                                    if c.skill is None:
                                        c.skill = skill
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
