# scripts/

OptiMeal 프로젝트 유틸리티 스크립트 모음.

---

## DB 세팅 순서 (신규 서버 · 식단 생성 후보까지)

`docker compose up -d` 로 `docker/init/*.sql` 이 적용된 빈 DB 에서 아래 순서로 실행한다.
모든 단계는 멱등이라 중간에 끊겨도 처음부터 다시 돌리면 된다. `migrations/*.sql` 은 init 이후에 추가된
스키마 변경이므로 빈 DB 라도 번호 순으로 한 번 적용한다.

```bash
# 0) 스키마 보강 (v2 → v3 → v4 → v5 → v6)
#    psql -f 로 파일을 직접 읽힌다(PowerShell 파이프는 한글 주석이 깨지며 문장이 빠진 적이 있다 — 루트 README 배포 체크리스트)
for f in migrations/v2_*.sql migrations/v3_*.sql migrations/v4_*.sql migrations/v5_*.sql migrations/v6_*.sql; do
  docker cp "$f" optimeal_db:/tmp/ && docker exec optimeal_db psql -U optimeal -d optimeal -f "/tmp/$(basename "$f")"; done

# 1) 레시피 본문 (data/raw/ xlsx 필요 — 구글드라이브)
docker exec optimeal_app python scripts/load_recipe_data.py
# 2) 식단 후보(영양) — 식품안전나라 COOKRCP01. .env 의 FOODSAFETY_RECIPE_KEY 필요
docker exec optimeal_app python scripts/load_recipe_foodsafety.py
# 3) 같은 원본의 로컬 xlsx 적재 — API 키가 없을 때의 대체 경로이자 2) 누락분 보강.
#    2)와 순서가 바뀌어도 이름 기준으로 서로 건너뛰어 중복 행이 생기지 않는다.
docker exec optimeal_app python scripts/load_nutrition_from_recipe_db.py
# 3b) 3)으로 적재한 행에 xlsx 조리 과정(cooking_step_1~11)을 original_data.MANUALnn 으로 붙인다
#     (레시피 화면의 조리 순서). 3) 다음에 실행. 2) API 적재 행은 원래 MANUAL 이 있어 건드리지 않는다.
docker exec optimeal_app python scripts/load_cooking_steps_from_recipe_db.py
# 4) 재료 맵 → 5) 메뉴↔재료보유 레시피 연결(원가 계산 경로)
#    1인분 양 = 원문 양 ÷ 레시피 인분. 2026-10-01 이전에 적재한 DB 는 2·4인분 레시피(44종)가 인분으로 나뉘지 않은 채
#    들어가 있다 → 한 번 `--reload-multi-serving` 으로 그 레시피들만 지우고 다시 적재한다.
docker exec optimeal_app python scripts/load_ingredients_from_recipe_db.py            # 기존 DB: --reload-multi-serving
docker exec optimeal_app python scripts/connect_menus_to_recipes.py --apply
# 5b) 그래도 재료 맵이 없는 후보 메뉴(같은 이름 xlsx 레시피가 없는 것)를 식품안전나라 원본 재료 문자열
#     (RCP_PARTS_DTLS)로 채운다. g 로 적힌 양만 쓰고 '적당량'·단위 없는 숫자는 양을 비운다(추정 없음).
#     원본에도 재료가 없는 메뉴는 그대로 남는다(2026-10-01 기준 4개). 미리보기 후 --apply.
docker exec optimeal_app python scripts/load_ingredients_from_rcp_parts.py
docker exec optimeal_app python scripts/load_ingredients_from_rcp_parts.py --apply
# 5c) 1인분 재료량 이상치 목록(DB 수정 없음) — 원본 값이면 목록만, 로더 버그면 로더를 고친다
docker exec optimeal_app python scripts/check_recipe_serving_outliers.py
# 6) 점검 — 카테고리별 후보가 다중일 식단에 충분한지(부족하면 exit 1)
docker exec optimeal_app python scripts/check_menu_candidates.py
# 7) 원가 — 가락시장(청과·수산 도매, GARAK_ID/PASSWD) → KAMIS(도매 우선, 축산은 소매, KAMIS_CERT_ID/KEY).
#    둘 다 preview 로 매칭 CSV(data/external/)를 검수한 뒤 load. 가락 → KAMIS 순서로 돌린다
#    (KAMIS 로더가 "가락 가격이 있는 재료엔 소매를 넣지 않음" 규칙을 가락 행 기준으로 판단한다).
docker exec optimeal_app python scripts/load_ingredient_price.py --mode preview
docker exec optimeal_app python scripts/load_ingredient_price.py --mode load
docker exec optimeal_app python scripts/load_ingredient_price_kamis.py --mode preview
docker exec optimeal_app python scripts/load_ingredient_price_kamis.py --mode load
# 7a) 참가격(한국소비자원 생필품 가격, 공공데이터포털 15083256 월 CSV → data/raw/한국소비자원_생필품가격_YYYYMM.csv)
#     data/raw/ 에 파일이 없으면 로더가 최신 월 CSV 를 받아 저장한 뒤 쓴다(있으면 그대로 사용, --csv 로 지정 가능).
#     받기에 실패하면 수동으로 받는 방법을 출력하고 멈춘다. 원본 CSV 는 .gitignore 대상.
#     가공식품·양념(두부·식용유·설탕·밀가루·장류 등). 가락·KAMIS 가격이 있는 재료는 건드리지 않으므로 7) 다음에.
docker exec optimeal_app python scripts/load_ingredient_price_chamgagyeok.py --mode preview
docker exec optimeal_app python scripts/load_ingredient_price_chamgagyeok.py --mode load
# 7b) 수기 참조 가격(data/manual/manual_price_reference_YYYYMMDD.csv, 저장소에 커밋) — 시세·참가격에 없는
#     생크림·올리브유·전분·빵가루·곤약 등. 다른 출처 가격이 있는 재료는 건드리지 않으므로 7)·7a) 다음에.
docker exec optimeal_app python scripts/load_ingredient_price_manual.py            # 대상 확인(DB 미반영)
docker exec optimeal_app python scripts/load_ingredient_price_manual.py --apply
# 7c) 규칙 파생 원가(밥류÷2.3, 부위=상위품목, 동의어 대체, 물·육수 정책 0원). 맨 마지막에,
#     가격이 갱신될 때마다 다시 실행(파생 행은 매번 지우고 현재 원천 가격으로 다시 만든다).
docker exec optimeal_app python scripts/derive_ingredient_price.py --apply
# 8) 알레르기 — 표시 대상 19종 → 재료 매핑(data/manual/allergen_ingredient_map.csv, 저장소에 커밋)을
#    constraints(constraint_type='알레르기', group_id NULL, description=19종 이름)에 적재. 없으면 대체식이 일반식과 같다.
#    CSV 의 id·이름이 DB 와 하나라도 다르면 아무것도 쓰지 않고 멈춘다. 재료 마스터가 바뀌면 CSV 를 고치고 다시 실행(멱등).
docker exec optimeal_app python scripts/load_allergen_constraints.py            # DB 대조·요약(미반영)
docker exec optimeal_app python scripts/load_allergen_constraints.py --apply
# 9) 기저질환 — 재료 → 100g당 열량·당류·인·칼륨(data/manual/ingredient_nutrient_map.csv, 식약처 식품영양성분DB 값을
#    저장소에 커밋)을 ingredient_nutrient 에 적재(migrations/v6). 후보 메뉴에 당류·칼륨·인이 없어 재료로 계산한다.
#    없어도 고혈압(나트륨)·당뇨(탄수화물 비율)는 공식 값으로 동작하고, 응답 ingredient_nutrients_loaded=false 로 드러난다.
docker exec optimeal_app python scripts/load_ingredient_nutrient.py            # DB 대조·요약(미반영)
docker exec optimeal_app python scripts/load_ingredient_nutrient.py --apply
```

