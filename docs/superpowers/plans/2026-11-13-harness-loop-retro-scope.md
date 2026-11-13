# harness-loop retro 범위 조정 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** retro 를 "발견 → 수정안 안내 → 수락/거절 기록" 범위로 줄이고(버전 추적·rework·진행·티켓 제거), 결정 패턴 관찰을 더하고, 패턴 관문을 2회·2세션으로 통일하고, 분류 기준을 rubric 과 연동한다.

**Architecture:** 기존 `plugins/harness-loop` 구조(훅 → evidence → observe → groups → gate → decide, rubric·replay)를 그대로 두고 범위 밖 기능을 걷어낸다. 판정 기준은 `defaults/rubric.md` 하나에 모으고, 관문 스크립트의 코드(결정 대상, 검증 결과 코드, 버림 이유, 설정 이름)가 rubric 에 모두 있는지 테스트로 묶는다.

**Tech Stack:** Python 3.11, `uv run --script`, pytest, pyyaml.

**Spec:** `docs/superpowers/specs/2026-11-06-harness-loop-retro-redesign-design.md` (범위 조정 2026-11-13)

## Global Constraints

- 작업 위치: worktree `/Users/yeonjin.roh/src/skills/.claude/worktrees/feat+harness-loop-redesign`, 브랜치 `feat/harness-loop-redesign`. push·PR 금지.
- 테스트: 저장소 루트에서 `uv run pytest plugins/harness-loop/tests -q`. 테스트는 실제 `~/.claude/projects/*/*/harness`·`~/.claude/plugins/data` 를 건드리지 않는다(`tests/conftest.py` 의 격리 픽스처를 쓴다).
- 호환 없음(사용자 결정): 옛 필드·옛 결정 값·옛 파일을 읽는 분기, 이전 형식 변환을 두지 않는다. 지운 기능의 코드·테스트·설정 키는 남기지 않는다.
- 사용자에게 보이는 문구(오류, 보고, rubric, SKILL.md)는 한국어. 스킬 문서는 규칙·표·명령만 두고 설정 값은 이름으로만 가리킨다.
- 커밋 메시지는 한국어 `feat: …`/`fix: …`/`refactor: …`, 마지막 줄 `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- 서브에이전트는 다른 서브에이전트를 부르지 않는다.

## Review Focus

1. 결정 패턴 묶음의 `conflicts` 가 참 관찰이 아닌 id 를 담음 → `groups.merge` 가 ValueError 로 거부해야 한다(Task 3 테스트).
2. 결정이 난 묶음에 새 관찰이 쌓임 → 다시 보고되지 않아야 한다(Task 1 테스트).
3. 대상 파일이 아직 없는 묶음(스크립트화의 새 파일)에 결정 → 보관본 `target_file.text` 가 null 이고 결정은 기록돼야 한다(Task 1 테스트).
4. `decision_pattern` 이 참인데 `rule` 이 없음 → `observe` 가 ValueError 로 거부해야 한다(Task 3 테스트).
5. 일반 작업 결정 패턴이 2개 프로젝트에서 나왔는데 대상이 프로젝트 지침 → `scope` 로 버려야 한다(Task 3 테스트).

---

### Task 1: 결정을 수락·거절로 바꾸고 수락률을 보인다

**Files:**
- Modify: `plugins/harness-loop/scripts/decide.py`, `plugins/harness-loop/scripts/groups.py`(`judge_group`·`gate` 의 결정 처리만), `plugins/harness-loop/scripts/retro.py`
- Test: `plugins/harness-loop/tests/test_decide.py`, `tests/test_groups.py`, `tests/test_retro_cli.py`, `tests/test_rubric.py`

**Interfaces:**
- Produces: `decide.CHOICES = ("accept", "reject")`, `decide.KINDS` 그대로. `decide.record(group_id: str, choice: str, *, kind=None, reason=None) -> dict` — 기록 필드 `group, decision, kind, note, target_type, at, rubric_version`. `decide.metrics() -> dict`. `decide.archive(group_id) -> Path` 의 input.json 키는 `group, observations, target_file, rubric`, `target_file = {"path": <확장한 target_path>, "text": <결정 시점 내용 또는 None>}`. `groups.gate()` 반환 키는 `report, held, dropped`(+ Task 2 전까지 남는 `notices`).
- 라벨: `accept` → `positive`, `reject` + `not-defect`·`trivial` → `negative`, `reject` + `wont-fix` → `excluded`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_accept_records_target_type_and_archives(g):
    rec = decide.record(g, "accept")
    assert rec["decision"] == "accept" and rec["target_type"] == groups.load()[g]["target_type"]
    data = json.loads((paths.data_dir() / "replay" / g / "input.json").read_text())
    assert set(data) == {"group", "observations", "target_file", "rubric"}

def test_archive_missing_target_file_has_null_text(obs_factory):
    # target_path 가 없는 파일인 묶음
    decide.record(gid, "accept")
    assert json.loads(...)["target_file"]["text"] is None

def test_choices_are_accept_and_reject():
    assert decide.CHOICES == ("accept", "reject")
    with pytest.raises(ValueError):
        decide.record(gid, "proceed")

def test_metrics_overall_by_target_and_rubric_version(obs_factory):
    # accept 2(script, fix), reject not-defect 1(script), reject wont-fix 1
    m = decide.metrics()
    assert m["overall"] == {"accepted": 2, "rejected": 1, "rate": 2 / 3}
    assert m["by_target"]["script"] == {"accepted": 1, "rejected": 1, "rate": 0.5}
    assert m["by_rubric_version"]["1"]["accepted"] == 2

def test_decided_group_not_reported_even_with_new_observations(g, obs_factory):
    decide.record(g, "accept")
    # 같은 묶음에 window 안 새 참 관찰 추가
    out = groups.gate()
    assert g not in [r["id"] for r in out["report"]]
    assert {"id": g, "reason": "decided"} in out["dropped"]
```

