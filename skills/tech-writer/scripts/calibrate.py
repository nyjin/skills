#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["kiwipiepy>=0.24.0", "duckdb>=1.1"]
# ///
"""보정 모드: AI slop 기준을 표본으로 다시 재고, 기준을 바꾸자는 제안을 만든다.

사용: uv run calibrate.py [--dir ~/.claude/tech-writing/calibration] [--holdout-every 3]
      uv run calibrate.py --apply B2[,A1] [--by <승인한 사람>]   승인한 항목만 slop_rules.json 으로 옮기고, 바뀐 값을 보이고, 판정 저장소에 기록한다

읽는 것 (모두 --dir 아래, 저장소 밖)
  slop.duckdb      판정 저장소(slop_db.py). 대상 문서(documents)와 사용자 판정(judgments)을 읽는다
  files/, cache/   file: 표본의 사본, Confluence 표본을 Markdown 으로 바꿔 둔 곳 (없으면 confluence-cli 로 받는다)
쓰는 것
  slop_rules.proposed.json  제안. 살아 있는 slop_rules.json 은 고치지 않는다
  calibration-report.md     slop-rubric.md "보정 모드 보고 형식"의 보고
  calibration-evidence.md   항목마다 실제로 걸린 문장 (기준 문서의 오탐 후보, AI 문서의 적중 예)

판정 (slop-rubric.md "보정 모드 보고 형식"과 같다)
  게이트  기준 문서 오탐 0줄, AI slop 문서 3편 이상, 검증용 표본이 있으면 거기서도 적중, 사용자 오탐 기록 0
  지시    AI slop 문서 3편 이상이지만 기준 문서나 사용자 기록에서 오탐이 나온다
  보류    AI slop 문서 3편 미만
  사용자 규칙(policy)은 수치만 보고하고 상태를 바꾸지 않는다. 예) 링크 없는 참조 번호는 slop 여부와 상관없이 고친다
"""
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lint_doc  # noqa: E402
import redundancy  # noqa: E402
import slop_check  # noqa: E402
import slop_db  # noqa: E402
import storage2md  # noqa: E402

DEFAULT_DIR = Path.home() / ".claude/tech-writing/calibration"
LINE_ITEMS = {"A1": "symbol-operator", "A3": "bold-verdict", "C1": "unlinked-ref"}
RANK = {"hold": 0, "advisory": 1, "gate": 2}
STATUS_WORD = {"gate": "게이트", "advisory": "지시", "judge": "판단", "hold": "보류"}
MIN_DOCS = 3


def load_corpus(con, holdout_every):
    """판정 저장소의 대상 문서. 검증용 여부가 없으면 출처의 해시로 정해 저장한다(다시 돌려도 같다)."""
    corpus = slop_db.doc_list(con)
    for c in corpus:
        if holdout_every and c.get("holdout") is None:
            c["holdout"] = int(hashlib.sha1(c["source"].encode()).hexdigest(), 16) % holdout_every == 0
            slop_db.set_holdout(con, c["source"], c["holdout"])
    return corpus


def text_of(d, c):
    kind, _, ref = c["source"].partition(":")
    if kind == "file":
        p = Path(ref).expanduser()
        return (p if p.is_absolute() else d / p).read_text(encoding="utf-8")
    if kind == "confluence":
        cache = d / "cache" / f"{ref}.md"
        if not cache.exists():
            out = subprocess.run(["confluence", "read", ref, "--format", "storage"], capture_output=True, text=True)
            if out.returncode != 0:
                raise SystemExit(f"Confluence 페이지 {ref}를 받지 못했다: {out.stderr.strip()[:200]}")
            cache.parent.mkdir(parents=True, exist_ok=True)
            base = os.environ.get("TECH_WRITER_JIRA_BASE", storage2md.JIRA)
            cache.write_text(storage2md.convert(out.stdout, base), encoding="utf-8")
        return cache.read_text(encoding="utf-8")
    raise SystemExit(f"알 수 없는 source: {c['source']}")


