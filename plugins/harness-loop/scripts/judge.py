#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""judge 입력(digest)을 만들고, judge 출력을 채점해 action-items.json 에 병합한다."""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import collect
import state
import transcript

SCHEMA = Path(__file__).resolve().parents[1] / "defaults" / "judge-schema.json"
SIG = re.compile(r"^C(1[01]|[1-9]):(?!\?$).+")
CONDITIONS = {f"C{i}" for i in range(1, 12)}
MODES = ("explicit", "session-end", "sweep")
KEPT = {"approved", "rejected", "applied", "regressed"}
LIVE = {"propose", "watch", "approved", "applied", "regressed"}
JUDGE_TIMEOUT = 540
BG_OK, BG_FAILED, BG_CAP, BG_LOCKED = 0, 1, 2, 3
C2_C7_RULE = "`C2:?` 후보마다 항목의 `condition` 을 C2(AI 가 원치 않는 일을 했다)와 C7(메모리·CLAUDE.md 에 있는 기존 규칙을 따르지 않았거나 잘못된 곳에 저장했다) 중 하나로 골라, 후보 하나에서 항목은 최대 하나만 내고 같은 근거로 C2 항목과 C7 항목을 함께 내지 않는다."
ITEM_RULE = "항목은 대상 파일에 바꿀 것이 있을 때만 낸다. 결함이 아니거나 고칠 것이 없으면(예: \"변경 불필요\") 항목을 내지 않는다."
SIG_RULE = "`?` 신호는 기존 sig 목록에서 주제가 같은 것을 재사용하고 없을 때만 `<조건>:<kebab-토픽>` 로 새로 만든다."


def _cell(text) -> str:
    return " ".join(str(text if text is not None else "").split()).replace("|", "\\|")


def _call_summary(call) -> str:
    inp = call.input or {}
    detail = inp.get("command") or inp.get("file_path") or inp.get("skill") or inp.get("pattern") or ""
    return f"`{call.name}` {_cell(detail)[:120]}".rstrip()


def _read_signals(hdir: Path) -> list[dict]:
    path = hdir / "signals.jsonl"
    if not path.exists():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


def _signal_lines(hdir: Path) -> int:
    path = hdir / "signals.jsonl"
    return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0


def _last_upto(hdir: Path) -> int:
    runs = sorted((hdir / "runs").glob("*.json")) if (hdir / "runs").is_dir() else []
    for run in reversed(runs):
        try:
            return int(json.loads(run.read_text(encoding="utf-8")).get("signals_upto", 0))
        except (OSError, ValueError, TypeError):
            continue
    return 0


def pending_signals(hdir: Path) -> list[dict]:
    """마지막 run 의 signals_upto 이후에 덧붙은 신호."""
    path = hdir / "signals.jsonl"
    if not path.exists():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines()[_last_upto(hdir):]:
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


def digest(transcript_path: Path, new_signals: list[dict]) -> str:
    path = Path(transcript_path)
    session = path.stem
    parts = [f"# harness-loop judge 입력\n\n세션 `{session}`. 근거(evidence)의 `session` 에는 `{session}` 을 쓴다.", state.data_dir().joinpath("rubric.md").read_text(encoding="utf-8").strip()]

    rows = ["## 신호", "", "| sig | 조건 | 턴 | 스킬 | judge 판정 | 인용 |", "|---|---|---|---|---|---|"]
    for s in new_signals:
        rows.append(f"| {_cell(s.get('sig'))} | {_cell(s.get('condition'))} | {_cell(s.get('turn'))} | {_cell(s.get('skill'))} | {'예' if s.get('needs_judge') else '아니오'} | {_cell(s.get('quote'))} |")
    parts.append("\n".join(rows))

    turns = {t.index: t for t in transcript.parse(path, 0)[0]}
    blocks = ["## 신호가 걸린 턴"]
    for idx in sorted({s.get("turn") for s in new_signals if s.get("turn") in turns}):
        t = turns[idx]
        lines = [f"### 턴 {idx} (줄 {t.line})", "", "사람 발화:", "", "```", t.user[:500], "```", "", "호출:"]
        lines += [f"- {_call_summary(c)}" for c in t.calls[:12]] or ["- 없음"]
        if len(t.calls) > 12:
            lines.append(f"- … 외 {len(t.calls) - 12}개")
        lines += ["", "오류:"]
        lines += [f"- `{e.tool}` {_cell(e.text)}" for e in t.errors if e.text.strip()] or ["- 없음"]
        if t.rejected:
            lines.append(f"- 사용자가 거절한 호출 {len(t.rejected)}개")
        if t.interrupted:
            lines.append("- 사용자가 중단함")
        lines += ["", "AI 응답 꼬리:", "", "```", t.ai_tail, "```"]
        blocks.append("\n".join(lines))
    parts.append("\n\n".join(blocks))

    live = set()
    for hdir in state.all_harness_dirs():
        own = ", 이 세션" if hdir.parent.name == session else ""
        for sig, item in state.load_items(hdir).items():
            if item.get("status") in LIVE:
                live.add((sig, f"{item.get('status')}{own}"))
    parts.append("## 살아 있는 sig\n\n" + ("\n".join(f"- {sig} ({note})" for sig, note in sorted(live)) or "- 없음"))

    decisions = state.load_decisions()
    parts.append("## 결정된 sig\n\n" + ("\n".join(f"- {sig}: {d['decision']}" for sig, d in sorted(decisions.items())) or "- 없음"))

    parts.append(f"## sig 규칙\n\n{SIG_RULE} `?` 가 아닌 신호는 sig 를 그대로 쓴다. {C2_C7_RULE} {ITEM_RULE}")
    parts.append("## 출력 스키마\n\n```json\n" + SCHEMA.read_text(encoding="utf-8").strip() + "\n```")
    return "\n\n".join(parts) + "\n"


