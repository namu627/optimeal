"""
nn_scaling_engine.py
====================
1D ResNet 기반 비선형 스케일링 엔진. Rule-based 엔진(fallback.py)을 대체한다.

학습 타겟:
  log(Y_g) = log(base_amount_g) + b_feedback × log(N)
  → 마스킹된 슬롯에서 MSELoss 적용 (b_feedback는 사전 lookup)

아키텍처 (2026-05-20: Ch3 group_type 추가):
  Input (B, 4, 30)
    → Conv1d(4→32, k=3, p=1) + BN + ReLU            # stem
    → ResBlock(32→32)  skip=identity
    → ResBlock(32→64)  skip=Conv1d(32→64, k=1)
    → Flatten (B, 64×30=1920)
    → Concat log(N) → (B, 1921)
    → Linear(1921→128) + ReLU + Dropout(0.3)
    → Linear(128→30)
  Output (B, 30) — log(scaled_amount_g)

  Optimizer: Adam(lr=1e-3, weight_decay=1e-4)
  Seed: torch.manual_seed(42)

추론 후 역변환:
  scaled_amount_g = exp(model_output) × valid_mask

담당: 권성민
기준 문서: CLAUDE.md (모듈 2, 2026-05-19), ADR-001
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

from nn_training_data import (
    CATEGORY_ID,
    GROUP_TYPE_ID,
    GROUP_TYPE_KOR2EN,
    N_SLOTS,
    SLOT_LAYOUT,
    normalize_category,
    normalize_group_type,
)


# ---------------------------------------------------------------------------
# 모델 정의
# ---------------------------------------------------------------------------

class ResBlock1D(nn.Module):
    """
    1D ResBlock: Conv → BN → ReLU → Conv → BN → (+ skip) → ReLU.

    in_channels != out_channels이면 skip은 1×1 Conv로 차원 맞춤.
    """

    def __init__(self, in_channels: int, out_channels: int) -> None:
        super().__init__()
        self.conv1 = nn.Conv1d(in_channels, out_channels, kernel_size=3, padding=1)
        self.bn1 = nn.BatchNorm1d(out_channels)
        self.conv2 = nn.Conv1d(out_channels, out_channels, kernel_size=3, padding=1)
        self.bn2 = nn.BatchNorm1d(out_channels)
        if in_channels != out_channels:
            self.skip: nn.Module = nn.Conv1d(in_channels, out_channels, kernel_size=1)
        else:
            self.skip = nn.Identity()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        identity = self.skip(x)
        out = F.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        return F.relu(out + identity)


class ResNet1D(nn.Module):
    """
    1D ResNet 스케일링 모델.

    Args:
        n_slots: 입력/출력 슬롯 수 (기본 30).
        dropout: FC 드롭아웃 비율 (기본 0.3).
    """

    def __init__(
        self, n_slots: int = N_SLOTS, in_channels: int = 4, dropout: float = 0.3
    ) -> None:
        super().__init__()
        self.n_slots = n_slots
        self.in_channels = in_channels
        self.stem = nn.Sequential(
            nn.Conv1d(in_channels, 32, kernel_size=3, padding=1),
            nn.BatchNorm1d(32),
            nn.ReLU(),
        )
        self.res1 = ResBlock1D(32, 32)
        self.res2 = ResBlock1D(32, 64)
        self.fc = nn.Sequential(
            nn.Linear(64 * n_slots + 1, 128),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(128, n_slots),
        )

    def forward(self, x: torch.Tensor, log_n: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: (B, in_channels, n_slots) — Ch0=log(base), Ch1=cat_id, Ch2=mask,
                                            Ch3=group_type_id (in_channels=4 기본)
            log_n: (B,) — log(N)

        Returns:
            (B, n_slots) — log(scaled_amount_g) 예측값
        """
        h = self.stem(x)
        h = self.res1(h)
        h = self.res2(h)
        h = torch.flatten(h, start_dim=1)            # (B, 64*n_slots)
        h = torch.cat([h, log_n.unsqueeze(-1)], dim=1)  # (B, 64*n_slots + 1)
        return self.fc(h)


# ---------------------------------------------------------------------------
# 학습/평가 루프
# ---------------------------------------------------------------------------

