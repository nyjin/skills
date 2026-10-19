#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""SessionStart 보고, sweep·backfill 대상 선별, 재발 감지, 결정 기록과 action item 조회."""
import argparse
import itertools
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
PROJECT_SCAN_LINES = 50


def _now() -> datetime:
    return datetime.now().astimezone()


def _transcript_of(hdir: Path) -> Path:
    t = hdir.parent.parent / f"{hdir.parent.name}.jsonl"
    return t if t.exists() else state.find_transcript(hdir.parent.name) or t


def _line_count(path: Path) -> int:
    with open(path, "rb") as f:
        return sum(1 for _ in f)


def sweep(now_iso: str, exclude_session: str | None = None) -> list[Path]:
    """판정이 밀린 세션 기록. 최근 OPEN_SESSION_SEC 안에 바뀐 기록(아직 열린 세션)과 exclude_session 은 뺀다."""
    installed = state.aware((state.data_dir() / "installed_at").read_text().strip()).timestamp()
    now = state.aware(now_iso).timestamp()
    out = []
    for t in sorted(state.projects_root().glob("*/*.jsonl")):
        mtime = t.stat().st_mtime
        if not installed < mtime <= now - OPEN_SESSION_SEC or t.stem == exclude_session:
            continue
        hdir = state.harness_path(t)
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


def backfill(now_iso: str, days: int | None = None) -> list[Path]:
    """설치 전에 바뀐 세션 기록, 최신순. days 가 있으면 최근 days 일 안의 것만. skip 표시 세션은 뺀다."""
    installed = state.aware((state.data_dir() / "installed_at").read_text().strip()).timestamp()
    since = state.aware(now_iso).timestamp() - days * 86400 if days is not None else None
    found = []
    for t in state.projects_root().glob("*/*.jsonl"):
        mtime = t.stat().st_mtime
        if mtime > installed or (since is not None and mtime < since) or (state.harness_path(t) / "skip").exists():
            continue
        found.append((mtime, t))
    return [t for _, t in sorted(found, key=lambda p: p[0], reverse=True)]


def _has_sig(hdir: Path, key: str) -> bool:
    return any(state.sig_key(s) == key for s in state.load_items(hdir)) or any(state.sig_key(str(s.get("sig"))) == key for s in judge._read_signals(hdir))


def regressions() -> list[dict]:
    """승인·반영 뒤 시작한 세션에서 sig(C2·C7 쌍둥이 포함)가 다시 나오면 regressed 로 만든다. 상태 규칙은 judge.regression 과 같다(plugin_cache 대상은 sig 당 한 번만 regressed, 그 뒤 재발은 watch)."""
    ctx = judge.regression_context()
    if not ctx[0]:
        return []
    created = []
    for hdir in state.all_harness_dirs():
        t = _transcript_of(hdir)
        if not t.exists():
            continue
        started = state.session_start(t)
        if not _regress(hdir, state.load_items(hdir), started, ctx):
            continue
        if not state.acquire_lock(hdir):
            _log_locked("regressions", hdir)
            continue
        try:
            items = state.load_items(hdir)
            changes = _regress(hdir, items, started, ctx)
            if changes:
                state.save_items(hdir, items)
        finally:
            state.release_lock(hdir)
        for new in changes:
            if new["status"] == "regressed":
                created.append(new)
                ctx[2].add(state.sig_key(new["sig"]))
    return created


def _regress(hdir: Path, items: dict, started: datetime, ctx: tuple[dict, dict, set]) -> list[dict]:
    """items 를 제자리에서 고치고 새로 쓴 항목들을 돌려준다. 세션에 쌍둥이 sig 항목이 있으면 그 sig 로 쓴다."""
    decided, sources, _ = ctx
    changes = []
    for key in decided:
        src = sources.get(key, {})
        sig = state.find_twin(items, key) or src.get("sig") or key
        result = judge.regression(sig, items.get(sig, {}).get("status"), started, ctx)
        if not result or not _has_sig(hdir, key):
            continue
        status, at = result
        new = dict(items.get(sig, {}))
        copied = ("proposal", "target", "scores", "sessions") if sig in items else ("condition", "proposal", "target", "scores", "sessions")
        new.update({k: src[k] for k in copied if k in src})
        new.setdefault("condition", sig.split(":", 1)[0])
        new.setdefault("score", src.get("score", 0))
        new.update(sig=sig, status=status, regressed_after=at.isoformat())
        new.pop("reported_at", None)
        items[sig] = new
        changes.append(new)
    return changes


def _log_locked(what: str, hdir: Path) -> None:
    """락 경합은 훅 오류가 아니라 hook-events.log 에 한 줄 남긴다(SessionStart 의 훅 오류 건수에 들어가지 않는다)."""
    try:
        with open(state.data_dir() / "hook-events.log", "a", encoding="utf-8") as f:
            f.write(f"{_now().isoformat()} report {what}: 세션 {hdir.parent.name} 이 잠겨 있어 건너뜀 ({hdir})\n")
    except OSError:
        pass


def pending(limit: int) -> list[tuple[Path, dict]]:
    """보고하지 않은 propose·regressed 항목. 다른 세션에서 이미 보고한 sig 는 regressed 가 아니면 다시 내지 않는다. C2·C7 쌍둥이 sig 는 sig_key 로 한 sig 로 본다."""
    loaded = [(hdir, state.load_items(hdir)) for hdir in state.all_harness_dirs()]
    reported = {state.sig_key(sig) for _, items in loaded for sig, it in items.items() if it.get("reported_at")}
    out = []
    for hdir, items in loaded:
        for sig, it in items.items():
            if it.get("status") in REPORTABLE and not it.get("reported_at") and (it.get("status") == "regressed" or state.sig_key(sig) not in reported):
                out.append((hdir, it))
    out.sort(key=lambda p: p[1].get("score") or 0, reverse=True)
    seen = set()
    out = [p for p in out if not (state.sig_key(p[1]["sig"]) in seen or seen.add(state.sig_key(p[1]["sig"])))]
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
        mark = "·반영 실패" if it.get("status") == "regressed" else ""
        lines.append(f"- [{it.get('condition')}{mark}] {first} ({date} {_project_label(t, hdir)})")
    if hook_errors > 0:
        lines.append(f"- {error_line}")
    return "\n".join(lines)