알레르기 매핑 요점(`allergen_ingredient_map.csv`):
- 화면 선택값·메뉴 `allergens` 는 19종 이름(난류·우유·메밀·땅콩·대두·밀·고등어·게·새우·돼지고기·복숭아·토마토·아황산류·
  호두·닭고기·쇠고기·오징어·조개류·잣). 구버전 '갑각류'·'계란' 등은 서버가 `alternative_menu.normalize_allergens` 로 바꾼다.
- `basis`: `직접`(재료 자체) / `가공품`(원재료로 들어감 — 간장·된장·고추장→대두·밀, 마요네즈→난류·대두, 햄→돼지고기 등) /
  `보수적`(종류 불명이라 위험 쪽으로 포함 — 견과류·젓갈·해물육수·안심·찜갈비, 김치류→새우젓).
- 카테고리 코드(조개류·난류 등)는 묶인 재료를 **전부** 넣는다. 제철 분석의 "카테고리 코드 제외" 기준과 반대다(여기는 누락=사고).
- 매핑하지 않은 것: 총칭 `육수`·`채소국물`(재료 불명), 피시소스·우스터소스(19종 해당 원료가 표준 레시피에 없음).
- 교체 대상은 그룹이 고른 알레르겐만으로 판정하고, 교차반응(`CROSS_REACTIVE_GROUPS`)은 대체 후보를 거를 때만 넓힌다.