retro CLI 테스트: `decide <id> accept` 성공, `decide <id> proceed` 는 종료 코드 1, `ticket-file` 하위 명령은 argparse 오류(종료 코드 1), `metrics` 출력 키 `overall·by_target·by_rubric_version`.

- [ ] **Step 2: 실패 확인** — `uv run pytest plugins/harness-loop/tests -q` → 위 테스트 FAIL.

- [ ] **Step 3: 구현**
  - `decide.py`: `proceed`·`ticket`·`baseline_hash`·`ticket_file`·`_around`·`_read`·`_version_text`·`CONTEXT_LINES`·`apply_target`·`versions`·`subprocess` 사용을 지운다. `record` 는 `archive` 를 먼저 쓰고 결정을 덧붙인다(기존 순서 유지). `archive` 는 관찰 해시 대신 `target_file` 에 결정 시점 파일 내용을 담는다. `precision()` 을 `metrics()` 로 바꾼다: 수락률 = positive ÷ (positive + negative), 분모 0 이면 `rate: None`. `by_rubric_version` 키는 문자열.
  - `groups.py`: `judge_group` 에서 결정이 있으면(어떤 결정이든) `("dropped", "decided", {})`. 진행 뒤 재발을 rework 로 올리던 분기, `waiting` 목록을 지운다.
  - `retro.py`: `decide` 의 `--ticket` 인자, `ticket-file` 하위 명령을 지우고 `metrics` 를 `decide.metrics()` 로 연결한다.
  - `rubric.py`: 라벨 이름이 그대로라 로직 변경 없음. 테스트 픽스처의 결정 값만 `accept` 로 바꾼다.

- [ ] **Step 4: 통과 확인** — `uv run pytest plugins/harness-loop/tests -q` → 전부 PASS.

- [ ] **Step 5: 커밋** — `refactor: harness-loop retro 결정을 수락·거절로 줄이고 수락률 추가`

---

### Task 2: 버전 추적과 rework, 스킬 수정 기록을 걷어낸다

