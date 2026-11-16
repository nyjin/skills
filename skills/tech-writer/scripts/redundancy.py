#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["kiwipiepy>=0.24.0"]
# ///
"""같은 내용을 여러 층에 되풀이한 곳을 찾아, 고칠 곳 목록으로 낸다.

문서를 절(H2) × 층(요약·산문·목록·표·그림·코드)으로 나눠 네 가지를 잰다.

- identifier-spread: 같은 식별자가 층 3개 이상에 나온다
- repeated-claim / similar-claim: 다른 절의 문장·표 칸이 겹친다. 유사도는 글자 4-gram Jaccard 와
  Kiwi 형태소 분석으로 뽑은 내용어(명사·동사·형용사 어간) Jaccard 의 평균이다. Kiwi 를 불러올 수 없으면
  글자 4-gram 만 쓴다
- code-transcribed: 코드 블록의 이름 대부분을 같은 절 산문이 다시 늘어놓는다
- diagram-transcribed: 그림의 노드 이름 대부분을 같은 절 글이 다시 늘어놓는다

항목마다 정의 자리(남길 곳)와 할 일을 규칙으로 정해 붙인다. 판정은 휴리스틱이라
표현을 바꾼 반복은 놓칠 수 있고, 걸린 항목도 문맥을 보고 고친다.

사용: uv run redundancy.py <file.md> [--json]
종료 코드: 0(검사 완료), 2(파일을 읽을 수 없음).
"""
import json
import re
import sys

LAYER_MIN = 3          # 식별자가 이만큼의 층에 나오면 걸린다
# 임계값은 사람이 판정한 152쌍으로 정했다. 4gram+kiwi 0.35 에서 정밀도 0.96 · 재현율 0.52,
# 0.25 까지 넓히면 재현율 0.72(정밀도 0.65)다. 표현을 완전히 바꾼 반복은 이 방식으로 잡히지 않는다.
STRONG = 0.35          # 이 유사도 이상이면 정리한다
WEAK = 0.25            # 이 유사도 이상이면 검토한다
KIWI_WEIGHT = 0.5      # 유사도에서 형태소 쪽 비중
CONTENT_TAGS = ("NNG", "NNP", "VV", "VA", "XR", "SL", "SN")
FIGURE_INTRO = re.compile(r"^(다음|아래|위)\s*(두\s*|세\s*)?(그림|표|시퀀스|흐름|순서도)")
UNIT_MIN = 18          # 공백을 뺀 글자 수가 이보다 짧은 문장·칸은 비교하지 않는다
TRANSCRIBE = 0.6       # 코드·그림 이름 가운데 이 비율 이상을 글이 다시 쓰면 걸린다

FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*(\S*)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
CAMEL = re.compile(r"(?<![A-Za-z0-9_])[A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)+(?![A-Za-z0-9_])")
LOWER_CAMEL = re.compile(r"(?<![A-Za-z0-9_])[a-z]+[A-Z][A-Za-z0-9]*(?![A-Za-z0-9_])")
CALL = re.compile(r"\.?([A-Za-z_][A-Za-z0-9_]*)\s*\(")
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
NODE_LABEL = re.compile(r"[\[(]{1,2}\"?([^\"\[\]()]+?)\"?[\])]{1,2}")
STOP_CALLS = {"disable", "enable", "build", "get", "set", "of", "from", "and", "with", "new", "return",
              "toString", "equals", "if", "for", "while", "switch", "catch", "map", "orElse", "orElseGet"}


