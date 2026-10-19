# harness-loop 플러그인 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 세션 기록에서 AI·도구·지침 처리의 개선 신호를 결정적으로 뽑고, judge 채점을 거쳐 action item(스킬 수정·신규 스킬·스크립트·규칙)으로 만들어 승인 후 대상 소유 환경의 규칙대로 반영하는 플러그인 `harness-loop`(스킬 `retro`)을 만든다.

**Architecture:** 결정적인 일(기록 파싱, 신호 추출, 재발성 계산, 가중합, 보고 대상 선별, 반영 대상 판정)은 모두 스크립트가 하고 pytest 로 고정한다. LLM 은 영향·해결 가능성 채점과 개선안 작성만 맡는다. 명시 호출에서는 현재 세션 모델이, 백그라운드에서는 `claude -p` 가 같은 judge 입력(digest)과 같은 출력 스키마를 쓴다. 상태는 세션 디렉터리의 `harness/` 와 `${CLAUDE_PLUGIN_DATA}` 세 파일뿐이다.

**Tech Stack:** Python 3.11+ 표준 라이브러리 + PyYAML(PEP 723, `uv run --script`), pytest(개발), Claude Code plugin hooks(SessionStart·SessionEnd), `claude -p`.

**Spec:** `docs/superpowers/specs/2026-10-16-harness-loop-design.md`

## Global Constraints

- 플러그인 이름 `harness-loop`, 스킬 이름 `retro`(호출 `/harness-loop:retro`, `review`, `backfill`), 위치 `~/src/skills/plugins/harness-loop/`, marketplace `source` 는 `./plugins/harness-loop`.
- 세션 상태: `~/.claude/projects/<프로젝트>/<세션ID>/harness/`(`HARNESS_PROJECTS_ROOT` 가 있으면 그 루트 아래 같은 구조) 아래 `cursor`, `signals.jsonl`, `runs/NNN-<explicit|session-end|sweep>.json`, `action-items.json`, `lock/`, `skip`.
- 공용 상태: `${CLAUDE_PLUGIN_DATA}` 아래 `scoring.yaml`, `rubric.md`, `decisions.jsonl`, 그리고 운영 파일 `installed_at`, `judge-runs.jsonl`, `hook-errors.log`, `hook-events.log`(락 경합으로 건너뛴 보고 쪽 쓰기). `CLAUDE_PLUGIN_DATA` 가 없으면 `~/.claude/plugins/data/harness-loop-local/` 를 쓴다.
- `scoring.yaml` 기본값: `weights: {recurrence: 0.4, impact: 0.4, actionability: 0.2}`, `propose: 2.2`, `watch: 1.6`, `global_bump: 0.3`, `queue_limit: 3`, `max_background_judges_per_day: 10`, `eval_markers: ["트리거 측정 중"]`.
- 재발성 점수: 같은 sig 가 나온 서로 다른 세션 수 1 → 1점, 2~3 → 2점, 4 이상 → 3점.
- 상태값: `propose|watch|discarded|approved|rejected|applied|regressed`.
- 훅은 어떤 경우에도 종료 코드 0 이고, 오류는 `hook-errors.log` 에 한 줄씩 남긴다.
- `HARNESS_RETRO=1`, 훅 입력에 `agent_id` 가 있음 → 그 세션에 `skip` 을 쓰고 아무것도 하지 않는다. `CLAUDE_CODE_CHILD_SESSION` 은 가드가 아니다(보통의 최상위 세션 훅·Bash 에서도 1 로 설정된다는 실측 결과).
- `HERDR_PW_WORKER` 가 설정된 세션(herdr 워커)은 수집·판정은 하되 SessionStart 보고를 출력하지 않는다.
- 백그라운드 judge: `HARNESS_RETRO=1 claude -p --setting-sources project --strict-mcp-config --no-session-persistence --tools "" --output-format json --json-schema <스키마>`(cwd 는 HOME 밖에 새로 만든 빈 임시 디렉터리 `tempfile.mkdtemp(prefix="harness-judge-")`. `TMPDIR` 가 HOME 아래면 `/tmp` 아래에 만든다. 끝나면 지운다), `subprocess.Popen(..., start_new_session=True, stdin/stdout/stderr=DEVNULL)`.
- 스킬 문서는 규칙·표·명령만 쓴다. 이력·결정 과정·근거 서술은 넣지 않고, config 값은 속성명으로만 참조한다. 마크다운 산문은 한 단락을 한 줄로 쓴다.
- 커밋 메시지는 이 저장소 형식(`feat: …`, `docs: …`, 한국어)이고 끝에 `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` 를 붙인다.

## Review Focus

1. **컴팩션 뒤 이어진 세션** — "This session is being continued from a previous conversation" 로 시작하는 사용자 턴은 사람 발화가 아니므로 C2·C9·C11 신호를 만들면 안 된다. → Task 2 `test_compaction_summary_is_not_user_turn`.
2. **기록이 쓰이는 중인 마지막 줄** — 마지막 줄이 잘린 JSON 이면 건너뛰고 cursor 를 그 줄 앞에 둬야 다음 호출이 다시 읽는다. → Task 2 `test_partial_last_line_not_consumed`.
3. **SessionEnd judge 와 SessionStart sweep 이 같은 세션을 동시에 잡음** — 나중 쪽은 건너뛰고, 10분 넘은 lock 은 회수한다. → Task 1 `test_lock_is_exclusive_and_stale_lock_reclaimed`.
4. **`claude -p` 가 실패하거나 JSON 이 아닌 출력을 냄** — `action-items.json` 을 건드리지 않고 오류를 남기며, 세션을 처리 완료로 표시하지 않아 다음 sweep 이 다시 시도한다. → Task 5 `test_background_judge_failure_leaves_state_untouched`.
5. **플러그인 밖 실행(`CLAUDE_PLUGIN_DATA` 없음)이나 깨진 훅 입력** — 크래시 없이 종료 코드 0, 오류 로그 한 줄. → Task 7 `test_hook_bad_input_exits_zero_and_logs`.

---

## 파일 구조

| 경로 (`plugins/harness-loop/` 기준) | 책임 |
|---|---|
| `.claude-plugin/plugin.json` | 매니페스트 |
| `hooks/hooks.json` | SessionStart(`startup\|resume`)·SessionEnd → `scripts/hook.py` |
| `scripts/state.py` | 경로, config 로드·기본값 복사, cursor, lock, decisions, action-items 읽기·쓰기 |
| `scripts/transcript.py` | jsonl 을 턴 목록으로 파싱, 세션 종류(normal·worker·eval) 판정 |
| `scripts/collect.py` | 턴에서 C1~C11 신호 추출, 잡음 규칙, `signals.jsonl` 추가 |
| `scripts/judge.py` | digest 조립, judge 출력 검증·채점·병합(`merge`), 재채점(`rescore`), 백그라운드 실행(`background`) |
| `scripts/report.py` | SessionStart sweep·재발 확인·보고 문구, `list`·`decide` |
| `scripts/apply_target.py` | 대상 경로 → 소유 환경(repo·local·plugin_cache·new) 판정 |
| `scripts/hook.py` | 훅 진입점, 가드, 오류 로그, 종료 코드 0 보장 |
| `skills/retro/SKILL.md` | 명시 진입점 절차 |
| `defaults/scoring.yaml`, `defaults/rubric.md`, `defaults/judge-schema.json` | 기본 설정, 채점 기준, judge 출력 스키마 |
| `tests/conftest.py`, `tests/fixtures/*.jsonl`, `tests/test_*.py` | 테스트 |

