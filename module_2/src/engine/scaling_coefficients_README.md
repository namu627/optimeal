# scaling_coefficients.csv — 계수 테이블 설명

**생성일:** 2026-03-30
**생성자:** Z (Day 1 검토 후 엔진 착수용 생성)
**최종 업데이트:** 2026-03-31 (id 10~24 추가 — ingredient_category 5분류 MixedLM)
**기반 분석:** `tests/MixedLM_Reports/`, `tests/EDAbeforeMixedLM/eda/reports/`, `otherstest/drive-download-20260330T064107Z-1-001/`

---

## 컬럼 정의

| 컬럼 | 설명 |
|------|------|
| `coefficient_id` | 행 식별자 |
| `group_type` | 조리방법 3대분류 (dry_heat/moist_heat/no_heat). 비어있으면 전체 적용 |
| `ingredient_category` | 재료 분류. id 1~9: 역할 기반 영어 3분류(main/sub/seasoning). id 10~24: DB 스키마 기준 한국어 5분류(주재료/부재료/양념류/수분류/유지류) |
| `power_law_a` | 멱함수 계수 a. `Y = a × base × N^b` |
| `power_law_b` | 멱함수 지수 b. b<1: 규모의 경제, b=1: 선형, b>1: 규모 초과 증가 |
| `se_b` | b의 표준오차. 신뢰도 플래그 산출 기준 (ε = 2 × se_b, ADR-003) |
| `ci_lower` / `ci_upper` | b의 95% 신뢰구간 |
| `r_squared` | 결정계수 (OLS 서브셋 행에만 존재) |
| `n_obs` | 분석에 사용된 관측 수 |
| `estimation_method` | 추정 방법 (아래 참조) |
| `lookup_priority` | 엔진 조회 우선순위. ORDER BY ASC LIMIT 1 |
| `source` | 데이터 출처 |
| `notes` | 비고 |

---

## 행 그룹 구분

### 그룹 A — role 기반 3분류 (id 1~9)

엔진 내부 참조용. MixedLM B_simple 및 상호작용 모델, OLS 서브셋 결과.
`ingredient_category` 값이 영어 역할명(main/sub/seasoning).

### 그룹 B — ingredient_category 5분류 (id 10~24)

**DB INSERT 대상.** `scaling_coefficient` 테이블의 실제 적재 데이터.
`ingredient.category` 컬럼(주재료/부재료/양념류/수분류/유지류)과 직접 매핑.
생성 스크립트: `otherstest/drive-download-20260330T064107Z-1-001/generate_scaling_coefficient.py`

---

## estimation_method 값 및 엔진 사용 지침

| 값 | 행 | 사용 지침 |
|----|----|-----------|
| `mixedlm` | 1~3 | 역할 기반 B_simple 모델. a·b 내적 일관성 보장. b=0.6163 전 역할 공통. 참조용 |
| `mixedlm_interaction` | 4~6 | 역할별 b 차등 적용 참조용. a=1.0 미보정. seasoning·sub는 CI가 0 포함 (신뢰도 medium) |
| `curve_fit` | 7~9 | OLS 서브셋 결과. 랜덤효과 미반영. group_type 단독 참조 필요 시만 사용 |
| `mixedlm` | 10~24 | **DB INSERT 대상.** ingredient_category 5분류 MixedLM 결과. 엔진 실제 룩업 대상 |

---

## 엔진 조회 우선순위 (ADR-002 v3)

```
1순위: nutritionist_feedback (cooking_method_id × ingredient_category) → 현재 없음
2순위: mixedlm (group_type × ingredient_category, id 10~24)
3순위: category_mean fallback → 전체 평균 b=0.6163 사용
```

**W 참고:** 엔진 구현은 id 10~24 (`estimation_method='mixedlm'`, 한국어 5분류) 행을 기본 룩업 대상으로 사용.
id 1~9는 통계 참조용으로 유지.

---

## 역변환 수식

```python
Y = power_law_a * base_amount * (N ** power_law_b)
```

- `Y`: 대규모 레시피 재료 투입량 (g)
- `base_amount`: 소규모 1인분 투입량 (g)
- `N`: 목표 인원수

---

## 신뢰도 플래그 기준 (ADR-003)

| se_b 범위 | 플래그 | 대상 행 |
|-----------|--------|---------|
| se_b ≤ 0.2 | high | id=1~3 (mixedlm role, se=0.1814), id=10~12 (주재료, se=0.011) |
| 0.2 < se_b ≤ 0.4 | medium | id=4 (main interaction, se=0.343), id=13~15 (부재료, se=0.021), id=16~18 (양념류, se=0.020), id=22~24 (유지류, se=0.026) |
| se_b > 0.4 | low | id=5~9, id=19~21 (수분류, se=0.027) |
