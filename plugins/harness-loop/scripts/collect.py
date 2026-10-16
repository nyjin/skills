#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""세션 턴에서 C1~C11 신호를 결정적으로 뽑아 signals.jsonl 에 덧붙인다."""
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import state
import transcript
from transcript import Turn

PERMISSION_NOISE = ("requires approval", "multiple operations", "was blocked. For security", "Contains simple_expansion", "changes directory before", "This session is isolated in the worktree", "Claude requested permissions to")
CORRECTION = re.compile(r"^아니|그게 아니|말고|왜 .{0,20}(했|하지|하냐)|다시 (해|하|작성)|취지|벗어나|대충|빈약|지나치|너무 (길|많|복잡)")
QUESTION = re.compile(r"(할까요|갈까요|될까요|드릴까요|볼까요|좋을까요|괜찮을까요|어떨까요)\??\s*$")
BARE_YES = re.compile(r"^(네|넵|예|어|응|ㅇㅇ|ㅇㅋ|좋아요?|진행|ok|yes|그래)(?![가-힣A-Za-z])", re.I)
NO_ASK = re.compile(r"묻지 말|물어보지 말|질문 없이|쭉 가")
RETRACT = re.compile(r"정정|바로잡|틀렸습니다|과장했|가정이었|판단이 일렀")
REVERT = re.compile(r"되돌|원복|revert|하지 말|바꾸지 말", re.I)
SKILL_MENTION = re.compile(r"(?:^|\s)/([A-Za-z][\w:.-]*)(?![\w/:.-])|([A-Za-z][\w:-]*) 스킬", re.A)
SEARCH_CMD = re.compile(r"(?:^|[;&|(]\s*)(?:grep|rg|find)\s")
STATUS_ASK = re.compile(r"끝났|끝난건가|다 됐|되고 있나")
TRIVIAL_CMD = re.compile(r"(?:ls|cat|sed|grep|head|tail|echo|wc|find|pwd|rg|git (?:status|diff|log|show))\b")
CD_PREFIX = re.compile(r"^\s*cd\s+[^&;\n]*(?:&&|;)\s*")
ENV_PREFIX = re.compile(r"""^\s*[A-Za-z_]\w*=(?:"[^"]*"|'[^']*'|[^\s"']*)\s+""")
HARNESS_SCRIPT = re.compile(r"""^uv run --script\s+["']?\S*/scripts/(?:apply_target|collect|hook|judge|report|state|transcript)\.py\b""")
INLINE_PY = re.compile(r"^(?:uv run\s+)?python3?\s+(?:-c\b|-\s*<<|<<)")
PY_IMPORT = re.compile(r"(?:^|[;\n])\s*(?:import\s+([\w., ]+)|from\s+([\w.]+)\s+import)")
PY_STD = {"sys", "os", "re", "json", "collections"}
EDITS = {"Edit", "Write", "MultiEdit"}
JUDGED = {"C2", "C5", "C6", "C9"}
WORKER_ONLY = {"C1", "C3", "C10"}


def normalize_error(text: str) -> str:
    text = re.sub(r"</?tool_use_error>", "", text)
    text = re.sub(r"(?:~|\.{1,2})?/[^\s'\"(),]+", "P", text)
    text = re.sub(r"\d+", "N", text)
    return " ".join(text.split())[:80]


def _strip_prefixes(cmd: str) -> str:
    while (m := CD_PREFIX.match(cmd) or ENV_PREFIX.match(cmd)):
        cmd = cmd[m.end():]
    return cmd.strip()


def command_signature(cmd: str) -> str:
    cmd = _strip_prefixes(cmd)
    if INLINE_PY.match(cmd):
        mods = set()
        for names, frm in PY_IMPORT.findall(cmd):
            for name in (names.split(",") if names else [frm]):
                if name.strip():
                    mods.add(name.split()[0].split(".")[0])
        return f"py-inline[{','.join(sorted(mods - PY_STD))}]"
    first = cmd.splitlines()[0] if cmd else ""
    return " ".join(first.split()[:3])


