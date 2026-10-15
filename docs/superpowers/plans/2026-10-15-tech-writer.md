# tech-writer 스킬 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 독자 config에 맞춰 기술 문서를 구성·작성·개선하는 Claude Code 플러그인 `tech-writing`(스킬 `tech-writer`)을 만든다.

**Architecture:** SKILL.md는 모드 판별과 파이프라인만 담고, 세부 지침은 필요한 단계에서만 읽는 references로 나눈다. 결정적으로 처리할 수 있는 일(config 병합, Markdown 기계 검사, mermaid 렌더링)은 스크립트로 만들고 pytest로 검증한다. 지침 내용은 `docs/research/synthesis.md`의 원칙 ID를 그대로 쓴다.

**Tech Stack:** Python 3.11+ 표준 라이브러리 + PyYAML(PEP 723 인라인 의존성, `uv run`으로 실행), pytest(개발), bash, `@mermaid-js/mermaid-cli`(mmdc 또는 npx), archify 스킬, korean-proofreader·eli5 스킬.

**Spec:** `docs/superpowers/specs/2026-10-15-tech-writer-design.md`

## Global Constraints

- 플러그인 이름 `tech-writing`, 스킬 이름 `tech-writer`, 저장소 루트 `~/src/skills`.
- 유저 config `~/.claude/tech-writing/config.yaml`, 프로젝트 config `<repo>/.tech-writing.yaml`, 프로젝트가 키 단위로 덮어쓴다.
- `level` ∈ {beginner, intermediate, expert, non-technical}, `tone` ∈ {합니다체, 해요체, 개조식}, `loanword_policy` ∈ {usage, standard}(기본 usage), `format` ∈ {markdown, confluence, html}, `diagrams` ∈ {auto, mermaid, archify, none}.
- 제목 30자 권장(경고), H4 이상이면 분리 제안(경고), 코드 블록 언어 지정 필수(오류).
- 복잡한 다이어그램 기준: 주요 노드 12개 초과 또는 경계·브랜드 아이콘 필요.
- 정적 다이어그램 이미지는 mmdc로만 만든다(`mmdc` → 없으면 `npx -y @mermaid-js/mermaid-cli`).
- 확인되지 않은 사실은 `TODO(확인 필요)`로 표시한다.
- 스킬 안의 모든 문서는 출처(`docs/research/sources/`) 원문과 25자 이상 연속 일치하는 문장이 없어야 한다(라이선스: CC BY-NC-SA, 2차 가공 금지).
- 스킬 내부 문서(SKILL.md, references)는 "~한다" 평서체로 쓴다(humanizer-ko와 같은 문체). 본보기 문서의 어조는 각 파일 맨 위 주석에 적은 config `tone`을 따른다.
- 런타임 스크립트는 PEP 723 헤더(`# /// script`)로 의존성을 선언하고 `uv run <script>`로 실행한다.
- 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Review Focus

1. **코드 블록 안의 `#` 줄** — `bash` 블록 안의 `# 주석`을 제목으로 잡으면 안 된다. → Task 2 테스트 `test_heading_inside_fence_ignored`.
2. **프로젝트 config가 일부 키만 있을 때** — 프로필 하나만 추가하면 유저 프로필들이 사라지지 않고 합쳐져야 한다. → Task 3 테스트 `test_project_adds_profile_keeps_user_profiles`.
3. **config YAML 문법 오류** — 크래시 대신 파일 경로와 줄 번호를 알려 줘야 한다. → Task 3 테스트 `test_malformed_yaml_reports_location`.
4. **mmdc가 없고 렌더링이 실패할 때** — 다이어그램을 조용히 빼지 않고 실패를 알리며 종료 코드가 0이 아니어야 한다. → Task 4 테스트 `test_invalid_syntax_fails_loudly`.
5. **한글 제목 길이** — 바이트가 아니라 보이는 글자 수로 세고, 인라인 코드 백틱과 링크 URL은 빼야 한다. → Task 2 테스트 `test_title_length_counts_visible_chars`.

---

## 파일 구조

| 경로 | 책임 |
|---|---|
| `.claude-plugin/plugin.json`, `marketplace.json` | 플러그인·로컬 마켓플레이스 매니페스트 |
| `README.md` | 설치·사용법, 출처·라이선스 표기 |
| `skills/tech-writer/SKILL.md` | 모드 판별, 파이프라인, 연동 규칙, reference 로드 시점 |
| `skills/tech-writer/config/default.yaml` | config 기본값이자 예시 |
| `skills/tech-writer/scripts/load_config.py` | 유저·프로젝트 config 병합·검증 → JSON |
| `skills/tech-writer/scripts/lint_doc.py` | Markdown 기계 검사 |
| `skills/tech-writer/scripts/render-mermaid.sh` | mermaid → SVG/PNG |
| `skills/tech-writer/references/*.md` | 원칙별 지침 (§ Task 5~7) |
| `skills/tech-writer/references/templates/*.md` | 유형별 빈 골격 8개 |
| `skills/tech-writer/references/exemplars/*.md` | 유형별 모범 문서 9편 |
| `tests/` | pytest: 스크립트 테스트 + 내용 검사(원문 일치, 원칙 커버리지, lint 통과) |

---

### Task 1: 플러그인 골격

**Files:**
- Create: `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `README.md`, `skills/tech-writer/SKILL.md`(임시 frontmatter만), `tests/test_manifest.py`

**Interfaces:**
- Produces: 플러그인 루트 구조. `claude plugin validate .` 통과.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_manifest.py`

```python
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_plugin_manifest_names():
    plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    assert plugin["name"] == "tech-writing"
    assert market["name"] == "tech-writing"
    assert market["plugins"][0]["name"] == "tech-writing"
    assert market["plugins"][0]["source"] == "./"
    assert plugin["version"] == market["plugins"][0]["version"]


def test_skill_frontmatter_name():
    text = (ROOT / "skills/tech-writer/SKILL.md").read_text()
    assert text.startswith("---\n")
    assert "\nname: tech-writer\n" in text
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_manifest.py -v`
Expected: FAIL (`FileNotFoundError`)

- [ ] **Step 3: 매니페스트와 임시 SKILL.md 작성**

`.claude-plugin/plugin.json`:
```json
{
  "$schema": "https://json.schemastore.org/claude-code-plugin-manifest.json",
  "name": "tech-writing",
  "displayName": "Tech Writing",
  "description": "독자에 맞춰 한국어 기술 문서를 구성·작성·개선한다. 문서 유형별 구조, 기술 문서 문장 규칙, mermaid·archify 시각화, korean-proofreader 교정, 독자 되말하기 검증까지 한 흐름으로 처리한다.",
  "version": "0.1.0",
  "author": { "name": "yeonjin.roh" },
  "license": "MIT",
  "keywords": ["technical-writing", "documentation", "korean", "기술 문서", "테크니컬 라이팅"]
}
```

`.claude-plugin/marketplace.json`:
```json
{
  "$schema": "https://json.schemastore.org/claude-code-plugin-manifest.json",
  "name": "tech-writing",
  "owner": { "name": "yeonjin.roh" },
  "description": "한국어 기술 문서 작성 도구 모음",
  "plugins": [
    {
      "name": "tech-writing",
      "source": "./",
      "description": "독자에 맞춰 한국어 기술 문서를 구성·작성·개선한다.",
      "version": "0.1.0",
      "author": { "name": "yeonjin.roh" },
      "license": "MIT",
      "keywords": ["technical-writing", "documentation", "korean"]
    }
  ]
}
```

