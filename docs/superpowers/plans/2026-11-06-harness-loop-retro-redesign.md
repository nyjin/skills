# harness-loop retro 재설계 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 훅이 해석 없이 근거만 기록하고, `/harness-loop:retro` 가 그 근거를 AI 로 판정·묶기·파일 검증한 뒤 결정적 관문을 넘은 결정 대상만 보고하고, 사용자의 진행·티켓·거절을 라벨로 남겨 rubric·replay 로 판정 기준을 고쳐 가게 한다.

**Architecture:** 결정적인 일(근거 기록, 버전 해시·사본·git 조회, 발췌, 관문, 라벨, 정밀도, rubric 버전, replay 채점)은 `uv run --script` 로 실행하는 Python 스크립트가 하고 pytest 로 고정한다. 해석(관찰 해당 여부, 묶기, 파일 검증, 수정안)은 retro 스킬이 Opus 서브에이전트에게 맡기고, 그 출력은 JSON 스키마 검증을 거쳐 스크립트가 저장한다. 백그라운드 `claude -p` judge 와 C1~C11 수집·가중합 채점은 없앤다.

**Tech Stack:** Python 3.11+ + PyYAML(PEP 723, 훅·스크립트 모두 `uv run --script`), pytest(개발, 저장소 루트 `uv run pytest`), git CLI, Claude Code plugin hooks(SessionStart, UserPromptSubmit, UserPromptExpansion, PreToolUse, PostToolUse, PostToolUseFailure), Agent 도구(`model: "opus"`).

**Spec:** `docs/superpowers/specs/2026-11-06-harness-loop-retro-redesign-design.md` (대체하지 않은 부분은 `docs/superpowers/specs/2026-10-16-harness-loop-design.md`)

## Global Constraints

