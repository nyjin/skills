#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.1"]
# ///
"""AI slop 판정 저장소. 대상 문서, 최초 판정, 사용자 판정, 기준 변경을 로컬 DuckDB 에 남긴다.

저장소 밖(기본 ~/.claude/tech-writing/calibration/slop.duckdb, 환경 변수 TECH_WRITER_SLOP_DB 로 바꾼다)에 둔다.
회사 문서의 인용이 들어가므로 스킬 저장소에 넣지 않는다.

사용: uv run slop_db.py [--db <경로>] <명령> [인자]
  doc-add  --source <confluence:ID|file:경로> --label <human|approved|slop> --reason <이유> [--title <제목>]
  doc-list [--label <묶음>]
  record   --source <문서> --check <slop_check --json 출력> [--judgments <판단.json>] [--doc-file <문서.md>]
  findings --source <문서> [--run <실행 ID>] [--pending]       찾은 곳과 사용자 판정
  judge    --finding <ID> --verdict <동의|오탐> [--note <메모>] [--by <이름>]
  miss     --source <문서> --item <항목 ID> --quote <인용> [--section <절>] [--note <메모>] [--by <이름>]
  history  --source <문서>                                    이 문서에 남은 사용자 판정 (실행을 시작할 때 읽는다)
  stats    [--item <항목 ID>]                                 항목별 사람 판정 수 (보고의 신뢰도, 보정의 근거)
  changes                                                     기준 변경 이력
"""
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
DEFAULT_DB = Path(os.environ.get("TECH_WRITER_SLOP_DB", Path.home() / ".claude/tech-writing/calibration/slop.duckdb"))
RULES_PATH = HERE / "slop_rules.json"
VERDICTS = ("동의", "오탐")

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (source TEXT PRIMARY KEY, label TEXT, reason TEXT, title TEXT,
  holdout BOOLEAN, added_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, source TEXT, doc_hash TEXT, rubric_version INTEGER,
  rules_hash TEXT, verdict TEXT, created_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS findings (run_id TEXT, finding_id TEXT, source TEXT, item TEXT, section TEXT, quote TEXT,
  means TEXT, outcome TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS judgments (finding_id TEXT, source TEXT, item TEXT, quote TEXT, section TEXT, verdict TEXT,
  note TEXT, judged_by TEXT, rubric_version INTEGER, created_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS changes (item TEXT, field TEXT, before TEXT, after TEXT, reason TEXT, approved_by TEXT,
  created_at TIMESTAMP);
"""


def now():
    return datetime.now().replace(microsecond=0)


def rules():
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def connect(path=DEFAULT_DB):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(SCHEMA)
    return con


def rows(con, sql, params=()):
    cur = con.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def finding_id(source, item, text):
    """문서 · 항목 · 걸린 문장으로 만든다. 줄 번호는 판마다 바뀌어 넣지 않는다."""
    norm = re.sub(r"\s+", " ", re.sub(r"[*`>#|]", "", text or "")).strip()
    return hashlib.sha1(f"{source}|{item}|{norm}".encode()).hexdigest()[:10]


def add_doc(con, source, label, reason, title=None, holdout=None):
    if label not in ("human", "approved", "slop"):
        raise SystemExit("label 은 human, approved, slop 중 하나다")
    con.execute("DELETE FROM documents WHERE source = ?", (source,))
    con.execute("INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)", (source, label, reason, title, holdout, now()))


def doc_list(con, label=None):
    if label:
        return rows(con, "SELECT * FROM documents WHERE label = ? ORDER BY source", (label,))
    return rows(con, "SELECT * FROM documents ORDER BY source")


def set_holdout(con, source, holdout):
    con.execute("UPDATE documents SET holdout = ? WHERE source = ?", (holdout, source))


def record(con, source, check, judgments=(), doc_text=""):
    """slop_check --json 결과와 판단 목록을 최초 판정으로 남긴다. 실행 ID 를 돌려준다."""
    r = rules()
    rules_hash = hashlib.sha1(RULES_PATH.read_bytes()).hexdigest()[:10]
    run_id = hashlib.sha1(f"{source}|{now().isoformat()}|{len(rows(con, 'SELECT run_id FROM runs'))}".encode()).hexdigest()[:10]
    con.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, source, hashlib.sha1(doc_text.encode()).hexdigest()[:10], check.get("rubric_version"),
                 rules_hash, check.get("verdict"), now()))
    items = r["items"]
    for item, found in check.get("found", {}).items():
        means = "스크립트 지표" if items.get(item, {}).get("status") == "advisory" else "스크립트"
        for f in found:
            text = f.get("text") or f.get("quote", "")
            con.execute("INSERT INTO findings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (run_id, finding_id(source, item, text), source, item, f.get("section"), f.get("quote"),
                         means, None, None))
    for j in judgments:
        if j.get("outcome") == "해당 없음" or items.get(j.get("item"), {}).get("kind") != "judgment":
            continue
        text = j.get("quote") or j.get("before") or j.get("reason", "")
        con.execute("INSERT INTO findings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (run_id, finding_id(source, j["item"], text), source, j["item"], j.get("section"),
                     j.get("quote") or j.get("before"), "AI 판단", j.get("outcome"), j.get("reason")))
    return run_id


def latest_run(con, source):
    r = rows(con, "SELECT run_id FROM runs WHERE source = ? ORDER BY created_at DESC, rowid DESC LIMIT 1", (source,))
    return r[0]["run_id"] if r else None


def findings(con, source, run=None, pending=False):
    run = run or latest_run(con, source)
    out = rows(con, """
        SELECT f.finding_id, f.item, f.section, f.quote, f.means, f.outcome,
               (SELECT j.verdict FROM judgments j WHERE j.finding_id = f.finding_id
                ORDER BY j.created_at DESC, j.rowid DESC LIMIT 1) AS judged
        FROM findings f WHERE f.run_id = ? ORDER BY f.rowid""", (run,))
    return [f for f in out if not (pending and f["judged"])]


def judge(con, fid, verdict, note=None, by=None):
    if verdict not in VERDICTS:
        raise SystemExit(f"verdict 는 {', '.join(VERDICTS)} 중 하나다. 놓친 곳은 miss 로 남긴다")
    f = rows(con, "SELECT source, item, quote, section FROM findings WHERE finding_id = ? LIMIT 1", (fid,))
    if not f:
        raise SystemExit(f"찾은 곳 {fid} 가 없다. findings 로 ID 를 확인한다")
    f = f[0]
    con.execute("INSERT INTO judgments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (fid, f["source"], f["item"], f["quote"], f["section"], verdict, note, by,
                 rules()["rubric_version"], now()))


def miss(con, source, item, quote, section=None, note=None, by=None):
    if item not in rules()["items"]:
        raise SystemExit(f"루브릭에 없는 항목: {item}")
    con.execute("INSERT INTO judgments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (finding_id(source, item, quote), source, item, quote, section, "놓침", note, by,
                 rules()["rubric_version"], now()))


def history(con, source):
    return rows(con, """SELECT finding_id, item, section, quote, verdict, note, judged_by, created_at
                        FROM judgments WHERE source = ? ORDER BY created_at, rowid""", (source,))


def prior_false(con, source):
    """이 문서에서 사람이 오탐으로 판정한 찾은 곳 ID. 실행 중에 다시 걸리면 보고에 표시한다."""
    return {r["finding_id"] for r in history(con, source) if r["verdict"] == "오탐"}


def stats(con, item=None):
    where, params = ("WHERE item = ?", (item,)) if item else ("", ())
    return rows(con, f"""SELECT item, COUNT(*) AS 판정,
                           SUM(CASE WHEN verdict = '동의' THEN 1 ELSE 0 END) AS 동의,
                           SUM(CASE WHEN verdict = '오탐' THEN 1 ELSE 0 END) AS 오탐,
                           SUM(CASE WHEN verdict = '놓침' THEN 1 ELSE 0 END) AS 놓침
                         FROM judgments {where} GROUP BY item ORDER BY item""", params)


def log_change(con, item, field, before, after, reason, by=None):
    con.execute("INSERT INTO changes VALUES (?, ?, ?, ?, ?, ?, ?)",
                (item, field, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False),
                 reason, by, now()))


def changes(con):
    out = rows(con, "SELECT * FROM changes ORDER BY created_at, rowid")
    for r in out:
        r["before"], r["after"] = json.loads(r["before"]), json.loads(r["after"])
    return out


def table(rows_, cols):
    names = rules()["items"]
    head = "| " + " | ".join(c for _, c in cols) + " |"
    out = [head, "|" + "---|" * len(cols)]
    for r in rows_:
        vals = []
        for k, _ in cols:
            v = r.get(k)
            if k == "item":
                v = f"{names.get(v, {}).get('name', v)}"
            vals.append("" if v is None else str(v).replace("|", "\\|"))
        out.append("| " + " | ".join(vals) + " |")
    return "\n".join(out)


def main(argv):
    path = DEFAULT_DB
    if "--db" in argv:
        i = argv.index("--db"); path = Path(argv[i + 1]); argv = argv[:i] + argv[i + 2:]
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    cmd, rest = argv[0], argv[1:]
    opt = lambda k, d=None: rest[rest.index(k) + 1] if k in rest else d
    con = connect(path)
    if cmd == "doc-add":
        add_doc(con, opt("--source"), opt("--label"), opt("--reason"), opt("--title"))
    elif cmd == "doc-list":
        print(table(doc_list(con, opt("--label")), [("source", "문서"), ("label", "묶음"), ("holdout", "검증용"),
                                                     ("reason", "이유")]))
    elif cmd == "record":
        check = json.loads(Path(opt("--check")).read_text(encoding="utf-8"))
        judg = json.loads(Path(opt("--judgments")).read_text(encoding="utf-8")) if opt("--judgments") else []
        text = Path(opt("--doc-file")).read_text(encoding="utf-8") if opt("--doc-file") else ""
        print(record(con, opt("--source"), check, judg, text))
    elif cmd == "findings":
        print(table(findings(con, opt("--source"), opt("--run"), "--pending" in rest),
                    [("finding_id", "ID"), ("item", "항목"), ("section", "절"), ("quote", "인용"), ("means", "판정 수단"),
                     ("judged", "판정")]))
    elif cmd == "judge":
        judge(con, opt("--finding"), opt("--verdict"), opt("--note"), opt("--by"))
    elif cmd == "miss":
        miss(con, opt("--source"), opt("--item"), opt("--quote"), opt("--section"), opt("--note"), opt("--by"))
    elif cmd == "history":
        print(table(history(con, opt("--source")), [("item", "항목"), ("section", "절"), ("quote", "인용"),
                                                     ("verdict", "판정"), ("note", "메모"), ("created_at", "날짜")]))
    elif cmd == "stats":
        print(table(stats(con, opt("--item")), [("item", "항목"), ("판정", "판정"), ("동의", "동의"), ("오탐", "오탐"),
                                                 ("놓침", "놓침")]))
    elif cmd == "changes":
        print(table(changes(con), [("item", "항목"), ("field", "바꾼 값"), ("before", "전"), ("after", "후"),
                                   ("reason", "이유"), ("approved_by", "승인"), ("created_at", "날짜")]))
    else:
        print(f"알 수 없는 명령: {cmd}\n{__doc__}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