저장소 루트에서는 `.claude-plugin/marketplace.json`(항목 추가), `pyproject.toml`(`testpaths` 에 `plugins/harness-loop/tests` 추가), `README.md`(플러그인 절 추가)를 고친다.

모든 스크립트는 PEP 723 헤더(`dependencies = ["pyyaml>=6"]`)를 갖고, 같은 디렉터리 모듈을 `import state` 처럼 가져온다.

---

### Task 1: 플러그인 골격과 상태 저장소

**Files:**
- Create: `plugins/harness-loop/.claude-plugin/plugin.json`, `plugins/harness-loop/defaults/scoring.yaml`, `plugins/harness-loop/defaults/rubric.md`, `plugins/harness-loop/scripts/state.py`, `plugins/harness-loop/tests/conftest.py`, `plugins/harness-loop/tests/test_state.py`, `plugins/harness-loop/tests/test_manifest.py`
- Modify: `.claude-plugin/marketplace.json`, `pyproject.toml`

**Interfaces:**
- Produces (`state.py`):
  - `data_dir() -> Path` — `CLAUDE_PLUGIN_DATA` 또는 폴백. 처음 부르면 `defaults/` 의 `scoring.yaml`·`rubric.md` 를 복사하고 `installed_at`(ISO 시각)을 쓴다.
  - `load_config() -> dict` — `scoring.yaml` 을 읽고 빠진 키는 기본값으로 채운다.
  - `harness_path(transcript: Path) -> Path` — `projects_root() / transcript.parent.name / transcript.stem / "harness"`. 만들지 않는다. `projects_root()` 는 `HARNESS_PROJECTS_ROOT` 또는 `~/.claude/projects` 다.
  - `harness_dir(transcript: Path) -> Path` — `harness_path` 를 만들어 두고 돌려준다.
  - `find_transcript(session_id: str) -> Path | None` — `projects_root()/*/<id>.jsonl`. 없고 `HARNESS_PROJECTS_ROOT` 가 설정돼 있으면 `~/.claude/projects/*/<id>.jsonl` 도 찾는다.
  - `read_cursor(hdir) -> int`, `write_cursor(hdir, line: int) -> None`.
  - `acquire_lock(hdir, stale_sec=600) -> bool`, `release_lock(hdir) -> None` — `lock/` 디렉터리 생성의 원자성을 쓴다.
  - `load_items(hdir) -> dict[str, dict]`(sig → item), `save_items(hdir, items) -> None` — 임시 파일에 쓰고 `os.replace`.
  - `append_decision(sig, decision, target=None, note=None, impact=None, actionability=None) -> None`(`impact`·`actionability` 는 주어졌을 때만 기록), `load_decisions() -> dict[str, dict]`(sig → edit-score 를 뺀 마지막 결정, `{"decision", "at", "target"}`).
  - `load_score_overrides() -> dict[str, dict]` — edit-score 기록에서 sig 별로 필드마다 마지막 `impact`·`actionability`.
  - `all_harness_dirs() -> list[Path]` — `projects_root()/*/*/harness`. 테스트에서는 `HARNESS_PROJECTS_ROOT` 로 루트를 바꾼다.

- [ ] **Step 1: 테스트 작성** — `tests/conftest.py` 는 저장소 `tests/conftest.py` 와 같은 `load_script(name)` 를 `plugins/harness-loop/scripts` 기준으로 두고, `tmp_projects` 픽스처가 `HARNESS_PROJECTS_ROOT`·`CLAUDE_PLUGIN_DATA` 를 `tmp_path` 아래로 바꾼다.

```python
def test_data_dir_copies_defaults_once(tmp_projects):
    d = state.data_dir()
    assert (d / "scoring.yaml").exists() and (d / "installed_at").exists()
    (d / "scoring.yaml").write_text("propose: 9\n")
    state.data_dir()
    assert state.load_config()["propose"] == 9
    assert state.load_config()["queue_limit"] == 3

def test_lock_is_exclusive_and_stale_lock_reclaimed(tmp_path):
    assert state.acquire_lock(tmp_path) is True
    assert state.acquire_lock(tmp_path) is False
    os.utime(tmp_path / "lock", (0, 0))
    assert state.acquire_lock(tmp_path, stale_sec=600) is True

def test_cursor_and_items_roundtrip(tmp_path):
    state.write_cursor(tmp_path, 42); assert state.read_cursor(tmp_path) == 42
    state.save_items(tmp_path, {"C1:x": {"sig": "C1:x", "status": "watch"}})
    assert state.load_items(tmp_path)["C1:x"]["status"] == "watch"

def test_decisions_last_wins(tmp_projects):
    state.append_decision("C1:x", "rejected"); state.append_decision("C1:x", "approved", target="/a")
    assert state.load_decisions()["C1:x"]["decision"] == "approved"
```

`test_manifest.py`: marketplace 에 `harness-loop` 항목(`source == "./plugins/harness-loop"`)이 있고 버전이 `plugin.json` 과 같다. 기존 `tests/test_manifest.py` 의 `plugins[0]` 검사는 그대로 통과해야 한다.

- [ ] **Step 2: 실패 확인** — `uv run pytest plugins/harness-loop/tests -q` → ImportError/FAIL.
- [ ] **Step 3: 구현** — 매니페스트(`name: harness-loop`, `version: 0.1.0`, `author: yeonjin.roh`, `license: MIT`), marketplace 항목, `pyproject.toml` `testpaths = ["tests", "plugins/harness-loop/tests"]`, `defaults/scoring.yaml`(Global Constraints 값), `defaults/rubric.md`(spec §6 표 그대로 + 영향·해결 가능성 판정 예시 각 1줄), `state.py`.
- [ ] **Step 4: 통과 확인** — `uv run pytest -q` → 전체 PASS(기존 tech-writer 테스트 포함).
- [ ] **Step 5: 커밋** — `feat: harness-loop 플러그인 골격과 상태 저장소`

### Task 2: 기록 파싱과 세션 종류 판정

**Files:**
- Create: `scripts/transcript.py`, `tests/test_transcript.py`, `tests/fixtures/basic.jsonl`, `tests/fixtures/compacted.jsonl`, `tests/fixtures/worker.jsonl`, `tests/fixtures/eval.jsonl`, `tests/fixtures/partial.jsonl`

