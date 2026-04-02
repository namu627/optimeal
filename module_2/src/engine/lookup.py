"""
lookup.py
=========
v_scaling_lookup 뷰 조회 함수.

DB 스키마 v4.3의 v_scaling_lookup 뷰 로직을 pandas로 재현한다.
DB 미구축 단계에서 scaling_coefficients.csv를 뷰 대용으로 사용한다.

룩업 우선순위 [ADR-002 v3, v_scaling_lookup 뷰 주석과 동일]:
  1순위: (cooking_method_id × ingredient_category) — estimation_method='nutritionist_feedback'
  2순위: (group_type × ingredient_category)        — estimation_method='mixedlm' | 'curve_fit'
  3순위: ingredient_category만                     — category_mean (뷰에 해당 행이 존재 시)

엔진은 ORDER BY lookup_priority ASC LIMIT 1 패턴으로 조회한다.
1·2순위 조회 실패 시 None 반환 → Fallback 체인(fallback.py 담당, 남유찬)이 처리.

신뢰도 플래그 기준 (ADR-003, scaling_coefficients_README.md):
  high   — se_b ≤ 0.2
  medium — 0.2 < se_b ≤ 0.4
  low    — se_b > 0.4

담당: 권성민
기준 문서: ADR-002 v3, ADR-003, DB 스키마 v4.3
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

# 엔진 실제 룩업 대상: ingredient_category 한국어 5분류 (README id 10~24)
# id 1~9 (영어 role 기반 참조용 행)는 제외
_VALID_CATEGORIES: frozenset[str] = frozenset(
    ["주재료", "부재료", "양념류", "수분류", "유지류"]
)

# lookup_priority 값 정의 (v_scaling_lookup 뷰 CASE WHEN 동일)
_PRIORITY_NUTRITIONIST: int = 1   # nutritionist_feedback + cooking_method_id 일치
_PRIORITY_MIXEDLM: int = 2        # mixedlm / curve_fit + group_type 일치
_PRIORITY_CATEGORY_MEAN: int = 3  # category_mean fallback

# 신뢰도 플래그 임계값 (ADR-003)
_SE_HIGH_MAX: float = 0.2
_SE_MEDIUM_MAX: float = 0.4


# ---------------------------------------------------------------------------
# 반환 타입
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScalingParams:
    """
    v_scaling_lookup 단일 행 조회 결과.

    역변환 함수(박미연 담당)에서 power_law_a, power_law_b를 직접 사용.
    Fallback 체인(남유찬 담당)에서 None 대신 채워진 ScalingParams를 반환.

    Attributes:
        power_law_a: 멱함수 계수 a. 역변환 수식: Y = a × base × N^b
        power_law_b: 멱함수 지수 b. b<1=규모의 경제, b=1=선형, b>1=규모 초과 증가
        se_b: b의 표준오차. 출력 클리핑 임계값 ε = 2×se_b (ADR-003)
        estimation_method: 추정 방법 ('mixedlm'|'curve_fit'|'nutritionist_feedback'|'category_mean')
        lookup_priority: 조회 우선순위 (1/2/3)
        confidence: 신뢰도 플래그 ('high'|'medium'|'low'), ADR-003 se_b 기준
    """
    power_law_a: float
    power_law_b: float
    se_b: float
    estimation_method: str
    lookup_priority: int
    confidence: str


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _compute_lookup_priority(df: pd.DataFrame) -> pd.Series:
    """
    v_scaling_lookup 뷰의 CASE WHEN lookup_priority 로직을 pandas로 재현.

    CSV에 lookup_priority 컬럼이 이미 존재하더라도 재계산하여 덮어쓴다.
    데이터 변경(CSV 수동 편집 등) 시 일관성을 보장하기 위함.

    스키마 v4.3 기준 CASE WHEN:
      WHEN cooking_method_id IS NOT NULL AND estimation_method='nutritionist_feedback' → 1
      WHEN group_type IS NOT NULL AND estimation_method IN ('mixedlm','curve_fit')    → 2
      ELSE                                                                              → 3

    Args:
        df: scaling_coefficients DataFrame (cooking_method_id, estimation_method,
            group_type 컬럼 포함)

    Returns:
        lookup_priority Series (int, 인덱스 df와 동일)
    """
    priority = pd.Series(_PRIORITY_CATEGORY_MEAN, index=df.index, dtype=int)

    # 2순위: group_type 존재 + mixedlm/curve_fit
    has_group = df["group_type"].notna() & (df["group_type"].astype(str).str.strip() != "")
    is_stat = df["estimation_method"].isin(["mixedlm", "curve_fit"])
    priority[has_group & is_stat] = _PRIORITY_MIXEDLM

    # 1순위: cooking_method_id 존재 + nutritionist_feedback (2순위보다 늦게 처리하여 덮어씀)
    cmi_col = df.get("cooking_method_id", pd.Series(pd.NA, index=df.index))
    has_method_id = cmi_col.notna()
    is_feedback = df["estimation_method"] == "nutritionist_feedback"
    priority[has_method_id & is_feedback] = _PRIORITY_NUTRITIONIST

    return priority


def _compute_confidence(se_b: float) -> str:
    """
    ADR-003 기준 신뢰도 플래그.

    Args:
        se_b: b의 표준오차

    Returns:
        'high' (se_b ≤ 0.2) | 'medium' (0.2 < se_b ≤ 0.4) | 'low' (se_b > 0.4)
    """
    if se_b <= _SE_HIGH_MAX:
        return "high"
    if se_b <= _SE_MEDIUM_MAX:
        return "medium"
    return "low"


def _row_to_params(row: pd.Series) -> ScalingParams:
    """
    DataFrame 단일 행 → ScalingParams 변환.

    Args:
        row: lookup_df의 단일 행

    Returns:
        ScalingParams 인스턴스
    """
    se_b = float(row.get("se_b") or 0.0)
    return ScalingParams(
        power_law_a=float(row["power_law_a"]),
        power_law_b=float(row["power_law_b"]),
        se_b=se_b,
        estimation_method=str(row["estimation_method"]),
        lookup_priority=int(row["lookup_priority"]),
        confidence=_compute_confidence(se_b),
    )


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

def build_lookup_table(csv_path: Optional[str | Path] = None) -> pd.DataFrame:
    """
    scaling_coefficients.csv를 로드하여 v_scaling_lookup 뷰 형태 DataFrame을 반환.

    처리 내용:
      - is_active=True 필터 (컬럼 없으면 전 행 포함)
      - cooking_method_id 컬럼 없으면 pd.NA로 추가 (현재 CSV 미포함)
      - lookup_priority 재계산 (CSV 기존값 덮어씀, 일관성 보장)
      - ingredient_category 한국어 5분류 행만 포함 (id 1~9 영어 참조용 행 제외)

    Args:
        csv_path: scaling_coefficients.csv 경로.
                  None이면 이 파일과 같은 디렉터리의 기본 경로 사용.

    Returns:
        lookup_priority 컬럼이 포함된 조회 준비 완료 DataFrame.
        인덱스는 reset_index(drop=True).
    """
    if csv_path is None:
        csv_path = Path(__file__).parent / "scaling_coefficients.csv"

    df = pd.read_csv(csv_path)

    # is_active 필터
    if "is_active" in df.columns:
        df = df[df["is_active"] == True].copy()  # noqa: E712
    else:
        df = df.copy()

    # cooking_method_id 컬럼 보장 (현재 CSV에 없는 경우 대비)
    if "cooking_method_id" not in df.columns:
        df["cooking_method_id"] = pd.NA

    # lookup_priority 재계산
    df["lookup_priority"] = _compute_lookup_priority(df)

    # 엔진 룩업 대상: ingredient_category 한국어 5분류만 포함
    # (scaling_coefficients_README.md: id 10~24가 DB INSERT 및 엔진 룩업 대상)
    df = df[df["ingredient_category"].isin(_VALID_CATEGORIES)].copy()

    return df.reset_index(drop=True)


def lookup_scaling_params(
    lookup_df: pd.DataFrame,
    ingredient_category: str,
    group_type: str,
    cooking_method_id: Optional[int] = None,
) -> Optional[ScalingParams]:
    """
    v_scaling_lookup에서 (ingredient_category, group_type) 기준으로
    lookup_priority ASC 최우선 레코드를 조회한다.

    ADR-002 v3 룩업 우선순위:
      1순위 — nutritionist_feedback: (cooking_method_id × ingredient_category) 일치
      2순위 — mixedlm/curve_fit:     (group_type × ingredient_category) 일치
      3순위 — category_mean:          ingredient_category만 일치 (해당 행 존재 시)

    현재 데이터(v1) 상태:
      - 1순위 행 없음 (영양사 피드백 아직 미수집)
      - 2순위 행 15개 (dry_heat/moist_heat/no_heat × 5분류)
      - 3순위 행 없음

    조회 실패 시 None 반환 → Fallback 체인(fallback.py)에서 처리.

    Args:
        lookup_df: build_lookup_table()로 생성된 DataFrame.
        ingredient_category: 재료 카테고리 ('주재료'|'부재료'|'양념류'|'수분류'|'유지류').
        group_type: 조리방법 3대분류 ('dry_heat'|'moist_heat'|'no_heat').
        cooking_method_id: 세부 조리방법 ID. 제공 시 1순위 nutritionist_feedback 조회.
                           None이면 1순위 조회 생략.

    Returns:
        ScalingParams (조회 성공) 또는 None (Fallback 체인으로 위임).
    """
    # 재료 카테고리 필터
    by_category = lookup_df[lookup_df["ingredient_category"] == ingredient_category]
    if by_category.empty:
        return None

    # 1순위: nutritionist_feedback — cooking_method_id × ingredient_category
    if cooking_method_id is not None:
        p1 = by_category[
            (by_category["cooking_method_id"] == cooking_method_id)
            & (by_category["estimation_method"] == "nutritionist_feedback")
        ]
        if not p1.empty:
            row = p1.sort_values("lookup_priority").iloc[0]
            return _row_to_params(row)

    # 2순위: mixedlm / curve_fit — group_type × ingredient_category
    p2 = by_category[
        (by_category["group_type"] == group_type)
        & (by_category["estimation_method"].isin(["mixedlm", "curve_fit"]))
    ]
    if not p2.empty:
        row = p2.sort_values("lookup_priority").iloc[0]
        return _row_to_params(row)

    # 3순위: category_mean — ingredient_category만 일치 (lookup_priority==3 행이 있는 경우)
    p3 = by_category[by_category["lookup_priority"] == _PRIORITY_CATEGORY_MEAN]
    if not p3.empty:
        row = p3.sort_values("lookup_priority").iloc[0]
        return _row_to_params(row)

    return None
