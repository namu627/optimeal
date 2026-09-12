"""
routers/scaling.py
==================
스케일링 API (FR-12 `/api/scaling/*`) — ADR-008 재정의판.

`POST /api/scaling/predict` 는 더 이상 '비선형 예측기'가 아니다:
  1) cold-start = 선형(ratio=1) 로 출발점을 만들고
  2) 해당 업장의 캘리브레이션 추정이 있는 재료만 그 추정으로 덮어쓰며
  3) 재료별 근거(method·n_obs·confidence)와 선택적 CBR 참고 이력을 함께 돌려준다.

`GET /api/scaling/coefficients` 는 ADR-006/007 계수(331행)를 **학술 비교군**으로만 노출한다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import config, repositories, schemas
from ..deps import get_store

router = APIRouter(prefix="/api/scaling", tags=["scaling"])


@router.get("/recipes", summary="레시피 목록 (키↔정수 id 매핑 포함)")
def list_recipes(
    q: str = Query("", description="레시피 키 부분일치"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    """스케일링 대상 레시피 목록. 캘리브레이션 API가 쓰는 정수 recipe_id를 함께 준다."""
    return repositories.list_recipes(limit=limit, offset=offset, q=q)


@router.get("/recipes/{recipe_key}", summary="레시피 재료 구성")
def get_recipe(recipe_key: str) -> dict:
    """레시피 1건의 재료 구성(1인분 기준)을 반환한다.

    Raises:
        HTTPException(404): 미등록 레시피 키.
    """
    meta = repositories.get_recipe_meta(recipe_key)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"레시피 없음: {recipe_key}")
    return {**meta, "ingredients": repositories.get_recipe_ingredients(recipe_key)}


@router.post("/predict", response_model=schemas.ScalingResponse,
             summary="레시피 스케일링 (cold-start 선형 + 캘리브레이션 오버레이)")
def predict(payload: schemas.ScalingRequest, store=Depends(get_store)) -> schemas.ScalingResponse:
    """소규모 레시피를 목표 인원수로 변환한다.

    site_id를 주지 않으면 전 재료가 cold-start 선형(`base×N`)이다. 주면 그 업장에 누적된
    보정이 있는 재료만 `calibrated`로 바뀐다 — 어떤 재료가 무슨 근거로 계산됐는지
    응답에서 재료 단위로 구분된다(감사 가능성).

    Args:
        payload: recipe_key·n_target·site_id·CBR 옵션.

    Returns:
        재료별 스케일링 결과 + 근거 + ADR-008 고지.

    Raises:
        HTTPException(404): 미등록 레시피 키.
    """
    meta = repositories.get_recipe_meta(payload.recipe_key)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"레시피 없음: {payload.recipe_key}")

    rows = repositories.get_recipe_ingredients(payload.recipe_key)
    recipe_id = meta["recipe_id"]
    all_ids = {r["ingredient_id"] for r in rows}
    out: list[schemas.ScaledIngredient] = []

    for r in rows:
        item = _scale_one(store, payload, recipe_id, r, all_ids)
        out.append(item)

    calibrated = sum(1 for i in out if i.method == "calibrated")
    return schemas.ScalingResponse(
        recipe_key=payload.recipe_key,
        recipe_id=recipe_id,
        group_type=meta["group_type"],
        cooking_method=meta["cooking_method"],
        n_target=payload.n_target,
        site_id=payload.site_id,
        ingredients=out,
        calibrated_count=calibrated,
        cold_start_count=len(out) - calibrated,
        disclaimer=config.COLD_START_DISCLAIMER,
    )


def _scale_one(store, payload, recipe_id: int, row: dict, all_ids: set) -> schemas.ScaledIngredient:
    """재료 1건을 스케일링한다(캘리브레이션 조회 + 선택적 CBR 동봉).

    Args:
        store: CalibrationStore.
        payload: 원 요청.
        recipe_id: 정수 레시피 id.
        row: repositories.get_recipe_ingredients 의 한 행.
        all_ids: 이 레시피의 전체 재료 id 집합(CBR Jaccard 입력).

    Returns:
        ScaledIngredient.
    """
    base = row["base_amount_g"]
    if payload.site_id is None:
        # 업장 미지정 → 캘리브레이션 조회 없이 순수 cold-start 선형
        scaled, method, ratio = base * payload.n_target, "cold_start_linear", 1.0
        n_obs, conf = 0, "cold_start"
    else:
        pred = store.predict(
            payload.site_id, recipe_id, row["ingredient_id"], base, payload.n_target
        )
        scaled, method, ratio = pred.scaled_g, pred.method, pred.ratio
        n_obs, conf = pred.n_obs, pred.confidence

    refs: list[schemas.CbrReference] = []
    if payload.include_cbr:
        refs = [
            schemas.CbrReference(**x)
            for x in store.cbr_references(
                all_ids, row["ingredient_id"], payload.cbr_top_k, exclude_recipe_id=recipe_id
            )
        ]

    return schemas.ScaledIngredient(
        ingredient_id=row["ingredient_id"],
        ingredient_name=row["ingredient_name"],
        role=row["role"],
        base_amount_g=base,
        scaled_g=round(scaled, 2),
        ratio=round(ratio, 4),
        method=method,
        n_obs=n_obs,
        confidence=conf,
        cbr_references=refs,
    )


@router.get("/coefficients", summary="스케일링 계수 조회 (학술 비교군 — 운영 미적용)")
def get_coefficients(
    group_type: str = Query("", description="3대분류 필터"),
    ingredient_category: str = Query("", description="재료 카테고리 필터"),
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    """`scaling_coefficients.csv`(331행, 수정 금지) 조회.

    ADR-008 이후 이 계수는 운영 스케일링에 **적용되지 않는다**. 논문 비교군·이력 추적용.
    """
    rows = repositories.list_coefficients(group_type, ingredient_category, limit)
    return {
        "count": len(rows),
        "applied_in_production": False,
        "note": (
            "ADR-006/007 산출 계수. ADR-008에서 운영 정확도 주장 철회 → 비교군 보존용. "
            "운영 cold-start는 선형(b=1)이다."
        ),
        "coefficients": rows,
    }
