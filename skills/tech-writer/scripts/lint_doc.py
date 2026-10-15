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

TITLE_MAX = 30
FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*(.*)$")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
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


def lint(text: str) -> list[Finding]:
    findings: list[Finding] = []
    fence: str | None = None
    fence_line = 0
    prev_level = 0
    h1_seen = False
    seen_abbr: set[str] = set()

    for no, line in enumerate(text.splitlines(), 1):
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

        h = HEADING.match(line)
        if not h:
            if not line.lstrip().startswith("<!--"):
                check_abbr(line, no, seen_abbr, findings)
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

    if fence is not None:
        findings.append(Finding(fence_line, "unclosed-fence", "error", "코드 블록이 닫히지 않았다."))
    return findings


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--json"]
    if len(args) != 1:
        print("사용: lint_doc.py <file.md> [--json]", file=sys.stderr)
        return 2
    try:
        with open(args[0], encoding="utf-8") as f:
            findings = lint(f.read())
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
