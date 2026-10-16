# harness-loop 플러그인 설계

- 작성일: 2026-10-16
- 상태: 설계 선택 확정, 실측 완료, spec 검토 대기
- 근거 자료: 2026-09-28 자가 개선 루프 설계 세션(`~/.claude` 7210693b), 2026-10-16 세션 기록 분석(81개 세션 1,506턴, 패턴 100건), 사례 조사(Cursor·Windsurf·Devin·Cline·Codex·claude-reflect·Claudeception·claude-mem, ETH AGENTS.md 평가, ACE)

## 1. 목적

Claude Code 세션에서 AI·도구·사용자 지침 처리 과정을 돌아보고, 다음 요청이 더 정확하고 빠르고 명확해지도록 개선안(action item)을 만든다. 개선안은 기존 스킬 수정, 신규 스킬, 결정적 스크립트, 규칙(메모리·CLAUDE.md) 중 하나다.

### 성공 기준

1. 사용자가 세션 안에서 스킬을 부르면 그 세션의 action item이 나온다. 같은 세션에서 다시 부르면 새 턴만 더해 같은 문제를 하나로 합친다.
2. 스킬을 부르지 않고 끝난 세션도 판정되고, `propose`에 이른 action item은 다음 세션 시작 때 보고된다.
3. 승인한 action item은 대상 파일을 소유한 환경의 규칙대로 반영된다.
4. 근거가 약한 항목은 올라오지 않는다. 한 번에 보고하는 수는 `queue_limit`(config 값) 이하이고, 거절한 sig는 다시 올라오지 않는다.
5. 수집 스크립트를 최근 기록 전체에 돌렸을 때 eval 세션·권한 프롬프트·`Exit code N` 잡음이 걸러지고, 2026-10-16 분석의 주요 신호(C1b, C3a, C4)가 재현된다.

### 범위 밖

- 권한 프롬프트 줄이기. `fewer-permission-prompts`가 맡는다.
- 대화 내용 기억과 검색. auto memory와 mempalace가 맡는다. mempalace는 입력으로도 쓰지 않는다.
- 외부 플러그인 파일 직접 수정.

## 2. 배치

`~/src/skills` marketplace의 두 번째 플러그인으로 둔다. tech-writing과 따로 켜고 끈다.

```
~/src/skills/
├── .claude-plugin/marketplace.json      # harness-loop 항목 추가 (source: ./plugins/harness-loop)
└── plugins/harness-loop/
    ├── .claude-plugin/plugin.json
    ├── hooks/hooks.json                 # SessionStart · SessionEnd
    ├── skills/retro/SKILL.md            # 명시 진입점
    ├── scripts/
    │   ├── state.py                     # 경로 · config · cursor · lock · decisions
    │   ├── transcript.py                # 기록 → 턴, 세션 종류
    │   ├── collect.py                   # 결정적 수집 (LLM 없음)
    │   ├── judge.py                     # digest · 채점 · 병합 · 백그라운드 judge
    │   ├── report.py                    # SessionStart 보고 · sweep · 결정 기록
    │   ├── apply_target.py              # 대상 경로 → 소유 환경 판정
    │   └── hook.py                      # 훅 진입점
    ├── defaults/scoring.yaml · rubric.md · judge-schema.json
    └── tests/fixtures/                  # 기록 조각과 기대 신호
```

`~/.claude/bin/harness-collect.py`는 `collect.py`의 출발점으로 옮기고 원래 위치에서는 지운다.

## 3. 저장 위치

| 데이터 | 위치 | 수명 |
|---|---|---|
| 세션별 수집·판정 결과 | `~/.claude/projects/<프로젝트>/<세션ID>/harness/` (`HARNESS_PROJECTS_ROOT`가 있으면 그 아래 같은 구조) | 세션 기록과 같다 (`cleanupPeriodDays`) |
| 설정 `scoring.yaml`, `rubric.md` | `${CLAUDE_PLUGIN_DATA}` (첫 실행 때 `defaults/`에서 복사) | 플러그인 데이터 |
| 승인·거절·반영·점수 수정 기록 `decisions.jsonl` | `${CLAUDE_PLUGIN_DATA}` | 플러그인 데이터 |

