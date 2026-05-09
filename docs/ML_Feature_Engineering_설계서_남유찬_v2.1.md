# ML 보조 모듈 Feature Engineering 설계서

| 항목 | 내용 |
|------|------|
| 문서 번호 | ML-FE-001 |
| 버전 | v2.1 |
| 작성자 | 남유찬 |
| 작성일 | 2026-04-22 |
| 관련 ADR | ADR-001, ADR-002 |
| 관련 FR | FR-06 (폐기), FR-07 (폐기), FR-08 (P0) |
| 상태 | **FR-06/FR-07 폐기 확정 — Rule-based 단독 채택** |

### 개정 이력

| 버전   | 일자         | 변경 내용                                                                                                                                                                                                                                                                                                                                                     |
| ---- | ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| v1.0 | 2026-04-21 | 최초 작성                                                                                                                                                                                                                                                                                                                                                     |
| v1.1 | 2026-04-22 | GitHub 이관 인수인계서(2026-04-21) 반영 — 데이터 소스를 파일 기반에서 DB 테이블 기반으로 전면 수정, Step 1 산출물 경로 명시, xlsx fallback 정책 명문화                                                                                                                                                                                                                                                |
| v1.2 | 2026-04-22 | 태스크 명세 대조 피드백 반영 — ① 4.1절 Output(y)에 ADR-001 수식 연결(`ratio = a × N^(b-1)`) 추가, ② 3.5절 검증 기준표에 Micro-F1(≥0.85), Weighted-F1(≥0.83) 추가 및 3종 F1 해석 기준 명문화, ③ Step 3 산출물 항목 전체 반영                                                                                                                                                                              |
| v1.3 | 2026-04-22 | 자체 피드백 반영 — ① 4.1절 Input X에 `ratio` 누락 추가, ② 4.2절 쿼리의 존재하지 않는 `data_quality` 컬럼 제거, ③ 수분류/유지류 건수 단정 표기 완화("약 ~건, 실제 추출 시 상이할 수 있음"), ④ Step 1/2 일정 순서 오류 수정 및 Step별 날짜 분리                                                                                                                                                                                 |
| v1.4 | 2026-05-07 | DB 실측 확인 반영 — ① `data_quality` 컬럼 실제 존재 확인, 단 전체 607건이 '미검증'으로 적재되어 있어 필터 조건으로 사용 불가 → 조건 제거 및 주석으로 사유 명시, ② `ratio BETWEEN 0.2 AND 3.0` 현재 전체 607건이 범위 내에 있어 실질적 필터링 없음 → 방어 코드로만 유지                                                                                                                                                                     |
| v1.5 | 2026-05-07 | DB 추가 실측 확인 반영 — ① `recipe_ingredient_map` 0건 미적재 확인 → 모듈 1 현재 xlsx fallback만 가능, DB 추출은 적재 완료 후 전환, ② `ingredient_category` 543건(89%)이 문자열 'nan'으로 적재 → 쿼리에 필터 추가 및 유효 데이터 64건 명시, ③ `pair_id` 컬럼 없음 확인 → `base_recipe_id + target_recipe_id` 조합으로 GroupKFold 그룹 키 대체, ④ `group_type` 한글 저장값 확인 → 인코딩 영문 기준에서 한글 기준으로 수정, ⑤ 2.3절 group_type 통계표 영문 병기 제거 |
| v1.6 | 2026-05-07 | load_recipe_data.py 소스 코드 확인 반영 — ① `ingredient_category` 'nan' 버그 원인 확인 (`str(NaN)='nan'` 변환 문제, 589번째 줄) 및 수정 (`pd.notna()` 조건으로 교체), ② `group_type` 실제 DB 저장값이 영문임을 재확인 (load_recipe_data.py에서 한글→영문 변환 후 적재) → 설계서 인코딩 기준 영문으로 복원, 2.3절 통계표 영문 병기 복원                                                                                                  |
| v1.7 | 2026-05-08 | DB 재적재 완료 반영 — ① `ingredient_category` NULL 정상 적재 확인, ② "팀장 확인 필요" 표시 전면 제거, ③ NULL 행 처리 방침 확정, ④ 상태 초안(Draft) → 구현 준비 완료로 변경                                                                                                                                                                                                                             |
| v1.8 | 2026-05-08 | 담당자 재확인 반영                                                                                                                                                                                                                                                                                                                                                |
| v1.9 | 2026-05-09 | DB 실측값 최종 확인 반영 — ① 4.2절 라벨(y) 카테고리별 실제 MixedLM 추정값으로 교체, ② 4.4절 FR-07 제외 판단 기준 업데이트, ③ 02_seed_scaling_coefficients.sql 수정 후 GitHub push 완료                                                                                                                                                                                                              |
| v2.0 | 2026-05-09 | ① FR-06 TF-IDF+로지스틱 회귀 구현 후 Macro-F1 성능 비교 ② FR-07 최종 결론 확정, ③ 6절 일정 전면 재작성                                                                                                                                                                                                                                                                               |
| v2.1 | 2026-05-09 | **FR-06/FR-07 최종 폐기 확정** — ① FR-06: TF-IDF 구현 완료 후 평가 결과 주재료 F1=0.12, Macro-F1=0.54로 목표 미달. 근본 원인 2가지 확인 (재료명 텍스트로 레시피 맥락 의존적 역할 구분 불가, 카테고리 간 b값 차이가 SE_b=0.1814 수준으로 노이즈와 구분 불가). 미등록 재료 category_mean fallback 처리로 대체. ② FR-07: RF/XGBoost 구현 완료 후 평가 결과 학습 가능 셀 5개로 일반화 불가 확인. category_mean fallback 유지. ③ 최종 결론: Rule-based 단독 채택                   |

