# EDA 태스크: ratio 구성 및 탐색적 분석
> OptiMeal 모듈 2 — 비선형 스케일링 엔진  
> 작성일: 2026-03-20 | 담당: Claude Code  
> 선행 완료 조건: matched_pairs_20260320.csv 검수 완료 (268쌍, is_manually_verified=True)

---

## 0. 전제 조건 및 컨텍스트

### 핵심 수식 (ADR-001 Option A 확정)
```
ratio = Y / (base × N)

Y    : 대규모 레시피 재료 투입량 (g)
base : 소규모 레시피 1인분 투입량 (g)
N    : 대규모 레시피 serving_size (인원수)

멱함수: ratio = a × N^(b-1)
log 변환: log(ratio) = log(a) + (b-1) × log(N)
```
- b = 1 → ratio ≈ 1 (완전 선형)
- b < 1 → ratio < 1 (대량 시 덜 필요, 양념류 예상)
- b > 1 → ratio > 1 (대량 시 더 필요)

### 재료 카테고리 매핑 (DB role 값 → 분석 카테고리)

**1단계: role 컬럼 기반 분류 (우선 적용)**
```
주재료  → main_ingredient
부재료  → sub_ingredient
양념    → seasoning        ← DB에서 '양념'과 '조미료' 두 값이 혼재함
조미료  → seasoning        ← 동일 카테고리로 통합 처리
```

**2단계: 재료명 기반 파생 분류 (role 분류 후 덮어쓰기 — 사전 확정 기준)**

정규화된 표준명(재료명정규화테이블_v4_1_20260314.xlsx Jaccard검증용_정규화맵 적용 후)을 기준으로 아래 목록과 대조한다. 목록에 해당하면 role 값에 무관하게 해당 카테고리로 재분류한다.

```python
# ── 수분류 확정 목록 (14종) ──────────────────────────────
WATER_BASE_LIST = [
    '물', '쌀뜨물',
    '육수', '닭육수', '멸치육수', '멸치다시마육수', '사골육수', '소고기육수',
    '우유', '두유', '두유(검은콩)', '생크림', '요구르트', '코코넛밀크',
]

# ── 유지류 확정 목록 (18종) ──────────────────────────────
OIL_FAT_LIST = [
    '식용유', '참기름', '들기름',
    '올리브유', '올리브유(엑스트라버진)',
    '버터', '무염버터', '저염버터', '마가린',
    '고추기름', '마늘기름', '허브버터',
    '콩기름', '현미유', '포도씨유', '해바라기유', '땅콩기름', '코코넛오일',
]

# ── suffix 패턴 fallback (목록 미등재 재료용) ────────────
# 표준명이 아래 suffix로 끝나면 해당 카테고리로 분류
WATER_SUFFIX = ['육수', '물', '우유', '크림', '두유', '요구르트']
OIL_SUFFIX   = ['기름', '버터', '마가린']
# '유'는 두유·올리브유 혼재로 단독 suffix 사용 금지 — 목록 매칭 우선
```

**분류 우선순위:**  
`WATER_BASE_LIST/OIL_FAT_LIST 정확 매칭` > `suffix 패턴` > `role 컬럼 값`

> ⚠️ 이 기준은 2026-03-20 EDA 시작 전 사전 확정된 것이다.  
> 분석 중 목록 수정이 필요하다고 판단될 경우 STOP 후 보고할 것.

### 파일 경로
```
소규모 레시피: 소규모_레시피_DB_남유찬_v0_08.xlsx  (시트: 대용량_레시피_입력템플릿)
대규모 레시피: 대규모_레시피_DB_남유찬_v0_08.xlsx  (시트: 대용량_레시피_입력템플릿)
매칭 쌍:       matched_pairs_20260320.csv
```

### 파일 구조 요약
```
공통 컬럼 (0-based 인덱스):
  col 0  : recipe_id
  col 1  : recipe_name
  col 5  : serving_size  ← N (대규모), 소규모는 항상 1
  col 9  : cooking_method_primary  (소규모)
  col 9  : cooking_method_group_type  (대규모, 건열/습열/비가열)
  col 10 : cooking_method_primary  (대규모, 볶기/끓이기/무치기 등)

재료 블록 (ingredient 1~28):
  ingredient_N_name   : col 15 + (N-1)*4
  ingredient_N_amount : col 16 + (N-1)*4
  ingredient_N_unit   : col 17 + (N-1)*4
  ingredient_N_role   : col 18 + (N-1)*4  (소규모)
                        col 19 + (N-1)*4  (대규모, col offset 1 차이)

주의: 헤더는 행 1(0-based), 한글 설명은 행 2, 실제 데이터는 행 3부터.
pandas로 읽을 때 header=1, skiprows=[2] 또는 header=2 직접 지정 확인 필요.
```

---

## 1. 태스크 목록

### TASK-01: 데이터 로딩 및 wide→long 변환
**목적:** 재료 단위 분석을 위해 wide 포맷(재료1~28이 열로 펼쳐진 구조)을 long 포맷으로 변환한다.