세션 디렉터리 구조:

```
<세션ID>/harness/
├── cursor               # 마지막으로 처리한 jsonl 줄 번호
├── signals.jsonl        # collect 결과, 호출마다 덧붙인다
├── runs/NNN-<explicit|session-end|sweep>.json
└── action-items.json    # sig 를 키로 합친 현재 상태 (보고·리뷰는 이 파일만 읽는다). 항목마다 reported_at 으로 보고 여부를 표시한다(보고는 sig 단위로 한 번)
```

- 세션 ID는 `CLAUDE_CODE_SESSION_ID`(명시 호출), 훅 입력의 `session_id`·`transcript_path`(SessionEnd)로 얻는다. 기록 경로는 `HARNESS_PROJECTS_ROOT`(없으면 `~/.claude/projects`)에서 찾고, `HARNESS_PROJECTS_ROOT`가 있는데 거기서 못 찾으면 `~/.claude/projects`에서 한 번 더 찾는다.
- 관찰 목록은 따로 두지 않는다. `status: watch`인 항목이 살아 있는 세션 디렉터리에 남아 있는 동안이 관찰 기간이다.
- 재발성은 `projects_root()/*/*/harness/`(기본 `~/.claude/projects`, `HARNESS_PROJECTS_ROOT`가 있으면 그 루트)의 `signals.jsonl`·`action-items.json`을 훑어 같은 sig가 나온 서로 다른 세션 수로 센다.

## 4. 흐름

```
[세션 안]   /harness-loop:retro
              → collect(cursor 이후 턴) → judge(기존 action-items·다른 세션 sig·decisions 대조)
              → action-items.json 갱신 → 보고 → 승인/거절/점수 수정 → 반영
            /harness-loop:retro review
              → 다른 세션에서 쌓인 미검토 action item 검토
[SessionEnd] 훅 → collect(cursor 이후 턴) → 신호 있으면 백그라운드 claude -p judge
              (HARNESS_RETRO=1 로 실행, 세션 종료를 기다리지 않는다)
[SessionStart] 훅 (LLM 없음)
              → 미처리 세션(기록은 있는데 cursor 가 끝에 못 미침) 이 있으면 백그라운드 judge
              → 승인된 sig 가 그 뒤 세션에서 다시 나왔는지 확인 → 재발 시 "반영 실패"(regressed) 항목
                (외부 플러그인 캐시 대상은 sig 마다 첫 재발만 regressed, 그 뒤 재발은 watch)
              → 보고하지 않은 action item 을 queue_limit 개까지 additionalContext 로 알림
```