**Interfaces:**
- Consumes: 없음
- Produces (`transcript.py`):
  - `@dataclass Turn`: `index: int`(1부터), `cwd: str | None`(그 사람 발화 줄의 `cwd` 필드), `line: int`(사람 발화 줄 번호), `user: str`, `calls: list[Call]`, `errors: list[ToolError]`, `rejected: list[str]`, `interrupted: bool`, `ai_tail: str`(마지막 AI 텍스트 끝 500자), `api_error: bool`, `queued: bool`(AI 가 일하는 도중 친 발화 `queued_command` 로 연 턴), `slash: bool`(슬래시 명령 호출로 연 턴).
  - `@dataclass Call`: `id, name, input: dict, skill: str | None`(그 시점 실행 중인 스킬).
  - `@dataclass ToolError`: `call_id, tool, text`(주제 줄 200자). 주제 줄은 `Exit code N`·셔뱅·마크다운 제목·`ls -l` 의 `total N` 줄과 권한 표기 줄·글자나 숫자가 없는 줄을 건너뛴 첫 줄이고, `Traceback (most recent call last):` 줄이 있으면 마지막 예외 줄(`EXCEPTION_LINE`, 건너뛸 줄 제외)이다. 모두 건너뛰면 `""`. Traceback 예외 줄이 없는 `Bash` 오류는 첫 줄 규칙보다 먼저, 건너뛸 줄을 뺀 나머지 중 오류 낱말(`BASH_ERROR`: `error|exception|fatal|failed|failure|denied|not found|no such file|cannot|can't|invalid|unknown|refused|timed out`, 대소문자 무시, 단어 일치)이 있는 마지막 줄을 쓴다.
  - `parse(path: Path, start_line: int = 0) -> tuple[list[Turn], int]` — `start_line` 이후만 읽고, 마지막으로 완전히 읽은 줄 번호를 돌려준다.
  - `session_kind(turns: list[Turn], cfg: dict) -> str` — `"eval"`(오류 문구에 `eval_markers` 포함), `"worker"`(첫 발화가 `[ibs-`·`executor`·`Read the brief`·`export HERDR_PW_WORKER`·`## 격리` 로 시작), 그 밖은 `"normal"`.

사람 발화 판정: `isSidechain`·`isMeta` 제외, `tool_result` 만 있는 user 메시지 제외, `<` 로 시작하는 시스템 주입 제외(단 `<pasted_content` 는 사람 발화이고, `<command-` 로 시작하고 `<command-name>/x</command-name>` 이 있는 슬래시 명령 호출은 `origin.kind` 가 없거나 `human` 이면 `user` 가 `/x <command-args>` 인 새 턴(`slash=True`)이다. 명령 이름에 `:` 가 있거나 그 턴의 `Skill` 호출과 마지막 `:` 뒤 이름이 같으면 그 턴을 그 스킬 구간으로 둔다), `[Request interrupted by user` 는 직전 턴의 `interrupted=True`, `This session is being continued from a previous conversation` 로 시작하면 턴을 만들지 않는다. AI 가 일하는 도중 친 발화(`type: attachment`, `attachment.type: queued_command`, `commandMode: prompt`, `origin.kind` 가 없거나 `human`)는 `prompt` 를 같은 규칙으로 판정해 새 턴(`queued=True`)을 연다. 사람 발화가 직전 2턴 안의 `queued` 턴 텍스트(`RESEND_MIN`=8자 이상)와 같거나 그것으로 시작하고 바로 뒤가 공백·문장부호면 다시 보낸 것으로 보고 새 턴을 열지 않는다. 작업 알림·다른 에이전트 메시지는 턴이 아니다. `Skill` 호출 뒤 다음 사람 발화 전까지가 그 스킬의 실행 구간이다(스킬 본문 주입인 `isMeta` 메시지는 구간을 닫지 않는다). 거절은 tool_result 본문의 `The user doesn't want to proceed`.

- [ ] **Step 1: 픽스처와 테스트 작성** — 픽스처는 실제 기록 형식(`type`, `message.content`, `tool_use`/`tool_result`, `is_error`)으로 손으로 쓴 최소 줄들이다. 실제 세션 내용은 옮기지 않는다.

```python
def test_parse_basic_turns_and_errors():
    turns, last = transcript.parse(FIX / "basic.jsonl")
    assert [t.index for t in turns] == [1, 2]
    assert turns[0].errors[0].tool == "mcp__claude_ai_Atlassian__getJiraIssue"
    assert "app is not installed" in turns[0].errors[0].text
    assert turns[1].user.startswith("아니")
    assert last == sum(1 for _ in open(FIX / "basic.jsonl"))

def test_compaction_summary_is_not_user_turn():
    turns, _ = transcript.parse(FIX / "compacted.jsonl")
    assert all(not t.user.startswith("This session is being continued") for t in turns)

def test_partial_last_line_not_consumed():
    turns, last = transcript.parse(FIX / "partial.jsonl")
    assert last == sum(1 for _ in open(FIX / "partial.jsonl")) - 1

def test_start_line_skips_processed_turns():
    _, last = transcript.parse(FIX / "basic.jsonl")
    turns, _ = transcript.parse(FIX / "basic.jsonl", start_line=last)
    assert turns == []

def test_skill_span_survives_meta_injection():
    turns, _ = transcript.parse(FIX / "basic.jsonl")
    assert {c.skill for c in turns[0].calls if c.name == "Bash"} == {"ticket-creator"}

def test_session_kind():
    cfg = {"eval_markers": ["트리거 측정 중"]}
    assert transcript.session_kind(transcript.parse(FIX / "eval.jsonl")[0], cfg) == "eval"
    assert transcript.session_kind(transcript.parse(FIX / "worker.jsonl")[0], cfg) == "worker"
    assert transcript.session_kind(transcript.parse(FIX / "basic.jsonl")[0], cfg) == "normal"

def test_pasted_content_is_user_turn():
    turns, _ = transcript.parse(FIX / "worker.jsonl")   # 첫 사람 발화는 "<pasted_content id=…>## 격리 …"
    assert turns[0].user.startswith("<pasted_content")
```

- [ ] **Step 2: 실패 확인** — `uv run pytest plugins/harness-loop/tests/test_transcript.py -q`
- [ ] **Step 3: 구현** — `transcript.py`. 출발점은 `~/.claude/bin/harness-collect.py` 의 `user_text`·`scan` 과 2026-10-16 분석용 `digest.py` 의 턴 구성이다.
- [ ] **Step 4: 통과 확인** — 같은 명령 PASS.
- [ ] **Step 5: 커밋** — `feat: harness-loop 기록 파싱과 세션 종류 판정`

### Task 3: 신호 추출 (C1~C11)

**Files:**
- Create: `scripts/collect.py`, `tests/test_collect.py`, `tests/fixtures/signals.jsonl`
- Modify: `tests/fixtures/basic.jsonl`(필요한 줄 추가)