**Files:**
- Delete: `plugins/harness-loop/scripts/versions.py`, `plugins/harness-loop/tests/test_versions.py`
- Modify: `scripts/paths.py`(`repo_roots` 이전), `scripts/evidence.py`, `scripts/backfill.py`, `scripts/groups.py`, `hooks/hooks.json`, `defaults/groups-schema.json`, `defaults/config.yaml`(`repeat_edit_min` 삭제)
- Test: `tests/test_evidence.py`, `tests/test_backfill.py`, `tests/test_groups.py`, `tests/test_hook.py`, `tests/test_paths.py`

**Interfaces:**
- Consumes: Task 1 의 `groups.gate()` 결정 처리.
- Produces: `paths.repo_roots(cwd, timeout: float = 1) -> tuple[str | None, str]`(동작은 `versions.repo_roots` 그대로). `evidence.handle(event, data, *, pos=None, at=None) -> list[dict]`(`hash_of`·`cache` 인자 없음). 근거 필드: `skill = {name, path, prompts_since}`, `meta.guidelines: list[str]`(경로), `meta.scripts: list[str]`(경로), `meta.project_root`. `kind` 에 `skill_edit` 없음. `groups.gate()` 반환 키 `report, held, dropped`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_prompt_meta_guidelines_are_paths(...):
    rec = evidence.handle("UserPromptSubmit", data)[0]
    assert all(isinstance(p, str) for p in rec["meta"]["guidelines"])
    assert "hash" not in (rec["skill"] or {})

def test_edit_tools_record_nothing(...):
    assert evidence.handle("PostToolUse", {**base, "tool_name": "Edit", "tool_input": {"file_path": skill_md}}) == []

def test_hooks_post_tool_use_matches_ask_only():
    assert hooks["PostToolUse"][0]["matcher"] == "AskUserQuestion"

def test_gate_has_no_notices_or_rework():
    assert set(groups.gate()) == {"report", "held", "dropped"}

def test_merge_rejects_rework_target(obs_factory):
    with pytest.raises(ValueError):
        groups.merge({"groups": [{..., "target_type": "rework"}]})