`skills/tech-writer/SKILL.md` (Task 10에서 본문 작성):
```markdown
---
name: tech-writer
description: 한국어 기술 문서 구성·작성·개선 (작성 중)
---

# 기술 문서 작성 (Tech Writer)
```

`README.md`: 제목, 한 줄 설명, 설치(`/plugin marketplace add ~/src/skills` → `/plugin install tech-writing@tech-writing`), 사용 예 3개, 의존 스킬(korean-proofreader, eli5, archify), mermaid-cli 안내, **출처 목록**(spec의 S1~S11 URL 전부와 라이선스 표기: Toss·easy-vibe는 CC BY-NC-SA 4.0으로 원문을 옮기지 않고 원칙만 참고했다는 문장, 인포그랩은 원칙만 참고), 라이선스 MIT.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_manifest.py -v && claude plugin validate .`
Expected: 2 passed, validate 성공

- [ ] **Step 5: 커밋**

```bash
git add .claude-plugin README.md skills/tech-writer/SKILL.md tests/test_manifest.py pyproject.toml uv.lock .gitignore
git commit -m "chore: tech-writing 플러그인 골격"
```

---

### Task 2: Markdown 기계 검사 `lint_doc.py`

**Files:**
- Create: `skills/tech-writer/scripts/lint_doc.py`, `tests/test_lint_doc.py`, `tests/conftest.py`

**Interfaces:**
- Produces: CLI `uv run skills/tech-writer/scripts/lint_doc.py <file.md> [--json]`. 종료 코드: 오류 있으면 1, 없으면 0. 함수 `lint(text: str) -> list[Finding]`, `Finding(line: int, rule: str, level: Literal["error","warn"], message: str)`. 규칙 ID: `code-lang`, `unclosed-fence`, `heading-depth`, `heading-length`, `heading-punct`, `multiple-h1`, `heading-skip`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/conftest.py`:
```python
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/tech-writer/scripts"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
```

`tests/test_lint_doc.py`:
```python
import json
import subprocess
from pathlib import Path

from conftest import ROOT, SCRIPTS, load_script

lint_doc = load_script("lint_doc")


def rules(text):
    return [(f.line, f.rule, f.level) for f in lint_doc.lint(text)]


def test_clean_document_has_no_findings():
    text = "# 배포 가이드\n\n개요.\n\n## 사전 준비\n\n```bash\nmake build\n```\n"
    assert rules(text) == []


def test_code_block_without_language_is_error():
    assert rules("# 제목\n\n```\nls\n```\n") == [(3, "code-lang", "error")]


def test_heading_inside_fence_ignored():
    text = "# 제목\n\n```bash\n# 이건 주석\n#### 이것도 주석\n```\n"
    assert rules(text) == []


def test_tilde_fence_and_longer_backtick_fence():
    text = "# 제목\n\n~~~yaml\n# a\n~~~\n\n````markdown\n```\n# 안쪽\n```\n````\n"
    assert rules(text) == []


def test_unclosed_fence_is_error():
    assert ("unclosed-fence", "error") in [(r, l) for _, r, l in rules("# 제목\n\n```bash\nls\n")]


def test_h4_warns_depth():
    text = "# 제목\n## 둘\n### 셋\n#### 넷\n"
    assert (4, "heading-depth", "warn") in rules(text)


def test_title_length_counts_visible_chars():
    ok = "# " + "가" * 30
    long = "# " + "가" * 31
    code = "# `" + "a" * 28 + "` 설정"  # 보이는 글자 31 → 경고
    link = "# [짧은 제목](https://example.com/very/long/url/that/should/not/count)"
    assert rules(ok) == []
    assert rules(long) == [(1, "heading-length", "warn")]
    assert rules(code) == [(1, "heading-length", "warn")]
    assert rules(link) == []


def test_question_or_exclamation_heading_warns():
    assert rules("# 에러를 해결하는 방법은?") == [(1, "heading-punct", "warn")]
    assert rules("# 지금 시작하세요!") == [(1, "heading-punct", "warn")]


def test_multiple_h1_warns():
    assert (3, "multiple-h1", "warn") in rules("# 하나\n\n# 둘\n")


def test_heading_skip_warns():
    assert rules("# 제목\n### 건너뜀\n") == [(2, "heading-skip", "warn")]


def test_html_comment_lines_ignored():
    assert rules("# 제목\n<!-- E-1: 개요 -->\n\n개요.\n") == []


