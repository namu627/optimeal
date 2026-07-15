# 작업 지시서: ingredient_price 가격 적재 스크립트

## 배경
가락시장(서울시농수산식품공사) 주요품목가격 API에서 식재료 가격을 받아와,
**이미 존재하는** `ingredient_price` 테이블에 적재하는 스크립트를 작성한다.
테이블은 이미 스키마에 정의돼 있으므로 **CREATE 하지 말고 INSERT(upsert)만** 한다.

## 먼저 파악할 것 (코드 짜기 전)
1. `scripts/load_recipe_data.py`를 열어서 재사용 가능한 패턴을 파악하라:
   - DB 연결 함수 (get_engine 등)
   - 파일/데이터 적재 로그, 중복 방지 패턴
   - Bulk INSERT / upsert 방식
   - get_or_create_ingredient 같은 재료 매칭 함수
   이 스크립트의 스타일과 유틸을 최대한 재사용해서 일관성을 유지하라.
2. `ingredient` 테이블과 `ingredient_synonym` 테이블 구조를 확인하라
   (\d ingredient_synonym). 재료명 매칭에 필요하다.

## API 정보
- **호출 URL (JSON)**: `http://www.garak.co.kr/homepage/publicdata/dataJsonOpen.do`
- **인증**: `id`, `passwd` 를 쿼리 파라미터로 전달 (⚠️ .env에서 읽어라. 하드코딩 금지)
  - `.env`에 `GARAK_ID`, `GARAK_PASSWD` 추가하고 os.getenv로 읽기
  - `.env.example`에도 키 이름만 추가(값은 비워서)
- **필수 파라미터**:
  - `dataid=data52` (주요품목가격)
  - `pagesize`, `pageidx` (페이징)
  - `p_ymd`(검색일 YYYYMMDD), `p_jymd`(전일), `p_jjymd`(전년도 같은날)
  - `d_cd`(부류: 청과 2, 수산 3)
  - `p_pos_gubun=1` (가락시장)
  - `pum_nm=` (비우면 전체 품목)
  - `portal.templet=false`

## 응답 필드 (실측 확인됨)
- `PUM_NM_A`: 품목명 (예: "고구마", "호박밤 고구마")
- `PUM_CD`: 품목코드
- `G_NAME_A` / `E_NAME`: 등급 (특/상/중/하)
- `AV_P_A`: 평균가격 (원)
- `U_NAME`: 거래단위 (예: " 10키로상자" — **앞 공백 주의, strip 필요**)
- `UNIT_QTY`: 거래수량 (예: " 10")

## 처리 규칙 (확정된 설계 결정)

### 1. 등급 선택: '상' 등급만
- 응답에서 `G_NAME_A == '상'` (또는 E_NAME) 인 행만 사용.
- 이유: 급식 납품 현실 기준. 특은 과대추정, 하는 품질 미달.
- `(ingredient_id, price_date)` UNIQUE 제약 때문에 재료·날짜당 1개 가격만 가능하므로
  등급을 반드시 하나로 좁혀야 한다.

### 2. 가격 기준일: 최근 30일 평균
- 오늘 기준 최근 30일(주말/휴일 제외 권장)의 `p_ymd`를 순회 호출.
- 각 날짜의 '상' 등급 `price_per_g`를 재료별로 모아서 **평균**을 낸다.
- `price_date`에는 평균 대상 기간의 대표일(예: 산출 기준일 = 오늘)을 넣는다.
- 데이터 없는 날짜(주말, 미확정일)는 건너뛰고, 실제 값이 있는 날짜만 평균에 포함.

### 3. 단위 환산: price_per_g 계산
- `U_NAME`과 `UNIT_QTY`로 총 그램수를 구해 g당 단가로 환산.
- 예: AV_P_A=31249, U_NAME="10키로상자" → 10kg=10000g → 31249/10000 = 3.1249 원/g
- "키로"="kg" 파싱 규칙 필요. 상자/망/포 등 단위 표기 다양할 수 있으니
  숫자+kg 패턴을 우선 처리하고, 파싱 실패 시 로그 남기고 건너뛴다.
- price_per_g는 numeric(10,4)이므로 소수 4자리로 반올림.

### 4. 재료명 → ingredient_id 매칭
- `PUM_NM_A`(품목명) 문자열을 `ingredient.ingredient_name`과 매칭.
- 1차: 정확히 일치하는 이름 검색.
- 2차: 실패 시 `ingredient_synonym` 테이블에서 동의어로 재시도.
- **3차 실패 시: 건너뛰고 로그에 남긴다** (자동 추가·억지 매칭 금지).
  - 매칭 실패 품목명을 별도 리스트(또는 CSV)로 출력해서, 나중에 수동으로
    동의어 규칙을 보강할 수 있게 한다.
- 이유: 잘못된 자동 매칭이 조용히 데이터를 오염시키는 것을 방지.

### 5. 적재: upsert (ON CONFLICT)
- `(ingredient_id, price_date)` UNIQUE 제약이 있으므로
  `INSERT ... ON CONFLICT (ingredient_id, price_date) DO UPDATE`로 처리.
- 재실행해도 안전(idempotent)하게.
- source 컬럼은 기본값('서울시농수산식품공사') 사용하거나 명시적으로 넣기.

## 산출물
1. 가격 적재 스크립트 (scripts/ 아래, 기존 네이밍 관례 따라. 예: load_ingredient_price.py)
2. 매칭 실패 품목 로그 (콘솔 출력 또는 CSV)
3. 실행 후 ingredient_price 테이블에 적재된 행 수 요약

## 주의
- .env의 실제 id/passwd 값을 코드나 로그에 절대 출력하지 마라.
- API 호출 사이 적절한 딜레이(예: 0.3~0.5초)를 둬서 서버 부하/차단을 피하라.
- 청과(d_cd=2)와 수산(d_cd=3)을 각각 호출해야 한다.
  가락시장 특성상 육류·곡류·가공품은 없을 수 있음(문서에 한계로 기록).
- 먼저 pagesize를 크게(예: 1000) 해서 전체 품목 수를 파악하고, 페이징 처리.
