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
- [ ] **현재**: Rule-based 엔진 구현 (4월)
- [ ] ML 보조 모듈 (4월 말)
- [ ] 영양사 피드백 루프 (5월)
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
