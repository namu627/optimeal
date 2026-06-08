# For_finalexam — 모듈 1+2 통합 데모

기말발표용 통합 시연 패키지. **11월 최종 모듈이 아니라 1-5월까지 진행한 작업(데이터 정규화, 매칭, EDA, 통계, 영양사 피드백, NN v1/v2, ADR-007 하이브리드)을 한 줄로 시연하기 위한 묶음**.

---

## 무엇을 시연하는가

```
[입력] 인원수 N (필수)
[입력] small_recipe_id (선택, 지정 없으면 랜덤)
         ↓
모듈 1: 소규모 레시피 DB 엑셀 (1,599개 1인분 메뉴, 메뉴당 평균 8개 재료)
         + df_B.csv group_type/cooking_method override
         (A 시리즈는 엑셀의 cooking_method_group_type이 NaN이라 df_B에서 보강)
         ↓
모듈 2: 운영 엔진 (ADR-007 하이브리드)
  for each 재료:
    카테고리 분류 (role / derived_category 추론, 5분류)
    if (group_type × ingredient_category) ∈ b_feedback 표 (13 셀)
       AND 카테고리 슬롯 사이즈 내:
        → NN_v2 (DeepSets-context, 학습된 셀 추론)
    else if 미커버 셀 OR 슬롯 초과:
        → lookup category_mean fallback
    else:
        → SKIP (role/카테고리 분류 실패 — 매우 드묾)
         ↓
[출력] CLI 요약 + HTML 리포트 (계산 과정·b값·식·결과 g)
```

---

## 실행

```bash
source .venv/bin/activate

# 랜덤 메뉴 × 100인분
python For_finalexam/run_demo.py --n 100

# 특정 레시피 × 200인분
python For_finalexam/run_demo.py --n 200 --recipe-id A1034

# 시드 고정
python For_finalexam/run_demo.py --n 50 --seed 42

# HTML 생략 (CLI만)
python For_finalexam/run_demo.py --n 100 --no-html
```

출력 HTML은 `For_finalexam/output/demo_<recipe_id>_N<n>_<ts>.html`에 저장된다 (브라우저로 직접 열람 가능).

---

## 파일 구성

| 파일 | 역할 |
|---|---|
| `run_demo.py` | CLI 진입점, 인자 처리, HTML 생성 호출 |
| `recipe_loader.py` | df_B.csv → small_recipe_id 단위 RecipeBundle |
| `hybrid_engine.py` | ADR-007 분기 추론 (NN_v2 / lookup) + 메타 반환 |
| `reporter.py` | HTML 리포트 생성 (lang=ko, inline CSS, self-contained) |
| `output/` | 생성된 HTML 저장 디렉터리 |

---

## 시연 케이스 예시

### 케이스 A — 학습 셀 다수 + 슬롯 초과 lookup (오무라이스, 13 재료)

```
$ python For_finalexam/run_demo.py --n 100 --recipe-id A2062
선택: A2062 오무라이스 (건열 / 볶기, 13 재료)

# 부재료 9개 중 base 큰 6개 → NN_v2 (학습된 건열 × 부재료 셀)
# 부재료 슬롯 초과 3개(마늘/생강/참깨) → lookup fallback (학습 셀이지만 슬롯 한계)
# 양념류 3개(설탕/후춧가루/토마토케첩) → NN_v2
# 유지류 1개(식용유) → NN_v2
# → 분기 요약: NN_v2 10건 + lookup 3건 (전부 처리, SKIP 0)
```

### 케이스 B — 미커버 셀 fallback 다수 (나박김치, 비가열, 12 재료)

```
$ python For_finalexam/run_demo.py --n 100 --recipe-id A1034
선택: A1034 나박김치 (비가열 / 절이기, 12 재료)

# 부재료 9개 → (no_heat × 부재료)는 b_feedback 미정의 → lookup fallback b=0.7649
# 양념류 2개(고춧가루/소금) → (no_heat × 양념류) 학습 셀 → NN_v2
# 수분류 1개(물) → (no_heat × 수분류) 미정의 → lookup fallback
# → 분기 요약: NN_v2 2건 + lookup 10건 (전부 처리)
```

### 케이스 C — 랜덤 시연

```
$ python For_finalexam/run_demo.py --n 50 --seed 42
선택: A1897 쇠고기당면국 (습열, 7 재료)
# 부재료 7개 중 6개 → NN_v2 (습열 × 부재료 학습 셀)
# 슬롯 초과 1개(다시마) → lookup fallback
```

---

## 학술적 위상 (발표 시 강조)

- **모듈 1 산물**: df_B.csv의 205개 1인분 레시피는 식약처 raw → 정규화 → 매칭(ADR-002 v5) → 정제 과정의 결정체.
- **모듈 2 운영 엔진**:
  - NN_v2 (DeepSets-context, ~21k params, 노이즈 σ=0.05 학습) → 학습 셀 추론 (5-Fold MAPE 19.65%, lookup 대비 +2.01%p 우위, `docs/for_reports/nn_results_analysis_20260520.md` §9.2)
  - lookup category_mean → 미커버 셀 외삽 (LOCO에서 NN_v2 대비 +25.99%p 우위, §9.3)
  - 두 모드를 ADR-007에서 명시적으로 분기

- **합성 타겟의 정직한 명시**: 학습 타겟은 1-5월 작업(영양사 50건 5회차 피드백)의 적용 결과이며 합성치이다. 20년 전 대규모 실측 데이터(df_B의 `Y` 컬럼)는 현재 기준 영양사 피드백을 반영할 수 없는 구조적 한계가 있어 학습 타겟으로 사용하지 않았다.

---

## 관련 문서

- ADR-007: `docs/decisions_summary_v5_8.md` §ADR-007
- v1/v2 분석: `docs/for_reports/nn_results_analysis_20260520.md`
- 학습 결과: `module_2/src/engine/train_nn_v2_run_20260520.log`
- LOCO 결과: `module_2/src/engine/loco_v2_run_20260520.log`
