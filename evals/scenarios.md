# tech-writer 평가 시나리오

스킬을 쓴 실행(with)과 쓰지 않은 실행(without)을 같은 재료·같은 독자 정보로 비교한다. 재료는 본보기 소재(정산 배치)와 다른 도메인이다.

| # | 시나리오 | 재료 | 독자 | 통과 기준 |
|---|---|---|---|---|
| S1 | README 작성 | 이 저장소(`~/src/skills`) | backend-dev | 한 줄 소개+빠른 시작이 맨 위 / 설치·사용 명령이 저장소 내용과 일치(지어낸 명령 없음) / 다음 단계·참조 링크 / lint error 0 |
| S2 | 포스트모템 | `materials/incident-memo.txt` | backend-dev | 요약 3~4문장(영향 수치 포함) / 시각 표 타임라인 / 직접 원인과 유발 요인 구분 / 재발 방지 조치에 담당·기한(없으면 TODO) / 개인 비난 없음(당번 실명 제거) / 다이어그램 렌더링 성공 |
| S3 | 기존 설계 문서 개선 | `materials/bad-design-doc.md` | backend-dev | 구조 문제를 제안표(현재/제안/근거)로 먼저 제시하고 동의 전 구조를 바꾸지 않음 / 지적에 E-1(개요), E-3(H4), F-2(수치 없음), S-2(대안 비교 없음), 피동·흐린 결론 포함 |
| S4 | PM용 설명 | `materials/idempotency-note.md` | pm | 결론·고객 영향 먼저 / 멱등 키 같은 용어를 쓰지 않거나 비유로 풀이 / 한계(6.12 미만, 10초)를 숨기지 않음 / 결정할 것(강제 업데이트) 명시 |
| S5 | Confluence 런북 | `materials/delivery-runbook-notes.txt` | oncall (format: confluence) | 발동 조건 맨 앞 / 번호 단계+단계별 확인 / 위험 단계 앞 경고 / 롤백 / mmdc로 다이어그램 이미지 생성(assets/) / Confluence 표기(패널·구분선 지시) |
| S6 | config 없음 최초 실행 | `materials/incident-memo.txt` | (없음) | load_config 종료 코드 3 처리 / 인터뷰 질문 4개 기록 / 지정 경로에 config 생성 후 재실행 성공 / 이후 문서 작성 진행 |

S6은 스킬 실행만 한다(기준선 비교 대상 없음).
