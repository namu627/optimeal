"""
nn_training_data.py
===================
1D ResNet 스케일링 엔진 학습 데이터 생성 모듈.

입력 데이터:
  - module_2/df_B.csv (607행, 레시피-재료 쌍 단위)
  - module_2/src/engine/scaling_coefficients.csv (b_feedback 참조 표준)

처리 흐름:
  1. df_B.csv 로드 → 카테고리/group_type 정규화
  2. small_recipe_id 단위로 레코드 그룹화
  3. 30슬롯 텐서 생성 (카테고리별 정렬 + base 내림차순)
  4. N ∈ {10,20,50,100,150,200,300} 7종으로 확장
  5. Y = base × N^b_feedback(group_type, category) 계산
  6. GroupKFold(n_splits=5, groups=small_recipe_id) 분할

설계 결정:
  - 슬롯 배정: 주재료 0~5 / 부재료 6~11 / 양념류 12~23 / 수분류 24~27 / 유지류 28~29
  - 입력 텐서 (B, 4, 30):
      Ch 0: log(base_amount_g), 패딩은 0.0
      Ch 1: category_id / 4.0 (주재료=0.0 ~ 유지류=1.0)
      Ch 2: valid_mask (1=유효 / 0=패딩)
      Ch 3: group_type_id / 2.0 (dry_heat=0.0, moist_heat=0.5, no_heat=1.0)
            ← 2026-05-20 추가. b_feedback이 (group_type × category)별로 다르므로
              모델이 분류 신호를 받지 못하면 셀 평균 b로 fit되어 큰 잔차 발생.
  - 학습 타겟: log(Y_g), 패딩 슬롯은 0.0 (마스크로 제외)
  - b_feedback 누락 셀(no_heat × 부재료/수분류) → 전체 평균 b=0.6163 fallback

담당: 권성민
기준 문서: ADR-001, ADR-002 v3, CLAUDE.md (모듈 2, 2026-05-19)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd
import torch
from sklearn.model_selection import GroupKFold


# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

# 슬롯 배정: 카테고리별 시작 인덱스와 슬롯 개수 (총 30슬롯)
SLOT_LAYOUT: dict[str, tuple[int, int]] = {
    "주재료": (0, 6),
    "부재료": (6, 6),
    "양념류": (12, 12),
    "수분류": (24, 4),
    "유지류": (28, 2),
}
N_SLOTS: int = 30

# 카테고리 ID (Channel 1 정규화 값 = id / 4.0)
CATEGORY_ID: dict[str, float] = {
    "주재료": 0.00,
    "부재료": 0.25,
    "양념류": 0.50,
    "수분류": 0.75,
    "유지류": 1.00,
}

# df_B.csv group_type 한국어 → scaling_coefficients.csv 영어
GROUP_TYPE_KOR2EN: dict[str, str] = {
    "비가열": "no_heat",
    "습열": "moist_heat",
    "건열": "dry_heat",
}

# group_type ID (Channel 3 정규화 값 = id / 2.0). 2026-05-20 추가.
GROUP_TYPE_ID: dict[str, float] = {
    "dry_heat": 0.0,
    "moist_heat": 0.5,
    "no_heat": 1.0,
}

# role 영어 → 한국어 카테고리 (derived_category가 NaN인 경우)
ROLE_KOR_MAP: dict[str, str] = {
    "main": "주재료",
    "sub": "부재료",
    "seasoning": "양념류",
}

# derived_category 영어 → 한국어 카테고리
DERIVED_KOR_MAP: dict[str, str] = {
    "oil_fat": "유지류",
    "water_base": "수분류",
}

# 학습 시 사용할 N 확장 값 (7종, 사용자 사양)
DEFAULT_N_VALUES: tuple[int, ...] = (10, 20, 50, 100, 150, 200, 300)

# b_feedback 누락 셀 fallback (전체 평균, scaling_coefficients.csv id=1 mixedlm B_simple)
B_FALLBACK: float = 0.6163


# ---------------------------------------------------------------------------
# 정규화 헬퍼
# ---------------------------------------------------------------------------

def normalize_category(derived_category: object, role: object) -> str | None:
    """
    df_B.csv의 derived_category·role 컬럼을 한국어 5분류로 정규화한다.

    derived_category가 'oil_fat'/'water_base'이면 우선 적용,
    그렇지 않으면 role을 한국어로 매핑한다.

    Args:
        derived_category: df_B.csv derived_category 컬럼 값 ('oil_fat'|'water_base'|NaN)
        role: df_B.csv role 컬럼 값 ('main'|'sub'|'seasoning')

    Returns:
        한국어 카테고리 ('주재료'|'부재료'|'양념류'|'수분류'|'유지류') 또는
        None (매핑 실패 시)
    """
    if isinstance(derived_category, str) and derived_category in DERIVED_KOR_MAP:
        return DERIVED_KOR_MAP[derived_category]
    if isinstance(role, str) and role in ROLE_KOR_MAP:
        return ROLE_KOR_MAP[role]
    return None


def normalize_group_type(group_type_kor: str) -> str:
    """
    df_B.csv의 group_type 한국어 값을 scaling_coefficients.csv의 영어 값으로 변환.

    Args:
        group_type_kor: '비가열'|'습열'|'건열'

    Returns:
        'no_heat'|'moist_heat'|'dry_heat'

    Raises:
        KeyError: 매핑 불가 값 입력 시
    """
    return GROUP_TYPE_KOR2EN[group_type_kor]


# ---------------------------------------------------------------------------
# b_feedback 룩업
# ---------------------------------------------------------------------------

def load_b_feedback_table(
    coefficients_path: str | Path | None = None,
) -> dict[tuple[str, str], float]:
    """
    scaling_coefficients.csv에서 nutritionist_feedback 행을 로드하여
    (group_type, ingredient_category) → b_feedback 사전을 생성한다.

    ADR-002 v3 1순위 룩업 (nutritionist_feedback)만 사용.
    누락 셀(no_heat × 부재료/수분류 등)은 사전에 포함하지 않음 →
    `lookup_b_feedback`에서 B_FALLBACK으로 처리.

    Args:
        coefficients_path: scaling_coefficients.csv 경로.
                           None이면 모듈 2 기본 경로 사용.

    Returns:
        {(group_type_en, ingredient_category_kor): power_law_b} 사전.
    """
    if coefficients_path is None:
        coefficients_path = Path(__file__).parent / "scaling_coefficients.csv"

    df = pd.read_csv(coefficients_path)
    fb = df[df["estimation_method"] == "nutritionist_feedback"].copy()

    # 동일 (group_type, category) 중복 시 coefficient_id 최신값(최대) 채택
    fb = fb.sort_values("coefficient_id").drop_duplicates(
        subset=["group_type", "ingredient_category"], keep="last"
    )

    table: dict[tuple[str, str], float] = {}
    for _, row in fb.iterrows():
        key = (str(row["group_type"]), str(row["ingredient_category"]))
        table[key] = float(row["power_law_b"])
    return table


def lookup_b_feedback(
    table: dict[tuple[str, str], float],
    group_type_en: str,
    category_kor: str,
) -> float:
    """
    (group_type_en, category_kor) 키로 b_feedback 조회. 누락 시 전체 평균 fallback.

    Args:
        table: load_b_feedback_table() 반환값.
        group_type_en: 'dry_heat'|'moist_heat'|'no_heat'.
        category_kor: '주재료'|'부재료'|'양념류'|'수분류'|'유지류'.

    Returns:
        해당 셀의 b_feedback 또는 B_FALLBACK(0.6163).
    """
    return table.get((group_type_en, category_kor), B_FALLBACK)


# ---------------------------------------------------------------------------
# 레시피 레코드 구성
# ---------------------------------------------------------------------------

@dataclass
class RecipeRecord:
    """
    단일 소규모 레시피의 슬롯 텐서 표현 (N 확장 전 원본).

    Attributes:
        recipe_id: small_recipe_id 값.
        group_type: 영어 정규화된 group_type ('dry_heat'|'moist_heat'|'no_heat').
        base_amounts: shape (30,) — 각 슬롯의 base_amount_g, 패딩은 0.0.
        category_ids: shape (30,) — 각 슬롯의 정규화 카테고리 ID(0~1), 패딩 0.0.
        mask: shape (30,) bool — 유효 슬롯 마스크.
        b_per_slot: shape (30,) — 각 슬롯의 b_feedback. 패딩은 B_FALLBACK으로 채움(무관).
    """
    recipe_id: str
    group_type: str
    base_amounts: np.ndarray = field(default_factory=lambda: np.zeros(N_SLOTS))
    category_ids: np.ndarray = field(default_factory=lambda: np.zeros(N_SLOTS))
    mask: np.ndarray = field(default_factory=lambda: np.zeros(N_SLOTS, dtype=bool))
    b_per_slot: np.ndarray = field(
        default_factory=lambda: np.full(N_SLOTS, B_FALLBACK)
    )


def build_recipe_records(
    df_b: pd.DataFrame,
    b_table: dict[tuple[str, str], float],
) -> list[RecipeRecord]:
    """
    df_B.csv를 small_recipe_id 단위로 그룹화하여 RecipeRecord 리스트 생성.

    동일 레시피 내 카테고리별 재료는 base 내림차순 정렬 후 슬롯에 배정.
    슬롯 초과 재료는 base 최소값부터 절단 (drop).

    Args:
        df_b: df_B.csv DataFrame.
        b_table: load_b_feedback_table() 반환값.

    Returns:
        RecipeRecord 리스트 (레시피 단위).
    """
    records: list[RecipeRecord] = []
    for recipe_id, group in df_b.groupby("small_recipe_id"):
        group_type_kor = str(group["group_type"].iloc[0])
        group_type_en = normalize_group_type(group_type_kor)
        rec = RecipeRecord(recipe_id=str(recipe_id), group_type=group_type_en)
        _fill_slots(rec, group, b_table)
        records.append(rec)
    return records


def _fill_slots(
    rec: RecipeRecord,
    group: pd.DataFrame,
    b_table: dict[tuple[str, str], float],
) -> None:
    """RecipeRecord의 슬롯을 카테고리별로 채운다.

    Args:
        rec: 채울 RecipeRecord (in-place 수정).
        group: 동일 small_recipe_id의 df_B.csv 부분 집합.
        b_table: b_feedback 사전.
    """
    for category, (start, size) in SLOT_LAYOUT.items():
        cat_rows = []
        for _, row in group.iterrows():
            cat = normalize_category(row.get("derived_category"), row.get("role"))
            if cat == category:
                cat_rows.append(row)
        # base 내림차순 정렬, 슬롯 초과 시 절단
        cat_rows.sort(key=lambda r: float(r["base"]), reverse=True)
        cat_rows = cat_rows[:size]

        b_val = lookup_b_feedback(b_table, rec.group_type, category)
        for offset, row in enumerate(cat_rows):
            slot_idx = start + offset
            rec.base_amounts[slot_idx] = float(row["base"])
            rec.category_ids[slot_idx] = CATEGORY_ID[category]
            rec.mask[slot_idx] = True
            rec.b_per_slot[slot_idx] = b_val


# ---------------------------------------------------------------------------
# N 확장 및 텐서 변환
# ---------------------------------------------------------------------------

def expand_and_tensorize(
    records: list[RecipeRecord],
    n_values: tuple[int, ...] = DEFAULT_N_VALUES,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor, np.ndarray]:
    """
    레시피 레코드를 N 확장 후 학습 텐서로 변환한다.

    각 레코드를 len(n_values)개로 복제하여, 각 복제본에 다른 N을 적용한다.
    학습 타겟 Y는 b_feedback 기반 파생 데이터: Y = base × N^b_feedback.

    Args:
        records: build_recipe_records() 반환값.
        n_values: 확장 N 값 (기본 7종).

    Returns:
        (X, Y, logN, mask, groups):
          X: (M, 4, 30) float32 — 입력 텐서 (Ch3=group_type_id)
          Y: (M, 30) float32 — log(Y_g), 패딩 슬롯은 0.0
          logN: (M,) float32 — log(N)
          mask: (M, 30) float32 — 유효 슬롯 마스크 (1.0/0.0)
          groups: (M,) ndarray[str] — small_recipe_id (GroupKFold 그룹 변수)
        M = len(records) × len(n_values)
    """
    samples_x, samples_y, samples_logn, samples_mask, samples_grp = [], [], [], [], []
    for rec in records:
        for n in n_values:
            x, y, mask = _make_sample(rec, n)
            samples_x.append(x)
            samples_y.append(y)
            samples_logn.append(np.log(float(n)))
            samples_mask.append(mask)
            samples_grp.append(rec.recipe_id)

    x_arr = np.stack(samples_x).astype(np.float32)
    y_arr = np.stack(samples_y).astype(np.float32)
    logn_arr = np.array(samples_logn, dtype=np.float32)
    mask_arr = np.stack(samples_mask).astype(np.float32)
    groups = np.array(samples_grp)

    return (
        torch.from_numpy(x_arr),
        torch.from_numpy(y_arr),
        torch.from_numpy(logn_arr),
        torch.from_numpy(mask_arr),
        groups,
    )


def _make_sample(rec: RecipeRecord, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """단일 (RecipeRecord, N) 조합에서 (X, Y, mask) ndarray를 생성.

    Args:
        rec: 원본 RecipeRecord.
        n: 목표 인원수.

    Returns:
        x: (4, 30) — Ch0=log(base), Ch1=category_id, Ch2=mask, Ch3=group_type_id
        y: (30,) — log(Y), 패딩은 0.0
        mask: (30,) — 1.0/0.0
    """
    mask_f = rec.mask.astype(np.float32)
    log_base = np.zeros(N_SLOTS, dtype=np.float32)
    valid = rec.mask
    log_base[valid] = np.log(rec.base_amounts[valid])

    # Ch3: group_type id, 유효 슬롯에만 부여 (패딩은 0.0)
    gt_val = GROUP_TYPE_ID[rec.group_type]
    gt_channel = np.zeros(N_SLOTS, dtype=np.float32)
    gt_channel[valid] = gt_val

    x = np.stack(
        [log_base, rec.category_ids.astype(np.float32), mask_f, gt_channel], axis=0
    )

    # Y = base × N^b_feedback → log(Y) = log(base) + b × log(N)
    y = np.zeros(N_SLOTS, dtype=np.float32)
    y[valid] = log_base[valid] + rec.b_per_slot[valid] * np.log(float(n))
    return x, y, mask_f


# ---------------------------------------------------------------------------
# GroupKFold split
# ---------------------------------------------------------------------------

def groupkfold_indices(
    groups: np.ndarray, n_splits: int = 5
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """
    GroupKFold split iterator. 동일 small_recipe_id의 7개 N 샘플이
    train/test에 분리되도록 한다.

    Args:
        groups: expand_and_tensorize() 반환값 중 groups.
        n_splits: fold 수 (기본 5).

    Yields:
        (train_idx, test_idx) 튜플 (np.ndarray).
    """
    gkf = GroupKFold(n_splits=n_splits)
    placeholder_x = np.zeros((len(groups), 1))  # GroupKFold.split은 X 형상만 사용
    for train_idx, test_idx in gkf.split(placeholder_x, groups=groups):
        yield train_idx, test_idx


# ---------------------------------------------------------------------------
# 통합 진입점
# ---------------------------------------------------------------------------

def prepare_training_data(
    df_b_path: str | Path | None = None,
    coefficients_path: str | Path | None = None,
    n_values: tuple[int, ...] = DEFAULT_N_VALUES,
) -> dict[str, object]:
    """
    학습 텐서 일괄 생성 진입점. train_nn.py에서 호출.

    Args:
        df_b_path: df_B.csv 경로 (None이면 module_2/df_B.csv 사용).
        coefficients_path: scaling_coefficients.csv 경로.
        n_values: 확장할 N 값.

    Returns:
        dict {
          'X': torch.Tensor (M, 4, 30),
          'Y': torch.Tensor (M, 30),
          'logN': torch.Tensor (M,),
          'mask': torch.Tensor (M, 30),
          'groups': np.ndarray (M,),
          'records': list[RecipeRecord],
          'b_table': dict[(str, str), float],
        }
    """
    if df_b_path is None:
        df_b_path = Path(__file__).parent.parent.parent / "df_B.csv"

    df_b = pd.read_csv(df_b_path)
    b_table = load_b_feedback_table(coefficients_path)
    records = build_recipe_records(df_b, b_table)
    x, y, logn, mask, groups = expand_and_tensorize(records, n_values)
    return {
        "X": x,
        "Y": y,
        "logN": logn,
        "mask": mask,
        "groups": groups,
        "records": records,
        "b_table": b_table,
    }