⚠ 2)는 2026-09 이전 버전에서 조리법 `RCP_WAY2='기타'`(김치·무침·샐러드 등 비가열 310건)를 통째로
skip 해 김치 후보가 1종만 남았다 → 3일 반복 금지 제약과 충돌해 2일 이상 식단이 항상 INFEASIBLE.
지금은 '기타'를 `cooking_method` '무침'(no_heat)으로 적재한다. 6)의 김치가 9종 미만이면 이 문제다.

가격(`ingredient_price`)은 7)의 API 키가 필요한 별도 적재이며, 없으면 원가가 0원으로 계산된다.
**순서: 가락 → KAMIS → 참가격 → 수기 → derive.** 뒤 단계는 앞 단계 가격이 있는 재료를 건드리지 않는다.
가락은 청과·수산뿐이라 축산(소·돼지·닭·계란·우유)·곡류(쌀·찹쌀·현미·콩)는 KAMIS 로만 채워진다.
**가격 선택 = 출처 우선순위(2026-09-30 고정).** 재료에 여러 출처 가격이 있으면 아래 표의 **순위**가 앞선 출처를 쓰고,
같은 출처 안에서만 최신 `price_date` 를 쓴다(예전의 "출처 무관 최신 날짜"는 로더 실행 순서에 따라 원가가 바뀌었다).
테이블 유일키도 `(ingredient_id, price_date, source)`(migrations/v5)라 같은 날 여러 출처 행이 함께 남고, 로더는 자기
출처 행만 갱신한다 → 로더를 어떤 순서로 돌려도 고르는 가격이 같다. 규칙은 두 곳에 같은 목록으로 있다 —
식단 원가 `module_3/src/csp_solver.py` `PRICE_SOURCE_PRIORITY`(`_MENU_QUERY`), 파생 부모 가격 `scripts/price_priority.py`.
둘이 같은지는 `module_4/backend/tests/test_price_priority.py` 가 확인한다. 교체 후보·레시피·원가 화면은 모두 module_3 원가를 쓴다.

`ingredient_price.source` 값(순위 순):

| 순위 | source | 적재 | 의미 |
|---|---|---|---|
| 1 | `KAMIS_W` | `load_ingredient_price_kamis.py` | KAMIS 도매(02) 당일가 |
| 2 | `서울시농수산식품공사` | `load_ingredient_price.py` | 가락시장 도매(청과·수산), '상' 등급 30일 평균 |
| 3 | `KAMIS_R` | `load_ingredient_price_kamis.py` | KAMIS 소매(01) 당일가 — 도매가 없는 재료만(축산 전부 포함) |
| 4 | `참가격_R` | `load_ingredient_price_chamgagyeok.py` | 한국소비자원 참가격 소매가(월) — 가공식품·양념·음료 |
| 5 | `수기_참조` | `load_ingredient_price_manual.py` | 사람이 조사한 참조 가격표(상품·URL·조사일 기록) — 재료별 g당 중앙값 |
| 6 | `쌀 환산` | `derive_ingredient_price.py` | 밥·현미밥·귀리밥 = 원곡 g당 가격 ÷ 2.3 |
| 6 | `상위품목 대체` | 〃 | `닭고기(가슴)` 등 부위 재료 = 상위품목(`닭고기`·`돼지고기`·`소고기`) 가격 |
| 6 | `동의어 대체` | 〃 | 이름만 다른 재료 = 원 재료 가격(배춧잎←배추, 미니 단호박←단호박, 홍시←감, 달걀(흰자)←달걀, 묵은지·백김치←김치, 맛간장←간장) |
| 7 | `정책 0원` | 〃 | 팀 정책: 물·쌀뜨물·발효종·조리장 육수류 = 0원(해당 재료 전용) |

