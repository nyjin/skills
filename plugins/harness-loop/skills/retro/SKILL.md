---
name: retro
description: 훅이 남긴 세션 근거를 판정·묶고 실제 파일로 검증해, 사용자가 결정할 스킬·지침·스크립트 개선(결정 대상)만 보고하고 진행·티켓·거절 결정을 처리하는 회고 스킬. "회고해줘", "이 세션 회고", "retro", "개선점 정리", "이 세션 돌아보기"처럼 요청하거나, SessionStart 의 "판정하지 않은 근거 N건" 알림에 답하거나, 보고 뒤 "1 진행, 2 티켓, 3 거절 사소함"처럼 결정을 입력하거나, 보고 끝의 rubric 보강 후보 안내에 답할 때 쓴다. 인자 backfill [--days N] 은 훅 설치 전 기록을 근거로 만든 뒤 회고한다.
argument-hint: "[backfill [--days N]]"
---

# harness-loop 회고 (retro)

호출 인자: `$ARGUMENTS`

## 공통 규칙

- 사용자에게 하는 말, 보고, 서브에이전트가 쓰는 `reason`·`problem`·`proposal` 은 한국어로 쓴다. 대화나 기록이 영어여도 같다. 원문 인용만 원문 그대로 둔다.
- 명령은 모두 아래 형식으로 Bash 에서 실행한다. 앞의 `CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}"` 를 빼지 않고, 다른 환경 변수를 덧붙이지 않는다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/retro.py" <하위 명령> …
```

- `${CLAUDE_PLUGIN_DATA}` 나 `${CLAUDE_PLUGIN_ROOT}` 자리가 빈 값이거나 글자 그대로 남아 있으면 아무 명령도 실행하지 않고 사용자에게 그 사실을 알린다.
- 명령에는 `uv` 가 필요하다. 없으면 설치를 안내하고 멈춘다.
- 임시 파일은 작업용 임시 디렉터리(scratchpad)에 쓴다. 아래에서는 `<tmp>` 로 적는다. `<tmp>` 는 절대 경로로 바꿔 쓴다.
- 하위 명령은 stdout 에 JSON 한 덩어리를 쓴다. 큰 출력(`context`·`gate`·`replay-inputs`)은 파일로 받아 Read 로 읽는다.
- 종료 코드별 처리는 아래 표를 따른다.

| 종료 코드 | 처리 |
|---|---|
| 0 | 다음 단계로 간다 |
| 1, `observe`·`groups`·`replay-score` | 검증 오류다. stderr 를 붙여 그 결과 파일을 쓴 서브에이전트에게 고치게 하고 다시 실행한다. 두 번 고쳐도 실패하면 stderr 를 보이고 그 모드를 멈춘다 |
| 1, 그 밖 | stderr 를 그대로 보이고 그 모드를 멈춘다 |
| 3 | 다른 retro 작업이 공용 데이터 락을 쥐고 있다. 사용자에게 알리고 잠시 뒤 다시 실행하라고 안내한 뒤 멈춘다 |

- 결정은 자유 입력으로만 받는다. 선택지 질문 도구는 쓰지 않는다.
- 승인만 받는 질문("이대로 진행할까요?")을 하지 않는다. 확인을 받는 곳은 티켓 등록 전 미리보기와 rubric 적용으로 빠지는 양성 묶음, 둘뿐이다.
- 보고·미리보기·후보 표를 보인 뒤에는 그 턴을 끝내고 사용자 입력을 기다린다.

### 서브에이전트

- 판정 서브에이전트는 Agent 도구를 `subagent_type: "general-purpose"`, `model: "opus"` 로 부른다.
- 프롬프트에는 파일 경로만 넣고 파일 내용은 넣지 않는다. `${CLAUDE_PLUGIN_ROOT}`·`${CLAUDE_PLUGIN_DATA}`·`<tmp>` 를 포함한 경로는 모두 절대 경로로 바꿔 넣는다.
- 서브에이전트는 결과 JSON 을 지정 파일에 Write 로 쓰고, 마지막 답은 지정한 한 줄만 쓴다.
- 고칠 때는 SendMessage 로 같은 서브에이전트에게 stderr 를 보낸다. SendMessage 를 쓸 수 없으면 같은 프롬프트 끝에 stderr 를 붙여 새로 부른다.

## 모드

| 인자 | 다른 진입 | 모드 |
|---|---|---|
| 없음 | "회고해줘", "개선점 정리", SessionStart 알림에 답함 | 기본 |
| `backfill [--days N]` | 없음 | 백필 후 기본 |
| 없음 | 보고 뒤 "1 진행, 2 티켓, 3 거절 사소함" 같은 입력 | 결정 |
| 없음 | 보고 끝 rubric 보강 후보 안내에 "보강할게" 같은 답 | rubric 갱신 |

## 기본 모드

1. 판정하지 않은 근거를 묶음으로 받는다. `<n>` 은 1부터 묶음마다 1씩 늘린다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/retro.py" pending --out "<tmp>/harness-pending-<n>.json"
```