def parse(text):
    """줄마다 (줄 번호, H2 절, 블록 제목, 층, 원문)을 낸다. 코드·그림은 블록 단위로도 모은다."""
    units, blocks = [], []
    h2, block = "머리", "머리"
    lines = text.splitlines()
    sep = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")
    header_rows = {i + 1 for i in range(len(lines) - 1)
                   if lines[i].lstrip().startswith("|") and "-" in lines[i + 1] and sep.match(lines[i + 1])}
    fence, fence_lang, fence_start, fence_lines = None, "", 0, []
    for no, line in enumerate(text.splitlines(), 1):
        m = FENCE.match(line)
        if fence is None and m:
            fence, fence_lang, fence_start, fence_lines = m.group(1), m.group(2).lower(), no, []
            continue
        if fence is not None:
            if m and m.group(1)[0] == fence[0] and not m.group(2):
                layer = "그림" if fence_lang == "mermaid" else "코드"
                blocks.append({"line": fence_start, "end": no, "h2": h2, "block": block,
                               "layer": layer, "text": "\n".join(fence_lines)})
                for i, fl in enumerate(fence_lines, fence_start + 1):
                    units.append({"line": i, "h2": h2, "block": block, "layer": layer, "text": fl})
                fence = None
            else:
                fence_lines.append(line)
            continue
        h = HEADING.match(line)
        if h:
            level, title = len(h.group(1)), h.group(2).strip()
            if level == 2:
                h2 = block = title
            elif level >= 3:
                block = title
            continue
        s = line.strip()
        if not s or s.startswith("<!--") or re.fullmatch(r"\|?[\s:|-]+\|?", s):
            continue
        if s.startswith(">"):
            layer = "요약"
        elif s.startswith("|"):
            layer = "표"
        elif re.match(r"^([-*+]|\d+[.)])\s", s):
            layer = "목록"
        else:
            layer = "산문"
        units.append({"line": no, "h2": h2, "block": block, "layer": layer, "text": line,
                      "header": no in header_rows})
    return units, blocks


def is_appendix(h2):
    return h2.startswith("부록")


def is_reference(h2):
    """끝의 참고 절은 링크를 모은 자리다(E-10, E-11). 링크 설명에 나오는 이름은 반복으로 세지 않는다."""
    return re.sub(r"^[\d.\s]+", "", h2).startswith(("참고", "참조"))


def is_summary(unit):
    return unit["layer"] == "요약" or "요약" in unit["h2"]


def place(u):
    return {"line": u["line"], "section": u["h2"], "layer": u["layer"], "excerpt": excerpt(u["text"])}


def excerpt(text, n=60):
    t = re.sub(r"\s+", " ", re.sub(r"[|>*`#]", " ", text)).strip()
    return t if len(t) <= n else t[:n] + "…"


def identifier_spread(units):
    seen = {}
    for u in units:
        if is_appendix(u["h2"]) or is_reference(u["h2"]):
            continue  # 부록은 코드의, 참고 절은 링크의 정의 자리다(E-12). 그 안의 이름은 반복으로 세지 않는다
        for tok in set(CAMEL.findall(u["text"])):
            seen.setdefault(tok, []).append(u)
    findings, max_layers = [], 0
    for tok, us in seen.items():
        layers = {u["layer"] for u in us}
        max_layers = max(max_layers, len(layers))
        if len(layers) < LAYER_MIN:
            continue
        tables = [u for u in us if u["layer"] == "표"]
        first_cell = [u for u in tables if tok in (u["text"].split("|")[1:2] or [""])[0]]
        home = (first_cell or tables or us)[0]
        where = sorted({u["line"]: u for u in us
                        if u["line"] != home["line"] and u["layer"] in ("산문", "목록", "요약", "표")}.values(),
                       key=lambda u: u["line"])
        if not where:
            continue
        findings.append({
            "kind": "identifier-spread", "subject": tok, "layers": sorted(layers),
            "home": place(home), "where": [place(u) for u in where],
            "action": (f"`{tok}`의 이름과 역할은 정의 자리 L{home['line']}({home['h2']}/{home['layer']})에 둔다. "
                       f"반복 줄에서는 역할을 나타내는 한국어 이름으로 부르고, 이름이 꼭 필요하면 정의 자리를 가리킨다. "
                       f"그림과 부록의 코드는 그대로 둔다."),
            "score": len(layers),
        })
    return findings, max_layers


_KIWI = []


def _kiwi():
    """Kiwi 인스턴스를 한 번만 만든다. 설치돼 있지 않으면 None."""
    if not _KIWI:
        try:
            from kiwipiepy import Kiwi
            _KIWI.append(Kiwi())
        except Exception:
            _KIWI.append(None)
    return _KIWI[0]


def content_morphs(kiwi, text):
    return {(t.form.lower() if t.tag == "SL" else t.form) for t in kiwi.tokenize(text) if t.tag.startswith(CONTENT_TAGS)}


def compress_latin(text, vocab):
    """영어 낱말 하나를 한 글자로 줄인다. 긴 고유명사 하나가 유사도를 끌어올리지 못하게 한다."""
    return re.sub(r"[A-Za-z][A-Za-z0-9_.-]*",
                  lambda m: vocab.setdefault(m.group(0).lower(), chr(0xE000 + len(vocab))), text)