- judge는 명시 호출과 SessionEnd(그리고 그 보완인 SessionStart sweep)에서만 돈다. 하루 1회 배치는 없다.
- 명시 호출 리포트를 보여 준 뒤 그 세션의 `propose`·`regressed` 항목을 보고한 것으로 표시한다(`report.py list --session <id> --mark-reported`). SessionStart 알림은 이 항목을 다시 알리지 않는다. 보고는 sig 단위로 한 번이다. 어느 세션에서든 `reported_at` 이 있는 sig 는 다른 세션의 새 항목으로도 다시 알리지 않는다. 단 `regressed` 항목은 알린다.
- 명시 호출 judge는 현재 세션 모델이 대화 전체를 보고 판정한다. 백그라운드 judge(`claude -p`)는 `judge.py`가 조립한 다이제스트(신호, 해당 턴 발화와 AI 응답 꼬리)만 본다. C2·C5·C7처럼 맥락이 필요한 판정은 명시 호출 쪽이 더 정확하다. 백그라운드 judge 는 `--setting-sources project --strict-mcp-config` 로, 플러그인 데이터 디렉터리(`.claude/` 가 없다)를 cwd 로 실행한다. 사용자 설정의 훅·MCP 와 세션 cwd 프로젝트의 자동 메모리가 judge 에 섞이지 않는다.
- `HARNESS_RETRO=1`이면 두 훅 모두 아무것도 하지 않는다. judge 세션이 자기 자신을 분석하지 않게 한다.
- 서브에이전트 세션(훅 입력에 `agent_id`가 있음)에서는 두 훅 모두 아무것도 하지 않는다. `CLAUDE_CODE_CHILD_SESSION`은 가드로 쓰지 않는다. Claude Code가 보통의 최상위 세션 훅·Bash에서도 1로 설정하므로(실측) 이것으로 거르면 모든 세션이 빠진다. 워커 세션(`HERDR_PW_WORKER` 가 설정된 세션)에서는 수집은 하되 SessionStart 보고는 하지 않는다.
- SessionStart 훅 matcher는 `startup|resume`이다. compact·clear 때는 다시 보고하지 않는다.
- sweep은 플러그인 설치 이후에 수정된 기록만 대상으로 한다. 최근 10분 안에 바뀐 기록(아직 열려 있을 세션)과 훅을 부른 세션 자신은 뺀다. 설치 전 기록은 `/harness-loop:retro backfill`로 명시적으로만 처리하고, 대상 목록은 `report.py backfill-list`가 최신순으로 낸다(`skip` 세션 제외). 기록마다 `judge.py background --mode sweep`을 돌리고, 종료 코드로 결과를 나눈다: 0 판정했거나 할 일 없음, 1 판정 실패(로그 남김), 2 하루 상한, 3 세션 락이 잡혀 있음. backfill은 2에서 멈추고, 1과 3은 세고 다음 기록으로 간다.
- 세션마다 `harness/lock`으로 judge 동시 실행을 막는다. SessionEnd judge와 SessionStart sweep이 같은 세션을 잡으면 나중 쪽이 건너뛴다. 보고 쪽 쓰기(재발 표시, 보고 표시, 결정 기록)도 같은 락을 잡는다. 재발 표시와 보고 표시는 락이 잡혀 있으면 그 세션을 건너뛰고 `hook-errors.log`에 한 줄 남긴다. 건너뛴 재발 표시는 다음 SessionStart에서 다시 시도하고, 보고 표시를 건너뛴 항목은 다음에 다시 보고된다. 결정 기록(`decide`)은 잠시 재시도하고, 그래도 못 잡으면 아무것도 쓰지 않고 오류로 끝난다.
- 백그라운드 judge 실행 수는 `max_background_judges_per_day`(config 값)를 넘지 않는다. 넘으면 남은 세션은 다음 날 sweep으로 미룬다. judge는 `claude -p`를 부르기 전에 `judge-runs.jsonl`에 예약 줄을 쓰고 끝나면 결과 줄을 쓴다. 하루 실행 수는 두 줄을 (세션, 시작 시각)으로 묶어 한 번만 센다. 그래서 아직 끝나지 않은 judge도 하루 실행 수에 들어간다. SessionStart는 남은 횟수만큼만 sweep judge를 띄운다.
- 외부 플러그인 캐시 대상의 재발로 `watch`가 된 항목(`regressed_after`가 있음)은 이후 병합·재채점에서도 `watch`로 둔다. 승인·거절·반영·재발(`regressed`) 상태도 병합·재채점이 바꾸지 않는다.
- judge 는 대상 파일에 바꿀 것이 있을 때만 항목을 낸다. 병합은 judge 가 영향·해결 가능성을 모두 1 로 준 항목을 버린다(고칠 것이 없는 항목의 결정적 하한).
- 훅은 항상 `exit 0`으로 끝나고, 오류는 `${CLAUDE_PLUGIN_DATA}/hook-errors.log`에 쌓아 SessionStart 알림에 건수를 붙인다.

## 5. 감지 조건

`collect.py`가 결정적으로 신호를 뽑고, LLM 판정이 필요한 조건은 후보만 표시해 judge에 넘긴다.