2. 출력의 `count` 가 0 이면 5단계로 간다. 아니면 "관찰 판정 프롬프트"로 서브에이전트를 부른다. `<근거 파일>` 은 출력의 `file`, `<결과 파일>` 은 `<tmp>/harness-observe-<n>.json` 이다.
3. 서브에이전트의 답이 `완료 <받은 근거 수>/<판정한 근거 수>` 이고 두 수가 출력의 `count` 와 같으면 저장한다. 다르면 빠진 근거를 판정해 결과 파일에 더하게 한 뒤 저장한다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/retro.py" observe --file "<tmp>/harness-observe-<n>.json"
```

4. `pending` 출력의 `remaining` 이 0 보다 크면 1단계로 돌아간다.
5. 묶기·검증 입력을 파일로 받는다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/retro.py" context > "<tmp>/harness-context.json"
```

6. "묶기·검증 프롬프트"로 서브에이전트를 부른다. `<결과 파일>` 은 `<tmp>/harness-groups.json` 이다. 서브에이전트가 `묶음 0` 으로 답하면 7단계를 건너뛴다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/retro.py" groups --file "<tmp>/harness-groups.json"
```

7. 관문 결과와 rubric 후보를 받는다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/retro.py" gate > "<tmp>/harness-gate.json"
```

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/retro.py" rubric-candidates
```

8. "보고 형식"대로 보고하고 턴을 끝낸다.

### 관찰 판정 프롬프트

```
harness-loop 관찰 판정을 맡는다. 사용자에게 묻지 말고, 판정 이유는 한국어 한 줄로 쓴다.

1. <rubric 파일> 을 Read 로 읽는다. 판정 기준은 이 파일만 따른다.
2. <근거 파일> 을 Read 로 읽는다. evidence 의 근거마다 kind·text·excerpt·skill·flags 를 보고 판정한다. excerpt 로 모자라면 근거의 transcript 파일에서 text 를 찾아 앞뒤를 읽는다.
3. examples 는 사용자가 결함 아님으로 거절한 묶음의 문제와 이유다. 같은 경우는 해당 아님으로 판정한다.
4. <스키마 파일> 형식의 JSON 을 <결과 파일> 에 Write 로 쓴다.

규칙:
- 받은 근거 id 마다 항목을 하나 이상 쓴다. 해당하지 않으면 verdict 를 false 로 쓴다. 근거를 하나도 빼지 않는다.
- 받지 않은 id 는 쓰지 않는다. evidence_id 는 근거의 id 그대로다.
- type 은 근거 kind 로 정한다: bash → repeat, failure → failure, ask → approval_question, prompt → correction(AI 가 글로 물은 질문에 대한 답이면 approval_question).
- type 이 repeat 이면 verdict 와 상관없이 purpose 에 명령의 목적을 짧은 문구로 쓴다. 같은 목적이면 같은 문구를 쓴다.
- reason 은 판정 이유 한 줄이다.
- 마지막 답은 "완료 <받은 근거 수>/<판정한 근거 수>" 한 줄만 쓴다.
```

| 자리 | 값 |
|---|---|
| `<rubric 파일>` | `${CLAUDE_PLUGIN_DATA}/rubric.md` |
| `<근거 파일>` | `pending` 출력의 `file` |
| `<스키마 파일>` | `${CLAUDE_PLUGIN_ROOT}/defaults/observe-schema.json` |
| `<결과 파일>` | `<tmp>/harness-observe-<n>.json` |

### 묶기·검증 프롬프트

```
harness-loop 묶기·검증을 맡는다. 사용자에게 묻지 말고, problem·proposal 은 한국어로 쓴다.

