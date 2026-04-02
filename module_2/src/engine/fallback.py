"""
fallback.py
===========
Fallback 체인 — lookup_scaling_params() 가 None을 반환할 때의 처리.

담당: 남유찬
기준 문서: ADR-002 v3, ADR-003, scaling_coefficients_README.md
전달사항: 남유찬·박미연 전달사항.pdf

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[역할 분담]
  lookup.py (권성민)   → lookup_scaling_params() 정상 조회
  fallback.py (남유찬) → None 반환 시 Fallback 처리
  역변환 (박미연)      → apply_power_law() 수식 실행
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[Fallback 체인 규칙 — ADR-002 v3]
  lookup_scaling_params() 결과:
    ScalingParams → 그대로 역변환 함수에 전달 (이 모듈 불필요)
    None          → resolve_fallback_params() 진입

  Fallback 처리 내용:
    1) b = 0.6163 (B_simple MixedLM 전체 평균, n=607)
    2) a = 카테고리별 B_simple 값 (없으면 전체 평균 6.6735)
    3) confidence = 'low' (ADR-003)
    4) estimation_method = 'category_mean' (ADR-002 v3)

[현재 데이터(v1) 상태 — 주의]
  scaling_coefficients.csv의 ingredient_category가 영어(main/sub/seasoning)로
  저장되어 있어, lookup.py의 한국어 필터로 인해 lookup_df가 비어있음.
  → 현재는 모든 조회가 Fallback으로 처리됨.
  → 한국어 카테고리 행이 CSV에 추가되면 정상 lookup이 동작함.

[역변환 수식 — ADR-001 Option A]
  ratio = Y / (base × N) = a × N^(b-1)
  역변환: Y = a × base × N^b

[MAPE 완료 기준]
  baseline_model_v0.02.py 정식 재실행 기준 Baseline MAPE = 60.75% (n=454, ratio [0.2, 3.0])
  완료 기준: MAPE ≤ 52.30% (업무 지시서 기준, todo.md 2026-04-01 수정)
  검증 데이터: baseline_model_v0.02.py 파이프라인 결과물
               (pairs CSV + 소규모/대규모 Excel 조인 후 ratio 필터 [0.2, 3.0] 적용, n=454)
               df_B.csv는 MixedLM 학습용으로 별도 전처리 구조임 (n=607)
  [이전 기준 — 구 ratio 필터 [0.5, 2.0] 기준: Baseline MAPE = 32.68% (n=342), 완료기준 = 26.14%]
  → ADR 확정 임계값 변경(2026-03-20)으로 위 기준으로 대체됨
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from lookup import ScalingParams, build_lookup_table, lookup_scaling_params


# ---------------------------------------------------------------------------
# 상수 (ADR-002 v3, scaling_coefficients_README.md)
# ---------------------------------------------------------------------------

# 전체 평균 b — B_simple MixedLM, n=607 (scaling_coefficients.csv id=1~3)
_FALLBACK_B: float = 0.6163

# Fallback se_b — B_simple 모델 표준오차
_FALLBACK_SE_B: float = 0.1814

# Fallback 마킹값
_FALLBACK_ESTIMATION_METHOD: str = "category_mean"
# confidence='low' 하드코딩 근거 (ADR-002 v3 전달사항 명시):
#   "파라미터 자체의 se_b 품질"이 아니라 "조회 실패 상황"을 나타내는 신뢰도.
#   se_b 기준 함수(_compute_confidence)와 의도적으로 분리. ADR에 확정 기록 필요.
_FALLBACK_CONFIDENCE: str = "low"
_FALLBACK_PRIORITY: int = 3

# 카테고리별 a 기본값 — B_simple 모델 (scaling_coefficients.csv id=1,2,3)
#   주재료 → main(id=1), 양념류 → seasoning(id=2), 부재료/수분류/유지류 → sub(id=3)
_CATEGORY_A_DEFAULTS: dict[str, float] = {
    "주재료": 7.9677,   # main
    "양념류": 6.3028,   # seasoning
    "부재료": 5.7500,   # sub
    "수분류": 5.7500,   # sub (별도 통계 미산출)
    "유지류": 6.3028,   # seasoning (별도 통계 미산출)
}

# 영어 role → 한국어 5분류 (df_B.csv 등 영어 컬럼 사용 시 내부 변환용)
# baseline_result.csv 실제 role 값('양념', '조미료') 포함
_ROLE_EN_TO_KR: dict[str, str] = {
    "main": "주재료",
    "sub": "부재료",
    "seasoning": "양념류",
    "양념": "양념류",    # baseline_result.csv 실제 값
    "조미료": "양념류",  # baseline_result.csv 실제 값
}

# 미등록 카테고리 최후 a (전체 role 평균: (7.9677+6.3028+5.7500)/3)
_FALLBACK_A_LAST_RESORT: float = 6.6735


# ---------------------------------------------------------------------------
# Fallback 핵심 함수
# ---------------------------------------------------------------------------

def resolve_fallback_params(
    ingredient_category: str,
    lookup_df: Optional[pd.DataFrame] = None,  # 시그니처 일관성용, 현재 미사용
) -> ScalingParams:
    """
    lookup_scaling_params()가 None을 반환했을 때 호출한다.
    카테고리별 a + 전체 평균 b=0.6163 으로 ScalingParams를 생성한다.

    ADR-002 v3 Fallback 규칙:
      1) b = 0.6163 (B_simple MixedLM 전체 평균)
      2) a = 카테고리별 기본값 (없으면 전체 평균 6.6735)
      3) confidence = 'low'
      4) estimation_method = 'category_mean'

    Args:
        ingredient_category: 재료 카테고리.
            한국어 5분류('주재료'|'부재료'|'양념류'|'수분류'|'유지류') 또는
            영어 role('main'|'sub'|'seasoning') 모두 수용.
        lookup_df: 미사용. 시그니처 일관성 유지 목적.

    Returns:
        ScalingParams(estimation_method='category_mean', confidence='low')

    Examples:
        >>> params = resolve_fallback_params('주재료')
        >>> params.power_law_b
        0.6163
        >>> params.power_law_a
        7.9677
        >>> params.confidence
        'low'
        >>> params.estimation_method
        'category_mean'
        >>> # 영어 role도 수용
        >>> params2 = resolve_fallback_params('main')
        >>> params2.power_law_a == params.power_law_a
        True
    """
    # 영어 role 수용: 'main' → '주재료'
    normalized = _ROLE_EN_TO_KR.get(ingredient_category, ingredient_category)
    power_law_a = _CATEGORY_A_DEFAULTS.get(normalized, _FALLBACK_A_LAST_RESORT)

    return ScalingParams(
        power_law_a=power_law_a,
        power_law_b=_FALLBACK_B,
        se_b=_FALLBACK_SE_B,
        estimation_method=_FALLBACK_ESTIMATION_METHOD,
        lookup_priority=_FALLBACK_PRIORITY,
        confidence=_FALLBACK_CONFIDENCE,
    )


def get_scaling_params(
    lookup_df: pd.DataFrame,
    ingredient_category: str,
    group_type: str,
    cooking_method_id: Optional[int] = None,
) -> ScalingParams:
    """
    스케일링 파라미터 통합 조회 — 엔진에서 직접 호출하는 진입점.

    lookup_scaling_params() 성공 → 그대로 반환.
    lookup_scaling_params() 실패(None) → resolve_fallback_params() 호출.
    항상 유효한 ScalingParams를 반환 (절대 None 없음).

    Args:
        lookup_df: build_lookup_table()로 생성된 DataFrame.
        ingredient_category: 재료 카테고리 (한국어 5분류 또는 영어 role 수용).
        group_type: 조리방법 3대분류 ('dry_heat'|'moist_heat'|'no_heat').
        cooking_method_id: 세부 조리방법 ID (영양사 피드백 1순위 조회용, 선택).

    Returns:
        ScalingParams — 항상 유효. None 반환 없음.

    Examples:
        >>> from lookup import build_lookup_table
        >>> lookup_df = build_lookup_table()
        >>> # Fallback 케이스 (미등록 조합)
        >>> params = get_scaling_params(lookup_df, '주재료', 'unknown_method')
        >>> params.estimation_method
        'category_mean'
        >>> params.confidence
        'low'
    """
    params = lookup_scaling_params(
        lookup_df=lookup_df,
        ingredient_category=ingredient_category,
        group_type=group_type,
        cooking_method_id=cooking_method_id,
    )
    if params is not None:
        return params

    return resolve_fallback_params(ingredient_category=ingredient_category)


# ---------------------------------------------------------------------------
# 역변환 수식 (ADR-001 Option A)
# ---------------------------------------------------------------------------

def apply_power_law(
    params: ScalingParams,
    base_amount_g: float,
    target_serving_n: int,
) -> float:
    """
    역변환 수식: Y = a × base_amount_g × N^b  [ADR-001 Option A]

    수식 유도:
      ratio = Y / (base × N) = a × N^(b-1)
      → Y = a × base × N^b

    Args:
        params: ScalingParams (lookup 또는 fallback 결과)
        base_amount_g: 소규모 1인분 재료 투입량 (g, >0)
        target_serving_n: 목표 인원수 N (>0)

    Returns:
        대규모 레시피 재료 투입량 Y (g)

    Raises:
        ValueError: base_amount_g <= 0 또는 target_serving_n <= 0

    Examples:
        >>> # 주재료 Fallback: a=7.9677, b=0.6163, base=10g, N=100
        >>> # Y = 7.9677 × 10 × 100^0.6163 ≈ 1361g
        >>> params = resolve_fallback_params('주재료')
        >>> round(apply_power_law(params, 10.0, 100))
        1361
    """
    if base_amount_g <= 0:
        raise ValueError(f"base_amount_g must be > 0, got {base_amount_g}")
    if target_serving_n <= 0:
        raise ValueError(f"target_serving_n must be > 0, got {target_serving_n}")

    return params.power_law_a * base_amount_g * (target_serving_n ** params.power_law_b)


# ---------------------------------------------------------------------------
# 역변환 수동 검증 헬퍼
# ---------------------------------------------------------------------------

def verify_inverse_transform(
    params: ScalingParams,
    base_amount_g: float,
    target_serving_n: int,
    y_true: float,
    tolerance_pct: float = 50.0,
) -> dict:
    """
    역변환 수식 수동 검증 헬퍼.

    Args:
        params: ScalingParams
        base_amount_g: 소규모 1인분 재료량 (g)
        target_serving_n: 목표 인원수
        y_true: 실제 대규모 재료량 (g)
        tolerance_pct: 허용 오차 (%, 기본 50%)

    Returns:
        {
            'y_pred': float,            # 예측값 (g)
            'y_true': float,            # 실제값 (g)
            'ape_pct': float,           # 절대 퍼센트 오차 (%)
            'pass': bool,               # tolerance_pct 이내 여부
            'estimation_method': str,   # 파라미터 출처
            'confidence': str,          # 신뢰도 플래그
        }
    """
    y_pred = apply_power_law(params, base_amount_g, target_serving_n)
    ape = abs(y_true - y_pred) / max(abs(y_true), 1.0) * 100
    return {
        "y_pred": round(y_pred, 2),
        "y_true": y_true,
        "ape_pct": round(ape, 2),
        "pass": ape <= tolerance_pct,
        "estimation_method": params.estimation_method,
        "confidence": params.confidence,
    }


# ---------------------------------------------------------------------------
# MAPE 계산 (완료 기준 검증용)
# ---------------------------------------------------------------------------

def calc_mape_fallback(
    df: pd.DataFrame,
    lookup_df: pd.DataFrame,
) -> float:
    """
    Fallback 포함 MAPE 계산.

    두 가지 DataFrame 구조 자동 감지:

    [구조 A — df_B.csv (MixedLM 학습용)]
      컬럼: role(영어), group_type(한국어), base(1인분g), N(인원수), Y(대규모g)

    [구조 B — baseline_model 파이프라인 (완료기준 26.14% 검증용)]
      컬럼: role, group_type(영어), base_amount_g, target_serving_size, target_amount_g
      ※ 이 구조 + ratio 필터(0.5~2.0) 적용 결과에서 Baseline=32.68%, 완료기준=26.14%

    Args:
        df: 위 두 구조 중 하나의 DataFrame
        lookup_df: build_lookup_table() 결과

    Returns:
        MAPE (%)
    """
    # 컬럼 구조 자동 감지
    if "base" in df.columns and "N" in df.columns and "Y" in df.columns:
        col_base, col_n, col_y = "base", "N", "Y"
    elif "base_amount_g" in df.columns:
        col_base = "base_amount_g"
        col_n = "target_serving_size"
        col_y = "target_amount_g"
    else:
        raise ValueError(
            "지원하지 않는 DataFrame 구조. "
            "'base'/'N'/'Y' 또는 'base_amount_g'/'target_serving_size'/'target_amount_g' 필요."
        )

    # group_type 한국어 → 영어 변환 (df_B: 건열/습열/비가열, lookup: dry_heat/moist_heat/no_heat)
    _GT_KR_TO_EN = {"건열": "dry_heat", "습열": "moist_heat", "비가열": "no_heat"}

    apes = []
    for _, row in df.iterrows():
        try:
            role_val = str(row.get("role", "sub"))
            ingredient_category = _ROLE_EN_TO_KR.get(role_val, role_val)
            group_type_raw = str(row.get("group_type", ""))
            group_type = _GT_KR_TO_EN.get(group_type_raw, group_type_raw)
            base = float(row[col_base])
            n = int(row[col_n])
            y_true = float(row[col_y])

            if base <= 0 or n <= 0 or y_true <= 0:
                continue

            params = get_scaling_params(lookup_df, ingredient_category, group_type)
            y_pred = apply_power_law(params, base, n)
            # baseline_model.py와 동일한 APE 공식: max(y_true, ε=1.0g) 분모 (Baseline 설계서 4.4절)
            apes.append(abs(y_true - y_pred) / max(y_true, 1.0) * 100)
        except (ValueError, KeyError, TypeError):
            continue

    return float(sum(apes) / len(apes)) if apes else float("nan")


# ---------------------------------------------------------------------------
# 배치 스케일링 (편의 함수)
# ---------------------------------------------------------------------------

def scale_recipe(
    ingredients: list[dict],
    group_type: str,
    target_serving_n: int,
    lookup_df: Optional[pd.DataFrame] = None,
) -> list[dict]:
    """
    소규모 레시피 재료 리스트를 대규모로 변환한다.

    Args:
        ingredients: [
            {'name': str, 'category': str, 'base_amount_g': float},
            ...
        ]
        group_type: 조리방법 3대분류
        target_serving_n: 목표 인원수 N
        lookup_df: build_lookup_table() 결과. None이면 내부에서 생성.

    Returns:
        각 dict에 추가된 필드:
            scaled_amount_g (float): 변환된 투입량 (g)
            confidence (str): 신뢰도 플래그
            estimation_method (str): 파라미터 출처
    """
    if lookup_df is None:
        lookup_df = build_lookup_table()

    results = []
    for ing in ingredients:
        params = get_scaling_params(lookup_df, ing["category"], group_type)
        scaled = apply_power_law(params, ing["base_amount_g"], target_serving_n)
        results.append({
            **ing,
            "scaled_amount_g": round(scaled, 2),
            "confidence": params.confidence,
            "estimation_method": params.estimation_method,
        })
    return results