def lint_lines(text, rule, threshold=None):
    """규칙 하나만 켜고 걸린 줄 번호와 메시지."""
    saved = lint_doc.SLOP_RULES
    lint_doc.SLOP_RULES = {rule: {"status": "gate", "threshold": threshold}}
    try:
        return [(f.line, f.message) for f in lint_doc.lint(text) if f.rule == rule]
    finally:
        lint_doc.SLOP_RULES = saved


def max_switches(text):
    found = lint_lines(text, "mixed-script", threshold=1)
    return max((int(re.search(r"(\d+)번 바뀐다", m).group(1)) for _, m in found), default=0)


def decide(ref_false, user_false, train_hits, holdout_hits, holdout_slops):
    if train_hits < MIN_DOCS:
        return "hold", f"AI 문서 적중이 {train_hits}편이라 {MIN_DOCS}편에 못 미친다"
    if ref_false or user_false:
        why = []
        if ref_false:
            why.append(f"기준 문서에서 {ref_false}줄 걸렸다")
        if user_false:
            why.append(f"사용자 오탐 기록이 {user_false}건 있다")
        return "advisory", ", ".join(why)
    if holdout_slops and not holdout_hits:
        return "advisory", "검증용 AI 문서에서는 한 편도 걸리지 않았다"
    return "gate", ""


def run(d, holdout_every=3, con=None):
    d = Path(d)
    con = con or slop_db.connect(d / "slop.duckdb")
    corpus = load_corpus(con, holdout_every)
    docs = [(c, text_of(d, c)) for c in corpus]
    ref_n = [(c["source"].partition(":")[2], t) for c, t in docs if c["label"] in ("human", "approved") and not c.get("holdout")]
    slop_n = [(c["source"].partition(":")[2], t) for c, t in docs if c["label"] == "slop" and not c.get("holdout")]
    hold_n = [(c["source"].partition(":")[2], t) for c, t in docs if c["label"] == "slop" and c.get("holdout")]
    ref, slop, hold_slop = [t for _, t in ref_n], [t for _, t in slop_n], [t for _, t in hold_n]
    judg = slop_db.rows(con, "SELECT item, verdict FROM judgments")
    user_false = Counter(j["item"] for j in judg if j["verdict"] == "오탐")
    rules = json.loads((HERE / "slop_rules.json").read_text(encoding="utf-8"))
    items = {}

    def line_item(k, find_fn):
        """find_fn(text) → 걸린 곳 인용 목록. 수와 함께 사용자가 확인할 실제 문장을 모은다."""
        ref_hits = [(n, q) for n, t in ref_n for q in find_fn(t)]
        slop_hits = {n: find_fn(t) for n, t in slop_n}
        hold_hits = {n: find_fn(t) for n, t in hold_n}
        hits = sum(1 for v in slop_hits.values() if v)
        holds = sum(1 for v in hold_hits.values() if v)
        status, why = decide(len(ref_hits), user_false[k], hits, holds, len(hold_slop))
        return {"reference_false_lines": len(ref_hits), "slop_docs_hit": hits, "holdout_hit": holds,
                "user_false": user_false[k], "proposal": {"status": status}, "why": why,
                "examples": {"reference": ref_hits,
                             "slop": [(n, q) for n, qs in slop_hits.items() for q in qs[:2]],
                             "holdout": [(n, q) for n, qs in hold_hits.items() for q in qs[:1]]}}

    def lint_quotes(rule, threshold=None):
        def find(t):
            lines = t.splitlines()
            return [slop_check.quote(lines[no - 1], 60) for no, _ in lint_lines(t, rule, threshold)]
        return find

    for k, rule in LINE_ITEMS.items():
        items[k] = line_item(k, lint_quotes(rule))
        if rules["items"][k].get("policy"):
            items[k]["proposal"] = {"status": rules["items"][k]["status"]}
            items[k]["why"] = "사용자가 정한 규칙이라 상태를 바꾸지 않는다"
            items[k]["policy"] = True

    ref_max = max((max_switches(t) for t in ref), default=0)
    threshold = ref_max + 2  # 기준 문서 최댓값보다 한 단계 낮춰도 오탐이 없게
    items["A2"] = line_item("A2", lint_quotes("mixed-script", threshold))
    items["A2"]["proposal"]["threshold"] = threshold
    items["A2"]["reference_max_switches"] = ref_max

    items["B2"] = line_item("B2", lambda t: [f"{x['section']} · {x['quote']}" for x in slop_check.restated_lists(t)])

    dens = [slop_check.density(t) for t in ref]
    limits = {k: max((x[k] for x in dens), default=0) for k in ("bold_per100s", "dash_per100s", "paren_per100s")}
    a4_hits = sum(1 for t in slop if any(slop_check.density(t)[k] > v for k, v in limits.items()))
    items["A4"] = {"slop_docs_hit": a4_hits, "reference_false_lines": 0, "user_false": user_false["A4"],
                   "proposal": {"status": rules["items"]["A4"]["status"], "limits": limits}, "why": "기준 문서 최댓값"}

    for k, kind in (("B1", "repeated-claim"), ("B3", "diagram-transcribed")):
        count = lambda t: sum(1 for f in redundancy.analyze(t)["findings"] if f["kind"] == kind)
        items[k] = {"reference_false_lines": sum(count(t) for t in ref), "slop_docs_hit": sum(1 for t in slop if count(t)),
                    "user_false": user_false[k], "proposal": {"status": rules["items"][k]["status"]}, "why": "지시 항목"}

    return {"rules": rules, "items": items, "judgments": {"오탐": sum(1 for j in judg if j.get("verdict") == "오탐"),
                                                          "놓침": sum(1 for j in judg if j.get("verdict") == "놓침")},
            "counts": {"reference": len(ref), "slop": len(slop), "holdout": sum(1 for c in corpus if c.get("holdout")),
                       "total": len(corpus)}}


