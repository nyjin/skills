#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""retro 스킬이 부르는 CLI. 하위 명령마다 JSON 한 덩어리를 stdout 에 쓴다. 오류는 stderr 와 종료 코드 1, 락 경합은 3."""
import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import apply_target
import backfill
import decide
import groups
import observe
import paths
import rubric

LOCKED = ("observe", "groups", "decide", "rubric-apply", "backfill")


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: {message}", file=sys.stderr)
        sys.exit(1)


def _read_json(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _train_labels(label: str) -> list[tuple[str, dict]]:
    """train 분할의 label 라벨 묶음, 최근 결정부터."""
    rows = [(g, v) for g, v in decide.labels().items() if v["label"] == label and decide.split(g) == "train"]
    return sorted(rows, key=lambda p: paths.aware(p[1]["at"]), reverse=True)


def pending_examples(limit: int) -> list[dict]:
    store = groups.load()
    out = []
    for gid, lab in _train_labels("negative"):
        if lab["kind"] != "not-defect":
            continue
        g = store.get(gid) or {}
        out.append({"group": gid, "problem": g.get("problem"), "reason": rubric._first_reason(g) or lab["note"]})
    return out[:limit]


def context_examples(limit: int) -> list[dict]:
    store = groups.load()
    pos, neg = _train_labels("positive"), _train_labels("negative")
    mixed = [x for pair in zip(pos, neg) for x in pair] + pos[len(neg):] + neg[len(pos):]
    return [{"group": gid, "label": lab["label"], "kind": lab["kind"],
             "problem": (store.get(gid) or {}).get("problem"), "note": lab["note"]} for gid, lab in mixed[:limit]]


def cmd_pending(a, cfg):
    batch = observe.pending(cfg["judge_batch"] if a.limit is None else a.limit)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"evidence": batch, "examples": pending_examples(cfg["label_examples"])},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    return {"count": len(batch), "file": str(out.resolve()), "remaining": observe.pending_count() - len(batch)}


def cmd_context(a, cfg):
    since = (paths.aware(paths.now()) - timedelta(days=cfg["window_days"])).isoformat()
    return {"observations": [o for o in observe.observations(since) if o.get("verdict") is True],
            "groups": groups.load(), "decisions": decide.last_decisions(),
            "rubric": str((paths.data_dir() / "rubric.md").resolve()),
            "examples": context_examples(cfg["label_examples"])}


def cmd_gate(a, cfg):
    out = groups.gate()
    out["report"] = [{**g, "apply_target": apply_target.classify(g["target_path"])} for g in out["report"]]
    return out


def cmd_observe(a, cfg):
    result = _read_json(a.file)
    if a.pending:
        wanted = [e.get("id") for e in _read_json(a.pending).get("evidence") or []]
        got = {e.get("evidence_id") for e in result.get("observations") or [] if isinstance(e, dict)}
        missing = [i for i in wanted if i not in got]
        if missing:
            raise ValueError(f"판정하지 않은 근거 {len(missing)}건: {' '.join(missing)}")
    return observe.save(result)


def _replay_gate_schema() -> dict:
    item = json.loads(observe.SCHEMA.read_text(encoding="utf-8"))["properties"]["observations"]["items"]
    return {"type": "object", "required": ["rubric_version", "results"], "properties": {
        "rubric_version": {"type": "integer"},
        "results": {"type": "array", "items": {
            "type": "object", "required": ["group", "observations"], "properties": {
                "group": {"type": "string", "minLength": 1},
                "observations": {"type": "array", "items": item},
                "group_fields": {"type": "object"}}}}}}


def cmd_replay_gate(a, cfg):
    """다시 판정한 관찰을 보관본 근거와 합쳐 관문에 걸고 replay-schema 결과를 쓴다."""
    data = _read_json(a.file)
    observe.validate(data, _replay_gate_schema())
    results = []
    for i, r in enumerate(data["results"]):
        inp = paths.data_dir() / "replay" / r["group"] / "input.json"
        if not inp.is_file():
            raise ValueError(f"$.results[{i}].group: 보관본이 없는 묶음 {r['group']}")
        archived = _read_json(inp)
        by_id = {e.get("evidence_id"): e for e in archived.get("observations") or []}
        obs = []
        for j, e in enumerate(r["observations"]):
            if e["evidence_id"] not in by_id:
                raise ValueError(f"$.results[{i}].observations[{j}]: 보관본에 없는 evidence_id {e['evidence_id']}")
            if e["type"] == "repeat" and not e.get("purpose"):
                raise ValueError(f"$.results[{i}].observations[{j}].purpose: repeat 는 purpose 가 필요합니다")
            src = by_id[e["evidence_id"]].get("evidence") or by_id[e["evidence_id"]].get("observation") or {}
            obs.append({**e, **{f: src.get(f) for f in observe.COPY_FIELDS},
                        "project": observe.project_of(src)})
        surfaces = rubric.gate_one(archived["group"], {"observations": obs, "group": r.get("group_fields") or {}})
        results.append({"group": r["group"], "surfaces": surfaces})
    out = {"rubric_version": data["rubric_version"], "results": results}
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def cmd_replay_inputs(a, cfg):
    base = paths.data_dir() / "replay"
    return [str(p) for g, v in decide.labels().items()
            if v["label"] in ("positive", "negative") and (p := base / g / "input.json").is_file()]


