<!-- 독자: backend-dev(intermediate) — 정산 배치 담당 개발자, HikariCP와 PostgreSQL 기본 개념은 앎 / 어조: 합니다체 / 유형: 문제 해결(트러블슈팅) -->
<!-- 이 문서는 tech-writer 스킬의 본보기다. 소재(정산 배치, 사내 도구 이름, 수치)는 설명을 위해 만든 것이다. -->

# 정산 배치 커넥션 타임아웃 해결하기

<!-- ✓ E-5: 독자가 검색할 증상 키워드(커넥션 타임아웃)를 제목에 -->
<!-- ✓ E-1: 이 문서로 무엇을 할 수 있는지 -->
정산 배치가 `Connection is not available` 오류로 멈출 때 원인을 가려내고 해결합니다. 원인은 대부분 세 가지 중 하나이며, 가능성이 높은 순서로 확인합니다.

## 증상

<!-- ✓ D-2: 에러 메시지를 원문 그대로, 발생 환경과 함께 -->
정산 배치 로그에 다음 오류가 반복되고, 배치가 `FAILED`로 끝납니다.

```text
HikariPool-1 - Connection is not available, request timed out after 30000ms.
  at com.zaxxer.hikari.pool.HikariPool.createTimeoutException(HikariPool.java:696)
```

- 환경: 운영(`settlement` 네임스페이스), 정산 배치 3.x
- 시각: 대부분 02:00~02:30, 정산 배치가 하루치 주문을 처리하는 시간대
- Grafana "정산 배치 현황"의 "DB 커넥션 대기" 패널이 30초 이상으로 치솟습니다.

## 원인

<!-- ✓ S-3: 원인을 이해하는 데 필요한 배경을 두세 문장으로 -->
확인은 원인 1부터 합니다. 설정 두 값만 비교하면 돼 10초면 끝나고, 대부분 여기서 걸립니다.

정산 배치는 커넥션 풀(HikariCP)에서 DB 커넥션을 빌려 쓰고, 30초 안에 빌리지 못하면 이 오류를 냅니다. 즉 이 오류는 "DB가 죽었다"가 아니라 "빌려줄 커넥션이 없다"는 뜻입니다. 커넥션이 모자라는 이유는 다음 셋 중 하나입니다.

| # | 원인 | 확인 방법 | 비중 |
|---|---|---|---|
| 1 | 동시 실행 스텝 수가 풀 크기보다 많음 | 배치 설정의 `concurrency`와 `maximum-pool-size` 비교 | 높음 |
| 2 | 긴 트랜잭션이 커넥션을 오래 잡고 있음 | PostgreSQL `pg_stat_activity`에서 5분 넘은 쿼리 확인 | 중간 |
| 3 | 다른 배치가 같은 DB 계정의 커넥션 한도를 씀 | DB 계정별 커넥션 수 확인 | 낮음 |

## 해결 방법

### 원인 1: 동시 실행 수가 풀 크기보다 많음

설정 파일에서 두 값을 비교합니다.

```bash
kubectl get configmap settlement-batch -n settlement -o yaml | grep -E "concurrency|maximum-pool-size"
```

<!-- ✓ F-2: 판단 기준을 수치로 -->
`concurrency`가 `maximum-pool-size`보다 크면 이 원인입니다. 스텝 하나가 커넥션 하나를 계속 쓰고, 배치 메타데이터 저장에 2개가 더 필요하므로 풀 크기는 `concurrency + 2`로 맞춥니다.

<!-- ✓ L-2: 한 단계 한 동작 -->
1. `maximum-pool-size`를 `concurrency + 2`로 올립니다. 예) `concurrency: 16`이면 `maximum-pool-size: 18`
2. 설정을 배포합니다(배포 파이프라인 `settlement-batch-config`).
3. 정산 배치를 재실행합니다.

<!-- ✓ S-1: 해결 원리를 한 문장으로 -->
풀 크기가 동시 실행 수보다 크면 모든 스텝이 커넥션을 받은 뒤에도 여유분이 남아, 대기가 생기지 않습니다.

### 원인 2: 긴 트랜잭션

5분 넘게 실행 중인 쿼리를 찾습니다.

```sql
SELECT pid, now() - xact_start AS duration, left(query, 80) AS query
FROM pg_stat_activity
WHERE datname = 'settlement' AND state <> 'idle'
  AND now() - xact_start > interval '5 minutes';
```

결과가 있으면 그 쿼리를 실행한 작업을 찾아 멈추고, 정산 배치를 재실행합니다. 쿼리를 강제로 끊는 방법은 "정산 배치 장애 대응 런북"의 "DB 잠금" 절을 따릅니다.

### 원인 3: 계정 커넥션 한도

```sql
SELECT usename, count(*) FROM pg_stat_activity GROUP BY usename ORDER BY 2 DESC;
```

`settlement_app` 계정이 한도(100)에 가까우면 같은 계정을 쓰는 다른 배치가 있는지 플랫폼팀에 문의합니다.

## 해결 확인

<!-- ✓ N-2: 해결됐는지 독자가 직접 확인할 기준(공개 문서에서 자주 빠지는 단계) -->
다음 세 가지가 모두 맞으면 해결된 것입니다.

1. 재실행한 배치가 `COMPLETED`로 끝납니다.

   ```bash
   settlement-cli status --env prod --date 2026-10-14
   ```

2. 배치 로그에 `Connection is not available`이 더 이상 없습니다.
3. "DB 커넥션 대기" 패널이 배치 시간대에도 1초 아래로 유지됩니다.

## 재발 방지

<!-- ✓ P-3: 같은 문제를 미리 잡을 장치 -->
- 배치 시작 시 `concurrency`가 `maximum-pool-size - 2`보다 크면 실행을 거부하는 검사가 3.4부터 들어가 있습니다. 3.4 미만이면 업그레이드합니다.
- "DB 커넥션 대기"가 10초를 넘으면 `#settlement-alert`로 알림이 갑니다. 알림이 오면 이 문서의 원인 1부터 확인합니다.