def change_word(cur, prop):
    if prop.get("threshold") is not None and cur.get("threshold") not in (None, prop["threshold"]):
        return f"임계값 {cur['threshold']}→{prop['threshold']}"
    a, b = RANK.get(cur["status"], -1), RANK.get(prop["status"], -1)
    return "유지" if a == b else ("올림" if b > a else "내림")


def render(result):
    c = result["counts"]
    out = [f"### 기준 점검 (표본 {c['total']}편: 기준 문서 {c['reference']}, AI slop 문서 {c['slop']}, 검증용 {c['holdout']})", "",
           "| 항목 | 지금 | 제안 | 근거 |", "|---|---|---|---|"]
    reasons = []
    for k, r in result["items"].items():
        cur = result["rules"]["items"][k]
        word = change_word(cur, r["proposal"])
        basis = f"기준 문서 오탐 {r['reference_false_lines']}줄 · AI 문서 {c['slop']}편 중 {r['slop_docs_hit']}편"
        if "holdout_hit" in r and c["holdout"]:
            basis += f" · 검증용 적중 {r['holdout_hit']}편"
        if r.get("user_false"):
            basis += f" · 사용자 오탐 {r['user_false']}건"
        if k == "A2":
            basis += f" · 기준 문서 최댓값 {r['reference_max_switches']}번"
        prop = STATUS_WORD[r["proposal"]["status"]]
        tag = " (사용자 규칙)" if r.get("policy") else ("" if word == "유지" else f" ({prop})")
        out.append(f"| {cur['name']} | {STATUS_WORD[cur['status']]} | {word}{tag} | {basis} |")
        if word != "유지":
            reasons.append(f"- {cur['name']}: {r['why'] or '채택 기준을 모두 채웠다'}.")
    out += ["", "바꿀 이유"] + (reasons or ["- 없음."])
    j = result["judgments"]
    out += ["", f"판정 기록 반영: {j['오탐'] + j['놓침']}건 (오탐 {j['오탐']}, 놓침 {j['놓침']})"]
    return "\n".join(out) + "\n"