턴은 사람 발화 하나에서 시작한다. AI 가 일하는 도중 사용자가 친 발화(`type: attachment`, `attachment.type: queued_command`, `commandMode: prompt`, `origin.kind` 가 없거나 `human`)도 새 턴을 연다. 작업 알림(`task-notification`)과 다른 에이전트 메시지(`origin.kind: peer`)는 사람 발화가 아니다.

| # | 조건 | collect가 잡는 신호 | judge가 판정할 것 |
|---|---|---|---|
| C1 | 도구 결과를 보고 고쳐 다시 부름 | `is_error` 뒤 같은 도구 재호출(재시도), 다른 도구 호출(우회). 오류의 주제 줄(잡음 규칙 참고)을 정규화해 토픽으로 쓴다 | 원인이 인자·인증·CLI 플래그·하네스 제약 중 무엇인지 |
| C2 | 원치 않는 결과라 사용자가 재요청 | 다음 사용자 발화의 교정 표현, 도구 거절, 중단 | 앞 AI 응답과 비교해 실제 교정인지, 무엇을 잘못했는지 |
| C3 | 결정적 스크립트로 할 일을 AI 호출로 처리 | Bash 명령 군집의 세션 간 반복, inline python 헬퍼 정의, scratchpad 스크립트 생성 후 미커밋, 스킬 실행마다 같은 참조 파일 읽기 | 스크립트화 가치, 기존 도구(Edit 등)로 충분한지 |
| C4 | 불필요한 확인·승인 게이트 | AI 응답이 질문으로 끝나고 다음 발화가 맨 동의뿐, "묻지 말고" 뒤의 확인 질문 | 확인이 필요했는지 |
| C5 | 확인 없이 단정했다가 철회 | AI 응답의 철회 표현(정정·과장·가정이었·판단이 일렀) | 사용자 반문이 계기였는지 |
| C6 | 범위 경계 어긋남 | 요청에 없던 Edit·Write·티켓 생성 뒤 사용자 거절·되돌림 | 요청 밖 행동인지, 반대로 고칠 drift 를 남겼는지 |
| C7 | 남긴 지침 미준수·저장 위치 오류 | 따로 잡지 않는다. C2 후보(`C2:?`)를 judge 가 C2 와 C7 중 하나로 판정한다 | 기존 메모리·CLAUDE.md 에 같은 규칙이 있는지, 다른 프로젝트 메모리에만 있는지 |
| C8 | 스킬·선호 도구 미발동 | 사용자가 스킬 이름을 댔는데 `Skill` 호출 없음(`<이름> 스킬`, `/플러그인:스킬`, 발화 맨 앞의 `/이름`. 문장 중간의 `/tmp` 같은 경로는 세지 않는다), `.codegraph/` 가 있는데 grep·find 연쇄 | 어떤 스킬·도구가 맡았어야 했는지 |
| C9 | 출력 형식 마찰 | 사용자 발화가 앞 AI 응답 문장을 그대로 포함, 요점 재질문 | 형식 문제인지 |
| C10 | 대기·폴링 낭비 | `sleep`·`Monitor`·`agent wait` 반복, "끝났나" 류 질문 | 알림·스크립트로 대체 가능한지 |
| C11 | 긴 턴 끊김·사용자 재전송 | `API Error`, AI 응답 없이 연속된 사용자 턴 | 진행 기록이 필요했는지 |

잡음 규칙:

