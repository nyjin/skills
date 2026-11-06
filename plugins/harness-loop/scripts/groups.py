#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""관찰 묶음(groups.json)의 병합과, 묶음을 결정 대상으로 올릴지 정하는 관문."""
import json
import os
import re
from datetime import timedelta
from pathlib import Path

import observe
import paths
import versions

SCHEMA = Path(__file__).resolve().parents[1] / "defaults" / "groups-schema.json"
EPOCH = "1970-01-01T00:00:00+00:00"
# 설계 7장 "관찰 → 결정 대상": 파일 검증 결과별로 허용하는 결정 대상(rework 는 검사하지 않음)
ALLOWED = {
    "missing": {"skill_supplement", "guideline_supplement", "script"},
    "no_instruction": {"skill_supplement", "guideline_supplement"},
    "wrong": {"fix"},
    "ignored": {"fix"},
    "has_script": {"fix"},
    "asks_here": {"fix"},
}


def slug(text: str) -> str:
    return re.sub(r"\W+", "-", text.lower()).strip("-")


def _file() -> Path:
    return paths.data_dir() / "groups.json"


def load() -> dict[str, dict]:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def merge(result: dict) -> list[str]:
    """검증 후 id 가 있으면 관찰 목록을 합치고 나머지 필드를 새 값으로, 없으면 추가한다. 바뀐 id 목록을 돌려준다."""
    observe.validate(result, json.loads(SCHEMA.read_text(encoding="utf-8")))
    true_ids = {o.get("evidence_id") for o in observe.observations() if o.get("verdict") is True}
    for i, g in enumerate(result["groups"]):
        if g["verified"] is not None and not isinstance(g["verified"], dict):
            raise ValueError(f"$.groups[{i}].verified: object 또는 null 이어야 합니다")
        if g["target_type"] == "rework" and not g.get("rework_kind"):
            raise ValueError(f"$.groups[{i}].rework_kind: rework 는 rework_kind 가 필요합니다")
        bad = [e for e in g["observations"] if e not in true_ids]
        if bad:
            raise ValueError(f"$.groups[{i}].observations: 참 관찰이 아닌 evidence_id {bad[0]}")
    store = load()
    changed = []
    at = paths.now()
    for g in result["groups"]:
        gid = g.get("id") or slug(g["problem"])
        old = store.get(gid)
        new = {**g, "id": gid}
        if old:
            new["observations"] = list(dict.fromkeys(old["observations"] + g["observations"]))
            new["created"] = old["created"]
        else:
            new["created"] = at
        if old and {k: v for k, v in old.items() if k != "updated"} == new:
            continue
        new["updated"] = at
        store[gid] = new
        if gid not in changed:
            changed.append(gid)
    if changed:
        paths.atomic_write(_file(), json.dumps(store, ensure_ascii=False, indent=2))
    return changed


def _last_decisions() -> dict[str, dict]:
    try:
        import decide
    except ImportError:
        return {}
    return decide.last_decisions()


def _real(p: str) -> str:
    return os.path.realpath(os.path.expanduser(p))


def obs_hash(obs: dict, target_path: str) -> str | None:
    """관찰 당시 대상 파일의 해시: 스킬 SKILL.md 면 skill.hash, 아니면 meta.scripts·meta.guidelines 중 같은 경로."""
    real = _real(target_path)
    skill = obs.get("skill") or {}
    if skill.get("path") and real in (_real(skill["path"]), _real(os.path.join(skill["path"], "SKILL.md"))):
        if skill.get("hash"):
            return skill["hash"]
    meta = obs.get("meta") or {}
    for key in ("scripts", "guidelines"):
        for e in meta.get(key) or []:
            if isinstance(e, dict) and e.get("path") and _real(e["path"]) == real and e.get("hash"):
                return e["hash"]
    return None


def _root_and_name(group: dict) -> tuple[str, str]:
    """git 밖 안내용 (스킬 루트, 이름). skill_root 가 없으면 대상 파일의 디렉터리와 파일 이름."""
    if group.get("skill_root"):
        root = os.path.expanduser(group["skill_root"])
        return root, os.path.basename(root.rstrip("/"))
    target = os.path.expanduser(group["target_path"])
    return os.path.dirname(target), os.path.basename(target)


