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
    items = observe.pending(10 ** 9)
    batch = items[:a.limit or cfg["judge_batch"]]
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"evidence": batch, "examples": pending_examples(cfg["label_examples"])},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    return {"count": len(batch), "file": str(out.resolve()), "remaining": len(items) - len(batch)}


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
    "observe": lambda a, cfg: observe.save(_read_json(a.file)),
    "context": cmd_context,
    "groups": lambda a, cfg: {"changed": groups.merge(_read_json(a.file))},
    "gate": cmd_gate,
    "decide": lambda a, cfg: decide.record(a.id, a.choice, kind=a.kind, reason=a.reason, ticket=a.ticket),
    "ticket-file": lambda a, cfg: {"file": str(decide.ticket_file(a.id, a.out_dir).resolve())},
    "metrics": lambda a, cfg: decide.precision(),
    "rubric-candidates": lambda a, cfg: rubric.candidates(),
    "rubric-apply": cmd_rubric_apply,
    "replay-score": lambda a, cfg: rubric.replay_score(_read_json(a.current), _read_json(a.candidate)),
    "replay-inputs": cmd_replay_inputs,
    "backfill": lambda a, cfg: backfill.run(a.days),
}


def parser() -> argparse.ArgumentParser:
    p = Parser(prog="retro.py")
    sub = p.add_subparsers(dest="command", required=True, parser_class=Parser)
    s = sub.add_parser("pending")
    s.add_argument("--out", required=True)
    s.add_argument("--limit", type=int)
    sub.add_parser("observe").add_argument("--file", required=True)
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
