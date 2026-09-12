"""
main.py
=======
OptiMeal 백엔드 진입점 (FastAPI, FR-12).

라우터 구성:
  /api/calibration/*  캘리브레이션 — 보정 누적·셀 추정·수렴 곡선·CBR 참고 (ADR-008 운영 핵심)
  /api/scaling/*      레시피 스케일링 (cold-start 선형 + 캘리브레이션 오버레이) · 계수 조회
  /api/nutrition/*    영양성분 (모듈 1, PostgreSQL 필요)
  /api/menu/*         식단 생성 (모듈 3 CSP 위임)

실행:
    source .venv/bin/activate
    uvicorn module_4.backend.src.main:app --reload --port 8000
    # Swagger: http://localhost:8000/docs
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .routers import calibration, menu, nutrition, scaling

# [통합테스트 수정안] 프론트(React/Vite)가 브라우저에서 이 API를 호출하려면 CORS 필수.
# 개발 기본값은 Vite(5173)·CRA(3000). 운영은 OPTIMEAL_CORS_ORIGINS(콤마구분)로 지정.
_CORS_ORIGINS = [
    o.strip() for o in os.getenv(
        "OPTIMEAL_CORS_ORIGINS",
        "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173",
    ).split(",") if o.strip()
]

DESCRIPTION = """
**ADR-008 이후의 계약**: 본 API의 스케일링은 정확 예측기가 아니다.

* cold-start 는 **선형(b=1)** — 실측 OOS에서 최선·무편향이었기 때문이다(과소예측 위험 없음).
* 정확도는 영양사 보정을 `/api/calibration/observations` 로 **누적**하면서 개선된다.
* CBR 참고 이력은 표시 전용이며 **자동 적용하지 않는다**(`auto_apply=false`).
* 모든 스케일링 응답은 재료별 `method`·`n_obs`·`confidence` 를 동반한다(감사 가능성).

---
### 프론트엔드 개발자용 연동 안내
* **Base URL(개발)**: `http://localhost:8000`
* **CORS**: `localhost:5173`(Vite)·`localhost:3000`(CRA) 허용. 운영은 `OPTIMEAL_CORS_ORIGINS`(콤마 구분)로 지정.
* **인증**: 현재 없음(학내 프로젝트). 영양사 식별은 요청의 `site_id`로 구분.
* **선택적 의존성**: `/api/nutrition/*`·`/api/menu/*`는 PostgreSQL·모듈3 필요. 미구성 시 **503 + reason·hint** 반환(500 아님) → 프론트는 503을 "인프라 준비중"으로 처리.
* **핵심 흐름**: ① 레시피 목록(`/api/scaling/recipes`) → ② 스케일링(`/api/scaling/predict`, `site_id`를 주면 보정 반영) → ③ 영양사 보정 누적(`/api/calibration/observations`). 스케일링 응답의 재료별 `method`·`confidence`를 UI에 표시할 것.
* **알려진 미구현**: 식단가(원가)·알레르기 배제는 데이터(`recipe_ingredient_map`·`constraints`) 적재 후 활성화 예정.
"""


def create_app() -> FastAPI:
    """FastAPI 앱을 구성해 반환한다(테스트에서도 동일 경로로 생성)."""
    application = FastAPI(
        title="OptiMeal API",
        version="0.1.0",
        description=DESCRIPTION,
        contact={"name": "권성민 (Back)"},
        servers=[{"url": "http://localhost:8000", "description": "개발 서버"}],
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(calibration.router)
    application.include_router(scaling.router)
    application.include_router(nutrition.router)
    application.include_router(menu.router)

    @application.get("/health", tags=["meta"], summary="헬스체크 · 선택적 의존성 상태")
    def health() -> dict:
        """앱 상태와 선택적 의존성(모듈 3·DB) 구성 여부를 보고한다."""
        m3 = config.module3_src_path()
        return {
            "status": "ok",
            "calibration_db": str(config.CALIBRATION_DB),
            "module_3_csp": str(m3) if m3 else None,
            "df_b_csv": config.DF_B_CSV.exists(),
            "coefficients_csv": config.COEFFICIENTS_CSV.exists(),
        }

    return application


app = create_app()