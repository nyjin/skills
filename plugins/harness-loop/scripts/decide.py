#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""결정 기록(decisions.jsonl)과 그 라벨·수락률, replay 보관."""
import hashlib
import json
import os
import re
from pathlib import Path

import groups
import observe
import paths

CHOICES = ("accept", "reject")
KINDS = ("not-defect", "trivial", "wont-fix")


def _file() -> Path:
    return paths.data_dir() / "decisions.jsonl"


def _rubric() -> tuple[str, int]:
    text = (paths.data_dir() / "rubric.md").read_text(encoding="utf-8")
    m = re.match(r"<!--\s*rubric-version:\s*(\d+)\s*-->", text)
    return text, int(m.group(1)) if m else 1


def _target(group: dict) -> str:
    return os.path.expanduser(group["target_path"])


def record(group_id: str, choice: str, *, kind=None, reason=None) -> dict:
    if choice not in CHOICES:
        raise ValueError(f"choice 는 {'·'.join(CHOICES)} 중 하나여야 합니다: {choice}")
    if choice == "reject" and kind not in KINDS:
        raise ValueError(f"reject 는 kind({'·'.join(KINDS)})가 필요합니다")
    group = groups.load().get(group_id)
    if group is None:
        raise ValueError(f"알 수 없는 묶음: {group_id}")
    rec = {"group": group_id, "decision": choice, "kind": kind, "note": reason,
           "target_type": group["target_type"], "at": paths.now(), "rubric_version": _rubric()[1]}
    archive(group_id)
    paths.append_line(_file(), rec)
    return rec


def last_decisions() -> dict[str, dict]:
    out: dict[str, dict] = {}
    try:
        lines = _file().read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for ln in lines:
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("group"):
            out[rec["group"]] = rec
    return out


def _label(rec: dict) -> str:
    if rec["decision"] == "accept":
        return "positive"
    return "excluded" if rec.get("kind") == "wont-fix" else "negative"


def labels() -> dict[str, dict]:
    return {g: {"label": _label(r), "kind": r.get("kind"), "note": r.get("note"), "at": r.get("at")}
            for g, r in last_decisions().items()}


def split(group_id: str) -> str:
    return "test" if hashlib.sha1(group_id.encode("utf-8")).digest()[0] < 128 else "train"


def _tally(recs: list[dict]) -> dict:
    acc = sum(r["decision"] == "accept" for r in recs)
    rej = len(recs) - acc
    return {"accepted": acc, "rejected": rej, "rate": acc / (acc + rej) if acc + rej else None}


def metrics() -> dict:
    """수락률 = 수락 ÷ (수락 + 거절). wont-fix 거절은 분모에서 뺀다."""
    recs = [r for r in last_decisions().values() if _label(r) != "excluded"]
    by_target: dict[str, list] = {}
    by_version: dict[str, list] = {}
    for r in recs:
        by_target.setdefault(r.get("target_type"), []).append(r)
        by_version.setdefault(str(r.get("rubric_version")), []).append(r)
    return {"overall": _tally(recs),
            "by_target": {k: _tally(v) for k, v in by_target.items()},
            "by_rubric_version": {k: _tally(v) for k, v in by_version.items()}}


def _evidence() -> dict[str, dict]:
    out = {}
    for hdir in paths.all_harness_dirs():
        try:
            lines = (hdir / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if isinstance(r, dict) and r.get("id"):
                out[r["id"]] = r
    return out


def _excerpt(ev: dict | None) -> str:
    return observe.excerpt(ev.get("transcript", ""), ev.get("pos") or 0, ev["kind"]) if ev else ""


def archive(group_id: str) -> Path:
    group = groups.load()[group_id]
    by_obs = {o.get("evidence_id"): o for o in observe.observations()}
    evidence = _evidence()
    entries = []
    for eid in group["observations"]:
        ev = evidence.get(eid)
        entries.append({"evidence_id": eid, "observation": by_obs.get(eid), "evidence": ev, "excerpt": _excerpt(ev)})
    target = _target(group)
    try:
        text = Path(target).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = None
    rubric_text, version = _rubric()
    out = paths.data_dir() / "replay" / group_id / "input.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    paths.atomic_write(out, json.dumps(
        {"group": group, "observations": entries, "target_file": {"path": target, "text": text},
         "rubric": {"version": version, "text": rubric_text}}, ensure_ascii=False, indent=2))
    return out
