#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""훅 진입점. 근거 이벤트는 evidence 로 기록하고 SessionStart 는 하루 한 번 알림을 낸다. 종료 코드는 항상 0."""
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evidence
import paths

NOTICE = "harness-loop: 판정하지 않은 근거 {n}건이 있습니다. /harness-loop:retro 로 확인하세요."


def _lines(path: Path) -> int:
    try:
        return path.read_bytes().count(b"\n")
    except OSError:
        return 0


def pending_count() -> int:
    total = 0
    for hdir in paths.all_harness_dirs():
        if (hdir / "skip").exists():
            continue
        try:
            cursor = int((hdir / "evidence.cursor").read_text().strip())
        except (OSError, ValueError):
            cursor = 0
        total += max(0, _lines(hdir / "evidence.jsonl") - cursor)
    return total


def session_start_text() -> str | None:
    d = paths.data_dir()
    today = date.today().isoformat()
    day_file = d / "notice-day"
    try:
        if day_file.read_text().strip() == today:
            return None
    except OSError:
        pass
    errors = _lines(d / "hook-errors.log")
    try:
        seen = int((d / "hook-errors.seen").read_text().strip())
    except (OSError, ValueError):
        seen = 0
    new_errors = errors - seen
    n = pending_count()
    if n <= 0 and new_errors <= 0:
        return None
    text = NOTICE.format(n=n)
    if new_errors > 0:
        text += f" (훅 오류 {new_errors}건, hook-errors.log)"
        paths.atomic_write(d / "hook-errors.seen", str(errors))
    paths.atomic_write(day_file, today)
    return text


def run(raw: str) -> str | None:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("훅 입력이 JSON 객체가 아니다")
    if os.environ.get("HARNESS_RETRO") == "1" or data.get("agent_id"):
        return None
    event = data.get("hook_event_name")
    if event == "SessionStart":
        if os.environ.get("HERDR_PW_WORKER"):
            return None
        text = session_start_text()
        if not text:
            return None
        return json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}, ensure_ascii=False)
    evidence.handle(event, data)
    return None


def main() -> None:
    try:
        out = run(sys.stdin.read())
        if out:
            print(out)
    except Exception as e:
        paths.log_error("hook", e)
    sys.exit(0)


if __name__ == "__main__":
    main()
