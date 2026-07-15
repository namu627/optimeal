# 작업 지시서: 식품안전나라 레시피 적재 스크립트 (태스크 2-②)

식품안전나라 조리식품 레시피 API(COOKRCP01)에서 레시피를 받아와,
**영양정보는 nutrition_recipe에, 레시피 본문은 recipe에** 적재하고 둘을 연결한다.
(두 테이블 모두 이미 존재 — CREATE 금지, INSERT만)

## 적재 순서 (중요 — 반드시 이 순서)
레시피 1건당:
1. 먼저 영양정보를 nutrition_recipe에 INSERT → 생성된 nutrition_id를 받는다.
   (INSERT ... RETURNING nutrition_id 사용)
2. 그다음 레시피를 recipe에 INSERT하며, nutrition_recipe_id에 위 nutrition_id를 넣어 연결.
순서가 반대면 연결할 id가 없다.

## 먼저 파악할 것
1. scripts/load_recipe_data.py의 DB 연결·중복방지 패턴 재사용.
2. cooking_method 테이블의 method_name↔method_id 대응을 조회해서 매핑에 사용
   (끓이기1/볶음2/찜3/구이4/무침5/조림6/튀김7/삶기8).

## API 정보
- **호출 URL (경로 삽입 방식)**:
  `http://openapi.foodsafetykorea.go.kr/api/{인증키}/COOKRCP01/json/{시작}/{끝}`
- **인증**: URL 경로에 키 삽입 (⚠️ .env에서 읽기)
  - `.env`에 `FOODSAFETY_RECIPE_KEY` 추가, os.getenv로 읽기 (.env.example도 키만 추가)
- **페이징**: 한 번에 최대 1000건. total_count로 전체 파악 후 순회.
  - 응답 구조: `{"COOKRCP01": {"total_count":"1146", "row":[...], "RESULT":{...}}}`
  - 실제 데이터는 COOKRCP01.row 배열

## nutrition_recipe 매핑 (영양정보) — data_source='식품안전나라'
```
RCP_NM    → recipe_name    (필수)
INFO_ENG  → calories       (필수, 빈값이면 그 레시피 전체 skip)
INFO_PRO  → protein
INFO_FAT  → fat
INFO_CAR  → carbs
INFO_NA   → sodium
당류/콜레스테롤/포화지방/트랜스지방 → 해당 필드 없음 → NULL
INFO_WGT  → serving_size_g (보통 비어있음 → 빈값이면 NULL)
menu_category → RCP_PAT2를 아래 표로 매핑
'식품안전나라' → data_source  (CHECK 허용값에 있음, 정확히 이 문자열)
원본 item dict 전체 → original_data (jsonb)
```
- 숫자 파싱: 빈 문자열/None → NULL, 콤마 제거 후 float, 자릿수(numeric 제약) 초과 시
  해당 필드만 NULL + overflow 로그 (nutrition 적재 때와 동일 규칙).

## recipe 매핑 (레시피 본문) — data_source='식약처API'
```
RCP_NM        → recipe_name  (필수)
RCP_WAY2      → primary_method_id (아래 조리법 매핑표. 매칭 실패 시 그 레시피 skip)
1 (고정)      → serving_size (식품안전나라는 1인분 기준)
'소규모'(고정) → serving_category
MANUAL01~MANUAL20 → description (비어있지 않은 것만 순서대로 줄바꿈 결합)
'출처=식품안전나라 RCP_SEQ={RCP_SEQ}' → notes  (원본 ID 기록 + 중복방지 키)
① nutrition_id → nutrition_recipe_id (연결)
'식약처API'    → data_source (CHECK 허용값. '식품안전나라'는 recipe엔 없으므로 이 값 사용)
```

## 조리법 매핑표 (RCP_WAY2 → cooking_method.method_name)
```
'찌기'  → '찜'
'끓이기' → '끓이기'
'삶기'  → '삶기'
'볶기'  → '볶음'
'굽기'  → '구이'
'튀기기' → '튀김'
'조리기' → '조림'
'무치기' → '무침'
'기타' 및 위에 없는 값 → 매칭 실패 → 그 레시피 skip + skip 로그에 RCP_WAY2 값 기록
```
⚠️ 식품안전나라 실제 조리법 표기를 3건밖에 못 봤다. 위 표에 없는 값이 나오면
   억지 매칭하지 말고 skip 후 **어떤 RCP_WAY2 값이 매칭 실패했는지 집계 출력**해라.
   (나중에 매핑표를 보강하기 위함)

## menu_category 매핑표 (RCP_PAT2 → 7종)
```
'반찬' → '반찬'
'국&찌개' 또는 '국','찌개' → '국' 또는 '찌개' (표기 보고 판단, 애매하면 '국')
'후식' → '후식'
'밥' 또는 '일품' 주식류 → '주식'
'음료' → '음료'
그 외/매칭 불가 → '기타'
```
menu_category는 매칭 실패해도 '기타'로 넣으면 되므로 레시피를 skip하지 않는다.
(CHECK 7종: 주식/국/찌개/반찬/후식/음료/기타)

## 중복 방지
- recipe에 UNIQUE 없음. notes에 박은 'RCP_SEQ={n}'로 판별.
- 시작 시 기존 recipe.notes에서 RCP_SEQ를 추출해 집합으로 만들고,
  이번 실행 중 처리한 것도 추가해 중복 skip.
- (참고: recipe는 기존 3,057건 존재. 그중 식품안전나라 원본이 이미 있을 수 있으니
  RCP_SEQ 기준 사전 조회 필수.)

## 산출물
1. scripts/load_recipe_foodsafety.py (기존 네이밍 관례 따라)
2. 콘솔 요약: 총 시도 / recipe 신규 / nutrition 신규 / skip(사유별: calories빈값,
   조리법매칭실패, 중복) / RCP_WAY2 매칭실패 값별 집계
3. skip 로그 CSV (data/external/)

## 주의
- 인증키를 코드·로그에 출력 금지.
- ① nutrition INSERT와 ② recipe INSERT는 **같은 트랜잭션**으로 묶어라.
  (레시피 넣다 실패 시 영양정보만 붕 뜨는 것 방지 — 레시피당 원자적으로)
- 바로 실행하지 말고 스크립트 전체를 보여줘. 소량(예: 10건)으로 먼저 테스트한다.
