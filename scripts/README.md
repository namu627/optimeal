# scripts/

OptiMeal 프로젝트 유틸리티 스크립트 모음.

---

## DB 세팅 순서 (신규 서버 · 식단 생성 후보까지)

`docker compose up -d` 로 `docker/init/*.sql` 이 적용된 빈 DB 에서 아래 순서로 실행한다.
모든 단계는 멱등이라 중간에 끊겨도 처음부터 다시 돌리면 된다. `migrations/*.sql` 은 init 이후에 추가된
스키마 변경이므로 빈 DB 라도 번호 순으로 한 번 적용한다.

```bash
# 0) 스키마 보강 (v2 → v3 → v4)
for f in migrations/v2_*.sql migrations/v3_*.sql migrations/v4_*.sql; do
  docker exec -i optimeal_db psql -U optimeal -d optimeal < "$f"; done

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
docker exec optimeal_app python scripts/load_ingredients_from_recipe_db.py
docker exec optimeal_app python scripts/connect_menus_to_recipes.py --apply
# 6) 점검 — 카테고리별 후보가 다중일 식단에 충분한지(부족하면 exit 1)
docker exec optimeal_app python scripts/check_menu_candidates.py
# 7) 원가 — 가락시장(청과·수산 도매, GARAK_ID/PASSWD) → KAMIS(도매 우선, 축산은 소매, KAMIS_CERT_ID/KEY).
#    둘 다 preview 로 매칭 CSV(data/external/)를 검수한 뒤 load. 가락 → KAMIS 순서로 돌린다
#    (KAMIS 로더가 "가락 가격이 있는 재료엔 소매를 넣지 않음" 규칙을 가락 행 기준으로 판단한다).
docker exec optimeal_app python scripts/load_ingredient_price.py --mode preview
docker exec optimeal_app python scripts/load_ingredient_price.py --mode load
docker exec optimeal_app python scripts/load_ingredient_price_kamis.py --mode preview
docker exec optimeal_app python scripts/load_ingredient_price_kamis.py --mode load
# 7b) 규칙 파생 원가(밥=쌀÷2.3, '닭고기(가슴)' 등 부위=상위품목). 7) 다음에, 가격이 갱신될 때마다 다시 실행.
docker exec optimeal_app python scripts/derive_ingredient_price.py --apply
```

⚠ 2)는 2026-09 이전 버전에서 조리법 `RCP_WAY2='기타'`(김치·무침·샐러드 등 비가열 310건)를 통째로
skip 해 김치 후보가 1종만 남았다 → 3일 반복 금지 제약과 충돌해 2일 이상 식단이 항상 INFEASIBLE.
지금은 '기타'를 `cooking_method` '무침'(no_heat)으로 적재한다. 6)의 김치가 9종 미만이면 이 문제다.

가격(`ingredient_price`)은 7)의 API 키가 필요한 별도 적재이며, 없으면 원가가 0원으로 계산된다.
가락은 청과·수산뿐이라 축산(소·돼지·닭·계란·우유)·곡류(쌀·찹쌀·현미·콩)는 KAMIS 로만 채워진다.
식단 원가는 재료별 **최신 `price_date`** 행을 쓰므로(`csp_solver._MENU_QUERY`), 두 소스에 다 있는 재료는
나중 조사일 쪽이 쓰인다. 같은 날짜면 먼저 적재된 행이 유지된다(KAMIS·파생 로더는 타 소스 행을 덮지 않음).

`ingredient_price.source` 값:

| source | 적재 | 의미 |
|---|---|---|
| `서울시농수산식품공사` | `load_ingredient_price.py` | 가락시장 도매(청과·수산), '상' 등급 30일 평균 |
| `KAMIS_W` | `load_ingredient_price_kamis.py` | KAMIS 도매(02) 당일가 |
| `KAMIS_R` | 〃 | KAMIS 소매(01) 당일가 — 도매가 없는 재료만(축산 전부 포함) |
| `쌀 환산` | `derive_ingredient_price.py` | 밥 = 쌀 g당 가격 ÷ 2.3 |
| `상위품목 대체` | 〃 | `닭고기(가슴)` 등 부위 재료 = 상위품목(`닭고기`·`돼지고기`·`소고기`) 가격 |

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

파생 원가 요점(`derive_ingredient_price.py`) — 값을 새로 만들지 않고 DB 에 있는 가격에 고정 규칙만 적용한다:
- `COOKED_RICE_WEIGHT_RATIO = 2.3`: 백미는 취반 시 무게가 약 2.2~2.4배(단체급식 취반 기준 쌀 1kg → 밥 약 2.3kg).
- 상위품목 대체는 부위 단가 차이(가슴살 > 통닭, 채끝 > 양지 등)를 반영하지 않는다. 뼈·꼬리 부위는 정육 단가와
  크게 달라 대체하지 않는다(`EXCLUDE_PART_KEYWORDS`). 수기 참조표로 덮을 자리를 표시하는 용도다.
- 두부 등 가공품은 다루지 않는다(수기 참조표 대상). 실행마다 두 source 행을 지우고 다시 만든다(멱등).

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