- 권한 프롬프트 오류(`requires approval`, `multiple operations` 등)는 신호가 아니다.
- C1 토픽은 오류 본문에서 건너뛸 줄을 뺀 첫 줄이다. 건너뛰는 줄은 `Exit code N`, 셔뱅(`#!`), 마크다운 제목(`# `~`###### `), `ls -l` 출력의 `total N` 줄과 권한 표기로 시작하는 줄(`drwxr-xr-x  2 …`, 링크 수가 없는 lsd·eza 형식 포함. 권한 9자에 r·w·x 가 하나도 없으면 건너뛰지 않는다), 글자·숫자가 없는 줄(구분선·괄호만 있는 줄)이다. 본문에 `Traceback (most recent call last):` 줄이 있으면 마지막 예외 줄(`…Error`·`…Exception` 등)을 토픽으로 쓴다. 모든 줄을 건너뛰면 토픽이 없으므로 C1 신호를 만들지 않는다(`Exit code N` 단독 오류가 여기에 해당한다).
- 스킬 eval 세션(훅 차단 메시지로 식별)은 수집하지 않는다.
- 워커 세션(dispatcher 가 보낸 브리프로 시작)은 C1·C3·C10 만 수집한다.
- 다른 스킬 파일 수정은 그 스킬을 작업 대상으로 한 것이라 스킬 결함 신호가 아니다. 실행 중인 그 스킬 자신의 파일을 고친 경우만 C1 후보다.

sig 는 조건과 토픽으로 만든다. C1·C3·C4·C8·C10·C11 은 collect 가 정규화한 오류 토픽이나 명령 군집으로 sig 를 정한다. C2·C5·C6·C7·C9 는 judge 가 sig 를 정하는데, judge 에는 다른 세션 `action-items.json` 의 살아 있는 sig 목록과 `decisions.jsonl` 의 sig 목록을 함께 넘기고, 주제가 같으면 기존 sig 를 재사용하고 맞는 것이 없을 때만 새로 만들게 한다. C7 은 `C2:?` 후보에서만 나온다. judge 는 후보마다 C2 와 C7 중 하나를 골라 항목을 최대 하나만 내고, 병합 때 토픽이 같은 C2 항목과 C7 항목이 함께 오면 출력 순서상 앞의 것만 남긴다. 스킬 이름은 sig 에 넣지 않고 분포 속성(`skills: {이름: 건수}`)으로 둔다. 한 스킬에 몰리면 그 스킬을, 여러 스킬에 퍼지면 하네스 공통 원인을 고칠 대상으로 본다.

## 6. 채점

재발성은 스크립트가 같은 sig 가 나온 세션 수로 센다. judge(LLM)는 영향·해결 가능성의 1~3점과 근거를 낸다. 최종 점수는 `scoring.yaml` 가중합으로 계산하므로 가중치와 기준값을 바꾸면 LLM 없이 다시 계산된다.

| 차원 | 1 | 2 | 3 |
|---|---|---|---|
| 재발성 | 1개 세션 | 2~3개 세션 | 4개 이상 세션 |
| 영향 | 낭비 거의 없음 | 우회나 재시도 | 막힘·틀린 결과·사용자 교정 |
| 해결 가능성 | 원인 불명 | 방향은 있음 | 바로 적용할 diff 가 있음 |

```yaml
weights: {recurrence: 0.4, impact: 0.4, actionability: 0.2}
propose: 2.4
watch: 1.6
global_bump: 0.3        # 전역 CLAUDE.md·메모리 대상이면 propose·watch 에 더한다
queue_limit: 3
max_background_judges_per_day: 10
```

- 통과 조건: 같은 규칙이 이미 있으면 점수와 무관하게 폐기하되, 지켜지지 않은 기존 규칙이면 "규칙 강화" 항목으로 바꾼다(C7).
- 항목마다 근거(세션 ID·턴·인용)가 최소 1개 있어야 한다. 없으면 judge 출력 검증에서 거부한다.
- 기본값에서 한 세션에서만 나온 sig 는 영향·해결 가능성이 모두 3 이어도 2.2 로 `propose` 에 못 미쳐 `watch` 에 머문다. 다른 세션에서 다시 나와 재발성이 오르면 `propose` 가 되어 보고된다.
- 사용자가 리뷰 중 점수를 고치면 `decisions.jsonl` 에 고친 `impact`·`actionability` 를 남긴다. 이 값은 sig 를 키로 모든 세션에 적용되고, 이후 병합·재채점에서 judge 점수보다 우선한다.

## 7. action item