**Interfaces:**
- Consumes: `transcript.parse`, `transcript.session_kind`, `state.harness_dir`, `state.read_cursor`/`write_cursor`, `state.load_config`
- Produces (`collect.py`):
  - `signals(turns: list[Turn], kind: str) -> list[dict]` — 각 신호 `{"sig", "condition", "turn", "line", "skill", "quote", "needs_judge": bool}`.
  - `run(transcript: Path) -> list[dict]` — cursor 이후를 파싱해 신호를 `signals.jsonl` 에 덧붙이고 cursor 를 옮긴 뒤 새 신호를 돌려준다. `kind == "eval"` 이면 `skip` 을 쓰고 `[]`.
  - `normalize_error(text: str) -> str` — 경로 → `P`, 숫자 → `N`, `<tool_use_error>` 태그 제거, 80자.
  - `command_signature(cmd: str) -> str` — 앞쪽 `cd …&&` 제거, inline python 은 `py-inline[<sys·os·re·json·collections 를 뺀 import>]`, 그 밖은 첫 줄 앞 3토큰.
  - CLI: `uv run --script collect.py <transcript.jsonl>` → 새 신호를 JSON 배열로 stdout. CLI 는 세션 락을 `state.lock_all`(`report.decide` 와 같은 재시도)로 잡고 `finally` 에서 푼다. 못 잡으면 stderr 에 한국어 안내, 종료 코드 3. `judge.background` 는 락을 쥔 채 `collect.run` 을 직접 부른다.

추출 규칙(sig 형식 포함):

| 조건 | 규칙 | sig | needs_judge |
|---|---|---|---|
| C1 | 오류 뒤 같은 턴의 다음 호출. 권한 오류(`requires approval`, `multiple operations`, `was blocked. For security`, `Contains simple_expansion`, `changes directory before`, `This session is isolated in the worktree`)와 `ToolError.text` 가 빈 오류(`Exit code N` 단독 등)는 제외. 실행 중인 스킬 자신의 파일을 Edit·Write 한 경우도 C1 | `C1:<normalize_error>` | False |
| C2 | 다음 사람 발화가 `^아니`, `그게 아니`, `말고`, `왜 .{0,20}(했\|하지\|하냐)`, `다시 (해\|하\|작성)`, `취지`, `벗어나`, `대충`, `빈약`, `지나치`, `너무 (길\|많\|복잡)` 중 하나, 또는 도구 거절·중단. 턴마다 `C2:?` 하나만 낸다 | `C2:?` | True |
| C3 | 한 세션에서 2회 이상 나온 `command_signature` 중 `ls\|cat\|sed\|grep\|head\|tail\|echo\|wc\|find\|pwd\|rg\|git (status\|diff\|log\|show)` 로 시작하지 않는 것, heredoc 안 `def ` 정의(`C3:inline-helper`), 스킬 실행 구간에서 그 스킬 `references/` 파일 읽기(`C3:skill-preamble:<skill>`) | `C3:cmd:<signature>` 등 | False |
| C4 | `ai_tail` 끝 80자가 `(할까요\|갈까요\|될까요\|드릴까요\|볼까요\|좋을까요\|괜찮을까요\|어떨까요)\??\s*$` 이고 다음 발화가 맨 동의(`네\|넵\|예\|어\|응\|ㅇㅇ\|ㅇㅋ\|좋아요?\|진행\|ok\|yes\|그래`, 30자 이내) | `C4:bare-yes` | False |
| C4 | 사람 발화에 `묻지 말\|물어보지 말\|질문 없이\|쭉 가` 가 있는데 같은 턴 `ai_tail` 이 위 질문형으로 끝남 | `C4:asked-after-no-ask` | False |
| C5 | `ai_tail` 에 `정정\|바로잡\|틀렸습니다\|과장했\|가정이었\|판단이 일렀` | `C5:?` | True |
| C6 | Edit·Write·`acli jira workitem create`·`createJiraIssue` 호출 다음 사람 발화에 `되돌\|원복\|revert\|하지 말\|바꾸지 말` 또는 그 호출이 거절됨 | `C6:?` | True |
| C7 | collect 는 만들지 않는다. judge 가 `C2:?` 후보마다 C2 와 C7 중 하나를 고른다(기존 규칙과 대조) | judge 가 정함 | — |
| C8 | 사람 발화에 `/<이름>` 또는 `<이름> 스킬` 이 있고 그 턴에 `Skill` 호출이 없음 → `C8:skill:<이름>`; `Turn.cwd` 의 git toplevel 에 `.codegraph/` 가 있고 한 턴에 `grep\|rg\|find` Bash 가 5회 이상이며 `codegraph` 호출이 없음 → `C8:codegraph-unused` | 좌동 | False |
| C9 | 다음 사람 발화가 직전 `ai_tail` 의 40자 이상 연속 구간을 포함 | `C9:?` | True |
| C10 | 한 턴에 `sleep `·`Monitor`·`agent wait` 호출 3회 이상 → `C10:polling`; 사람 발화가 `끝났\|끝난건가\|다 됐\|되고 있나` | `C10:status-ask` | False |
| C11 | `api_error` → `C11:api-error`; 앞 턴의 `calls` 와 `ai_tail` 이 모두 비어 있음 → `C11:resend` | 좌동 | False |

`kind == "worker"` 이면 C1·C3·C10 만 만든다(`WORKER_ONLY`). `needs_judge` 는 C2·C5·C6·C9(`JUDGED`)이고, `?` 가 들어간 sig 는 judge 가 정한다.

- [ ] **Step 1: 테스트 작성** — 조건마다 양성 1건과, 잡음 규칙 1건씩.

```python
def test_normalize_error():
    assert collect.normalize_error("<tool_use_error>File /Users/a/b.md not found at line 12</tool_use_error>") == "File P not found at line N"

def test_c1_retry_topic_normalized():
    s = by_cond(collect.signals(turns("basic.jsonl"), "normal"), "C1")
    assert [x["sig"] for x in s] == ["C1:" + collect.normalize_error(FIXTURE_403_TEXT)]

def test_permission_and_exit_code_errors_are_noise():
    # signals.jsonl 픽스처에는 권한 오류 1건, "Exit code 1" 단독 오류 1건, Figma 검증 오류 1건이 있다
    assert [x["sig"] for x in by_cond(collect.signals(turns("signals.jsonl"), "normal"), "C1")] == ["C1:" + collect.normalize_error(FIXTURE_FIGMA_TEXT)]

def test_c4_bare_yes_after_question():
    assert any(x["sig"] == "C4:bare-yes" for x in collect.signals(turns("signals.jsonl"), "normal"))

def test_worker_collects_only_c1_c3_c10():
    sig = collect.signals(turns("signals.jsonl"), "worker")
    assert {x["condition"] for x in sig} <= {"C1", "C3", "C10"}

def test_c3_repeated_command_and_trivial_excluded():
    sig = [x["sig"] for x in collect.signals(turns("signals.jsonl"), "normal") if x["condition"] == "C3"]
    assert "C3:cmd:acli jira workitem" in sig and not any(s.startswith("C3:cmd:cat") for s in sig)

def test_command_signature():
    assert collect.command_signature("cd /x && acli jira workitem view IBS-1 --json") == "acli jira workitem"
    assert collect.command_signature("python3 - <<'EOF'\nimport json, yaml\nEOF") == "py-inline[yaml]"

def test_run_appends_and_moves_cursor(tmp_projects):
    t = copy_fixture(tmp_projects, "basic.jsonl")
    first = collect.run(t); second = collect.run(t)
    assert first and second == []
    assert len(open(state.harness_dir(t) / "signals.jsonl").readlines()) == len(first)

def test_eval_session_writes_skip(tmp_projects):
    t = copy_fixture(tmp_projects, "eval.jsonl")
    assert collect.run(t) == [] and (state.harness_dir(t) / "skip").exists()
```

