# Scoring Function 정의서 — 다양성 · 제철 · 나트륨당 저감

| 항목 | 내용 |
|---|---|
| 담당 | nyc (남유찬) |
| 대상 코드 | `module_3/src/soft_constraints_diversity.py` |
| 테스트 | `module_3/tests/test_soft_constraints_diversity.py` (15케이스 통과) |
| 데모 | `module_3/demo_diversity.py` (pmy 파일 미참조 · 독립 end-to-end 실행) |
| 규약 출처 | ksm `soft_constraints.py` 와 동일 · FR-11 · ADR-002 Scoring |
| 상태 | **담당 부분 완료.** 코드·테스트·독립 실행 검증 완료. pmy 훅 통합은 통합 담당자 몫(§8) |

---

## 1. 범위와 위치

CSP Solver(OR-Tools CP-SAT)의 **목적함수(Soft) 층** 중 다음을 담당한다. Hard 제약(feasible region)이 아니라, 실행 가능한 식단들 사이에서 **더 좋은 식단을 고르는 점수**다.

담당 항목: (1) 다양성 — 조리법 3종↑ · 주재료/색/맛 중복 회피, (2) 제철, (3) 나트륨·당 저감.
범위 밖(ksm): 제공빈도 · 기호도 · 식단가. 메뉴 중복 회피는 별개 항목.

진입점은 팀장과 동형의 **단일 함수** `add_diversity_soft_objective(model, x, menus, *, days, n_meals, weights)` 이며, `DiversitySoftObjective.score`(정수-선형식)를 돌려준다.

## 2. 설계 규약 (ksm 모듈과 동일 — 합산 호환의 전제)

1. **"클수록 좋음" 부호 통일** — 모든 항을 좋을수록 큰 값으로 정규화. 통합자는 최대화만 한다.
2. **정수-선형 강제** — CP-SAT 목적함수 제약. `season_score`(0~1 float), 나트륨(mg)·당(g)은 정수 계수로 스케일.
3. **우아한 저하** — 데이터가 없으면 infeasible 시키지 않고 해당 항을 **중립(0)으로 자동 비활성**. 데이터 적재 시 코드 변경 없이 켜진다.
4. **순수 함수 + model 주입** — DB·네트워크 없이 mock 으로 단위테스트.

## 3. 총점 수식

```
score =  + w_season · Σ season_coef(m)·x
         − w_cook   · Σ_d cook_shortfall(d)
         − w_color  · Σ_d color_shortfall(d)
         − w_main   · Σ_v main_excess(v)
         − w_taste  · Σ_v taste_excess(v)
         − w_na     · Σ sodium_coef(m)·x
         − w_sugar  · Σ sugar_coef(m)·x
         − w_commercial · Σ_{m∈완제품} x
```

`x = x[m,d,s]` 는 pmy 골조의 결정변수(메뉴 m 을 d일 s끼니에 편성). 통합자는
`model.Maximize(ksm.score + diversity.score)` 로 합산한다.

## 4. 항목별 정의

### 4.1 조리법 다양성 (w_cook=8, 목표 하루 3종↑)
- 각 메뉴를 조리법 유형으로 분류(`classify_cooking_methods`). **메뉴명 키워드 우회**(튀김/무침/끓이기/조림/찜/구이/부침/볶음, DB method_name 어휘에 정렬)로 재료 데이터 없이도 동작하며, `load_cooking_methods`(recipe.primary_method_id) 주입값이 있으면 그걸 우선 사용.
- 일별로 사용된 조리법 종수 `distinct(d)` 를 지시변수 OR(`AddMaxEquality`)로 계산.
- 부족량 `shortfall(d) = max(0, 3 − distinct(d))` 를 IntVar 로 두고 감점.

### 4.2 색감 중복 회피 (w_color=6, 목표 하루 3색↑)
- 조리법과 동일 패턴으로 일별 색 종수 부족량 감점. FR-11 "한 끼 3색↑"의 일 단위 근사.
- **현재 비활성**: `ingredient.color_category` 전량 NULL → 색 데이터 0. 데이터 적재 시 자동 활성.

