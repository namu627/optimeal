"""
train_nn_v2.py
==============
v2 학습 진입점 — DeepSetsContext 모델 × noisy 합성 타겟.

실행:
    python module_2/src/engine/train_nn_v2.py            # 60 epoch, σ=0.05
    python module_2/src/engine/train_nn_v2.py --quick    # 10 epoch
    python module_2/src/engine/train_nn_v2.py --sigma 0  # 노이즈 없이 (v1 비교용)

수행:
  1. prepare_training_data_v2 — per-slot feature 텐서 + noisy 타겟
  2. 5-Fold GroupKFold (small_recipe_id)
  3. fold마다 DeepSetsContext 학습 + MAPE 평가
  4. 평균 MAPE 보고
  5. 전체 데이터 재학습 → nn_model_v2.pt 저장

  + lookup baseline (셀별 진짜 b_feedback 사용) MAPE 동시 계산 — NN vs lookup 직접 비교.

담당: 권성민 (Opus 4.7 보조)
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import numpy as np
import torch
from torch import optim
from torch.utils.data import DataLoader, TensorDataset

_ENGINE_DIR = Path(__file__).parent
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))

from nn_scaling_engine_v2 import (  # noqa: E402
    DeepSetsContext,
    evaluate_v2,
    save_model_v2,
    train_epoch_v2,
)
from nn_training_data_v2 import (  # noqa: E402
    DEFAULT_NOISE_SIGMA,
    groupkfold_indices_v2,
    prepare_training_data_v2,
)


DEFAULT_EPOCHS: int = 60
DEFAULT_BATCH: int = 64
DEFAULT_LR: float = 1e-3
DEFAULT_WD: float = 1e-4
SEED: int = 42
MODEL_PATH: Path = _ENGINE_DIR / "nn_model_v2.pt"


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
    """v2 DataLoader 생성. 배치 형식: (x, y, log_n, mask)."""
    idx_t = torch.from_numpy(indices)
    ds = TensorDataset(x[idx_t], y[idx_t], logn[idx_t], mask[idx_t])
    return DataLoader(ds, batch_size=batch, shuffle=shuffle)


@torch.no_grad()
def lookup_baseline_mape(
    x: torch.Tensor,
    y: torch.Tensor,
    logn: torch.Tensor,
    mask: torch.Tensor,
    b_lookup: float,
) -> float:
    """lookup baseline: 모든 슬롯에 동일 b_lookup 적용.

    log(Y_pred) = log_base + b_lookup * log_n.

    Args:
        x, y, mask, logn: 평가 텐서.
        b_lookup: 룩업 b.

    Returns:
        MAPE (%).
    """
    log_base = x[:, :, 0]  # (B, K)
    pred_log = log_base + b_lookup * logn.unsqueeze(-1)
    pred_g = torch.exp(pred_log) * mask
    true_g = torch.exp(y) * mask
    ape = torch.abs(pred_g - true_g) / true_g.clamp_min(1e-8) * mask
    n = mask.sum().clamp_min(1.0)
    return float((ape.sum() / n).item() * 100.0)


def train_one_fold(
    fold_idx: int,
    train_idx: np.ndarray,
    test_idx: np.ndarray,
    data: dict,
    epochs: int,
    batch: int,
    device: torch.device,
    b_lookup_mean: float,
) -> dict[str, float]:
    """단일 fold 학습 + 평가 + lookup baseline 비교."""
    set_seed(SEED + fold_idx)
    model = DeepSetsContext().to(device)
    opt = optim.Adam(model.parameters(), lr=DEFAULT_LR, weight_decay=DEFAULT_WD)

    train_loader = make_loader(
        train_idx, data["X"], data["Y"], data["logN"], data["mask"],
        batch, shuffle=True,
    )
    test_loader = make_loader(
        test_idx, data["X"], data["Y"], data["logN"], data["mask"],
        batch, shuffle=False,
    )

    for ep in range(1, epochs + 1):
        loss = train_epoch_v2(model, train_loader, opt, device)
        if ep % max(epochs // 5, 1) == 0:
            metric = evaluate_v2(model, test_loader, device)
            print(
                f"  [fold {fold_idx}] epoch {ep:3d} | "
                f"train_loss={loss:.4f} | "
                f"test_mape={metric['mape']:.2f}%"
            )

    nn_metric = evaluate_v2(model, test_loader, device)
    lookup_mape = lookup_baseline_mape(
        data["X"][torch.from_numpy(test_idx)],
        data["Y"][torch.from_numpy(test_idx)],
        data["logN"][torch.from_numpy(test_idx)],
        data["mask"][torch.from_numpy(test_idx)],
        b_lookup_mean,
    )
    return {
        "fold": fold_idx,
        "test_loss": nn_metric["loss"],
        "test_mape": nn_metric["mape"],
        "lookup_mape": lookup_mape,
    }


def train_final(
    data: dict,
    epochs: int,
    batch: int,
    device: torch.device,
    save_path: Path,
) -> None:
    """전체 데이터 재학습 후 저장."""
    set_seed(SEED)
    model = DeepSetsContext().to(device)
    opt = optim.Adam(model.parameters(), lr=DEFAULT_LR, weight_decay=DEFAULT_WD)
    all_idx = np.arange(len(data["groups"]))
    loader = make_loader(
        all_idx, data["X"], data["Y"], data["logN"], data["mask"],
        batch, shuffle=True,
    )
    for ep in range(1, epochs + 1):
        loss = train_epoch_v2(model, loader, opt, device)
        if ep % max(epochs // 5, 1) == 0:
            print(f"  [final] epoch {ep:3d} | train_loss={loss:.4f}")
    save_model_v2(model, save_path)
    print(f"[final] saved → {save_path}")


def main() -> None:
    """v2 5-Fold CV + 최종 저장."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS)
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    parser.add_argument(
        "--sigma", type=float, default=DEFAULT_NOISE_SIGMA,
        help="타겟 b 노이즈 σ (0이면 결정론 = v1과 동일 타겟)",
    )
    args = parser.parse_args()

    epochs = 10 if args.quick else args.epochs
    device = pick_device()
    print(
        f"[setup] device={device} epochs={epochs} batch={args.batch} "
        f"noise_sigma={args.sigma}"
    )

    data = prepare_training_data_v2(noise_sigma=args.sigma)
    print(
        f"[data] X={tuple(data['X'].shape)} "
        f"groups={len(set(data['groups']))} "
        f"valid_slots={int(data['mask'].sum())}"
    )

    # lookup baseline b: 모든 b_feedback 셀의 평균 (NN과 동일한 정보 제약)
    b_lookup = float(np.mean(list(data["b_table"].values())))
    print(f"[lookup] mean b across {len(data['b_table'])} cells = {b_lookup:.4f}")

    print("[cv] 5-Fold GroupKFold")
    results = []
    for i, (tr, te) in enumerate(groupkfold_indices_v2(data["groups"], n_splits=5)):
        res = train_one_fold(i, tr, te, data, epochs, args.batch, device, b_lookup)
        results.append(res)
        print(
            f"[fold {i}] NN_MAPE={res['test_mape']:.2f}% "
            f"LOOKUP_MAPE={res['lookup_mape']:.2f}% "
            f"Δ={res['lookup_mape'] - res['test_mape']:+.2f}%p"
        )

    mean_nn = float(np.mean([r["test_mape"] for r in results]))
    std_nn = float(np.std([r["test_mape"] for r in results]))
    mean_lk = float(np.mean([r["lookup_mape"] for r in results]))
    std_lk = float(np.std([r["lookup_mape"] for r in results]))
    print(f"[cv] NN mean MAPE     = {mean_nn:.2f}% ± {std_nn:.2f}%")
    print(f"[cv] LOOKUP mean MAPE = {mean_lk:.2f}% ± {std_lk:.2f}%")
    print(f"[cv] Δ (LOOKUP - NN)  = {mean_lk - mean_nn:+.2f}%p")

    print("[final] training on full dataset")
    train_final(data, epochs, args.batch, device, MODEL_PATH)


if __name__ == "__main__":
    main()