def _rework(group: dict, obs: list[dict], proceed: dict | None, cfg: dict) -> tuple[bool, dict]:
    """rework 관문. (넘었는지, 보고 필드).

    proceed 결정이 있으면 obs 는 이미 결정 뒤 것뿐이라 해시가 있는 관찰을 모두 재발로 센다.
    없으면 fix_version 의 versions.history 순서 이후(같은 버전 포함) 해시를 가진 관찰만 센다."""
    target = _real(group["target_path"])
    hist = [e["hash"] for e in versions.history(target, EPOCH)]
    if group["rework_kind"] == "repeat_edit":
        edits = set(group.get("edits") or [])
        return bool(edits) and edits <= set(hist) and len(edits) >= cfg["repeat_edit_min"], {}
    if proceed:
        rec = [o["evidence_id"] for o in obs if obs_hash(o, target)]
        return bool(rec), ({"recurrence": rec} if rec else {})
    base = group.get("fix_version")
    if base not in hist:
        return False, {}
    start = hist.index(base)
    rec = [o["evidence_id"] for o in obs if (h := obs_hash(o, target)) in hist and hist.index(h) >= start]
    return bool(rec), ({"recurrence": rec} if rec else {})


def judge_group(group: dict, observations_by_id: dict, decisions: dict, cfg: dict, now: str) -> tuple[str, str | None, dict]:
    """묶음 하나를 관문에 건다. ("report"|"held"|"dropped", 버림 이유, 보고 필드)."""
    true_obs = [observations_by_id[e] for e in group["observations"]
                if e in observations_by_id and observations_by_id[e].get("verdict") is True]
    if group.get("verified") is None:
        return "dropped", "unverified", {}
    if not group.get("specific_facts"):
        return "dropped", "not_specific", {}
    if group.get("failure_ref") not in {o["evidence_id"] for o in true_obs}:
        return "dropped", "no_failure_ref", {}
    decision = decisions.get(group["id"])
    choice = (decision or {}).get("decision")
    if choice == "reject":
        return "dropped", "rejected", {}
    ttype = group["target_type"]
    if ttype != "rework" and ttype not in ALLOWED.get(group["verified"].get("finding"), set()):
        return "dropped", "inconsistent", {}

    cut = paths.aware(now) - timedelta(days=cfg["window_days"])
    after = paths.aware(decision["at"]) if choice == "proceed" and decision.get("at") else None
    obs = [o for o in true_obs if o.get("at") and paths.aware(o["at"]) >= cut
           and (after is None or paths.aware(o["at"]) > after)]
    extra = {"count": len(obs), "sessions": len({o.get("session") for o in obs}),
             "projects": len({o.get("project") for o in obs})}
    if choice == "ticket":
        return "held", None, extra

    types = {o.get("type") for o in obs}
    count, sessions = extra["count"], extra["sessions"]
    if ttype == "rework":
        if versions.git_root(_real(group["target_path"])) is None:
            return "dropped", "untracked", extra
        ok, more = _rework(group, obs, decision if choice == "proceed" else None, cfg)
        return ("report" if ok else "held"), None, {**extra, **more}
    if ttype == "script":
        ok = count >= cfg["script_min_count"] and sessions >= cfg["script_min_sessions"]
    elif ttype in ("skill_supplement", "fix") and "approval_question" in types:
        ok = count >= 1
    elif ttype == "skill_supplement":
        ok = sum(o.get("type") == "correction" for o in obs) >= cfg["correction_min_count"]
    elif ttype == "guideline_supplement":
        is_global = _real(group["target_path"]) == _real("~/.claude/CLAUDE.md")
        if is_global != (extra["projects"] >= 2):
            return "dropped", "scope", extra
        ok = count >= cfg["correction_min_count"] and sessions >= cfg["guideline_min_sessions"]
    else:
        ok = count >= 1
    return ("report" if ok else "held"), None, extra


def gate(now: str | None = None) -> dict:
    now = now or paths.now()
    cfg = paths.load_config()
    by_id = {o["evidence_id"]: o for o in observe.observations() if o.get("verdict") is True and o.get("evidence_id")}
    decisions = _last_decisions()
    out = {"report": [], "held": [], "dropped": [], "notices": []}
    untracked: dict[str, list[str]] = {}
    for gid, group in load().items():
        status, reason, extra = judge_group(group, by_id, decisions, cfg, now)
        if status == "dropped":
            out["dropped"].append({"id": gid, "reason": reason})
            if reason != "untracked":
                continue
        elif status == "report":
            out["report"].append({**group, **extra})
        else:
            out["held"].append(gid)
        if reason == "untracked" or versions.git_root(_real(group["target_path"])) is None:
            root, name = _root_and_name(group)
            names = untracked.setdefault(root, [])
            if name not in names:
                names.append(name)
    out["notices"] = versions.untracked_notice(untracked)
    return out
