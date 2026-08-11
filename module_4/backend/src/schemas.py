"""
schemas.py
==========
요청/응답 스키마 (pydantic v2).

ADR-008 정직성을 **타입으로 강제**하는 것이 이 파일의 목적이다:
  · 스케일링 응답의 모든 재료는 `method`(cold_start_linear|calibrated)와 `confidence`를 반드시 갖는다.
  · CBR 참고 이력은 `auto_apply: false`를 응답에 항상 포함한다(자동 적용 금지).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Method = Literal["cold_start_linear", "calibrated"]
Confidence = Literal["cold_start", "low", "high"]


# ---------------------------------------------------------------------------
# 캘리브레이션
# ---------------------------------------------------------------------------
class SiteIn(BaseModel):
    """업장(조리현장) 등록 요청. user_group(급식 대상)과 별개 개념."""
    site_id: int = Field(..., ge=1, description="업장 id")
    site_name: str = Field("", max_length=100)
    site_type: str = Field("", max_length=50, description="예: 학교/노인복지원/산업체")


class ObservationIn(BaseModel):
    """영양사 보정 1건(누적 대상). corrected_g가 '관측된 정답'이다."""
    site_id: int = Field(..., ge=1)
    recipe_id: int = Field(..., ge=1)
    ingredient_id: int = Field(..., ge=1)
    n_target: int = Field(..., ge=1, description="목표 인원수 N")
    base_amount_g: float = Field(..., gt=0, description="1인분 투입량 (g)")
    corrected_g: float = Field(..., ge=0, description="영양사 최종 확정 투입량 (g)")
    cbr_shown: bool = Field(False, description="CBR 참고 이력을 노출한 상태였는지(파일럿 A/B)")
    observed_at: Optional[str] = Field(None, description="ISO 시각. 미지정 시 서버 UTC")


class EstimateOut(BaseModel):
    """셀(업장×레시피×재료) 추정 상태."""
    site_id: int
    recipe_id: int
    ingredient_id: int
    est_ratio: float = Field(..., description="누적 추정 ratio (cold-start=1.0)")
    n_obs: int = Field(..., description="누적 보정 관측 수")
    confidence: Confidence
    method: Method
    updated_at: Optional[str] = None


class ObservationOut(BaseModel):
    """보정 적재 결과 — 갱신된 셀 추정과 그 추정으로 재계산한 제안량."""
    accepted: bool = True
    round_no: int = Field(..., description="이 셀에서 몇 번째 보정인지")
    estimate: EstimateOut
    suggested_g: float = Field(..., description="갱신된 추정으로 계산한 N인분 제안량 (g)")
    note: str


class ConvergencePoint(BaseModel):
    """수렴 곡선 1점 — 갱신 전 추정으로 다음 관측을 맞힌 오차."""
    round_no: int
    obs_ratio: float
    prior_ratio: float
    ape_pct: float = Field(..., description="|prior−obs|/obs ×100 (%)")


class ConvergenceOut(BaseModel):
    """셀 수렴 곡선 + 해석 주의."""
    site_id: int
    recipe_id: int
    ingredient_id: int
    points: list[ConvergencePoint]
    n_obs: int
    caveat: str


class CbrReference(BaseModel):
    """CBR 참고 이력 1건(표시 전용)."""
    site_id: int
    recipe_id: int
    jaccard: float = Field(..., description="재료구성 Jaccard 유사도")
    est_ratio: float
    n_obs: int
    updated_at: Optional[str] = None


class CbrOut(BaseModel):
    """CBR 참고 이력 응답. auto_apply는 항상 False."""
    target_ingredient_id: int
    references: list[CbrReference]
    auto_apply: bool = False
    disclaimer: str


# ---------------------------------------------------------------------------
# 스케일링 (FR-12 /api/scaling/predict — ADR-008 재정의판)
# ---------------------------------------------------------------------------
class ScalingRequest(BaseModel):
    """레시피 스케일링 요청."""
    recipe_key: str = Field(..., description="df_B.csv small_recipe_id (예: A1034)")
    n_target: int = Field(..., ge=1, le=5000, description="목표 인원수 N")
    site_id: Optional[int] = Field(None, ge=1, description="업장 id. 주면 그 업장 보정을 적용")
    include_cbr: bool = Field(False, description="재료별 CBR 참고 이력 동봉 여부")
    cbr_top_k: int = Field(3, ge=1, le=10)


class ScaledIngredient(BaseModel):
    """재료별 스케일링 결과 — 근거·신뢰도를 항상 동반한다."""
    ingredient_id: int
    ingredient_name: str
    role: str
    base_amount_g: float
    scaled_g: float
    ratio: float
    method: Method
    n_obs: int
    confidence: Confidence
    cbr_references: list[CbrReference] = Field(default_factory=list)


class ScalingResponse(BaseModel):
    """스케일링 응답."""
    recipe_key: str
    recipe_id: int
    group_type: str
    cooking_method: str
    n_target: int
    site_id: Optional[int]
    ingredients: list[ScaledIngredient]
    calibrated_count: int = Field(..., description="캘리브레이션이 적용된 재료 수")
    cold_start_count: int = Field(..., description="cold-start 선형으로 계산된 재료 수")
    disclaimer: str


# ---------------------------------------------------------------------------
# 식단 생성 (모듈 3 위임)
# ---------------------------------------------------------------------------
class MenuGenerateRequest(BaseModel):
    """식단 생성 요청(모듈 3 CSP로 위임)."""
    days: int = Field(7, ge=1, le=31)
    month: Optional[int] = Field(None, ge=1, le=12, description="제철 기준 월")
    target_kcal_per_day: float = Field(2000.0, gt=0)
    kcal_tolerance: float = Field(0.10, ge=0, le=0.5)
    budget_limit_per_person: Optional[float] = Field(3500.0, gt=0)
    sodium_max_mg_per_day: Optional[float] = Field(
        2000.0, gt=0,
        description="1일 나트륨 상한 mg (H-2e Hard 제약). null이면 미적용. "
                    "기본 2000=WHO 성인 권고. 저염 대상은 더 낮게 지정.",
    )
    excluded_allergens: list[str] = Field(default_factory=list)
    solver_time_limit: float = Field(30.0, gt=0, le=120)
    with_alternatives: bool = Field(
        False, description="알레르기 그룹별 대체식(공통식+대체식 트랙) 동반 산출"
    )
    allergy_groups: list[dict] = Field(
        default_factory=list,
        description='[{"label":"우유알레르기","allergens":["우유"],"count":5}]',
    )
