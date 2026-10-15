<!-- 독자: backend-dev(intermediate) — 정산 배치를 운영하는 개발자, Kafka와 kubectl은 익숙함 / 어조: 합니다체 / 유형: 문제 해결(how-to) -->
<!-- 이 문서는 tech-writer 스킬의 본보기다. 소재(정산 배치, 사내 도구 이름, 수치)는 설명을 위해 만든 것이다. -->

# 정산 배치 실패 건 재처리하기

<!-- ✓ E-1: 언제 필요한지와 결과를 2문장으로 -->
정산 배치가 처리하지 못해 DLQ에 쌓인 주문을 다시 정산합니다. 실패 원인을 고친 뒤 이 가이드를 따르면 하루치 실패 건을 10분 안에 재처리할 수 있습니다.

## 이 가이드로 할 수 있는 것

<!-- ✓ E-2: 결과와 범위(다루지 않는 것)를 먼저 -->
- 특정 날짜의 DLQ 메시지를 정산 배치로 다시 보냅니다.
- 재처리 결과가 정산 결과 테이블에 반영됐는지 확인합니다.

실패 원인을 찾는 방법은 다루지 않습니다. 원인을 모르면 "정산 배치 타임아웃 해결하기"(트러블슈팅)를 먼저 봅니다.

## 사전 준비

- `settlement` 네임스페이스의 운영 권한(`settlement-operator` 역할)
- settlement-cli 2.3 이상
- 실패 원인을 고친 배포가 끝나 있어야 합니다. 원인을 고치지 않고 재처리하면 같은 주문이 다시 DLQ로 돌아옵니다.

## 절차

### 1. 재처리할 실패 건 수를 확인합니다

<!-- ✓ F-3: 어디에서(어느 토픽, 어느 명령) 확인하는지 -->
DLQ 토픽 `settlement.dlq`에서 날짜별 실패 건 수를 셉니다.

```bash
settlement-cli dlq count --env prod --date 2026-10-14
```

```text
date=2026-10-14 pending=182 oldest=2026-10-15T02:14:09+09:00
```

<!-- ✓ F-6: 이 숫자를 4단계에서 다시 쓴다는 것을 미리 알린다 -->
`pending` 값(이 예에서는 182)을 적어 둡니다. 재처리 전 반영 건수도 함께 적어 둡니다. 두 값은 "확인 방법" 절에서 재처리 결과와 비교합니다.

```sql
SELECT coalesce(sum(replayed_count), 0) AS replayed_before
FROM daily_settlement WHERE settlement_date = '2026-10-14';
```

### 2. 재처리 범위를 미리 봅니다

`--dry-run`으로 실제로 보내지 않고 대상만 확인합니다.

```bash
settlement-cli dlq replay --env prod --date 2026-10-14 --dry-run
```

```text
[dry-run] date=2026-10-14 targets=182 batches=1
```

출력의 `targets` 값이 1단계의 `pending` 값과 같아야 합니다. 다르면 재처리 중에 새 실패 건이 들어온 것이므로 1단계부터 다시 합니다.

### 3. 재처리합니다

<!-- ✓ P-2: 되돌릴 수 없는 단계 바로 앞에 경고 -->
> [!WARNING]
> 재처리한 정산은 09:00(KST) 송금 배치에 포함되어 결제사로 나갑니다. 08:30(KST) 이후에는 재처리하지 말고 다음 날 송금 주기로 미룹니다.

```bash
settlement-cli dlq replay --env prod --date 2026-10-14
```

<!-- ✓ J-2: 시스템 동작은 시스템을 주어로 -->
settlement-cli는 메시지를 500건씩 정산 배치로 다시 보내고, 정산에 성공한 메시지를 DLQ에서 지웁니다. 이미 정산된 주문은 건너뛰므로, 중간에 멈추거나 실패해도 같은 명령을 다시 실행하면 남은 건만 처리됩니다(중복 정산 없음). 일부만 실패하면 실패한 건은 DLQ에 남고, 성공한 건만큼 `replayed_count`가 늘어납니다.

### 4. 결과를 확인합니다

<!-- ✓ N-2: 성공 판정 기준을 수치로 -->
다시 DLQ 건 수를 셉니다. `pending=0`이면 성공입니다.

```bash
settlement-cli dlq count --env prod --date 2026-10-14
```

## 확인 방법

정산 결과 테이블에 재처리 건이 반영됐는지 확인합니다. 아래 쿼리의 `replayed` 값이 1단계의 `replayed_before`보다 1단계의 `pending` 값만큼 커야 합니다(이 예에서는 182 증가).

```sql
SELECT settlement_date, sum(replayed_count) AS replayed
FROM daily_settlement
WHERE settlement_date = '2026-10-14'
GROUP BY settlement_date;
```

Grafana의 "정산 배치 현황" 대시보드에서 "DLQ 적체" 패널이 0으로 내려왔는지도 봅니다.

## 문제가 생기면

<!-- ✓ P-3: 실패했을 때 무엇이 보이고 무엇을 하는지 -->
<!-- ✓ L-4: 증상과 대처를 같은 기준으로 표에 -->
| 증상 | 원인 | 대처 |
|---|---|---|
| 4단계에서 `pending`이 0이 아님 | 원인이 아직 남아 있음 | 남은 메시지 하나를 `settlement-cli dlq show --env prod --date <날짜> --limit 1`로 열어 `error` 필드를 확인합니다 |
| 3단계에서 `403 Forbidden` | 운영 권한 없음 | 사전 준비의 `settlement-operator` 역할을 신청합니다 |
| 08:30 이후에 재처리를 실행함 | 재처리 건이 09:00 송금에 포함되지 않을 수 있음 | `#finance-oncall`에 날짜와 건수를 알리고, 다음 날 송금에 포함되는지 재무시스템팀과 확인합니다 |
| 3단계가 30분 넘게 끝나지 않음 | 정산 DB 잠금 대기 | "정산 배치 장애 대응 런북"의 "DB 잠금" 절을 따릅니다 |