- 실행 위치: 이 worktree(`feat/harness-loop`)는 사용자 설치 플러그인이 직접 읽는 경로라 커밋이 다음 세션부터 바로 실사용된다. 구현은 별도 worktree·브랜치(`feat/harness-loop-redesign`, `feat/harness-loop` 에서 분기)에서 하고, Task 13 Step 4 직전에 `feat/harness-loop` 로 병합한다. 그 전에는 이 worktree 를 바꾸지 않는다.
- 모든 경로는 `plugins/harness-loop/` 기준. 스크립트는 기존처럼 `#!/usr/bin/env python3` 와 PEP 723 블록(`requires-python = ">=3.11"`, `dependencies = ["pyyaml>=6"]`)으로 시작하고 `uv run --script` 로 실행한다(사용자 결정).
- 세션 상태: `<projects_root>/<프로젝트>/<세션ID>/harness/` 아래 `evidence.jsonl`, `evidence.cursor`, `skill-state.json`, `scripts/<sha1>.txt`, `skip`. `projects_root` 는 `HARNESS_PROJECTS_ROOT`, 없으면 `~/.claude/projects`.
- 공용 상태: `${CLAUDE_PLUGIN_DATA}`(없으면 `~/.claude/plugins/data/harness-loop-local/`) 아래 `config.yaml`, `rubric.md`(첫 줄 `<!-- rubric-version: N -->`), `observations.jsonl`, `groups.json`, `decisions.jsonl`, `rubric-changes.jsonl`, `skill-snapshots/<blob해시>`, `replay/<묶음 id>/input.json`, `notice-day`, `hook-errors.log`, `installed_at`. 이 데이터는 저장소에 넣지 않는다.
- `config.yaml` 기본값(YAML 매핑): `{"eval_markers": ["트리거 측정 중"], "window_days": 30, "script_min_count": 3, "script_min_sessions": 2, "correction_min_count": 2, "guideline_min_sessions": 2, "repeat_edit_min": 3, "label_examples": 10, "rubric_min_evidence": 3, "replay_min_labels": 30, "judge_batch": 40}`. 옛 `scoring.yaml` 은 읽지 않는다.
- 관찰 종류: `correction`(교정), `repeat`(반복 실행), `failure`(실패·이탈), `approval_question`(승인용 질문).
- 결정 대상 종류: `script`(스크립트화), `skill_supplement`(스킬 보완), `guideline_supplement`(지침 보완), `fix`(지침·스크립트 수정), `rework`(수정 방식 재검토). `rework` 하위 종류: `recurrence`, `verified_recurrence`, `repeat_edit`.
- 결정: `proceed`(진행), `ticket`(티켓), `reject` + `kind` ∈ `not-defect`·`trivial`·`wont-fix`. 라벨: proceed·ticket → positive, not-defect·trivial → negative, wont-fix → excluded. 분할: `sha1(묶음 id)` 첫 바이트 < 128 이면 test, 아니면 train.
- 옛 `decisions.jsonl` 줄(`sig` 키, `group` 키 없음)은 읽을 때 무시한다.
- 훅은 어떤 경우에도 종료 코드 0, stdout 은 SessionStart 알림 외에 아무것도 쓰지 않는다. 오류는 `hook-errors.log` 한 줄. 훅 안에서 LLM·네트워크 호출 금지.
- 가드: `HARNESS_RETRO=1` 이거나 훅 입력에 `agent_id` 가 있으면 아무것도 하지 않는다. 세션 `harness/skip` 이 있으면 기록하지 않는다. 프롬프트에 `eval_markers` 중 하나가 있으면 `skip` 을 만들고 그 뒤 기록하지 않는다. `HERDR_PW_WORKER` 세션은 기록하되 SessionStart 알림을 내지 않는다.
- 승인용 질문 제외 대상: 되돌리기 어렵거나 외부로 나가는 동작 앞의 확인(push, 삭제, 메시지 전송, 티켓 생성, 배포). 이 규칙은 `defaults/rubric.md` 에 둔다.
- git 밖 대상의 안내 문구(그대로): `버전 추적 불가: <스킬 루트> 는 git 저장소가 아니라 수정 전후 비교와 재발 판정을 할 수 없습니다. 이 디렉터리를 git 으로 관리하면(git init 후 커밋) 다음 retro 부터 추적합니다. 해당 스킬: <이름들>`. 외부 플러그인 캐시(`~/.claude/plugins/cache`·`marketplaces` 아래)는 이 안내 대신 `원본 저장소에 티켓을 내세요: <경로>` 를 쓴다.
- SessionStart 알림 문구(그대로, 하루 한 번): `harness-loop: 판정하지 않은 근거 N건이 있습니다. /harness-loop:retro 로 확인하세요.` 훅 오류가 새로 있으면 같은 줄 끝에 ` (훅 오류 M건, hook-errors.log)`.
- 스킬 문서는 규칙·표·명령만 쓰고, 산문은 한 단락 한 줄, config 값은 속성명으로 참조한다. 사용자에게 하는 말은 한국어.
- 커밋 메시지는 `feat: …`/`fix: …`/`docs: …`/`refactor: …` 한국어, 끝에 `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. push·PR 하지 않는다.
- 테스트와 실측은 실제 `~/.claude/projects/*/*/harness`·`~/.claude/plugins/data` 를 건드리지 않는다(`conftest.py` 의 `HARNESS_PROJECTS_ROOT`·`CLAUDE_PLUGIN_DATA`·`HOME` 격리를 유지).

## Review Focus

1. **병렬 도구 호출로 훅 여러 개가 같은 `evidence.jsonl` 에 동시에 씀** — 줄이 섞이거나 깨지지 않아야 한다. → Task 3 `test_concurrent_appends_stay_valid_jsonl`.
2. **retro 가 관찰 저장 뒤 묶기 전에 끊긴 뒤 다시 실행** — 같은 근거가 두 번 판정·저장되지 않아야 한다. → Task 6 `test_observe_is_idempotent_per_evidence`.
3. **관찰 뒤 스킬 파일이 지워지거나 옮겨짐** — 해시가 `None` 이어도 기록·묶기·관문이 크래시 없이 그 묶음을 검증 실패로 버린다. → Task 2 `test_blob_hash_missing_file_is_none`, Task 7 `test_unverified_group_is_dropped`.
4. **transcript 경로가 아직 없거나 마지막 줄이 쓰이는 중** — 훅이 `pos` 를 파일 크기로 두고 크래시 없이 기록, 발췌는 잘린 줄을 건너뜀. → Task 3 `test_missing_transcript_records_pos_zero`, Task 6 `test_excerpt_skips_partial_line`.
5. **Bash 명령에 비 UTF-8·CRLF·매우 긴 heredoc** — 본문 파일 저장이 깨지지 않고 근거 `text` 는 2,000자로 자른다. → Task 4 `test_inline_body_crlf_and_long`.

---

## 파일 구조

| 경로 | 책임 | 처리 |
|---|---|---|
| `scripts/paths.py` | 데이터·세션 디렉터리, config, 원자적 쓰기, 덧붙이기, 락, 오류 로그 | 새로 |
| `scripts/versions.py` | git blob 해시, 커밋 전 사본, git 조회, 버전 이력, git 밖 판정 | 새로 |
| `scripts/evidence.py` | 훅 이벤트 → 근거 기록, 현재 스킬 상태, inline 스크립트 저장, 거절 표시 | 새로 |
| `scripts/hook.py` | 훅 진입점(이벤트 분기, 가드, SessionStart 알림) | 다시 씀 |
| `scripts/backfill.py` | 지난 transcript → 훅 입력 재구성 → `evidence.handle` | 새로 |
| `scripts/observe.py` | 미판정 근거 묶음(발췌 포함), 관찰 저장, cursor | 새로 |
| `scripts/groups.py` | 묶음 저장·병합, 관문, 재발, git 밖 안내 | 새로 |
| `scripts/decide.py` | 결정 기록, 라벨, 분할, 정밀도, replay 입력 보관, 티켓 근거 파일 | 새로 |
| `scripts/rubric.py` | rubric 후보, 적용·버전, replay 채점 | 새로 |
| `scripts/retro.py` | 스킬이 부르는 CLI(하위 명령으로 위 모듈 호출) | 새로 |
| `scripts/transcript.py` | 턴 파싱 | 그대로 |
| `scripts/apply_target.py` | 대상 소유 환경 판정 | 그대로 |
| `scripts/state.py`, `collect.py`, `judge.py`, `report.py` | 옛 파이프라인 | 삭제(Task 11) |
| `defaults/config.yaml`, `defaults/rubric.md`, `defaults/observe-schema.json`, `defaults/groups-schema.json`, `defaults/replay-schema.json` | 기본 설정·판정 기준·서브에이전트 출력 스키마 | 새로(rubric.md 는 다시 씀) |
| `defaults/scoring.yaml`, `defaults/judge-schema.json` | 옛 설정 | 삭제(Task 11) |
| `hooks/hooks.json` | 훅 등록 | 다시 씀 |
| `skills/retro/SKILL.md` | retro 절차 | 다시 씀 |
| `tests/fixtures/payloads/*.json`, `tests/fixtures/redesign/*.jsonl` | 실측 훅 입력, 기록 조각 | 새로 |

스키마 검증 라이브러리를 늘리지 않기 위해 `observe.py` 의 `validate(obj, schema)` 가 필요한 부분(`type`, `required`, `enum`, `properties`, `items`, `minItems`, `minLength`)만 구현하고, `groups.py`·`rubric.py`·`retro.py` 가 그것을 쓴다.

---

### Task 1: 경로·설정·락 기반(`paths.py`)

**Files:**
- Create: `scripts/paths.py`, `defaults/config.yaml`, `tests/test_paths.py`

**Interfaces:**
- Produces:
  - `DEFAULT_CONFIG: dict` (Global Constraints 의 값)
  - `data_dir() -> Path` — 없으면 만들고 `config.yaml`·`rubric.md` 를 `defaults/` 에서 처음 한 번 복사, `installed_at` 기록. 이미 있는 `rubric.md` 에 `## 관찰로 보지 않는 경우` 가 없으면(옛 채점 기준) `rubric-old.md` 로 이름을 바꾸고 새 기본값을 복사한다. 옛 운영 파일(`scoring.yaml`, `judge-runs.jsonl`, `hook-errors.seen`)은 읽지 않고 그대로 둔다
  - `load_config() -> dict` — `config.yaml` 을 `DEFAULT_CONFIG` 위에 덮음. 깨진 YAML·매핑 아님 → 기본값 + `hook-errors.log` 한 줄. 옛 `scoring.yaml` 은 읽지 않는다
  - `projects_root() -> Path`, `harness_path(transcript) -> Path`(만들지 않음), `harness_dir(transcript) -> Path`(만듦), `all_harness_dirs() -> list[Path]`
  - `now() -> str`(로컬 시간대 ISO), `aware(text) -> datetime`
  - `atomic_write(path, text) -> None`, `append_line(path, obj: dict) -> None`(한 번의 `os.write` 로 `O_APPEND`)
  - `acquire_lock(dir, stale_sec=600) -> bool`, `release_lock(dir) -> None`, `LOCKED_EXIT = 3` (동작은 옛 `state.py` 와 같음)
  - `log_error(where: str, error: BaseException) -> None`(절대 예외를 내지 않음)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_paths.py`

```python
def test_data_dir_copies_defaults_once(tmp_path):
    paths = load_script("paths")
    d = paths.data_dir()
    assert yaml.safe_load((d / "config.yaml").read_text())["replay_min_labels"] == 30
    (d / "config.yaml").write_text("window_days: 7\n")
    paths.data_dir()
    assert paths.load_config()["window_days"] == 7
    assert paths.load_config()["script_min_count"] == 3

def test_old_rubric_replaced_once():
    paths = load_script("paths")
    d = Path(os.environ["CLAUDE_PLUGIN_DATA"]); d.mkdir(parents=True)
    (d / "rubric.md").write_text("<!-- rubric-version: 1 -->\n# 채점 기준\n")
    paths.data_dir()
    assert "## 관찰로 보지 않는 경우" in (d / "rubric.md").read_text()
    assert "# 채점 기준" in (d / "rubric-old.md").read_text()

def test_broken_config_falls_back_and_logs():
    paths = load_script("paths")
    (paths.data_dir() / "config.yaml").write_text("[1,")
    assert paths.load_config() == paths.DEFAULT_CONFIG
    assert "config.yaml" in (paths.data_dir() / "hook-errors.log").read_text()

def test_append_line_writes_one_json_per_line(tmp_path):
    paths = load_script("paths")
    p = tmp_path / "e.jsonl"
    paths.append_line(p, {"a": "줄\n바꿈"})
    paths.append_line(p, {"b": 2})
    assert [json.loads(l) for l in p.read_text().splitlines()] == [{"a": "줄\n바꿈"}, {"b": 2}]

def test_lock_is_exclusive_and_stale_lock_reclaimed(tmp_path): ...  # 옛 test_state 의 같은 이름 테스트를 paths 로 옮김
```

- [ ] **Step 2: 실패 확인** — `uv run pytest plugins/harness-loop/tests/test_paths.py -q` → FAIL(`paths` 없음)
- [ ] **Step 3: `paths.py` 와 `defaults/config.yaml` 구현**
- [ ] **Step 4: 통과 확인** — `uv run pytest plugins/harness-loop/tests -q` → 새 테스트 PASS, 기존 테스트도 PASS
- [ ] **Step 5: 커밋** — `refactor: harness-loop 경로·설정 모듈 추가`

---

### Task 2: 스킬 버전(`versions.py`)

**Files:**
- Create: `scripts/versions.py`, `tests/test_versions.py`

**Interfaces:**
- Consumes: `paths.data_dir`
- Produces:
  - `blob_hash(path) -> str | None` — `sha1(b"blob %d\0" % len(data) + data)` 16진. 파일이 없거나 읽을 수 없으면 `None`
  - `git_root(path) -> str | None` — `git -C <dir> rev-parse --show-toplevel`, 실패 시 `None`
  - `in_plugin_cache(path) -> bool` — `apply_target._in_plugin_cache` 재사용
  - `remember(path) -> str | None` — 해시를 계산하고, git 밖이거나(`git_root` 없음) 그 해시가 커밋에 없으면(`git cat-file -e <해시>` 실패 또는 `git log --all --find-object=<해시>` 결과 없음) `skill-snapshots/<해시>` 에 내용을 저장(이미 있으면 그대로). 해시를 돌려줌
  - `commit_of(path, h) -> dict | None` — `git log --all --format=%H%x09%cI%x09%s --find-object=<h> -- <상대경로>` 의 가장 오래된 줄 `{"commit", "at", "subject"}`
  - `history(path, since: str) -> list[dict]` — 그 파일의 버전 목록(시각 오름차순) `{"hash", "at", "source": "commit"|"snapshot", "commit"?, "subject"?}`. 커밋은 `git log --format=%H%x09%cI%x09%s -- <상대경로>` 에서 각 커밋의 `git rev-parse <커밋>:<상대경로>` blob, 사본은 `skill-snapshots` 파일 mtime. 같은 해시는 처음 것만
  - `hash_at(path, at: str) -> str | None` — `git log -1 --before=<at> --format=%H -- <상대경로>` 의 blob(백필용). git 밖이면 `None`
  - `untracked_notice(paths: dict[str, list[str]]) -> list[str]` — `{스킬 루트: [스킬 이름]}` → Global Constraints 의 안내 문구 목록(플러그인 캐시는 티켓 문구)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_versions.py`. 픽스처 `repo`(tmp_path 에 `git init`, `user.email`·`user.name` 설정, `skills/a/SKILL.md` 커밋)

```python
def test_blob_hash_matches_git(repo):
    f = repo / "skills/a/SKILL.md"
    assert versions.blob_hash(f) == run(["git", "-C", repo, "hash-object", f]).strip()

def test_blob_hash_missing_file_is_none(tmp_path):
    assert versions.blob_hash(tmp_path / "없음.md") is None

def test_committed_version_not_snapshotted(repo):
    h = versions.remember(repo / "skills/a/SKILL.md")
    assert versions.commit_of(repo / "skills/a/SKILL.md", h)["subject"] == "init"
    assert not (paths.data_dir() / "skill-snapshots" / h).exists()

def test_uncommitted_version_snapshotted_then_found_after_commit(repo):
    f = repo / "skills/a/SKILL.md"; f.write_text("v2\n")
    h = versions.remember(f)
    assert (paths.data_dir() / "skill-snapshots" / h).read_text() == "v2\n"
    git(repo, "commit", "-am", "v2")
    assert versions.commit_of(f, h)["subject"] == "v2"

def test_history_merges_commits_and_snapshots_in_time_order(repo): ...
    # init 커밋 → 작업트리 v2 사본 → v2 커밋 → v3 사본: 해시 3개, source 순서 commit, snapshot(v2 는 사본이 먼저 보였어도 한 번만), snapshot

def test_outside_git_snapshots_and_notice(tmp_path):
    f = tmp_path / "skills/b/SKILL.md"; f.parent.mkdir(parents=True); f.write_text("x")
    assert versions.git_root(f) is None
    assert (paths.data_dir() / "skill-snapshots" / versions.remember(f)).exists()
    [line] = versions.untracked_notice({str(tmp_path / "skills"): ["b"]})
    assert line.startswith("버전 추적 불가: ") and line.endswith("해당 스킬: b")
```

- [ ] **Step 2: 실패 확인** — `uv run pytest plugins/harness-loop/tests/test_versions.py -q` → FAIL
- [ ] **Step 3: `versions.py` 구현** — git 은 `subprocess.run(..., capture_output=True, text=True, timeout=5)`, 실패는 `None`/빈 목록
- [ ] **Step 4: 통과 확인** — 같은 명령 → PASS
- [ ] **Step 5: 커밋** — `feat: harness-loop 스킬 버전 해시·사본·git 조회`

---

### Task 3: 근거 기록 핵심(`evidence.py`): 프롬프트·스킬 상태·가드

**Files:**
- Create: `scripts/evidence.py`, `tests/test_evidence.py`, `tests/fixtures/payloads/`(실측 입력)

**Interfaces:**
- Consumes: `paths.*`, `versions.remember`, `versions.blob_hash`
- Produces:
  - 근거 레코드(한 줄): `{"id": "<세션ID 앞 8자>-<순번>", "kind": "prompt"|"skill_start"|"bash"|"failure"|"ask"|"skill_edit", "at", "session", "transcript", "pos": int, "cwd", "skill": {"name", "path", "hash", "prompts_since"} | None, "text": str(최대 2,000자), "file": str | None, "flags": dict, "meta": dict}`. 순번은 그 세션 `evidence.jsonl` 의 줄 수 + 1
  - `handle(event: str, data: dict, *, pos: int | None = None, at: str | None = None, hash_of=versions.remember) -> list[dict]` — 이벤트를 레코드로 바꿔 덧붙이고 돌려줌. `pos` 기본값은 transcript 파일 크기(없으면 0), `at` 기본값은 `paths.now()`. `event` 는 훅 이벤트 이름 그대로(`UserPromptSubmit` 등)
  - `skill_state(hdir) -> dict | None`, `set_skill(hdir, name, path, pos) -> None`
  - `skill_base_dir(transcript, start_pos: int) -> str | None` — `start_pos` 이후 바이트에서 `Base directory for this skill: (\S+)` 첫 매치
  - `after_rejection(transcript, pos: int) -> bool` — `pos` 앞 마지막 64KB 에서 마지막 `tool_result` 가 `is_error` 이고 내용이 `transcript.REJECTED` 로 시작하면 `True`

규칙:
- `UserPromptSubmit`: `prompt` 가 `<` 로 시작하거나 `transcript.CONTINUED` 로 시작하면 기록 안 함. `eval_markers` 포함 → `skip` 만들고 끝. 프롬프트가 `/플러그인:스킬` 로 시작하면 먼저 `set_skill`. 레코드 `kind: "prompt"`, `flags.after_rejection`, 스킬 상태가 있으면 `prompts_since` 를 1 올리고 레코드에 복사.
- `PreToolUse` + `tool_name == "Skill"`, `UserPromptExpansion`: `set_skill` 후 `kind: "skill_start"` 레코드. 스킬 이름 필드는 Step 1 실측 입력에서 정한다.
- 지침 파일 버전: 스킬 상태가 없는 `prompt` 레코드는 `meta.guidelines = [{"path", "hash"}]` 에 적용되는 지침 파일(cwd 에서 위로 올라가며 있는 `CLAUDE.md`·`AGENTS.md`·`.claude/CLAUDE.md`, cwd 의 git 루트 `.claude/rules/*.md`, `~/.claude/CLAUDE.md`)을 `hash_of` 로 남긴다. 파일이 없으면 넣지 않는다.
- 레코드의 `skill.path` 가 비어 있으면 `skill_base_dir(transcript, 스킬 시작 pos)` 로 채우고 `skill-state.json` 에도 저장. `hash` 는 `hash_of(<path>/SKILL.md)`.

- [ ] **Step 1: 훅 입력 확보(`claude -p` 만)** — 격리 디렉터리(`HARNESS_PROJECTS_ROOT`·`CLAUDE_PLUGIN_DATA` 를 scratch 아래로)에서 입력을 `$CLAUDE_PLUGIN_DATA/payloads/<이벤트>-<n>.json` 에 그대로 저장하는 임시 훅을 `--plugin-dir` 로 걸고 `claude -p` 한 번으로 스킬 호출·실패하는 Bash·Edit 를 실행한다. 대표 입력을 `tests/fixtures/payloads/` 에 복사한다(경로·세션 ID 는 테스트용 값으로). 임시 훅은 커밋하지 않는다. 확인할 것: Skill 의 `tool_input` 스킬 이름 키, `PostToolUseFailure` 의 `error`. AskUserQuestion 답과 `UserPromptExpansion` 은 `claude -p` 로 재현되지 않으므로 이 단계에서 잡지 않는다: 슬래시 명령 스킬은 `UserPromptSubmit` 프롬프트의 `/플러그인:스킬` 로, AskUserQuestion 답은 `meta.answer_missing` 으로 두고 observe 가 transcript 에서 읽는다. `UserPromptExpansion` 처리는 입력에 `prompt` 또는 명령 이름이 있으면 쓰고 없으면 아무것도 하지 않는 방어적 구현으로 둔다(실제 입력 확인은 사용자가 따로 요청하는 검증에서)
- [ ] **Step 2: 실패하는 테스트 작성** — `tests/test_evidence.py`

```python
def test_prompt_record_fields(session):  # session 픽스처: tmp projects 아래 transcript 파일
    [r] = evidence.handle("UserPromptSubmit", payload("UserPromptSubmit", prompt="아니 그거 말고", transcript=session))
    assert r["kind"] == "prompt" and r["text"] == "아니 그거 말고" and r["pos"] == session.stat().st_size
    assert r["id"].endswith("-1") and r["skill"] is None

def test_injected_and_continued_prompts_not_recorded(session):
    assert evidence.handle("UserPromptSubmit", payload(..., prompt="<task-notification>…")) == []
    assert evidence.handle("UserPromptSubmit", payload(..., prompt="This session is being continued from a previous conversation …")) == []

def test_eval_marker_creates_skip_and_stops(session): ...  # 이후 Bash 기록도 []

def test_skill_state_from_skill_call_and_base_dir(session_with_skill_load):
    evidence.handle("PreToolUse", fixture("PreToolUse-Skill"))
    [r] = evidence.handle("UserPromptSubmit", payload(..., prompt="다시 해"))
    assert r["skill"]["name"] == "tech-writing:write" and r["skill"]["prompts_since"] == 1
    assert r["skill"]["path"].endswith("skills/write") and r["skill"]["hash"]

def test_prompt_outside_skill_records_guideline_hashes(session, tmp_path): ...  # cwd 에 CLAUDE.md 와 .claude/rules/a.md → meta.guidelines 두 개, hash 는 blob_hash

def test_after_rejection_flag(session_with_rejected_tool):
    [r] = evidence.handle("UserPromptSubmit", payload(..., prompt="그 파일은 건드리지 마"))
    assert r["flags"]["after_rejection"] is True

def test_missing_transcript_records_pos_zero(tmp_path): ...

def test_concurrent_appends_stay_valid_jsonl(session):
    # multiprocessing 으로 8개 프로세스가 각 50번 handle("PreToolUse", Bash) → 400줄 모두 json.loads 성공, id 는 중복 허용하지 않음
```

`id` 순번 경쟁을 막기 위해 순번 계산과 덧붙이기를 세션 `harness/` 의 `paths.acquire_lock` 안에서 한다(실패 시 최대 0.5초 재시도 후 순번 대신 `<세션8자>-t<나노초>` 를 쓴다).

- [ ] **Step 3: 실패 확인** — `uv run pytest plugins/harness-loop/tests/test_evidence.py -q` → FAIL
- [ ] **Step 4: 구현**
- [ ] **Step 5: 통과 확인** → PASS
- [ ] **Step 6: 커밋** — `feat: harness-loop 훅 근거 기록(프롬프트·스킬 상태)`

---

### Task 4: 근거 기록: Bash·실패·질문·스킬 수정

**Files:**
- Modify: `scripts/evidence.py`
- Test: `tests/test_evidence.py`

**Interfaces:**
- Produces:
  - `inline_body(command: str) -> str | None` — heredoc(`<<'X'`, `<<"X"`, `<<X`, `<<-X` 의 본문), `python3? -c`, `node -e`, `bash -c`, `ruby -e`, `perl -e` 인자 본문. 없으면 `None`
  - `skill_scripts(command: str, skill_path: str | None) -> list[str]` — 명령 안의 경로 중 `skill_path` 아래 실제 파일
  - `is_tracked_file(path: str) -> bool` — 스킬 파일(경로에 `/skills/` 가 있고 그 아래 파일이거나 이름이 `SKILL.md`) 또는 지침 파일(이름이 `apply_target.RULE_FILES` 중 하나이거나 `.claude/rules/` 아래 `.md`)

규칙:
- `PreToolUse` Bash: `kind: "bash"`, `text` = 명령. `inline_body` 가 있으면 `harness/scripts/<sha1(본문)>.txt` 로 저장(같으면 덮지 않음)하고 `file` 에 경로. `meta.scripts = [{"path", "hash": hash_of(path)}]`(`skill_scripts`).
- `PostToolUseFailure`: `is_interrupt` 가 참이면 기록 안 함. `kind: "failure"`, `text` = `tool_name` + 줄바꿈 + `error` 앞 20줄, `meta.input` = `tool_input` 을 JSON 으로 600자까지.
- `PostToolUse` AskUserQuestion: `kind: "ask"`, `text` = 질문·선택지·고른 답을 `Q: …\n- 선택지\n답: …` 형태로. 답 위치는 Task 3 Step 1 실측대로, 없으면 `meta.answer_missing = true`(observe 가 transcript 에서 읽음).
- `PostToolUse` Edit·Write·MultiEdit 이고 `is_tracked_file(file_path)`: `kind: "skill_edit"`, `file` = 경로, `meta.hash_after = hash_of(path)`. 아니면 기록 안 함.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
@pytest.mark.parametrize("cmd,body", [
    ("python3 - <<'EOF'\nprint(1)\nEOF", "print(1)\n"),
    ('python3 -c "import json; print(2)"', "import json; print(2)"),
    ("cat <<-X\n\tsome\nX", "\tsome\n"),
    ("ls -la", None),
])
def test_inline_body(cmd, body): assert evidence.inline_body(cmd) == body

def test_bash_saves_inline_script_once(session): ...  # 같은 본문 두 번 → 파일 하나, 두 레코드의 file 같음

def test_inline_body_crlf_and_long(session):
    cmd = "python3 - <<'EOF'\r\n" + "x=1\r\n" * 5000 + "EOF"
    [r] = evidence.handle("PreToolUse", bash(cmd))
    assert len(r["text"]) == 2000 and Path(r["file"]).read_text().count("x=1") == 5000

def test_bash_records_skill_script_hash(session_in_skill): ...  # skill_path 아래 scripts/run.py 를 부르면 meta.scripts[0].hash == blob_hash

def test_failure_record_and_interrupt_skipped(session): ...
def test_ask_record_text(session): ...  # Q:/답: 형식, 픽스처 기반
def test_skill_edit_for_skill_and_guideline_files_only(session): ...  # SKILL.md·CLAUDE.md·.claude/rules/x.md 기록, src/a.py 기록 안 함
```

- [ ] **Step 2: 실패 확인** → FAIL
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** → PASS
- [ ] **Step 5: 커밋** — `feat: harness-loop 근거 기록(Bash·실패·질문·스킬 수정)`

---

### Task 5: 훅 진입점과 등록(`hook.py`, `hooks.json`)

**Files:**
- Modify: `scripts/hook.py`(다시 씀), `hooks/hooks.json`(다시 씀)
- Test: `tests/test_hook.py`(다시 씀)

**Interfaces:**
- Consumes: `evidence.handle`, `paths.*`
- Produces:
  - `run(raw: str) -> str | None` — 입력 JSON 의 `hook_event_name` 으로 분기, SessionStart 면 출력할 JSON 문자열을 돌려줌
  - `pending_count() -> int` — 모든 `harness/` 의 `evidence.jsonl` 줄 수 − `evidence.cursor`(없으면 0) 합. `skip` 이 있는 세션은 뺀다
  - `session_start_text() -> str | None` — 오늘(`notice-day` 가 오늘 날짜면 `None`) 그리고 `pending_count() > 0` 또는 새 훅 오류가 있으면 Global Constraints 문구, 쓰고 나면 `notice-day` 를 오늘로
  - `main() -> None` — stdin 읽고 `run`, 예외는 `paths.log_error`, 항상 `sys.exit(0)`

`hooks.json`(모두 `"timeout": 5`, 명령 `uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/hook.py"`):

| 이벤트 | matcher |
|---|---|
| `SessionStart` | `startup\|resume` |
| `UserPromptSubmit` | 없음 |
| `UserPromptExpansion` | 없음 |
| `PreToolUse` | `Skill\|Bash` |
| `PostToolUse` | `AskUserQuestion\|Edit\|Write\|MultiEdit` |
| `PostToolUseFailure` | 없음 |

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_guard_agent_and_retro_env_record_nothing(session, monkeypatch): ...
def test_worker_session_records_but_no_notice(monkeypatch): ...
def test_session_start_notice_once_per_day(evidence_pending):
    out = json.loads(hook.run(json.dumps({"hook_event_name": "SessionStart", "source": "startup", ...})))
    assert out["hookSpecificOutput"]["additionalContext"] == "harness-loop: 판정하지 않은 근거 2건이 있습니다. /harness-loop:retro 로 확인하세요."
    assert hook.run(same) is None

def test_hook_bad_input_exits_zero_and_logs(tmp_path): ...  # subprocess 로 "{" 입력 → 종료 코드 0, stdout 빈 값, hook-errors.log 한 줄

def test_hooks_json_events_and_commands():
    spec = json.loads((PLUGIN / "hooks/hooks.json").read_text())["hooks"]
    assert set(spec) == {"SessionStart", "UserPromptSubmit", "UserPromptExpansion", "PreToolUse", "PostToolUse", "PostToolUseFailure"}
    for groups in spec.values():
        for g in groups:
            for h in g["hooks"]:
                assert h["command"].startswith('uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/hook.py"')

@pytest.mark.slow
def test_hook_p95_under_100ms(session):
    # subprocess 로 `uv run --script hook.py` 에 PreToolUse Bash 입력 40회(첫 1회는 uv 캐시 준비로 제외). uv 기동 시간 때문에 넘으면 실패 그대로 두고 측정값을 보고한다(기준 조정은 사용자 검증에서), 경과 시간 정렬 후 38번째 < 0.1
```

- [ ] **Step 2: 실패 확인** — `uv run pytest plugins/harness-loop/tests/test_hook.py -q` → FAIL
- [ ] **Step 3: 구현** — `hook.py` 는 `judge`·`report`·`state` 를 import 하지 않는다
- [ ] **Step 4: 통과 확인** — `uv run pytest plugins/harness-loop/tests/test_hook.py -q -m "slow or not slow"` → PASS
- [ ] **Step 5: 커밋** — `feat: harness-loop 훅을 근거 기록 이벤트로 교체`

---

### Task 6: 미판정 근거 묶음과 관찰 저장(`observe.py`)

**Files:**
- Create: `scripts/observe.py`, `defaults/observe-schema.json`, `tests/test_observe.py`

**Interfaces:**
- Consumes: `paths.*`, `transcript.REJECTED`
- Produces:
  - `excerpt(transcript, pos: int, kind: str) -> str` — `prompt`·`ask`: `pos` 앞의 마지막 assistant 텍스트 끝 1,500자 + 그 assistant 메시지의 도구 호출 이름 목록, 그리고 `ask` 의 답이 레코드에 없으면 해당 `tool_result`. `failure`: 빈 문자열(레코드에 이미 있음). 잘린 마지막 줄·깨진 줄은 건너뜀
  - `pending(limit: int) -> list[dict]` — 모든 세션에서 cursor 뒤 근거를 시각순으로 `limit` 개. 각 항목은 레코드 + `excerpt` + `project`(cwd 의 마지막 두 경로 요소). `skill_start`·`skill_edit` 은 판정 대상이 아니므로 넣지 않되 cursor 계산에서는 넘긴다
  - `save(result: dict) -> dict` — `observe-schema.json` 검증 후 `observations.jsonl` 에 덧붙이고, 판정에 포함된 근거까지 각 세션 cursor 를 옮김. 이미 `observations.jsonl` 에 있는 `evidence_id` 는 건너뜀. `{"saved": n, "skipped": m}`
  - `observations(since: str | None = None) -> list[dict]`

`observe-schema.json`: `{"observations": [{"evidence_id", "type": enum(correction, repeat, failure, approval_question), "verdict": bool, "reason": str(minLength 1), "purpose": str(repeat 일 때 필수, 같은 목적 묶기용 한 줄)}]}`. `save` 는 근거 레코드에서 `session`, `at`, `cwd`, `project`, `skill`, `file`, `text`, `meta` 를 복사해 관찰에 붙인다. 근거 하나는 관찰 여러 개(예: `prompt` → `correction` 과 `approval_question`)를 낼 수 있다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_excerpt_prompt_has_previous_assistant_tail(redesign_fixture): ...
def test_excerpt_skips_partial_line(tmp_path): ...
def test_pending_orders_across_sessions_and_skips_meta_kinds(two_sessions): ...
def test_save_validates_schema(two_sessions):
    with pytest.raises(ValueError, match="verdict"): observe.save({"observations": [{"evidence_id": "x", "type": "correction", "reason": "r"}]})
def test_observe_is_idempotent_per_evidence(two_sessions):
    result = {"observations": [{"evidence_id": eid, "type": "correction", "verdict": True, "reason": "되돌림"}]}
    assert observe.save(result) == {"saved": 1, "skipped": 0}
    assert observe.save(result) == {"saved": 0, "skipped": 1}
def test_cursor_advances_only_through_judged_evidence(two_sessions): ...
```

- [ ] **Step 2: 실패 확인** → FAIL
- [ ] **Step 3: 구현** — 스키마 검증은 Task 10 의 `retro.validate` 가 아직 없으므로 `observe.py` 에 `validate(obj, schema)` 를 두고 `retro.py` 는 그것을 쓴다
- [ ] **Step 4: 통과 확인** → PASS
- [ ] **Step 5: 커밋** — `feat: harness-loop 미판정 근거 묶음과 관찰 저장`

---

### Task 7: 묶음과 관문(`groups.py`)

**Files:**
- Create: `scripts/groups.py`, `defaults/groups-schema.json`, `tests/test_groups.py`

**Interfaces:**
- Consumes: `observe.observations`, `observe.validate`, `versions.history`, `versions.git_root`, `versions.in_plugin_cache`, `decide.last_decisions`(Task 8; Task 7 에서는 같은 이름의 빈 dict 를 돌려주는 자리 함수로 시작하고 Task 8 에서 바꾼다)
- Produces:
  - 묶음 레코드(`groups.json` 의 값): `{"id", "target_type", "rework_kind"?, "target_path", "skill_root"?, "problem", "observations": [evidence_id…], "verified": {"checked_path", "hash", "finding": "missing"|"wrong"|"ignored"|"has_script"|"asks_here"|"no_instruction", "line"?} | null, "specific_facts": [str], "failure_ref": evidence_id, "proposal": {"scope", "opinion", "diff"}, "fix_version"?, "edits"?: [hash…], "created", "updated"}`
  - `merge(result: dict) -> list[str]` — `groups-schema.json` 검증 후 `id` 가 있으면 관찰 목록을 합치고 나머지 필드를 새 값으로, 없으면 추가. 관찰 ID 가 `observations.jsonl` 에 없거나 verdict 가 거짓이면 `ValueError`. 바뀐 id 목록
  - `gate(now: str | None = None) -> dict` — `{"report": [묶음 + "count", "sessions", "projects", "recurrence"?], "held": [id…], "dropped": [{"id", "reason"}], "notices": [str]}`
  - `load() -> dict[str, dict]`, `slug(text) -> str`

관문(`cfg = paths.load_config()`, 관찰은 `window_days` 안, 그 묶음에 `proceed` 결정이 있으면 그 뒤 것만):

| 조건 | 결과 |
|---|---|
| `verified` 없음, `specific_facts` 비었음, `failure_ref` 가 묶음의 참 관찰이 아님 | `dropped`(이유 `unverified`·`not_specific`·`no_failure_ref`) |
| 마지막 결정이 `reject` | `dropped`(`rejected`) |
| 마지막 결정이 `ticket` | `held`(횟수만 갱신) |
| `script` | 관찰 ≥ `script_min_count` 이고 세션 수 ≥ `script_min_sessions` |
| `skill_supplement` 이고 관찰이 `correction` | 관찰 ≥ `correction_min_count` |
| `skill_supplement`·`fix` 이고 관찰에 `approval_question` 있음 | 1 이상 |
| `guideline_supplement` | 관찰 ≥ `correction_min_count` 이고 세션 수 ≥ `guideline_min_sessions`. `target_path` 가 `~/.claude/CLAUDE.md` 면 프로젝트 수 ≥ 2, 아니면 `dropped`(`scope`) |
| `fix` | 1 이상 |
| `rework` + `recurrence`·`verified_recurrence` | `fix_version`(또는 마지막 `proceed` 결정의 `baseline_hash`) 이 `versions.history(target_path)` 에 있고, 그 버전 이후 순서의 해시를 가진 관찰이 1 이상(`recurrence` 에 그 관찰 ID). 이전 버전 해시의 관찰은 재발이 아님 |
| `rework` + `repeat_edit` | `edits` 가 `history` 에 모두 있고 개수 ≥ `repeat_edit_min` |
| `rework` 이고 `git_root(target_path)` 없음 | `dropped`(`untracked`), `notices` 에 `versions.untracked_notice` |
| `verified.finding` 과 `target_type` 이 설계 7장 "관찰 → 결정 대상" 표와 맞지 않음(예: `missing` 인데 `fix`, `has_script` 인데 `script`) | `dropped`(`inconsistent`) |
| 그 외 미달 | `held` |

관찰의 버전 해시는 `target_path` 가 스킬 파일이면 `skill.hash`(또는 `meta.scripts` 중 같은 경로), 지침 파일이면 `meta.guidelines` 중 같은 경로의 `hash` 를 쓴다. 관찰에 그 해시가 없으면 재발 판정에서 세지 않는다.

`notices` 에는 `report`·`held` 묶음 중 대상이 git 밖인 것의 안내도 스킬 루트별로 한 번 넣는다.

- [ ] **Step 1: 실패하는 테스트 작성** — 관찰·묶음은 `observe.save`·`groups.merge` 로 만든다

```python
def test_script_needs_three_in_two_sessions(obs_factory): ...  # 3회 1세션 → held, 3회 2세션 → report
def test_correction_supplement_needs_two(obs_factory): ...
def test_approval_question_reports_on_one(obs_factory): ...
def test_unverified_group_is_dropped(obs_factory): ...
def test_generic_proposal_without_specific_facts_dropped(obs_factory): ...
def test_guideline_global_needs_two_projects(obs_factory): ...
def test_window_excludes_old_observations(obs_factory): ...  # 31일 전 관찰은 세지 않음
def test_rework_recurrence_only_after_fix_version(repo, obs_factory):
    # v1 관찰, v2 커밋(fix_version), v2 관찰 → report 에 recurrence == [v2 관찰]; v1 관찰만 있으면 held
def test_rework_untracked_dropped_with_notice(tmp_path, obs_factory): ...
def test_merge_rejects_false_or_unknown_observations(obs_factory): ...
def test_inconsistent_finding_and_target_dropped(obs_factory): ...
```

- [ ] **Step 2: 실패 확인** → FAIL
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** → PASS
- [ ] **Step 5: 커밋** — `feat: harness-loop 묶음 병합과 결정 대상 관문`

---

### Task 8: 결정·라벨·보관·티켓 근거(`decide.py`)

**Files:**
- Create: `scripts/decide.py`, `tests/test_decide.py`
- Modify: `scripts/groups.py`(자리 함수를 `decide.last_decisions` 로 교체)

**Interfaces:**
- Consumes: `groups.load`, `observe.observations`, `versions.blob_hash`, `versions.history`, `apply_target.classify`, `paths.*`
- Produces:
  - `record(group_id, choice, *, kind=None, reason=None, baseline_hash=None, ticket=None) -> dict` — `choice` ∈ `proceed`·`ticket`·`reject`. `reject` 는 `kind` 필수. `proceed` 는 `baseline_hash` 가 없으면 `blob_hash(target_path)`. 대상이 플러그인 캐시면 `proceed` 를 `ValueError`. `decisions.jsonl` 에 `{"group", "decision", "kind", "note", "baseline_hash", "ticket", "at", "rubric_version"}` 덧붙이고, 같은 호출에서 `archive(group_id)`
  - `last_decisions() -> dict[str, dict]` — 묶음별 마지막 결정(`group` 키 없는 옛 줄 무시)
  - `labels() -> dict[str, dict]` — `{"label": positive|negative|excluded, "kind", "note", "at"}`
  - `split(group_id) -> str`
  - `precision() -> dict` — `{"positive", "negative", "precision": float | None}`
  - `archive(group_id) -> Path` — `replay/<id>/input.json`: 묶음(결정 필드 없이), 관찰마다 근거 레코드·발췌, 대상 파일의 관찰 당시 해시별 내용(git 이면 `git cat-file -p <해시>`, 아니면 사본), 당시 `rubric.md` 본문과 버전. 이미 있으면 덮어씀
  - `ticket_file(group_id, out_dir) -> Path` — 마크다운: 제목 `[결정 대상] 스킬 이름 — 문제`, 반복 스크립트 원문(관찰의 `file` 내용), 교정 프롬프트와 발췌, 대상 파일 해당 구간(`verified.line` 앞뒤 15줄), 수정안 diff, 판정 이유. 경로 대신 내용

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_reject_requires_kind(group): ...
def test_proceed_records_baseline_hash_and_archives(group_in_repo): ...
def test_plugin_cache_target_cannot_proceed(group_in_cache): ...
def test_labels_and_precision(groups3): ...  # proceed·ticket·reject(trivial)·reject(wont-fix) → positive 2, negative 1, precision 2/3
def test_old_sig_lines_ignored(): ...
def test_archive_contains_no_decision_and_file_versions(group_in_repo): ...
def test_ticket_file_embeds_contents_not_paths(group_with_script): ...  # 스크립트 원문 문자열이 파일 본문에 있음
def test_gate_reflects_decisions(group): ...  # reject 뒤 gate 의 dropped 에 rejected
```

- [ ] **Step 2: 실패 확인** → FAIL
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `uv run pytest plugins/harness-loop/tests/test_decide.py plugins/harness-loop/tests/test_groups.py -q` → PASS
- [ ] **Step 5: 커밋** — `feat: harness-loop 결정 기록·라벨·replay 보관·티켓 근거`

---

### Task 9: rubric 과 replay 채점(`rubric.py`)

**Files:**
- Create: `scripts/rubric.py`, `defaults/replay-schema.json`, `tests/test_rubric.py`
- Modify: `defaults/rubric.md`(다시 씀)

**Interfaces:**
- Consumes: `decide.labels`, `decide.split`, `groups.load`, `paths.*`
- Produces:
  - `version(text) -> int`(줄 없으면 1)
  - `candidates() -> dict[str, list[dict]]` — kind(`not-defect`·`trivial`)별 train 분할 negative 라벨 중 `rubric-changes.jsonl` 근거에 아직 쓰이지 않은 것 `{"group", "problem", "target_type", "note", "first_reason"}`와, kind 별 개수가 `rubric_min_evidence` 이상인지 `ready`
  - `apply(kind, rule, evidence: list[str], *, dropped_positives: list[str], confirmed: bool, out: Path | None = None) -> int` — 근거가 test 분할이거나 이미 쓰인 묶음이면 `ValueError`(그 id 명시), 근거 수 < `rubric_min_evidence` 면 `ValueError`, `dropped_positives` 가 있는데 `confirmed` 가 거짓이면 `ValueError`. `not-defect` → `## 관찰로 보지 않는 경우`, `trivial` → `## 결정 대상으로 올리지 않는 경우` 절에 `- rule` 추가, 버전 +1, `rubric-changes.jsonl` 에 `{"version", "at", "kind", "rule", "evidence", "dropped_positives", "confirmed"}`. `out` 이면 그 파일에만 씀. 새 버전 반환
  - `replay_score(current: dict, candidate: dict) -> dict` — 두 입력은 `replay-schema.json` 형식(`{"rubric_version", "results": [{"group", "surfaces": bool}]}`, 서브에이전트가 보관본마다 관찰 판정·검증을 다시 하고 `groups.gate` 와 같은 관문을 적용한 결과). 반환 `{"dropped_positives": [id…](모든 positive 중 current 에서 surfaces 이고 candidate 에서 아닌 것), "eligible": bool(test 분할 positive+negative ≥ replay_min_labels), "current": {"tpr", "tnr", "n_pos", "n_neg"}, "candidate": {...}}`
  - `gate_one(archived_group: dict, rejudged: dict) -> bool` — 보관본 묶음과 다시 판정한 관찰·검증으로 Task 7 관문을 적용(`groups.gate` 의 판정 함수를 묶음 하나에 쓰는 형태로 분리해 재사용)

`defaults/rubric.md` 내용(규칙만): 관찰 네 가지 판정 기준(설계 4장 표의 원칙과 해당 조건), 공통 확인(구체성·실패 근거), 결정 대상 연결 표(설계 7장 "관찰 → 결정 대상"), 승인용 질문 제외(Global Constraints), 모든 관찰 제외(환경 원인, 바꿀 파일 없음, 정리 제안), 빈 절 `## 관찰로 보지 않는 경우`, `## 결정 대상으로 올리지 않는 경우`. 첫 줄 `<!-- rubric-version: 1 -->`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_candidates_train_only_and_ready_flag(labels_mix): ...
def test_apply_rejects_test_split_and_reused_evidence(labels_mix): ...
def test_apply_requires_confirmation_when_positives_drop(labels_mix):
    with pytest.raises(ValueError, match="확인"): rubric.apply("trivial", "규칙", ev, dropped_positives=["g1"], confirmed=False)
    assert rubric.apply("trivial", "규칙", ev, dropped_positives=["g1"], confirmed=True) == 2
    assert json.loads(last_line(data / "rubric-changes.jsonl"))["confirmed"] is True
def test_replay_score_regression_and_eligibility(labels_mix): ...  # 30 미만이면 eligible False 이지만 dropped_positives 는 계산
def test_default_rubric_has_sections_and_exclusions():
    text = (PLUGIN / "defaults/rubric.md").read_text()
    for s in ("## 관찰로 보지 않는 경우", "## 결정 대상으로 올리지 않는 경우", "push", "티켓 생성"): assert s in text
```

- [ ] **Step 2: 실패 확인** → FAIL
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** → PASS
- [ ] **Step 5: 커밋** — `feat: harness-loop rubric 후보·적용과 replay 채점`

---

### Task 10: 스킬용 CLI(`retro.py`)와 백필(`backfill.py`)

**Files:**
- Create: `scripts/retro.py`, `scripts/backfill.py`, `tests/test_retro_cli.py`, `tests/test_backfill.py`, `tests/fixtures/redesign/*.jsonl`

**Interfaces:**
- Consumes: Task 1~9 의 공개 함수
- Produces — `uv run --script scripts/retro.py <하위 명령>`(출력은 모두 JSON 한 덩어리, 오류는 stderr 와 종료 코드 1, 락 경합은 3):

| 하위 명령 | 동작 |
|---|---|
| `pending [--limit N]` | `{"evidence": observe.pending(N 기본 judge_batch), "examples": train 분할 negative(not-defect) 라벨 묶음의 문제·관찰 판정 이유 최대 `label_examples` 개}` 를 `--out <파일>` 에 쓰고 `{"count", "file", "remaining"}` |
| `observe --file F` | `observe.save` |
| `context` | 묶기 입력: 참 관찰 전체(창 안), 기존 묶음, 결정, 현재 rubric 경로, 라벨 예시(train, positive·negative 번갈아 `label_examples` 개) |
| `groups --file F` | `groups.merge` |
| `gate` | `groups.gate` + 각 보고 묶음에 `apply_target.classify(target_path)` |
| `decide <id> proceed\|ticket\|reject [--kind K] [--reason R] [--ticket KEY]` | `decide.record` |
| `ticket-file <id> --out-dir D` | `decide.ticket_file` |
| `metrics` | `decide.precision` |
| `rubric-candidates` / `rubric-apply …` / `replay-score --current A --candidate B` | Task 9 |
| `replay-inputs` | 모든 positive·negative 라벨 묶음의 `replay/<id>/input.json` 경로 목록 |
| `backfill [--days N]` | `backfill.run` |

  - `backfill.run(days: int | None) -> dict` — 설치 시각(`installed_at`) 전 또는 `--days` 안의 transcript 마다 이미 `evidence.jsonl` 이 있으면 건너뛰고, 줄을 읽어 훅 입력을 재구성해 `evidence.handle(event, data, pos=<그 줄 끝 바이트>, at=<줄 timestamp>, hash_of=lambda p: versions.hash_at(p, at))` 호출. 재구성: 사람 발화(`transcript.parse` 와 같은 사람 발화 판정, `<…>` 주입·이어하기 요약 제외) → `UserPromptSubmit`; 슬래시 명령 → `UserPromptExpansion`; assistant `tool_use` Skill → `PreToolUse`; Bash → `PreToolUse`; `is_error` `tool_result`(거절 제외) → `PostToolUseFailure`(`error` = 내용); AskUserQuestion `tool_result` → `PostToolUse`; Edit·Write·MultiEdit → `PostToolUse`. `{"sessions", "records"}`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_cli_pipeline_end_to_end(redesign_sessions, tmp_path):
    # pending → (테스트가 쓴 observe JSON) → observe → context → (테스트가 쓴 groups JSON) → groups → gate → decide proceed → metrics
    assert gate["report"][0]["id"] == "release-adr-number"
    assert metrics["precision"] == 1.0

def test_cli_validation_error_exit_1(tmp_path): ...
def test_context_label_examples_alternate(labels_mix): ...
def test_pending_includes_not_defect_examples(labels_mix): ...
def test_backfill_matches_live_records(redesign_sessions):
    # 같은 기록 조각을 handle 로 직접 넣은 결과와 backfill 결과의 (kind, text, flags) 목록이 같음
def test_backfill_skips_injected_and_continued(redesign_sessions): ...
def test_backfill_uses_git_hash_at_time(repo, redesign_sessions): ...
```

- [ ] **Step 2: 실패 확인** → FAIL
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `uv run pytest plugins/harness-loop/tests -q` → PASS
- [ ] **Step 5: 커밋** — `feat: harness-loop retro CLI 와 백필`

---

### Task 11: 옛 파이프라인 제거

**Files:**
- Delete: `scripts/state.py`, `scripts/collect.py`, `scripts/judge.py`, `scripts/report.py`, `defaults/scoring.yaml`, `defaults/judge-schema.json`, `tests/test_state.py`, `tests/test_collect.py`, `tests/test_judge.py`, `tests/test_judge_background.py`, `tests/test_report.py`, `tests/test_e2e.py`, 옛 전용 픽스처(`tests/fixtures/signals.jsonl` 등 다른 테스트가 쓰지 않는 것)
- Modify: `tests/test_transcript.py`(`state` import 를 `paths` 로)

- [ ] **Step 1: 남은 참조 확인** — `git grep -n -e "import state" -e "import judge" -e "import report" -e "import collect" -e 'load_script("state")' -- plugins/harness-loop` → 출력이 지울 파일 안에만 있음
- [ ] **Step 2: 삭제와 수정**
- [ ] **Step 3: 확인** — `uv run pytest plugins/harness-loop/tests -q` → 모두 PASS
- [ ] **Step 4: 커밋** — `refactor: harness-loop 옛 신호 수집·채점·백그라운드 judge 제거`

---

### Task 12: retro 스킬 다시 쓰기(`SKILL.md`)

**Files:**
- Modify: `skills/retro/SKILL.md`(다시 씀), `tests/test_skill.py`(다시 씀), `.claude-plugin/plugin.json`(`description` 을 "세션 근거를 모아 사용자가 결정할 스킬·지침·스크립트 개선만 보고한다." 로, `version` `0.2.0`), `../../.claude-plugin/marketplace.json`(같은 `version`)

SKILL.md 가 정할 것(규칙·표·명령만):
- frontmatter: `name: retro`, description 에 트리거(`회고`, `retro`, `개선점 정리`, `이 세션 돌아보기`, `판정하지 않은 근거`), `argument-hint: "[backfill [--days N]]"`.
- 공통: 명령은 `uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/retro.py" …` 앞에 `CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}"`. 한국어. 임시 파일은 scratchpad.
- 기본 모드 절차:
  1. `pending` 을 남은 것이 없을 때까지 반복하며, 묶음마다 Agent 도구(`model: "opus"`)로 관찰 판정 서브에이전트를 부른다. 프롬프트: rubric 파일 경로를 읽으라는 지시, 근거 파일 경로, `observe-schema.json` 형식으로 지정 파일에 쓰라는 지시. 결과로 `observe --file`.
  2. `context` 를 받아 Agent 도구(`model: "opus"`)로 묶기·검증 서브에이전트를 부른다. 지시: 참 관찰만 묶고 기존 묶음 id 를 재사용, 결정 대상마다 대상 파일(관찰 당시 해시는 `skill-snapshots` 나 `git cat-file -p`, 현재는 경로)을 실제로 Read 해서 `verified` 를 채우고, 버전 이력이 필요하면 `git log`·`git diff` 를 읽고, `specific_facts`·`failure_ref`·`proposal.diff` 를 채워 `groups-schema.json` 형식으로 쓴다. 결과로 `groups --file`.
  3. `gate` 결과로 보고. 형식: `번호. [결정 대상] 스킬 이름(경로) — 문제 한 줄` 다음 줄들에 근거(경로·발췌), 수정 범위와 의견, diff. `held`·`dropped` 는 개수 한 줄. `notices` 는 끝에 그대로. rubric 후보가 `ready` 면 마지막에 한 줄.
  4. 보고 뒤 턴을 끝내고 입력을 기다린다.
- 결정 입력(`1 진행, 2 티켓, 3 거절 사소함` 등) 처리 표:

| 입력 | 동작 |
|---|---|
| 진행 | 대상 파일에 diff 반영(대상이 다른 저장소·worktree 여도 그 작업 트리를 고치고 경로를 알림) → 근거 문제 재확인(스크립트 실행 등) → `decide <id> proceed`. 커밋하지 않음(사용자가 요청하면 그 저장소 규칙으로). 플러그인 캐시 대상은 진행 불가 안내 |
| 티켓 | `ticket-file` → 사용자에게 미리보기 → 확인 뒤 `ticket-creator` 스킬에 그 파일 경로를 사양으로 위임 → 받은 키로 `decide <id> ticket --ticket KEY` |
| 거절 | 사유(결함 아님 / 사소함 / 안 고침)를 `--kind not-defect\|trivial\|wont-fix` 로, 이유는 `--reason` |

- rubric 갱신(사용자가 후보 안내에 답할 때): `rubric-candidates` → 패턴별 규칙 초안 → `rubric-apply --out <tmp>` 로 후보 rubric → `replay-inputs` 의 보관본을 지금 rubric·후보 rubric 으로 각각 Agent(`model: "opus"`) 에 다시 판정시켜 `replay-schema.json` 결과 두 개 → `replay-score` → `dropped_positives` 가 있으면 그 묶음을 보이고 사용자의 명시 확인을 받은 뒤 `rubric-apply … --confirmed` (확인 없으면 적용 안 함). `eligible` 이면 TPR·TNR 을 참고 수치로 보임.
- `backfill [--days N]`: `backfill` 실행 후 결과 수를 알리고 기본 모드로 이어감.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_skill.py`: frontmatter 이름·트리거, 500줄 이하, 본문의 모든 `retro.py <하위 명령>` 이 Task 10 표에 있음, 명령이 모두 `uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/retro.py"` 형식, `model: "opus"` 언급, `--confirmed` 언급, 플러그인·marketplace 버전 일치(기존 `test_plugin_manifest.py` 유지)
- [ ] **Step 2: 실패 확인** → FAIL
- [ ] **Step 3: SKILL.md 작성** — `retro.py` 에 `rubric-apply --confirmed` 플래그가 Task 9·10 과 맞는지 확인
- [ ] **Step 4: 문서 검토** — korean-proofreader 교정, skill-reviewer 검토를 거쳐 지적 반영
- [ ] **Step 5: 통과 확인** — `uv run pytest plugins/harness-loop/tests -q` → PASS
- [ ] **Step 6: 커밋** — `feat: harness-loop retro 스킬을 결정 대상 보고 절차로 다시 씀`

---

### Task 13: 실측과 성공 기준 확인

> 사용자가 따로 요청할 때만 진행한다. 실행 방식(작업별 서브에이전트)으로 Task 1~12 를 끝내면 여기서 멈추고 보고한다. 단, Step 4 의 병합은 사용자 요청 전에는 하지 않는다.

**Files:**
- Create: `tests/fixtures/redesign/`(성공 기준 2 의 기록 조각이 Task 10 에서 빠진 것만), `docs/superpowers/specs/2026-11-06-harness-loop-retro-redesign-design.md` 끝에 `## 14. 실측 결과` 절

- [ ] **Step 1: 고정 기록 조각 확인(성공 기준 2)** — 대안 없는 진행 확인 → `approval_question` 참 + `report`, push 확인 → rubric 제외로 거짓, 도구 거절 후 교정 → `after_rejection`, 같은 heredoc 3회 2세션 → `script` 보고, git 안 스킬 수정 후 재발 → `rework/recurrence`, 같은 곳 3회 수정 → `rework/repeat_edit`. 판정은 테스트가 쓴 서브에이전트 출력 JSON 으로 고정한다. `uv run pytest plugins/harness-loop/tests -q` → PASS
- [ ] **Step 2: 훅 시간(성공 기준 3)** — `uv run pytest plugins/harness-loop/tests/test_hook.py -m slow -q` → PASS, 측정한 p95 값을 실측 절에 적음
- [ ] **Step 3: 2026-10-19 회고 세션 재현(성공 기준 1)** — 격리 디렉터리(`HARNESS_PROJECTS_ROOT=<scratch>/projects`, `CLAUDE_PLUGIN_DATA=<scratch>/data`)에 그 13세션 기록만 복사하고 `retro.py backfill` → `--plugin-dir` 로 이 플러그인을 띄운 대화형 세션에서 같은 환경 변수로 `/harness-loop:retro`. 확인: "메모리에만 있다 → 전역으로" 류와 응답 품질 지적이 `report` 에 없음, ADR 번호·테스트 재요구·run_eval 충돌·inline-helper 가 `report` 또는 `held` 에 있음. 세션 목록과 옛 항목은 실제 `${CLAUDE_PLUGIN_DATA}` 의 2026-10-19 18:01~18:03 run·`decisions.jsonl` 에서 읽기만 한다. 결과 표(항목, 기대, 실제)를 실측 절에 적음
- [ ] **Step 4: 병합과 실설치 확인** — `feat/harness-loop-redesign` 을 `feat/harness-loop` 로 병합(이 worktree 에서 `git merge --ff-only` 또는 일반 병합)한 뒤, 사용자 설치 환경에서 `/reload-plugins` 뒤 새 세션 하나: 프롬프트·Bash·스킬 호출 뒤 세션 `harness/evidence.jsonl` 에 레코드가 생기고, 다음 세션 SessionStart 에 알림 문구가 한 번 나오는지 확인. 옛 `scoring.yaml` 이 남아 있어도 동작에 영향이 없고, 옛 `rubric.md` 가 `rubric-old.md` 로 바뀌고 새 기본값이 복사됐는지 확인
- [ ] **Step 5: 정밀도 기준선 기록** — `retro.py metrics` 출력(처음에는 라벨 0)을 실측 절에 적음. 재현율 표본 확인 절차(한 달에 한 번, 무작위 `held`·`dropped`·참 관찰 아닌 근거 20건을 사람이 보고 놓친 결정 대상을 센다)를 실측 절에 적음
- [ ] **Step 6: 커밋** — `docs: harness-loop retro 재설계 실측 결과`