```

- [ ] **Step 2: 실패 확인** — 위 테스트 FAIL.

- [ ] **Step 3: 구현**
  - `repo_roots`(와 그 안의 git 호출)를 `paths.py` 로 옮기고 `versions.py` 를 지운다. 훅 호출 제한 시간은 1초를 유지한다.
  - `evidence.py`: 해시 계산·`_Cache.hash`·`skill_edit`·`EDIT_TOOLS`·`is_tracked_file` 을 지운다. `_Cache` 는 cwd 별 git 루트·프로젝트 루트만 캐시한다. `handle` 의 `hash_of`·`cache` 인자를 지운다.
  - `backfill.py`: `versions.hash_at` 사용과 `POST_TOOLS` 의 편집 도구를 지운다.
  - `groups.py`: `rework` 분기, `_rework`, `obs_hash`, `_root_and_name`, `EPOCH`, untracked 안내, `notices` 를 지운다.
  - `groups-schema.json`: `target_type` enum 에서 `rework`, `rework_kind`·`fix_version`·`edits`·`recurrence` 속성을 지운다.
  - `hooks.json`: `PostToolUse` matcher 를 `AskUserQuestion` 로.

- [ ] **Step 4: 통과 확인** — 전체 PASS, `grep -rn "versions\|rework\|skill_edit\|hash_of\|snapshot" plugins/harness-loop/scripts plugins/harness-loop/hooks plugins/harness-loop/defaults` 결과 없음.

- [ ] **Step 5: 커밋** — `refactor: harness-loop 스킬 버전 추적과 수정 방식 재검토 제거`

---

### Task 3: 결정 패턴 관찰, 2회·2세션 관문, rubric 연동

**Files:**
- Modify: `defaults/rubric.md`(아래 전문으로 교체), `defaults/config.yaml`, `defaults/observe-schema.json`, `defaults/groups-schema.json`, `scripts/observe.py`, `scripts/groups.py`, `scripts/retro.py`(`pending` 의 `recent_rules`, `replay-gate` 의 rule 검사)
- Create: `plugins/harness-loop/tests/test_rubric_linkage.py`
- Test: `tests/test_observe.py`, `tests/test_groups.py`, `tests/test_retro_cli.py`

**Interfaces:**
- Consumes: Task 2 의 `groups.judge_group`.
- Produces:
  - 관찰 유형 enum: `correction, repeat, failure, approval_question, decision_pattern`. 관찰 항목에 `rule`(string). `verdict` 가 true 인 `decision_pattern` 은 `rule` 필수(`observe.save`·`replay-gate` 둘 다 검사).
  - `groups-schema.json`: `verified.finding` enum `missing, no_instruction, wrong, ignored, has_script, asks_here, asks_user, opposite, environment`. 묶음 속성 `conflicts: array of string`(선택).
  - `groups.ALLOWED`: 기존 6개 + `"asks_user": {"fix"}`, `"opposite": {"fix"}`, `"environment": set()`.
  - `groups.DROP_REASONS = ("unverified", "not_specific", "no_failure_ref", "decided", "environment", "inconsistent", "conflicting_choice", "scope")`.
  - `config.yaml`: `script_min_count`·`script_min_sessions`·`correction_min_count`·`guideline_min_sessions` 를 지우고 `pattern_min_count: 2`, `pattern_min_sessions: 2` 를 둔다.
  - `pending` 출력에 `recent_rules`: `window_days` 안 참 `decision_pattern` 관찰의 `rule`, 최근 것부터 중복 없이 최대 50개(`recent_purposes` 와 같은 함수로 일반화).

`judge_group` 순서(이 순서대로 첫 해당에서 끝):
1. 결정 있음 → `decided`
2. `verified` null → `unverified`; `specific_facts` 빔 → `not_specific`; `failure_ref` 가 참 관찰 아님 → `no_failure_ref`
3. `finding == "environment"` → `environment`; 짝이 `ALLOWED` 에 없음 → `inconsistent`
4. `conflicts` 에 `window_days` 안의 참 관찰이 하나라도 → `conflicting_choice`
5. window 안 참 관찰로 센다. `guideline_supplement` 는 대상이 `~/.claude/CLAUDE.md` 인지와 프로젝트 수 ≥ 2 가 다르면 `scope`
6. 넘는지: 관찰 유형에 `approval_question` 있음 → 1회; `decision_pattern` 있음 → 결정 패턴 관찰을 같은 `rule` 끼리 세어 하나라도 패턴 기준; `script` → 전체 관찰 패턴 기준; `skill_supplement`·`guideline_supplement` → 교정 관찰만 패턴 기준; 그 밖(`fix`) → 1회. 패턴 기준 = 개수 ≥ `pattern_min_count` 그리고 세션 수 ≥ `pattern_min_sessions`.

`defaults/rubric.md` 전문:

````markdown
<!-- rubric-version: 1 -->
# 판정 기준

분류 기준은 이 파일에만 둔다. 판정·묶기·검증 서브에이전트는 이 파일을 따른다. 설정 이름(`config.yaml`)으로 적은 기준과 버림 코드는 관문 스크립트가 적용한다.

## 관찰 판정

관찰 판정은 해당과 아님 두 값에 이유 한 줄이다. 중간 등급은 없다.

| 관찰 | 유형 코드 | 해당 조건 |
|---|---|---|
| 교정 | `correction` | 사용자가 AI 가 이미 내린 판단이나 동작을 뒤집거나 바로잡았다. 새 요청·추가 지시는 아니다. 스킬 실행 중과 일반 작업 모두 본다. 근거의 `after_rejection` 이 참이면(직전 도구 호출을 사용자가 거절함) 강한 근거다 |
| 반복 실행 | `repeat` | 같은 목적의 명령·inline 스크립트를 다시 작성했다 |
| 실패·이탈 | `failure` | 스킬 실행 중 도구가 실패했거나, AI 가 스킬이 정한 절차와 다르게 움직였다 |
| 승인용 질문 | `approval_question` | 스킬 실행 중 AI 가 이미 결정해 놓고 승인만 남긴 질문이다: 대안 없는 "이대로 진행할까요?", 선택지가 진행·취소뿐이고 취소하면 다음 경로가 없음, 계획을 다 정한 뒤 "괜찮을까요?". 질문 원문의 형태로 판정하고, 사용자가 긍정으로 답했다는 사실은 보조 근거다 |
| 결정 패턴 | `decision_pattern` | AI 가 실제로 다른 결과를 낳는 선택지를 주었고 사용자가 그중 하나를 골랐다(직접 입력한 답 포함). 스킬 실행 중과 일반 작업 모두 본다 |

### 근거 종류별 관찰 유형

| 근거 종류 | 관찰 유형 |
|---|---|
| `prompt` | `correction`. AI 가 글로 물은 질문에 대한 답이면 `approval_question` 또는 `decision_pattern` |
| `bash` | `repeat` |
| `failure` | `failure` |
| `ask` | `approval_question` 또는 `decision_pattern` |

- 질문의 답은 질문의 성격으로 가른다: 선택지가 사실상 하나면 `approval_question`, 실제로 여럿이면 `decision_pattern`. 한 근거는 하나로만 판정한다.
- `repeat` 는 해당 여부와 상관없이 `purpose` 에 명령의 목적을 짧은 문구로 쓴다.
- `decision_pattern` 이 해당이면 `rule` 에 "<상황>이면 <선택>" 형식의 규칙 문장 하나를 쓴다. 상황은 다시 나올 조건으로 쓰고 특정 세션·파일 이름을 쓰지 않는다.
- 같은 목적·같은 규칙이면 같은 문구를 쓴다. 판정 입력의 `recent_purposes`·`recent_rules` 에 같은 뜻의 문구가 있으면 글자 그대로 쓴다. 관문은 이 문구로 묶인 관찰을 센다.

## 관찰 제외

| 제외 | 적용 관찰 | 예 |
|---|---|---|
| 되돌리기 어렵거나 외부로 나가는 동작 앞의 확인 | `approval_question`, `decision_pattern` | push, 삭제, 메시지 전송, 티켓 생성, 배포 |
| 일반 작업 중 질문 | `approval_question` | 스킬 실행 중이 아닌 질문(고칠 지시 지점이 없다) |
| 환경 원인 | 모두 | 인증, 네트워크, 권한 프롬프트 |
| 바꿀 파일이 없는 것 | 모두 | 말투, 응답 품질 |
| 정리 제안 | 모두 | 메모리를 전역으로 옮기기 |

## 결정 대상

| 결정 대상 | 코드 | 원칙 |
|---|---|---|
| 스크립트화 | `script` | 같은 목적의 명령·inline 스크립트를 매번 새로 작성하고, 담을 스킬·스크립트가 없다 |
| 스킬 보완 | `skill_supplement` | 스킬은 지시대로 했으나 지시에 없는 판단에서 문제가 남았거나, 스킬 안에서 같은 결정을 반복해서 묻는다 |
| 지침 보완 | `guideline_supplement` | 스킬 밖 일반 작업에서 같은 교정이나 같은 결정이 반복되고 지침 파일에 규칙이 없다 |
| 지침·스크립트 수정 | `fix` | 스킬·지침에 적힌 지시·스크립트가 틀렸거나 AI 를 다르게 움직이게 만든다 |

## 파일 검증

결정 대상마다 대상 파일을 실제로 읽고 검증 결과를 아래 코드 하나로 적는다. 결과 코드와 결정 대상은 이 표의 짝만 쓴다. 다른 짝은 관문이 `inconsistent` 로 버린다.

| 검증 결과 코드 | 뜻 | 허용 결정 대상 |
|---|---|---|
| `missing` | 대상 파일에 해당 규칙·스크립트가 없다 | `skill_supplement`, `guideline_supplement`, `script` |
| `no_instruction` | 지시 없이 AI 가 스스로 그렇게 했다 | `skill_supplement`, `guideline_supplement` |
| `wrong` | 적힌 지시·스크립트가 틀렸다 | `fix` |
| `ignored` | 지시가 있는데 AI 가 무시했다 | `fix` |
| `has_script` | 스킬에 이미 스크립트가 있는데 쓰지 않았다 | `fix` |
| `asks_here` | 승인만 받는 질문을 하게 만든 지시 줄이 있다 | `fix` |
| `asks_user` | 스킬이 그 결정을 사용자에게 묻도록 지시한다 | `fix` |
| `opposite` | 지침의 기준이 사용자 선택과 반대다 | `fix` |
| `environment` | 원인이 환경에 있다 | 없음(관문이 `environment` 로 버림) |

### 관찰 → 결정 대상

| 관찰 | 검증 결과 | 결정 대상 |
|---|---|---|
| 교정(스킬 실행 중) | `missing`·`no_instruction` | 스킬 보완 |
| 교정(스킬 실행 중) | `wrong`·`ignored` | 지침·스크립트 수정 |
| 교정(일반 작업) | `missing`(메모리에만 있는데 재발함 포함) | 지침 보완 |
| 교정(일반 작업) | `wrong`·`ignored` | 지침·스크립트 수정(대상이 지침 파일) |
| 반복 실행 | `missing` | 스크립트화 |
| 반복 실행 | `has_script` | 지침·스크립트 수정 |
| 실패·이탈 | `wrong`·`ignored` | 지침·스크립트 수정 |
| 승인용 질문 | `asks_here` | 지침·스크립트 수정 |
| 승인용 질문 | `no_instruction` | 스킬 보완("이 단계는 묻지 않는다") |
| 결정 패턴(스킬 실행 중) | `missing` | 스킬 보완("<상황>이면 <선택>으로 한다") |
| 결정 패턴(스킬 실행 중) | `asks_user` | 지침·스크립트 수정(그 지시 줄을 기본값으로 바꿈) |
| 결정 패턴(일반 작업) | `missing` | 지침 보완 |
| 결정 패턴(일반 작업) | `opposite` | 지침·스크립트 수정(대상이 지침 파일) |
| 어떤 관찰이든 | `environment` | 버림 |

승인용 질문의 수정안은 셋 중 하나다: 묻지 않고 진행 후 결과 알림 / 실제 대안을 담은 결정 질문으로 바꿈 / 흩어진 승인을 맨 앞 한 번으로 모음.

### 대상 파일과 범위

- 지침 대상 파일은 사용자가 소유한 지침 파일이다: 프로젝트 `CLAUDE.md`(cwd 에서 위로 올라가며 적용되는 것), 프로젝트 `.claude/rules/`, 전역 `~/.claude/CLAUDE.md`. 근거의 `meta.guidelines` 가 그 cwd 에 적용되는 목록이다.
- 자동 메모리(`memory/`)는 검증 때 읽기만 하고 대상으로 삼지 않는다. 메모리 사이의 이동·정리는 결정 대상이 아니다.
- 지침 보완의 범위는 관찰이 나온 프로젝트 수로 정한다: 한 프로젝트면 그 프로젝트 지침, 2개 이상 프로젝트면 `~/.claude/CLAUDE.md`. 관문이 `meta.project_root` 로 세어 맞지 않으면 `scope` 로 버린다.
- 스킬이 대상이면 `skill_root` 에 스킬 디렉터리를 쓴다.

## 관문

관문은 스크립트가 적용한다. 횟수와 세션 수는 `window_days` 안의 참 관찰로 센다.

| 결정 대상(관찰) | 기준 |
|---|---|
| 스크립트화(반복 실행) | 같은 목적의 관찰 `pattern_min_count` 회 이상, `pattern_min_sessions` 개 이상 세션 |
| 스킬 보완·지침 보완(교정) | 같은 종류의 교정 `pattern_min_count` 회 이상, `pattern_min_sessions` 개 이상 세션 |
| 스킬 보완·지침 보완·지침·스크립트 수정(결정 패턴) | 같은 규칙의 결정 `pattern_min_count` 회 이상, `pattern_min_sessions` 개 이상 세션. 같은 상황에서 다르게 고른 관찰은 묶음의 `conflicts` 에 넣고, 하나라도 있으면 `conflicting_choice` 로 버린다 |
| 스킬 보완·지침·스크립트 수정(승인용 질문) | 1회. 질문을 만든 지점을 특정해야 한다(질문 형태로 판정하므로 횟수를 보지 않는다) |
| 지침·스크립트 수정(교정·실패·반복의 결함) | 1회. 결함 위치를 특정해야 한다 |

결정이 난 묶음은 다시 보고하지 않는다(`decided`).

## 공통 확인

하나라도 아니면 결정 대상으로 올리지 않는다.

| 확인 | 기준 | 못 넘으면 |
|---|---|---|
| 파일 검증 | 대상 파일을 읽고 검증 결과 코드를 정했다 | `unverified` |
| 구체성 | 수정안에 이 스킬·프로젝트에만 있는 사실(경로, 명령, 이름, 순서)이 있다 | `not_specific` |
| 실패 근거 | 이 규칙이 없어서(또는 틀려서) AI 가 실제로 다르게 움직인 장면을 관찰 id 로 가리킬 수 있다. 결정 패턴이면 사용자에게 다시 물은 장면이다 | `no_failure_ref` |

## 관찰로 보지 않는 경우

## 결정 대상으로 올리지 않는 경우
````

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_rubric_linkage.py`:

