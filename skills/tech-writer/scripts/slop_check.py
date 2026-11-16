#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["kiwipiepy>=0.24.0", "duckdb>=1.1"]
# ///
"""AI slop 점검. 루브릭(references/slop-rubric.md)의 스크립트 항목을 재고 보고 블록을 만든다.

사용:
  uv run slop_check.py <문서.md> [--baseline <고치기 전.md>] [--judgments <판단.json>] [--db <문서 식별자>] [--json]

- --baseline: 고치기 전 판. 항목별 '처음' 수를 재고, 사라진 사실(D1)을 비교한다
- --judgments: AI가 낸 판단 목록(JSON). 판단 항목, 남긴 이유, 결정 대기, 고친 예를 담는다
  [{"item": "C2", "section": "4. 후속 과제", "quote": "…", "outcome": "고침|남김|결정 대기|해당|해당 없음",
    "reason": "…", "before": "…", "after": "…"}]
  outcome 마다 쓰는 칸: 고침 → before·after(보고의 "고친 것"), 남김·해당 → section·reason(남긴 이유),
  결정 대기 → reason 에 "<정할 것> — <선택지와 추천>"을 한 문장으로, 해당 없음 → item 만
- --db: 판정 저장소(slop_db.py)에서 이 문서의 사람 판정을 읽어, 전에 오탐으로 판정된 곳과 항목별 누적을 보고에 붙인다
- 기본 출력은 9단계 보고에 넣을 Markdown 블록, --json 은 기계용 결과(record 에 넘긴다)
종료 코드: 판정이 '미통과'면 1, 아니면 0.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lint_doc  # noqa: E402
import redundancy  # noqa: E402

RULES = json.loads((HERE / "slop_rules.json").read_text(encoding="utf-8"))
FENCE = re.compile(r"^```.*?^```", re.S | re.M)
LINT_TO_ITEM = {v["lint_rule"]: k for k, v in RULES["items"].items() if v.get("lint_rule")}
REDUNDANCY_TO_ITEM = {v["redundancy_kind"]: k for k, v in RULES["items"].items() if v.get("redundancy_kind")}


def sections(text):
    """줄 번호 → 가장 가까운 위 제목."""
    cur, out = "머리", {}
    for no, line in enumerate(text.splitlines(), 1):
        m = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if m:
            cur = m.group(1)
        out[no] = cur
    return out


def quote(line, n=24):
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", line)
    t = re.sub(r"[*>#|]|^\s*([-+]|\d+[.)])\s", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s+([:.,)])", r"\1", t)
    return t if len(t) <= n else t[:n] + "…"


def prose_lines(text):
    body = FENCE.sub("", re.sub(r"<!--.*?-->", "", text, flags=re.S))
    return [l for l in body.splitlines() if l.strip() and not l.lstrip().startswith(("#", "|", "!["))
            and not re.match(r"^\s*\|", l)]


def density(text):
    """A4 지표: 100문장당 굵게·대시·괄호. 문장은 산문 줄에서 종결부호로 센다."""
    lines = prose_lines(text)
    body = "\n".join(re.sub(r"`[^`]*`", "", l) for l in lines)
    sents = max(1, len(re.findall(r"[.!?。](\s|$)|[다요]$", body, flags=re.M)))
    per = lambda n: round(100 * n / sents, 1)
    return {
        "bold_per100s": per(len(re.findall(r"\*\*[^*]+\*\*", body))),
        "dash_per100s": per(body.count("—")),
        "paren_per100s": per(len(re.findall(r"\([^)\n]{1,80}\)", body))),
        "sentences": sents,
    }


def measure(text):
    """스크립트 항목별 찾은 곳 목록."""
    sec = sections(text)
    found = {k: [] for k in RULES["items"]}
    for f in lint_doc.lint(text):
        item = LINT_TO_ITEM.get(f.rule)
        if item and RULES["items"][item]["status"] in ("gate", "advisory"):
            line = text.splitlines()[f.line - 1]
            found[item].append({"line": f.line, "section": sec.get(f.line, ""), "quote": quote(line),
                                "text": quote(line, 400), "action": f.message})
    rep = redundancy.analyze(text)
    for f in rep["findings"]:
        item = REDUNDANCY_TO_ITEM.get(f["kind"])
        if item and RULES["items"][item]["status"] in ("gate", "advisory"):
            w = f["where"][0]
            found[item].append({"line": w["line"], "section": w["section"], "quote": quote(w["excerpt"]),
                                "text": w["excerpt"],
                                "action": (f"'{f['home']['section']}' 절과 같은 내용이다. 한 자리에만 두고 다른 자리는 그 절을 가리킨다 (E-12)."
                                           if f.get("home") else "그림에 있는 내용을 글로 다시 옮겼다. 그림 뒤에는 핵심 한 문장만 둔다 (M-3)."),
                                "raw_action": f["action"]})
    if RULES["items"].get("B2", {}).get("status") in ("gate", "advisory"):
        found["B2"] = restated_lists(text)
    d = density(text)
    limits = RULES["items"]["A4"].get("limits", {})
    over = {k: (d[k], v) for k, v in limits.items() if d[k] > v}
    if over and RULES["items"]["A4"]["status"] in ("gate", "advisory"):
        names = {"bold_per100s": "굵게", "dash_per100s": "대시", "paren_per100s": "괄호"}
        found["A4"].append({"line": 0, "section": "문서 전체", "text": "문서 전체",  # 수치가 바뀌어도 같은 찾은 곳 ID
                            "quote": ", ".join(f"{names[k]} {a}회/100문장(기준 {b})" for k, (a, b) in over.items()),
                            "action": "많이 쓴 절을 읽고 장식으로 쓴 강조를 걷는다 (L-6, L-10)."})
    return found, d


def restated_lists(text):
    """B2: 같은 대상 셋 이상을 표·목록으로 구조만 바꿔 다시 쓴 곳. 용어 절은 뺀다.

    표의 첫 열 값과 목록의 굵은 머리말(또는 '— ' 앞)을 대상 키로 모아, 두 구조의 키가
    3개 이상 겹치고 작은 쪽의 60% 이상이면 같은 대상 목록으로 본다.
    """
    body = FENCE.sub("", re.sub(r"<!--.*?-->", "", text, flags=re.S))
    norm = lambda k: re.sub(r"\s+", " ", re.sub(r"^\d+[.)]\s*", "", re.sub(r"[`*]|<br>.*$|\(.*?\)", "",
                             re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", k)).strip().lower()))[:40]
    toks = lambda k: set(re.findall(r"[a-z][a-z0-9_.-]+|[가-힣]{2,}", k))
    structs, cur, kind, start, head, header = [], [], None, 0, "", False

    def named(k):
        """번호(1, ①), 한두 글자 기호(a, #)는 대상 이름이 아니다. 이름은 한글 두 글자 이상이나 영문 세 글자 이상이다."""
        return bool(re.search(r"[가-힣]{2,}|[a-z][a-z0-9_.-]{2,}", k))

    def close():
        nonlocal cur, kind
        keys = {k for k in cur if k and named(k)}
        if len(keys) >= 3 and "용어" not in head:
            structs.append({"kind": kind, "line": start, "section": head, "keys": keys})
        cur, kind = [], None

    for no, line in enumerate(body.splitlines(), 1):
        st = line.strip()
        if st.startswith("#"):
            close(); head = st.lstrip("# ").strip(); continue
        if st.startswith("|"):
            if kind != "table":
                close(); kind, start, header = "table", no, True
            if re.match(r"^\|[\s:|-]+\|?$", st):
                continue
            if header:
                header = False; continue
            cur.append(norm(st.strip("|").split("|")[0]))
            continue
        m = re.match(r"^\s*(?:[-*]|\d+[.)])\s+(.*)$", line)
        if m:
            if kind != "list":
                close(); kind, start = "list", no
            b = re.match(r"^\*\*([^*]+)\*\*", m.group(1))
            if b:
                cur.append(norm(b.group(1)))
            elif " — " in m.group(1):
                cur.append(norm(m.group(1).split(" — ")[0]))
            continue
        if st:
            close()
    close()
    same = lambda a, b: a == b or (toks(a) and toks(b) and (toks(a) <= toks(b) or toks(b) <= toks(a)))
    found = []
    for i in range(len(structs)):
        for j in range(i + 1, len(structs)):
            a, b = structs[i], structs[j]
            shared = sorted(x for x in a["keys"] if any(same(x, y) for y in b["keys"]))
            if len(shared) >= 3 and len(shared) / min(len(a["keys"]), len(b["keys"])) >= 0.6:
                found.append({"line": b["line"], "section": b["section"], "quote": " · ".join(shared[:3]),
                              "action": f"'{a['section']}' 절의 {'표' if a['kind'] == 'table' else '목록'}와 같은 대상을 다시 쓴다. "
                                        "한 표에 열을 더해 합칠 수 있는지 본다 (E-12)."})
    return found


def preservation(before, after):
    """D1: 식별자·수치·링크·표·제목이 사라졌는지.

    값을 백틱 안팎으로 옮긴 것은 사라진 것이 아니다. 식별자는 글자가 고친 판 어디에도 없을 때,
    수치는 백틱 안팎을 합친 개수가 줄었을 때 사라진 것으로 본다. 제목 글자와 절 번호 참조(§3)는
    다른 규칙(제목 길이, 번호 참조)이 고치라고 하는 자리라 수치로 세지 않고, 제목은 수만 비교한다.
    """
    def parts(t):
        prose = FENCE.sub("", t)
        ticks = re.findall(r"`([^`\n]+)`", prose)
        links = Counter(re.findall(r"\]\(([^)]+)\)", t))
        body = re.sub(r"(?m)^#{1,6} .*$", "", prose)                  # 제목 글자는 따로 센다
        body = re.sub(r"§\s?\d+(?:\.\d+)*", "", re.sub(r"\]\([^)]*\)", "", body))  # 절 번호 참조는 사실이 아니다
        nums = Counter(re.findall(r"(?<![0-9A-Za-z_.])\d+(?:\.\d+)?(?![0-9A-Za-z_])", body))
        heads = re.findall(r"(?m)^#{1,6} .+$", t)
        tables = len(re.findall(r"(?m)^\|[\s:|-]+\|?\s*$", prose))
        return ticks, links, nums, heads, tables, prose
    b, a = parts(before), parts(after)
    lost = []
    lost += [f"식별자 `{k}`" for k in dict.fromkeys(b[0]) if k not in a[5]]
    lost += [f"링크 {k}" for k in (b[1] - a[1])]
    lost += [f"수치 {k}" for k in (b[2] - a[2])]
    if len(a[3]) < len(b[3]):  # 제목을 줄여 쓰는 것은 다른 규칙이 요구한다. 제목 수가 줄었을 때만 본다
        lost.append(f"제목 {len(b[3]) - len(a[3])}개")
    if a[4] < b[4]:
        lost.append(f"표 {b[4] - a[4]}개")
    return lost


def verdict(now, judgments, lost):
    gate_left = sum(len(v) for k, v in now.items() if RULES["items"][k]["status"] == "gate")
    unexplained_loss = lost and not any(j.get("item") == "D1" for j in judgments)
    if gate_left or unexplained_loss:
        return "미통과"
    advisory_left = sum(len(v) for k, v in now.items() if RULES["items"][k]["status"] == "advisory")
    judged_left = [j for j in judgments if j.get("outcome") in ("남김", "결정 대기", "해당")]
    if advisory_left or judged_left:
        return "손볼 곳 남음"
    return "통과"


def reason_line(v, now, judgments, lost):
    if v == "통과":
        return "스크립트가 재는 항목과 AI가 판단한 항목 모두 남은 것이 없다."
    if v == "미통과":
        n = sum(len(x) for k, x in now.items() if RULES["items"][k]["status"] == "gate")
        parts = []
        if n:
            parts.append(f"반드시 고칠 항목이 {n}곳 남았다")
        if lost and not any(j.get("item") == "D1" for j in judgments):
            parts.append(f"고치면서 사실 {len(lost)}개가 사라졌다")
        return ", ".join(parts) + "."
    explained = {x.get("item") for x in judgments if x.get("outcome") in ("남김", "결정 대기")}
    pending = sum(len(x) for k, x in now.items() if RULES["items"][k]["status"] == "advisory" and k not in explained)
    left = len([x for x in judgments if x.get("outcome") in ("남김", "해당")])
    wait = len([x for x in judgments if x.get("outcome") == "결정 대기"])
    parts = [f"이유를 적고 남긴 것 {left}곳"] if left else []
    if wait:
        parts.append(f"결정을 기다리는 것 {wait}건")
    if pending:
        parts.append(f"아직 판단하지 않은 것 {pending}곳")
    return "반드시 고칠 항목은 없다. " + ", ".join(parts) + "이 있다."


def render(path, now, first, judgments, lost, baseline_given, db=None):
    items = RULES["items"]
    v = verdict(now, judgments, lost)
    out = ["### AI slop 점검", "", f"판정: {v} — {reason_line(v, now, judgments, lost)}", ""]
    rows = []
    for k, meta in items.items():
        if meta["status"] in ("hold",) or meta.get("kind") == "judgment":
            continue
        a = len(first.get(k, [])) if baseline_given else None
        b = len(now.get(k, []))
        if (a or 0) == 0 and b == 0:
            continue
        ex = (now.get(k) or first.get(k) or [{}])[0]
        means = "스크립트 지표" if meta["status"] == "advisory" else "스크립트"
        cnt = f"{a} → {b}" if baseline_given else f"{b}"
        rows.append(f"| {meta['name']} | {means} | {cnt} | {ex.get('section', '')} · \"{ex.get('quote', '')}\" |")
    by_item = {}
    for j in judgments:
        if items.get(j.get("item"), {}).get("kind") == "judgment" and j.get("outcome") != "해당 없음":
            by_item.setdefault(j["item"], []).append(j)
    for k, js in by_item.items():
        a, b = len(js), len([j for j in js if j.get("outcome") != "고침"])
        cnt = f"{a} → {b}" if baseline_given else f"{b}"
        rows.append(f"| {items[k]['name']} | AI 판단 | {cnt} | {js[0].get('section', '')} · \"{js[0].get('quote', '')}\" |")
    if rows:
        out += ["| 항목 | 판정 수단 | " + ("처음 → 지금" if baseline_given else "찾은 곳") + " | 예 (절 · 인용) |",
                "|---|---|---|---|"] + rows + [""]
    fixed = [j for j in judgments if j.get("outcome") == "고침" and j.get("before")]
    if fixed:
        out.append("고친 것")
        seen = Counter()
        for j in fixed:
            seen[j["item"]] += 1
            if seen[j["item"]] <= 1 and sum(seen.values()) <= 5:
                out.append(f"- {items[j['item']]['name']}: \"{j['before']}\" → \"{j['after']}\"")
        out.append("")
    left = [j for j in judgments if j.get("outcome") in ("남김", "해당")]
    explained = {j.get("item") for j in judgments if j.get("outcome") in ("남김", "결정 대기")}
    advisory_unexplained = [(k, f) for k, fs in now.items() if items[k]["status"] == "advisory" for f in fs
                            if k not in explained]
    if left or advisory_unexplained:
        out.append("남긴 것")
        out += [f"- {items[j['item']]['name']} · {j.get('section', '')}: {j.get('reason', '(이유를 적지 않았다)')}" for j in left]
        out += [f"- {items[k]['name']} · {f['section']}: (아직 판단하지 않았다) {f['action']}" for k, f in advisory_unexplained]
        out.append("")
    wait = [j for j in judgments if j.get("outcome") == "결정 대기"]
    if wait:
        out.append("결정 대기")
        out += [f"- {j.get('reason', '')}" for j in wait]
        out.append("")
    if db:
        import slop_db
        con, source = db
        prior = slop_db.prior_false(con, source)
        notes = {h["finding_id"]: h.get("note") for h in slop_db.history(con, source) if h["verdict"] == "오탐"}
        again = [(k, f) for k, fs in now.items() for f in fs
                 if slop_db.finding_id(source, k, f.get("text") or f.get("quote", "")) in prior]
        if again:
            out.append("전에 사람이 오탐으로 판정한 곳 (다시 걸렸다. 고치지 않아도 된다)")
            for k, f in again:
                fid = slop_db.finding_id(source, k, f.get("text") or f.get("quote", ""))
                out.append(f"- {items[k]['name']} · {f['section']}: \"{f['quote']}\"" + (f" — {notes[fid]}" if notes.get(fid) else ""))
            out.append("")
        shown = {k for k, fs in now.items() if fs} | {j.get("item") for j in judgments}
        st = [s for s in slop_db.stats(con) if s["item"] in shown and s["item"] in items]
        if st:
            out.append("사람 판정 누적: " + ", ".join(
                f"{items[s['item']]['name']} {s['판정']}건 중 오탐 {s['오탐']}건" + (f"·놓침 {s['놓침']}건" if s["놓침"] else "")
                for s in st))
            out.append("")
    clear = [items[j["item"]]["name"] for j in judgments if j.get("outcome") == "해당 없음" and j.get("item") in items]
    if clear:
        out.append("AI가 읽고 해당 없다고 본 항목: " + ", ".join(dict.fromkeys(clear)))
        out.append("")
    if baseline_given:
        out.append("사실 보존: " + ("식별자·수치·링크·표·제목 모두 보존" if not lost else f"{len(lost)}개 달라짐 → " + ", ".join(lost[:8])))
    return "\n".join(out).rstrip() + "\n", v


def main(argv):
    args = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] not in ("--baseline", "--judgments", "--db"))]
    opts = {argv[i]: argv[i + 1] for i, a in enumerate(argv[:-1]) if a in ("--baseline", "--judgments", "--db")}
    if len(args) < 1:
        print(__doc__, file=sys.stderr)
        return 2
    path = Path(args[0])
    text = path.read_text(encoding="utf-8")
    now, dens = measure(text)
    base = opts.get("--baseline")
    first, lost = ({}, [])
    if base:
        btext = Path(base).read_text(encoding="utf-8")
        first, _ = measure(btext)
        lost = preservation(btext, text)
    judgments = json.loads(Path(opts["--judgments"]).read_text(encoding="utf-8")) if "--judgments" in opts else []
    db = None
    if "--db" in opts:
        import slop_db
        db = (slop_db.connect(), opts["--db"])
    report, v = render(path, now, first, judgments, lost, bool(base), db)
    if "--json" in argv:
        print(json.dumps({"verdict": v, "rubric_version": RULES["rubric_version"], "found": now,
                          "first": first, "density": dens, "lost": lost}, ensure_ascii=False))
    else:
        print(report, end="")
    return 1 if v == "미통과" else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