1. <context 파일> 을 Read 로 읽는다. rubric 경로의 파일을 Read 로 읽고 "관찰 → 결정 대상"과 "공통 확인"을 따른다.
2. observations(참 관찰)만 묶는다. 같은 대상 파일·같은 문제끼리 한 묶음이다. groups 의 기존 묶음(decisions 에 결정이 있는 묶음 포함)과 같은 문제면 그 id 를 그대로 쓰고 새 관찰 id 만 observations 에 넣는다. 새 묶음 id 는 짧은 영어 소문자 슬러그(예: release-adr-number)로 정한다.
3. examples 는 사용자가 내린 결정 예시다(positive 는 진행·티켓, negative 는 거절). negative 와 같은 경우는 묶음으로 올리지 않는다.
4. 결정 대상마다 대상 파일을 실제로 Read 로 읽고 verified 를 채운다.
   - 관찰 당시 버전: 관찰의 skill.hash 또는 meta.scripts·meta.guidelines 의 hash. 내용은 <스냅샷 디렉터리>/<hash> 를 Read 하거나, 없으면 대상 저장소에서 git cat-file -p <hash> 로 읽는다.
   - 현재 버전: 대상 경로를 Read 로 읽는다.
   - 수정 이력이 필요하면(재발·반복 수정) git log --find-object=<hash>, git log -p -- <경로>, git diff 를 읽는다.
5. <스키마 파일> 형식의 JSON 을 <결과 파일> 에 Write 로 쓴다. 바뀐 묶음만 넣는다.

필드 규칙:
- target_type: script | skill_supplement | guideline_supplement | fix | rework. target_path 는 고칠 파일의 절대 경로(없는 파일이면 만들 위치), 스킬이면 skill_root 에 스킬 디렉터리.
- verified: checked_path(읽은 파일), hash(읽은 버전 해시, 현재 파일이면 빈 문자열), finding, 결함 줄을 특정했으면 line. 파일로 확인하지 못했으면 verified 를 null 로 둔다.
- finding 과 target_type 은 아래 짝만 쓴다.
  missing → skill_supplement | guideline_supplement | script
  no_instruction → skill_supplement | guideline_supplement
  wrong | ignored | has_script | asks_here → fix
- specific_facts: 이 스킬·프로젝트에만 있는 사실(경로, 명령, 이름, 순서) 한 개 이상. 일반론만 있으면 묶음으로 올리지 않는다.
- failure_ref: AI 가 실제로 다르게 움직인 장면의 관찰 id. 이 묶음 observations 안의 id 여야 한다.
- proposal: scope(수정 범위), opinion(의견), diff(대상 파일 기준 unified diff).
- guideline_supplement 는 관찰이 2개 이상 프로젝트에서 나왔으면 ~/.claude/CLAUDE.md, 한 프로젝트면 그 프로젝트의 CLAUDE.md 나 .claude/rules/ 파일을 대상으로 한다. 자동 메모리(memory/)는 읽기만 하고 대상으로 삼지 않는다.
- rework 는 rework_kind 가 필요하다: recurrence | verified_recurrence 는 fix_version(수정한 버전의 blob 해시), repeat_edit 은 edits(같은 구간을 고친 버전들의 blob 해시 목록). git 밖 대상은 rework 로 만들지 않는다.

