#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""SessionStart 보고, sweep 대상 선별, 재발 감지, 결정 기록과 action item 조회."""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import judge
import state

REPORTABLE = {"propose", "regressed"}
DECISIONS = ("approved", "rejected", "applied", "edit-score")
OPEN_SESSION_SEC = 600


def _now() -> datetime:
    return datetime.now().astimezone()


def _aware(text: str) -> datetime:
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.astimezone()


def _transcript_of(hdir: Path) -> Path:
    return hdir.parent.parent / f"{hdir.parent.name}.jsonl"


def _line_count(path: Path) -> int:
    with open(path, "rb") as f:
        return sum(1 for _ in f)


def sweep(now_iso: str, exclude_session: str | None = None) -> list[Path]:
    """판정이 밀린 세션 기록. 최근 OPEN_SESSION_SEC 안에 바뀐 기록(아직 열린 세션)과 exclude_session 은 뺀다."""
    installed = _aware((state.data_dir() / "installed_at").read_text().strip()).timestamp()
    now = _aware(now_iso).timestamp()
    out = []
    for t in sorted(state.projects_root().glob("*/*.jsonl")):
        mtime = t.stat().st_mtime
        if not installed < mtime <= now - OPEN_SESSION_SEC or t.stem == exclude_session:
            continue
        hdir = t.parent / t.stem / "harness"
        if (hdir / "skip").exists():
            continue
        if judge.pending_signals(hdir):
            out.append(t)
            continue
        cursor = hdir / "cursor"
        if cursor.exists() and cursor.stat().st_mtime >= mtime:
            continue
        if state.read_cursor(hdir) < _line_count(t):
            out.append(t)
    return out


def _session_start(t: Path) -> datetime:
    try:
        with open(t, encoding="utf-8") as f:
            for raw in f:
                try:
                    ts = json.loads(raw).get("timestamp")
                except (ValueError, AttributeError):
                    continue
                if isinstance(ts, str):
                    return _aware(ts)
    except (OSError, ValueError):
        pass
    return datetime.fromtimestamp(t.stat().st_mtime).astimezone()


def _has_sig(hdir: Path, sig: str) -> bool:
    return sig in state.load_items(hdir) or any(s.get("sig") == sig for s in judge._read_signals(hdir))


def regressions() -> list[dict]:
    decided = {sig: _aware(d["at"]) for sig, d in state.load_decisions().items() if d["decision"] in ("approved", "applied")}
    if not decided:
        return []
    hdirs = state.all_harness_dirs()
    sources = {}
    for hdir in hdirs:
        for sig, it in state.load_items(hdir).items():
            if sig in decided and it.get("status") in ("approved", "applied"):
                sources.setdefault(sig, it)
    created = []
    for hdir in hdirs:
        t = _transcript_of(hdir)
        if not t.exists():
            continue
        started = _session_start(t)
        items = state.load_items(hdir)
        dirty = False
        for sig, at in decided.items():
            if started <= at or items.get(sig, {}).get("status") == "regressed" or not _has_sig(hdir, sig):
                continue
            src = sources.get(sig, {})
            new = dict(items.get(sig, {}))
            new.update({k: src[k] for k in ("condition", "proposal", "target", "scores", "sessions") if k in src})
            new.setdefault("condition", sig.split(":", 1)[0])
            new.setdefault("score", src.get("score", 0))
            new.update(sig=sig, status="regressed", regressed_after=at.isoformat())
            new.pop("reported_at", None)
            items[sig] = new
            created.append(new)
            dirty = True
        if dirty:
            state.save_items(hdir, items)
    return created


def pending(limit: int) -> list[tuple[Path, dict]]:
    out = []
    for hdir in state.all_harness_dirs():
        for it in state.load_items(hdir).values():
            if it.get("status") in REPORTABLE and not it.get("reported_at"):
                out.append((hdir, it))
    out.sort(key=lambda p: p[1].get("score") or 0, reverse=True)
    return out[:limit]


def _errors_log() -> Path:
    return state.data_dir() / "hook-errors.log"


def _seen_file() -> Path:
    return state.data_dir() / "hook-errors.seen"


def _error_lines() -> int:
    log = _errors_log()
    return _line_count(log) if log.exists() else 0


def hook_errors() -> tuple[int, int]:
    """(hook-errors.log 전체 줄 수, 마지막 보고 이후 새 줄 수)."""
    try:
        seen = int(_seen_file().read_text().strip())
    except (OSError, ValueError):
        seen = 0
    total = _error_lines()
    return total, total - seen if total >= seen else total


def mark_hook_errors_seen(total: int) -> None:
    _seen_file().write_text(str(total))