def masked_mse_loss(
    pred: torch.Tensor, target: torch.Tensor, mask: torch.Tensor
) -> torch.Tensor:
    """
    valid_mask=1 슬롯에서만 MSE 계산. 분모는 유효 슬롯 수.

    Args:
        pred: (B, n_slots) 예측 log(Y)
        target: (B, n_slots) 정답 log(Y)
        mask: (B, n_slots) 유효 슬롯 마스크 (1.0/0.0)

    Returns:
        스칼라 loss 텐서.
    """
    diff_sq = ((pred - target) ** 2) * mask
    denom = mask.sum().clamp_min(1.0)
    return diff_sq.sum() / denom


def train_epoch(
    model: ResNet1D,
    loader: Iterable[tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]],
    optimizer: torch.optim.Optimizer,
    device: torch.device,
) -> float:
    """
    한 에폭 학습. 배치마다 forward → loss → backward → step.

    Args:
        model: ResNet1D 인스턴스.
        loader: (X, Y, logN, mask) 배치 iterable.
        optimizer: Adam 등 옵티마이저.
        device: 학습 디바이스.

    Returns:
        에폭 평균 loss.
    """
    model.train()
    total_loss = 0.0
    n_batches = 0
    for x, y, log_n, mask in loader:
        x, y = x.to(device), y.to(device)
        log_n, mask = log_n.to(device), mask.to(device)
        optimizer.zero_grad()
        pred = model(x, log_n)
        loss = masked_mse_loss(pred, y, mask)
        loss.backward()
        optimizer.step()
        total_loss += float(loss.item())
        n_batches += 1
    return total_loss / max(n_batches, 1)


@torch.no_grad()
def evaluate(
    model: ResNet1D,
    loader: Iterable[tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]],
    device: torch.device,
) -> dict[str, float]:
    """
    검증 세트 평가: loss + MAPE (원본 g 스케일).

    Args:
        model: ResNet1D 인스턴스.
        loader: 검증 배치 iterable.
        device: 평가 디바이스.

    Returns:
        {'loss': float, 'mape': float} — MAPE는 % 단위.
    """
    model.eval()
    total_loss, total_mape, total_valid = 0.0, 0.0, 0
    for x, y, log_n, mask in loader:
        x, y = x.to(device), y.to(device)
        log_n, mask = log_n.to(device), mask.to(device)
        pred = model(x, log_n)
        loss = masked_mse_loss(pred, y, mask)
        # MAPE: |exp(pred) - exp(y)| / exp(y) on valid slots
        pred_g = torch.exp(pred) * mask
        true_g = torch.exp(y) * mask
        ape = torch.abs(pred_g - true_g) / true_g.clamp_min(1e-8) * mask
        total_loss += float(loss.item()) * float(mask.sum().item())
        total_mape += float(ape.sum().item())
        total_valid += int(mask.sum().item())
    valid = max(total_valid, 1)
    return {
        "loss": total_loss / valid,
        "mape": total_mape / valid * 100.0,
    }


# ---------------------------------------------------------------------------
# 저장/로드
# ---------------------------------------------------------------------------

def save_model(model: ResNet1D, path: str | Path) -> None:
    """모델 state_dict + 메타를 파일로 저장.

    Args:
        model: 저장할 모델.
        path: 저장 경로 (.pt).
    """
    payload = {
        "state_dict": model.state_dict(),
        "n_slots": model.n_slots,
        "in_channels": model.in_channels,
    }
    torch.save(payload, path)


def load_model(path: str | Path, device: torch.device | str = "cpu") -> ResNet1D:
    """저장된 모델을 로드.

    Args:
        path: 모델 파일 경로.
        device: 로드 디바이스.

    Returns:
        ResNet1D 인스턴스 (eval 모드).
    """
    payload = torch.load(path, map_location=device, weights_only=False)
    in_ch = int(payload.get("in_channels", 4))
    model = ResNet1D(n_slots=payload["n_slots"], in_channels=in_ch)
    model.load_state_dict(payload["state_dict"])
    model.to(device)
    model.eval()
    return model


# ---------------------------------------------------------------------------
# 추론 인터페이스
# ---------------------------------------------------------------------------

@dataclass
class IngredientInput:
    """predict() 입력 재료 항목.

    Attributes:
        name: 재료명 (식별용, 모델 입력은 아님).
        base: 1인분 투입량 (g).
        role: 'main'|'sub'|'seasoning' 중 하나 (derived_category가 없으면 사용).
        derived_category: 'oil_fat'|'water_base' 또는 None.
    """
    name: str
    base: float
    role: str | None = None
    derived_category: str | None = None