---

## 목차

1. [문서 목적 및 범위](#1-문서-목적-및-범위)
2. [현재 데이터 현황](#2-현재-데이터-현황)
3. [ML 보조 모듈 1 — 재료 카테고리 자동 분류기 (FR-06)](#3-ml-보조-모듈-1--재료-카테고리-자동-분류기-fr-06)
4. [ML 보조 모듈 2 — 미등록 조합 스케일링 지수 예측 (FR-07)](#4-ml-보조-모듈-2--미등록-조합-스케일링-지수-예측-fr-07)
5. [하이브리드 파이프라인 통합 (FR-08)](#5-하이브리드-파이프라인-통합-fr-08)
6. [구현 일정](#6-구현-일정)
7. [참조 문서](#7-참조-문서)

---

## 1. 문서 목적 및 범위

본 문서는 OptiMeal 하이브리드 스케일링 파이프라인의 ML 보조 모듈 개발을 위한 Feature Engineering 설계서 및 구현 과정 기록이다.

ML 보조 모듈(FR-06, FR-07) 구현 및 평가를 완료하였으나, 두 모듈 모두 데이터 구조적 한계로 인해 폐기하고 Rule-based 단독 채택으로 결론 내렸다. 본 문서는 설계 의도, 구현 과정, 폐기 사유를 논문 기록용으로 보존한다.

> **최종 결론**: FR-06, FR-07 모두 폐기. 미등록 재료 및 미등록 조합은 category_mean fallback(b=0.6163)으로 처리. Rule-based 엔진 단독 채택.

### 1.1 배경: 왜 ML이 필요했는가

Rule-based 엔진은 `scaling_coefficient` 룩업 테이블에서 `(group_type × ingredient_category)` 키로 멱함수 파라미터 `(a, b)`를 조회하여 스케일링을 수행한다. DB에 등록된 재료와 조합에 대해 해석 가능성과 재현성이 높다.

그러나 두 가지 상황에서 Rule-based 처리가 불가능하여 ML 보조 모듈을 설계하였다.

- **예외 상황 1**: `ingredient_synonym` 테이블에 없는 미등록 재료가 입력되면 카테고리를 알 수 없어 룩업 키 자체가 구성되지 않는다 → FR-06 설계
- **예외 상황 2**: 재료 카테고리는 알지만 해당 `(group_type × category)` 셀이 룩업 테이블에 없으면 Fallback으로 전체 평균 `b=0.6163`을 사용하게 된다 → FR-07 설계

### 1.2 모듈 구성 및 최종 결론 요약

| 모듈         | FR    | 설계 의도             | 구현 모델                   | 최종 결론              |
| ---------- | ----- | ----------------- | ----------------------- | ------------------ |
| ML 보조 모듈 1 | FR-06 | 미등록 재료 카테고리 자동 분류 | TF-IDF + 로지스틱 회귀        | **폐기** — 구조적 한계 확인 |
| ML 보조 모듈 2 | FR-07 | 미등록 조합 스케일링 지수 예측 | Random Forest / XGBoost | **폐기** — 데이터 한계 확인 |

> **FR-06 폐기 사유**: 재료명 텍스트만으로 레시피 맥락 의존적 역할(주재료/부재료)을 구분하는 것이 TF-IDF 구조상 불가능함을 실증. 카테고리 간 b값 차이도 SE_b=0.1814 수준으로 노이즈와 구분 불가하여 설령 분류에 성공해도 스케일링 정확도 개선 효과 없음.
>
> **FR-07 폐기 사유**: 학습 가능한 셀이 ingredient_category 5종으로 제한되어 ML 일반화 불가. label_b가 카테고리 단위 고정값으로 수렴하여 RF/XGBoost 학습 의미 없음.

---

## 2. 현재 데이터 현황

> **v1.1 변경**: GitHub 이관 작업(2026-04-21) 완료에 따라, 모든 데이터 소스가 파일이 아닌 **PostgreSQL DB 테이블**로 관리된다. DB 환경은 `docker compose up -d` 실행 시 자동 구성된다.

### 2.1 학습 가능한 데이터 소스 ✏️ v1.1 수정

| DB 테이블 | 행 수 | 원본 파일 | 적재 방식 | 모듈 사용처 |
|-----------|-------|-----------|-----------|-------------|
| `recipe` + `recipe_ingredient_map` + `ingredient` | 레시피 3,057행 / **재료 행 19,919건** | 소규모·대규모 레시피 xlsx | `scripts/load_recipe_data.py` 수동 실행 | 모듈 1 학습 데이터 |
| `ml_training_dataset` | **607행** | df_B.csv | `scripts/load_recipe_data.py` 수동 실행 | 모듈 2 학습 데이터 |
| `scaling_coefficient` | **24행** | `docker/init/02_seed_scaling_coefficients.sql` | Docker 실행 시 자동 적재 | 모듈 2 라벨(b값) 소스 |
| `ingredient_synonym` | **2,040행** | 재료명정규화테이블_최종_20260307_권성민.xlsx | `scripts/load_recipe_data.py` 수동 실행 | 모듈 1 트리거 조건 판단 + 보조 참조 |

> **수동 적재 전제 조건**: 아래 5개 원본 파일을 구글드라이브에서 받아 `data/raw/`에 넣은 후 `docker exec optimeal_app python scripts/load_recipe_data.py` 실행 필요.
> ```
> 소규모_레시피_DB_남유찬_v0_10.xlsx
> 대규모_레시피_DB_남유찬_v0_08.xlsx
> 소규모대규모_레시피_매칭쌍_단일_csv_박소희.csv
> df_B.csv
> 재료명정규화테이블_최종_20260307_권성민.xlsx
> ```

### 2.2 소규모 레시피 DB 재료 역할 분포 (모듈 1 학습 데이터)

| 역할 (role) | 행 수 | 비율 | 비고 |
|------------|-------|------|------|
| 부재료 (sub) | 12,900 | 64.8% | 가장 많음 — 클래스 불균형 주의 |
| 조미료 (seasoning) | 4,819 | 24.2% | |
| 양념 (seasoning 하위) | 1,183 | 5.9% | seasoning과 병합 검토 |
| 주재료 (main) | 1,014 | 5.1% | 가장 적음 → **Macro-F1이 필요한 이유** |
| **합계** | **19,919** | 100% | |

> **주의**: `derived_category`(oil_fat, water_base)는 607건 중 64건(10.5%)만 채워져 있음. `recipe_ingredient_map.ingredient_role` 컬럼을 주 라벨로 사용하고, `ingredient.category` 컬럼으로 수분류/유지류를 보완한다.

### 2.3 ml_training_dataset 통계 (모듈 2 학습 데이터) ✏️ v1.1 수정

> v1.0에서 `df_B.csv`로 기술했으나, v1.1부터 DB의 `ml_training_dataset` 테이블 기준으로 수정.

| group_type | main | seasoning | sub | 합계 |
|------------|------|-----------|-----|------|
| dry_heat (건열) | 35 | 74 | 88 | **197** |
| moist_heat (습열) | 45 | 93 | 130 | **268** |
| no_heat (비가열) | 9 | 94 | 39 | **142** |
| **합계** | **89** | **261** | **257** | **607** |

> **DB 저장값**: `group_type`은 `load_recipe_data.py`에서 한글(건열/습열/비가열) → 영문(dry_heat/moist_heat/no_heat)으로 변환 후 적재됨. 코드에서 영문 기준으로 처리.

> **⚠ 주의**: 비가열 main이 **9건**으로 매우 적음. FR-07 구현 시 이 셀의 b 예측 신뢰도가 낮을 수 있음.

### 2.4 현재 scaling_coefficient 테이블 SE(b) 현황 ✏️ v1.1 수정

> v1.0에서 `scaling_coefficients.csv` 기준으로 9행을 기술했으나, v1.1부터 DB의 `scaling_coefficient` 테이블 기준 24행으로 수정. Docker 자동 적재(`02_seed_scaling_coefficients.sql`)로 구성되므로 별도 파일 관리 불필요.

| id | group_type | ingredient_category | power_law_b | se_b | estimation_method | 판단 |
|----|------------|---------------------|-------------|------|-------------------|------|
| 1 | - | main | 0.6163 | 0.1814 | mixedlm | ✅ se_b ≤ 0.2 |
| 2 | - | seasoning | 0.6163 | 0.1814 | mixedlm | ✅ se_b ≤ 0.2 |
| 3 | - | sub | 0.6163 | 0.1814 | mixedlm | ✅ se_b ≤ 0.2 |
| 4 | - | main (interaction) | 1.1526 | 0.3430 | mixedlm_interaction | ⚠ se_b > 0.2 |
| 5 | - | seasoning (interaction) | 0.3483 | 0.5851 | mixedlm_interaction | ⚠ se_b > 0.2 |
| 6 | - | sub (interaction) | 0.4544 | 0.5605 | mixedlm_interaction | ⚠ se_b > 0.2 |
| 7 | dry_heat | - | 0.2008 | 0.4172 | curve_fit | ⚠ se_b > 0.2 |
| 8 | moist_heat | - | 0.7715 | 0.2540 | curve_fit | ⚠ se_b > 0.2 |
| 9 | no_heat | - | 0.6716 | 0.3593 | curve_fit | ⚠ se_b > 0.2 |
| 10~24 | group_type × 한국어 5분류 (15개 조합) | 주재료/부재료/양념류/수분류/유지류 | v_scaling_lookup 뷰 대상 행 | - | mixedlm | 엔진 실제 룩업 대상 |

> **결론**: id 1~9 기준으로 9개 계수 중 6개(**67%**)가 SE(b) > 0.2. FR-07 제외 판단 기준(50% 이상이면 축소/제외)을 이미 초과.
> FR-07은 팀 회의에서 제외 결정 가능성 높음. 제외 시 category_mean fallback(b=0.6163)을 최종 fallback으로 사용하고 논문에 명시.

---

## 3. ML 보조 모듈 1 — 재료 카테고리 자동 분류기 (FR-06)

> **📋 FR-06 최종 결론: 구현 완료, 구조적 한계 확인 — 폐기. 미등록 재료는 category_mean fallback 처리.**

### 3.1 문제 정의

| 항목     | 내용                                                 |
| ------ | -------------------------------------------------- |
| 입력 (X) | 재료명 텍스트 (미등록 재료 — `ingredient_synonym` 테이블에 없는 것만) |
| 출력 (y) | 카테고리 레이블 5종: `주재료` / `부재료` / `양념류` / `수분류` / `유지류` |
| 트리거 조건 | `ingredient_id`가 DB에 없는 경우에만 실행                    |
| 담당     | 남유찬 (TF-IDF + 로지스틱 회귀)                             |

### 3.2 구현 내용

#### 3.2.1 학습 데이터 구성

| 소스 | 내용 | 행 수 |
|------|------|-------|
| `소규모_레시피_DB_남유찬_v0_10.xlsx` | ingredient_name + role(주재료/부재료/조미료/양념) | 1,226건 |
| `df_B.csv` derived_category | oil_fat→유지류, water_base→수분류 | 7건 |
| **합계** | | **1,233건** |

카테고리별 분포:

| 카테고리 | 행 수 | 비고 |
|----------|-------|------|
| 부재료 | 821 | |
| 양념류 | 213 | |
| 주재료 | 192 | |
| 수분류 | 4 | ⚠ 학습 불가 수준 |
| 유지류 | 3 | ⚠ 학습 불가 수준 |

#### 3.2.2 모델 구성

```python
Pipeline([
    TfidfVectorizer(analyzer='char', ngram_range=(2,3), max_features=5000, sublinear_tf=True),
    LogisticRegression(solver='lbfgs', max_iter=1000, C=1.0, class_weight='balanced',
                       multi_class='multinomial', random_state=42)
])
```

교차 검증: StratifiedKFold(n_splits=5, shuffle=True, random_state=42)

### 3.3 평가 결과

#### 5분류 (주재료/부재료/양념류/수분류/유지류) — 최빈값 라벨 기준

| 지표 | 결과 | 목표 | 판정 |
|------|------|------|------|
| Macro-F1 | 0.3761 | ≥ 0.80 | ❌ FAIL |
| Micro-F1 | 0.8178 | ≥ 0.85 | ❌ FAIL |
| Weighted-F1 | 0.8237 | ≥ 0.83 | ✅ PASS |

#### 3분류 (주재료/부재료/양념류) — 수분류/유지류 제외

| 카테고리 | Precision | Recall | F1 | Support |
|----------|-----------|--------|----|---------|
| 주재료 | 0.07 | 0.06 | 0.06 | 16 |
| 부재료 | 0.88 | 0.92 | 0.90 | 189 |
| 양념류 | 0.94 | 0.80 | 0.87 | 41 |
| **Macro-F1** | | | **0.61** | |

> 수분류/유지류를 제외해도 주재료 F1=0.06으로 Macro-F1 목표 미달.

#### 라벨 처리 방식별 비교

| 조건 | Macro-F1 | 주재료 F1 | 부재료 F1 | 양념류 F1 |
|------|----------|-----------|-----------|-----------|
| 최빈값 + 5분류 | 0.3761 | 0.18 | 0.88 | 0.82 |
| 최빈값 + 3분류 | 0.6105 | 0.06 | 0.90 | 0.87 |
| 첫 번째 유지 + 3분류 | 0.5366 | 0.12 | 0.74 | 0.75 |

> 어떤 라벨 처리 방식을 적용해도 주재료 F1이 0.18 이하로 Macro-F1 목표(0.80) 달성 불가.

### 3.4 폐기 사유

**사유 1 — 레시피 맥락 의존적 역할을 텍스트로 구분 불가 (구조적 한계)**

"돼지고기"는 김치찌개에서 주재료이지만 된장찌개에서는 부재료다. TF-IDF는 재료명 텍스트 패턴만 학습하므로 동일한 재료명이 두 카테고리 모두에 등장하면 어느 쪽도 특징적 패턴으로 인식하지 못한다. 주재료를 육류/어류/채소류로 세분화해도 부재료에도 동일한 육류/어류/채소류가 존재하므로 구분 불가하다.

**사유 2 — 카테고리 간 b값 차이가 노이즈 수준 (개선 효과 없음)**

MixedLM B_simple 모델의 SE_b=0.1814로, 카테고리 간 b값 차이가 통계적 불확실성 범위 내에 있다. 설령 분류에 성공하더라도 스케일링 정확도 개선 효과가 없다.

**대체 처리**: 미등록 재료는 `category_mean fallback(b=0.6163)`으로 처리하고 `confidence='low'` 플래그 부여.

**논문 기술 내용**: "FR-06(미등록 재료 카테고리 자동 분류기)을 TF-IDF + 로지스틱 회귀로 구현하여 평가한 결과, 주재료 F1=0.12, Macro-F1=0.54로 목표(0.80) 미달이었다. 근본 원인은 재료명 텍스트만으로 레시피 맥락 의존적 역할(주재료/부재료)을 구분하는 것이 구조적으로 불가능하기 때문이다. 또한 카테고리 간 b값 차이(SE_b=0.1814)가 노이즈 수준으로 미비하여 분류 성공 시에도 스케일링 정확도 개선 효과가 없음을 확인하였다. 따라서 FR-06을 폐기하고 미등록 재료는 category_mean fallback으로 처리한다."

---

## 4. ML 보조 모듈 2 — 미등록 조합 스케일링 지수 예측 (FR-07)

> **📋 FR-07 최종 결론: 구현 완료, 데이터 구조적 한계 확인 — 폐기. category_mean fallback 유지.**

### 4.1 문제 정의 (설계 당시)

| 항목 | 내용 |
|------|------|
| 트리거 조건 | `(group_type × category)` 조합이 룩업 테이블에 없을 때 |
| 입력 (X) | `ingredient_category` (5분류), `group_type` (3대분류), `base_amount_g`, `target_serving_n`, `ratio` |
| 출력 (y) | `scaling_exponent_b` (MixedLM 추정값) — ADR-001의 `ratio = a × N^(b-1)` 수식에서 b를 예측하면 ratio가 결정됨 |

### 4.2 구현 내용

- **모델**: Random Forest Regressor + XGBoost Regressor (random_state=42)
- **교차 검증**: 5-Fold GroupKFold (`base_recipe_id + target_recipe_id` 조합 기준)
- **학습 데이터**: `ml_training_dataset` 테이블 607건
- **라벨(y)**: `scaling_coefficient` 테이블 id 10~24의 카테고리별 MixedLM 추정값

| ingredient_category | power_law_b | se_b |
|---------------------|-------------|------|
| 주재료 | 0.9330 | 0.0110 |
| 부재료 | 0.8950 | 0.0210 |
| 양념류 | 0.6550 | 0.0200 |
| 수분류 | 1.0580 | 0.0270 |
| 유지류 | 0.7570 | 0.0260 |

### 4.3 평가 결과

| 모델 | CV MAE (mean ± std) | baseline 대비 개선율 |
|------|---------------------|----------------------|
| RandomForest | 0.0001 ± 0.0001 | +99.9% |
| XGBoost | 0.0001 ± 0.0000 | +100.0% |
| category_mean fallback (b=0.6163) | 0.1894 | — |

### 4.4 폐기 사유

MAE가 0.0001로 거의 0에 가까운 이유는 label_b가 ingredient_category 단위 고정값이기 때문이다. 같은 카테고리라면 group_type에 무관하게 동일한 b값이 할당되므로, 모델이 "카테고리 → b값" 5가지 매핑만 외우면 완벽한 예측이 가능하다. Feature importance에서도 cat_enc가 97.5%를 차지하여 이를 뒷받침한다.

학습 가능한 셀이 카테고리 5종으로 제한되어 ML 일반화 조건을 충족하지 못한다. 이는 "미등록 조합(물성 추론)을 현재 데이터로 ML 학습시키는 것이 불가능하다"는 요구사항정의서의 사전 판단(FR-07 P2 조건부 설계)과 일치한다.

**대체 처리**: category_mean fallback(b=0.6163) 유지.

**논문 기술 내용**: "FR-07(미등록 조합 스케일링 지수 예측)을 Random Forest 및 XGBoost로 구현하여 5-Fold GroupKFold CV로 평가하였다. MAE=0.0001로 수치상 우수하나, 이는 label_b가 ingredient_category 단위 고정값으로 수렴하여 ML이 단순 룩업 매핑을 학습한 결과다. 학습 가능한 셀이 5종으로 제한되어 미등록 조합에 대한 일반화가 불가능하다. 따라서 FR-07을 폐기하고 category_mean fallback(b=0.6163)을 최종 적용한다."

---

## 5. 하이브리드 파이프라인 통합 (FR-08)

### 5.1 최종 파이프라인 구조 — FR-06/FR-07 폐기 후

FR-06, FR-07 모두 폐기되어 ML 보조 모듈 없이 Rule-based 단독으로 동작한다.

```python
def get_scaling_params(ingredient_id, ingredient_name, group_type):
    # 1. 등록된 재료: DB에서 category 직접 조회
    if ingredient_id in registered_ingredient_ids:
        category = db_lookup_category(ingredient_id)
        confidence = 'high'
    else:
        # 미등록 재료: category_mean fallback (FR-06 폐기)
        category = None
        confidence = 'low'

    # 2. 스케일링 파라미터 조회
    if category:
        params = lookup_scaling_params(lookup_df, category, group_type)
        if params is not None:
            return params, 'high'

    # 3. category_mean fallback (FR-06/FR-07 모두 폐기)
    return ScalingParams(power_law_a=1.0, power_law_b=0.6163), 'low'
```

| 단계 | 조건 | 처리 | confidence |
|------|------|------|------------|
| 1. 재료 카테고리 확정 | ingredient_id가 DB에 있음 | DB 직접 조회 | `high` |
| | ingredient_id가 DB에 없음 | category_mean fallback (FR-06 폐기) | `low` |
| 2. 스케일링 파라미터 조회 | 조합이 룩업 테이블에 있음 | 해당 a, b 사용 | `high` |
| | 조합이 없음 | category_mean fallback, b=0.6163 (FR-07 폐기) | `low` |

### 5.2 3종 모델 비교 계획 (H1, H2 가설 검정)

| 비교 | 가설 | 판단 기준 | 검정 방법 |
|------|------|-----------|----------|
| 선형 vs Rule-based | H1: 멱함수가 선형보다 우월하다 | p < 0.05, MAPE 개선 ≥ 20% | paired t-test (ratio 기준) |
| Rule-based vs 하이브리드 | H2: ML fallback이 유의미하게 기여한다 | p < 0.05 | paired t-test (ratio 기준) |

> **H2 결론**: FR-06/FR-07 폐기로 하이브리드 구성 불가. H2는 "Rule-based 단독이 최종 모델"로 결론. 논문에 ML 보조 모듈 시도 및 폐기 사유를 한계 절에 기술.

---

## 6. 구현 일정 — 최종 완료 현황

| 작업                                                            | 담당  | 상태        | 산출물                                                                                               |
| ------------------------------------------------------------- | --- | --------- | ------------------------------------------------------------------------------------------------- |
| Feature Engineering 설계서 작성                                    | 남유찬 | ✅ 완료      | `docs/ML-FE-001.md`                                                                               |
| DB 환경 구성 (load_recipe_data.py 버그 수정, scaling_coefficient 재적재) | 남유찬 | ✅ 완료      | `scripts/load_recipe_data.py`, `docker/init/02_seed_scaling_coefficients.sql`                     |
| FR-07 RF/XGBoost 구현 및 5-Fold GroupKFold CV 평가                 | 남유찬 | ✅ 완료 (폐기) | `data/ml/rf_cv_results.json`, `data/ml/rf_performance_report.txt`                                 |
| FR-06 TF-IDF + 로지스틱 회귀 구현 및 평가                                | 남유찬 | ✅ 완료 (폐기) | `data/ml/classifier.pkl`, `data/ml/classification_report.txt`, `data/ml/sensitivity_analysis.csv` |
| FR-06/FR-07 폐기 확정, 설계서 최종 업데이트                                | 남유찬 | ✅ 완료      | `docs/ML-FE-001.md v2.1`                                                                          |

> **최종 결론**: FR-06, FR-07 모두 폐기. Rule-based 단독 채택.

---

## 7. 참조 문서 ✏️ v1.1 수정

| 문서명                                            | 용도                                                  |
| ---------------------------------------------- | --------------------------------------------------- |
| `ADR-001_멱함수_독립변수_정의_20260222.docx`            | `ratio = Y/(base×N) = a×N^(b-1)` 수식 정의              |
| `ADR-002_통계전략_대분류병합_v6_20260320.docx`          | group_type 3대분류, MixedLM 주전략, SE(b) 판단 기준           |
| `요구사항정의서_기능명세서_v1_4_20260314.md`               | FR-06, FR-07, FR-08 상세 요구사항                         |
| `비선형_하이브리드_스케일링_모델_프로젝트_로드맵_v5_6_20260314.md`  | 4월 Week 4-5 일정 및 산출물 정의                             |
| `작업_인수인계서_남유찬.md`                              | GitHub 이관 및 Docker 환경 구축 내역, DB 적재 현황               |
| `docker/init/02_seed_scaling_coefficients.sql` | scaling_coefficient 테이블 자동 적재 데이터                   |
| `scripts/load_recipe_data.py`                  | 레시피/재료/ml_training_dataset 수동 적재 스크립트               |
| `scripts/prepare_ml_training_data.py`          | ML 모듈 1 학습 데이터 추출 스크립트 (DB → train_ingredients.csv) |