def session_start_text(items: list[tuple[Path, dict]], hook_errors: int) -> str:
    error_line = f"훅 오류 {hook_errors}건: {_errors_log()}"
    if not items:
        return f"harness-loop: {error_line}" if hook_errors > 0 else ""
    lines = [f"harness-loop: 검토 대기 action item {len(items)}건 — /harness-loop:retro review"]
    for hdir, it in items:
        t = _transcript_of(hdir)
        stamp = (t if t.exists() else hdir).stat().st_mtime
        date = datetime.fromtimestamp(stamp).astimezone().date().isoformat()
        first = (str(it.get("proposal") or "").splitlines() or [""])[0][:80]
        lines.append(f"- [{it.get('condition')}] {first} ({date} {hdir.parent.parent.name})")
    if hook_errors > 0:
        lines.append(f"- {error_line}")
    return "\n".join(lines)


def mark_reported(pairs) -> None:
    at = _now().isoformat()
    by_dir: dict[Path, set[str]] = {}
    for hdir, it in pairs:
        by_dir.setdefault(Path(hdir), set()).add(it["sig"])
    for hdir, sigs in by_dir.items():
        items = state.load_items(hdir)
        for sig in sigs:
            if sig in items:
                items[sig]["reported_at"] = at
        state.save_items(hdir, items)


def decide(sig: str, decision: str, session: str | None = None, impact: int | None = None, actionability: int | None = None) -> dict:
    if decision not in DECISIONS:
        raise ValueError(f"decision 은 {'|'.join(DECISIONS)} 중 하나다: {decision}")
    hdirs = [h for h in state.all_harness_dirs() if (session is None or h.parent.name == session) and sig in state.load_items(h)]
    if not hdirs:
        raise ValueError(f"sig 가 없다: {sig}" + (f" (세션 {session})" if session else ""))
    first = state.load_items(hdirs[0])[sig]
    if decision == "edit-score":
        for hdir in hdirs:
            scores = state.load_items(hdir)[sig].get("scores") or {}
            if (impact is None and not isinstance(scores.get("impact"), int)) or (actionability is None and not isinstance(scores.get("actionability"), int)):
                raise ValueError(f"점수가 없는 항목이라 impact·actionability 를 모두 줘야 한다: {sig} (세션 {hdir.parent.name})")
    state.append_decision(sig, decision, target=(first.get("target") or {}).get("path"))
    cfg = state.load_config()
    updated = []
    for hdir in hdirs:
        items = state.load_items(hdir)
        it = items[sig]
        if decision == "edit-score":
            scores = dict(it.get("scores") or {})
            if impact is not None:
                scores["impact"] = impact
            if actionability is not None:
                scores["actionability"] = actionability
            it["scores"] = scores
            total, status = judge.score({**it, "sessions": it.get("sessions", 1), "impact": scores["impact"], "actionability": scores["actionability"]}, cfg)
            it["score"] = total
            if it.get("status") not in judge.KEPT:
                it["status"] = status
        else:
            it["status"] = decision
        state.save_items(hdir, items)
        updated.append({"session": hdir.parent.name, **it})
    return {"sig": sig, "decision": decision, "items": updated}


def list_items(scope: str) -> list[dict]:
    if scope not in ("pending", "all") and not scope.startswith("session:"):
        raise ValueError(f"scope 는 pending|session:<id>|all 중 하나다: {scope}")
    out = []
    for hdir in state.all_harness_dirs():
        sid = hdir.parent.name
        if scope.startswith("session:") and sid != scope.split(":", 1)[1]:
            continue
        for it in state.load_items(hdir).values():
            if scope == "pending" and it.get("status") not in REPORTABLE:
                continue
            out.append({"session": sid, "project": hdir.parent.parent.name, **it})
    out.sort(key=lambda i: i.get("score") or 0, reverse=True)
    return out


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="report.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("where")
    p = sub.add_parser("list")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--session")
    g.add_argument("--all", action="store_true")
    p = sub.add_parser("decide")
    p.add_argument("sig")
    p.add_argument("decision", choices=DECISIONS)
    p.add_argument("--session")
    p.add_argument("--impact", type=int, choices=(1, 2, 3))
    p.add_argument("--actionability", type=int, choices=(1, 2, 3))
    args = parser.parse_args(argv)
    if args.cmd == "where":
        sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
        t = state.find_transcript(sid) if sid else None
        if t is None:
            print(f"세션 기록을 찾지 못했다: CLAUDE_CODE_SESSION_ID={sid or ''}", file=sys.stderr)
            sys.exit(2)
        print(t)
        sys.exit(0)
    try:
        if args.cmd == "list":
            scope = f"session:{args.session}" if args.session else "all" if args.all else "pending"
            result = list_items(scope)
        else:
            result = decide(args.sig, args.decision, args.session, args.impact, args.actionability)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