def predict(
    model: ResNet1D,
    group_type: str,
    ingredients: list[IngredientInput] | list[dict],
    n: int,
    device: torch.device | str = "cpu",
) -> list[dict]:
    """
    1인분 레시피를 N인분으로 변환.

    Args:
        model: 학습된 ResNet1D.
        group_type: '비가열'|'습열'|'건열' (한국어) 또는
                    'no_heat'|'moist_heat'|'dry_heat' (영어).
        ingredients: IngredientInput 또는 dict 리스트.
                     dict의 경우 IngredientInput과 동일 필드 사용.
        n: 목표 인원수.
        device: 추론 디바이스.

    Returns:
        [{'name': str, 'base': float, 'scaled': float, 'category': str}, ...].
        입력 순서 유지.
    """
    group_type_en = (
        normalize_group_type(group_type) if group_type in GROUP_TYPE_KOR2EN else group_type
    )
    items = [IngredientInput(**i) if isinstance(i, dict) else i for i in ingredients]
    categories = [
        normalize_category(it.derived_category, it.role) for it in items
    ]
    slot_idx = _assign_slots_for_predict(items, categories)
    x, log_n_tensor = _build_predict_tensor(
        items, categories, slot_idx, n, group_type_en
    )

    model.eval()
    device_t = torch.device(device) if isinstance(device, str) else device
    x = x.to(device_t)
    log_n_tensor = log_n_tensor.to(device_t)
    with torch.no_grad():
        pred = model(x, log_n_tensor)  # (1, 30)
    pred_g = torch.exp(pred[0]).cpu().numpy()

    return [
        {
            "name": it.name,
            "base": it.base,
            "scaled": float(pred_g[slot_idx[i]]) if slot_idx[i] is not None else float("nan"),
            "category": categories[i],
        }
        for i, it in enumerate(items)
    ]


def _assign_slots_for_predict(
    items: list[IngredientInput], categories: list[str | None]
) -> list[int | None]:
    """입력 재료 리스트에 대해 슬롯 인덱스 할당. 카테고리별 base 내림차순.

    Args:
        items: predict() 입력 재료 리스트.
        categories: normalize_category()로 정규화된 카테고리 리스트.

    Returns:
        items와 동일 길이 리스트. 각 원소는 슬롯 인덱스 또는 None(매핑 실패).
    """
    slot_idx: list[int | None] = [None] * len(items)
    for cat, (start, size) in SLOT_LAYOUT.items():
        idx_in_cat = [i for i, c in enumerate(categories) if c == cat]
        idx_in_cat.sort(key=lambda i: items[i].base, reverse=True)
        for offset, i in enumerate(idx_in_cat[:size]):
            slot_idx[i] = start + offset
    return slot_idx


def _build_predict_tensor(
    items: list[IngredientInput],
    categories: list[str | None],
    slot_idx: list[int | None],
    n: int,
    group_type_en: str,
) -> tuple[torch.Tensor, torch.Tensor]:
    """predict()용 (X, logN) 텐서 생성 (배치 1).

    Args:
        items: predict() 입력 재료 리스트.
        categories: 정규화된 카테고리 리스트.
        slot_idx: _assign_slots_for_predict() 결과.
        n: 목표 인원수.
        group_type_en: 'dry_heat'|'moist_heat'|'no_heat'.

    Returns:
        x: (1, 4, 30) float32, log_n: (1,) float32.
    """
    log_base = np.zeros(N_SLOTS, dtype=np.float32)
    cat_id = np.zeros(N_SLOTS, dtype=np.float32)
    mask = np.zeros(N_SLOTS, dtype=np.float32)
    gt_channel = np.zeros(N_SLOTS, dtype=np.float32)
    gt_val = GROUP_TYPE_ID[group_type_en]
    for i, sl in enumerate(slot_idx):
        if sl is None or categories[i] is None:
            continue
        log_base[sl] = np.log(items[i].base)
        cat_id[sl] = CATEGORY_ID[categories[i]]
        mask[sl] = 1.0
        gt_channel[sl] = gt_val
    x = np.stack([log_base, cat_id, mask, gt_channel], axis=0)[None, :, :]
    return torch.from_numpy(x), torch.tensor([np.log(float(n))], dtype=torch.float32)
