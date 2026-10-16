#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""SessionStart·SessionEnd 훅 진입점. 어떤 경우에도 종료 코드 0 이고 오류는 hook-errors.log 에 남긴다."""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _guarded(data: dict) -> bool:
    return os.environ.get("HARNESS_RETRO") == "1" or bool(data.get("agent_id")) or bool(os.environ.get("CLAUDE_CODE_CHILD_SESSION"))


def run(event: str, raw: str) -> None:
    import judge
    import report
    import state

    if event not in ("session-start", "session-end"):
        raise ValueError(f"알 수 없는 이벤트: {event}")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("훅 입력이 JSON 객체가 아니다")
    transcript_path = data.get("transcript_path")
    if _guarded(data):
        if transcript_path and Path(transcript_path).is_file():
            (state.harness_dir(Path(transcript_path)) / "skip").touch()
        return
    if event == "session-end":
        if transcript_path:
            judge.spawn_background(Path(transcript_path), "session-end")
        return
    if data.get("source") not in ("startup", "resume"):
        return
    try:
        report.regressions()
    except Exception as e:
        _log_error(f"{event} regressions", e)
    try:
        room = max(0, state.load_config()["max_background_judges_per_day"] - judge.runs_today())
        for t in report.sweep(datetime.now().astimezone().isoformat(), exclude_session=data.get("session_id"))[:room]:
            judge.spawn_background(t, "sweep")
    except Exception as e:
        _log_error(f"{event} sweep", e)
    if os.environ.get("HERDR_PW_WORKER"):
        return
    pairs = report.pending(state.load_config()["queue_limit"])
    errors_total, errors_new = report.hook_errors()
    text = report.session_start_text(pairs, errors_new)
    if not text:
        return
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}, ensure_ascii=False))
    report.mark_reported(pairs)
    report.mark_hook_errors_seen(errors_total)


def _log_error(event: str, error: BaseException) -> None:
    try:
        env = os.environ.get("CLAUDE_PLUGIN_DATA")
        d = Path(env) if env else Path.home() / ".claude/plugins/data/harness-loop-local"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "hook-errors.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().astimezone().isoformat()} hook {event}: {type(error).__name__}: {' '.join(str(error).split())}\n")
    except Exception:
        pass


def main(argv=None) -> None:
    argv = sys.argv if argv is None else argv
    event = argv[1] if len(argv) > 1 else ""
    try:
        run(event, sys.stdin.read())
    except Exception as e:
        _log_error(event or "unknown", e)
    sys.exit(0)


if __name__ == "__main__":
    main()
