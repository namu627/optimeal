# CSP Soft(다양성) 모듈 조리법 DB 헬퍼(load_cooking_methods) 검증 결과

- 검증일: 2026-07-30
- 대상 DB: `optimeal_db` (PostgreSQL 18.1, docker-compose `db` 서비스)
- 접속 정보 출처: `.env` (`POSTGRES_USER=optimeal`, `POSTGRES_DB=optimeal`)
- 실행 방식: `docker compose up -d db` 로 컨테이너 기동 후 `psql`에
  `PGOPTIONS='-c default_transaction_read_only=on'` 를 적용해 세션을
  read-only로 강제한 상태에서 조회. 작업 종료 후 `docker compose stop db`로
  원상 복구(작업 시작 전 `db` 서비스는 Exited 상태였음. `app` 서비스는 이미
  Up 상태였고 이번 작업에서 건드리지 않음).

## 1. 컬럼 존재 확인 (information_schema)

### 쿼리
```sql
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema='public'
  AND (
    (table_name='recipe' AND column_name IN ('recipe_id','nutrition_recipe_id','primary_method_id'))
    OR (table_name='cooking_method' AND column_name IN ('method_id','method_name'))
  )
ORDER BY table_name, column_name;
```

### 결과
```
   table_name   |     column_name     |     data_type
----------------+---------------------+-------------------
 cooking_method | method_id           | integer
 cooking_method | method_name         | character varying
 recipe         | nutrition_recipe_id | integer
 recipe         | primary_method_id   | integer
 recipe         | recipe_id           | integer
(5 rows)
```
에러 없음. 헬퍼가 참조하는 컬럼 5개 전부 존재 확인.

## 2. cooking_method 실제 내용 확인

### 쿼리
```sql
SELECT method_id, method_name FROM cooking_method ORDER BY method_id;
```

### 결과
```
 method_id | method_name
-----------+-------------
         1 | 끓이기
         2 | 볶음
         3 | 찜
         4 | 구이
         5 | 무침
         6 | 조림
         7 | 튀김
         8 | 삶기
(8 rows)
```
에러 없음. `method_name`은 총 8종의 한글 조리법 라벨(끓이기, 볶음, 찜, 구이, 무침, 조림, 튀김, 삶기)로 구성.

## 3. 헬퍼 B(load_cooking_methods) 실제 SQL 실행

### 쿼리 — 샘플 10건
```sql
WITH ids AS (
  SELECT nutrition_recipe_id AS nid FROM recipe
  WHERE nutrition_recipe_id IS NOT NULL LIMIT 10
)
SELECT nr.nutrition_id AS menu_id, cm.method_name AS method
FROM nutrition_recipe nr
JOIN recipe r          ON r.nutrition_recipe_id = nr.nutrition_id
JOIN cooking_method cm ON cm.method_id = r.primary_method_id
WHERE nr.nutrition_id = ANY(ARRAY(SELECT nid FROM ids));
```

### 결과
```
 menu_id | method
---------+--------
  302526 | 찜
  302527 | 찜
  302528 | 끓이기
  302529 | 끓이기
  302530 | 끓이기
  302531 | 끓이기
  302532 | 찜
  302533 | 구이
  302534 | 구이
  302535 | 끓이기
(10 rows)
```
에러 없음. 10건 모두 정상적으로 method_name이 매핑되어 반환됨 (찜, 끓이기, 구이 등장).

## 4. 커버리지 확인

### 쿼리 — recipe에 연결 + primary_method_id 있는 메뉴 수
```sql
SELECT COUNT(DISTINCT nr.nutrition_id) AS menus_with_db_method
FROM nutrition_recipe nr
JOIN recipe r ON r.nutrition_recipe_id = nr.nutrition_id
WHERE r.primary_method_id IS NOT NULL;
```
### 결과
```
 menus_with_db_method
-----------------------
                   844
(1 row)
```

### 쿼리 — recipe 테이블 자체의 primary_method_id NULL 여부
```sql
SELECT COUNT(*) AS recipe_total,
       COUNT(primary_method_id) AS recipe_with_method
FROM recipe;
```
### 결과
```
 recipe_total | recipe_with_method
--------------+---------------------
         3901 |                3901
(1 row)
```
에러 없음. `recipe` 테이블의 `primary_method_id`는 **NULL이 하나도 없음** (3,901/3,901, 100% 채워짐).

### 추가 확인 — 844건인 이유 (recipe 행 수와 고유 메뉴 수의 괴리)
```sql
SELECT COUNT(*) AS recipe_rows, COUNT(DISTINCT nutrition_recipe_id) AS distinct_nid FROM recipe;
```
```
 recipe_rows | distinct_nid
-------------+---------------
        3901 |          844
(1 row)
```
```sql
SELECT COUNT(*) FROM nutrition_recipe;
```
```
 count
--------
 303369
(1 row)
```
→ `recipe` 테이블은 3,901행이지만 `nutrition_recipe_id` 기준 고유 메뉴는 844개뿐(메뉴 하나당 평균 약 4.6개 recipe 행이 존재, 즉 레시피가 여러 행으로 중복/분해되어 저장됨). 그리고 전체 `nutrition_recipe`는 303,369건이므로, **조리법 정답을 얻을 수 있는 메뉴는 전체의 약 0.28%(844/303,369)에 불과**하다.

## 5. 결론 요약

| 항목 | 결과 |
|---|---|
| 1. 컬럼 존재 확인 | Y (에러 없음, 5/5 컬럼 확인) |
| 2. cooking_method 실제 내용 확인 | Y (에러 없음) |
| 3. 헬퍼 B 실행 | Y (에러 없음, 10건 모두 정상 매핑) |
| 4. 커버리지 확인 | Y (에러 없음) |
| cooking_method.method_name 값 목록 | 끓이기, 볶음, 찜, 구이, 무침, 조림, 튀김, 삶기 (총 8종) |
| 헬퍼가 정답 조리법을 붙일 수 있는 메뉴 건수 | **844건** (전체 nutrition_recipe 303,369건 중 약 0.28%) |
| 어긋난 컬럼명 | **없음.** `recipe.recipe_id`, `recipe.nutrition_recipe_id`, `recipe.primary_method_id`, `cooking_method.method_id`, `cooking_method.method_name` 모두 실제 스키마와 정확히 일치 |

**종합**: 헬퍼 `load_cooking_methods`는 컬럼명·조인 경로 모두 실제 스키마와 정확히 일치하며 에러 없이 동작한다. `recipe.primary_method_id`는 존재하는 3,901개 recipe 행 전부에서 NULL 없이 채워져 있지만, 애초에 `recipe` 테이블 자체가 `nutrition_recipe`(303,369건) 대비 극히 일부(고유 메뉴 844개)만 커버하고 있어 **실제 서비스에서 조리법 정답을 얻을 수 있는 메뉴는 전체의 약 0.28% 수준**이다. 스키마/쿼리 로직 자체는 문제없으나, 커버리지 부족은 데이터 적재(recipe 테이블 확장) 관점에서 별도로 다뤄야 할 이슈다.