`FIXTURE_403_TEXT`·`FIXTURE_FIGMA_TEXT` 는 픽스처에 넣은 오류 첫 줄 문자열 상수로 `test_collect.py` 위쪽에 둔다.

- [ ] **Step 2: 실패 확인** — `uv run pytest plugins/harness-loop/tests/test_collect.py -q`
- [ ] **Step 3: 구현** — `collect.py`.
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: 커밋** — `feat: harness-loop 신호 추출 C1~C11`

### Task 4: judge 입력·채점·병합

**Files:**
- Create: `scripts/judge.py`, `defaults/judge-schema.json`, `tests/test_judge.py`

**Interfaces:**
- Consumes: `state.*`, `transcript.parse`, `apply_target.classify`(Task 6, 없으면 `owner` 는 `"unknown"`)
- Produces (`judge.py`):
  - `digest(transcript: Path, new_signals: list[dict]) -> str` — judge 에게 줄 Markdown. 포함: `rubric.md`, 신호 표, 신호가 걸린 턴의 사람 발화(500자)·호출 요약(12개)·오류·`ai_tail`, 다른 세션의 살아 있는 sig 목록(`all_harness_dirs` 의 `action-items.json`), `decisions.jsonl` sig 목록, 출력 스키마.
  - `recurrence(sig: str, this_session: str) -> int` — 이 세션을 포함해 sig 가 `signals.jsonl` 또는 `action-items.json` 에 나온 세션 수.
  - `score(item: dict, cfg: dict) -> tuple[float, str]` — `item["sessions"]`(재발 세션 수)·`impact`·`actionability` 로 가중합과 상태를 낸다. 재발성 점수는 Global Constraints 구간. 대상 경로가 `~/.claude/CLAUDE.md` 이거나 `/memory/` 를 포함하면 `propose`·`watch` 에 `global_bump` 를 더한다. `duplicate_of_rule` 가 있고 `rule_unenforced` 가 거짓이면 `discarded`. `decisions` 의 마지막 결정이 `rejected` 면 `rejected`.
  - `merge(transcript: Path, judge_output: dict, mode: str) -> dict` — 스키마 검증 → 사용자 점수(`state.load_score_overrides()`)를 덮은 실효 `impact`·`actionability` 가 모두 1 인 항목은 버림(고칠 것 없음 하한. 사용자가 1/1 보다 높게 고친 sig 는 남는다) → 토픽(sig 의 첫 `:` 뒤)이 같은 C2·C7 항목 쌍은 출력 순서상 앞의 것만 남김 → 항목마다 `state.load_score_overrides()` 의 사용자 점수로 judge 의 `impact`·`actionability` 를 덮고 `sessions = recurrence(sig, 이 세션)`·`score`·`target.owner` 계산 → 기존 `action-items.json` 과 sig 로 병합(근거는 덧붙이고, `KEPT = approved|rejected|applied|regressed` 상태와 `regressed_after` 가 있는 `watch` 는 유지) → `runs/NNN-<mode>.json` 저장. 반환값은 병합된 items.
  - `rescore() -> int` — 모든 세션의 items 를 현재 `scoring.yaml` 로 다시 계산, 바뀐 항목 수를 돌려준다. 저장된 점수에 사용자 점수(`load_score_overrides`)를 덮어 쓰고, 상태는 `merge` 와 같은 규칙으로 유지한다.
  - CLI: `judge.py digest <transcript>`, `judge.py merge <transcript> --mode explicit < judge.json`(collect CLI 와 같이 세션 락을 잡고, 못 잡으면 종료 코드 3), `judge.py rescore`, `judge.py background <transcript> --mode <mode>`(Task 5, 종료 코드는 `background_status` 값: 0 판정했거나 할 일 없음, 1 실패, 2 하루 상한, 3 락이 잡혀 있음).

`judge-schema.json`: `{"items": [{"sig": str, "condition": "C1".."C11", "impact": 1..3, "actionability": 1..3, "evidence": [{"session", "turn", "quote"}] (최소 1), "target": {"path": str}, "proposal": str, "duplicate_of_rule": str|null, "rule_unenforced": bool, "skills": {str: int}}]}`. sig 규칙: `?` 신호는 기존 sig 목록에서 주제가 같은 것을 재사용하고 없을 때만 `<조건>:<kebab-토픽>` 로 새로 만든다(digest 에 이 문장을 그대로 넣는다). `C2:?` 후보마다 `condition` 을 C2 와 C7 중 하나로 골라 항목을 최대 하나만 낸다(`C2_C7_RULE`, digest 에 함께 넣는다).

- [ ] **Step 1: 테스트 작성**

```python
EV = [{"session": "s", "turn": 1, "quote": "q"}]

def test_score_thresholds():
    cfg = state.load_config()
    item = {"sig": "C1:a", "impact": 3, "actionability": 3, "evidence": EV, "target": {"path": "/r/x.md"}}
    assert judge.score({**item, "sessions": 4}, cfg) == (pytest.approx(3.0), "propose")   # 1.2+1.2+0.6
    assert judge.score({**item, "sessions": 1}, cfg) == (pytest.approx(2.2), "propose")   # 0.4+1.2+0.6
    assert judge.score({**item, "sessions": 1, "impact": 1, "actionability": 1}, cfg) == (pytest.approx(1.0), "discarded")

def test_global_target_bumps_threshold():
    cfg = state.load_config()
    item = {"sig": "C7:a", "sessions": 4, "impact": 2, "actionability": 3, "evidence": EV}   # 1.2+0.8+0.6=2.6
    assert judge.score({**item, "target": {"path": "/r/x.md"}}, cfg)[1] == "propose"
    assert judge.score({**item, "target": {"path": "~/.claude/CLAUDE.md"}}, cfg)[1] == "watch"   # 기준 2.7

def test_duplicate_rule_discarded_unless_unenforced(): ...
def test_rejected_sig_stays_rejected(tmp_projects): ...  # decisions 에 rejected → status rejected
def test_merge_twice_same_sig_appends_evidence_keeps_approved(tmp_projects): ...
def test_recurrence_counts_distinct_sessions(tmp_projects): ...  # 3개 세션 signals.jsonl 에 같은 sig → 3
def test_rescore_after_threshold_change(tmp_projects): ...       # propose 를 9 로 바꾸면 propose 항목이 watch 로
def test_digest_contains_rubric_signals_existing_sigs(tmp_projects): ...
def test_merge_rejects_schema_violation(tmp_projects): ...       # impact 4 → ValueError, 파일 변화 없음
```

