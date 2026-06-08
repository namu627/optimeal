"""
train_nn.py
===========
1D ResNet 스케일링 엔진 학습 진입점.

실행:
    cd module_2
    python src/engine/train_nn.py            # 5-Fold CV → 최종 모델 저장
    python src/engine/train_nn.py --quick    # 빠른 검증 (epochs=10)

동작:
  1. prepare_training_data()로 텐서 준비 (1435 샘플, 7 N 확장)
  2. GroupKFold(n_splits=5) — small_recipe_id 기준
  3. fold마다 ResNet1D 학습 + MAPE 평가
  4. 5-Fold 평균 MAPE 보고
  5. 전체 데이터 재학습 → nn_model.pt 저장

random_state=42 / torch.manual_seed(42) 고정 (CLAUDE.md 규칙).

담당: 권성민
기준 문서: CLAUDE.md (모듈 2, 2026-05-19)
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

# 동일 디렉터리 모듈 직접 import 가능하도록 경로 추가
_ENGINE_DIR = Path(__file__).parent
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))

from nn_scaling_engine import (
    ResNet1D,
    evaluate,
    save_model,
    train_epoch,
)
from nn_training_data import groupkfold_indices, prepare_training_data


# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

DEFAULT_EPOCHS: int = 60
DEFAULT_BATCH: int = 64
DEFAULT_LR: float = 1e-3
DEFAULT_WD: float = 1e-4
MODEL_PATH: Path = _ENGINE_DIR / "nn_model.pt"
SEED: int = 42


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def set_seed(seed: int = SEED) -> None:
    """학습 재현성을 위한 시드 고정 (torch, numpy, random)."""
    torch.manual_seed(seed)
    np.random.seed(seed)
    random.seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def pick_device() -> torch.device:
    """학습 디바이스 선택. mps > cpu (CUDA는 macOS에서 미사용)."""
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def make_loader(
    indices: np.ndarray,
    x: torch.Tensor,
    y: torch.Tensor,
    logn: torch.Tensor,
    mask: torch.Tensor,
    batch_size: int,
    shuffle: bool,
) -> DataLoader:
    """인덱스 부분 집합으로 DataLoader 생성.

    Args:
        indices: 샘플 인덱스 배열.
        x, y, logn, mask: prepare_training_data() 텐서.
        batch_size: 배치 크기.
        shuffle: True=학습용, False=평가용.

    Returns:
        TensorDataset 기반 DataLoader.
    """
    idx_t = torch.from_numpy(indices)
    ds = TensorDataset(x[idx_t], y[idx_t], logn[idx_t], mask[idx_t])
    return DataLoader(ds, batch_size=batch_size, shuffle=shuffle)


def train_one_fold(
    fold_idx: int,
    train_idx: np.ndarray,
    test_idx: np.ndarray,
    data: dict,
    epochs: int,
    batch_size: int,
    device: torch.device,
) -> dict[str, float]:
    """단일 fold 학습 + 평가.

    Args:
        fold_idx: fold 번호 (로그용).
        train_idx, test_idx: GroupKFold split 인덱스.
        data: prepare_training_data() 반환 dict.
        epochs: 에폭 수.
        batch_size: 배치 크기.
        device: 학습 디바이스.

    Returns:
        {'fold': int, 'final_train_loss': float, 'test_loss': float, 'test_mape': float}.
    """
    set_seed(SEED + fold_idx)  # fold마다 다른 시드 + 재현성
    model = ResNet1D().to(device)
    opt = optim.Adam(model.parameters(), lr=DEFAULT_LR, weight_decay=DEFAULT_WD)

    train_loader = make_loader(
        train_idx, data["X"], data["Y"], data["logN"], data["mask"],
        batch_size=batch_size, shuffle=True,
    )
    test_loader = make_loader(
        test_idx, data["X"], data["Y"], data["logN"], data["mask"],
        batch_size=batch_size, shuffle=False,
    )

    last_loss = float("nan")
    for ep in range(1, epochs + 1):
        last_loss = train_epoch(model, train_loader, opt, device)
        if ep % max(epochs // 5, 1) == 0:
            metric = evaluate(model, test_loader, device)
            print(
                f"  [fold {fold_idx}] epoch {ep:3d} | "
                f"train_loss={last_loss:.4f} | "
                f"test_loss={metric['loss']:.4f} | test_mape={metric['mape']:.2f}%"
            )

    final = evaluate(model, test_loader, device)
    return {
        "fold": fold_idx,
        "final_train_loss": last_loss,
        "test_loss": final["loss"],
        "test_mape": final["mape"],
    }


def train_final(
    data: dict,
    epochs: int,
    batch_size: int,
    device: torch.device,
    save_path: Path,
) -> None:
    """전체 데이터로 최종 모델 학습 후 저장.

    Args:
        data: prepare_training_data() 반환 dict.
        epochs: 에폭 수.
        batch_size: 배치 크기.
        device: 학습 디바이스.
        save_path: 모델 저장 경로.
    """
    set_seed(SEED)
    model = ResNet1D().to(device)
    opt = optim.Adam(model.parameters(), lr=DEFAULT_LR, weight_decay=DEFAULT_WD)
    all_idx = np.arange(len(data["groups"]))
    loader = make_loader(
        all_idx, data["X"], data["Y"], data["logN"], data["mask"],
        batch_size=batch_size, shuffle=True,
    )
    for ep in range(1, epochs + 1):
        loss = train_epoch(model, loader, opt, device)
        if ep % max(epochs // 5, 1) == 0:
            print(f"  [final] epoch {ep:3d} | train_loss={loss:.4f}")
    save_model(model, save_path)
    print(f"[final] saved → {save_path}")


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------

def main() -> None:
    """5-Fold CV + 최종 모델 저장 진입점."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="빠른 검증 (epochs=10)")
    parser.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS)
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    args = parser.parse_args()

    epochs = 10 if args.quick else args.epochs
    device = pick_device()
    print(f"[setup] device={device} epochs={epochs} batch={args.batch}")

    data = prepare_training_data()
    print(
        f"[data] X={tuple(data['X'].shape)} groups={len(set(data['groups']))} "
        f"valid_slots={int(data['mask'].sum())}"
    )

    print("[cv] 5-Fold GroupKFold (group=small_recipe_id)")
    results = []
    for i, (tr, te) in enumerate(groupkfold_indices(data["groups"], n_splits=5)):
        res = train_one_fold(i, tr, te, data, epochs, args.batch, device)
        results.append(res)
        print(
            f"[fold {i}] test_loss={res['test_loss']:.4f} "
            f"test_mape={res['test_mape']:.2f}%"
        )

    mean_mape = float(np.mean([r["test_mape"] for r in results]))
    std_mape = float(np.std([r["test_mape"] for r in results]))
    print(f"[cv] mean MAPE = {mean_mape:.2f}% ± {std_mape:.2f}%")

    print("[final] training on full dataset")
    train_final(data, epochs, args.batch, device, MODEL_PATH)


if __name__ == "__main__":
    main()
