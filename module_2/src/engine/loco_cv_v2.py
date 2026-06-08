"""
loco_cv_v2.py
=============
v2 모델로 Leave-One-Cell-Out CV 재실행.

v1 LOCO (loco_cv.py) 결과: 13/13 셀에서 lookup이 NN보다 압도적 우위 (평균 Δ -124%p).
v1 실패 원인: 카테고리가 슬롯 위치(0~5=주재료 등)로만 인코딩되어 hold-out 시
그 슬롯이 통째로 비어 모델이 매핑을 학습할 자리가 없었음.

v2 변경 (nn_training_data_v2 + nn_scaling_engine_v2):
  - 카테고리·group_type을 per-slot one-hot feature로 표현
  - 학습 타겟에 노이즈 추가 (ε ~ N(0, σ²))
  - DeepSets-with-context 아키텍처 (slot 위치 비의존)

LOCO 평가:
  hold-out 셀의 모든 슬롯 학습 신호 제거 후, 그 셀에 대한 NN 예측을
  lookup(category_mean of 정의된 다른 셀들)과 비교.

담당: 권성민 (Opus 4.7 보조)
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch import optim
from torch.utils.data import DataLoader, TensorDataset

_ENGINE_DIR = Path(__file__).parent
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))

from nn_scaling_engine_v2 import (  # noqa: E402
    DeepSetsContext,
    train_epoch_v2,
)
from nn_training_data_v2 import (  # noqa: E402
    DEFAULT_NOISE_SIGMA,
    SLOT_TO_CATEGORY,
    prepare_training_data_v2,
)
from nn_training_data import (  # noqa: E402
    GROUP_TYPE_KOR2EN,
    N_SLOTS,
    SLOT_LAYOUT,
    normalize_category,
)


SEED: int = 42
EPOCHS_PER_FOLD: int = 40
BATCH: int = 64
LR: float = 1e-3
WD: float = 1e-4


def set_seed(seed: int = SEED) -> None:
    """학습 재현성 시드 고정."""
    torch.manual_seed(seed)
    np.random.seed(seed)
    random.seed(seed)


def pick_device() -> torch.device:
    """mps > cpu."""
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def make_loader(
    indices: np.ndarray,
    x: torch.Tensor,
    y: torch.Tensor,
    logn: torch.Tensor,
    mask: torch.Tensor,
    batch: int,
    shuffle: bool,
) -> DataLoader:
    """v2 DataLoader 생성."""
    idx_t = torch.from_numpy(indices)
    ds = TensorDataset(x[idx_t], y[idx_t], logn[idx_t], mask[idx_t])
    return DataLoader(ds, batch_size=batch, shuffle=shuffle)


def cell_slot_mask(
    records: list,
    n_values: tuple[int, ...],
    hold_gt: str,
    hold_cat: str,
) -> tuple[np.ndarray, np.ndarray]:
    """hold-out 셀에 속하는 (record, N, slot) 마스크 생성.

    Args:
        records: build_recipe_records 결과.
        n_values: N 확장.
        hold_gt: 영어 group_type.
        hold_cat: 한국어 카테고리.

    Returns:
        sample_has_cell: (M,) bool — 샘플이 hold-out 셀 슬롯을 가지는가
        slot_in_cell: (M, 30) bool — 슬롯별 hold-out 셀 여부
    """
    cat_start, cat_size = SLOT_LAYOUT[hold_cat]
    cat_slots = list(range(cat_start, cat_start + cat_size))

    m = len(records) * len(n_values)
    has_cell = np.zeros(m, dtype=bool)
    slot_in = np.zeros((m, N_SLOTS), dtype=bool)

    idx = 0
    for rec in records:
        rec_match = rec.group_type == hold_gt
        valid_cell_slots = (
            [s for s in cat_slots if rec.mask[s]] if rec_match else []
        )
        for _ in n_values:
            if valid_cell_slots:
                has_cell[idx] = True
                for s in valid_cell_slots:
                    slot_in[idx, s] = True
            idx += 1
    return has_cell, slot_in


def category_mean_lookup(
    b_table_loco: dict[tuple[str, str], float], category: str
) -> float:
    """동일 카테고리의 다른 셀들의 b 평균. 없으면 전체 평균."""
    matched = [b for (_, c), b in b_table_loco.items() if c == category]
    if matched:
        return float(np.mean(matched))
    return float(np.mean(list(b_table_loco.values())))


@torch.no_grad()
def _eval_cell(
    model: DeepSetsContext,
    x: torch.Tensor,
    y: torch.Tensor,
    logn: torch.Tensor,
    eval_mask: torch.Tensor,
    b_lookup: float,
    device: torch.device,
    batch: int,
) -> tuple[float, float]:
    """NN과 lookup MAPE 동시 평가 (hold-out 셀 슬롯에 한해서)."""
    n = x.shape[0]
    loader = make_loader(
        np.arange(n), x, y, logn, eval_mask, batch, shuffle=False
    )
    nn_sum, lk_sum, valid = 0.0, 0.0, 0
    for x_b, y_b, logn_b, mask_b in loader:
        x_b = x_b.to(device); y_b = y_b.to(device)
        logn_b = logn_b.to(device); mask_b = mask_b.to(device)
        pred = model(x_b, mask_b, logn_b)
        log_base = x_b[:, :, 0]
        pred_lk = log_base + b_lookup * logn_b.unsqueeze(-1)

        true_g = torch.exp(y_b) * mask_b
        pred_g_nn = torch.exp(pred) * mask_b
        pred_g_lk = torch.exp(pred_lk) * mask_b
        denom = true_g.clamp_min(1e-8)
        nn_ape = (torch.abs(pred_g_nn - true_g) / denom) * mask_b
        lk_ape = (torch.abs(pred_g_lk - true_g) / denom) * mask_b
        nn_sum += float(nn_ape.sum().item())
        lk_sum += float(lk_ape.sum().item())
        valid += int(mask_b.sum().item())
    valid = max(valid, 1)
    return nn_sum / valid * 100.0, lk_sum / valid * 100.0


def run_one_cell(
    hold_gt: str,
    hold_cat: str,
    data: dict,
    epochs: int,
    device: torch.device,
) -> dict[str, float]:
    """단일 셀 hold-out 실험."""
    records = data["records"]
    b_table = data["b_table"]

    # b_table에서 해당 셀만 제외 (lookup baseline 계산용)
    b_table_loco = {k: v for k, v in b_table.items() if k != (hold_gt, hold_cat)}

    _, slot_in = cell_slot_mask(
        records, tuple(int(np.exp(v)) for v in []),  # dummy, 아래에서 수정
        hold_gt, hold_cat,
    )
    # 위 dummy 호출 대신 직접 슬롯 마스크 계산
    cat_start, cat_size = SLOT_LAYOUT[hold_cat]
    cat_slots = list(range(cat_start, cat_start + cat_size))
    m = data["X"].shape[0]
    n_per_recipe = m // len(records)
    slot_in = np.zeros((m, N_SLOTS), dtype=bool)
    idx = 0
    for rec in records:
        rec_match = rec.group_type == hold_gt
        valid_cell_slots = (
            [s for s in cat_slots if rec.mask[s]] if rec_match else []
        )
        for _ in range(n_per_recipe):
            for s in valid_cell_slots:
                slot_in[idx, s] = True
            idx += 1
    slot_in_t = torch.from_numpy(slot_in.astype(np.float32))

    # train_mask: hold-out 셀 슬롯 신호 제거
    train_mask = data["mask"] * (1.0 - slot_in_t)
    eval_mask = data["mask"] * slot_in_t

    n_eval_slots = int(eval_mask.sum().item())
    if n_eval_slots == 0:
        return {
            "group_type": hold_gt, "category": hold_cat,
            "n_eval_slots": 0, "nn_mape": float("nan"),
            "lookup_mape": float("nan"), "delta": float("nan"),
            "b_true": float("nan"), "b_lookup": float("nan"), "note": "no_data",
        }

    # 학습
    set_seed(SEED)
    model = DeepSetsContext().to(device)
    opt = optim.Adam(model.parameters(), lr=LR, weight_decay=WD)
    indices = np.arange(m)
    loader = make_loader(
        indices, data["X"], data["Y"], data["logN"], train_mask, BATCH, shuffle=True,
    )
    for _ in range(1, epochs + 1):
        train_epoch_v2(model, loader, opt, device)

    # 평가
    b_lookup = category_mean_lookup(b_table_loco, hold_cat)
    nn_mape, lookup_mape = _eval_cell(
        model, data["X"], data["Y"], data["logN"], eval_mask,
        b_lookup, device, BATCH,
    )

    return {
        "group_type": hold_gt,
        "category": hold_cat,
        "n_eval_slots": n_eval_slots,
        "nn_mape": nn_mape,
        "lookup_mape": lookup_mape,
        "delta": lookup_mape - nn_mape,
        "b_true": float(b_table[(hold_gt, hold_cat)]),
        "b_lookup": float(b_lookup),
        "note": "",
    }


def main() -> None:
    """v2 LOCO 진입점."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=EPOCHS_PER_FOLD)
    parser.add_argument("--sigma", type=float, default=DEFAULT_NOISE_SIGMA)
    parser.add_argument(
        "--out", type=str,
        default=str(_ENGINE_DIR / "loco_v2_results_20260520.csv"),
    )
    args = parser.parse_args()

    device = pick_device()
    print(f"[setup] device={device} epochs={args.epochs} sigma={args.sigma}")

    data = prepare_training_data_v2(noise_sigma=args.sigma)
    print(
        f"[data] X={tuple(data['X'].shape)} "
        f"groups={len(set(data['groups']))} "
        f"valid_slots={int(data['mask'].sum())}"
    )

    # 데이터에 등장하는 (gt, cat) 셀 후보
    df_b = pd.read_csv(_ENGINE_DIR.parent.parent / "df_B.csv")
    cells_in_data = set()
    for _, row in df_b.iterrows():
        gt_kor = str(row["group_type"])
        gt_en = GROUP_TYPE_KOR2EN.get(gt_kor)
        cat = normalize_category(row.get("derived_category"), row.get("role"))
        if gt_en and cat:
            cells_in_data.add((gt_en, cat))
    candidates = sorted([k for k in data["b_table"].keys() if k in cells_in_data])

    print(f"[loco] {len(candidates)}개 셀")
    results = []
    for i, (gt, cat) in enumerate(candidates, 1):
        print(f"\n[loco {i}/{len(candidates)}] hold-out ({gt}, {cat})")
        res = run_one_cell(gt, cat, data, args.epochs, device)
        results.append(res)
        print(
            f"  n_slots={res['n_eval_slots']:4d} | "
            f"b_true={res['b_true']:.4f} b_lookup={res['b_lookup']:.4f} | "
            f"NN={res['nn_mape']:.2f}% LOOKUP={res['lookup_mape']:.2f}% "
            f"Δ={res['delta']:+.2f}%p"
        )

    df_res = pd.DataFrame(results)
    df_res.to_csv(args.out, index=False)
    print(f"\n[loco] saved → {args.out}")

    valid = df_res[df_res["n_eval_slots"] > 0]
    nn_wins = int((valid["delta"] > 0).sum())
    lk_wins = int((valid["delta"] < 0).sum())
    mean_delta = float(valid["delta"].mean())
    print(f"[loco] NN 우위: {nn_wins}/{len(valid)}, lookup 우위: {lk_wins}/{len(valid)}")
    print(f"[loco] 평균 Δ (LOOKUP - NN) = {mean_delta:+.2f}%p")
    if mean_delta > 1.0:
        print("[verdict] NN < LOOKUP — NN 외삽 우위. 논문 주장 가능.")
    elif mean_delta < -1.0:
        print("[verdict] NN > LOOKUP — lookup 회귀 검토.")
    else:
        print("[verdict] NN ≈ LOOKUP — 동등. 위상 재정의 필요.")


if __name__ == "__main__":
    main()
