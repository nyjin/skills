# SDD ledger — plan: docs/superpowers/plans/2026-10-15-tech-writer.md
Spec: docs/superpowers/specs/2026-10-15-tech-writer-design.md
Branch: feat/tech-writer (from main ca6cb2e)

## Pre-flight
| 생산 → 소비 | 내용 | 결과 |
|---|---|---|
| T2 → T5, T10 | `lint_doc.lint(text) -> list[Finding]`, Finding.level "error"/"warn" | 일치 |
| T3 → T11 | load_config CLI, 종료 코드 0/2/3 | 일치 |
| T4 → T6, T10 | `bash render-mermaid.sh in out`, 0/1/2 | 일치 |
| T5 → T6, T7, T10 | 원칙 `## X-n`, 예시 `### X-na`, 주석 `<!-- ✓ X-n: -->`, formatting.md가 L·N 담당 | 일치 (계획 자체 수정으로 정리됨) |
| T8 → T10 | TEMPLATE_SECTIONS 키·섹션 | 일치 |
| T9 → T10 | exemplar-notes.md (사용자 게이트) | T9에서 사용자 응답 대기 필요 — 4가지 정지 사유 중 '사용자 결정'으로 계획에 명시된 게이트 |
Task 1: complete (commits ca6cb2e..e45cab7, tests: uv run pytest tests/test_manifest.py -q → 2 passed in 0.00s)
Task 2: complete (commits e45cab7..50eba97, tests: uv run pytest -q → 14 passed in 0.13s)
Task 3: complete (commits 50eba97..bfc6a4c, tests: uv run pytest -q → 23 passed in 0.43s)
Task 4: complete (commits bfc6a4c..245b1ea, tests: uv run pytest -q → 26 passed in 2.36s)
Task 5: Ruling: 계획상 원칙·예시 테스트는 T6·T7에서 통과시키는 실패 테스트로 커밋 — task-done은 현재 통과해야 하는 'verbatim or lint'만 실행 — 비용: 없음(T7 task-done에서 전체 실행)
Task 5: complete (commits 245b1ea..829069c, tests: uv run pytest tests/test_content.py -k 'verbatim or lint' -q → 2 passed, 3 deselected in 0.05s)
Task 6: complete (commits 829069c..9ce6a40, tests: uv run pytest tests/test_content.py -k 'owns or verbatim or lint' -q → 10 passed, 2 deselected in 0.03s)
Task 7: Ruling: test_examples_have_dont_do_why가 코드 블록 안 '### '에서도 블록을 잘라 오탐 — 예시 제목 패턴 '### X-na '로만 나누도록 테스트 수정 — 비용: 없음(형식 검사 의도 유지)
Task 7: complete (commits 9ce6a40..7d60e76, tests: uv run pytest -q → 39 passed in 2.67s)
Task 8: complete (commits 7d60e76..5b976d2, tests: uv run pytest -q → 55 passed in 2.29s)
Task 9: Ruling: 사용자 결정으로 AI 흔적 글 2편을 반례로 활용 — review-checklist.md O-5에 수치 출처·절 중복·요약-본문 대응 3항목 추가(계획 범위 밖 파일 수정) — 비용: 체크리스트 3줄
Task 9: complete (commits 5b976d2..f17eefd, tests: uv run pytest -q → 56 passed in 2.27s)
Task 10: Ruling: 되말하기 1차 지적 반영(런북 회차별 기한·절차 순서 재구성, 포스트모템 날짜를 2027-02로 옮겨 본보기 간 사실 일치, 참조 401/429/503·상태 조회 추가 등) — 2차 되말하기로 재확인 — 비용: 본보기 분량 증가
Task 10: Ruling: 2차 되말하기에서 high 지적만 수정(멈춤 기준 high 0), low는 남김 — 3차 확인은 최종 fable 점검에 포함 — 비용: low 수준의 멈칫 구간 잔존
Task 11: Ruling: skill-reviewer F10(100줄 초과 reference 목차) — 원칙 reference 9개에 목차 추가, 본보기 9편은 제외(한 편을 통째로 읽는 견본이고 E-7 기준을 스스로 보여 줌) — 비용: 본보기 부분 읽기 시 범위 파악 어려움
Task 11: Ruling: 용어 통일 — exemplars/ = "본보기", 외부 분석 문서 = "참고 문서" — 비용: 없음
Task 11: complete (commits 4002f0f..5e9535c, tests: uv run pytest -q → 94 passed in 8.79s)
Task 12: Ruling: 평가 실행은 sonnet·비대화형으로 하고, 스킬 사용/미사용 블라인드 채점을 4회 반복(S5는 4차까지) — 모든 비교 시나리오에서 스킬 우세 확인 후 종료 — 비용: 대화형 경로(목차 승인, 질문)는 평가하지 않음
Task 12: complete (commits 5e9535c..3f7cf95, tests: uv run pytest -q → 94 passed in 8.55s)
Final review: fable (최종 점검 요청에 따라 most capable 대신 fable 사용) — 판정 ready with fixes, Important 5 / Minor 12
Final: fixed #1 기본 독자 없음 → exit 3 인터뷰 — test_config_without_default_audience_requests_interview RED→GREEN, suite 102/102
Final: fixed #2 비매핑 프로필 → ConfigError — test_non_mapping_profile_is_config_error RED→GREEN, suite 102/102
Final: fixed #3 런북 시각 모순(알림 15분·기한·소요 10분·연기 사전 통보) — 본보기 수정, 테스트 없음(내용)
Final: fixed #4 비개발 독자 템플릿 추가, D-4 연결 — test_template_sections_in_order[non-technical.md]·test_exemplar_follows_template RED→GREEN
Final: fixed #5 약어 첫 등장 검사(abbr-first warn) + 본보기 DLQ·JDK 풀어쓰기 — test_abbreviation_first_use_without_expansion_warns RED→GREEN
Final: fixed (minor, 사용자 요청 "만족할 때까지") #7 confluence 매크로 예외, #8 MCP 도구명, #9 "절대로" 오독, #13 런북 그림·본문 분기 일치, #14 계정 합계 쿼리, #17 Toss와 닮은 예시 재작성, 본보기 시점 주석
Final: Ruling: #6 `${CLAUDE_SKILL_DIR}` 대신 "<스킬 디렉터리>"(Base directory) 유지 — 이 변수가 스킬 본문에서 치환되는지 확인하지 못함 — 비용: 에이전트가 경로를 한 번 해석해야 함
Final: Ruling: #16 스펙 "원칙 ID별 3~5쌍" vs 구현 "문자별 3쌍 이상 + ID마다 1쌍 이상"(58쌍) — 계획 단계에서 완화, 사용자에게 보고 — 비용: 원칙별 예시 다양성 부족
Final: fixed #10 들여쓴 펜스 — test_fence_indented_in_long_ordered_list_is_checked RED→GREEN, suite 105/105
Final: fixed #11 식별자 _ 보존 — test_title_length_keeps_underscores_in_identifiers RED→GREEN
Final: fixed #12 오류 파일 귀속 — test_validation_error_points_to_file_that_set_the_value RED→GREEN
Final: minor (deferred): #15 non-technical 본보기 67줄(계획 80~200줄)
