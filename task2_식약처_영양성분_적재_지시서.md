# 작업 지시서: 식약처 영양성분 DB 적재 스크립트 (태스크 2-①)

식약처 영양성분 API(FoodNtrCpntDbInfo02)에서 약 30만 건을 받아와
**이미 존재하는 nutrition_recipe 테이블**에 적재한다.
(테이블은 스키마에 이미 있음 — CREATE 금지, INSERT만)

## 먼저 파악할 것
1. scripts/load_recipe_data.py의 DB 연결(get_engine), 배치 처리, 예외 처리 패턴 재사용.
2. load_ingredient_price.py의 .env 자격증명 읽기 패턴 참고
   (단, 이 API는 공공데이터포털 키로 serviceKey 쿼리스트링 방식).

## API 정보
- **엔드포인트**: `https://apis.data.go.kr/1471000/FoodNtrCpntDbInfo02/getFoodNtrCpntDbInq02`
- **인증**: serviceKey 쿼리 파라미터 (⚠️ .env에서 읽기. 하드코딩 금지)
  - `.env`에 `FOODSAFETY_NUTRITION_KEY` 추가, os.getenv로 읽기
  - `.env.example`에도 키 이름만 추가(값 비움)
- **파라미터**: `serviceKey`, `pageNo`, `numOfRows`(최대 1000), `type=json`
- **응답 구조**: `{"header":{...}, "body":{"pageNo":N, "totalCount":302629, "items":[...]}}`
  - totalCount로 전체 건수 파악 → 페이징
  - items가 실제 데이터 배열

## 컬럼 매핑 (명세로 확정됨 — 반드시 이대로)
```
FOOD_NM_KR   → recipe_name      (필수, varchar 200)
AMT_NUM1     → calories         (필수, 에너지 kcal)
AMT_NUM3     → protein          (단백질 g)
AMT_NUM4     → fat              (지방 g)
AMT_NUM6     → carbs            (탄수화물 g)
AMT_NUM13    → sodium           (나트륨 mg)   ← 13번! 81 아님 주의
AMT_NUM7     → sugar            (당류 g)
AMT_NUM23    → cholesterol      (콜레스테롤 mg)
AMT_NUM24    → saturated_fat    (포화지방산 g)
AMT_NUM25    → trans_fat        (트랜스지방산 g)
SERVING_SIZE → serving_size_g   ("100g" → 숫자 100 파싱)
'식약처' 고정 → data_source     (필수)
'기타' 고정   → menu_category   (잠정, CHECK 7종 중 하나여야 함)
응답 dict 통째 → original_data  (jsonb, 원본 보존 → FOOD_CD 등 여기 남김)
```
나머지 AMT_NUM(147개)은 테이블 컬럼이 없으므로 적재하지 않음
(단 original_data에 원본이 통째로 보존됨).

## 데이터 처리 규칙 (중요 — 실제 응답의 함정들)
1. **빈 문자열 → NULL**: AMT_NUM 값이 ""인 경우 매우 많음. float 변환 전 빈 값은 None 처리.
2. **콤마 제거**: "1,670.000" 같이 콤마 포함 값 → 콤마 제거 후 float.
3. **SERVING_SIZE 파싱**: "100g"에서 숫자만 추출(정규식). 파싱 실패 시 NULL.
4. **calories 필수**: calories(AMT_NUM1)가 빈 값이면 그 행은 skip하고 로그
   (NOT NULL 제약 위반 방지).
5. **자릿수 주의**: protein/fat/carbs 등은 numeric(6,2)라 정수부 4자리 초과 시 에러.
   100g 기준이라 대부분 안전하나, 초과 값은 클리핑 또는 skip 후 로그.

## 30만 건 처리 (태스크 1과 규모가 다름 — 필수 반영)
1. **배치 커밋**: numOfRows=1000으로 페이징. 한 페이지(1000건) 받을 때마다
   DB에 INSERT하고 커밋. 30만 건을 메모리에 다 모으지 마라.
2. **진행 상황 출력**: 매 페이지마다 "진행 N/302629 (X%)" 출력.
3. **이어받기**: 중간 실패 대비. 실패한 pageNo를 로그로 남기고,
   가능하면 --start-page 인자로 특정 페이지부터 재시작 가능하게.
4. **API 딜레이**: 페이지 호출 사이 0.2~0.5초.
5. **재시도**: 페이지 호출 실패 시 2~3회 재시도 후에도 실패하면 로그 남기고 계속.

## 중복 방지
- nutrition_recipe에는 UNIQUE 제약이 없음(DB가 안 막아줌).
- FOOD_CD를 중복 판별 기준으로 사용:
  - 이미 적재된 FOOD_CD 집합을 시작 시 조회(original_data에서),
    또는 이번 실행 내에서 본 FOOD_CD를 set으로 관리해 중복 skip.
- 첫 적재 시엔 테이블이 0건이라 사실상 전부 신규지만,
  재실행해도 중복이 안 쌓이도록 반드시 넣을 것.

## 산출물
1. scripts/load_nutrition_mfds.py (또는 기존 네이밍 관례 따라)
2. 적재 결과 요약: 총 시도/신규 삽입/skip(빈 calories, 중복)/실패 페이지 수
3. skip·실패 로그 CSV (data/external/)

## 주의
- serviceKey 값을 코드·로그에 출력 금지.
- 바로 실행하지 말고 스크립트 전체를 보여줘. 검토 후 소량(예: 2~3페이지)으로
  먼저 테스트하고, 정상 확인 후 전체 적재한다.
