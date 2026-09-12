# 모듈 4 백엔드 — REST API + 캘리브레이션 API

FastAPI 기반 REST API (FR-12). **ADR-008 이후의 계약**을 HTTP 표면에 그대로 옮긴 것이 이 서비스의 핵심이다.

## 실행

```bash
source .venv/bin/activate
uvicorn module_4.backend.src.main:app --reload --port 8000
# Swagger: http://localhost:8000/docs   ·  OpenAPI: /openapi.json
pytest module_4/backend/tests/ -v      # 22 PASS
```

### ⚠ 의존성 (팀 회의 안건)

`fastapi`·`uvicorn`(그리고 모듈 3의 `ortools`)은 **`requirements.txt`에 아직 없다.**
프로젝트 규칙상 `requirements.txt` 변경은 팀 회의 필수이므로 현재는 `.venv` 로컬 설치만 했다.

| 패키지 | 검증에 쓴 버전 | 필요 사유 |
|---|---|---|
| fastapi | 0.115.6 | 본 API (PRD 기술스택 확정) |
| uvicorn | 0.34.0 | ASGI 실행 |
| httpx | 0.28.1 | TestClient (이미 frozen에 존재) |
| ortools | 9.11.4210 | 모듈 3 CSP — **전 브랜치 requirements 미선언** |

## 엔드포인트

### 캘리브레이션 (ADR-008 운영 핵심)

| Method | Path | 설명 |
|---|---|---|
| POST | `/api/calibration/observations` | **보정 누적** — 원장 append + ADR-003 단순이동평균으로 셀 추정 갱신 |
| GET | `/api/calibration/estimate` | 셀 추정 + 신뢰도 플래그. 미관측 셀은 cold-start 선형(ratio=1.0) |
| GET | `/api/calibration/convergence` | 회차별 수렴 곡선(APE) — NFR-01(B) 수렴성 원자료 (+ 미실증 caveat) |
| GET | `/api/calibration/references` | **CBR 참고 이력 표시** — `auto_apply=false` 고정 |
| POST/GET | `/api/calibration/sites` | 업장 등록·조회 |

### 스케일링

| Method | Path | 설명 |
|---|---|---|
| GET | `/api/scaling/recipes` | 레시피 목록 (`recipe_key`↔정수 `recipe_id` 매핑 제공) |
| GET | `/api/scaling/recipes/{key}` | 재료 구성(1인분) |
| POST | `/api/scaling/predict` | cold-start 선형 + 캘리브레이션 오버레이. 재료별 `method`·`n_obs`·`confidence` 동반 |
| GET | `/api/scaling/coefficients` | 계수 331행 조회 — `applied_in_production=false` (학술 비교군) |

### 선택적 의존성 (없으면 해당 엔드포인트만 503)

| Method | Path | 필요 인프라 |
|---|---|---|
| GET | `/api/nutrition/search`, `/api/nutrition/{id}` | PostgreSQL `nutrition_recipe` (모듈 1) |
| POST | `/api/menu/generate` | 모듈 3 CSP (팀 repo) + PostgreSQL |

`GET /health`가 이 둘의 구성 여부를 보고한다.

## 설계 원칙 — 왜 이렇게 만들었나

1. **정직성을 타입으로 강제.** 스케일링 응답의 모든 재료는 `method`(`cold_start_linear`/`calibrated`)와 `confidence`를 반드시 갖는다. "AI가 예측했다"고 오해할 여지를 스키마 수준에서 없앴다.
2. **부분 적용이 정상 상태.** 보정이 쌓인 재료만 `calibrated`가 되고 나머지는 cold-start로 남는다 (`calibrated_count`/`cold_start_count`로 노출).
3. **CBR은 표시 전용.** `auto_apply=false` + 출처(site/recipe/시각) + Jaccard 동봉 — 닻내림 완화(ADR-008).
4. **업장 격리.** 셀 키는 (site × recipe × ingredient). 한 업장의 보정이 다른 업장에 새지 않는다(테스트로 고정).
5. **선택적 의존성 격리.** 모듈 3·DB가 없어도 앱은 기동한다. 캘리브레이션은 이 저장소 단독으로 완결된다.

## 상태 저장

캘리브레이션 원장만 쓰기 대상이다 (`OPTIMEAL_CALIBRATION_DB`, 기본 `data/processed/calibration.db`).
`CalibrationStore`는 `sqlite3` 커넥션을 보유하므로 스레드 간 공유가 불가 → **요청당 커넥션**(`deps.get_store`).

## 알려진 제약

- 캘리브레이션 셀 id는 `df_B.csv` 정렬 순서 기반 결정론 매핑이다. **운영 DB 이관 시 `recipe.recipe_id`·`ingredient.ingredient_id`로 교체 필요** (`repositories._registries`).
- 수렴성은 미실증 가설이다(실데이터 반복 셀 0건). `/convergence`는 측정 파이프라인이며 수렴 자체의 증거가 아니다.
- 인증·권한은 미구현(학내 프로젝트 범위). 영양사 식별은 요청 본문의 `site_id`에 의존한다.
