<!-- 독자: backend-dev(intermediate) — 정산 테이블을 조회하는 리포트를 고치려는 개발자 / 어조: 개조식 / 유형: 코드 조사 보고 -->
<!-- 이 문서는 tech-writer 스킬의 본보기다. 소재(정산 배치, 주문 수집기, 저장소 이름, 수치)는 설명을 위해 만든 것이다. -->

# 대행사 결제의 merchant_code 값

<!-- ✓ E-1: 첫 줄에 결론 -->
<!-- ✓ P-4: 구현·관측까지 확인해 단정함 -->
- 정산 테이블 `settlement_item.merchant_code`에는 가맹점 코드가 들어가지만, **대행사 결제는 대행사 코드**가 들어간다.
<!-- ✓ E-11: 조사 보고는 근거 링크가 가리키는 시점을 맨 위에 적는다 -->
- 기준: [order-collector](https://git.example.com/pay/order-collector) `main`, [settlement-batch](https://git.example.com/pay/settlement-batch) `main`

## 결정적인 곳

<!-- ✓ E-11: 결론을 정하는 코드만 짧게 -->
주문 수집기가 정산 대상 주문을 만들 때, 대행사 결제면 가맹점 대신 대행사 코드를 넣는다.

```java
// OrderToSettlementMapper.merchantCode()
return order.isAgencyPayment()
        ? order.agency().code()
        : order.merchant().code();
```

## 값이 흘러가는 길

<!-- ✓ L-4: 단계를 표로 -->
<!-- ✓ E-12: 이름과 역할은 이 표 한 곳에 -->
| 단계 | 하는 일 | 근거 파일 |
|---|---|---|
| 1. 주문 수집 | 대행사 결제면 대행사 코드를 고름 | [OrderToSettlementMapper.java#L42](https://git.example.com/pay/order-collector/blob/main/src/OrderToSettlementMapper.java#L42) |
| 2. 정산 적재 | 받은 코드를 그대로 `merchant_code`에 저장 | [settlement_item.xml#L88](https://git.example.com/pay/settlement-batch/blob/main/src/mapper/settlement_item.xml#L88) |

## 데이터 확인

<!-- ✓ F-2: 환경과 수치를 밝힌다 -->
- 검증 환경 2025년 3월 정산분 41,200건 가운데 대행사 결제 3,870건: `merchant_code`가 모두 대행사 코드

## 주의할 점

<!-- ✓ P-4: 선언과 실제가 다른 곳을 첫 항목에 -->
<!-- ✓ E-13: 정정 이력 대신 함정만 남긴다 -->
- 칸 이름은 가맹점 코드지만 대행사 결제 건은 가맹점을 가리키지 않는다. 가맹점별 집계 리포트는 대행사 결제를 따로 다뤄야 한다.
<!-- ✓ P-1: 확인하지 않은 범위를 밝힌다 -->
- 운영 환경 데이터는 조회하지 않았다.