```json
{"sig": "C1:atlassian-403-app-not-installed", "condition": "C1", "status": "propose|watch|discarded|approved|rejected|applied|regressed",
 "scores": {"recurrence": 3, "impact": 2, "actionability": 3}, "score": 2.6,
 "target": {"path": "~/src/inbound-skills/skills/ticket-creator/SKILL.md", "owner": "repo:~/src/inbound-skills"},
 "proposal": "변경 요지와 diff 초안", "evidence": [{"session": "…", "turn": 3, "quote": "…"}],
 "skills": {"ticket-creator": 4}}
```

반영 대상은 `apply_target.py` 가 정한다. 대상 경로를 realpath 로 풀고, 소유 환경에 따라 처리한다. 세션이 열린 저장소는 기준이 아니다.

| 소유 환경 | 반영 |
|---|---|
| git 저장소 안 | 그 저장소의 CLAUDE.md·AGENTS.md·기여 절차를 따른다 |
| git 밖 (전역 CLAUDE.md, 메모리, 로컬 스킬) | 세션 안에서 diff 를 보이고 승인받아 적용한다 |
| 외부 플러그인 캐시(`~/.claude/plugins/cache`·`marketplaces`) | 고치지 않는다. 교훈을 메모리에 남기고 업스트림 이슈 초안을 만든다 |
| 신규 스킬·스크립트 | 둘 위치를 정한 뒤 그 위치의 소유 환경 규칙을 따른다 |

## 8. 실측 결과 (Claude Code 2.1.294)

| 항목 | 결과 | 설계 반영 |
|---|---|---|
| 정상 종료·SIGINT·SIGHUP 때 SessionEnd 실행 | 셋 다 실행됨, `reason: other` (headless `claude -p` 기준) | SessionEnd 를 주 경로로 쓴다. SIGKILL·크래시와 대화형 TUI 의 터미널 닫기는 SessionStart sweep 이 보완한다 |
| SessionEnd 에서 분리한 자식 프로세스 | 부모 종료 12초 뒤에도 살아 있음 (`( … ) &` + `disown`, stdin·stdout 분리) | 백그라운드 judge 를 같은 방식으로 띄운다 |
| SessionEnd 시점 `transcript_path` 기록 지연 | 훅 시점과 12초 뒤 줄 수가 같음 | 대기 없이 바로 collect 한다 |
| `--resume` 시 session_id | 유지됨. 같은 jsonl 에 이어 쌓임 (재개한 이 세션으로 확인) | cursor 로 이어 처리하면 된다 |
| `cleanupPeriodDays` 정리 범위 | 기록이 지워진 세션의 디렉터리가 하나도 남아 있지 않음 (09-15 이전 기록 정리 뒤 확인) | `harness/` 도 기록과 함께 지워진다. 별도 정리 불필요 |
| `${CLAUDE_PLUGIN_DATA}` 경로 | 기존 플러그인 기준 `~/.claude/plugins/data/<플러그인>-<marketplace>/` | 설치 후 한 번 더 확인한다 |

## 9. 검증

- `tests/fixtures/`: 2026-10-16 분석에서 확인한 사례(Atlassian 403 재시도, `command -v acli` 반복, 질문 뒤 맨 동의, eval 세션, 권한 오류)를 기록 조각으로 두고 collect 출력을 고정한다.
- 최근 기록 전체로 collect 를 돌려 조건별 신호 수와 상위 sig 를 확인하고, judge 를 돌려 propose·watch·폐기 분포를 본 뒤 기준값을 정한다.
- 명시 호출 두 번 → SessionEnd → 다음 SessionStart 순서로 한 세션을 끝까지 돌려 합치기·이어 처리·보고를 확인한다. 기본 채점에서 한 세션에서만 나온 sig 는 보고되지 않으므로, 보고는 다른 세션에서도 나온 sig 로 확인한다(`plugins/harness-loop/tests/test_e2e.py`).
- 스킬 문서는 korean-proofreader 교정과 skill-reviewer 를 거친다.
