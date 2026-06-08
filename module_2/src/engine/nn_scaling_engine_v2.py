"""
nn_scaling_engine_v2.py
=======================
v2 모델 — DeepSets-with-context. 슬롯 위치 비의존, permutation-invariant.

설계 동기 (분석 md §8.4):
  v1 (1D ResNet)은 카테고리를 슬롯 위치(0~5=주재료 등)로만 인코딩하여
  hold-out 시 해당 슬롯이 통째로 비면 그 카테고리 매핑을 학습할 수 없음.
  v2는 카테고리·group_type을 per-slot one-hot feature로 받고, 슬롯 사이
  permutation-invariant pooling으로 context를 공유.

아키텍처:
  Input X: (B, K=30, F=9)   F = [log_base, cat_oh(5), gt_oh(3)]
  mask: (B, K)
  log_n: (B,)

  phi: Linear(F→64) → ReLU → Linear(64→64) → ReLU       # per-slot 공유 MLP
  → phi(X): (B, K, 64)
  pooled = sum_i (phi_i * mask_i) / sum_i mask_i        # (B, 64) mean over valid
  context_in = concat(pooled, log_n): (B, 65)
  context_mlp: Linear(65→64) → ReLU → Linear(64→64) → ReLU
  → context: (B, 64)

  per-slot:
    feat_i = concat(phi_i, context_broadcast)           # (B, K, 128)
    rho: Linear(128→64) → ReLU → Dropout → Linear(64→1)
  → output: (B, K, 1) → squeeze → (B, K)

파라미터 수: 약 21k (ResNet1D 250k 대비 12분의 1).
Seed: torch.manual_seed(42).

담당: 권성민 (Opus 4.7 보조)
"""

from __future__ import annotations

from pathlib import Path

import torch
from torch import nn
from torch.utils.data import DataLoader


class DeepSetsContext(nn.Module):
    """Permutation-invariant set encoder with per-slot output."""

    def __init__(
        self,
        in_features: int = 9,
        hidden: int = 64,
        dropout: float = 0.2,
    ) -> None:
        super().__init__()
        self.in_features = in_features
        self.hidden = hidden
        self.phi = nn.Sequential(
            nn.Linear(in_features, hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
        )
        self.context_mlp = nn.Sequential(
            nn.Linear(hidden + 1, hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
        )
        self.rho = nn.Sequential(
            nn.Linear(hidden * 2, hidden),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden, 1),
        )

    def forward(
        self, x: torch.Tensor, mask: torch.Tensor, log_n: torch.Tensor
    ) -> torch.Tensor:
        """
        Args:
            x: (B, K, F)
            mask: (B, K)
            log_n: (B,)

        Returns:
            (B, K) — log(scaled_amount_g) per slot.
        """
        phi_x = self.phi(x)                                  # (B, K, H)
        mask_e = mask.unsqueeze(-1)                          # (B, K, 1)
        sum_phi = (phi_x * mask_e).sum(dim=1)                # (B, H)
        count = mask_e.sum(dim=1).clamp_min(1.0)             # (B, 1)
        pooled = sum_phi / count                             # (B, H)
        context_in = torch.cat(
            [pooled, log_n.unsqueeze(-1)], dim=1
        )                                                    # (B, H+1)
        context = self.context_mlp(context_in)               # (B, H)
        context_e = context.unsqueeze(1).expand(-1, x.shape[1], -1)  # (B, K, H)
        per_slot = torch.cat([phi_x, context_e], dim=2)      # (B, K, 2H)
        out = self.rho(per_slot).squeeze(-1)                 # (B, K)
        return out


# ---------------------------------------------------------------------------
# Loss / train / eval
# ---------------------------------------------------------------------------

def masked_mse_loss_v2(
    pred: torch.Tensor, target: torch.Tensor, mask: torch.Tensor
) -> torch.Tensor:
    """v1과 동일한 마스크된 MSE.

    Args:
        pred: (B, K).
        target: (B, K).
        mask: (B, K).

    Returns:
        스칼라 loss.
    """
    diff_sq = ((pred - target) ** 2) * mask
    denom = mask.sum().clamp_min(1.0)
    return diff_sq.sum() / denom


def train_epoch_v2(
    model: DeepSetsContext,
    loader: DataLoader,
    optimizer: torch.optim.Optimizer,
    device: torch.device,
) -> float:
    """v2 모델 1 에폭 학습.

    DataLoader 배치 형식: (x, y, log_n, mask).

    Args:
        model: DeepSetsContext.
        loader: 배치 iterable.
        optimizer: Adam 등.
        device: 학습 디바이스.

    Returns:
        에폭 평균 loss.
    """
    model.train()
    total, n = 0.0, 0
    for x, y, log_n, mask in loader:
        x = x.to(device); y = y.to(device)
        log_n = log_n.to(device); mask = mask.to(device)
        optimizer.zero_grad()
        pred = model(x, mask, log_n)
        loss = masked_mse_loss_v2(pred, y, mask)
        loss.backward()
        optimizer.step()
        total += float(loss.item())
        n += 1
    return total / max(n, 1)


@torch.no_grad()
def evaluate_v2(
    model: DeepSetsContext,
    loader: DataLoader,
    device: torch.device,
) -> dict[str, float]:
    """v2 모델 평가 — loss + MAPE (원본 g 스케일).

    Args:
        model: DeepSetsContext.
        loader: 평가 iterable.
        device: 디바이스.

    Returns:
        {'loss': float, 'mape': float}.
    """
    model.eval()
    total_loss, total_ape, total_valid = 0.0, 0.0, 0
    for x, y, log_n, mask in loader:
        x = x.to(device); y = y.to(device)
        log_n = log_n.to(device); mask = mask.to(device)
        pred = model(x, mask, log_n)
        loss = masked_mse_loss_v2(pred, y, mask)
        pred_g = torch.exp(pred) * mask
        true_g = torch.exp(y) * mask
        ape = torch.abs(pred_g - true_g) / true_g.clamp_min(1e-8) * mask
        total_loss += float(loss.item()) * float(mask.sum().item())
        total_ape += float(ape.sum().item())
        total_valid += int(mask.sum().item())
    valid = max(total_valid, 1)
    return {"loss": total_loss / valid, "mape": total_ape / valid * 100.0}


# ---------------------------------------------------------------------------
# Save / Load
# ---------------------------------------------------------------------------

def save_model_v2(model: DeepSetsContext, path: str | Path) -> None:
    """모델 + 메타 저장."""
    payload = {
        "state_dict": model.state_dict(),
        "in_features": model.in_features,
        "hidden": model.hidden,
    }
    torch.save(payload, path)


def load_model_v2(
    path: str | Path, device: torch.device | str = "cpu"
) -> DeepSetsContext:
    """저장된 v2 모델 로드 (eval 모드)."""
    payload = torch.load(path, map_location=device, weights_only=False)
    model = DeepSetsContext(
        in_features=payload["in_features"], hidden=payload["hidden"]
    )
    model.load_state_dict(payload["state_dict"])
    model.to(device)
    model.eval()
    return model
