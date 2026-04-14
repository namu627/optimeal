"""
feedback_update.py
==================
영양사 피드백 기반 파라미터 업데이트 — ADR-003 수식 구현.

기준 문서: ADR-003, DB 스키마 v4.3, feedback_json_schema.md, feedback_rubric_v1.0.md

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[ADR-003 구현 내용]

1. b_feedback 역산
   ratio_adj = (실제 투입량 + Δ_g) / (base_amount × N)
   b_feedback = log(ratio_adj / a_old) / log(N) + 1

2. 1단계 클리핑 (입력): Δ_g IQR 기반 이상치 제거
   IQR = Q3 - Q1
   fence_lower = Q1 - 1.5 × IQR
   fence_upper = Q3 + 1.5 × IQR
   Δ_g_clipped = clip(Δ_g_original, fence_lower, fence_upper)

3. 단순 이동 평균 업데이트
   b_new_raw = (b_old × n_old + b_feedback) / (n_old + 1)

4. 2단계 클리핑 (출력): 도메인 지식 기반 + ε 클리핑
   ε = 2 × se_b  (DB/CSV에서 읽기 — 하드코딩 금지)
   b_clip_lo = max(B_DOMAIN_LOW,  b_old - ε)
   b_clip_hi = min(B_DOMAIN_HIGH, b_old + ε)
   b_new = clip(b_new_raw, b_clip_lo, b_clip_hi)

[멘토링 제안 기각 근거 — ADR-003]
  b_feedback에 직접 ε 클리핑 금지:
  (1) 정상 피드백도 차단 가능
  (2) 업스트림 Δ_g 오류 미탐지
  (3) 이중 방어 부재
  → Δ_g IQR(1단계) + b_new 출력 클리핑(2단계)으로 분리 처리

[DB 감사 추적 컬럼 — 삭제 금지]
  delta_g_original : 클리핑 전 원본 Δ_g
  delta_g_clipped  : 1단계 IQR 클리핑 후 Δ_g
  b_new_raw        : 2단계 클리핑 전 b_new
  is_clipped       : 1·2단계 중 클리핑 발생 여부

[se_b 읽기 위치]
  lookup_scaling_params() → ScalingParams.se_b
  se_b 하드코딩 금지 (ADR-003 명시). DB 미구축 단계에서는 scaling_coefficients.csv 경유.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

import numpy as np


# ---------------------------------------------------------------------------
# 도메인 상수 (물리적 절대 한계)
# ---------------------------------------------------------------------------

# b의 물리적 하한 — ratio가 0에 수렴하는 극단적 경우만 해당
B_DOMAIN_LOW: float = 0.1

# b의 물리적 상한 — 대량 조리 시 재료가 선형보다 2배 이상 필요한 경우는 없음
B_DOMAIN_HIGH: float = 2.0

# IQR 클리핑 계수 (표준값 1.5)
IQR_FACTOR: float = 1.5

# Δ_g → g 변환 시 ml 단위 경고 메시지 트리거 (1:1 변환 후 경고)
_ML_UNITS: frozenset[str] = frozenset(["ml", "mL", "ML"])

# ingredient_adjustments 값 파싱 정규식: "+100g", "-50.5g", "0g", "+200ml"
_ADJ_PATTERN = re.compile(r"^([+-]?\d+(?:\.\d+)?)(g|ml|mL|ML)$")


# ---------------------------------------------------------------------------
# 반환 타입
# ---------------------------------------------------------------------------

@dataclass
class FeedbackResult:
    """
    apply_feedback() 반환값. DB nutritionist_feedback 테이블에 저장할 감사 추적 데이터.

    Attributes:
        b_new: 최종 적용할 업데이트된 b값 (2단계 클리핑 완료)
        b_new_raw: 2단계 클리핑 전 이동 평균 결과
        delta_g_original: 영양사 입력 원본 Δ_g (1단계 클리핑 전)
        delta_g_clipped: 1단계 IQR 클리핑 후 Δ_g. 클리핑 미발생 시 None
        is_clipped: 1·2단계 중 어느 하나라도 클리핑 발생 시 True
        b_feedback: 역산된 b_feedback 값 (중간값, 감사용)
        n_new: 업데이트 후 누적 관측 수
    """
    b_new: float
    b_new_raw: float
    delta_g_original: float
    delta_g_clipped: Optional[float]
    is_clipped: bool
    b_feedback: float
    n_new: int


# ---------------------------------------------------------------------------
# JSON 유효성 검사
# ---------------------------------------------------------------------------

def validate_feedback_json(feedback: dict) -> list[str]:
    """
    피드백 JSON 양식의 유효성을 검사하고 오류 목록을 반환한다.

    feedback_json_schema.md의 유효성 검사 규칙을 코드로 구현.
    오류가 없으면 빈 리스트 반환.

    Args:
        feedback: feedback_json_schema.md 형식의 단건 피드백 dict

    Returns:
        오류 메시지 리스트. 빈 리스트면 유효.

    Examples:
        >>> f = {
        ...     "feedback_meta": {"recipe_id": 1, "feedback_round": 1,
        ...                       "nutritionist_name": "홍", "nutritionist_id": "N001",
        ...                       "feedback_date": "2026-05-04"},
        ...     "rubric_scores": {"taste_seasoning": 4, "cooking_feasibility": 5,
        ...                       "field_applicability": 4},
        ...     "ingredient_adjustments": {"고추장": "-100g"},
        ...     "detailed_comments": ""
        ... }
        >>> validate_feedback_json(f)
        []
    """
    errors: list[str] = []

    # 최상위 필수 키
    required_top = {"feedback_meta", "rubric_scores", "ingredient_adjustments"}
    missing_top = required_top - set(feedback.keys())
    if missing_top:
        errors.append(f"필수 최상위 키 누락: {missing_top}")
        return errors  # 구조 자체 없으면 이후 검사 불가

    meta = feedback.get("feedback_meta", {})
    rubric = feedback.get("rubric_scores", {})
    adjustments = feedback.get("ingredient_adjustments", {})

    # feedback_meta 필수 필드
    for field in ["recipe_id", "feedback_round", "nutritionist_name", "nutritionist_id", "feedback_date"]:
        if field not in meta:
            errors.append(f"feedback_meta.{field} 누락")

    # feedback_round 범위
    if "feedback_round" in meta:
        if meta["feedback_round"] not in {1, 2, 3, 4, 5}:
            errors.append(f"feedback_round는 1~5 정수여야 함, 현재: {meta['feedback_round']}")

    # 날짜 형식
    if "feedback_date" in meta:
        date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        if not date_pattern.match(str(meta["feedback_date"])):
            errors.append(f"feedback_date 형식 오류 (YYYY-MM-DD 필요): {meta['feedback_date']}")

    # 루브릭 점수
    rubric_fields = ["taste_seasoning", "cooking_feasibility", "field_applicability"]
    for field in rubric_fields:
        if field not in rubric:
            errors.append(f"rubric_scores.{field} 누락")
        elif rubric[field] not in {1, 2, 3, 4, 5}:
            errors.append(f"rubric_scores.{field}는 1~5 정수여야 함, 현재: {rubric[field]}")

    # ingredient_adjustments 값 형식
    for ingredient, value in adjustments.items():
        m = _ADJ_PATTERN.match(str(value))
        if not m:
            errors.append(
                f"ingredient_adjustments['{ingredient}'] 형식 오류: '{value}' "
                "(예: '+100g', '-50g', '0g', '+200ml')"
            )
        elif m.group(2) in _ML_UNITS:
            errors.append(
                f"경고: ingredient_adjustments['{ingredient}']가 ml 단위 — "
                "unit_conversion 테이블로 g 변환 필요"
            )

    return errors


# ---------------------------------------------------------------------------
# Δ_g 파싱
# ---------------------------------------------------------------------------

def parse_delta_g(value_str: str) -> float:
    """
    ingredient_adjustments 값 문자열을 Δ_g(float, g 기준)로 파싱한다.

    ml 단위는 1:1 변환(밀도 1g/ml 가정). 정확한 변환은 unit_conversion 테이블 사용 필요.

    Args:
        value_str: "+100g", "-50g", "0g", "+200ml" 등

    Returns:
        Δ_g float (g 단위)

    Raises:
        ValueError: 형식 불일치

    Examples:
        >>> parse_delta_g("+100g")
        100.0
        >>> parse_delta_g("-50.5g")
        -50.5
        >>> parse_delta_g("0g")
        0.0
    """
    m = _ADJ_PATTERN.match(str(value_str).strip())
    if not m:
        raise ValueError(f"Δ_g 파싱 실패: '{value_str}'. 형식: '+100g', '-50g', '0g'")
    return float(m.group(1))


# ---------------------------------------------------------------------------
# 1단계: Δ_g IQR 클리핑
# ---------------------------------------------------------------------------

def clip_delta_g_iqr(
    delta_g: float,
    delta_g_history: list[float],
) -> tuple[float, bool]:
    """
    1단계 클리핑: Δ_g를 이전 피드백 이력의 IQR 기반 범위로 제한한다.

    이력이 3개 미만이면 클리핑 없이 원본 반환 (IQR 계산 불안정).

    Args:
        delta_g: 영양사 입력 Δ_g 원본 (g)
        delta_g_history: 동일 (group_type × ingredient_category) 셀의 이전 Δ_g 이력

    Returns:
        (clipped_delta_g, was_clipped)
        clipped_delta_g: 클리핑 후 Δ_g (클리핑 없으면 원본)
        was_clipped: 클리핑 발생 여부

    Examples:
        >>> # 이력 충분: 이상치 클리핑
        >>> clip_delta_g_iqr(500.0, [-20.0, -10.0, 0.0, 10.0, 20.0])
        (42.5, True)
        >>> # 이력 부족: 클리핑 없음
        >>> clip_delta_g_iqr(500.0, [])
        (500.0, False)
    """
    # 이력 부족 — IQR 계산 불안정, 클리핑 생략
    if len(delta_g_history) < 3:
        return delta_g, False

    arr = np.array(delta_g_history, dtype=float)
    q1 = float(np.percentile(arr, 25))
    q3 = float(np.percentile(arr, 75))
    iqr = q3 - q1

    fence_lower = q1 - IQR_FACTOR * iqr
    fence_upper = q3 + IQR_FACTOR * iqr

    clipped = max(fence_lower, min(fence_upper, delta_g))
    was_clipped = abs(clipped - delta_g) > 1e-9

    return clipped, was_clipped


# ---------------------------------------------------------------------------
# b_feedback 역산
# ---------------------------------------------------------------------------

def compute_b_feedback(
    actual_amount_g: float,
    delta_g: float,
    base_amount_g: float,
    target_serving_n: int,
    a_old: float,
) -> float:
    """
    영양사 피드백(Δ_g)으로부터 b_feedback을 역산한다. [ADR-003]

    역산 수식:
      ratio_adj = (실제 투입량 + Δ_g) / (base_amount × N)
      b_feedback = log(ratio_adj / a_old) / log(N) + 1

    Args:
        actual_amount_g: 엔진이 생성한 실제 대규모 투입량 (g, 역변환 Y)
        delta_g: 1단계 클리핑 후 Δ_g (g). 양수=더 필요, 음수=덜 필요
        base_amount_g: 소규모 1인분 기준 투입량 (g)
        target_serving_n: 목표 인원수 N
        a_old: 현재 적용 중인 멱함수 계수 a

    Returns:
        b_feedback (float)

    Raises:
        ValueError: 인수 범위 위반 또는 N=1(log 0)

    Examples:
        >>> # 실제량=200g, Δ=-50g(줄임), base=2g, N=100, a=7.9677
        >>> # ratio_adj = (200-50)/(2×100) = 0.75
        >>> # b = log(0.75/7.9677)/log(100)+1 ≈ 0.3
        >>> round(compute_b_feedback(200.0, -50.0, 2.0, 100, 7.9677), 3)
        0.298
    """
    if base_amount_g <= 0:
        raise ValueError(f"base_amount_g > 0 필요, 현재: {base_amount_g}")
    if target_serving_n <= 1:
        raise ValueError(
            f"target_serving_n > 1 필요 (N=1이면 log(N)=0, b 역산 불가), 현재: {target_serving_n}"
        )
    if a_old <= 0:
        raise ValueError(f"a_old > 0 필요, 현재: {a_old}")

    adjusted_amount = actual_amount_g + delta_g
    if adjusted_amount <= 0:
        raise ValueError(
            f"조정 후 투입량(actual + Δ_g = {adjusted_amount:.2f}g) ≤ 0. "
            "Δ_g가 너무 큰 음수값일 수 있음. 1단계 IQR 클리핑 결과를 확인하라."
        )

    ratio_adj = adjusted_amount / (base_amount_g * target_serving_n)
    if ratio_adj <= 0:
        raise ValueError(f"ratio_adj ≤ 0 불가: {ratio_adj:.6f}")

    # b_feedback = log(ratio_adj / a_old) / log(N) + 1
    b_feedback = math.log(ratio_adj / a_old) / math.log(target_serving_n) + 1
    return b_feedback


# ---------------------------------------------------------------------------
# 이동 평균 업데이트
# ---------------------------------------------------------------------------

def moving_average_update(
    b_old: float,
    n_old: int,
    b_feedback: float,
) -> float:
    """
    단순 이동 평균으로 b를 업데이트한다. [ADR-003]

    수식: b_new_raw = (b_old × n_old + b_feedback) / (n_old + 1)

    EMA(지수이동평균) 대신 단순 이동 평균 채택 이유 [ADR-003]:
    - α 선택 정당화 어려움
    - 피드백 5회 고정 소규모 데이터에 부적합

    Args:
        b_old: 현재 b값
        n_old: 현재까지의 누적 관측(피드백) 수
        b_feedback: 역산된 b_feedback

    Returns:
        b_new_raw (2단계 클리핑 전)

    Examples:
        >>> moving_average_update(0.6163, 0, 0.50)
        0.50
        >>> round(moving_average_update(0.6163, 1, 0.50), 4)
        0.5582
    """
    if n_old < 0:
        raise ValueError(f"n_old ≥ 0 필요, 현재: {n_old}")

    return (b_old * n_old + b_feedback) / (n_old + 1)


# ---------------------------------------------------------------------------
# 2단계: b 출력 클리핑
# ---------------------------------------------------------------------------

def clip_b_output(
    b_new_raw: float,
    b_old: float,
    se_b: float,
) -> tuple[float, bool]:
    """
    2단계 클리핑: b_new_raw를 도메인 지식 기반 범위로 제한한다. [ADR-003]

    클리핑 범위:
      ε = 2 × se_b  (ADR-003. se_b는 ScalingParams.se_b에서 읽어야 함, 하드코딩 금지)
      lower = max(B_DOMAIN_LOW,  b_old - ε)
      upper = min(B_DOMAIN_HIGH, b_old + ε)
      b_new = clip(b_new_raw, lower, upper)

    멘토링 제안(b_feedback에 직접 ε 적용)을 기각하고 b_new 출력에 적용한다. [ADR-003]

    Args:
        b_new_raw: 이동 평균 업데이트 결과 (클리핑 전)
        b_old: 업데이트 이전 b값
        se_b: MixedLM 표준오차 SE(b). 반드시 DB/CSV에서 읽은 값을 전달할 것.

    Returns:
        (b_new, was_clipped)
        b_new: 클리핑 완료된 최종 b
        was_clipped: 클리핑 발생 여부

    Examples:
        >>> # se_b=0.1814 → ε=0.3628. b_old=0.6163
        >>> # 허용 범위: [max(0.1, 0.6163-0.3628), min(2.0, 0.6163+0.3628)]
        >>> #           = [0.2535, 0.9791]
        >>> # b_new_raw=0.25 → 클리핑 → 0.2535
        >>> b_new, clipped = clip_b_output(0.25, 0.6163, 0.1814)
        >>> clipped
        True
        >>> round(b_new, 4)
        0.2535
        >>> # b_new_raw=0.70 → 허용 범위 내 → 클리핑 없음
        >>> b_new2, clipped2 = clip_b_output(0.70, 0.6163, 0.1814)
        >>> clipped2
        False
        >>> b_new2
        0.7
    """
    if se_b <= 0:
        raise ValueError(f"se_b > 0 필요, 현재: {se_b}. DB/CSV에서 올바른 값을 전달하라.")

    epsilon = 2.0 * se_b
    lower = max(B_DOMAIN_LOW, b_old - epsilon)
    upper = min(B_DOMAIN_HIGH, b_old + epsilon)

    b_new = max(lower, min(upper, b_new_raw))
    was_clipped = abs(b_new - b_new_raw) > 1e-9

    return b_new, was_clipped


# ---------------------------------------------------------------------------
# 통합 함수
# ---------------------------------------------------------------------------

def apply_feedback(
    actual_amount_g: float,
    delta_g_original: float,
    base_amount_g: float,
    target_serving_n: int,
    b_old: float,
    n_old: int,
    a_old: float,
    se_b: float,
    delta_g_history: Optional[list[float]] = None,
) -> FeedbackResult:
    """
    ADR-003 파라미터 업데이트 전체 파이프라인을 실행한다.

    실행 순서:
      1. Δ_g IQR 클리핑 (1단계 입력 클리핑)
      2. b_feedback 역산
      3. 이동 평균 업데이트 → b_new_raw
      4. b 출력 클리핑 (2단계 출력 클리핑)
      5. FeedbackResult 반환 (DB 감사 추적 컬럼 포함)

    Args:
        actual_amount_g: 엔진이 생성한 실제 대규모 투입량 (g)
        delta_g_original: 영양사 입력 원본 Δ_g (g, 1단계 클리핑 전)
        base_amount_g: 소규모 1인분 기준 투입량 (g)
        target_serving_n: 목표 인원수 N
        b_old: 현재 b값 (ScalingParams.power_law_b)
        n_old: 현재까지의 누적 피드백 수
        a_old: 현재 멱함수 계수 a (ScalingParams.power_law_a)
        se_b: MixedLM SE(b). ScalingParams.se_b에서 읽어야 함. 하드코딩 금지.
        delta_g_history: 동일 셀 이전 Δ_g 이력 (1단계 IQR 클리핑용).
                         None 또는 빈 리스트면 클리핑 생략.

    Returns:
        FeedbackResult — DB nutritionist_feedback 컬럼에 그대로 저장 가능

    Examples:
        >>> result = apply_feedback(
        ...     actual_amount_g=200.0,
        ...     delta_g_original=-50.0,
        ...     base_amount_g=2.0,
        ...     target_serving_n=100,
        ...     b_old=0.6163,
        ...     n_old=0,
        ...     a_old=6.3028,
        ...     se_b=0.1814,
        ... )
        >>> 0.1 <= result.b_new <= 2.0
        True
        >>> result.delta_g_clipped is None  # 이력 없어서 1단계 클리핑 미발생
        True
    """
    if delta_g_history is None:
        delta_g_history = []

    # --- 1단계: Δ_g IQR 클리핑 ---
    delta_g_clipped_val, stage1_clipped = clip_delta_g_iqr(delta_g_original, delta_g_history)
    delta_g_for_calc = delta_g_clipped_val
    delta_g_clipped_out = delta_g_clipped_val if stage1_clipped else None

    # --- b_feedback 역산 ---
    b_feedback = compute_b_feedback(
        actual_amount_g=actual_amount_g,
        delta_g=delta_g_for_calc,
        base_amount_g=base_amount_g,
        target_serving_n=target_serving_n,
        a_old=a_old,
    )

    # --- 이동 평균 업데이트 ---
    b_new_raw = moving_average_update(b_old, n_old, b_feedback)

    # --- 2단계: b 출력 클리핑 ---
    b_new, stage2_clipped = clip_b_output(b_new_raw, b_old, se_b)

    return FeedbackResult(
        b_new=b_new,
        b_new_raw=b_new_raw,
        delta_g_original=delta_g_original,
        delta_g_clipped=delta_g_clipped_out,
        is_clipped=stage1_clipped or stage2_clipped,
        b_feedback=b_feedback,
        n_new=n_old + 1,
    )


# ---------------------------------------------------------------------------
# rubric_avg 계산 헬퍼
# ---------------------------------------------------------------------------

def compute_rubric_avg(
    taste_seasoning: int,
    cooking_feasibility: int,
    field_applicability: int,
) -> float:
    """
    루브릭 3항목 평균 및 전체 판정을 계산한다.

    Args:
        taste_seasoning: 맛·간 적절성 (1~5)
        cooking_feasibility: 조리 실현 가능성 (1~5)
        field_applicability: 대량 조리 현장 적합성 (1~5)

    Returns:
        rubric_avg (소수 2자리 round)

    Examples:
        >>> compute_rubric_avg(4, 5, 4)
        4.33
        >>> compute_rubric_avg(3, 3, 3)
        3.0
    """
    for name, val in [
        ("taste_seasoning", taste_seasoning),
        ("cooking_feasibility", cooking_feasibility),
        ("field_applicability", field_applicability),
    ]:
        if val not in {1, 2, 3, 4, 5}:
            raise ValueError(f"{name}은 1~5 정수여야 함, 현재: {val}")

    avg = (taste_seasoning + cooking_feasibility + field_applicability) / 3
    return round(avg, 2)


def get_overall_rating(rubric_avg: float) -> str:
    """
    rubric_avg로부터 overall_rating을 결정한다.

    기준 (feedback_rubric_v1.0.md):
      ≥ 4.00  → '상용가능'
      ≥ 2.50  → '수정필요'
      < 2.50  → '부적합'

    Args:
        rubric_avg: compute_rubric_avg() 결과

    Returns:
        '상용가능' | '수정필요' | '부적합'

    Examples:
        >>> get_overall_rating(4.33)
        '상용가능'
        >>> get_overall_rating(3.0)
        '수정필요'
        >>> get_overall_rating(2.0)
        '부적합'
    """
    if rubric_avg >= 4.00:
        return "상용가능"
    if rubric_avg >= 2.50:
        return "수정필요"
    return "부적합"