마지막 답은 "묶음 <결과 파일에 쓴 묶음 수>" 한 줄만 쓴다.
```

| 자리 | 값 |
|---|---|
| `<context 파일>` | `<tmp>/harness-context.json` |
| `<스냅샷 디렉터리>` | `${CLAUDE_PLUGIN_DATA}/skill-snapshots` |
| `<스키마 파일>` | `${CLAUDE_PLUGIN_ROOT}/defaults/groups-schema.json` |
| `<결과 파일>` | `<tmp>/harness-groups.json` |

## 보고 형식

`<tmp>/harness-gate.json` 의 `report` 를 순서대로 1부터 번호를 붙여 쓴다. 항목마다 아래 형식이다.

````
1. [<결정 대상>] <스킬 이름>(<target_path>) — <problem>
   - 근거: <세션 ID 앞 8자> <skill.path 또는 cwd> — "<관찰 text 발췌>"
   - 검증: <verified.checked_path>:<verified.line> — <verified.finding>
   - 수정 범위: <proposal.scope>
   - 의견: <proposal.opinion>
   ```diff
   <proposal.diff>
   ```
````

| `target_type` | 결정 대상 |
|---|---|
| `script` | 스크립트화 |
| `skill_supplement` | 스킬 보완 |
| `guideline_supplement` | 지침 보완 |
| `fix` | 지침·스크립트 수정 |
| `rework` | 수정 방식 재검토 |

- 스킬 이름은 `skill_root` 의 마지막 디렉터리 이름, 없으면 `target_path` 의 파일 이름이다.
- 근거 줄은 묶음 `observations` 의 관찰을 `<tmp>/harness-context.json` 의 `observations` 에서 찾아 쓴다. 근거 줄은 세 줄까지 쓰고, 넘으면 `- 근거 외 N건` 한 줄을 더한다. `failure_ref` 관찰을 첫 근거로 쓴다.
- 근거 줄 아래에 `count`·`sessions` 를 `- 횟수: N회, M개 세션` 한 줄로 쓴다.
- `verified.line` 이 없으면 `:<verified.line>` 을 뺀다.
- `rework` 항목은 `rework_kind` 를 결정 대상 뒤 괄호에 쓰고, `recurrence` 가 있으면 `- 재발 관찰: <id 목록>` 한 줄을 더한다.
- `apply_target.kind` 가 `plugin_cache` 인 항목은 `- 외부 플러그인 캐시: 진행 불가, 티켓만 고를 수 있음` 한 줄을 더한다.
- 항목 뒤에 `보류 N건, 버림 M건(<reason>별 개수)` 한 줄을 쓴다. 둘 다 0 이면 쓰지 않는다.
- `notices` 가 있으면 그 뒤에 한 줄에 하나씩 그대로 쓴다. 고치거나 줄이지 않는다.
- `rubric-candidates` 출력의 `ready` 에 true 인 kind 가 있으면 `rubric 보강 후보 있음(<kind> N건) — "보강할게"로 진행` 한 줄을 쓴다.
- `report` 가 비면 `결정 대상 없음` 한 줄을 쓰고 위의 보류·버림, `notices`, rubric 줄만 쓴다.
- `report` 가 있으면 끝에 `결정을 입력하세요. 예: 1 진행, 2 티켓, 3 거절 사소함` 한 줄을 쓴다.

## 결정

1. 입력의 번호를 직전 보고에서 찾아 그 항목의 `id` 로 바꾼다. 직전 보고가 이 대화에 없으면 `gate` 를 다시 실행해 같은 순서로 번호를 매긴다. 번호가 보고에 없거나 뜻이 모호하면 그 부분만 실행하지 않고 되묻는다.
2. 결정마다 아래 표대로 처리한다.

| 입력 | 처리 |
|---|---|
| 진행 | 아래 "진행" 절 |
| 티켓 | 아래 "티켓" 절 |
| 거절 | 사유를 아래 거절 표로 `--kind` 로 바꾸고, 사용자 이유 문장은 `--reason` 에 그대로 넘긴다 |

거절 명령:

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/retro.py" decide <id> reject --kind <kind> --reason "<이유>"
```

| 사용자 사유 | `--kind` |
|---|---|
| 결함 아님(정상 동작을 결함으로 잡았다) | `not-defect` |
| 사소함(결함이지만 고칠 가치가 작다) | `trivial` |
| 안 고침(가치는 있지만 고치지 않는다) | `wont-fix` |

