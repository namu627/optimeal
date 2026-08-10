"""
routers/calibration.py
======================
캘리브레이션 API (ADR-008 §2.2) — 본 모듈이 모듈 4 백엔드의 **운영 핵심**이다.

ADR-008로 모듈 2는 '정확 예측 엔진'이 아니라 '영양사 보정을 누적·기억하는 캘리브레이션
도구'로 재정의되었다. 이 라우터가 그 계약을 HTTP로 노출한다:

  POST /observations   보정 1건 누적 → ADR-003 단순이동평균으로 셀 추정 갱신
  GET  /estimate       셀 추정 + 신뢰도 플래그(cold_start/low/high)
  GET  /convergence    회차별 수렴 곡선(APE) — NFR-01(B) 수렴성 지표의 원자료
  GET  /references     CBR 참고 이력 (auto_apply=false, 표시 전용)
  POST /sites, GET /sites  업장 등록·조회

⚠ 수렴은 (레시피×재료) 요구량의 시간적 정상성 **가정**에 의존하며 실데이터에서 아직
   미실증이다(반복 셀 0건, `wiki/analysis/calibration_pilot_realdata_feasibility.md`).
   따라서 /convergence 응답에는 caveat 문구를 항상 동봉한다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import config, schemas
from ..deps import get_store

router = APIRouter(prefix="/api/calibration", tags=["calibration"])

_CONVERGENCE_CAVEAT = (
    "수렴성은 (레시피×재료) 요구량의 시간적 정상성을 가정한다. 실데이터에서 동일 셀의 "
    "반복 관측이 0건이어서 아직 실증되지 않은 가설이다(ADR-008 한계 1). "
    "회차 수가 3 미만이면 추세로 해석하지 말 것."
)


@router.post("/sites", status_code=201, summary="업장 등록")
def create_site(payload: schemas.SiteIn, store=Depends(get_store)) -> dict:
    """업장(조리현장)을 등록한다. 동일 site_id 재호출은 갱신(upsert)."""
    store.add_site(payload.site_id, payload.site_name, payload.site_type)
    return {"site_id": payload.site_id, "created": True}


@router.get("/sites", summary="업장 목록")
def list_sites(store=Depends(get_store)) -> list[dict]:
    """등록된 업장 목록을 반환한다."""
    rows = store.con.execute(
        "SELECT site_id, site_name, site_type FROM cooking_site ORDER BY site_id"
    ).fetchall()
    return [dict(r) for r in rows]


@router.post("/observations", status_code=201, response_model=schemas.ObservationOut,
             summary="영양사 보정 누적 (핵심)")
def record_observation(
    payload: schemas.ObservationIn, store=Depends(get_store)
) -> schemas.ObservationOut:
    """영양사 보정 1건을 append-only 원장에 적재하고 셀 추정을 갱신한다.

    갱신식은 ADR-003 단순 이동평균(EMA 기각). 반환되는 `suggested_g`는 **갱신 후** 추정으로
    다시 계산한 값이므로, 같은 재료를 다음에 조회하면 이 값이 출발점이 된다.

    Args:
        payload: 보정 관측(업장·레시피·재료·N·1인분량·확정량).

    Returns:
        갱신된 셀 추정과 제안량.

    Raises:
        HTTPException(400): base_amount_g ≤ 0 또는 n_target ≤ 0 등 도메인 위반.
    """
    try:
        pred = store.record_observation(
            site_id=payload.site_id,
            recipe_id=payload.recipe_id,
            ingredient_id=payload.ingredient_id,
            n_target=payload.n_target,
            base_amount_g=payload.base_amount_g,
            corrected_g=payload.corrected_g,
            cbr_shown=payload.cbr_shown,
            observed_at=payload.observed_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    est = schemas.EstimateOut(
        site_id=payload.site_id,
        recipe_id=payload.recipe_id,
        ingredient_id=payload.ingredient_id,
        est_ratio=pred.ratio,
        n_obs=pred.n_obs,
        confidence=pred.confidence,
        method=pred.method,
        updated_at=payload.observed_at,
    )
    return schemas.ObservationOut(
        round_no=pred.n_obs,
        estimate=est,
        suggested_g=pred.scaled_g,
        note=f"셀 누적 {pred.n_obs}회 · 신뢰도 {pred.confidence} (ADR-003 단순이동평균 갱신)",
    )


@router.get("/estimate", response_model=schemas.EstimateOut, summary="셀 추정 조회")
def get_estimate(
    site_id: int = Query(..., ge=1),
    recipe_id: int = Query(..., ge=1),
    ingredient_id: int = Query(..., ge=1),
    base_amount_g: float = Query(1.0, gt=0, description="비교용 1인분량(응답 ratio에는 영향 없음)"),
    n_target: int = Query(1, ge=1),
    store=Depends(get_store),
) -> schemas.EstimateOut:
    """셀 추정을 조회한다. 미관측 셀은 cold-start 선형(ratio=1.0)으로 응답한다."""
    pred = store.predict(site_id, recipe_id, ingredient_id, base_amount_g, n_target)
    return schemas.EstimateOut(
        site_id=site_id,
        recipe_id=recipe_id,
        ingredient_id=ingredient_id,
        est_ratio=pred.ratio,
        n_obs=pred.n_obs,
        confidence=pred.confidence,
        method=pred.method,
    )


@router.get("/convergence", response_model=schemas.ConvergenceOut, summary="셀 수렴 곡선")
def get_convergence(
    site_id: int = Query(..., ge=1),
    recipe_id: int = Query(..., ge=1),
    ingredient_id: int = Query(..., ge=1),
    store=Depends(get_store),
) -> schemas.ConvergenceOut:
    """회차별 수렴 곡선(갱신 전 추정 대비 실측 APE)을 반환한다.

    NFR-01(B) '캘리브레이션 수렴성'의 원자료. round_no=1은 cold-start(ratio=1) 대비
    오차이므로 첫 보정의 headroom으로 읽는다.
    """
    pts = store.convergence_curve(site_id, recipe_id, ingredient_id)
    return schemas.ConvergenceOut(
        site_id=site_id,
        recipe_id=recipe_id,
        ingredient_id=ingredient_id,
        points=[schemas.ConvergencePoint(**p) for p in pts],
        n_obs=len(pts),
        caveat=_CONVERGENCE_CAVEAT,
    )


@router.get("/references", response_model=schemas.CbrOut, summary="CBR 참고 이력 표시")
def get_references(
    target_ingredient_id: int = Query(..., ge=1, description="참고를 보고 싶은 재료 id"),
    ingredient_ids: str = Query("", description="신규 레시피 재료 id 목록(쉼표 구분)"),
    top_k: int = Query(3, ge=1, le=10),
    exclude_recipe_id: int | None = Query(None, ge=1),
    store=Depends(get_store),
) -> schemas.CbrOut:
    """재료구성이 유사한 과거 레시피의 실보정 이력을 **표시 전용**으로 조회한다.

    유사도는 재료 id 집합 Jaccard. 자동 적용하지 않으며(`auto_apply=false`), 닻내림
    완화를 위해 출처(site/recipe)와 갱신 시각을 함께 반환한다.

    Raises:
        HTTPException(400): ingredient_ids 가 정수 목록이 아닐 때.
    """
    try:
        ids = {int(v) for v in ingredient_ids.split(",") if v.strip()}
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="ingredient_ids는 쉼표로 구분된 정수 목록이어야 합니다"
        ) from exc

    refs = store.cbr_references(ids, target_ingredient_id, top_k, exclude_recipe_id)
    return schemas.CbrOut(
        target_ingredient_id=target_ingredient_id,
        references=[schemas.CbrReference(**r) for r in refs],
        auto_apply=False,
        disclaimer=config.CBR_DISCLAIMER,
    )