파생(6)의 부모 가격도 같은 우선순위로 고른다(예: 밥 = 우선순위로 고른 쌀 가격 ÷ 2.3).

KAMIS 로더 요점(`load_ingredient_price_kamis.py`):
- `dailyPriceByCategoryList` 를 도매(02)·소매(01) × 6개 부류로 호출하고, 부류별로 당일 가격이 있는 직전 영업일까지
  폴백한다(당일 오후엔 축산만 먼저 올라와 있기도 함).
- **도매 우선**: 재료에 도매 행이 하나라도 매칭되면 도매만 쓴다. 축산물(500)은 API 에 도매 표가 없어
  (02 로 불러도 소매와 같은 값) 소매만 조회한다(`WHOLESALE_UNAVAILABLE`).
- **가락 보존**: 가락 가격이 있는 재료에는 소매(`KAMIS_R`)를 넣지 않고, 걸리는 기존 소매 행은 load 때 지운다.
  KAMIS 도매는 가락을 덮을 수 있다(최신 조사일 우선).
- 도매에는 같은 품목에 수입·중국산 품종이 섞여 온다 → 국산 품종이 있으면 수입 품종은 버린다(`IMPORT_MARKERS`).
- kg 환산가(`p_convert_kg_yn=Y`)를 쓰고 포기·개·마리 단위는 건너뛰되 계란(특란 60g, **30구 가격만**)·우유(1L=1030g)·
  김(2.5g/장, 1속=250g)만 개당 중량으로 환산한다.
- 축산물은 부위 오매칭을 막으려 `KAMIS_ALIASES` 명시 별칭으로만 매칭(일반명 소고기=양지·설도 1등급, 돼지고기=앞다리).
- 오매칭은 `data/external/kamis_price_match_exclude.csv`(`kamis_name` 컬럼, `품목|품종`)에 적고 load 재실행.
- 재실행 멱등(같은 조사일은 갱신). 1차 적재(2026-09-30)의 구분 없는 `source='KAMIS'` 행은 load 때 정리된다.

참가격 로더 요점(`load_ingredient_price_chamgagyeok.py`):
- 원본 CSV 는 cp949. 용량은 상품명 끝 괄호(`(900ml)`, `(냉동, 100g)`, `(300~500g)`→중간값, `(500ml*20개)`).
  개수 단위(개·입·캔·매)는 g 로 못 바꿔 환산 실패 CSV 로 남긴다. 세일·1+1 행은 정상가가 아니라 뺀다.
- ml → g 는 `DENSITY_G_PER_ML`(식물성 유지 0.92, 간장 1.15, 식초 1.01…), 표에 없으면 1.0 가정 + 로그.
- 매칭: 브랜드를 버린 상품명 뒤토막으로 `ingredient`/`ingredient_synonym` 완전일치 → `KEYWORD_RULES` 로 보충.
  간편식·완제품(`COMPOSITE_EXCLUDE`), 비식품(`NON_FOOD_EXCLUDE` — 생필품 조사라 절반 이상이 비식품),
  특수 변형(`VARIANT_EXCLUDE`, 자일로스 설탕)은 매칭하지 않는다.