def recurrence(sig: str, this_session: str) -> int:
    sessions = {this_session}
    for hdir in state.all_harness_dirs():
        sid = hdir.parent.name
        if sid in sessions:
            continue
        if sig in state.load_items(hdir) or (not _placeholder(sig) and any(s.get("sig") == sig for s in _read_signals(hdir))):
            sessions.add(sid)
    return len(sessions)


def _placeholder(sig: str) -> bool:
    return sig.split(":", 1)[-1] == "?"


def _recurrence_score(sessions: int) -> int:
    return 1 if sessions <= 1 else 2 if sessions <= 3 else 3


def _is_global(path: str) -> bool:
    return path in ("~/.claude/CLAUDE.md", str(Path.home() / ".claude/CLAUDE.md")) or "/memory/" in path


def score(item: dict, cfg: dict) -> tuple[float, str]:
    w = cfg["weights"]
    total = round(w["recurrence"] * _recurrence_score(item["sessions"]) + w["impact"] * item["impact"] + w["actionability"] * item["actionability"], 6)
    decision = state.load_decisions().get(item["sig"])
    if decision and decision["decision"] == "rejected":
        return total, "rejected"
    if item.get("duplicate_of_rule") and not item.get("rule_unenforced"):
        return total, "discarded"
    bump = cfg["global_bump"] if _is_global((item.get("target") or {}).get("path", "")) else 0
    if total >= round(cfg["propose"] + bump, 6):
        return total, "propose"
    if total >= round(cfg["watch"] + bump, 6):
        return total, "watch"
    return total, "discarded"


def _validate(output) -> list[dict]:
    def need(ok, msg):
        if not ok:
            raise ValueError(msg)

    need(isinstance(output, dict) and isinstance(output.get("items"), list), "items 목록이 없다")
    for n, it in enumerate(output["items"]):
        where = f"items[{n}]"
        need(isinstance(it, dict), f"{where} 가 객체가 아니다")
        need(isinstance(it.get("sig"), str) and SIG.match(it["sig"]), f"{where}.sig 가 `C<n>:<토픽>` 이 아니거나 `?` 자리표시다")
        need(it.get("condition") in CONDITIONS, f"{where}.condition 이 C1~C11 이 아니다")
        for key in ("impact", "actionability"):
            v = it.get(key)
            need(isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 3, f"{where}.{key} 가 1~3 정수가 아니다")
        ev = it.get("evidence")
        need(isinstance(ev, list) and ev, f"{where}.evidence 가 비었다")
        for e in ev:
            need(isinstance(e, dict) and isinstance(e.get("session"), str) and isinstance(e.get("turn"), int) and isinstance(e.get("quote"), str), f"{where}.evidence 항목에 session·turn·quote 가 없다")
        need(isinstance(it.get("target"), dict) and isinstance(it["target"].get("path"), str) and it["target"]["path"], f"{where}.target.path 가 없다")
        need(isinstance(it.get("proposal"), str), f"{where}.proposal 이 없다")
        need("duplicate_of_rule" in it and (it["duplicate_of_rule"] is None or isinstance(it["duplicate_of_rule"], str)), f"{where}.duplicate_of_rule 이 문자열·null 이 아니다")
        need(isinstance(it.get("rule_unenforced"), bool), f"{where}.rule_unenforced 가 bool 이 아니다")
        skills = it.get("skills")
        need(isinstance(skills, dict) and all(isinstance(k, str) and isinstance(v, int) for k, v in skills.items()), f"{where}.skills 가 {{이름: 건수}} 가 아니다")
    return output["items"]