**처리 절차:**
1. 소규모/대규모 레시피 xlsx 각각 로드 (실제 데이터 시작 행 주의)
2. matched_pairs CSV와 조인: `small_recipe_id`, `large_recipe_id` 기준
3. ingredient_1~28 블록을 long 포맷으로 변환
   - 결과 컬럼: `recipe_id`, `recipe_type(소규모/대규모)`, `ingredient_name`, `amount`, `unit`, `role`
4. amount가 NULL인 행 제거 (DISCRETIONARY/UNKNOWN 재료 — ADR-004)
5. unit이 'g' 또는 'ml'이 아닌 행 별도 집계하여 보고 (변환 가능 여부 탐색)
6. role 값 정규화: '양념' + '조미료' → 'seasoning', '주재료' → 'main', '부재료' → 'sub'

**출력:** `data/long_ingredients.parquet` (또는 CSV)

---

### TASK-02: ratio 계산
**목적:** 매칭 쌍별·재료별 ratio 산출

**처리 절차:**
1. TASK-01 결과에서 쌍별로 재료명 정규화 매핑
   - 정규화 기준: `재료명정규화테이블_v4_1_20260314.xlsx` 사용
   - 정규화 후 동일 재료명으로 소규모-대규모 재료 매칭
2. 매칭된 재료 쌍에 대해:
   ```python
   N = 대규모_serving_size  # 인원수
   base = 소규모_amount     # 1인분 투입량 (g)
   Y = 대규모_amount        # 대규모 총 투입량 (g)
   ratio = Y / (base * N)
   log_ratio = log(ratio)
   log_N = log(N)
   ```
3. 결과 컬럼: `pair_id`, `group_type`, `cooking_method`, `ingredient_name`, `role`, `N`, `base`, `Y`, `ratio`, `log_ratio`, `log_N`

**주의사항:**
- base = 0 또는 Y = 0인 경우 제외하고 별도 집계
- unit 불일치 쌍(한쪽 g, 한쪽 ml) 처리 방침: 제외 후 보고
- 소규모 serving_size ≠ 1인 레시피 발견 시 base = amount / serving_size 로 정규화

**출력:** `data/ratio_table.parquet`

---

### TASK-03: 이상치 탐지 및 필터링 기준 정의
**목적:** EDA 단계에서 ratio 이상치 기준을 사전 정의 (ADR-001 준수사항)

**처리 절차:**
1. ratio 분포 확인: 전체 / group_type별 / role별
2. 이상치 탐지 방법 두 가지 모두 실행:
   - IQR 방법: Q1 - 1.5*IQR ~ Q3 + 1.5*IQR
   - 도메인 기준: ratio < 0.2 또는 ratio > 3.0 (물리적으로 불가능한 범위)
3. 각 방법별 제거 건수 비교 테이블 출력
4. **최종 채택 기준을 코드 주석에 명시적으로 기재** (p-hacking 방지 — 데이터 확인 후 변경 금지)
5. 필터링 전후 데이터셋 크기 비교

**출력:**
- `reports/outlier_report.md` — 이상치 기준 및 제거 건수 테이블
- `data/ratio_table_clean.parquet` — 이상치 제거 후 데이터

---

### TASK-04: 기술통계 및 분포 시각화
**목적:** group_type × role 셀별 분포 파악 및 ADR-002 v2 SEPARATE/MERGE 결정

**처리 절차:**

#### 4-1. 셀별 관측 수 집계 (SEPARATE/MERGE 결정용)
```
집계 단위: cooking_method(세분류) × role(카테고리)
기준: 셀당 n ≥ 10 → SEPARATE, n < 10 → MERGE(3대분류 유지)
결과를 reports/cell_count_table.md에 기록 후 ADR-002 v2 §4.2 결정 기재
```

#### 4-2. ratio 기술통계
- group_type(건열/습열/비가열) × role(main/sub/seasoning)별:
  - mean, median, std, min, max, Q1, Q3, IQR, n

#### 4-3. 시각화 (matplotlib/seaborn, 한글 폰트 설정 필수)
- **Figure 1:** group_type × role 조합별 ratio 박스플롯 (3×3 grid)
- **Figure 2:** log(ratio) ~ log(N) 산점도, group_type별 색상 구분
- **Figure 3:** ratio 히스토그램, role별 서브플롯 (선형성 기준선 ratio=1 표시)
- **Figure 4:** 조리방법 세분류별 ratio 분포 (violin plot)

**출력:**
- `reports/figures/fig1_boxplot_group_role.png`
- `reports/figures/fig2_scatter_logN_logratio.png`
- `reports/figures/fig3_histogram_by_role.png`
- `reports/figures/fig4_violin_cooking_method.png`
- `reports/descriptive_stats.csv`

---

### TASK-05: 비가열 그룹 Power Analysis
**목적:** ADR-002 v5 준수사항 15항 — n=68(현재 비가열 쌍 수) 검정력 산출

