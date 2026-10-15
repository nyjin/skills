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
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*(.*)$")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


@dataclass
class Finding:
    line: int
    rule: str
    level: str
    message: str


def visible_len(title: str) -> int:
    title = re.sub(r"`([^`]*)`", r"\1", title)
    title = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", title)
    title = re.sub(r"[*_]", "", title)
    return len(title.strip())


def lint(text: str) -> list[Finding]:
    findings: list[Finding] = []
    fence: str | None = None
    fence_line = 0
    prev_level = 0
    h1_seen = False

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
    with open(args[0], encoding="utf-8") as f:
        findings = lint(f.read())
    if "--json" in argv:
        print(json.dumps([asdict(x) for x in findings], ensure_ascii=False))
    else:
        for x in findings:
            print(f"L{x.line} [{x.level}] {x.rule}: {x.message}")
    return 1 if any(x.level == "error" for x in findings) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
