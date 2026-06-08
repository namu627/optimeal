"""
nn_training_data_v2.py
======================
v2 학습 데이터 — 카테고리/group_type을 슬롯 위치가 아닌 per-slot feature로 표현,
타겟 b에 결정론적 노이즈 추가.

목적:
  - LOCO에서 v1(슬롯 위치 기반)이 모두 lookup에 패한 원인 두 가지를 시정:
    (a) 카테고리가 슬롯 위치(0~5=주재료 등)로만 인코딩되어 hold-out 시 그 슬롯이 통째로 비어
        모델이 해당 카테고리·group_type 매핑을 학습할 자리가 없음.
    (b) 학습 타겟이 결정론 (Y=base×N^b_feedback)이라 lookup이 정의상 정답에 가까움 — NN이
        추가로 학습할 신호가 없음.
  - v2 변경:
    (a) per-slot feature vector = [log_base, cat_onehot(5), gt_onehot(3)] → 9개 채널 (또는 텐서 차원).
        모델은 슬롯 위치에 의존하지 않고 feature로 카테고리·group_type을 식별.
    (b) Y = base × N^(b_feedback + ε), ε ~ N(0, σ²) 결정론적
        (per (recipe_id, slot_idx)로 시드 고정, 모든 N 확장에 동일 ε 적용)

입력 텐서 (v2):
  X: (M, 30, 9) float32
     dim 2 = [log_base, cat_주재료, cat_부재료, cat_양념류, cat_수분류, cat_유지류,
              gt_dry_heat, gt_moist_heat, gt_no_heat]
  mask: (M, 30) float32 — 유효 슬롯 1.0
  logN: (M,) float32
  Y: (M, 30) float32 — log(Y_noisy)

담당: 권성민 (Opus 4.7 보조)
기준 문서: docs/for_reports/nn_results_analysis_20260520.md §8.4
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd
import torch
from sklearn.model_selection import GroupKFold

# 동일 디렉터리 모듈 import 가능하도록
import sys
_ENGINE_DIR = Path(__file__).parent
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))

from nn_training_data import (  # noqa: E402
    B_FALLBACK,
    DEFAULT_N_VALUES,
    GROUP_TYPE_KOR2EN,
    N_SLOTS,
    SLOT_LAYOUT,
    build_recipe_records,
    load_b_feedback_table,
    normalize_category,
    normalize_group_type,
)


# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

# Feature 차원 = log_base(1) + cat_onehot(5) + gt_onehot(3) = 9
N_FEATURES: int = 9

CATEGORY_ORDER: tuple[str, ...] = ("주재료", "부재료", "양념류", "수분류", "유지류")
GROUP_TYPE_ORDER: tuple[str, ...] = ("dry_heat", "moist_heat", "no_heat")

CAT_TO_OH_IDX: dict[str, int] = {c: i for i, c in enumerate(CATEGORY_ORDER)}
GT_TO_OH_IDX: dict[str, int] = {g: i for i, g in enumerate(GROUP_TYPE_ORDER)}

# 노이즈 기본 표준편차
DEFAULT_NOISE_SIGMA: float = 0.05

# slot index → category (LOCO 평가에 사용)
SLOT_TO_CATEGORY: dict[int, str] = {}
for cat, (start, size) in SLOT_LAYOUT.items():
    for s in range(start, start + size):
        SLOT_TO_CATEGORY[s] = cat


# ---------------------------------------------------------------------------
# 노이즈 생성
# ---------------------------------------------------------------------------

def _slot_noise(
    recipe_id: str, slot_idx: int, sigma: float, salt: int
) -> float:
    """(recipe_id, slot_idx) 결정론 노이즈.

    동일 (recipe, slot)에 대해 모든 N 확장에서 같은 ε이 적용되어야 한다
    (학습 신호 일관성 유지).

    Args:
        recipe_id: small_recipe_id.
        slot_idx: 0~29 슬롯 인덱스.
        sigma: 표준편차.
        salt: 노이즈 시드 추가 솔트 (실험 분리용).

    Returns:
        ε ~ N(0, σ²).
    """
    # 결정론 시드: hash 후 32bit로 마스크
    key = f"{salt}|{recipe_id}|{slot_idx}".encode("utf-8")
    h = int.from_bytes(__import__("hashlib").sha256(key).digest()[:4], "big")
    rng = np.random.RandomState(h)
    return float(rng.normal(0.0, sigma))


# ---------------------------------------------------------------------------
# 텐서 빌더
# ---------------------------------------------------------------------------

def build_feature_tensor(
    records: list,
    n_values: tuple[int, ...] = DEFAULT_N_VALUES,
    noise_sigma: float = DEFAULT_NOISE_SIGMA,
    noise_salt: int = 42,
) -> dict[str, object]:
    """RecipeRecord 리스트로부터 v2 feature 텐서 생성.

    Args:
        records: nn_training_data.build_recipe_records 결과.
        n_values: 확장할 N 값.
        noise_sigma: b에 더할 가우시안 노이즈 σ.
        noise_salt: 노이즈 결정성 시드 솔트.

    Returns:
        dict {
          'X': torch.Tensor (M, 30, 9),
          'Y': torch.Tensor (M, 30),
          'logN': torch.Tensor (M,),
          'mask': torch.Tensor (M, 30),
          'groups': np.ndarray (M,) of small_recipe_id,
          'noise_eps': torch.Tensor (M, 30) — 진단용,
        }
    """
    samples = []
    for rec in records:
        slot_eps = np.zeros(N_SLOTS, dtype=np.float32)
        for slot in range(N_SLOTS):
            if rec.mask[slot]:
                slot_eps[slot] = _slot_noise(
                    rec.recipe_id, slot, noise_sigma, noise_salt
                )
        for n in n_values:
            samples.append(_make_sample_v2(rec, n, slot_eps))

    X = torch.stack([s["x"] for s in samples])
    Y = torch.stack([s["y"] for s in samples])
    mask = torch.stack([s["mask"] for s in samples])
    logN = torch.tensor([s["logN"] for s in samples], dtype=torch.float32)
    eps = torch.stack([s["eps"] for s in samples])
    groups = np.array([s["recipe_id"] for s in samples])

    return {
        "X": X,
        "Y": Y,
        "logN": logN,
        "mask": mask,
        "groups": groups,
        "noise_eps": eps,
    }


def _make_sample_v2(
    rec, n: int, slot_eps: np.ndarray
) -> dict[str, object]:
    """단일 (record, N, slot_eps) 샘플 생성.

    Args:
        rec: RecipeRecord.
        n: 목표 인원수.
        slot_eps: shape (30,) — 슬롯별 b 노이즈.

    Returns:
        dict — x, y, mask, logN, eps, recipe_id.
    """
    x = np.zeros((N_SLOTS, N_FEATURES), dtype=np.float32)
    y = np.zeros(N_SLOTS, dtype=np.float32)
    mask = rec.mask.astype(np.float32)
    log_n = float(np.log(n))

    gt_idx = GT_TO_OH_IDX[rec.group_type]
    for slot in range(N_SLOTS):
        if not rec.mask[slot]:
            continue
        x[slot, 0] = float(np.log(rec.base_amounts[slot]))  # log_base
        # category onehot: 어떤 카테고리에 속하는지는 slot 인덱스로 결정 (학습 데이터 생성 단계).
        # 모델은 onehot으로 받음 → 슬롯 위치 비의존.
        cat = SLOT_TO_CATEGORY[slot]
        x[slot, 1 + CAT_TO_OH_IDX[cat]] = 1.0
        # group_type onehot
        x[slot, 1 + len(CATEGORY_ORDER) + gt_idx] = 1.0
        # noisy 타겟
        b_eff = rec.b_per_slot[slot] + slot_eps[slot]
        y[slot] = float(np.log(rec.base_amounts[slot])) + b_eff * log_n

    return {
        "x": torch.from_numpy(x),
        "y": torch.from_numpy(y),
        "mask": torch.from_numpy(mask),
        "logN": log_n,
        "eps": torch.from_numpy(slot_eps.copy()),
        "recipe_id": rec.recipe_id,
    }


# ---------------------------------------------------------------------------
# GroupKFold
# ---------------------------------------------------------------------------

def groupkfold_indices_v2(
    groups: np.ndarray, n_splits: int = 5
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """동일 small_recipe_id의 모든 샘플이 한 fold에 묶이도록 분할.

    Args:
        groups: build_feature_tensor 반환의 groups.
        n_splits: fold 수.

    Yields:
        (train_idx, test_idx).
    """
    gkf = GroupKFold(n_splits=n_splits)
    placeholder = np.zeros((len(groups), 1))
    for tr, te in gkf.split(placeholder, groups=groups):
        yield tr, te


# ---------------------------------------------------------------------------
# 통합 진입점
# ---------------------------------------------------------------------------

def prepare_training_data_v2(
    df_b_path: str | Path | None = None,
    coefficients_path: str | Path | None = None,
    n_values: tuple[int, ...] = DEFAULT_N_VALUES,
    noise_sigma: float = DEFAULT_NOISE_SIGMA,
    noise_salt: int = 42,
) -> dict[str, object]:
    """v2 학습 데이터 일괄 생성 진입점.

    Args:
        df_b_path: df_B.csv 경로.
        coefficients_path: scaling_coefficients.csv 경로.
        n_values: N 확장 값.
        noise_sigma: 타겟 b 노이즈 표준편차.
        noise_salt: 노이즈 결정성 솔트.

    Returns:
        build_feature_tensor 반환 dict + 'records', 'b_table' 추가.
    """
    if df_b_path is None:
        df_b_path = _ENGINE_DIR.parent.parent / "df_B.csv"

    df_b = pd.read_csv(df_b_path)
    b_table = load_b_feedback_table(coefficients_path)
    records = build_recipe_records(df_b, b_table)
    data = build_feature_tensor(records, n_values, noise_sigma, noise_salt)
    data["records"] = records
    data["b_table"] = b_table
    return data