def cmd_rubric_apply(a, cfg):
    ver = rubric.apply(a.kind, a.rule, a.evidence, dropped_positives=a.dropped or [], confirmed=a.confirmed,
                       out=Path(a.out) if a.out else None)
    return {"version": ver, **({"out": str(Path(a.out).resolve())} if a.out else {})}


COMMANDS = {
    "pending": cmd_pending,
    "observe": cmd_observe,
    "context": cmd_context,
    "groups": lambda a, cfg: {"changed": groups.merge(_read_json(a.file))},
    "gate": cmd_gate,
    "decide": lambda a, cfg: decide.record(a.id, a.choice, kind=a.kind, reason=a.reason, ticket=a.ticket),
    "ticket-file": lambda a, cfg: {"file": str(decide.ticket_file(a.id, a.out_dir).resolve())},
    "metrics": lambda a, cfg: decide.precision(),
    "rubric-candidates": lambda a, cfg: rubric.candidates(),
    "rubric-apply": cmd_rubric_apply,
    "replay-score": lambda a, cfg: rubric.replay_score(_read_json(a.current), _read_json(a.candidate)),
    "replay-gate": cmd_replay_gate,
    "replay-inputs": cmd_replay_inputs,
    "backfill": lambda a, cfg: backfill.run(a.days),
}


def parser() -> argparse.ArgumentParser:
    p = Parser(prog="retro.py")
    sub = p.add_subparsers(dest="command", required=True, parser_class=Parser)
    s = sub.add_parser("pending")
    s.add_argument("--out", required=True)
    s.add_argument("--limit", type=int)
    s = sub.add_parser("observe")
    s.add_argument("--file", required=True)
    s.add_argument("--pending")
    sub.add_parser("context")
    sub.add_parser("groups").add_argument("--file", required=True)
    sub.add_parser("gate")
    s = sub.add_parser("decide")
    s.add_argument("id")
    s.add_argument("choice", choices=decide.CHOICES)
    s.add_argument("--kind", choices=decide.KINDS)
    s.add_argument("--reason")
    s.add_argument("--ticket")
    s = sub.add_parser("ticket-file")
    s.add_argument("id")
    s.add_argument("--out-dir", required=True)
    sub.add_parser("metrics")
    sub.add_parser("rubric-candidates")
    s = sub.add_parser("rubric-apply")
    s.add_argument("--kind", required=True)
    s.add_argument("--rule", required=True)
    s.add_argument("--evidence", nargs="+", required=True)
    s.add_argument("--dropped", nargs="+")
    s.add_argument("--confirmed", action="store_true")
    s.add_argument("--out")
    s = sub.add_parser("replay-score")
    s.add_argument("--current", required=True)
    s.add_argument("--candidate", required=True)
    s = sub.add_parser("replay-gate")
    s.add_argument("--file", required=True)
    s.add_argument("--out", required=True)
    sub.add_parser("replay-inputs")
    sub.add_parser("backfill").add_argument("--days", type=int)
    return p


def main(argv=None) -> None:
    a = parser().parse_args(argv)
    lock = None
    try:
        if a.command in LOCKED:
            lock = paths.data_dir()
            if not paths.acquire_lock(lock):
                lock = None
                print("다른 retro 작업이 공용 데이터 락을 쥐고 있습니다. 잠시 뒤 다시 실행하세요", file=sys.stderr)
                sys.exit(paths.LOCKED_EXIT)
        result = COMMANDS[a.command](a, paths.load_config())
    except (ValueError, KeyError, OSError) as e:
        print(f"retro.py {a.command}: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if lock is not None:
            paths.release_lock(lock)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