각 테스트의 기대값은 Global Constraints 기본값으로 손계산해 리터럴로 적는다(예: 재발 1·영향 3·해결 3 → 0.4·1+0.4·3+0.2·3 = 2.2 → `propose`). 첫 테스트의 주석처럼 계산을 미루지 않는다.

- [ ] **Step 2: 실패 확인** — `uv run pytest plugins/harness-loop/tests/test_judge.py -q`
- [ ] **Step 3: 구현** — `judge.py`, `judge-schema.json`. 스키마 검증은 표준 라이브러리로 필요한 필드·범위만 확인한다.
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: 커밋** — `feat: harness-loop judge 입력·채점·병합`

### Task 5: 백그라운드 judge

**Files:**
- Modify: `scripts/judge.py`
- Test: `tests/test_judge_background.py`

**Interfaces:**
- Consumes: Task 3 `collect.run`, Task 4 `digest`·`merge`, `state.acquire_lock`·`release_lock`
- Produces:
  - `background_status(transcript: Path, mode: str, claude_bin: str = "claude") -> int` — `BG_OK`(0)·`BG_FAILED`(1)·`BG_CAP`(2)·`BG_LOCKED`(3) 중 하나. `background(...) -> bool` 은 `background_status(...) == BG_OK` 다. lock 획득 실패면 `BG_LOCKED`. 오늘(`judge-runs.jsonl` 날짜 기준) 실행 수가 `max_background_judges_per_day` 이상이면 `BG_CAP`. 실행 수(`runs_today`)는 예약 줄과 결과 줄을 `(session, at)` 으로 묶어 한 번만 센다. `collect.run` 으로 새 신호를 받고, 이 세션에 아직 판정 안 된 신호(직전 run 이후 신호)가 없으면 `BG_OK`(할 일 없음). 있으면 `digest` 를 stdin 으로 `HARNESS_RETRO=1 <claude_bin> -p --setting-sources project --strict-mcp-config --no-session-persistence --tools "" --output-format json --json-schema <judge-schema.json 내용>` 을 HOME 밖 빈 임시 디렉터리(`tempfile.mkdtemp(prefix="harness-judge-", dir=...)`. `tempfile.gettempdir()` 가 HOME 아래로 풀리면 `/tmp` 아래에 만든다. `finally` 에서 지움)를 cwd 로 실행, 응답 JSON 의 `structured_output`(없으면 `result` 를 JSON 으로 파싱)을 `merge(..., mode)` 에 넘긴다. `claude -p` 를 부르기 전에 `judge-runs.jsonl` 에 예약 줄(`"reserved": true`)을 쓰고, 성공·실패 모두 결과 줄을 한 줄 더 쓴다. 실패 시 `hook-errors.log` 에 한 줄, items 는 그대로, `BG_FAILED`.
  - `spawn_background(transcript: Path, mode: str) -> None` — `uv run --script <judge.py> background <transcript> --mode <mode>` 를 `start_new_session=True`·DEVNULL·`HARNESS_RETRO=1` 로 띄우고 바로 돌아온다.
  - "판정 대기" 판단: `signals.jsonl` 의 마지막 줄 번호와 마지막 `runs/*.json` 의 `signals_upto` 를 비교한다. `merge` 는 run 파일에 `signals_upto` 를 기록한다.

- [ ] **Step 1: 실측 확인** — `claude -p --no-session-persistence --tools "" --output-format json --json-schema '{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"]}' <<< "reply ok=true"` 를 실행해 응답 JSON 에서 구조화 출력이 들어 있는 키 이름을 확인하고, 그 키를 `background` 의 파싱 우선순위로 쓴다. `--tools ""` 와 함께 쓸 때 구조화 출력이 나오지 않으면 `--tools ""` 를 빼고 `--json-schema` 만 쓴다(입력은 stdin 뿐이라 도구가 필요 없다).
- [ ] **Step 2: 테스트 작성** — `claude_bin` 에 가짜 실행 파일(`tmp_path/fake-claude`, 고정 JSON 출력 또는 종료 코드 1)을 넘긴다.

```python
def test_background_judge_merges_fake_output(tmp_projects, fake_claude_ok): ...
def test_background_judge_failure_leaves_state_untouched(tmp_projects, fake_claude_fail):
    t = copy_fixture(tmp_projects, "basic.jsonl")
    assert judge.background(t, "session-end", claude_bin=fake_claude_fail) is False
    assert not (state.harness_dir(t) / "action-items.json").exists()
    assert "basic" in (state.data_dir() / "hook-errors.log").read_text()
def test_daily_cap_blocks_extra_runs(tmp_projects, fake_claude_ok): ...   # cap 1 → 두 번째 False
def test_locked_session_is_skipped(tmp_projects, fake_claude_ok): ...
def test_nothing_new_returns_true_without_calling_claude(tmp_projects, fake_claude_fail): ...
```

- [ ] **Step 3: 실패 확인 → Step 4: 구현 → Step 5: 통과 확인** — `uv run pytest plugins/harness-loop/tests/test_judge_background.py -q`
- [ ] **Step 6: 커밋** — `feat: harness-loop 백그라운드 judge`

### Task 6: 반영 대상 판정

**Files:**
- Create: `scripts/apply_target.py`, `tests/test_apply_target.py`

**Interfaces:**
- Produces: `classify(path: str) -> dict` — `{"kind": "repo"|"local"|"plugin_cache"|"new", "real_path": str, "root": str|None, "rules": [str]}`.
  - `~` 확장 후 realpath. 경로가 `~/.claude/plugins/cache/` 또는 `~/.claude/plugins/marketplaces/` 아래면 `plugin_cache`.
  - 파일이 없으면 가장 가까운 존재하는 상위 디렉터리로 판정하고 `kind` 는 `new`, `root` 는 그 디렉터리의 git toplevel(없으면 None).
  - `git -C <dir> rev-parse --show-toplevel` 이 성공하면 `repo`, `rules` 는 toplevel 의 `CLAUDE.md`·`AGENTS.md`·`.claude/CLAUDE.md` 중 존재하는 것.
  - 그 밖은 `local`.
  - CLI: `apply_target.py <path>` → JSON.

- [ ] **Step 1: 테스트 작성** — `tmp_path` 에 git 저장소(`git init`)와 `CLAUDE.md`, 그 안을 가리키는 심볼릭 링크, git 밖 파일, `plugins/cache` 흉내 경로(`HOME` 을 `tmp_path` 로 바꿔서)를 만든다.