def _drop_c2_c7_twins(items: list[dict]) -> list[dict]:
    """같은 토픽의 C2·C7 항목이 함께 오면 출력 순서상 먼저 온 것만 남긴다."""
    first, out = {}, []
    for it in items:
        cond, topic = it["condition"], it["sig"].split(":", 1)[1]
        if cond in ("C2", "C7") and first.setdefault(topic, cond) != cond:
            continue
        out.append(it)
    return out


def _owner(path: str) -> str:
    try:
        import apply_target

        c = apply_target.classify(path)
        return f"{c['kind']}:{c['root']}" if c.get("root") else c["kind"]
    except Exception:
        return "unknown"


def _next_run(hdir: Path, mode: str) -> Path:
    runs = hdir / "runs"
    runs.mkdir(exist_ok=True)
    nums = [int(p.name[:3]) for p in runs.glob("*.json") if p.name[:3].isdigit()]
    return runs / f"{max(nums, default=0) + 1:03d}-{mode}.json"


def merge(transcript_path: Path, judge_output: dict, mode: str) -> dict:
    if mode not in MODES:
        raise ValueError(f"mode 는 {'|'.join(MODES)} 중 하나다: {mode}")
    new_items = _drop_c2_c7_twins([it for it in _validate(judge_output) if (it["impact"], it["actionability"]) != (1, 1)])
    path = Path(transcript_path)
    session = path.stem
    hdir = state.harness_dir(path)
    cfg = state.load_config()
    items = state.load_items(hdir)
    overrides = state.load_score_overrides()
    for it in new_items:
        sig = it["sig"]
        it = {**it, **overrides.get(sig, {})}
        sessions = recurrence(sig, session)
        total, status = score({**it, "sessions": sessions}, cfg)
        fresh = {
            "sig": sig,
            "condition": it["condition"],
            "status": status,
            "scores": {"recurrence": _recurrence_score(sessions), "impact": it["impact"], "actionability": it["actionability"]},
            "score": total,
            "sessions": sessions,
            "target": {"path": it["target"]["path"], "owner": _owner(it["target"]["path"])},
            "proposal": it["proposal"],
            "evidence": [],
            "skills": {},
            "duplicate_of_rule": it["duplicate_of_rule"],
            "rule_unenforced": it["rule_unenforced"],
        }
        old = items.get(sig, {})
        merged = {**old, **fresh}
        evidence = list(old.get("evidence", []))
        seen = {json.dumps(e, sort_keys=True, ensure_ascii=False) for e in evidence}
        for e in it["evidence"]:
            key = json.dumps(e, sort_keys=True, ensure_ascii=False)
            if key not in seen:
                seen.add(key)
                evidence.append(e)
        merged["evidence"] = evidence
        skills = dict(old.get("skills", {}))
        for name, n in it["skills"].items():
            skills[name] = max(skills.get(name, 0), n)
        merged["skills"] = skills
        if old.get("status") in KEPT or (old.get("status") == "watch" and old.get("regressed_after")):
            merged["status"] = old["status"]
        items[sig] = merged
    state.save_items(hdir, items)
    run = {"mode": mode, "at": datetime.now().astimezone().isoformat(), "signals_upto": _signal_lines(hdir), "output": judge_output}
    _next_run(hdir, mode).write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    return items


def rescore() -> int:
    cfg = state.load_config()
    overrides = state.load_score_overrides()
    changed = 0
    for hdir in state.all_harness_dirs():
        items = state.load_items(hdir)
        dirty = False
        for sig, it in items.items():
            stored = it.get("scores") or {}
            scores = {**stored, **overrides.get(sig, {})}
            try:
                total, status = score({**it, "impact": scores["impact"], "actionability": scores["actionability"]}, cfg)
            except (KeyError, TypeError):
                continue
            if it.get("status") in KEPT or (it.get("status") == "watch" and it.get("regressed_after")):
                status = it["status"]
            if total != it.get("score") or status != it.get("status") or scores != stored:
                it["score"], it["status"], it["scores"] = total, status, scores
                changed += 1
                dirty = True
        if dirty:
            state.save_items(hdir, items)
    return changed


def runs_today() -> int:
    """오늘 시작한 백그라운드 판정 수. 예약 줄과 결과 줄은 (session, at) 이 같아 한 번만 센다."""
    path = state.data_dir() / "judge-runs.jsonl"
    if not path.exists():
        return 0
    today = datetime.now().astimezone().date().isoformat()
    runs = set()
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines()):
        try:
            r = json.loads(raw)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("date") == today:
            runs.add((r.get("session"), r.get("at") or n))
    return len(runs)


