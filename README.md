# OptiMeal — 비선형 AI 스케일링 모델 기반 대량 조리 레시피 자동 변환 시스템

소규모(1인분) → 대규모(100인분) 레시피를 비선형 멱함수 모델로 자동 변환하는 학술 프로젝트.

**Code Freeze**: 2026-09-30 | **최종 발표**: 2026-11

---

## 프로젝트 구조

```
OptiMeal/
├── data/
│   ├── raw/            # 원본 레시피 DB (수정 금지)
│   └── processed/      # 전처리 완료 데이터
├── docs/
│   ├── adr/            # Architecture Decision Records (읽기 전용)
│   ├── for_reports/    # 논문/보고서용 결과물
│   └── *.md, *.sql     # 핵심 참조 문서
├── module_1/           # 영양성분 DB
├── module_2/           # 비선형 스케일링 엔진 (현재 단계)
│   ├── src/engine/     # Rule-based 엔진 (lookup, fallback)
│   ├── src/statistics/ # MixedLM 통계 분석
│   ├── src/matching/   # 레시피 매칭
│   ├── tests/          # 테스트 및 분석 결과
│   └── otherstest/     # 팀원별 실험 스크립트
├── module_3/           # CSP Solver (6월~)
├── module_4/           # Frontend + Backend API (7~8월~)
├── reports/            # EDA, 통계, 피드백 리포트
├── scripts/            # 유틸리티 스크립트
├── tasks/              # 주차별 태스크 계획 및 리뷰
├── requirements.txt    # 라이브러리 버전 (변경 금지, 팀 회의 필수)
└── mise.toml           # Python 버전 고정 (3.12)
```

---

## 환경 설정

### 1. Python 버전 고정 (mise 사용)
```bash
# mise 설치 후
mise install
# Python 3.12가 자동 설정됨
```

### 2. 가상환경 생성 및 활성화
```bash
python -m venv .venv
source .venv/bin/activate      # macOS/Linux
# .venv\Scripts\activate       # Windows
```

### 3. 의존성 설치
```bash
pip install -r requirements.txt
```

> **주의**: `requirements.txt` 버전은 팀 회의 없이 변경 금지.
> 학술적 재현성 확보를 위해 버전이 고정되어 있습니다.

---

## 주요 명령어

```bash
# 모듈 2 테스트 실행
pytest module_2/tests/ -v

# EDA 실행 (경로 의존성 있음 — 아래 위치에서 실행)
python module_2/tests/EDAbeforeMixedLM/eda/eda_main.py

# Baseline 선형 모델 실행
python "module_2/otherstest/Baseline 선형모델_20260323_박미연/baseline_model_v0.02.py"

# 코드 포맷팅
black module_2/

# 린트 검사
flake8 module_2/
```

---

## 배포 체크리스트 (식단 생성 서버)

새 서버에 올리거나 DB 를 새로 만들 때 위에서부터 차례로 확인한다. 예산 규칙은 `docs/budget_carryover.md`.

- [ ] **마이그레이션 v2 → v5 적용** — `psql -f` 로 파일을 직접 읽힌다.
  ```bash
  for f in migrations/v2_*.sql migrations/v3_*.sql migrations/v4_*.sql migrations/v5_*.sql; do
    docker cp "$f" optimeal_db:/tmp/ && docker exec optimeal_db psql -U optimeal -d optimeal -f "/tmp/$(basename "$f")"; done
  ```
  ⚠ PowerShell 에서 `Get-Content 파일 | docker exec -i ... psql` 로 흘려 넣지 말 것 — 한글 주석이 깨지면서 뒤 문장이
  잘려 v5 의 `SET NOT NULL` 이 빠진 채 적용된 적이 있다. 적용 뒤 `\d ingredient_price` 에서 `source` 가 not null 이고
  유일키가 `(ingredient_id, price_date, source)` 인지 확인한다.
- [ ] **`.env` 키** — `.env.example` 참고. 원가에 `GARAK_ID`/`GARAK_PASSWD`(가락시장), `KAMIS_CERT_ID`/`KAMIS_CERT_KEY`(KAMIS),
  후보 메뉴에 `FOODSAFETY_RECIPE_KEY` 가 필요하다. 키 값은 출력·커밋하지 않는다(KAMIS 응답 JSON 의 `condition` 에 키가
  그대로 되돌아오므로 원본 응답을 로그·파일로 남기지 않는다). 확인은 길이만: `awk -F= '/^KAMIS_CERT_KEY=/{print length($2)}' .env`.
- [ ] **적재 순서** — `scripts/README.md` 의 "DB 세팅 순서"대로: 레시피 → 후보(영양) → 재료 맵 → 원가
  **가락 → KAMIS → 참가격 → 수기 → derive**. derive 는 가격이 바뀔 때마다 맨 마지막에 다시 돌린다.
  끝나면 `scripts/check_menu_candidates.py` 로 카테고리별 후보 수를 확인한다.