### 4.3 주재료 중복 회피 (w_main=6, cap 주2회)
- `main_ingredient` 값별 지평 내 등장 횟수의 cap 초과분(`main_excess`) 감점. cap 은 주당→지평 스케일(`scale_targets`).
- **현재 비활성**: `recipe_ingredient_map`(role='주재료') 0건 → 주재료 미상.

### 4.4 맛 중복 회피 (w_taste=4, cap 주3회)
- 주재료와 동일 패턴(`taste` 값별 초과분 감점).
- **영구 비활성(현 스키마)**: 맛 속성 컬럼이 스키마에 **없음**. §5 참조.

### 4.5 제철 (w_season=3)
- `season_reward = Σ round(season_score × 100)·x`. 제철점수 높은 식단 가점.
- **현재 사실상 0**: `season_score` 는 로더가 메뉴→재료→`seasonal_ingredient` 조인으로 계산하는데 `recipe_ingredient_map` 0건이라 0으로 회수됨. `seasonal_ingredient` 자체는 2,988건 적재됨.
- **대안(권장)**: 메뉴명↔제철재료명 직접 매칭으로 season_score 를 로더에서 채우면(제철분석 스크립트와 동일 전략) map 없이도 활성 가능.

### 4.6 완제품 자제 (w_commercial=5)
- 완제품/가공식품으로 판별된 메뉴가 편성될 때마다 감점(`− w_commercial · Σ x`).
- 판별(`classify_commercial`): (1) `commercial_menu_ids` 주입(ADR-004 `ingredient_type='COMMERCIAL'`, DB 헬퍼 `load_commercial_menu_ids`) 우선 > (2) 메뉴명 키워드(햄·소시지·어묵·만두·너겟 등, ksm 가공식품 목록 정렬).
- **지금 작동**: 키워드 폴백으로 메뉴명 기반 판별이 되어 recipe_ingredient_map 없이도 활성. DB 신호가 채워지면 자동으로 정밀도 향상.
- ※ ksm '제공빈도 — 가공식품 주2회↓'(빈도 상한)와 **구분되는 항목**: 이건 "완제품 일반 자제"(편성당 감점). 원 제약조건의 `제철·완제품자제·나트륨당저감` 클러스터 소속. 두 항 중복 계상은 통합 시 가중치로 조정(§6).

### 4.7 나트륨·당 저감 (w_na=1, w_sugar=1)
- `sodium_coef = round(sodium/100mg)`, `sugar_coef = round(sugar/2g)`. 총량 감점(낮을수록 가점).
- **즉시 활성 가능**: `nutrition_recipe.sodium/sugar` 직속 컬럼(99% 적재). `load_nutrition_fields` 주입으로 pmy 파일 수정 없이 동작.

## 5. 데이터 의존성 및 활성 상태

| 항 | 데이터 소스 | 현재 활성 | 활성 조건 |
|---|---|---|---|
| 조리법 다양성 | 메뉴명(키워드) / recipe.primary_method_id | **✅ 지금 가능** | 없음(이름 기반 동작) |
| 나트륨·당 저감 | nutrition_recipe.sodium/sugar | ⚠️ 로더 확장 후 | MenuItem+쿼리에 sodium/sugar 추가 |
| 제철 | seasonal_ingredient (2,988) | ⚠️ 로더 대안 필요 | 메뉴명 매칭 또는 map 적재 |
| 색감 중복 | ingredient.color_category | ❌ 대기 | color_category 채움 + map 적재 |
| 주재료 중복 | recipe_ingredient_map(role) | ❌ 대기 | map 적재 |
| 맛 중복 | (없음) | ❌ 영구 | **스키마 신설 필요** |

### 데이터 조달 방식 (ksm 규약과 동일 — pmy 파일 수정 불필요)
ksm `add_soft_objective` 가 그랬듯, **MenuItem 에 이미 있는 값만 직접 읽고, 없는 값은
side-channel 인자 + 자체 DB 헬퍼로 조달**한다. pmy 의 MenuItem/쿼리를 건드리지 않는다.

