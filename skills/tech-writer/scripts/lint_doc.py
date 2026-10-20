#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""기술 문서 Markdown 기계 검사.

사용: uv run lint_doc.py <file.md> [--json]
종료 코드: 오류(error)가 하나라도 있으면 1, 아니면 0.
"""
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

TITLE_MAX = 30
FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*(.*)$")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
CODE_LOCATION = re.compile(
    r"[\w./-]+\.(?:java|kt|kts|py|ts|tsx|js|jsx|go|rb|rs|cs|swift|scala|php|cc|cpp|c|h|hpp|sql|gradle):\d+"
)
BOLD_LEAD = re.compile(r"^\s*([-*+]|\d+[.)])\s+\*\*[^*]+\*\*")
BOLD_LEAD_MIN = 3  # 굵은 요약으로 시작하는 목록 항목이 이만큼 이어지면 표로 바꿀 후보다
# 문단·인용을 굵은 결정 문장으로 연다(**정산은 하루 한 번 확정한다.** …). 근거는 slop_rules.json 이 갖는다.
BOLD_VERDICT = re.compile(r"^(?:>\s*)?(?:`[^`]+`)?\*\*[^*]{2,}?(?:다|요)\.?\*\*")
# 산문에서 기호로 말을 잇는다(정산 확정 ≠ 송금 완료, A vs B, 회차 = 송금 묶음, 가맹점 + 담당자). 숫자 계산은 제외한다.
# 백틱 밖에서 한글과 영문이 번갈아 나오는 횟수. 한 문장에 이만큼이면 산문·용어·코드가 뒤섞인 것이다.
# 기본값이다. 실제 임계값과 근거는 slop_rules.json 이 갖는다.
SCRIPT_SWITCH = re.compile(r"[가-힣][^가-힣A-Za-z]*[A-Za-z]|[A-Za-z][^가-힣A-Za-z]*[가-힣]")
SCRIPT_SWITCH_MAX = 10


def load_slop_rules(path: Path) -> dict:
    """slop_rules.json 에서 lint 규칙별 상태·임계값을 읽는다. 없거나 깨졌으면 빈 dict(위 기본값으로 돈다)."""
    try:
        items = json.loads(path.read_text(encoding="utf-8"))["items"]
    except (OSError, ValueError, KeyError):
        return {}
    return {v["lint_rule"]: {"status": v.get("status"), "threshold": v.get("threshold")}
            for v in items.values() if v.get("lint_rule")}


SLOP_RULES = load_slop_rules(Path(__file__).resolve().parent / "slop_rules.json")


def slop_on(rule: str) -> bool:
    """보류·끔 상태인 규칙은 경고하지 않는다. 데이터에 없는 규칙은 켠다."""
    return SLOP_RULES.get(rule, {}).get("status", "gate") in ("gate", "advisory")
SYMBOL_OP = re.compile(r"≠|(?<![A-Za-z])vs\.?(?![A-Za-z])|[가-힣)] = [가-힣(]|[가-힣)] \+ [가-힣(A-Za-z]")
# 소스 파일을 직접 가리키는 링크(…/blob/<ref>/…/Foo.java#L10-L20). 저장소·PR·문서 링크는 해당하지 않는다.
FILE_LINK = re.compile(
    r"https?://[^\s)\"']+/(?:blob|tree|src)/[^\s)\"']+\.(?:java|kt|kts|py|ts|tsx|js|jsx|go|rb|rs|cs|swift|scala|php|cc|cpp|c|h|hpp|sql|gradle)(?:#L\d+(?:-L\d+)?)?"
)
# 티켓 키(PAY-4553)와 결정 기록 번호(ADR 0067). 표준·인코딩 이름(SHA-256, UTF-8)은 참조가 아니다.
# 경계는 ASCII 로 본다. \w 는 한글도 포함해 "ADR 0067이"처럼 조사를 붙인 번호를 놓친다.
EXTERNAL_REF = re.compile(r"(?<![0-9A-Za-z_/-])(?:([A-Z][A-Z0-9]{1,9})-\d{2,}|ADR[ -]?\d{2,4})(?![0-9A-Za-z_-])")
NOT_REF_PREFIX = {"SHA", "AES", "UTF", "ISO", "MD", "CRC", "HS", "RS", "ES", "PS", "TLS", "X", "IEC", "IEEE", "PKCS", "RFC"}
ABBR = re.compile(r"(?<![A-Za-z0-9_])([A-Z]{2}[A-Z0-9]{0,4})(?![A-Za-z0-9_])")
# 널리 알려져 풀어 쓰지 않아도 되는 약어. 독자에 따라 다르면 문서에서 풀어 쓴다.
COMMON_ABBR = {
    "API", "URL", "URI", "HTTP", "HTTPS", "JSON", "YAML", "SQL", "CLI", "UI", "UX", "ID", "DB",
    "OS", "CPU", "GPU", "PR", "CI", "CD", "KST", "UTC", "UUID", "TODO", "README", "IDE", "SDK",
    "AWS", "GCP", "CSS", "HTML", "JS", "TS", "PDF", "CSV", "SSH", "TLS", "SSL", "DNS", "IP",
    "TCP", "UDP", "VM", "OK", "FAQ", "QA", "PM", "CS", "TL", "DR", "AI", "SVG", "PNG",
}


@dataclass
class Finding:
    line: int
    rule: str
    level: str
    message: str


def visible_len(title: str) -> int:
    parts = re.split(r"(`[^`]*`)", title)
    for i, part in enumerate(parts):
        if part.startswith("`"):
            parts[i] = part.strip("`")
        else:
            part = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", part)
            parts[i] = re.sub(r"(\*\*|\*)(.+?)\1", r"\2", part)
    return len("".join(parts).strip())


def check_abbr(line: str, no: int, seen: set[str], findings: list[Finding]) -> None:
    body = re.sub(r"`[^`]*`", "", line)
    body = re.sub(r"\]\([^)]*\)", "]", body)
    for m in ABBR.finditer(body):
        tok = m.group(1)
        if tok in COMMON_ABBR or tok in seen:
            continue
        seen.add(tok)
        expanded = body[m.end():m.end() + 1] == "(" or re.search(r"\([^)]*\b" + tok + r"\)", body)
        if not expanded:
            findings.append(Finding(no, "abbr-first", "warn", f"약어 {tok}가 처음 나온다. 풀어 쓴 이름을 함께 쓴다. 예) {tok}(풀어 쓴 이름)"))


def check_external_ref(line: str, no: int, findings: list[Finding]) -> None:
    body = re.sub(r"\[[^\]]*\]\([^)]*\)", "", line)
    body = re.sub(r"`[^`]*`", "", body)
    refs = [m.group(0) for m in EXTERNAL_REF.finditer(body) if m.group(1) not in NOT_REF_PREFIX]
    if refs:
        findings.append(Finding(no, "unlinked-ref", "warn",
                                f"링크 없는 참조 번호 {', '.join(refs)}. 나오는 자리마다 [번호](주소)로 링크하거나, 내용으로 바꾸거나 지운다 (E-10)."))


def check_slop(line: str, no: int, findings: list[Finding]) -> None:
    s = line.strip()
    if s.startswith("|"):
        return
    is_item = bool(re.match(r"^([-*+]|\d+[.)])\s", s))
    if not is_item and slop_on("bold-verdict") and BOLD_VERDICT.match(s):
        findings.append(Finding(no, "bold-verdict", "warn",
                                "문단을 굵은 결정 문장으로 연다. 굵게를 풀고 결정을 평서문 첫 문장으로 쓴다. "
                                "결정이 여럿 이어지면 '결정 | 이유' 표로 모은다 (L-11)."))
    body = re.sub(r"`[^`]*`", " ", s)
    body = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", body)
    plain = re.sub(r"https?://\S+", " ", body)
    switches = max((len(SCRIPT_SWITCH.findall(x)) for x in re.split(r"(?<=[다요])\.\s", plain)), default=0)
    limit = SLOP_RULES.get("mixed-script", {}).get("threshold") or SCRIPT_SWITCH_MAX
    if slop_on("mixed-script") and switches >= limit:
        findings.append(Finding(no, "mixed-script", "warn",
                                f"한 문장에서 한글과 영문이 {switches}번 바뀐다. 식별자는 백틱으로 묶어 표로 빼고, "
                                "일반 낱말은 한국어로 쓰고, 영어 용어는 용어 절에서 한 번 정의한 뒤 한 표기만 쓴다 (H-6)."))
    m = SYMBOL_OP.search(body) if slop_on("symbol-operator") else None
    if m:
        sym = re.search(r"≠|vs|=|\+", m.group(0)).group(0)
        findings.append(Finding(no, "symbol-operator", "warn",
                                f"기호 '{sym}'로 말을 잇는다. 조사와 서술어로 풀어 쓴다. "
                                "예) A ≠ B → A는 B가 아니다, A vs B → A와 B 중, A + B → A와 B (G-5)."))


DOC_TYPES = ("investigation",)
BASELINE = re.compile(r"^\s*[-*]\s*기준\s*:")
BASELINE_REF = re.compile(r"`[^`]+`|\b[0-9a-f]{7,40}\b")


def check_baseline(text: str, findings: list[Finding]) -> None:
    """조사 보고는 첫 H2 앞에 저장소·브랜치 또는 커밋 기준 줄을 둔다 (E-11 예외)."""
    h1_line, ok = 1, False
    for no, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            break
        if line.startswith("# "):
            h1_line = no
        if BASELINE.match(line) and BASELINE_REF.search(line):
            ok = True
    if not ok:
        findings.append(Finding(h1_line, "missing-baseline", "warn",
                                "조사 보고는 맨 위에 저장소와 브랜치 또는 커밋 기준을 적는다 (E-11)."))


def lint(text: str, doc_type: str | None = None) -> list[Finding]:
    findings: list[Finding] = []
    fence: str | None = None
    fence_line = 0
    prev_level = 0
    h1_seen = False
    seen_abbr: set[str] = set()
    bold_run: list[int] = []

    def flush_bold_run():
        if len(bold_run) >= BOLD_LEAD_MIN:
            findings.append(Finding(bold_run[0], "bold-lead-list", "warn",
                                    f"굵은 요약으로 시작하는 목록 항목이 {len(bold_run)}개 이어진다. "
                                    "결정과 이유라면 '결정 | 이유' 표로 바꾼다 (L-10)."))
        bold_run.clear()

    for no, line in enumerate(text.splitlines(), 1):
        if fence is None and not FENCE.match(line):
            if BOLD_LEAD.match(line):
                bold_run.append(no)
            elif line.strip() and not line.startswith((" ", "\t")):
                flush_bold_run()
        m = FENCE.match(line)
        if fence is None and m:
            fence, fence_line = m.group(1), no
            if not m.group(2).strip():
                findings.append(Finding(no, "code-lang", "error", "코드 블록에 언어를 지정한다 (예: ```bash)."))
            continue
        if fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not m.group(2).strip():
                fence = None
            continue

        if doc_type != "investigation" and FILE_LINK.search(line) and not line.lstrip().startswith("<!--"):
            findings.append(Finding(no, "file-link", "warn",
                                    "소스 파일 링크가 있다. 파일별 링크 대신 이름과 역할을 쓰고, 참고 절에 저장소·PR 링크를 둔다 (E-11)."))
        if CODE_LOCATION.search(line) and not line.lstrip().startswith("<!--"):
            findings.append(Finding(no, "code-location", "warn",
                                    "파일 경로·라인 근거가 있다. 본문은 이름과 역할로 쓰고, 근거는 참고 절의 저장소·PR 링크로 모은다 (E-11)."))
        h = HEADING.match(line)
        if not h:
            if not line.lstrip().startswith("<!--"):
                check_abbr(line, no, seen_abbr, findings)
                check_external_ref(line, no, findings)
                check_slop(line, no, findings)
            continue
        level, title = len(h.group(1)), h.group(2)
        if level == 1:
            if h1_seen:
                findings.append(Finding(no, "multiple-h1", "warn", "H1은 문서 제목 하나만 둔다."))
            h1_seen = True
        if prev_level and level > prev_level + 1:
            findings.append(Finding(no, "heading-skip", "warn", f"H{prev_level} 다음에 H{level}이 온다. 단계를 건너뛰지 않는다."))
        if level >= 4:
            findings.append(Finding(no, "heading-depth", "warn", "H4 이상이다. 문서 분리를 고려한다."))
        if visible_len(title) > TITLE_MAX:
            findings.append(Finding(no, "heading-length", "warn", f"제목이 {visible_len(title)}자다. {TITLE_MAX}자 이내를 권장한다."))
        if title.rstrip().endswith(("?", "!")):
            findings.append(Finding(no, "heading-punct", "warn", "제목은 물음표·느낌표 없이 평서문으로 쓴다."))
        prev_level = level

    flush_bold_run()
    if doc_type == "investigation":
        check_baseline(text, findings)
    if fence is not None:
        findings.append(Finding(fence_line, "unclosed-fence", "error", "코드 블록이 닫히지 않았다."))
    return findings


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--json"]
    doc_type = None
    if "--type" in args:
        i = args.index("--type")
        doc_type = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
        if doc_type not in DOC_TYPES:
            print(f"알 수 없는 --type: {doc_type} (가능: {', '.join(DOC_TYPES)})", file=sys.stderr)
            return 2
    if len(args) != 1:
        print("사용: lint_doc.py <file.md> [--json] [--type investigation]", file=sys.stderr)
        return 2
    try:
        with open(args[0], encoding="utf-8") as f:
            findings = lint(f.read(), doc_type)
    except (OSError, UnicodeDecodeError) as e:
        print(f"파일을 읽을 수 없다: {args[0]} ({e})", file=sys.stderr)
        return 2
    if "--json" in argv:
        print(json.dumps([asdict(x) for x in findings], ensure_ascii=False))
    else:
        for x in findings:
            print(f"L{x.line} [{x.level}] {x.rule}: {x.message}")
    return 1 if any(x.level == "error" for x in findings) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
