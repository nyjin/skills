---
name: retro
description: Claude Code 세션에서 하네스 결함 신호를 모아 채점하고, 스킬·규칙·스크립트 개선안(action item)을 보고·결정·반영하는 회고 스킬. "이 세션 회고해줘", "action item 정리해줘", "이 세션 돌아보기", "개선점 정리", "retro", "검토 대기 항목 보여줘", "검토할게"처럼 요청하거나 SessionStart 의 harness-loop 검토 대기 알림에 답할 때 쓴다. 인자 review 는 쌓인 항목 검토, backfill [--days N] 은 설치 전 기록 처리다.
argument-hint: "[review | backfill [--days N]]"
---

# harness-loop 회고 (retro)

호출 인자: `$ARGUMENTS`

## 공통 규칙

- 명령은 모두 아래 형식 그대로 Bash 로 실행한다. 앞의 `CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}"` 를 빼지 않는다.
- `${CLAUDE_PLUGIN_DATA}` 자리가 빈 값이나 글자 그대로 남아 있으면 아무 명령도 실행하지 않고 사용자에게 그 사실을 알린다.
- 명령에는 `uv` 가 필요하다. 없으면 설치를 안내하고 멈춘다.
- `<transcript>` 는 세션 기록 jsonl 의 절대 경로, `<세션ID>` 는 그 파일 이름에서 `.jsonl` 을 뺀 값이다.
- 임시 파일은 작업용 임시 디렉터리(scratchpad)에 쓴다. 아래에서는 `<tmp>` 로 적는다.
- 사용자 결정은 자유 입력으로만 받는다. 선택지 질문 도구는 쓰지 않는다.
- 리포트를 출력하면 그 턴을 끝내고 사용자 입력을 기다린다. 현재 세션 회고는 보고 기록(7단계)까지 마친 뒤 다른 출력 없이 턴을 끝낸다.
- 스크립트가 0 이 아닌 코드로 끝나면 stderr 를 그대로 보여 주고 그 모드를 멈춘다. 단, `judge.py merge` 가 검증 오류로 끝나면 JSON 을 고쳐 다시 실행한다.

## 모드

| 인자 | 다른 진입 | 모드 |
|---|---|---|
| 없음 | "이 세션 회고해줘", "action item 정리해줘" | 현재 세션 회고 |
| `review` | SessionStart 알림을 보고 "검토할게" | 쌓인 항목 검토 |
| `backfill [--days N]` | 없음 | 지난 기록 처리 |
| 없음 | 리포트 뒤 "1 승인, 2 거절", "2번 영향 3으로" 같은 입력 | 결정 |

## 현재 세션 회고

1. 현재 세션 기록 경로를 얻는다. 출력 한 줄이 `<transcript>` 다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/report.py" where
```

2. 새 턴의 신호를 모은다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/collect.py" "<transcript>"
```

3. judge 입력(digest)을 파일로 만들고 Read 로 읽는다. digest 에는 채점 기준(`${CLAUDE_PLUGIN_DATA}/rubric.md`), 지난 판정 이후 신호, 신호가 걸린 턴, 살아 있는 sig, 결정된 sig, sig 규칙, 출력 스키마가 들어 있다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/judge.py" digest "<transcript>" > "<tmp>/harness-digest.md"
```

4. digest 와 이 대화 전체를 근거로 `${CLAUDE_PLUGIN_ROOT}/defaults/judge-schema.json` 형식의 JSON 을 `<tmp>/harness-judge.json` 에 쓴다. 작성 규칙은 "판정 JSON 작성 규칙" 절을 따른다.
5. 판정을 병합한다. 항목이 없어도 `{"items": []}` 로 실행한다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/judge.py" merge "<transcript>" --mode explicit < "<tmp>/harness-judge.json"
```

6. 이 세션의 항목을 읽어 "리포트 형식" 절에 맞춰 출력한다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/report.py" list --session "<세션ID>"
```

7. 리포트를 출력한 바로 뒤 보고한 항목(`propose`·`regressed`)을 기록하고 턴을 끝낸다. 기록한 항목은 SessionStart 알림에 다시 나오지 않는다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/report.py" list --session "<세션ID>" --mark-reported
```

### 판정 JSON 작성 규칙

- digest 신호 표의 신호를 하나씩 판정한다. 결함이 아니면 항목을 만들지 않는다.
- 신호로 잡히지 않았어도 대화에서 C1~C11 에 해당하는 사례를 찾으면 항목으로 넣는다.
- `sig` 는 digest 의 "sig 규칙" 절을 따른다. `?` 를 그대로 쓰지 않는다.
- `C2:?` 신호는 `condition` 을 C2(AI 가 원치 않는 일을 했다)와 C7(기존 메모리·CLAUDE.md 규칙을 따르지 않았거나 잘못된 곳에 저장했다) 중 하나로 고른다. 신호 하나에서 항목은 하나만 만들고, 같은 근거로 C2 항목과 C7 항목을 함께 만들지 않는다.
- `impact`·`actionability` 는 digest 의 채점 기준표로 1~3 을 준다. 재발성은 쓰지 않는다.
- `evidence` 는 항목마다 1개 이상이고, `session` 은 `<세션ID>`, `turn` 은 digest 의 턴 번호, `quote` 는 원문 인용이다.
- `target.path` 는 고칠 파일의 절대 경로다. 아직 없는 스킬·스크립트면 만들 위치를 쓴다.
- `proposal` 첫 줄은 개선안 요지 한 문장이고, 다음 줄부터 diff 초안을 쓴다.
- 같은 규칙이 이미 CLAUDE.md·메모리·스킬에 있으면 `duplicate_of_rule` 에 그 파일과 문구를 쓰고, 그 규칙이 지켜지지 않았으면 `rule_unenforced` 를 true 로 둔다. 없으면 null 과 false 다.
- `skills` 는 그 sig 가 나온 스킬별 건수다. 스킬과 무관하면 `{}` 다.