def _append_run(line: dict) -> None:
    with open(state.data_dir() / "judge-runs.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def _judge_output(stdout: str) -> dict:
    try:
        reply = json.loads(stdout)
    except ValueError:
        raise ValueError(f"stdout 이 JSON 이 아니다: {stdout[:200]!r}")
    if not isinstance(reply, dict):
        raise ValueError("응답이 객체가 아니다")
    if reply.get("is_error"):
        raise ValueError(f"claude 오류 응답: {str(reply.get('result'))[:200]}")
    if isinstance(reply.get("structured_output"), dict):
        return reply["structured_output"]
    try:
        return json.loads(reply.get("result") or "")
    except (TypeError, ValueError):
        raise ValueError("structured_output 이 없고 result 도 JSON 이 아니다")


def _invoke(path: Path, hdir: Path, mode: str, claude_bin: str) -> None:
    text = digest(path, pending_signals(hdir))
    cmd = [claude_bin, "-p", "--setting-sources", "project", "--strict-mcp-config", "--no-session-persistence", "--tools", "", "--output-format", "json", "--json-schema", SCHEMA.read_text(encoding="utf-8")]
    os.utime(hdir / "lock")
    try:
        proc = subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=JUDGE_TIMEOUT, cwd=state.data_dir(), env={**os.environ, "HARNESS_RETRO": "1"})
    except subprocess.TimeoutExpired:
        raise ValueError(f"timeout {JUDGE_TIMEOUT}s")
    if proc.returncode != 0:
        raise ValueError(f"exit {proc.returncode}: {' '.join(proc.stderr.split())[:200]}")
    merge(path, _judge_output(proc.stdout), mode)


def _log_error(path: Path, error: Exception) -> None:
    try:
        with open(state.data_dir() / "hook-errors.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().astimezone().isoformat()} judge background {path}: {' '.join(str(error).split())}\n")
    except Exception:
        pass


def _judge_once(path: Path, hdir: Path, mode: str, claude_bin: str) -> bool:
    if runs_today() >= state.load_config()["max_background_judges_per_day"]:
        return False
    collect.run(path)
    if (hdir / "skip").exists() or not pending_signals(hdir):
        return True
    now = datetime.now().astimezone()
    line = {"at": now.isoformat(), "date": now.date().isoformat(), "session": path.stem, "transcript": str(path), "mode": mode}
    _append_run({**line, "ok": None, "reserved": True})
    line["ok"] = True
    error = None
    try:
        _invoke(path, hdir, mode, claude_bin)
    except Exception as e:
        error = e
        line.update(ok=False, error=str(e))
    _append_run(line)
    if error:
        raise error
    return True


def background_status(transcript_path: Path, mode: str, claude_bin: str = "claude") -> int:
    """락을 잡고 새 신호를 모아 claude -p 로 판정해 병합한다. BG_OK(판정했거나 할 일 없음), BG_FAILED(실패, 로그 남김), BG_CAP(하루 상한), BG_LOCKED(락이 잡혀 있음)."""
    path = Path(transcript_path)
    hdir = state.harness_dir(path)
    if not state.acquire_lock(hdir):
        return BG_LOCKED
    try:
        return BG_OK if _judge_once(path, hdir, mode, claude_bin) else BG_CAP
    except Exception as e:
        _log_error(path, e)
        return BG_FAILED
    finally:
        state.release_lock(hdir)


def background(transcript_path: Path, mode: str, claude_bin: str = "claude") -> bool:
    """background_status 가 BG_OK 면 True."""
    return background_status(transcript_path, mode, claude_bin) == BG_OK


def spawn_background(transcript_path: Path, mode: str) -> None:
    subprocess.Popen(
        ["uv", "run", "--script", str(Path(__file__).resolve()), "background", str(transcript_path), "--mode", mode],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "HARNESS_RETRO": "1"},
    )


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="judge.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("digest")
    p.add_argument("transcript", type=Path)
    p = sub.add_parser("merge")
    p.add_argument("transcript", type=Path)
    p.add_argument("--mode", choices=MODES, required=True)
    sub.add_parser("rescore")
    p = sub.add_parser("background")
    p.add_argument("transcript", type=Path)
    p.add_argument("--mode", choices=MODES, required=True)
    args = parser.parse_args(argv)
    if args.cmd == "digest":
        print(digest(args.transcript, pending_signals(state.harness_dir(args.transcript))), end="")
    elif args.cmd == "merge":
        print(json.dumps(merge(args.transcript, json.load(sys.stdin), args.mode), ensure_ascii=False, indent=2))
    elif args.cmd == "background":
        sys.exit(background_status(args.transcript, args.mode))
    else:
        print(rescore())


if __name__ == "__main__":
    main()
