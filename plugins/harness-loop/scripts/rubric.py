#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""rubric 규칙 후보·적용과 replay 채점."""
import json
import re
from pathlib import Path

import decide
import groups
import observe
import paths

SCHEMA = Path(__file__).resolve().parents[1] / "defaults" / "replay-schema.json"
SECTIONS = {"not-defect": "## 관찰로 보지 않는 경우", "trivial": "## 결정 대상으로 올리지 않는 경우"}
VERSION = re.compile(r"<!--\s*rubric-version:\s*(\d+)\s*-->")


def version(text: str) -> int:
    m = VERSION.match(text)
    return int(m.group(1)) if m else 1


def _changes_file() -> Path:
    return paths.data_dir() / "rubric-changes.jsonl"


def _used() -> set[str]:
    used: set[str] = set()
    try:
        lines = _changes_file().read_text(encoding="utf-8").splitlines()
    except OSError:
        return used
    for ln in lines:
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if isinstance(rec, dict):
            used.update(str(e) for e in rec.get("evidence") or [])
    return used


def _first_reason(group: dict) -> str | None:
    by_id = {o.get("evidence_id"): o for o in observe.observations()}
    for eid in group.get("observations") or []:
        if (by_id.get(eid) or {}).get("reason"):
            return by_id[eid]["reason"]
    return None


def candidates() -> dict:
    """train 분할 negative 라벨 중 아직 근거로 쓰지 않은 묶음을 kind 별로. {"groups": {kind: [...]}, "ready": {kind: bool}}"""
    used, store = _used(), groups.load()
    out: dict[str, list[dict]] = {k: [] for k in SECTIONS}
    for gid, lab in sorted(decide.labels().items(), key=lambda p: paths.aware(p[1]["at"])):
        if lab["label"] != "negative" or lab["kind"] not in SECTIONS or gid in used or decide.split(gid) != "train":
            continue
        g = store.get(gid) or {}
        out[lab["kind"]].append({"group": gid, "problem": g.get("problem"), "target_type": g.get("target_type"),
                                 "note": lab["note"], "first_reason": _first_reason(g)})
    need = paths.load_config()["rubric_min_evidence"]
    return {"groups": out, "ready": {k: len(v) >= need for k, v in out.items()}}


def _with_rule(text: str, kind: str, rule: str, ver: int) -> str:
    line = f"<!-- rubric-version: {ver} -->"
    m = VERSION.match(text)
    text = line + text[m.end():] if m else f"{line}\n{text}"
    lines = text.rstrip("\n").split("\n")
    header = SECTIONS[kind]
    if header not in lines:
        lines += ["", header]
    i = lines.index(header)
    end = i + 1
    while end < len(lines) and not lines[end].startswith("#"):
        end += 1
    body = lines[i + 1:end]
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    tail = [""] if end < len(lines) else []
    return "\n".join(lines[:i + 1] + [""] + body + [f"- {rule}"] + tail + lines[end:]) + "\n"


def apply(kind: str, rule: str, evidence: list[str], *, dropped_positives: list[str], confirmed: bool,
          out: Path | None = None) -> int:
    if kind not in SECTIONS:
        raise ValueError(f"kind 는 {'|'.join(SECTIONS)} 중 하나여야 합니다: {kind}")
    rule = " ".join(rule.split())
    if not rule:
        raise ValueError("규칙 문장이 비었습니다")
    labels, used = decide.labels(), _used()
    for gid in evidence:
        if decide.split(gid) == "test":
            raise ValueError(f"test 분할 묶음은 근거로 쓸 수 없습니다: {gid}")
        if gid in used:
            raise ValueError(f"이미 rubric 규칙 근거로 쓴 묶음입니다: {gid}")
        lab = labels.get(gid) or {}
        if lab.get("label") != "negative" or lab.get("kind") != kind:
            raise ValueError(f"{kind} 거절 라벨이 아닌 묶음입니다: {gid}")
    need = paths.load_config()["rubric_min_evidence"]
    if len(set(evidence)) < need:
        raise ValueError(f"근거는 서로 다른 묶음 {need}개 이상 필요합니다: {len(set(evidence))}개")
    if dropped_positives and not confirmed:
        raise ValueError(f"양성 묶음이 빠집니다({', '.join(dropped_positives)}). 사용자 확인이 필요합니다")
    path = paths.data_dir() / "rubric.md"
    old = path.read_text(encoding="utf-8")
    ver = version(old) + 1
    new = _with_rule(old, kind, rule, ver)
    if out is not None:
        Path(out).write_text(new, encoding="utf-8")
        return ver
    paths.atomic_write(path, new)
    paths.append_line(_changes_file(), {"version": ver, "at": paths.now(), "kind": kind, "rule": rule,
                                        "evidence": list(evidence), "dropped_positives": list(dropped_positives),
                                        "confirmed": confirmed})
    return ver


def _rates(surfaced: set[str], pos: list[str], neg: list[str]) -> dict:
    return {"tpr": sum(g in surfaced for g in pos) / len(pos) if pos else None,
            "tnr": sum(g not in surfaced for g in neg) / len(neg) if neg else None,
            "n_pos": len(pos), "n_neg": len(neg)}


def replay_score(current: dict, candidate: dict) -> dict:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    for name, side in (("current", current), ("candidate", candidate)):
        observe.validate(side, schema, f"${name}")
    cur, cand = ({r["group"] for r in s["results"] if r["surfaces"]} for s in (current, candidate))
    labels = decide.labels()
    positives = [g for g, v in labels.items() if v["label"] == "positive"]
    test = {g: v["label"] for g, v in labels.items() if decide.split(g) == "test"}
    pos = [g for g, lab in test.items() if lab == "positive"]
    neg = [g for g, lab in test.items() if lab == "negative"]
    return {"dropped_positives": [g for g in positives if g in cur and g not in cand],
            "eligible": len(pos) + len(neg) >= paths.load_config()["replay_min_labels"],
            "current": _rates(cur, pos, neg), "candidate": _rates(cand, pos, neg)}


def gate_one(archived_group: dict, rejudged: dict) -> bool:
    """보관본 묶음에 다시 판정한 관찰·검증을 적용해 관문을 넘는지. 결정(라벨)은 넣지 않는다."""
    obs = rejudged.get("observations") or []
    by_id = {o["evidence_id"]: o for o in obs if o.get("evidence_id")}
    group = {**archived_group, **(rejudged.get("group") or {})}
    # 보관본 근거는 window_days 밖일 수 있어 기간은 가장 늦은 관찰 시각을 기준으로 잰다
    now = max((o["at"] for o in obs if o.get("at")), key=paths.aware, default=paths.now())
    status, _, _ = groups.judge_group(group, by_id, {}, paths.load_config(), now)
    return status == "report"