| 값 | MenuItem 존재? | 조달 방식 |
|---|---|---|
| name·category·colors·season_score | ✅ (pmy 로더가 채움) | 직접 읽음 |
| cooking_method | ✖ | `load_cooking_methods(engine, menus)` → `cooking_methods=` 주입(부분). 미주입 메뉴는 메뉴명 키워드로 폴백 병합 |
| sodium·sugar | ✖ | `load_nutrition_fields(engine, menus)` → `sodium_by_idx=`, `sugar_by_idx=` 주입 |
| main_ingredient | ✖ (현재 **양 경로 모두 커버리지 0**) | 정식: `load_main_ingredients`(recipe_ingredient_map=0행). 우회: `load_main_ingredients_from_training`(ml_training_dataset 주재료 89건은 있으나 참조 recipe 가 전부 nutrition_recipe_id NULL → 메뉴 0건, 2026-07-30 검증). 둘 다 준비돼 있고 데이터 연결 확장 시 자동 활성 |
| 완제품(commercial) | △ (키워드로 지금 작동) | `load_commercial_menu_ids`(ADR-004 ingredient_type='COMMERCIAL', recipe_ingredient_map 적재 후) → `commercial_menu_ids=` 주입. 미주입 시 메뉴명 키워드로 판별 |
| taste | ✖ (스키마 부재) | 주입 없음 → 영구 중립 |

조리법 분류 우선순위(메뉴별): `load_cooking_methods` 주입값 > `menu.cooking_method` 속성 > 메뉴명 키워드. recipe 연결 메뉴(≤844건)는 DB 정답 조리법, 나머지는 이름 키워드로 병합된다.

즉 통합자는 pmy MenuItem 을 확장할 필요 없이, DB 가동 시 위 헬퍼를 호출해 결과를
`add_diversity_soft_objective(..., sodium_by_idx=..., main_by_idx=...)` 로 넘기면 된다.
헬퍼를 생략하면(주입 None) 해당 항은 중립 저하한다.

## 6. 통합 및 팀 조정 사항

- **합산**: `model.Maximize(add_soft_objective(...).score + add_diversity_soft_objective(...).score)`.
- **가중치 정규화(필수 협의)**: ksm 은 `w_freq=10 ≫ w_pref=5 ≫ w_cost=1`. 본 모듈 기본값은 같은 정수 척도 계열이나, **두 모듈 항의 상대 스케일은 팀 캘리브레이션으로 확정**해야 특정 항이 과잉 지배하지 않는다. 특히 나트륨·당은 절대값이 커(수백 mg) 계수 스케일(100mg/2g 단위)로 이미 억제했으나, 실데이터로 재점검 권장.
- **메뉴 중복 회피**: ksm README 가 "미구현 → 데모 반복적"으로 지적한 항목. 주재료/색 중복 회피가 부분적으로 이를 완화하나, 완전한 메뉴-동일성 중복 회피는 별도 term 으로 추가 가능(동일 IntVar-excess 패턴).
- **`ortools` 의존성**: `requirements.txt` 에 없음 → 팀 회의로 추가 필요(ksm 과 동일 이슈).

## 7. 검증