- [ ] **이미지 재빌드** — `requirements.txt`·`docker/Dockerfile` 이 바뀌었으면 `docker compose build app && docker compose up -d app`.
  PDF 한글 폰트(`fonts-nanum`)가 이미지에 들어 있는지 `/api/menu/export/pdf` 가 200 이고 PDF 안에 `NanumGothic` 서브셋이
  임베드되는지로 확인한다.
- [ ] **식단 생성은 동시 1건** — CP-SAT 이 코어를 다 쓰므로 백엔드가 한 번에 1건만 푼다(`_SOLVE_LOCK`, 3초 대기 후
  429 `solver_busy`). 락이 프로세스 단위라 **uvicorn 워커는 1개**(`--workers` 를 주지 않음)로 띄운다.
  4코어 기준 7일 3식 약 40초, 31일 2식 약 1분. 31일은 2식까지 지원(3식은 80초 한도에 여유가 없어 화면에서 막음).
- [ ] **목업 스위치는 꺼 둔다** — 프론트는 생성이 실패하면 목업으로 대체하지 않고 실패 화면(백엔드 연결 실패 / DB 연결 실패
  / 서버 오류)을 띄운다. 목업은 `VITE_USE_MOCK=true` 로 Vite 를 띄웠을 때만 쓰이며, 그때는 상단에 "목업 데이터 — 실제 생성
  결과 아님" 배너가 계속 뜨고 저장·PDF·CSV 가 막힌다. 사이드바 상태는 `/health` 의 `db`(DB ping) 까지 본다.

---

## Git 협업 방법

```bash
# 매주 작업 시작 전 반드시 pull
git pull origin main

# 작업 후 commit & push
git add .
git commit -m "module_2: [작업 내용 한 줄 요약]"
git push origin main
```

### 브랜치 전략
- `main`: 안정 버전. 직접 push 가능 (소규모 팀).
- 큰 변경은 별도 브랜치 생성 후 PR 권장.

---

## 핵심 수식 (ADR-001)

```
ratio = 실제 대규모 투입량 / (base_amount × N)   ← 종속변수
역변환: Y = a × base_amount × N^b               ← 엔진 출력
Baseline: ratio = 1 (b=1, 선형)

b < 1 : 대량 시 덜 필요 (양념류)
b ≈ 1 : 선형 (주재료)
b > 1 : 대량 시 더 필요
```

**MixedLM 최종 결과** (2026-03-20 확정):
- **b = 0.6163** (SE=0.1814, 95%CI [0.261, 0.972], AIC=1262.87, n=607)

---

## 핵심 제약사항

| 항목 | 규칙 |
|------|------|
| `requirements.txt` | 버전 변경 금지 (팀 회의 필수) |
| 합성 데이터 | 사용 금지 |
| ratio 이상치 기준 | ratio < 0.2 또는 ratio > 3.0 (변경 금지) |
| MixedLM 우선순위 | \|b_MixedLM - b_curvefit\| > 0.1이어도 MixedLM 채택 |
| 교차검증 | GroupKFold(n_splits=5, 단위=base_recipe_id) |
| 원본 데이터 | `data/raw/` 직접 수정 금지 → `data/processed/`에 복사 후 작업 |
| ADR 문서 | `docs/adr/` 읽기 전용 (직접 수정 금지) |
| ML random_state | 42 고정 |

---

## 코딩 컨벤션

- 주석: **한국어**
- Docstring: Google style 한국어
- import 순서: 표준 라이브러리 → 서드파티 → 로컬
- 함수 길이: 50줄 이내
- 클래스 전체 생성 금지 — 함수/메서드 단위

---

## 현재 진행 단계 (2026-04 기준)

- [x] 모듈 1: 영양성분 DB 완성
- [x] 통계 분석 완료 (MixedLM B안 확정, b=0.6163)
- [x] **현재**: Rule-based 엔진 구현 (4월)
- [x] ML 보조 모듈 (4월 말)
- [x] 영양사 피드백 루프 (5월)
- [ ] CSP Solver (6월~)
- [ ] FastAPI + React (7~8월)

---

## 핵심 참조 문서

| 문서 | 경로 |
|------|------|
| ADR 요약 | `docs/decisions_summary_v5_7.md` |
| ADR 원본 | `docs/adr/` |
| DB 스키마 | `docs/optimized_schema_v4_3_20260316.sql` |
| 요구사항/기능명세 | `docs/요구사항정의서_기능명세서_v1_4_20260314.md` |
| 데이터 흐름 | `docs/data_flow_documentation_v1_4.md` |
| 로드맵 (1~6월) | `docs/비선형_하이브리드_스케일링_모델_프로젝트_로드맵_1to6_v5_6_20260314.md` |
| 로드맵 (6~11월) | `docs/비선형_하이브리드_스케일링_모델_프로젝트_로드맵_6to11_v5_6_20260314.md` |
| PRD | `docs/PRD_v1_5_20260314.md` |