**처리 절차:**
1. `pingouin` 또는 `statsmodels` 사용
2. 조건:
   - α = 0.05 (단측/양측 명시)
   - 효과크기 f² = 0.15 (소), 0.25 (중) 두 케이스
   - 비가열 실제 n (TASK-03 필터링 후 재료 관측 수 기준)
3. 달성 가능 검정력 1-β 산출
4. p < 0.05 달성을 위한 최소 효과크기 역산

**출력:** `reports/power_analysis.md`

---

### TASK-06: OLS 기준선 회귀 (MixedLM AIC 비교 준비)
**목적:** ADR-002 v4 준수사항 6항 — OLS 단순회귀 먼저 실행하여 기준선 확보

**모델:**
```python
# OLS: log(ratio) ~ log(N) + C(role)
import statsmodels.formula.api as smf
ols_model = smf.ols('log_ratio ~ log_N + C(role)', data=df_clean).fit()
```

**처리 절차:**
1. 전체 데이터 / group_type별 OLS 각각 실행
2. 회귀계수, AIC, BIC, R² 추출
3. residual plot으로 랜덤효과 필요성 시각적 확인 (group_type별 잔차 패턴)
4. 결과를 테이블로 정리하여 MixedLM과 비교 준비

**출력:**
- `reports/ols_baseline.md` — 계수 테이블 + AIC/BIC
- `reports/figures/fig5_ols_residuals.png`

---

## 2. 출력 디렉토리 구조

```
eda/
├── data/
│   ├── long_ingredients.parquet
│   ├── ratio_table.parquet
│   └── ratio_table_clean.parquet
├── reports/
│   ├── outlier_report.md
│   ├── cell_count_table.md        ← SEPARATE/MERGE 결정 포함
│   ├── descriptive_stats.csv
│   ├── power_analysis.md
│   ├── ols_baseline.md
│   └── figures/
│       ├── fig1_boxplot_group_role.png
│       ├── fig2_scatter_logN_logratio.png
│       ├── fig3_histogram_by_role.png
│       ├── fig4_violin_cooking_method.png
│       └── fig5_ols_residuals.png
└── eda_main.py                    ← 전체 파이프라인 실행 스크립트
```

---

## 3. 코드 작성 규칙

```python
# 모든 주석은 한국어로 작성
# 예시:
def calculate_ratio(Y, base, N):
    """
    ratio = Y / (base * N) 계산 (ADR-001 Option A)
    
    Args:
        Y: 대규모 레시피 재료 투입량 (g)
        base: 소규모 레시피 1인분 투입량 (g)
        N: 대규모 레시피 인원수
    Returns:
        ratio: 스케일링 비율 (b=1이면 ratio≈1)
    """
```

- Python 3.12, pandas 2.x, numpy 1.x, statsmodels, matplotlib, seaborn
- 한글 폰트: `matplotlib.rcParams['font.family'] = 'NanumGothic'` 또는 시스템 사용 가능 폰트 자동 감지
- 파일 경로 하드코딩 금지 — 스크립트 상단 `CONFIG` 딕셔너리로 관리
- 각 TASK는 함수 단위로 분리, `eda_main.py`에서 순서대로 호출

---

## 4. 완료 기준 체크리스트

- [ ] TASK-01: long 포맷 변환 완료, unit 이슈 보고서 포함
- [ ] TASK-02: ratio_table 생성, base=0/Y=0 제외 건수 명시
- [ ] TASK-03: 이상치 기준 코드 주석에 명시, 필터링 전후 n 비교 테이블
- [ ] TASK-04: 셀별 n 집계 테이블 생성 (SEPARATE/MERGE 결정 가능한 형태)
- [ ] TASK-04: 시각화 5종 저장
- [ ] TASK-05: Power Analysis 결과 (비가열 n 기준 검정력 수치 포함)
- [ ] TASK-06: OLS AIC/BIC 테이블 생성
- [ ] 전체 파이프라인 `eda_main.py` 에러 없이 실행 완료

---

## 5. 주의 / 제약 사항

1. **이상치 기준 변경 금지:** TASK-03에서 확정한 기준은 이후 분석에서 변경하지 않는다. 기준 변경이 필요하다고 판단될 경우 STOP 후 보고.

2. **SEPARATE/MERGE 결정 방식:** TASK-04 셀별 n 집계 결과를 토대로 `cell_count_table.md`에 기계적으로 적용 (n ≥ 10 → SEPARATE). 데이터를 보고 주관적으로 조정하지 않는다.

3. **수분류/유지류 파생 분류:** 현재 DB에 명시적 컬럼이 없다. 재료명 기반 파생이 가능한지 탐색만 하고, 분류 기준을 임의로 정의하지 않는다. 탐색 결과를 보고서에 포함하고 결정은 사람에게 위임한다.

4. **MixedLM은 이 태스크의 범위 외다.** OLS까지만 실행하고 종료.
