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
# 4) 재료 맵 → 5) 메뉴↔재료보유 레시피 연결(원가 계산 경로)
docker exec optimeal_app python scripts/load_ingredients_from_recipe_db.py
docker exec optimeal_app python scripts/connect_menus_to_recipes.py --apply
# 6) 점검 — 카테고리별 후보가 다중일 식단에 충분한지(부족하면 exit 1)
docker exec optimeal_app python scripts/check_menu_candidates.py
```

⚠ 2)는 2026-09 이전 버전에서 조리법 `RCP_WAY2='기타'`(김치·무침·샐러드 등 비가열 310건)를 통째로
skip 해 김치 후보가 1종만 남았다 → 3일 반복 금지 제약과 충돌해 2일 이상 식단이 항상 INFEASIBLE.
지금은 '기타'를 `cooking_method` '무침'(no_heat)으로 적재한다. 6)의 김치가 9종 미만이면 이 문제다.

가격(`ingredient_price`)은 가락시장 API 키가 필요한 별도 적재이며, 없으면 원가가 0원으로 계산된다.

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