def render_evidence(result, limit=8):
    """항목마다 실제로 걸린 문장. 사용자가 숫자 대신 문장을 보고 판정을 확인한다."""
    out = ["# 기준 점검 근거", "",
           "항목마다 실제로 걸린 문장이다. 기준 문서에서 걸린 줄은 오탐 후보이고, AI 문서에서 걸린 줄은 적중 예다.", ""]
    for k, r in result["items"].items():
        ex = r.get("examples")
        if not ex:
            continue
        cur = result["rules"]["items"][k]
        out += [f"## {cur['name']}", "",
                f"지금 {STATUS_WORD[cur['status']]} → 제안 {STATUS_WORD[r['proposal']['status']]}"
                + (f" · {r['why']}" if r.get("why") else ""), ""]
        for key, title in (("reference", "기준 문서에서 걸린 줄"), ("slop", "AI 문서에서 걸린 줄"), ("holdout", "검증용 AI 문서에서 걸린 줄")):
            rows = ex.get(key, [])
            out.append(f"{title} ({len(rows)})")
            out += [f"- {n} · {q}" for n, q in rows[:limit]] or ["- 없음"]
            if len(rows) > limit:
                out.append(f"- … 외 {len(rows) - limit}줄")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def apply(proposed_path, live_path, item_ids, con=None, by=None):
    """승인한 항목만 제안에서 살아 있는 기준으로 옮기고, 바뀐 값을 '전 → 후'로 돌려준다."""
    proposed = json.loads(Path(proposed_path).read_text(encoding="utf-8"))
    live = json.loads(Path(live_path).read_text(encoding="utf-8"))
    lines = []
    for k in item_ids:
        if k not in proposed["items"] or k not in live["items"]:
            raise SystemExit(f"제안에 없는 항목: {k}")
        cur, new = live["items"][k], proposed["items"][k]
        name = cur["name"]
        reason = (new.get("evidence") or {}).get("note") or "보정 제안 승인"
        if cur.get("status") != new.get("status"):
            lines.append(f"- {name}: 상태 {STATUS_WORD[cur['status']]} → {STATUS_WORD[new['status']]}")
            if con is not None:
                slop_db.log_change(con, k, "status", cur.get("status"), new.get("status"), reason, by)
        for key, word in (("threshold", "임계값"), ("limits", "기준값")):
            if key in new and cur.get(key) != new[key]:
                lines.append(f"- {name}: {word} {cur.get(key)} → {new[key]}")
                if con is not None:
                    slop_db.log_change(con, k, key, cur.get(key), new[key], reason, by)
        for key in ("status", "threshold", "limits", "evidence"):
            if key in new:
                cur[key] = new[key]
    if not lines:
        lines.append("- 바뀐 값 없음")
    Path(live_path).write_text(json.dumps(live, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return "반영한 변경\n" + "\n".join(lines) + "\n"


def write_outputs(d, result, report):
    proposed = json.loads(json.dumps(result["rules"]))
    for k, r in result["items"].items():
        item = proposed["items"][k]
        item["status"] = r["proposal"]["status"]
        for key in ("threshold", "limits"):
            if key in r["proposal"]:
                item[key] = r["proposal"][key]
        item["evidence"] = {key: r[key] for key in ("reference_false_lines", "slop_docs_hit", "holdout_hit", "user_false")
                            if key in r}
    proposed["corpus"] = result["counts"]
    (Path(d) / "slop_rules.proposed.json").write_text(json.dumps(proposed, ensure_ascii=False, indent=2) + "\n",
                                                      encoding="utf-8")
    (Path(d) / "calibration-report.md").write_text(report, encoding="utf-8")
    (Path(d) / "calibration-evidence.md").write_text(render_evidence(result), encoding="utf-8")


def main(argv):
    d = Path(argv[argv.index("--dir") + 1]).expanduser() if "--dir" in argv else DEFAULT_DIR
    if "--apply" in argv:
        ids = argv[argv.index("--apply") + 1].split(",")
        by = argv[argv.index("--by") + 1] if "--by" in argv else None
        print(apply(d / "slop_rules.proposed.json", HERE / "slop_rules.json", ids, slop_db.connect(d / "slop.duckdb"), by), end="")
        return 0
    every = int(argv[argv.index("--holdout-every") + 1]) if "--holdout-every" in argv else 3
    if not slop_db.doc_list(slop_db.connect(d / "slop.duckdb")):
        print(f"대상 문서가 없다. slop_db.py doc-add 로 넣는다 ({d / 'slop.duckdb'})", file=sys.stderr)
        return 2
    result = run(d, every)
    report = render(result)
    write_outputs(d, result, report)
    print(report, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
