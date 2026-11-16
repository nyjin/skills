#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Confluence storage XHTML -> Markdown (AI slop 측정용 최소 변환).

confluence-cli 의 Markdown 내보내기는 패널 본문을 두 번 내고, jira 매크로의 티켓 키를 빠뜨리고,
작업 목록의 상태 글자(incomplete)를 본문에 붙인다. 그대로 재면 내보내기의 흠을 글쓴이의 slop으로 센다.

- ac:adf-fallback 은 버린다 (adf-content 와 같은 본문의 대체 렌더링이라 두면 문단이 두 번 나온다)
- jira 매크로는 [KEY](browse URL) 로, 패널·info/note/tip 매크로는 > 인용으로, code 매크로는 펜스로
사용: uv run storage2md.py <in.storage.html> > out.md
"""
import re
import sys
from html.parser import HTMLParser

JIRA = "https://jira.invalid/browse/"  # 주소를 모를 때도 링크 모양은 남긴다(링크된 티켓 키를 링크 없는 번호로 세지 않게)


class Conv(HTMLParser):
    def __init__(self, jira_base=JIRA):
        super().__init__(convert_charrefs=True)
        self.jira_base = jira_base
        self.in_attr = False
        self.out = []          # 완성된 블록
        self.buf = []          # 현재 인라인 버퍼
        self.skip = 0          # adf-fallback 깊이
        self.quote = 0
        self.lists = []        # 'ul' | 'ol' 스택, ol 은 [kind, n]
        self.table = None      # rows
        self.row = None
        self.cell = None
        self.macro = []        # (name, params)
        self.param = None
        self.href = None
        self.code_macro = None

    # -- helpers
    def flush(self, prefix=""):
        text = re.sub(r"[ \t]+", " ", "".join(self.buf)).strip()
        self.buf = []
        if not text:
            return
        if self.cell is not None:
            self.cell.append(text)
            return
        q = "> " * self.quote
        self.out.append(q + prefix + text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.skip:
            if tag == "ac:adf-fallback":
                self.skip += 1
            return
        if tag == "ac:adf-fallback":
            self.skip = 1
            return
        if tag == "ac:structured-macro":
            self.macro.append([a.get("ac:name"), {}])
            name = a.get("ac:name")
            if name in ("info", "note", "tip", "warning", "panel"):
                self.flush(); self.quote += 1
            return
        if tag == "ac:parameter":
            self.param = a.get("ac:name"); self.buf_param = []
            return
        if tag == "ac:adf-node" and a.get("type") == "panel":
            self.flush(); self.quote += 1
            return
        if tag == "ac:plain-text-body":
            self.code_macro = []
            return
        if tag == "ac:adf-attribute":
            self.in_attr = True
            return
        if tag in ("ac:task-id", "ac:task-status", "ac:task-uuid"):
            self.in_attr = True
            return
        if tag == "ac:task-list":
            tag = "ul"
        if tag == "ac:task":
            tag = "li"
        if tag == "ri:attachment":
            self.buf.append(f"![{a.get('ri:filename','img')}](attachments/{a.get('ri:filename','img')})")
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.flush()
            self.buf.append("#" * int(tag[1]) + " ")
        elif tag == "p":
            if self.cell is not None:
                if self.buf and "".join(self.buf).strip():
                    self.flush()
            else:
                self.flush()
        elif tag in ("ul", "ol"):
            self.flush_li()
            self.lists.append(["ol", 0] if tag == "ol" else ["ul", 0])
        elif tag == "li":
            self.flush_li()
            self.lists[-1][1] += 1
            self.li_start = True
        elif tag == "table":
            self.flush(); self.table = []
        elif tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.cell = []
        elif tag == "strong" or tag == "b":
            self.buf.append("**")
        elif tag == "code":
            self.buf.append("`")
        elif tag == "a":
            self.href = a.get("href"); self.buf.append("[")
        elif tag == "br":
            self.buf.append(" ")

    def flush_li(self):
        if not self.lists:
            self.flush(); return
        depth = len(self.lists) - 1
        kind, n = self.lists[-1]
        mark = f"{n}. " if kind == "ol" else "- "
        if self.cell is not None:
            self.flush(); return
        text = re.sub(r"[ \t]+", " ", "".join(self.buf)).strip()
        self.buf = []
        if text:
            self.out.append("> " * self.quote + "   " * depth + mark + text)

    def handle_endtag(self, tag):
        if self.skip:
            if tag == "ac:adf-fallback":
                self.skip -= 1
            return
        if tag == "ac:parameter":
            if self.macro and self.param:
                self.macro[-1][1][self.param] = "".join(self.buf_param)
            self.param = None
            return
        if tag == "ac:plain-text-body":
            return
        if tag == "ac:adf-attribute":
            self.in_attr = False
            return
        if tag in ("ac:task-id", "ac:task-status", "ac:task-uuid"):
            self.in_attr = False
            return
        if tag == "ac:task-list":
            tag = "ul"
        if tag == "ac:task":
            tag = "li"
        if tag == "ac:structured-macro":
            name, params = self.macro.pop()
            if name == "jira" and params.get("key"):
                k = params["key"]
                self.buf.append(f"[{k}]({self.jira_base}{k})")
            elif name in ("info", "note", "tip", "warning", "panel"):
                self.flush(); self.quote -= 1
            elif name == "code" and self.code_macro is not None:
                self.flush()
                self.out.append("```" + params.get("language", "") + "\n" + "".join(self.code_macro).strip("\n") + "\n```")
                self.code_macro = None
            return
        if tag == "ac:adf-node":
            self.flush(); self.quote = max(0, self.quote - 1)
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p"):
            if self.lists and self.cell is None:
                return
            self.flush()
        elif tag == "li":
            self.flush_li()
        elif tag in ("ul", "ol"):
            self.flush_li()
            self.lists.pop()
        elif tag in ("td", "th"):
            self.flush()
            self.row.append("<br>".join(self.cell))
            self.cell = None
        elif tag == "tr":
            self.table.append(self.row); self.row = None
        elif tag == "table":
            rows = self.table; self.table = None
            if rows:
                w = max(len(r) for r in rows)
                lines = []
                for i, r in enumerate(rows):
                    r = r + [""] * (w - len(r))
                    lines.append("| " + " | ".join(c.replace("|", "\\|") for c in r) + " |")
                    if i == 0:
                        lines.append("|" + "---|" * w)
                self.out.append("\n".join(lines))
        elif tag in ("strong", "b"):
            self.buf.append("**")
        elif tag == "code":
            self.buf.append("`")
        elif tag == "a":
            self.buf.append(f"]({self.href})"); self.href = None

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_attr:
            return
        if self.param is not None:
            self.buf_param.append(data); return
        if self.code_macro is not None:
            self.code_macro.append(data); return
        self.buf.append(data.replace("\n", " "))


def convert(s, jira_base=JIRA):
    s = re.sub(r"<!\[CDATA\[(.*?)\]\]>", lambda m: m.group(1).replace("<", "&lt;").replace(">", "&gt;"), s, flags=re.S)
    c = Conv(jira_base)
    c.feed(s)
    c.flush()
    md = "\n\n".join(c.out)
    md = re.sub(r"\*\*\s*\*\*", "", md)
    return md + "\n"


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--jira-base=")]
    base = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--jira-base=")), JIRA)
    if len(args) != 1:
        print("사용: storage2md.py <in.storage.html> [--jira-base=https://<host>/browse/]", file=sys.stderr)
        sys.exit(2)
    print(convert(open(args[0], encoding="utf-8").read(), base), end="")