def test_cli_json_and_exit_code(tmp_path: Path):
    doc = tmp_path / "doc.md"
    doc.write_text("# 제목\n\n```\nls\n```\n")
    proc = subprocess.run(
        ["uv", "run", str(SCRIPTS / "lint_doc.py"), str(doc), "--json"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 1
    data = json.loads(proc.stdout)
    assert data[0]["rule"] == "code-lang"
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_lint_doc.py -v`
Expected: FAIL (`FileNotFoundError: lint_doc.py`)

- [ ] **Step 3: 구현** — `skills/tech-writer/scripts/lint_doc.py`

```python
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
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_lint_doc.py -v`
Expected: 12 passed

- [ ] **Step 5: 커밋**

```bash
git add skills/tech-writer/scripts/lint_doc.py tests/conftest.py tests/test_lint_doc.py
git commit -m "feat: 기술 문서 Markdown 기계 검사 스크립트"
```

---

### Task 3: config 병합·검증 `load_config.py` + `default.yaml`

**Files:**
- Create: `skills/tech-writer/scripts/load_config.py`, `skills/tech-writer/config/default.yaml`, `tests/test_load_config.py`

**Interfaces:**
- Produces: CLI `uv run skills/tech-writer/scripts/load_config.py [--user PATH] [--project-dir DIR]`.
  - 기본값: `--user ~/.claude/tech-writing/config.yaml`, `--project-dir` 현재 디렉터리에서 위로 올라가며 `.tech-writing.yaml`을 찾되 `.git`이 있는 디렉터리나 홈에서 멈춘다.
  - 출력(stdout JSON): `{"status": "ok", "config": {...}, "sources": [경로...], "warnings": [...]}`
  - 종료 코드: 0 ok, 3 config 없음(`{"status":"missing"}` — 스킬이 인터뷰 시작), 2 오류(`{"status":"error","errors":[{"file","line","message"}]}`).
- 함수 `deep_merge(base: dict, override: dict) -> dict`(dict는 재귀 병합, 그 외는 덮어씀), `validate(config: dict) -> list[str]`.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_load_config.py`

```python
import json
import subprocess
from pathlib import Path

from conftest import ROOT, SCRIPTS, load_script

lc = load_script("load_config")

USER = """\
audiences:
  default: backend-dev
  profiles:
    backend-dev: {role: 백엔드 개발자, level: intermediate}
style: {tone: 합니다체}
"""


def run(tmp_path, user=None, project=None):
    user_path = tmp_path / "user.yaml"
    if user is not None:
        user_path.write_text(user)
    proj = tmp_path / "repo"
    proj.mkdir(exist_ok=True)
    (proj / ".git").mkdir(exist_ok=True)
    if project is not None:
        (proj / ".tech-writing.yaml").write_text(project)
    proc = subprocess.run(
        ["uv", "run", str(SCRIPTS / "load_config.py"), "--user", str(user_path), "--project-dir", str(proj)],
        capture_output=True, text=True, cwd=ROOT,
    )
    return proc.returncode, json.loads(proc.stdout)


def test_deep_merge_replaces_scalars_and_merges_dicts():
    assert lc.deep_merge({"a": {"x": 1, "y": 2}, "l": [1]}, {"a": {"y": 3}, "l": [2]}) == {"a": {"x": 1, "y": 3}, "l": [2]}


def test_missing_both_returns_status_missing(tmp_path):
    code, out = run(tmp_path)
    assert code == 3 and out["status"] == "missing"


def test_user_only_fills_defaults(tmp_path):
    code, out = run(tmp_path, user=USER)
    assert code == 0
    cfg = out["config"]
    assert cfg["style"]["loanword_policy"] == "usage"
    assert cfg["output"]["format"] == "markdown"
    assert cfg["style"]["tone"] == "합니다체"


def test_project_adds_profile_keeps_user_profiles(tmp_path):
    project = "audiences:\n  default: pm\n  profiles:\n    pm: {role: PM, level: non-technical}\n"
    code, out = run(tmp_path, user=USER, project=project)
    assert code == 0
    profiles = out["config"]["audiences"]["profiles"]
    assert set(profiles) == {"backend-dev", "pm"}
    assert out["config"]["audiences"]["default"] == "pm"
    assert len(out["sources"]) == 2


def test_malformed_yaml_reports_location(tmp_path):
    code, out = run(tmp_path, user="audiences:\n  default: [unclosed\n")
    assert code == 2
    err = out["errors"][0]
    assert err["file"].endswith("user.yaml") and err["line"] >= 1


def test_default_profile_must_exist(tmp_path):
    code, out = run(tmp_path, user="audiences:\n  default: ghost\n  profiles:\n    a: {role: x, level: expert}\n")
    assert code == 2
    assert "ghost" in out["errors"][0]["message"]


def test_invalid_enum_rejected(tmp_path):
    bad = USER.replace("intermediate", "guru")
    code, out = run(tmp_path, user=bad)
    assert code == 2
    assert "level" in out["errors"][0]["message"]


def test_unknown_key_is_warning_not_error(tmp_path):
    code, out = run(tmp_path, user=USER + "extra: 1\n")
    assert code == 0
    assert any("extra" in w for w in out["warnings"])


def test_project_search_stops_at_git_root(tmp_path):
    outer = tmp_path / ".tech-writing.yaml"
    outer.write_text("style: {tone: 해요체}\n")
    code, out = run(tmp_path, user=USER)
    assert out["config"]["style"]["tone"] == "합니다체"
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_load_config.py -v`
Expected: FAIL (`FileNotFoundError: load_config.py`)

- [ ] **Step 3: `default.yaml` 작성** — `skills/tech-writer/config/default.yaml`

```yaml
# tech-writer config 기본값.
# 유저: ~/.claude/tech-writing/config.yaml / 프로젝트: <repo>/.tech-writing.yaml
# 프로젝트 파일이 유저 파일을 키 단위로 덮어쓴다.
audiences:
  default: null            # profiles의 키 하나
  profiles: {}             # 예) backend-dev: {role, level, knows, needs, context}
style:
  tone: 합니다체            # 합니다체 | 해요체 | 개조식 — 한 문서 안에서 섞지 않는다
  loanword_policy: usage   # usage(사용 빈도) | standard(국립국어원)
  glossary: {}             # 다른 표기 → 통일 표기. 모든 표기 규칙보다 우선한다
output:
  format: markdown         # markdown | confluence | html
  diagrams: auto           # auto | mermaid | archify | none
  assets_dir: docs/assets
  confluence_mermaid_macro: false
```

- [ ] **Step 4: 구현** — `skills/tech-writer/scripts/load_config.py`

```python
#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""유저·프로젝트 config를 병합하고 검증해 JSON으로 출력한다.

종료 코드: 0 정상, 2 오류, 3 config 없음(인터뷰 필요).
"""
import argparse
import json
import sys
from pathlib import Path

import yaml

DEFAULTS = Path(__file__).resolve().parents[1] / "config" / "default.yaml"
ENUMS = {
    ("style", "tone"): {"합니다체", "해요체", "개조식"},
    ("style", "loanword_policy"): {"usage", "standard"},
    ("output", "format"): {"markdown", "confluence", "html"},
    ("output", "diagrams"): {"auto", "mermaid", "archify", "none"},
}
LEVELS = {"beginner", "intermediate", "expert", "non-technical"}


class ConfigError(Exception):
    def __init__(self, file, line, message):
        super().__init__(message)
        self.file, self.line, self.message = file, line, message


def deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        raise ConfigError(str(path), (mark.line + 1) if mark else 0, f"YAML 문법 오류: {e}")
    if not isinstance(data, dict):
        raise ConfigError(str(path), 1, "최상위는 키-값 매핑이어야 한다.")
    return data


def find_project_config(start: Path) -> Path | None:
    home = Path.home().resolve()
    cur = start.resolve()
    while True:
        candidate = cur / ".tech-writing.yaml"
        if candidate.is_file():
            return candidate
        if (cur / ".git").exists() or cur == home or cur.parent == cur:
            return None
        cur = cur.parent


def validate(config: dict) -> list[str]:
    errors = []
    aud = config.get("audiences") or {}
    profiles = aud.get("profiles") or {}
    default = aud.get("default")
    if default is not None and default not in profiles:
        errors.append(f"audiences.default '{default}'가 profiles에 없다.")
    for name, prof in profiles.items():
        level = (prof or {}).get("level")
        if level not in LEVELS:
            errors.append(f"audiences.profiles.{name}.level '{level}'은 {sorted(LEVELS)} 중 하나여야 한다.")
    for (section, key), allowed in ENUMS.items():
        value = (config.get(section) or {}).get(key)
        if value not in allowed:
            errors.append(f"{section}.{key} '{value}'은 {sorted(allowed)} 중 하나여야 한다.")
    return errors


def unknown_keys(config: dict, defaults: dict, prefix="") -> list[str]:
    found = []
    for key, value in config.items():
        path = f"{prefix}{key}"
        if key not in defaults:
            found.append(path)
        elif isinstance(value, dict) and isinstance(defaults[key], dict) and defaults[key]:
            found += unknown_keys(value, defaults[key], path + ".")
    return found


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--user", default=str(Path.home() / ".claude/tech-writing/config.yaml"))
    p.add_argument("--project-dir", default=".")
    args = p.parse_args(argv)

    user = Path(args.user).expanduser()
    project = find_project_config(Path(args.project_dir))
    present = [x for x in (user, project) if x and x.is_file()]
    if not present:
        print(json.dumps({"status": "missing"}, ensure_ascii=False))
        return 3
    try:
        defaults = read_yaml(DEFAULTS)
        merged = defaults
        warnings = []
        for path in present:
            data = read_yaml(path)
            warnings += [f"{path}: 알 수 없는 키 '{k}'" for k in unknown_keys(data, defaults)]
            merged = deep_merge(merged, data)
    except ConfigError as e:
        print(json.dumps({"status": "error", "errors": [{"file": e.file, "line": e.line, "message": e.message}]}, ensure_ascii=False))
        return 2
    errors = validate(merged)
    if errors:
        print(json.dumps({"status": "error", "errors": [{"file": str(present[-1]), "line": 0, "message": m} for m in errors]}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "ok", "config": merged, "sources": [str(x) for x in present], "warnings": warnings}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_load_config.py -v`
Expected: 9 passed

- [ ] **Step 6: 커밋**

```bash
git add skills/tech-writer/scripts/load_config.py skills/tech-writer/config/default.yaml tests/test_load_config.py
git commit -m "feat: 독자 config 병합·검증 스크립트"
```

---

### Task 4: mermaid 렌더링 `render-mermaid.sh`

**Files:**
- Create: `skills/tech-writer/scripts/render-mermaid.sh`, `tests/test_render_mermaid.py`

**Interfaces:**
- Produces: CLI `bash render-mermaid.sh <input.mmd> <output.svg|png>`. 종료 코드 0 성공(출력 파일이 비어 있지 않음), 1 렌더링 실패(stderr에 원인), 2 사용법 오류.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_render_mermaid.py`

```python
import subprocess

import pytest

from conftest import SCRIPTS

SCRIPT = str(SCRIPTS / "render-mermaid.sh")


def run(*args):
    return subprocess.run(["bash", SCRIPT, *args], capture_output=True, text=True, timeout=300)


def test_usage_error_without_args():
    assert run().returncode == 2


@pytest.mark.slow
def test_renders_svg(tmp_path):
    src = tmp_path / "a.mmd"
    src.write_text("flowchart LR\n  주문 --> 결제\n")
    out = tmp_path / "a.svg"
    proc = run(str(src), str(out))
    assert proc.returncode == 0, proc.stderr
    assert "<svg" in out.read_text()


@pytest.mark.slow
def test_invalid_syntax_fails_loudly(tmp_path):
    src = tmp_path / "bad.mmd"
    src.write_text("flowchart LR\n  A -->\n  ][\n")
    out = tmp_path / "bad.svg"
    proc = run(str(src), str(out))
    assert proc.returncode == 1
    assert proc.stderr.strip()
```

`pyproject.toml`에 추가:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["slow: mermaid-cli 실행이 필요한 테스트"]
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_render_mermaid.py -v`
Expected: FAIL (스크립트 없음, 종료 코드 127)

- [ ] **Step 3: 구현** — `skills/tech-writer/scripts/render-mermaid.sh`

```bash
#!/usr/bin/env bash
# mermaid 소스를 SVG/PNG로 렌더링한다.
# 사용: render-mermaid.sh <input.mmd> <output.svg|png>
# mmdc가 있으면 쓰고, 없으면 npx로 @mermaid-js/mermaid-cli를 실행한다.
set -uo pipefail

if [ $# -ne 2 ]; then
  echo "사용: render-mermaid.sh <input.mmd> <output.svg|png>" >&2
  exit 2
fi
in="$1"
out="$2"

if command -v mmdc >/dev/null 2>&1; then
  cmd=(mmdc)
else
  cmd=(npx -y @mermaid-js/mermaid-cli)
fi

rm -f "$out"
if ! log=$("${cmd[@]}" -i "$in" -o "$out" -b transparent 2>&1); then
  echo "mermaid 렌더링 실패: $in" >&2
  echo "$log" >&2
  exit 1
fi
if [ ! -s "$out" ]; then
  echo "mermaid 렌더링 결과가 비어 있다: $out" >&2
  echo "$log" >&2
  exit 1
fi
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_render_mermaid.py -v`
Expected: 3 passed (첫 실행은 npx 다운로드로 1~2분 걸릴 수 있다)

- [ ] **Step 5: 커밋**

```bash
chmod +x skills/tech-writer/scripts/render-mermaid.sh
git add skills/tech-writer/scripts/render-mermaid.sh tests/test_render_mermaid.py pyproject.toml
git commit -m "feat: mermaid 렌더링 스크립트 (mmdc → npx 폴백)"
```

---

### Task 5: 내용 검사 테스트 (원문 일치·원칙 커버리지·lint)

references를 쓰기 전에 내용 검사를 먼저 만든다. 이후 Task 6~9는 이 테스트를 통과시키는 방식으로 진행한다.

**Files:**
- Create: `tests/test_content.py`

**Interfaces:**
- Consumes: Task 2 `lint_doc.lint`.
- Produces: 원칙 ID 규약 — references의 원칙 제목은 `## F-3 구체적 수치·기준`처럼 `^#{2,3} ([A-Z])-(\d+) ` 형식. examples.md의 예시 제목은 `### F-3a ...`, `### F-3b ...`처럼 원칙 ID + 소문자 접미사. 본보기 주석은 `<!-- ✓ E-1: 설명 -->`.

필수 원칙 문자와 담당 파일(spec §6):

| 문자 | 파일 |
|---|---|
| C | audience.md |
| D, T | doc-types.md |
| E, S | structure.md |
| F, G, H, I, J, P | style.md |
| L, N | formatting.md |
| M | visualization.md |
| O, Q | review-checklist.md |

- [ ] **Step 1: 테스트 작성** — `tests/test_content.py`

```python
import re
from pathlib import Path

import pytest

from conftest import ROOT, load_script

lint_doc = load_script("lint_doc")
SKILL = ROOT / "skills/tech-writer"
REFS = SKILL / "references"
SOURCES = ROOT / "docs/research/sources"
WINDOW = 25
PRINCIPLE = re.compile(r"^#{2,3} ([A-Z])-(\d+) ", re.M)
EXAMPLE = re.compile(r"^### ([A-Z])-(\d+)[a-z] ", re.M)
OWNERS = {
    "audience.md": "C", "doc-types.md": "DT", "structure.md": "ES",
    "style.md": "FGHIJP", "formatting.md": "LN", "visualization.md": "M",
    "review-checklist.md": "OQ",
}


def skill_docs():
    return sorted(p for p in SKILL.rglob("*.md"))


def norm(text):
    return re.sub(r"\s+", " ", text)


@pytest.fixture(scope="module")
def source_shingles():
    if not SOURCES.exists():
        pytest.skip("docs/research/sources 없음 (로컬 전용 자료)")
    shingles = set()
    for f in SOURCES.glob("*.md"):
        t = norm(f.read_text(encoding="utf-8"))
        shingles.update(t[i:i + WINDOW] for i in range(len(t) - WINDOW + 1))
    return shingles


def test_no_verbatim_copy_from_sources(source_shingles):
    hits = []
    for doc in skill_docs():
        t = norm(doc.read_text(encoding="utf-8"))
        for i in range(len(t) - WINDOW + 1):
            chunk = t[i:i + WINDOW]
            if chunk.strip() and not chunk.isascii() and chunk in source_shingles:
                hits.append(f"{doc.relative_to(ROOT)}: «{chunk}»")
                break
    assert not hits, "출처 원문과 25자 이상 일치:\n" + "\n".join(hits)


def test_each_reference_owns_its_principles():
    for name, letters in OWNERS.items():
        path = REFS / name
        assert path.exists(), f"{name} 없음"
        found = {m.group(1) for m in PRINCIPLE.finditer(path.read_text(encoding="utf-8"))}
        assert set(letters) <= found, f"{name}: {set(letters) - found} 원칙 없음"


def test_examples_cover_principles():
    ids = set()
    for name in OWNERS:
        path = REFS / name
        if path.exists():
            ids |= {f"{m.group(1)}-{m.group(2)}" for m in PRINCIPLE.finditer(path.read_text(encoding="utf-8"))}
    examples = (REFS / "examples.md").read_text(encoding="utf-8")
    pairs = [f"{m.group(1)}-{m.group(2)}" for m in EXAMPLE.finditer(examples)]
    per_letter = {}
    for pid in pairs:
        per_letter[pid[0]] = per_letter.get(pid[0], 0) + 1
    example_letters = set("EFGHIJLMNPS")
    for letter in example_letters:
        assert per_letter.get(letter, 0) >= 3, f"{letter}: 예시 {per_letter.get(letter, 0)}쌍 (최소 3)"
    for pid in ids:
        if pid[0] in example_letters:
            assert pid in pairs, f"{pid}: 예시 없음"


def test_examples_have_dont_do_why():
    text = (REFS / "examples.md").read_text(encoding="utf-8")
    blocks = re.split(r"^### ", text, flags=re.M)[1:]
    for b in blocks:
        head = b.splitlines()[0]
        assert "❌" in b and "✅" in b and "왜:" in b, f"{head}: ❌/✅/왜: 형식 누락"


@pytest.mark.parametrize("doc", skill_docs(), ids=lambda p: str(p.relative_to(SKILL)))
def test_skill_docs_pass_lint(doc):
    errors = [f for f in lint_doc.lint(doc.read_text(encoding="utf-8")) if f.level == "error"]
    assert not errors, errors
```

N(코드 예제) 원칙은 별도 파일 없이 `formatting.md`에 `## N-1` 형식으로 함께 둔다.

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_content.py -v`
Expected: `test_each_reference_owns_its_principles`, `test_examples_*` FAIL (파일 없음). `test_no_verbatim_copy_from_sources`, `test_skill_docs_pass_lint`는 현재 SKILL.md만 있어 PASS.

- [ ] **Step 3: 커밋** (실패하는 테스트를 먼저 커밋, Task 6~8에서 통과시킨다)

```bash
git add tests/test_content.py
git commit -m "test: 스킬 내용 검사 (원문 일치·원칙 커버리지·lint)"
```

---

### Task 6: 원칙 references 7개

**Files:**
- Create: `skills/tech-writer/references/{audience,doc-types,structure,style,formatting,visualization,review-checklist}.md`

**Interfaces:**
- Consumes: Task 5 원칙 ID 규약, `docs/research/synthesis.md`(v1)의 섹션 A~T와 §R 해소안.
- Produces: 원칙 ID 목록. examples.md와 본보기 주석이 이 ID를 참조한다.

**작성 규칙 (모든 파일 공통)**
- 맨 위에 1~2문장 개요: 이 파일을 언제 읽는지.
- 원칙마다 `## X-n 원칙 이름` → 한두 문장 규칙 → **왜**(한 문장) → 근거 `[S#]` → 예시 링크 `→ examples.md X-n`.
- synthesis의 문장을 그대로 붙여 넣지 않고 다시 쓴다(원문 일치 테스트가 잡는다).
- 합의 수가 높은 원칙부터 배치한다.
- 과교정 주의: 원칙마다 예외가 있으면 "예외:" 한 줄(예: J-2 시스템 동작 설명은 시스템 주체 허용).

**파일별 필수 원칙** (synthesis 섹션 → ID)

`audience.md` (C)
- C-1 쓰기 전에 독자를 정한다 / C-2 파악 항목(역할·수준·목표·질문·읽는 상황) / C-3 같은 독자도 상황마다 필요가 다르다(절차=무엇·어떻게, 교육=왜) / C-4 수준별 전략 표(beginner·intermediate·expert·non-technical, 의사결정자는 확장으로 표시) / C-5 지식의 저주: `knows`에 없는 개념은 정의한다
- 추가 절: **config 해석** — `load_config.py` 출력 필드별 의미, 독자 결정 우선순위(요청 명시 → default), 재료·독자 불일치 판단 기준과 확인 질문 예, 최초 인터뷰 질문 4개(역할, 수준, 이미 아는 것, 문서에서 먼저 알고 싶은 것)와 YAML 작성 예.
- 추가 절: **eli5 호출 조건** — 독자 level이 non-technical/beginner이고 재료에 `knows`에 없는 개념이 3개 이상이거나 핵심 개념 설명이 필요한 경우. 넘기는 값: config `role`을 eli5 Job Role 표 항목에 대응(개발자→Engineer, PM→Product Manager, 리더→Manager/Director, 디자이너→Designer).

`doc-types.md` (D, T)
- D-1 독자 목표로 유형을 고른다(결정 트리: 처음 배움→학습, 특정 작업·문제→문제 해결, 사실 확인→참조, 깊은 이해→설명) / D-2 유형별 필수 요소 표 / D-3 유형은 결합할 수 있다 / D-4 실무 문서 매핑 표(설계·ADR→설명+결정 기록, 런북→문제 해결(절차), 트러블슈팅→문제 해결(진단), 포스트모템→설명+타임라인, README→학습 진입점+참조) — 각 행에 `templates/<파일>`과 `exemplars/<파일>` 경로 / D-5 여러 페이지 문서는 유형별 디렉터리 + 크로스링크 + 용어집
- T-1 매체에 맞춰 형식을 고른다 / T-2 시작하기 진입점 / T-3 학습 문서 끝에 다음 단계

`structure.md` (E, S)
- E-1 제목 아래 1~2문장 개요(읽고 나면 무엇을 할 수 있는가) / E-2 가치·결론 먼저 / E-3 한 문서 한 주제(H4면 분리 제안) / E-4 전개 방식 선택(시간순·중요도순·단계순·개념→사용→예제→심화) / E-5 제목 규칙(키워드, 30자 권장, 평서문, 같은 수준 같은 형태, 명사 나열 금지) / E-6 예측 가능성(같은 위계, 섹션 이름 통일, 설명→코드) / E-7 훑어보기(목차·앵커) / E-8 필요한 정보는 빠짐없이, 필요 없는 정보는 뺀다(기준은 독자 config) / E-9 문단은 짧게, 소주제별로
- S-1 결정은 이유와 함께 / S-2 대안과 비교 / S-3 설명 문서는 배경(문제)부터

`style.md` (F, G, H, I, J, P) — 기술 문서 특화 규칙만. 맨 위에 "일반 한국어 문장 교정은 korean-proofreader가 맡는다"와 위임 시 넘길 제약 3개(spec §4-8).
- F-1 단정적으로 / F-2 수치·기준 / F-3 누가·무엇을·어디에·어떻게 / F-4 지시어 대신 구체 명사(링크 텍스트 포함) / F-5 이중 부정 대신 긍정문 / F-6 참조 값의 출처·사용처 밝히기
- G-1 한 문장 한 생각(절차는 단문, 설명은 길이 변화 — R-8) / G-2 메타 담화 제거 / G-3 중복·군더더기 제거 / G-4 명사화 대신 동사(진행·수행·처리·실시)
- H-1 한 개념 한 용어, 용어집 우선 / H-2 공식 표기·대소문자 / H-3 외래어 정책(usage 기본, 판단 어려우면 표준 — R-1) / H-4 목록·지시문 형식 통일 / H-5 어조는 config `tone`, 한 문서 안에서 섞지 않는다(R-3)
- I-1 전문 용어는 독자 `knows` 기준 / I-2 약어는 처음에 풀어쓴다(두 표기 형식) / I-3 은어·직역 대신 실제 동작
- J-1 절차·지시문은 독자가 주체, 능동형 / J-2 예외: 시스템 동작 설명은 시스템 주체 허용(R-2)
- P-1 확인된 사실만, 확인 못 하면 `TODO(확인 필요)` / P-2 위험한 작업은 경고를 눈에 띄게(작업 직전 위치) / P-3 예외·오류 상황도 쓴다

`formatting.md` (L, N)
- L-1 번호 목록=순서 있는 절차, 글머리=대등 항목 / L-2 한 단계 한 동작, 분기는 단계로 / L-3 목록은 필요한 곳에만, 중첩 1단계까지 / L-4 표: 비교·속성·매개변수(이름·타입·필수·기본값·설명) / L-5 코드 블록 언어 지정, 설명 다음에 / L-6 경고·주의 패널 절제 / L-7 헤딩 H1~H3 / L-8 시각 자료 5개 이상이면 캡션 / L-9 긴 부가 정보는 접기·FAQ로
- N-1 실행 가능한 예제, 입력과 출력 함께 / N-2 성공 판정 기준 / N-3 단순→복잡 / N-4 잘못된 예와 오류 처리도 보여 준다
- 추가 절: **형식별 차이** — Markdown(GitHub 렌더링, `<details>` 접기, 경고는 `> [!WARNING]`), Confluence(개요+구분자, 목차 매크로, 정보·경고 패널, mermaid 매크로 여부), HTML(단일 파일, 다이어그램 인라인 SVG). 각 형식의 경고·접기·캡션 문법 예시 코드 블록.

`visualization.md` (M)
- M-1 그림이 나은 경우(구조·흐름·관계·상태) / M-2 표가 나은 경우(비교·속성) / M-3 다이어그램 앞 한 문장, 뒤 핵심 한 문장, 글로 반복하지 않기 / M-4 노드 12개 이하, 주요 경로 하나, 라벨은 짧게 / M-5 스크린숏 환경·크기 통일
- 추가 절: **내용 → 다이어그램 표**, **형식 × 복잡도 → 도구 표**(spec §7 그대로), **mermaid 작성 규칙**(노드 ID는 영문, 라벨은 한국어 따옴표, `flowchart LR` 기본, subgraph로 경계), **렌더링 절차**(`bash scripts/render-mermaid.sh in.mmd <assets_dir>/name.svg`, 실패 시 오류 메시지 보고 문법 수정 후 재시도, 2회 실패하면 코드 블록 유지하고 사용자에게 보고), **archify 호출 절차**(archify 스킬을 부르고 type 선택: architecture·workflow·sequence·dataflow·lifecycle, 산출 HTML은 `<assets_dir>/`에 두고 문서에서 링크. archify는 이미지 내보내기를 CLI로 하지 않으므로 Markdown·Confluence 본문용 정적 이미지는 mermaid 개요도로 만든다).

`review-checklist.md` (O, Q)
- O-1 기계 검사: `uv run scripts/lint_doc.py <doc>` + 용어집 위반 grep + 렌더링 성공 / O-2 사실 검증: 명령어·경로·API·설정 키를 재료와 대조 / O-3 되말하기: 서브에이전트 프롬프트 전문(독자 역할 주입, "핵심 3줄 요약 / 막힌 곳 / 빠졌다고 느낀 정보"를 받는다), 의도와 비교해 고칠 곳 판단 / O-4 유형별 체크리스트(학습·문제 해결·참조·설명·설계·런북·포스트모템 각 4~6문항) / O-5 구조·문장 공통 체크리스트
- Q-1 SSOT와 링크 / Q-2 deprecated 표시 / Q-3 문서 담당자·갱신일 메타데이터
- 마지막: 결과 보고 형식(독자, 유형, lint 결과, 사실 검증 결과, 되말하기 요약, 남은 TODO).

- [ ] **Step 1:** 7개 파일 작성(위 목록의 원칙을 모두 `## X-n` 제목으로).
- [ ] **Step 2:** Run: `uv run pytest tests/test_content.py -k "owns or verbatim or lint" -v` → Expected: PASS
- [ ] **Step 3:** 각 파일을 `uv run skills/tech-writer/scripts/lint_doc.py <file>`로 돌려 경고도 0개로 만든다.
- [ ] **Step 4: 커밋**

```bash
git add skills/tech-writer/references/*.md
git commit -m "docs: tech-writer 원칙 references"
```

---

### Task 7: Don't/Do 예시 `examples.md`

**Files:**
- Create: `skills/tech-writer/references/examples.md`

**Interfaces:**
- Consumes: Task 6 원칙 ID, `docs/research/dodont-catalog.md`의 **패턴** 열.

**형식**
````markdown
## F 명확성·구체성

### F-2a 처리량 기준을 수치로
❌ 주문이 몰리면 배치가 늦어질 수 있습니다.
✅ 주문이 시간당 5만 건을 넘으면 정산 배치가 30분 이상 늦어집니다.
왜: "몰리면", "늦어질 수 있다"로는 언제 대응해야 하는지 정할 수 없다.
````

**작성 규칙**
- 대상 문자: E, F, G, H, I, J, L, M, N, P, S. 문자마다 3~5쌍, 원칙 ID마다 최소 1쌍.
- 소재는 백엔드·운영·배포·데이터 파이프라인·결제·물류 같은 실무 장면. 카탈로그 원문의 소재(React, fetch, MongoDB 등)를 그대로 쓰지 않는다.
- 카탈로그에서 예시가 없던 영역은 새로 만든다: M(다이어그램 앞뒤 문장 / 표로 충분한데 그림을 그린 경우 / 노드 과다), L(번호 목록 vs 글머리, 매개변수 표, 경고 위치), N(출력이 없는 예제 vs 출력·성공 기준이 있는 예제).
- 여러 줄 Don't/Do는 언어를 지정한 코드 블록(`markdown`)으로 쓴다.
- 일반 문장 교정 패턴(번역투, 이중 피동)은 넣지 않는다. G-4 명사화만 포함.

- [ ] **Step 1:** examples.md 작성.
- [ ] **Step 2:** Run: `uv run pytest tests/test_content.py -v` → Expected: 모든 테스트 PASS(템플릿·본보기 전이므로 해당 파일 없음은 무관).
- [ ] **Step 3: 커밋**

```bash
git add skills/tech-writer/references/examples.md
git commit -m "docs: 원칙별 Don't/Do 예시"
```

---

### Task 8: 유형별 템플릿 8개

**Files:**
- Create: `skills/tech-writer/references/templates/{tutorial,how-to,troubleshooting,reference,explanation,design-adr,runbook,postmortem}.md`
- Modify: `tests/test_content.py` (템플릿 필수 섹션 테스트 추가)

**Interfaces:**
- Consumes: Task 6 D-2(유형별 필수 요소), S-1~S-3.
- Produces: 템플릿 경로. doc-types.md D-4 표가 이 경로를 가리킨다.

**필수 섹션** (H2 제목 문자열, 이 순서)

| 파일 | H2 섹션 |
|---|---|
| tutorial.md | 목표, 사전 준비, 1단계…(번호 단계), 결과 확인, 다음 단계 |
| how-to.md | 이 가이드로 할 수 있는 것, 사전 준비, 절차, 확인 방법, 문제가 생기면 |
| troubleshooting.md | 증상, 원인, 해결 방법, 해결 확인, 재발 방지 |
| reference.md | 개요, 사전 정보(인증 등), 시그니처, 매개변수, 반환값, 오류, 예제 |
| explanation.md | 한 줄 정의, 등장 배경, 동작 원리, 대안과 비교, 활용 |
| design-adr.md | 요약, 배경, 목표와 비목표, 대안 비교, 결정, 영향과 리스크, 후속 작업 |
| runbook.md | 개요, 언제 쓰는가, 사전 확인, 절차, 롤백, 확인 방법, 연락처 |
| postmortem.md | 요약, 영향, 타임라인, 원인, 대응, 재발 방지 조치, 교훈 |

각 템플릿: H1 자리표시자, 개요 1~2문장 자리표시자, 섹션마다 `<!-- 작성 지침: ... (원칙 ID) -->` 한두 줄. 다이어그램 자리는 `<!-- M: ... -->` 주석으로 표시(design-adr: architecture, postmortem: 타임라인 표 + 필요 시 sequence, runbook: flowchart 선택).

- [ ] **Step 1: 테스트 추가** — `tests/test_content.py` 끝에:

```python
TEMPLATE_SECTIONS = {
    "tutorial.md": ["목표", "사전 준비", "결과 확인", "다음 단계"],
    "how-to.md": ["이 가이드로 할 수 있는 것", "사전 준비", "절차", "확인 방법", "문제가 생기면"],
    "troubleshooting.md": ["증상", "원인", "해결 방법", "해결 확인", "재발 방지"],
    "reference.md": ["개요", "사전 정보", "시그니처", "매개변수", "반환값", "오류", "예제"],
    "explanation.md": ["한 줄 정의", "등장 배경", "동작 원리", "대안과 비교", "활용"],
    "design-adr.md": ["요약", "배경", "목표와 비목표", "대안 비교", "결정", "영향과 리스크", "후속 작업"],
    "runbook.md": ["개요", "언제 쓰는가", "사전 확인", "절차", "롤백", "확인 방법", "연락처"],
    "postmortem.md": ["요약", "영향", "타임라인", "원인", "대응", "재발 방지 조치", "교훈"],
}


@pytest.mark.parametrize("name,sections", TEMPLATE_SECTIONS.items())
def test_template_sections_in_order(name, sections):
    text = (REFS / "templates" / name).read_text(encoding="utf-8")
    h2 = [m.group(1).strip() for m in re.finditer(r"^## (.+)$", text, re.M)]
    positions = [next(i for i, h in enumerate(h2) if h.startswith(s)) for s in sections]
    assert positions == sorted(positions), f"{name}: 섹션 순서 {h2}"
```

- [ ] **Step 2:** Run: `uv run pytest tests/test_content.py -k template -v` → Expected: FAIL(파일 없음)
- [ ] **Step 3:** 템플릿 8개 작성.
- [ ] **Step 4:** Run: `uv run pytest tests/test_content.py -v` → Expected: PASS
- [ ] **Step 5: 커밋**

```bash
git add skills/tech-writer/references/templates tests/test_content.py
git commit -m "docs: 문서 유형별 템플릿 8개"
```

---

### Task 9: 본보기 선정과 분석 노트 (사용자 게이트)

**Files:**
- Create: `skills/tech-writer/references/exemplar-notes.md`
- Read: `docs/research/exemplars-a.md`, `exemplars-b.md`, `exemplars-c.md`

- [ ] **Step 1:** 수집 결과 3개(`exemplars-a/b/c.md`)를 유형별 후보표로 합친다(유형, 제목, 출처, 점수, 잘한 점 요약). 중복을 지운다.
- [ ] **Step 2: 사용자 게이트** — 후보표를 보여 주고 유형별 본보기(1~2편)를 고르게 한다. 9개 유형(8개 + 비개발 독자 설명) 모두 정해질 때까지 진행하지 않는다. 일본 글(Qiita·Zenn)은 구조 패턴 목록으로만 제시한다.
- [ ] **Step 3:** `exemplar-notes.md` 작성 — 유형별로 선정 문서의 링크, 잘한 점 → 원칙 ID, 골격(H2 수준), 우리 모범 문서에 가져올 점과 보완할 점(예: 국내 트러블슈팅 문서에 없는 "해결 확인" 단계). 마지막 절: 일본 글에서 가져온 구조 패턴. 인용은 20자 이내.
- [ ] **Step 4:** Run: `uv run pytest tests/test_content.py -v` → Expected: PASS(원문 일치 검사 포함)
- [ ] **Step 5: 커밋**

```bash
git add skills/tech-writer/references/exemplar-notes.md docs/research/exemplars-*.md
git commit -m "docs: 한국어 본보기 분석 노트"
```

---

### Task 10: 유형별 모범 문서 9편

**Files:**
- Create: `skills/tech-writer/references/exemplars/{tutorial,how-to,troubleshooting,reference,explanation,design-adr,runbook,postmortem,non-technical}.md`
- Modify: `tests/test_content.py` (본보기 테스트 추가)

**Interfaces:**
- Consumes: Task 8 템플릿 섹션, Task 9 분석 노트, Task 6·7 원칙과 예시.

**작성 규칙**
- 가상의 서비스 하나(예: 사내 "정산 배치 서비스"와 그 주변 시스템)를 9편 전체에 공통 소재로 쓴다. 독자와 어조는 파일 맨 위 주석에 적는다(`<!-- 독자: backend-dev(intermediate) / 어조: 합니다체 -->`). non-technical.md는 PM 독자, 해요체.
- 템플릿 섹션 순서를 지킨다. 모든 원칙 적용 지점에 `<!-- ✓ X-n: 무엇을 어떻게 했는지 -->` 주석을 단다. 문서당 주석 8개 이상.
- 다이어그램은 mermaid 코드 블록으로 넣고, 각 블록을 `render-mermaid.sh`로 렌더링해 오류가 없는지 확인한다(산출 이미지는 커밋하지 않는다).
- 분량: 문서당 80~200줄.

- [ ] **Step 1: 테스트 추가** — `tests/test_content.py` 끝에:

```python
import subprocess

EXEMPLARS = REFS / "exemplars"
EXEMPLAR_NAMES = list(TEMPLATE_SECTIONS) + ["non-technical.md"]


@pytest.mark.parametrize("name", EXEMPLAR_NAMES)
def test_exemplar_annotated_and_clean(name):
    text = (EXEMPLARS / name).read_text(encoding="utf-8")
    assert text.startswith("<!-- 독자:"), "맨 위 독자·어조 주석 없음"
    assert len(re.findall(r"<!-- ✓ [A-Z]-\d+:", text)) >= 8
    assert not [f for f in lint_doc.lint(text) if f.level == "error"]
    assert "TODO" not in text


@pytest.mark.parametrize("name,sections", TEMPLATE_SECTIONS.items())
def test_exemplar_follows_template(name, sections):
    text = (EXEMPLARS / name).read_text(encoding="utf-8")
    h2 = [m.group(1).strip() for m in re.finditer(r"^## (.+)$", text, re.M)]
    for s in sections:
        assert any(h.startswith(s) for h in h2), f"{name}: '{s}' 섹션 없음"


@pytest.mark.slow
@pytest.mark.parametrize("name", EXEMPLAR_NAMES)
def test_exemplar_mermaid_renders(name, tmp_path):
    text = (EXEMPLARS / name).read_text(encoding="utf-8")
    blocks = re.findall(r"^```mermaid\n(.*?)^```", text, re.M | re.S)
    for i, block in enumerate(blocks):
        src = tmp_path / f"{i}.mmd"
        src.write_text(block)
        proc = subprocess.run(["bash", str(SKILL / "scripts/render-mermaid.sh"), str(src), str(tmp_path / f"{i}.svg")],
                              capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0, f"{name} 블록 {i}: {proc.stderr}"
```

- [ ] **Step 2:** Run: `uv run pytest tests/test_content.py -k exemplar -v` → Expected: FAIL(파일 없음)
- [ ] **Step 3:** 9편 작성.
- [ ] **Step 4:** Run: `uv run pytest -v` → Expected: 전체 PASS
- [ ] **Step 5: 되말하기 검증** — 본보기마다 서브에이전트에게 독자 역할을 주고 3줄 요약·막힌 곳을 받는다(review-checklist.md O-3 프롬프트). 막힌 곳이 나오면 고치고 Step 4를 다시 돌린다.
- [ ] **Step 6: 커밋**

```bash
git add skills/tech-writer/references/exemplars tests/test_content.py
git commit -m "docs: 유형별 모범 문서 9편"
```

---

### Task 11: SKILL.md 본문

**Files:**
- Modify: `skills/tech-writer/SKILL.md`
- Modify: `tests/test_manifest.py` (SKILL.md 테스트 추가)

**Interfaces:**
- Consumes: 모든 references 경로, 스크립트 3개의 CLI.

**내용** (500줄 이하, 목표 200~250줄)

frontmatter `description` (한 문단, 트리거 문구 포함):
> 한국어 기술 문서를 독자에 맞춰 구성·작성·개선하는 스킬. 코드·메모·대화·장애 기록 같은 재료로 README, 튜토리얼, how-to, 트러블슈팅, API 참조, 개념 설명, 설계 문서·ADR, 운영 런북, 장애 보고서(포스트모템)를 쓰거나, 이미 있는 기술 문서의 구조·흐름·시각화를 진단해 고칠 때 쓴다. "문서 써줘", "README 만들어줘", "설계 문서로 정리해줘", "포스트모템 작성", "런북 만들어줘", "이 문서 구조 개선해줘", "PM도 읽을 수 있게 정리해줘", "기술 문서 독자 설정"처럼 요청하면 발동한다. 독자 config(`~/.claude/tech-writing/config.yaml`, `.tech-writing.yaml`)를 만들거나 바꾸는 요청도 포함. 문장만 다듬는 교정 요청은 korean-proofreader, 비즈니스 문서·영어 문서는 제외.

본문 절 (순서대로):
1. 한 단락 역할 소개.
2. **모드 판별** 표(spec §3).
3. **0단계: config** — `uv run ${CLAUDE_SKILL_DIR}/scripts/load_config.py` 실행, 종료 코드별 행동(0 사용, 3 인터뷰 → `audience.md` 인터뷰 절 → 유저 파일 작성 후 재실행, 2 오류 내용을 보여 주고 수정 제안). `CLAUDE_SKILL_DIR`이 없으면 이 SKILL.md가 있는 디렉터리 기준 상대 경로.
4. **작성 파이프라인** 1~10단계(spec §4). 단계마다 "읽을 파일" 명시: 2→audience.md, 3→doc-types.md + templates/<유형> + exemplars/<유형>(이 한 편만), 4·5→structure.md·style.md·examples.md(해당 원칙만 검색), 6→visualization.md, 7→formatting.md, 9→review-checklist.md.
5. **연동 스킬** — eli5(호출 조건·넘길 값), archify(호출 조건), korean-proofreader(넘길 제약 3개 문구 그대로, 문서 층위는 쓰지 않음). 연동 스킬이 설치되지 않았으면: eli5 없음→audience.md C-4 전략으로 직접, proofreader 없음→style.md만 적용하고 보고에 표시, archify 없음→mermaid만.
6. **개선 모드** — 진단 순서(독자 적합성 → 유형 → 구조 → 시각화 → 문장), 구조 변경은 제안표(현재/제안/근거 원칙 ID) → 동의 후 수정.
7. **설정 모드** — 파일 선택 질문, 수정 후 `load_config.py`로 검증.
8. **출력과 보고** — 형식별 저장 위치, 보고 형식(review-checklist.md 마지막 절).
9. **하지 말 것** — 확인 안 된 명령어·API 지어내기, 본보기 원문 소재 복사, 다이어그램 내용 글로 반복, 한 문서에 어조 섞기, 구조를 동의 없이 재배치(개선 모드).

- [ ] **Step 1: 테스트 추가** — `tests/test_manifest.py` 끝에:

```python
import re


def test_skill_md_size_and_references_exist():
    skill = ROOT / "skills/tech-writer"
    text = (skill / "SKILL.md").read_text()
    assert len(text.splitlines()) <= 500
    for ref in set(re.findall(r"`((?:references|scripts|config)/[^`]+?)`", text)):
        ref = ref.split()[0]
        if "<" in ref:
            continue
        assert (skill / ref).exists(), f"SKILL.md가 가리키는 {ref} 없음"


def test_description_mentions_key_triggers():
    text = (ROOT / "skills/tech-writer/SKILL.md").read_text()
    desc = text.split("description:", 1)[1].split("\n---", 1)[0]
    for word in ["README", "트러블슈팅", "설계 문서", "포스트모템", "런북", "korean-proofreader"]:
        assert word in desc
```

- [ ] **Step 2:** Run: `uv run pytest tests/test_manifest.py -v` → Expected: FAIL
- [ ] **Step 3:** SKILL.md 작성.
- [ ] **Step 4:** Run: `uv run pytest -v && claude plugin validate .` → Expected: 전체 PASS
- [ ] **Step 5:** skill-reviewer 스킬로 SKILL.md를 점검하고, 지적 중 spec과 충돌하지 않는 것만 반영한다.
- [ ] **Step 6: 커밋**

```bash
git add skills/tech-writer/SKILL.md tests/test_manifest.py
git commit -m "feat: tech-writer SKILL.md 파이프라인"
```

---

### Task 12: 로컬 설치와 평가

**Files:**
- Create: `evals/scenarios.md`, `evals/results/2026-10-15.md`

- [ ] **Step 1: 설치**

```bash
claude plugin marketplace add ~/src/skills
claude plugin install tech-writing@tech-writing
claude plugin list | grep -A3 tech-writing
```
Expected: `Scope: user`, `Status: ✔ enabled`

- [ ] **Step 2: 시나리오 작성** — `evals/scenarios.md`에 spec §10의 6개 시나리오. 시나리오마다 재료(실제 파일 경로 또는 붙여 넣을 메모 원문), 독자 config, 기대 결과(통과 기준 3~5개: 유형 선택, 필수 섹션, 다이어그램 렌더링, TODO 처리, 독자 되말하기 일치)를 적는다. 재료 예: 이 저장소 자체(README 시나리오), 가상의 장애 메모 20줄(포스트모템), Task 10의 design-adr 본보기를 일부러 망가뜨린 버전(개선 모드).
- [ ] **Step 3: 실행** — skill-creator 스킬의 평가 절차로 시나리오마다 서브에이전트 두 개(스킬 사용 / 미사용)를 같은 재료로 돌리고, 채점 서브에이전트가 통과 기준과 review-checklist.md로 채점한다. 시나리오 6(config 없음)은 `HOME`을 임시 디렉터리로 바꿔 실행한다.
- [ ] **Step 4: 기록** — `evals/results/2026-10-15.md`에 시나리오별 통과/실패, 스킬 사용·미사용 비교, 발견한 문제.
- [ ] **Step 5: 개선** — 실패 항목을 고치고 해당 시나리오만 다시 돌린다. 2회 반복 후에도 남은 문제는 결과 파일에 남긴다.
- [ ] **Step 6: 커밋**

```bash
git add evals
git commit -m "test: tech-writer 시나리오 평가"
```