```python
RUBRIC = (ROOT / "defaults" / "rubric.md").read_text(encoding="utf-8")

def _codes(enum): return [f"`{v}`" for v in enum]

def test_observation_types_and_evidence_kinds_in_rubric():
    enum = observe_schema["properties"]["observations"]["items"]["properties"]["type"]["enum"]
    assert all(c in RUBRIC for c in _codes(enum) + _codes(observe.JUDGEABLE))

def test_targets_findings_drop_reasons_and_config_in_rubric():
    for enum in (target_enum, finding_enum, groups.DROP_REASONS,
                 ("pattern_min_count", "pattern_min_sessions", "window_days")):
        assert all(c in RUBRIC for c in _codes(enum))

def test_finding_table_matches_allowed():
    # "## 파일 검증" 절 표의 행: 첫 칸 코드 → 셋째 칸의 백틱 코드 집합
    assert parse_finding_table(RUBRIC) == groups.ALLOWED
    assert set(finding_enum) == set(groups.ALLOWED)

def test_rule_sections_exist():
    assert all(h in RUBRIC.split("\n") for h in rubric.SECTIONS.values())
```

관문·관찰 테스트:

```python
def test_decision_pattern_needs_rule_when_true(): ...  # observe.save → ValueError
def test_decision_pattern_reports_at_two_in_two_sessions(obs_factory): ...  # report
def test_decision_pattern_one_session_is_held(obs_factory): ...  # held
def test_conflicting_choice_drops(obs_factory): ...  # dropped conflicting_choice
def test_merge_rejects_conflicts_not_true_observations(obs_factory): ...  # ValueError
def test_script_reports_at_two_in_two_sessions(obs_factory): ...  # 기존 3회 테스트를 2회로
def test_correction_supplement_needs_two_sessions(obs_factory): ...  # 2회·1세션 → held
def test_environment_finding_drops(obs_factory): ...  # dropped environment
def test_general_decision_pattern_two_projects_project_target_is_scope(obs_factory): ...
def test_pending_has_recent_rules(...): ...
def test_replay_gate_rejects_true_decision_pattern_without_rule(...): ...
```