- 단위테스트 15케이스 전부 통과(`pytest`, DB 불필요, mock 메뉴): 분류기·조리법/색 다양성·주재료/맛 중복·제철·나트륨/당·우아한 저하·외부 항 합성·리포팅.
- `evaluate_diversity_breakdown(...)` 로 풀린 해의 항별 지표(일별 조리법/색 종수, 주재료·맛 반복, 총 제철점수·나트륨·당)를 사람이 읽는 형태로 재계산.
- **독립 end-to-end 실행**(`demo_diversity.py`, pmy 파일 미참조): 주식1·국1·반찬2 슬롯 모델에서 목적함수가 OPTIMAL 로 풀림. 기본 가중치는 제철·나트륨이 우세해 반복 식단(팀장 README '데모 반복' 재현), **다양성 강조 가중치로는 조리법 3종 달성·주재료 분산**이 확인됨 → 항과 가중치 노브가 실제로 다양성을 제어함을 실증.
- **DB 헬퍼 실스키마 검증**(2026-07-30, `DB_SCHEMA_VERIFICATION.md`): `load_nutrition_fields`·`load_main_ingredients` 가 참조하는 컬럼·테이블명 9개 전부 실재, `ingredient_role` CHECK 제약에 `'주재료'` 포함(유효 라벨) 확인, 두 헬퍼 SQL 에러 없이 실행. **sodium 99.48%·sugar 98.91% 적재** → 나트륨·당 항은 `load_nutrition_fields` 주입만으로 즉시 실데이터 작동 가능. 주재료 항은 `recipe_ingredient_map`(현 0행) 적재 후 활성.
- **주재료 경로 검증**(2026-07-30): 정식 소스 `recipe_ingredient_map` 0행. 우회 소스 `ml_training_dataset` 에 주재료 89행+ingredient_id 존재하나, 그 recipe 들이 전부 `nutrition_recipe_id` NULL 이라 **메뉴 커버리지 0건**. 두 헬퍼 모두 SQL 정상·우아한 저하로 안전하나, 메뉴↔재료 연결이 확장돼야 활성. → 주재료 항은 현재 중립.
- **조리법 헬퍼 검증**(2026-07-30, `DB_COOKING_METHOD_VERIFICATION.md`): `load_cooking_methods` 컬럼(`recipe.primary_method_id`, `cooking_method.method_id/method_name`) 전부 실재, 샘플 10건 정상 매핑. DB `method_name` 8종(끓이기·볶음·찜·구이·무침·조림·튀김·삶기)에 맞춰 키워드 라벨 어휘를 정렬(`국물`→`끓이기`)해 **이중 계상 방지**. **커버리지 한계**: DB 정답 조리법을 얻는 메뉴는 844건(전체의 약 0.28%)뿐 → 나머지는 메뉴명 키워드 폴백. recipe 연결 확대 시 자동 개선.

## 8. 통합 핸드오프 (통합 담당자 앞 — 본 담당 범위 밖)

본 모듈은 pmy `csp_solver.py` 를 **import·수정하지 않는다.** MenuItem 속성은 전부 `getattr`
로 읽어 **완전 디커플링**되어 있어, 통합자는 아래 3줄만 `build_and_solve` 의
목적함수 구역(★Soft 담당자 구현 구역★)에 넣으면 된다. 우아한 저하 설계라 로더 확장 전에
넣어도 크래시 없이 동작(미적재 항은 중립).

```python
from soft_constraints_diversity import (
    add_diversity_soft_objective, load_nutrition_fields, load_cooking_methods,
    load_main_ingredients, load_main_ingredients_from_training, load_commercial_menu_ids)
from soft_constraints import add_soft_objective          # ksm

# (선택) DB 가동 시 side-channel 조달 — 미가동이면 아래 줄 생략(폴백/중립 저하)
na_by_idx, sg_by_idx = load_nutrition_fields(engine, menus)
cook_by_idx = load_cooking_methods(engine, menus)        # recipe 연결분은 DB 정답, 나머지는 이름 폴백
# 주재료: 정식 소스(recipe_ingredient_map) 우선, 비었으면 학습셋 우회 경로로 대체
main_by_idx = load_main_ingredients(engine, menus) or load_main_ingredients_from_training(engine, menus)
commercial_ids = load_commercial_menu_ids(engine, menus)  # 비면 메뉴명 키워드로 자동 폴백

soft_ksm = add_soft_objective(model, x, menus, days=req.days, n_meals=len(req.meals), ...)
soft_div = add_diversity_soft_objective(
    model, x, menus, days=req.days, n_meals=len(req.meals),
    cooking_methods=cook_by_idx, sodium_by_idx=na_by_idx, sugar_by_idx=sg_by_idx,
    main_by_idx=main_by_idx, commercial_menu_ids=commercial_ids)
model.Maximize(soft_ksm.score + soft_div.score)          # 부호 규약 동일 → 그냥 합산
```

통합자가 챙길 것(본 담당 범위 밖):
1. 위 합산을 pmy 훅에 삽입. **pmy 파일(MenuItem·쿼리)은 수정 불필요** — 데이터는 위 헬퍼로 조달.
2. 두 모듈 가중치의 상대 스케일 최종 캘리브레이션(§6, 데모에서 노브 효과 실증됨).
3. `requirements.txt` 에 `ortools` 추가.
