#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""harness-loop 상태 저장소: 공용 데이터 디렉터리, 세션별 harness 디렉터리, 락, 결정 기록."""
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

DEFAULTS = Path(__file__).resolve().parents[1] / "defaults"
LOCK_TRIES = 10
LOCK_SLEEP = 0.5
LOCKED_EXIT = 3

DEFAULT_CONFIG = {
    "weights": {"recurrence": 0.4, "impact": 0.4, "actionability": 0.2},
    "propose": 2.2,
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
    """scoring.yaml 을 읽어 기본값 위에 덮는다. 파싱할 수 없거나 매핑이 아니면 기본값을 쓰고 hook-errors.log 에 한 줄 남긴다."""
    d = data_dir()
    try:
        loaded = yaml.safe_load((d / "scoring.yaml").read_text()) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"매핑이 아니다: {type(loaded).__name__}")
    except (OSError, yaml.YAMLError, ValueError) as e:
        with open(d / "hook-errors.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().astimezone().isoformat()} config scoring.yaml 기본값 사용: {type(e).__name__}: {' '.join(str(e).split())[:200]}\n")
        loaded = {}
    config = dict(DEFAULT_CONFIG)
    config.update(loaded)
    config["weights"] = {**DEFAULT_CONFIG["weights"], **(loaded.get("weights") or {})}
    return config


def projects_root() -> Path:
    env = os.environ.get("HARNESS_PROJECTS_ROOT")
    return Path(env) if env else Path.home() / ".claude/projects"


def harness_path(transcript: Path) -> Path:
    """세션 기록의 harness 디렉터리 경로. 만들지 않는다."""
    transcript = Path(transcript)
    return projects_root() / transcript.parent.name / transcript.stem / "harness"


def harness_dir(transcript: Path) -> Path:
    d = harness_path(transcript)
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_transcript(session_id: str) -> Path | None:
    found = next(iter(projects_root().glob(f"*/{session_id}.jsonl")), None)
    if found is None and os.environ.get("HARNESS_PROJECTS_ROOT"):
        found = next(iter((Path.home() / ".claude/projects").glob(f"*/{session_id}.jsonl")), None)
    return found


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


def lock_all(hdirs) -> list[Path]:
    """hdirs 의 락을 모두 잡는다. LOCK_TRIES 번 안에 못 잡으면 잡은 것을 풀고 못 잡은 디렉터리를 돌려준다. 다 잡으면 []."""
    held, waiting = [], [Path(h) for h in hdirs]
    for attempt in range(LOCK_TRIES):
        for hdir in list(waiting):
            if acquire_lock(hdir):
                held.append(hdir)
                waiting.remove(hdir)
        if not waiting:
            return []
        if attempt < LOCK_TRIES - 1:
            time.sleep(LOCK_SLEEP)
    for hdir in held:
        release_lock(hdir)
    return waiting


def locked_cli(hdir, what: str):
    """CLI 진입점용: 세션 락을 lock_all 로 잡는다. 못 잡으면 stderr 에 알리고 LOCKED_EXIT 로 끝낸다. 잡으면 hdir 를 돌려준다(호출 쪽이 finally 에서 release_lock)."""
    if lock_all([hdir]):
        print(f"세션 {Path(hdir).parent.name} 이 잠겨 있어(백그라운드 판정 중일 수 있다) {what}. 잠시 뒤 다시 실행한다.", file=sys.stderr)
        sys.exit(LOCKED_EXIT)
    return hdir


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


def append_decision(sig, decision, target=None, note=None, impact=None, actionability=None) -> None:
    record = {
        "sig": sig,
        "decision": decision,
        "at": datetime.now().astimezone().isoformat(),
        "target": target,
        "note": note,
    }
    for key, value in (("impact", impact), ("actionability", actionability)):
        if value is not None:
            record[key] = value
    with open(data_dir() / "decisions.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _decision_records() -> list[dict]:
    path = data_dir() / "decisions.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and all(r.get(k) for k in ("sig", "decision", "at")):
            out.append(r)
    return out


def load_score_overrides() -> dict[str, dict]:
    """edit-score 기록에서 sig_key 별로(C2·C7 쌍둥이 합침) 필드마다 마지막에 준 impact·actionability."""
    result = {}
    for r in _decision_records():
        if r["decision"] != "edit-score":
            continue
        for key in ("impact", "actionability"):
            v = r.get(key)
            if isinstance(v, int) and not isinstance(v, bool):
                result.setdefault(sig_key(r["sig"]), {})[key] = v
    return result


def load_decisions() -> dict[str, dict]:
    result = {}
    for r in _decision_records():
        if r.get("decision") == "edit-score":
            continue
        result[r["sig"]] = {"decision": r["decision"], "at": r["at"], "target": r.get("target")}
    return result


def aware(text: str) -> datetime:
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.astimezone()


def session_start(t: Path) -> datetime:
    """세션 시작 시각: 기록의 첫 timestamp, 없으면 파일 mtime."""
    try:
        with open(t, encoding="utf-8") as f:
            for raw in f:
                try:
                    ts = json.loads(raw).get("timestamp")
                except (ValueError, AttributeError):
                    continue
                if isinstance(ts, str):
                    return aware(ts)
    except (OSError, ValueError):
        pass
    return datetime.fromtimestamp(Path(t).stat().st_mtime).astimezone()


def sig_key(sig: str) -> str:
    """재발·결정 대조용 키. `C2:x` 와 `C7:x` 는 같은 문제라 `C7:` 을 `C2:` 로 접는다."""
    return "C2:" + sig[3:] if sig.startswith("C7:") else sig


def load_decisions_by_key() -> dict[str, dict]:
    """sig_key 별 마지막 결정(edit-score 제외). C2·C7 쌍둥이 sig 의 결정을 합쳐 가장 나중 것을 쓴다. `sig` 에 원래 sig 를 둔다."""
    result = {}
    for r in _decision_records():
        if r["decision"] != "edit-score":
            result[sig_key(r["sig"])] = {"decision": r["decision"], "at": r["at"], "target": r.get("target"), "sig": r["sig"]}
    return result


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