- [ ] **Step 2: 실패 확인** — 위 테스트 FAIL.

- [ ] **Step 3: 구현** — Interfaces 의 값과 `judge_group` 순서대로. `groups.merge` 는 `conflicts` 의 id 도 참 관찰인지 검사한다. `defaults/rubric.md` 는 위 전문으로 교체한다.

- [ ] **Step 4: 통과 확인** — 전체 PASS.

- [ ] **Step 5: 커밋** — `feat: harness-loop 결정 패턴 관찰과 2회·2세션 관문, rubric 연동 테스트 추가`

---

### Task 4: retro 스킬 문서를 새 범위에 맞춘다

**Files:**
- Modify: `plugins/harness-loop/skills/retro/SKILL.md`
- Test: `plugins/harness-loop/tests/test_skill.py`

**Interfaces:**
- Consumes: Task 1~3 의 CLI(`decide <id> accept|reject`, `metrics`, `gate` 의 `report·held·dropped`, `pending` 의 `recent_purposes·recent_rules`, 보관본 `target_file`).

- [ ] **Step 1: 실패하는 테스트를 쓴다** (`test_skill.py`)

```python
def test_no_removed_features():
    for word in ("proceed", "ticket", "rework", "skill-snapshots", "fix_version", "git log --find-object", "notices", "waiting"):
        assert word not in SKILL

def test_decision_table_covers_choices():
    assert "decide <id> accept" in SKILL and "decide <id> reject" in SKILL
    assert all(f"`{k}`" in SKILL for k in decide.KINDS)

def test_prompts_defer_criteria_to_rubric():
    # 서브에이전트 프롬프트에 분류 기준 표·짝 목록을 두지 않는다
    assert "missing → skill_supplement" not in SKILL and "bash → repeat" not in SKILL
    assert "recent_rules" in SKILL and "conflicts" in SKILL and "target_file" in SKILL
```