def claim_units(units):
    out, vocab = [], {}
    for u in units:
        if u["layer"] in ("코드", "그림") or u.get("header"):
            continue
        parts = u["text"].split("|") if u["layer"] == "표" else re.split(r"(?<=[다요음함])\.\s+", u["text"])
        for p in parts:
            clean = re.sub(r"`[^`]*`|\[([^\]]*)\]\([^)]*\)|[*>#]|^\s*[-+]\s|^\s*\d+[.)]\s", r"\1", p)
            key = compress_latin(re.sub(r"\s", "", clean), vocab)
            if len(key) >= UNIT_MIN and not FIGURE_INTRO.match(clean.strip()):
                out.append((u, {key[i:i + 4] for i in range(len(key) - 3)}, clean.strip()))
    return out


def repeated_claims(units):
    cu = claim_units(units)
    kiwi = _kiwi()
    morphs = [content_morphs(kiwi, c[2]) for c in cu] if kiwi else None
    findings, strong, weak, done = [], 0, 0, set()
    for i in range(len(cu)):
        for j in range(i + 1, len(cu)):
            (a, ga, ta), (b, gb, tb) = cu[i], cu[j]
            if a["h2"] == b["h2"] or is_summary(a) or is_summary(b):
                continue
            sim = len(ga & gb) / max(1, len(ga | gb))
            if morphs is not None:
                ma, mb = morphs[i], morphs[j]
                sim = (1 - KIWI_WEIGHT) * sim + KIWI_WEIGHT * len(ma & mb) / max(1, len(ma | mb))
            if sim < WEAK:
                continue
            if is_appendix(a["h2"]) != is_appendix(b["h2"]):
                home, other = (b, a) if is_appendix(a["h2"]) else (a, b)
            else:
                home, other = a, b
            key = (home["line"], other["line"])
            if key in done:
                continue
            done.add(key)
            kind = "repeated-claim" if sim >= STRONG else "similar-claim"
            strong += kind == "repeated-claim"
            weak += kind == "similar-claim"
            verb = "같다" if kind == "repeated-claim" else "비슷하다(검토)"
            findings.append({
                "kind": kind, "similarity": round(sim, 2), "home": place(home), "where": [place(other)],
                "action": (f"L{other['line']}({other['h2']}/{other['layer']})의 내용이 정의 자리 "
                           f"L{home['line']}({home['h2']}/{home['layer']})와 {verb}. "
                           f"반복 쪽에서는 지우고 '{home['h2']}' 절을 가리키거나, 그 자리에만 필요한 차이만 남긴다."),
                "score": sim,
            })
    return findings, strong, weak


def code_tokens(code):
    toks = set(CAMEL.findall(code)) | set(LOWER_CAMEL.findall(code)) | set(CALL.findall(code))
    return {t for t in toks if t not in STOP_CALLS and len(t) >= 3}


def transcribed(units, blocks):
    findings, code_n, diagram_n = [], 0, 0
    for b in blocks:
        prose = [u for u in units if u["block"] == b["block"] and u["h2"] == b["h2"]
                 and u["layer"] not in ("코드", "그림")]
        words = set()
        for u in prose:
            words |= set(WORD.findall(u["text"]))
            words |= {w.strip() for w in re.findall(r"[^|`,·]+", u["text"])}
        if b["layer"] == "코드":
            toks = code_tokens(b["text"])
            if len(toks) < 3:
                continue
            hit = sorted(t for t in toks if t in words)
            ratio = len(hit) / len(toks)
            if ratio < TRANSCRIBE:
                continue
            code_n += 1
            lines = sorted({u["line"] for u in prose if any(t in u["text"] for t in hit)})
            findings.append({
                "kind": "code-transcribed", "ratio": round(ratio, 2),
                "where": [{"line": b["line"], "section": b["h2"], "layer": "코드",
                           "excerpt": f"L{b['line']}–L{b['end']} 코드, 겹치는 이름: {', '.join(hit[:6])}"}],
                "prose_lines": lines, "home": None,
                "action": (f"L{b['line']}–L{b['end']} 코드와 산문 L{', L'.join(map(str, lines))}이 같은 내용을 "
                           f"두 번 보인다(겹침 {ratio:.0%}). 본문에는 이유만 남기고 코드는 부록으로 옮기거나, "
                           f"코드 앞 한 문장만 남기고 산문의 설정 나열을 지운다."),
                "score": ratio,
            })
        else:
            labels = {re.split(r"<br\s*/?>", m)[0].strip() for m in NODE_LABEL.findall(b["text"])}
            labels = {x for x in labels if len(x) >= 3}
            if len(labels) < 4:
                continue
            after = [u for u in prose if u["line"] > b["end"]]
            hit = sorted(x for x in labels if any(x in u["text"] for u in after))
            ratio = len(hit) / len(labels)
            if ratio < TRANSCRIBE:
                continue
            diagram_n += 1
            findings.append({
                "kind": "diagram-transcribed", "ratio": round(ratio, 2),
                "where": [{"line": b["line"], "section": b["h2"], "layer": "그림",
                           "excerpt": f"노드 {len(labels)}개 중 {len(hit)}개를 그림 뒤 글이 다시 늘어놓는다"}],
                "home": None,
                "action": ("그림 뒤에는 핵심 한 문장만 둔다. 표가 필요하면 그림에 없는 속성(단계, 이유)만 남기고, "
                           "같은 목록을 두 번 보이지 않는다."),
                "score": ratio,
            })
    return findings, code_n, diagram_n