- 사유가 없거나 두 kind 에 걸치면 그 항목만 실행하지 않고 위 세 사유 중 무엇인지 되묻는다.
- 이유 문장이 없으면 `--reason` 을 뺀다.

3. 결정마다 결과를 한 줄로 보고한다: `<번호>. <결정> — <고친 경로 | 티켓 키 | 거절 사유>`.

### 진행

1. 항목의 `apply_target.kind` 가 `plugin_cache` 면 고치지 않는다. `원본 저장소에 티켓을 내세요: <apply_target.real_path>` 를 알리고 티켓을 고를 수 있다고 안내한다.
2. `proposal.diff` 를 `apply_target.real_path` 에 반영한다. `new` 면 파일을 만든다. 대상이 다른 저장소·worktree 여도 그 작업 트리를 직접 고치고 고친 경로를 알린다.
3. 근거가 된 문제를 다시 확인한다: 고친 스크립트를 실행하거나, 고친 지시를 근거 장면에 비춰 다시 읽는다. 확인 결과를 한 줄로 알린다.
4. 반영과 확인이 끝난 뒤에 기록한다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/retro.py" decide <id> proceed
```

5. 커밋하지 않는다. 사용자가 커밋·PR 을 요청하면 그 저장소 규칙(`apply_target.rules` 의 파일, `contribute` 스킬 등)을 따른다.

### 티켓

1. 티켓 근거 파일을 만든다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/retro.py" ticket-file <id> --out-dir "<tmp>"
```

2. 출력 `file` 을 Read 로 읽어 미리보기로 보이고, 세션 발췌가 외부로 나간다는 점을 한 줄로 알린 뒤 턴을 끝낸다.
3. 사용자가 명시적으로 확인하면 `ticket-creator` 스킬을 Skill 도구로 부르고, 인자에 그 파일 경로를 사양 파일로 넘긴다. 사용자가 고칠 내용을 말하면 파일을 고쳐 다시 미리보기를 보인다.
4. 받은 티켓 키로 기록한다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/retro.py" decide <id> ticket --ticket <KEY>
```


## rubric 갱신

1. 후보를 읽는다. 출력의 `groups` 는 kind(`not-defect`·`trivial`)별 거절 묶음이고 `ready` 는 kind 별로 `rubric_min_evidence` 를 채웠는지다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/retro.py" rubric-candidates
```

2. `ready` 가 true 인 kind 가 없으면 `규칙 후보 없음(kind 별 건수)` 한 줄을 쓰고 멈춘다.
3. `ready` 인 kind 마다 묶음의 `problem`·`note`·`first_reason` 을 읽어 같은 판단으로 묶이는 패턴을 찾는다. 패턴마다 서로 다른 묶음 id 를 `rubric_min_evidence` 개 이상 근거로 들고, 근거가 모자란 패턴은 버린다. 한 묶음은 한 패턴에만 쓴다.
4. 패턴마다 규칙 초안을 한 문장으로 쓴다. `not-defect` 는 "…이면 관찰로 보지 않는다", `trivial` 은 "…이면 결정 대상으로 올리지 않는다" 형식이다. 특정 세션·파일 이름 대신 다시 나올 상황으로 쓴다.
5. 초안을 아래 표로 보이고 턴을 끝낸다. 남은 패턴이 없으면 `근거가 충분한 패턴 없음` 한 줄만 쓴다.

| 번호 | kind | 규칙 초안 | 근거 묶음 |
|---|---|---|---|
| 1 | `not-defect` | 규칙 문장 | `id`, `id`, `id` |

6. 사용자가 고른 패턴마다 7~13단계를 차례로 진행한다. 사용자가 문구를 고치면 고친 문구를 쓴다.
7. 후보 rubric 을 파일로 만든다. 출력의 `version` 이 후보 rubric 버전이다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/retro.py" rubric-apply --kind <kind> --rule "<규칙>" --evidence <id> <id> <id> --out "<tmp>/harness-rubric-candidate.md"
```

8. 판정 입력 보관본 목록을 받는다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/retro.py" replay-inputs > "<tmp>/harness-replay-inputs.json"
```