```python
def test_symlink_resolves_to_owner_repo(repo_with_symlink): assert classify(link)["kind"] == "repo" and classify(link)["root"] == str(repo)
def test_repo_rules_listed(...): assert classify(f)["rules"] == [str(repo / "CLAUDE.md")]
def test_outside_git_is_local(...): ...
def test_plugin_cache_is_not_editable(...): ...
def test_missing_file_is_new_with_parent_repo(...): ...
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인** — `uv run pytest plugins/harness-loop/tests/test_apply_target.py -q`
- [ ] **Step 5: 커밋** — `feat: harness-loop 반영 대상 소유 환경 판정`

### Task 7: SessionStart 보고·sweep, 훅 진입점

**Files:**
- Create: `scripts/report.py`, `scripts/hook.py`, `hooks/hooks.json`, `tests/test_report.py`, `tests/test_hook.py`

**Interfaces:**
- Consumes: Task 1~6 전부
- Produces:
  - `report.sweep(now_iso: str, exclude_session: str | None = None) -> list[Path]` — `installed_at` 이후 수정된 기록 중 `harness/skip` 이 없고(`cursor` < 줄 수이거나 판정 대기 신호가 있는) 세션 목록. 최근 `OPEN_SESSION_SEC`(10분) 안에 바뀐 기록과 `exclude_session` 은 뺀다. harness 디렉터리를 만들지 않는다(`state.harness_path`).
  - `report.backfill(now_iso: str, days: int | None = None) -> list[Path]` — `installed_at` 이전(같은 시각 포함)에 수정된 기록을 mtime 최신순으로. `days` 가 있으면 최근 `days` 일 안의 것만, `harness/skip` 세션은 뺀다. harness 디렉터리를 만들지 않는다. sweep 대상과 겹치지 않는다.
  - `report.regressions() -> list[dict]` — `decisions` 에서 `approved`·`applied` 인 sig 가, 결정 시각 이후에 시작된 세션의 `signals.jsonl` 에 다시 나오면 그 세션 items 에 `status: regressed` 항목을 만든다. 대상(`target.owner` 가 `plugin_cache` 로 시작하거나 `target.kind == "plugin_cache"`)이 외부 플러그인 캐시면 sig 마다 처음 한 번만 `regressed` 이고, 그 뒤 재발은 `watch`(`regressed_after` 유지, `reported_at` 제거)로 쓴다. 바뀔 세션만 락을 잡고 다시 읽어 계산한다. 락이 잡혀 있으면 그 세션을 건너뛰고 `hook-events.log` 에 한 줄 남긴다(훅 오류 건수에 넣지 않는다. 다음 SessionStart 에서 다시 시도). 세션 기록 경로는 harness 디렉터리에서 거꾸로 구하고, 없으면 `state.find_transcript` 로 찾는다(`_transcript_of`).
  - `report.pending(limit: int) -> list[tuple[Path, dict]]` — 모든 세션에서 `status in {propose, regressed}` 이고 `reported_at` 이 없는 항목, `score` 내림차순 `limit` 개.
  - `report.session_start_text(items, hook_errors: int) -> str` — 비어 있으면 `""`. 형식: 첫 줄 `harness-loop: 검토 대기 action item <N>건 — /harness-loop:retro review`, 이어서 항목마다 `- [<condition>] <proposal 첫 줄 80자> (<날짜> <프로젝트>)`, 오류가 있으면 `- 훅 오류 <n>건: <hook-errors.log 경로>`.
  - `report.mark_reported(pairs) -> None` — 세션마다 락을 잡고 `reported_at` 을 쓴다. 락이 잡혀 있으면 그 세션을 건너뛰고 `hook-events.log` 에 한 줄 남긴다(훅 오류 건수에 넣지 않는다. 그 항목은 다음에 다시 보고된다).
  - `report.decide(sig: str, decision: str, session: str | None = None, impact: int | None = None, actionability: int | None = None) -> dict` — decision ∈ `approved|rejected|applied|edit-score`. 해당 세션(없으면 sig 가 있는 모든 세션)의 락을 모두 잡는다(`state.lock_all`: `LOCK_TRIES`×`LOCK_SLEEP` 안에 못 잡으면 잡은 락을 풀고 아무것도 쓰지 않은 채 `ValueError`). 락 안에서 다시 읽어 items 를 갱신·저장하고(edit-score 면 다시 채점), 마지막에 `append_decision`(edit-score 면 준 `impact`·`actionability` 포함).
  - `report.list_items(scope: str) -> list[dict]` — scope ∈ `pending|session:<id>|all`.
  - CLI: `report.py where` — `CLAUDE_CODE_SESSION_ID` 의 기록 경로(`state.find_transcript`)를 출력, 없으면 종료 코드 2. `report.py list [--session <id>|--all] [--mark-reported]` — `--mark-reported` 는 어느 범위와도 함께 쓸 수 있고, 출력한 항목 중 `propose`·`regressed` 만 `mark_reported` 한다(출력은 표시 전 목록). `report.py decide <sig> <decision> [--session <id>] [--impact N] [--actionability N]`. `report.py backfill-list [--days N]` — `backfill` 결과를 한 줄에 경로 하나씩 출력, 없으면 아무것도 출력하지 않고 종료 코드 0.
  - `hook.py session-start|session-end` — stdin 훅 JSON. 가드(Global Constraints) 해당 시 `skip` 쓰고 종료. session-end: `spawn_background(transcript_path, "session-end")`. session-start: `source` 가 `startup|resume` 일 때만, `regressions()` → `sweep(now, exclude_session=훅 입력 session_id)` 결과 중 오늘 남은 상한(`max_background_judges_per_day - runs_today()`)만큼 `spawn_background(t, "sweep")` → `pending(queue_limit)` 을 `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": <text>}}` 로 stdout, `mark_reported`. `HERDR_PW_WORKER` 가 설정돼 있으면 보고를 출력하지 않는다(sweep 과 regressions 는 돈다). 예외는 모두 잡아 `hook-errors.log` 에 쓰고 종료 코드 0.
  - `hooks/hooks.json`: SessionStart `matcher: "startup|resume"`, `timeout: 10`; SessionEnd `timeout: 10`; 명령 `uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/hook.py" session-start`(또는 `session-end`).

- [ ] **Step 1: 테스트 작성**

```python
def test_pending_respects_limit_and_order(tmp_projects): ...       # 5개 중 score 상위 3개
def test_reported_items_not_repeated(tmp_projects): ...
def test_regression_after_approval(tmp_projects): ...              # approved 뒤 새 세션에 같은 sig → regressed
def test_sweep_skips_skip_marker_and_preinstall(tmp_projects): ...
def test_decide_reject_blocks_future_merge(tmp_projects): ...
def test_session_start_text_format(): assert text.splitlines()[0] == "harness-loop: 검토 대기 action item 2건 — /harness-loop:retro review"
def test_hook_guard_harness_retro_writes_skip(tmp_projects, monkeypatch): ...
def test_hook_guard_agent_id(tmp_projects): ...
def test_hook_bad_input_exits_zero_and_logs(tmp_projects):
    r = subprocess.run([sys.executable, HOOK, "session-start"], input="not json", capture_output=True, text=True, env=env_without_plugin_data)
    assert r.returncode == 0 and r.stdout == ""
    assert "session-start" in (fallback_data_dir / "hook-errors.log").read_text()
def test_hook_session_start_emits_additional_context(tmp_projects): ...  # JSON 파싱 가능, hookEventName == "SessionStart"
def test_hook_session_start_ignores_compact_source(tmp_projects): ...
def test_hook_session_start_skips_worker_env(tmp_projects): ...   # HERDR_PW_WORKER=w1 → stdout "", sweep 은 실행
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인** — `uv run pytest plugins/harness-loop/tests/test_report.py plugins/harness-loop/tests/test_hook.py -q`
- [ ] **Step 5: 커밋** — `feat: harness-loop SessionStart 보고·sweep과 훅`

### Task 8: retro 스킬과 문서

**Files:**
- Create: `plugins/harness-loop/skills/retro/SKILL.md`, `plugins/harness-loop/tests/test_skill.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: CLI 전부 — `collect.py`, `judge.py digest|merge|rescore|background`, `report.py where|list|decide|backfill-list`, `apply_target.py`

