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

SCHEMA = Path(__file__).resolve().parents[1] / "defaults" / "groups-schema.json"
# 설계 7장 "관찰 → 결정 대상": 파일 검증 결과별로 허용하는 결정 대상
ALLOWED = {
    "missing": {"skill_supplement", "guideline_supplement", "script"},
    "no_instruction": {"skill_supplement", "guideline_supplement"},
    "wrong": {"fix"},
    "ignored": {"fix"},
    "has_script": {"fix"},
    "asks_here": {"fix"},
    "asks_user": {"fix"},
    "opposite": {"fix"},
    "environment": set(),
}
DROP_REASONS = ("unverified", "not_specific", "no_failure_ref", "decided", "environment", "inconsistent",
                "conflicting_choice", "scope")


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
        for key in ("observations", "conflicts"):
            bad = [e for e in g.get(key, []) if e not in true_ids]
            if bad:
                raise ValueError(f"$.groups[{i}].{key}: 참 관찰이 아닌 evidence_id {bad[0]}")
        if not g.get("id", slug(g["problem"])):
            raise ValueError(f"$.groups[{i}].id: 빈 묶음 id 입니다")
    store = load()
    changed = []
    at = paths.now()
    for g in result["groups"]:
        gid = g.get("id", slug(g["problem"]))
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
    if decisions.get(group["id"]):
        return "dropped", "decided", {}
    finding = group["verified"].get("finding")
    ttype = group["target_type"]
    if finding == "environment":
        return "dropped", "environment", {}
    if ttype not in ALLOWED.get(finding, set()):
        return "dropped", "inconsistent", {}
    if any(observations_by_id.get(e, {}).get("verdict") is True for e in group.get("conflicts", [])):
        return "dropped", "conflicting_choice", {}

    cut = paths.aware(now) - timedelta(days=cfg["window_days"])
    obs = [o for o in true_obs if o.get("at") and paths.aware(o["at"]) >= cut]
    extra = {"count": len(obs), "sessions": len({o.get("session") for o in obs}),
             "projects": len({o.get("project") for o in obs})}
    if ttype == "guideline_supplement":
        is_global = _real(group["target_path"]) == _real("~/.claude/CLAUDE.md")
        if is_global != (extra["projects"] >= 2):
            return "dropped", "scope", extra

    def pattern(of: list[dict]) -> bool:
        return len(of) >= cfg["pattern_min_count"] and len({o.get("session") for o in of}) >= cfg["pattern_min_sessions"]

    types = {o.get("type") for o in obs}
    of_type = lambda t: [o for o in obs if o.get("type") == t]  # noqa: E731
    if "approval_question" in types:
        ok = len(obs) >= 1
    elif "decision_pattern" in types:
        ok = pattern(of_type("decision_pattern"))
    elif ttype == "script":
        ok = pattern(obs)
    elif ttype in ("skill_supplement", "guideline_supplement"):
        ok = pattern(of_type("correction"))
    else:
        ok = len(obs) >= 1
    return ("report" if ok else "held"), None, extra


def gate(now: str | None = None) -> dict:
    now = now or paths.now()
    cfg = paths.load_config()
    by_id = {o["evidence_id"]: o for o in observe.observations() if o.get("verdict") is True and o.get("evidence_id")}
    decisions = _last_decisions()
    out = {"report": [], "held": [], "dropped": []}
    for gid, group in load().items():
        status, reason, extra = judge_group(group, by_id, decisions, cfg, now)
        if status == "dropped":
            out["dropped"].append({"id": gid, "reason": reason})
        elif status == "report":
            out["report"].append({**group, **extra})
        else:
            out["held"].append(gid)
    return out