9. "replay 프롬프트"로 서브에이전트 두 개를 한 메시지에서 함께 부른다. 하나는 `<rubric 파일>` 이 `${CLAUDE_PLUGIN_DATA}/rubric.md`, `<결과 파일>` 이 `<tmp>/harness-replay-current.json` 이고, 다른 하나는 `<tmp>/harness-rubric-candidate.md`, `<tmp>/harness-replay-candidate.json` 이다.
10. 두 결과를 채점한다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/retro.py" replay-score --current "<tmp>/harness-replay-current.json" --candidate "<tmp>/harness-replay-candidate.json"
```

11. 출력의 `eligible` 이 true 면 `current`·`candidate` 의 `tpr`·`tnr`·`n_pos`·`n_neg` 를 참고 수치로 한 줄에 보인다. false 면 test 분할 라벨이 `replay_min_labels` 보다 적어 정확도 비교를 생략했다고 한 줄로 알린다.
12. `dropped_positives` 에 따라 적용한다.

| `dropped_positives` | 처리 |
|---|---|
| 비었음 | 아래 적용 명령을 `--dropped`·`--confirmed` 없이 실행한다 |
| 있음 | 빠지는 묶음의 id 와 `problem` 을 보이고 턴을 끝낸다. 사용자가 명시적으로 확인하면 아래 적용 명령을 `--dropped` 와 `--confirmed` 를 붙여 실행한다. 확인하지 않으면 적용하지 않는다 |

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/retro.py" rubric-apply --kind <kind> --rule "<규칙>" --evidence <id> <id> <id> --dropped <id> … --confirmed
```

13. 패턴마다 `rubric 버전 <version>: <규칙>` 또는 적용하지 않은 이유를 한 줄로 보고한다.

### replay 프롬프트

```
harness-loop replay 판정을 맡는다. 사용자에게 묻지 않는다.

1. <rubric 파일> 을 Read 로 읽는다. 판정 기준은 이 파일만 따른다. 첫 줄 <!-- rubric-version: N --> 의 N 이 rubric 버전이다.
2. <목록 파일> 을 Read 로 읽는다. JSON 배열의 경로마다 input.json 을 Read 로 읽는다.
3. input.json 마다 다시 판정한다. group 은 묶음, observations 는 관찰마다 evidence·excerpt, file_versions 는 관찰 당시 대상 파일 내용이다. observations 의 observation 에 남은 예전 verdict·reason 과 input.json 의 rubric 은 쓰지 않는다.
4. 이 rubric 으로 group.failure_ref 의 근거가 여전히 관찰에 해당하고, 묶음이 "공통 확인"을 넘고 "결정 대상으로 올리지 않는 경우"에 걸리지 않으면 surfaces 를 true, 아니면 false 로 쓴다.
5. <스키마 파일> 형식으로 {"rubric_version": N, "results": [{"group": <group.id>, "surfaces": true|false}, …]} 를 <결과 파일> 에 Write 로 쓴다. 입력마다 결과를 하나씩 쓴다. 목록이 비었으면 results 를 빈 배열로 쓴다.

마지막 답은 "완료 <결과 수>" 한 줄만 쓴다.
```

| 자리 | 값 |
|---|---|
| `<rubric 파일>` | 9단계에서 정한 rubric 파일 |
| `<목록 파일>` | `<tmp>/harness-replay-inputs.json` |
| `<스키마 파일>` | `${CLAUDE_PLUGIN_ROOT}/defaults/replay-schema.json` |
| `<결과 파일>` | 9단계에서 정한 결과 파일 |

## 백필 (backfill)

1. 훅 설치 전 기록을 근거로 만든다. `--days N` 이 있으면 명령 끝에 붙인다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/retro.py" backfill
```

2. 출력의 `sessions`·`records` 를 `백필: 세션 N개, 근거 M건` 한 줄로 알리고 기본 모드 1단계로 이어 간다.