def _skill_of(turn: Turn):
    return next((c.skill for c in reversed(turn.calls) if c.skill), None)


def _cmd(call):
    return (call.input or {}).get("command", "") if call.name == "Bash" else ""


def _risky(call) -> bool:
    return call.name in EDITS or call.name.endswith("createJiraIssue") or "acli jira workitem create" in _cmd(call)


def _codegraph_repo(cwd) -> bool:
    if not cwd or not Path(cwd).is_dir():
        return False
    try:
        top = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=2, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(top) and (Path(top) / ".codegraph").is_dir()


def _quotes_previous(user: str, tail: str) -> bool:
    return any(tail[k:k + 40].strip() and tail[k:k + 40] in user for k in range(len(tail) - 39))


def _owns(path: str, skill: str) -> bool:
    return f"/{skill.split(':')[-1]}/" in path


def signals(turns: list[Turn], kind: str) -> list[dict]:
    out, seen = [], set()

    def add(sig, turn, quote, skill=None):
        cond = sig.split(":")[0]
        if kind == "worker" and cond not in WORKER_ONLY or (sig, turn.index) in seen:
            return
        seen.add((sig, turn.index))
        out.append({"sig": sig, "condition": cond, "turn": turn.index, "line": turn.line, "skill": skill, "quote": (quote or "")[:200], "needs_judge": cond in JUDGED})

    first_cmd: dict[str, tuple] = {}
    cmd_count: Counter = Counter()
    prev = None
    for n, turn in enumerate(turns):
        order = {c.id: i for i, c in enumerate(turn.calls)}
        nxt = turns[n + 1] if n + 1 < len(turns) else None
        # C1 — 오류가 턴의 마지막 호출이면 이어지는 대기 입력(queued) 턴의 첫 호출을 다음 호출로 본다
        for e in turn.errors:
            if not e.text.strip() or any(p in e.text for p in PERMISSION_NOISE):
                continue
            i = order.get(e.call_id)
            if i is not None and (i + 1 < len(turn.calls) or (nxt is not None and nxt.queued and nxt.calls)):
                add("C1:" + normalize_error(e.text), turn, e.text, turn.calls[i].skill)
        for c in turn.calls:
            if c.name in EDITS and c.skill and _owns((c.input or {}).get("file_path", ""), c.skill):
                add("C1:skill-self-edit", turn, c.input.get("file_path"), c.skill)
        # C2·C7 후보 — C7 여부는 judge 가 condition 으로 고른다
        if prev and CORRECTION.search(turn.user):
            add("C2:?", turn, turn.user, _skill_of(prev))
        if turn.rejected or turn.interrupted:
            add("C2:?", turn, turn.user, _skill_of(turn))
        # C3
        for c in turn.calls:
            cmd = _cmd(c)
            if cmd and not HARNESS_SCRIPT.match(_strip_prefixes(cmd)):
                s = command_signature(cmd)
                cmd_count[s] += 1
                first_cmd.setdefault(s, (turn, cmd, c.skill))
                if "<<" in cmd and re.search(r"^\s*def \w+", cmd, re.M):
                    add("C3:inline-helper", turn, cmd, c.skill)
            if c.name == "Read" and c.skill and "/references/" in (c.input or {}).get("file_path", "") and _owns(c.input["file_path"], c.skill):
                add(f"C3:skill-preamble:{c.skill}", turn, c.input["file_path"], c.skill)
        # C4
        if prev and QUESTION.search(prev.ai_tail[-80:]) and len(turn.user.strip()) <= 30 and BARE_YES.match(turn.user.strip()) and not turn.user.strip().endswith(("?", "？")):
            add("C4:bare-yes", turn, turn.user, _skill_of(prev))
        if NO_ASK.search(turn.user) and QUESTION.search(turn.ai_tail[-80:]):
            add("C4:asked-after-no-ask", turn, turn.ai_tail[-80:], _skill_of(turn))
        # C5
        if RETRACT.search(turn.ai_tail):
            add("C5:?", turn, turn.ai_tail, _skill_of(turn))
        # C6
        if any(_risky(c) and c.id in turn.rejected for c in turn.calls):
            add("C6:?", turn, turn.user, _skill_of(turn))
        if prev and REVERT.search(turn.user) and any(_risky(c) for c in prev.calls):
            add("C6:?", turn, turn.user, _skill_of(prev))
        # C8 — 슬래시 명령 턴은 그 명령 자체가 호출이다
        if not turn.slash and not any(c.name == "Skill" for c in turn.calls):
            for m in SKILL_MENTION.finditer(turn.user):
                slash, word = m.groups()
                if slash and ":" not in slash and turn.user[:m.start(1)].strip() != "/":
                    continue
                add(f"C8:skill:{slash or word}", turn, turn.user, slash or word)
        searches = sum(1 for c in turn.calls if SEARCH_CMD.search(_cmd(c)))
        if searches >= 5 and not any("codegraph" in c.name or "codegraph" in _cmd(c) for c in turn.calls) and _codegraph_repo(turn.cwd):
            add("C8:codegraph-unused", turn, turn.user, _skill_of(turn))
        # C9
        if prev and _quotes_previous(turn.user, prev.ai_tail):
            add("C9:?", turn, turn.user, _skill_of(prev))
        # C10
        polls = sum(1 for c in turn.calls if c.name == "Monitor" or "sleep " in _cmd(c) or "agent wait" in _cmd(c))
        if polls >= 3:
            add("C10:polling", turn, turn.user, _skill_of(turn))
        if STATUS_ASK.search(turn.user):
            add("C10:status-ask", turn, turn.user, _skill_of(prev) if prev else None)
        # C11
        if turn.api_error:
            add("C11:api-error", turn, turn.user, _skill_of(turn))
        if prev and not prev.calls and not prev.ai_tail and not prev.queued and not prev.slash and not turn.queued:
            add("C11:resend", turn, turn.user)
        prev = turn
    for s, n in cmd_count.items():
        if n >= 2 and not TRIVIAL_CMD.match(s):
            turn, cmd, skill = first_cmd[s]
            add(f"C3:cmd:{s}", turn, cmd, skill)
    return out


