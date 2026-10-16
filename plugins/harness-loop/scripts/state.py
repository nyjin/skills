#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""harness-loop 상태 저장소: 공용 데이터 디렉터리, 세션별 harness 디렉터리, 락, 결정 기록."""
import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

import yaml

DEFAULTS = Path(__file__).resolve().parents[1] / "defaults"

DEFAULT_CONFIG = {
    "weights": {"recurrence": 0.4, "impact": 0.4, "actionability": 0.2},
    "propose": 2.4,
    "watch": 1.6,
    "global_bump": 0.3,
    "queue_limit": 3,
    "max_background_judges_per_day": 10,
    "eval_markers": ["트리거 측정 중"],
}


def data_dir() -> Path:
    env = os.environ.get("CLAUDE_PLUGIN_DATA")
    d = Path(env) if env else Path.home() / ".claude/plugins/data/harness-loop-local"
    d.mkdir(parents=True, exist_ok=True)
    for name in ("scoring.yaml", "rubric.md"):
        if not (d / name).exists():
            shutil.copy(DEFAULTS / name, d / name)
    if not (d / "installed_at").exists():
        (d / "installed_at").write_text(datetime.now().astimezone().isoformat())
    return d


def load_config() -> dict:
    loaded = yaml.safe_load((data_dir() / "scoring.yaml").read_text()) or {}
    config = dict(DEFAULT_CONFIG)
    config.update(loaded)
    config["weights"] = {**DEFAULT_CONFIG["weights"], **(loaded.get("weights") or {})}
    return config


def projects_root() -> Path:
    env = os.environ.get("HARNESS_PROJECTS_ROOT")
    return Path(env) if env else Path.home() / ".claude/projects"


def harness_dir(transcript: Path) -> Path:
    transcript = Path(transcript)
    d = transcript.parent / transcript.stem / "harness"
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_transcript(session_id: str) -> Path | None:
    return next(iter(projects_root().glob(f"*/{session_id}.jsonl")), None)


def all_harness_dirs() -> list[Path]:
    return sorted(p for p in projects_root().glob("*/*/harness") if p.is_dir())


def read_cursor(hdir) -> int:
    try:
        return int((Path(hdir) / "cursor").read_text().strip())
    except (OSError, ValueError):
        return 0


def write_cursor(hdir, line: int) -> None:
    _atomic_write(Path(hdir) / "cursor", str(line))


def acquire_lock(hdir, stale_sec=600) -> bool:
    lock = Path(hdir) / "lock"
    for _ in range(2):
        try:
            os.mkdir(lock)
            return True
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime <= stale_sec:
                    return False
                os.rmdir(lock)
            except FileNotFoundError:
                continue
            except OSError:
                return False
    return False


def release_lock(hdir) -> None:
    try:
        os.rmdir(Path(hdir) / "lock")
    except OSError:
        pass


def load_items(hdir) -> dict[str, dict]:
    try:
        return json.loads((Path(hdir) / "action-items.json").read_text())
    except (OSError, ValueError):
        return {}


def save_items(hdir, items) -> None:
    _atomic_write(Path(hdir) / "action-items.json", json.dumps(items, ensure_ascii=False, indent=2))


def append_decision(sig, decision, target=None, note=None) -> None:
    record = {
        "sig": sig,
        "decision": decision,
        "at": datetime.now().astimezone().isoformat(),
        "target": target,
        "note": note,
    }
    with open(data_dir() / "decisions.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_decisions() -> dict[str, dict]:
    path = data_dir() / "decisions.jsonl"
    result = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("decision") == "edit-score":
            continue
        result[r["sig"]] = {"decision": r["decision"], "at": r["at"], "target": r.get("target")}
    return result


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
