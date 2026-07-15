# 작업 지시서 v3: 수입품목전용 가격 API 추가 적재

기존 load_ingredient_price.py에 **수입품목전용 가격 API(data65)** 를 추가한다.
응답 구조가 기존 주요품목가격(data52)과 동일하므로, 매칭·환산·등급필터·upsert
로직은 그대로 재활용하고 **API 호출부만 확장**한다. 기존 국산 적재 로직은 건드리지 않는다.

---

## 추가할 API 정보

- **호출 URL**: 기존과 동일 (`http://www.garak.co.kr/homepage/publicdata/dataJsonOpen.do`)
- **dataid**: `data65` (기존은 data52)
- **인증**: 별도 id/passwd 사용
  - `.env`에서 `GARAK_IMPORT_ID`, `GARAK_IMPORT_PASSWD`로 읽기 (id=9697)
  - 기존 GARAK_ID(9695)와 다른 계정이므로 반드시 분리해서 읽을 것
- **파라미터**: 기존과 거의 같으나 `p_buryu` 파라미터가 추가됨
  - `p_buryu`: 부류 (청과=2, 수산=3) — d_cd와 같은 값을 넣으면 됨
  - 즉 청과 호출 시 `d_cd=2` & `p_buryu=2`, 수산 호출 시 `d_cd=3` & `p_buryu=3`
  - 나머지(pagesize, pageidx, p_ymd, p_jymd, p_jjymd, p_pos_gubun=1)는 동일

## 응답 필드 (실측 확인)
- `PUM_NM_A`(품목명), `G_NAME_A`(등급), `AV_P_A`(가격), `U_NAME`(단위)
- ⚠️ 기존과 달리 `UNIT_QTY`, `PUM_CD` 필드가 **없다**.
  - parse_unit_to_grams는 이미 "UNIT_QTY 없으면 U_NAME에서 숫자 추출"로 처리되므로
    U_NAME(예: "12키로상자")에서 숫자를 뽑아 환산 가능. 추가 수정 불필요.
- 응답 컨테이너는 동일하게 `{"resultData": [...]}`.

## 구현 방향
1. collect_price_samples()가 두 소스를 모두 수집하도록 확장한다:
   - 소스 A: 기존 주요품목가격(data52, GARAK_ID)  — 국산 위주
   - 소스 B: 수입품목전용(data65, GARAK_IMPORT_ID) — 수입 위주
2. 두 소스의 샘플을 **같은 samples 리스트에 합친다.**
   이후 매칭·일별대표값·품목별평균·upsert 로직은 기존 그대로 통과.
3. 수입 품목은 "수입" 수식어가 붙어 오지만(예: "골드파인애플 수입"),
   기존 정규화 로직이 "수입"을 제거하도록 돼 있어 매칭에 문제 없음.
4. fetch 함수에 dataid, id, passwd, p_buryu를 인자로 받도록 일반화하면
   두 소스를 같은 함수로 호출 가능. (기존 fetch_grade_items_for_date를 파라미터화)

## 주의 (기존 유지)
- 등급 '상' 필터, ON CONFLICT upsert, 예외 시 str(e) 미출력, 매칭 실패/단위 실패 CSV
- preview/load 2단계 구조 그대로 유지 (수입품목도 preview에서 함께 검수 가능해야 함)
- API 호출 사이 delay 유지. 수입 API도 30일 순회.

## 작성 후
바로 실행하지 말고 수정 부분을 보여줘. preview 모드로 먼저 돌려서
수입 품목까지 합친 매칭 결과를 검수한 뒤 load 한다.