ORDER = {"repeated-claim": 0, "identifier-spread": 1, "code-transcribed": 2, "diagram-transcribed": 3, "similar-claim": 4}


def analyze(text):
    units, blocks = parse(text)
    spread, max_layers = identifier_spread(units)
    claims, strong, weak = repeated_claims(units)
    trans, code_n, diagram_n = transcribed(units, blocks)
    findings = sorted(spread + claims + trans, key=lambda f: (ORDER[f["kind"]], -f["score"]))
    for i, f in enumerate(findings, 1):
        f["id"] = f"R{i}"
    metrics = {
        "similarity_mode": "4gram+kiwi" if _kiwi() else "4gram",
        "identifier_max_layers": max_layers,
        "identifiers_spread": len(spread),
        "repeated_claims_strong": strong,
        "repeated_claims_weak": weak,
        "code_transcribed": code_n,
        "diagram_transcribed": diagram_n,
    }
    return {"metrics": metrics, "findings": findings}


METRIC_NAMES = {
    "similarity_mode": "주장 유사도 방식 (4gram+kiwi 권장, 4gram 이면 형태소 분석 미사용)",
    "identifier_max_layers": f"식별자 최대 층 수 (목표 {LAYER_MIN - 1} 이하)",
    "identifiers_spread": f"층 {LAYER_MIN}개 이상에 퍼진 식별자 (목표 0)",
    "repeated_claims_strong": f"다른 절에서 되풀이된 주장, 유사도 {STRONG} 이상 (목표 0)",
    "repeated_claims_weak": f"비슷한 주장, 유사도 {WEAK}~{STRONG} (검토)",
    "code_transcribed": "코드를 산문으로 다시 옮긴 블록 (목표 0)",
    "diagram_transcribed": "그림을 글로 다시 옮긴 곳 (목표 0)",
}


def to_markdown(report, limit=40):
    out = ["## 요약 지표", "", "| 지표 | 값 |", "|---|---|"]
    out += [f"| {METRIC_NAMES[k]} | {v} |" for k, v in report["metrics"].items()]
    out += ["", "## 고칠 곳 (우선순위순)", ""]
    if not report["findings"]:
        out.append("없음")
    for f in report["findings"][:limit]:
        tag = f.get("subject") or f.get("similarity") or f.get("ratio")
        out.append(f"### {f['id']} {f['kind']} · {tag}")
        if f.get("home"):
            h = f["home"]
            out.append(f"- 정의 자리: L{h['line']} ({h['section']} / {h['layer']}) «{h['excerpt']}»")
        for w in f["where"][:8]:
            out.append(f"- 반복: L{w['line']} ({w['section']} / {w['layer']}) «{w['excerpt']}»")
        if len(f["where"]) > 8:
            out.append(f"- 반복: 외 {len(f['where']) - 8}곳 (--json 으로 전체)")
        out.append(f"- 할 일: {f['action']}")
        out.append("")
    if len(report["findings"]) > limit:
        out.append(f"외 {len(report['findings']) - limit}개 (--json 으로 전체)")
    return "\n".join(out)


def main(argv):
    args = [a for a in argv if a != "--json"]
    if len(args) != 1:
        print("사용: redundancy.py <file.md> [--json]", file=sys.stderr)
        return 2
    try:
        with open(args[0], encoding="utf-8") as f:
            report = analyze(f.read())
    except (OSError, UnicodeDecodeError) as e:
        print(f"파일을 읽을 수 없다: {args[0]} ({e})", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2) if "--json" in argv else to_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
