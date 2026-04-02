# [B] curve_fit 비수렴 셀 처리 방침

**작성일:** 2026-03-30
**작성자:** X
**근거 파일:** `otherstest/curvefit/curve_fit_nonfit_cells.xlsx`
**상태:** ✅ 확정 — 변경 금지

---

## 1. 결론

> **비수렴 전 셀(18셀)에 대해 MixedLM b값으로 fallback한다.**
> curve_fit 성공 셀은 현재 데이터에 존재하지 않는다.

---

## 2. 비수렴 현황

총 18셀 전체가 비수렴. 사유별 분류:

### 사유 1 — `fit_failed` (2셀)

초기 추정값이 허용 범위 외(bounds 초과)로 curve_fit 자체가 실패.

| 셀 | role | n | unique_N | 적용 b (MixedLM) |
|----|------|---|----------|-----------------|
| 무치기 \| seasoning \| SEPARATE | seasoning | 129 | 4 | 0.348 |
| 볶기 \| sub \| SEPARATE | sub | 78 | 2 | 0.454 |

> unique_N이 각각 4, 2로 이론상 curve_fit 실행 대상이나, 데이터 분포 특성으로 수렴 불가.

### 사유 2 — `n<10_use_MixedLM_only` (3셀)

ADR-002 v2 기준 셀당 n<10 → MERGE 대상. curve_fit 실행 자체가 해당 안 됨.

| 셀 | role | n | 적용 b (MixedLM) |
|----|------|---|-----------------|
| 건열 \| main \| MERGED_3GROUP | main | 6 | 1.153 |
| 건열 \| seasoning \| MERGED_3GROUP | seasoning | 6 | 0.348 |
| 비가열 \| sub \| MERGED_3GROUP | sub | 8 | 0.454 |

### 사유 3 — `not_identifiable_unique_N=1` (13셀)

N값이 단 1종류뿐 — 멱함수 `ratio = a × N^(b-1)`에서 N이 상수가 되므로 b 식별 불가.

| 셀 | role | n | unique_N | 적용 b (MixedLM) |
|----|------|---|----------|-----------------|
| 굽기 \| main \| SEPARATE | main | 15 | 1 | 1.153 |
| 굽기 \| seasoning \| SEPARATE | seasoning | 12 | 1 | 0.348 |
| 굽기 \| sub \| SEPARATE | sub | 34 | 1 | 0.454 |
| 끓이기 \| main \| SEPARATE | main | 32 | 1 | 1.153 |
| 끓이기 \| seasoning \| SEPARATE | seasoning | 45 | 1 | 0.348 |
| 끓이기 \| sub \| SEPARATE | sub | 78 | 1 | 0.454 |
| 끓이기(삶기) \| main \| SEPARATE | main | 28 | 1 | 1.153 |
| 끓이기(삶기) \| seasoning \| SEPARATE | seasoning | 71 | 1 | 0.348 |
| 끓이기(삶기) \| sub \| SEPARATE | sub | 108 | 1 | 0.454 |
| 볶기 \| seasoning \| SEPARATE | seasoning | 115 | 1 | 0.348 |
| 절이기(담그기) \| seasoning \| SEPARATE | seasoning | 10 | 1 | 0.348 |
| 찌기 \| seasoning \| SEPARATE | seasoning | 10 | 1 | 0.348 |
| 튀기기 \| sub \| SEPARATE | sub | 24 | 1 | 0.454 |

---

## 3. 구조적 원인 분석

unique_N=1 셀이 13개(전체의 72%)에 달하는 것은 현재 대규모 레시피 DB의 구조적 한계다.

```
현재 데이터의 N 고유값: 80, 100, 120, 130, 210, 260, 300인 (7종)
대부분의 조리방법 셀: N=100인 레시피만 존재 (unique_N=1)
```

멱함수 `ratio = a × N^(b-1)`에서 N이 단 1개 값이면 log(N)이 상수가 되어
**b와 a를 동시에 식별하는 것이 수학적으로 불가능**하다.

---

## 4. 확정 처리 방침

### 4-1. 엔진 Fallback 규칙

```
curve_fit 성공 셀 존재 → curve_fit b 사용   [해당 없음 — 현재 데이터 기준]
curve_fit 비수렴 (전 사유 공통) → MixedLM b 사용
```

Rule-based 엔진의 `v_scaling_lookup` 조회 시 `estimation_method`가
`'curve_fit'`인 레코드가 없으면 `'mixedlm'` 레코드를 자동 사용.
별도 분기 로직 없이 `lookup_priority` 정렬로 자연스럽게 처리됨.

### 4-2. 적용 b값 출처

비수렴 셀 전체에 적용되는 b값은 MixedLM B안의 **role별 고정효과**에서 도출:

| role | b 적용값 |
|------|--------:|
| main | 1.153 |
| sub | 0.454 |
| seasoning | 0.348 |

### 4-3. `scaling_coefficient` DB 적재 방침

비수렴 셀에 대해 `estimation_method = 'mixedlm'`으로 적재.
`curve_fit` 레코드는 생성하지 않음 (비어 있는 것이 올바른 상태).

---

## 5. 논문 기재 의무

```
"scipy.optimize.curve_fit을 n≥10·unique_N≥2인 셀에 대해 보조적으로 실행하였으나,
현재 데이터의 N 고유값이 7종(80~300인)에 불과하고 대부분의 셀이 단일 N값으로
구성되어 있어 18개 전체 셀에서 curve_fit b 식별이 불가하였다.
이에 모든 셀의 b값은 MixedLM B안 추정값을 사용하였다."
```

---

## 6. Z 리뷰 체크리스트

- [x] 비수렴 사유 3종이 빠짐없이 분류되었는가?
- [x] 각 사유별 처리 방침이 명확한가?
- [x] 엔진 구현(W)에 전달할 Fallback 규칙이 구체적인가?
- [x] DB 적재 방침이 명시되었는가?
- [x] 논문 기재 문구가 준비되었는가?