- 재료 단가 = 상품별 [판매점별 g당 가격(점포 내 조사일 중앙값)]의 중앙값 → **매칭된 전체 상품**의 중앙값.
  (한때 '용량 상위 절반'을 썼으나 대용량이 프리미엄 제품이면 왜곡돼 폐기 — 두부가 380g 국산콩 기준 13.2원/g.)
- 참가격에 없는 저염간장은 일반 간장(진간장·양조간장) 값으로 근사한다. 두유는 팩 단위(용량 미표기)라 환산 불가,
  전분·빵가루·올리브유·생크림·곤약·튀김가루는 2026-08 원본에 상품이 없다(→ 수기 참조표).
- 음료(`EXCLUSIVE_RULES`): 탄산수·맥주(카스·테라·하이트·에일 500ml)·오렌지주스(델몬트·미닛메이드). 맞으면 동의어
  뒤토막 매칭을 하지 않는다(주스가 과일 '오렌지'로 가지 않게). 묶음(6캔)은 개당 용량 표기가 없어 건너뛴다.
- `120g×5`·`500ml*20개` 처럼 개당 중량이 적힌 개수 단위는 곱해서 환산하고, 중량 없는 개수(`5개입`·`4개`)는 추정하지 않는다.
  그래서 요거트(4개)·라면(5개입)은 없음. 떡볶이·냉면은 소스 포함 키트라 떡·면 단가로 쓰지 않는다.
- 산출물: `data/external/chamgagyeok_price_{match_preview,unmatched,unit_fail}_YYYYMM.csv`.
- 가락·KAMIS 가 있는 재료는 적재하지 않고, 걸리는 기존 `참가격_R` 행은 load 때 지운다. 같은 조사일 재실행은 갱신(멱등).

파생 원가 요점(`derive_ingredient_price.py`) — 값을 새로 만들지 않고 DB 에 있는 가격에 고정 규칙만 적용한다:
- `COOKED_RICE_WEIGHT_RATIO = 2.3`: 백미는 취반 시 무게가 약 2.2~2.4배(단체급식 취반 기준 쌀 1kg → 밥 약 2.3kg).
  현미밥·귀리밥도 같은 배수. 원곡(현미·귀리) 가격이 없으면 보류한다(2026-09-30 기준 귀리밥 보류).
- 동의어 대체는 `ingredient_synonym` 에 넣지 않는다 — 대상 이름들이 이미 각자 별도 ingredient 행이라,
  synonym 을 옮기면 레시피 적재·영양·알레르기의 재료 식별이 바뀐다. 원 재료 가격이 없으면 보류(홍시←감).
- `정책 0원` 은 원가 합계엔 가격 없음과 같지만, 커버리지·미가격 목록에서 빈칸이 아닌 정책값으로 구분된다.
- 상위품목 대체는 부위 단가 차이(가슴살 > 통닭, 채끝 > 양지 등)를 반영하지 않는다. 뼈·꼬리 부위는 정육 단가와
  크게 달라 대체하지 않는다(`EXCLUDE_PART_KEYWORDS`). 수기 참조표로 덮을 자리를 표시하는 용도다.
- 두부 등 가공품은 다루지 않는다(수기 참조표 대상). 순두부·연두부는 두부로 대체하지 않는다(두부보다 g당 가격이
  훨씬 낮아 과대 계상). 실행마다 파생 source 행을 지우고 다시 만든다(멱등).

수기 참조 로더 요점(`load_ingredient_price_manual.py`):
- 행마다 g당 가격 = `price_won ÷ (package_amount × 비중)`. 단위 ml 면 `density_g_per_ml`(비면 1.0 + 경고), g 면 1.
- 재료별 g당 중앙값, 조사일은 `surveyed_on`(최신). `ingredient_id`·`ingredient_name` 이 DB 와 다르면 그 행은 건너뛴다.
- 다른 출처 가격이 있는 재료는 건너뛰고, 다른 출처가 새로 생긴 재료의 옛 `수기_참조` 행은 `--apply` 때 지운다. 멱등.