SKILL.md 가 정하는 절차(규칙만 쓴다):

| 모드 | 진입 | 절차 |
|---|---|---|
| 현재 세션 회고 | `/harness-loop:retro` 또는 "이 세션 회고해줘"·"action item 정리해줘" | `report.py where` 로 현재 세션 기록 경로 얻기 → `collect.py` → `judge.py digest` → digest 와 대화 전체를 근거로 `judge-schema.json` 형식 JSON 작성 → `judge.py merge --mode explicit` → `report.py list --session <id>` 로 리포트 출력 → `report.py list --session <id> --mark-reported` 로 보고 표시 후 턴 종료 |
| 쌓인 항목 검토 | `review` 또는 SessionStart 알림을 보고 "검토할게" | `report.py list` → 리포트 출력 후 턴 종료 |
| 지난 기록 처리 | `backfill [--days N]` | `report.py backfill-list [--days N]` 출력을 파일로 받아(비면 0 보고 후 멈춤) 한 줄씩 `judge.py background <transcript> --mode sweep` 를 순서대로 실행(Bash 한 번에 기록 하나). 종료 코드 2(하루 상한)면 멈추고, 1(실패)·3(락)은 세고 다음 기록으로 간다. 처리 수(성공·실패·건너뜀·남은 기록) 보고 |
| 결정 | 사용자 자유 입력("1 승인, 2 거절", "2번 영향 3으로") | `report.py decide` → 승인 항목마다 `apply_target.py <path>` → kind 별 반영 |

반영 규칙: `repo` → `rules` 파일을 읽고 그 저장소의 기여 절차(스킬이 있으면 그 스킬, 예: contribute)를 따른다. `local` → diff 를 보여 승인받아 적용한다. `plugin_cache` → 파일을 고치지 않고 교훈을 메모리에 남기고 업스트림 이슈 초안을 리포트에 쓴다. `new` → 위치와 초안을 보이고 승인받아 만든 뒤 그 위치의 `kind` 규칙을 따른다. 반영이 끝나면 `report.py decide <sig> applied`. edit-score 로 고친 점수는 그 sig 의 모든 세션에서 이후 병합·재채점보다 우선한다.

리포트 형식: 조건·sig·점수(재발/영향/해결)·대상·소유 환경·개선안 요지·근거 인용 1~2개를 항목마다 표 한 줄과 아래 상세로 쓴다. 선택 질문 도구(AskUserQuestion)는 쓰지 않고 사용자의 자유 입력으로 결정을 받는다.

description 에는 트리거 문구 "회고", "action item", "이 세션 돌아보기", "개선점 정리", "retro", "검토 대기" 를 넣는다.

- [ ] **Step 1: 테스트 작성** — `test_skill.py`: frontmatter `name: retro`, 500줄 이하, 본문에서 백틱으로 가리키는 `scripts/…`·`defaults/…` 경로가 플러그인 안에 존재, 스크립트 경로는 `${CLAUDE_PLUGIN_ROOT}/scripts/` 형식, description 에 위 트리거 문구 포함, 본문에 `AskUserQuestion` 을 쓰라는 지시가 없음, `queue_limit` 등 config 값 숫자를 하드코딩하지 않음(`queue_limit` 은 이름으로만).
- [ ] **Step 2: 실패 확인 → Step 3: SKILL.md·README 작성 → Step 4: 통과 확인** — `uv run pytest -q`
- [ ] **Step 5: 교정·리뷰** — `humanizer-ko:korean-proofreader` 로 SKILL.md·README 교정, `skill-reviewer` Self-Review. P1(근거 설명) 지적은 "사용자 지시로 근거 생략"으로 처리한다.
- [ ] **Step 6: 커밋** — `feat: harness-loop retro 스킬`

### Task 9: 실데이터 검증과 설치

**Files:**
- Modify: `defaults/scoring.yaml`(기준값 조정이 필요할 때만), `docs/superpowers/specs/2026-10-16-harness-loop-design.md` §8(`CLAUDE_PLUGIN_DATA` 경로)
- Delete: `~/.claude/bin/harness-collect.py`(저장소 밖)

Step 1~2 의 모든 명령은 `HARNESS_PROJECTS_ROOT=<scratchpad>/projects CLAUDE_PLUGIN_DATA=<scratchpad>/plugin-data` 로 실행해 실제 플러그인 상태(`installed_at`, `judge-runs.jsonl`, `decisions.jsonl`)를 건드리지 않는다.

- [ ] **Step 1: 수집 재현** — 실제 기록 복사본으로 collect 를 돌린다: `<scratchpad>/projects` 에 `~/.claude/projects` 를 `cp -R` 하고 모든 jsonl 에 `collect.py` 실행. 기대: eval 세션 62개 내외에 `skip`, `C1:` 상위에 Atlassian `app is not installed` 토픽, `C3:cmd:acli jira workitem`·`py-inline[]` 이 다수 세션, `C4:bare-yes` 가 20개 세션 내외. 조건별 신호 수와 세션 수를 표로 남긴다.
- [ ] **Step 2: 기준값 점검** — 신호가 많은 세션 5개에 `judge.py background --mode sweep` 를 실행(복사본 루트에서), propose·watch·discarded 분포와 `pending(queue_limit)` 결과를 표로 남긴다. propose 가 세션당 평균 3건을 넘으면 `propose` 를 올리는 안을 사용자에게 보고한다(직접 바꾸지 않는다).
- [ ] **Step 3: 설치** — `/plugin marketplace add ~/src/skills`(이미 있으면 update) → `/plugin install harness-loop@tech-writing`. `ls ~/.claude/plugins/data/` 로 데이터 디렉터리 이름을 확인해 spec §8 을 갱신한다.
- [ ] **Step 4: 끝까지 한 번 돌리기** — 새 세션에서 짧은 작업 → `/harness-loop:retro` 두 번(두 번째는 새 턴만 처리되는지) → 세션 종료 → 새 세션 시작 때 알림이 뜨는지(기본 채점에서 한 세션에서만 나온 sig 는 `watch` 라 알리지 않으므로, 다른 세션에서도 나온 sig 로 확인한다) → `review` 에서 거절 1건 → 다음 판정에서 그 sig 가 올라오지 않는지 확인한다.
- [ ] **Step 5: 기존 수집기 제거** — `~/.claude/bin/harness-collect.py` 삭제(사용자 확인 후).
- [ ] **Step 6: 커밋** — `docs: harness-loop 실데이터 검증 결과와 설치 경로 반영`