def _project_label(t: Path, hdir: Path) -> str:
    """알림에 보일 프로젝트: 기록 앞 PROJECT_SCAN_LINES 줄에서 처음 나온 `cwd` 의 마지막 두 경로 요소, 없으면 프로젝트 디렉터리 이름."""
    try:
        with open(t, encoding="utf-8") as f:
            for raw in itertools.islice(f, PROJECT_SCAN_LINES):
                try:
                    cwd = json.loads(raw).get("cwd")
                except (ValueError, AttributeError):
                    continue
                if isinstance(cwd, str) and cwd.strip("/"):
                    return "/".join(Path(cwd).parts[-2:]).lstrip("/")
    except OSError:
        pass
    return hdir.parent.parent.name


def mark_reported(pairs) -> None:
    at = _now().isoformat()
    by_dir: dict[Path, set[str]] = {}
    for hdir, it in pairs:
        by_dir.setdefault(Path(hdir), set()).add(it["sig"])
    for hdir, sigs in by_dir.items():
        if not state.acquire_lock(hdir):
            _log_locked("mark_reported", hdir)
            continue
        try:
            items = state.load_items(hdir)
            for sig in sigs:
                if sig in items:
                    items[sig]["reported_at"] = at
            state.save_items(hdir, items)
        finally:
            state.release_lock(hdir)


def _lock_all(hdirs: list[Path]) -> None:
    """hdirs 의 락을 모두 잡는다(state.lock_all). 못 잡으면 ValueError."""
    waiting = state.lock_all(hdirs)
    if waiting:
        names = ", ".join(h.parent.name for h in waiting)
        raise ValueError(f"세션 {names} 이 잠겨 있어(판정 중일 수 있다) 결정을 기록하지 않았다. 잠시 뒤 다시 실행한다.")


def decide(sig: str, decision: str, session: str | None = None, impact: int | None = None, actionability: int | None = None) -> dict:
    if decision not in DECISIONS:
        raise ValueError(f"decision 은 {'|'.join(DECISIONS)} 중 하나다: {decision}")
    if decision == "edit-score" and session is not None:
        raise ValueError("edit-score 는 sig 단위로 모든 세션에 적용되므로 --session 과 함께 쓸 수 없다.")
    hdirs = [h for h in state.all_harness_dirs() if (session is None or h.parent.name == session) and _twins(state.load_items(h), sig)]
    if not hdirs:
        raise ValueError(f"sig 가 없다: {sig}" + (f" (세션 {session})" if session else ""))
    _lock_all(hdirs)
    try:
        return _decide_locked(sig, decision, hdirs, impact, actionability)
    finally:
        for hdir in hdirs:
            state.release_lock(hdir)


def _twins(items: dict, sig: str) -> list[str]:
    """items 에서 sig 와 sig_key 가 같은 sig 들(C2·C7 쌍둥이 포함)."""
    key = state.sig_key(sig)
    return [s for s in items if state.sig_key(s) == key]


def _decide_locked(sig: str, decision: str, hdirs: list[Path], impact: int | None, actionability: int | None) -> dict:
    loaded = {hdir: state.load_items(hdir) for hdir in hdirs}
    matched = {hdir: _twins(items, sig) for hdir, items in loaded.items()}
    for hdir, sigs in matched.items():
        if not sigs:
            raise ValueError(f"sig 가 없다: {sig} (세션 {hdir.parent.name})")
    if decision == "edit-score":
        for hdir, items in loaded.items():
            for local in matched[hdir]:
                scores = items[local].get("scores") or {}
                if (impact is None and not isinstance(scores.get("impact"), int)) or (actionability is None and not isinstance(scores.get("actionability"), int)):
                    raise ValueError(f"점수가 없는 항목이라 impact·actionability 를 모두 줘야 한다: {local} (세션 {hdir.parent.name})")
    first = loaded[hdirs[0]][matched[hdirs[0]][0]]
    cfg = state.load_config()
    updated = []
    for hdir, items in loaded.items():
        for local in matched[hdir]:
            it = items[local]
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
            updated.append({"session": hdir.parent.name, **it})
    for hdir, items in loaded.items():
        state.save_items(hdir, items)
    state.append_decision(sig, decision, target=(first.get("target") or {}).get("path"), impact=impact, actionability=actionability)
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
    p = sub.add_parser("backfill-list")
    p.add_argument("--days", type=int)
    p = sub.add_parser("list")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--session")
    g.add_argument("--all", action="store_true")
    p.add_argument("--mark-reported", action="store_true")
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
    if args.cmd == "backfill-list":
        for t in backfill(_now().isoformat(), args.days):
            print(t)
        return
    try:
        if args.cmd == "list":
            scope = f"session:{args.session}" if args.session else "all" if args.all else "pending"
            result = list_items(scope)
            if args.mark_reported:
                mark_reported([(state.projects_root() / i["project"] / i["session"] / "harness", i) for i in result if i.get("status") in REPORTABLE])
        else:
            result = decide(args.sig, args.decision, args.session, args.impact, args.actionability)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