가락 로더 단위 해석(2026-09-30 점검): 30일 조회의 단위 해석 실패 261건은 전부 개수 단위(`N개` 203·`N속` 58)였다 —
kg·g 표기를 못 읽은 경우는 없음. 개수 단위는 계속 건너뛴다. 품목명이 달라 못 잡던 `생표고`·`생표고 수입`→표고버섯,
`새우수입`→새우는 `GARAK_ALIASES` 로 매칭한다(갯장어·가리비는 장어·관자와 다른 상품이라 넣지 않음).

조리 순서(레시피 화면)는 `nutrition_recipe.original_data` 의 `MANUAL01~20` 에서 읽는다. 2)는 API 원본에
이 키가 있고, 3)으로 들어온 행은 3b)가 xlsx `cooking_step_N` 원문을 같은 키로 옮긴다(`manual_source` 로 출처 표시,
다듬기·보충 없음). 3b)를 빼먹으면 해당 메뉴(약 300개)의 조리 순서가 화면에서 공백으로 나온다.

---

## load_recipe_data.py

`data/raw/` 폴더의 xlsx/csv 파일을 Docker 내 PostgreSQL DB에 일괄 적재합니다.

### 실행 방법

```bash
docker exec optimeal_app python scripts/load_recipe_data.py
```

### 적재 대상 파일 및 테이블

| 파일 (data/raw/) | 테이블 | 비고 |
|---|---|---|
| `소규모_레시피_DB_남유찬_v0_10.xlsx` | `recipe` | `serving_category='소규모'` |
| `대규모_레시피_DB_남유찬_v0_08.xlsx` | `recipe` | `serving_category='대규모'` |
| `소규모-대규모 레시피 매칭 쌍 단일 csv_박소희.csv` | `recipe_similarity` | recipe 적재 후 실행 |
| `df_B.csv` | `ml_training_dataset` | recipe 적재 후 실행 |
| `재료명정규화테이블_최종_20260307_권성민.xlsx` | `ingredient_synonym` | 확정(Y) 행만 적재 |

### 동작 방식

- **파일 없음**: 에러 없이 경고 메시지 출력 후 해당 파일 건너뜀
- **중복 방지**:
  - `recipe`: `notes` 필드의 원본 ID 마커(`[orig:{id}]`)로 중복 판별 → skip
  - `recipe_similarity`: `(small_recipe_id, large_recipe_id)` UNIQUE 제약 → `ON CONFLICT DO NOTHING`
  - `ingredient_synonym`: `(synonym_name)` UNIQUE 제약 → `ON CONFLICT DO NOTHING`
  - `ml_training_dataset`: 테이블이 비어 있을 때만 전체 적재
- **Bulk Insert**: `chunksize=500` 청크 단위로 삽입
- **원본 recipe_id 보존**: xlsx의 문자열 ID(예: `A1034`)를 `recipe.notes` 필드에 `[orig:A1034]` 형태로 저장

### 값 변환 규칙

**조리방법** (xlsx 텍스트 → `cooking_method.method_id` FK)

| xlsx 값 | 매핑 |
|---|---|
| `습열`, `끓이기(삶기)`, `밥짓기` | 끓이기 |
| `건열`, `볶기` | 볶음 |
| `비가열`, `무치기`, `절이기(담그기)`, `샐러드` | 무침 |
| `굽기`, `부침` | 구이 |
| `찌기` | 찜 |
| `졸이기`, `조리기(졸이기)` | 조림 |
| `튀기기` | 튀김 |

**data_source** (xlsx 값 → DB CHECK 제약)

| xlsx 값 | DB 값 |
|---|---|
| `식품안전나라` | `식약처API` |
| `영양사도우미`, `대한영양사협회` | `영양사협회` |
| `레시피코리아` | `커뮤니티` |
| `서적` | `산업체` |

**match_decision** (매칭쌍 CSV → DB CHECK 제약)

| CSV 값 | DB 값 |
|---|---|
| `auto_tag` | `자동태깅` |
| `manual_review` | `수동검토` |

### 사전 조건

1. Docker 컨테이너 실행 중 (`docker ps`로 확인)
2. `data/raw/` 폴더에 파일 존재
3. DB 마이그레이션 완료 (`docker/init/01_schema.sql` 적용 상태)