def run(transcript_path: Path) -> list[dict]:
    """세션 전체를 다시 파싱해 아직 signals.jsonl 에 없는 (sig, line) 신호만 덧붙이고 cursor 를 옮긴다."""
    path = Path(transcript_path)
    hdir = state.harness_dir(path)
    turns, last = transcript.parse(path, 0)
    kind = transcript.session_kind(turns, state.load_config())
    if kind == "eval":
        (hdir / "skip").touch()
        return []
    log = hdir / "signals.jsonl"
    known = set()
    if log.exists():
        for raw in log.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(raw)
            except ValueError:
                continue
            known.add((r.get("sig"), r.get("line")))
    new = [s for s in signals(turns, kind) if (s["sig"], s["line"]) not in known]
    if new:
        with open(log, "a", encoding="utf-8") as f:
            for s in new:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
    state.write_cursor(hdir, last)
    return new


def main(argv=None) -> None:
    """명시 호출용 CLI. 세션 락을 잡고 run 한다. judge.background 는 이미 락을 쥐고 run 을 직접 부른다."""
    path = Path((sys.argv[1:] if argv is None else argv)[0])
    hdir = state.locked_cli(state.harness_dir(path), "신호를 모으지 않았다")
    try:
        print(json.dumps(run(path), ensure_ascii=False))
    finally:
        state.release_lock(hdir)


if __name__ == "__main__":
    main()