## 쌓인 항목 검토

1. 검토 대기 항목(propose·regressed)을 읽는다. SessionStart 알림과 달리 `queue_limit` 에 묶이지 않고 모두 보인다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/report.py" list
```

2. "리포트 형식" 절에 맞춰 출력하고 턴을 끝낸다. 항목이 없으면 "검토 대기 항목 없음" 한 줄만 쓴다.

모든 상태를 보려면 `list --all` 을 쓴다.

## 지난 기록 처리 (backfill)

1. 설치(`installed_at`) 전에 수정된 기록을 최신순으로 한 줄에 하나씩 파일에 쓴다. `skip` 표시가 있는 세션은 빠진다. `--days N` 이 있으면 명령 끝에 `--days N` 을 붙이고, 없으면 기간 제한 없이 나열한다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/report.py" backfill-list > "<tmp>/harness-backfill.txt"
```

2. 파일이 비어 있으면 처리 수 0 을 보고하고 멈춘다.
3. 파일을 Read 로 읽어 위에서부터 한 줄씩 차례로 처리한다. 한 줄이 `<transcript>` 하나이고, 경로에 공백이 있어도 줄 전체를 따옴표 안에 그대로 넣는다. 기록마다 Bash 를 한 번씩 실행하고 Bash 타임아웃은 600000 ms 로 준다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/judge.py" background "<transcript>" --mode sweep
```

4. 종료 코드 1 이면 `${CLAUDE_PLUGIN_DATA}/hook-errors.log` 끝줄을 본다. 이번 기록의 새 줄이 있으면 실패로 세고 다음 기록으로 간다. 새 줄이 없으면 하루 상한(`max_background_judges_per_day`)에 걸린 것으로 보고 멈춘다.
5. 처리 수(성공·실패·남은 기록)를 보고한다. 새로 생긴 검토 대기 항목은 `review` 로 본다.

## 결정

1. 사용자 입력의 번호를 직전 리포트의 번호와 sig 로 맞춘다. 번호가 리포트에 없거나 뜻이 모호하면 실행하지 않고 그 부분만 되묻는다.
2. 결정마다 실행한다.

| 입력 | 명령 |
|---|---|
| 승인 | `CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/report.py" decide "<sig>" approved` |
| 거절 | `CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/report.py" decide "<sig>" rejected` |
| 영향·해결 점수 수정 | `CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/report.py" decide "<sig>" edit-score --impact <1~3> --actionability <1~3>` (바꾸는 쪽만 준다) |

3. 점수를 고친 항목은 출력의 `score`·`status` 를 보고 바뀐 점수와 상태를 한 줄로 알린다. 고친 점수는 그 sig 가 있는 모든 세션에 적용되고, 이후 판정 병합·재채점에서도 judge 점수 대신 쓰인다.
4. 승인한 항목마다 반영 대상의 소유 환경을 판정한다. 출력 JSON 의 `kind`·`real_path`·`root`·`rules` 를 쓴다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/apply_target.py" "<target.path>"
```

5. `kind` 별로 반영한다.

| kind | 반영 |
|---|---|
| `repo` | `rules` 의 파일을 읽고 그 저장소의 기여 절차를 따라 `real_path` 를 고친다. 기여 절차를 맡는 스킬(예: `contribute`)이 있으면 그 스킬로 커밋·PR 을 만든다 |
| `local` | `real_path` 의 diff 를 보여 주고 사용자가 승인하면 적용한다 |
| `plugin_cache` | 파일을 고치지 않는다. 교훈을 메모리에 남기고, 업스트림 이슈 초안(제목·재현·제안)을 리포트에 쓴다. 이 sig 가 다시 나오면 처음 한 번만 `regressed` 로 올라오고, 그 뒤 재발은 `watch` 로 남아 보고되지 않는다 |
| `new` | 만들 위치와 초안을 보여 주고 사용자가 승인하면 만든다. 그 뒤 `root` 가 있으면 `repo`, 없으면 `local` 규칙을 따른다 |

6. 반영이 끝난 항목마다 기록한다.

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/report.py" decide "<sig>" applied
```

7. 결정·반영 결과를 항목별 한 줄로 보고한다.

## 리포트 형식

항목은 `score` 내림차순이고 1부터 번호를 붙인다. 표 한 줄과 그 아래 상세를 항목마다 쓴다.

| 번호 | 상태 | 조건 | sig | 점수(재발/영향/해결 → 합계) | 대상 | 소유 환경 |
|---|---|---|---|---|---|---|
| 1 | `status` | `condition` | `sig` | `scores.recurrence`/`scores.impact`/`scores.actionability` → `score` | `target.path` | `target.owner` |

상세는 항목마다 아래 형식으로 쓴다.

```
### 1. <sig>
- 개선안: <proposal 첫 줄>
- 근거: "<evidence[0].quote>" (세션 <evidence[0].session>, 턴 <evidence[0].turn>)
- 근거: "<evidence[1].quote>" (있을 때만)
```

- `regressed` 항목은 상세 첫 줄에 `반영 실패: <regressed_after> 이후 재발` 을 쓴다.
- review 모드는 상세에 `session`·`project` 를 한 줄 더 쓴다.
- 리포트 끝에 "결정은 자유롭게 입력하세요. 예: 1 승인, 2 거절, 3번 영향 3으로" 한 줄을 쓴다.