기존 `test_report_shows_notices_verbatim` 은 지운다.

- [ ] **Step 2: 실패 확인** — 위 테스트 FAIL.

- [ ] **Step 3: SKILL.md 를 고친다**
  - description·모드 표: 결정 입력 예시를 `1 수락, 2 거절 사소함` 으로. 진행·티켓 언급을 지운다. 확인 받는 곳은 "rubric 적용으로 빠지는 양성 묶음" 하나만 남긴다.
  - 관찰 판정 프롬프트: 판정 기준·근거 종류별 유형·문구 규칙은 "rubric 의 '관찰 판정' 절을 따른다"로 대신한다. 출력 규칙(근거마다 항목, `purpose`, 참인 `decision_pattern` 의 `rule`, `recent_purposes`·`recent_rules` 사용)만 둔다.
  - 묶기·검증 프롬프트: 관찰 당시 버전·스냅샷·git 이력 읽기와 rework 를 지운다. 현재 대상 파일을 Read 로 검증하고, 결정 대상·검증 결과 코드·대상 파일과 범위는 rubric 을 따른다고 쓴다. 결정 패턴 묶음은 같은 `rule` 끼리 묶고 반대 선택 관찰 id 를 `conflicts` 에 넣는다. decisions 가 있는 묶음에 같은 문제가 나오면 그 id 에 새 관찰만 넣는다. examples 설명은 positive = 수락.
  - 보고 형식: 결정 대상 표에서 `rework` 행 삭제, rework·재발 줄 삭제, `notices`·`waiting` 줄 삭제. `apply_target.kind` 가 `plugin_cache` 면 `- 외부 플러그인 캐시: 원본 저장소에서 고쳐야 함`. 보류·버림 줄은 `held` 수와 `dropped` 중 `decided` 를 뺀 수. 끝 줄 `결정을 입력하세요. 예: 1 수락, 2 거절 사소함`.
  - 결정 절: 수락은 `decide <id> accept`, 거절은 기존 표·명령. 진행·티켓 절을 지운다. 결과를 항목마다 `<번호>. <수락|거절 사유>` 한 줄로 보고한다. 수정은 사용자가 diff 를 보고 직접 한다고 한 줄 안내한다.
  - replay 프롬프트: `file_versions` 설명을 `target_file`(결정 시점 대상 파일 내용)로.
  - 새 하위 명령 표·`metrics` 안내: 기본 모드 끝에 쓰지 않는다(사용자가 "수락률" 등을 물으면 `metrics` 를 실행해 `overall`·`by_target`·`by_rubric_version` 을 표로 보인다는 한 줄을 모드 표에 추가).

- [ ] **Step 4: 통과 확인** — 전체 PASS.

- [ ] **Step 5: 커밋** — `docs: harness-loop retro 스킬을 수락·거절과 rubric 단일 기준에 맞춤`
