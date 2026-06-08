"""
loco_cv.py
==========
Leave-One-Cell-Out Cross-Validation.

목적:
  NN이 단순 lookup 대비 추가 가치가 있는지 검증한다.
  학습 타겟이 합성(Y = base × N^b_feedback)이므로 절대 MAPE는 의미가 없고,
  "lookup이 미커버한 셀에 대한 외삽 능력"이 NN의 유일한 잠재 우위다 (분석 md §4).

설계:
  1. (group_type × category) 셀 중 데이터가 존재하는 13개를 후보로.
  2. 각 셀을 hold-out:
     - 학습: 그 셀의 모든 샘플을 제외하고 나머지로 NN 학습.
     - 평가: 그 셀의 샘플에 대해 NN과 lookup_baseline(category_mean) 비교.
  3. 비교 지표:
     - NN_MAPE: 합성 타겟 vs NN 예측
     - LOOKUP_MAPE: 합성 타겟 vs lookup(category_mean) 예측
     - DELTA = LOOKUP_MAPE - NN_MAPE (양수면 NN 우위)

세 가지 시나리오 (분석 md §5):
  - NN < lookup: NN 외삽 우위. 논문 주장 가능.
  - NN ≈ lookup: 동치. NN을 "smooth 보간기"로 위상 재정의.
  - NN > lookup: NN 폐기. ADR-006 부분 철회.

담당: 권성민 (Opus 4.7 보조)
기준 문서: docs/for_reports/nn_results_analysis_20260520.md
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch import optim
from torch.utils.data import DataLoader, TensorDataset

_ENGINE_DIR = Path(__file__).parent
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))

from nn_scaling_engine import ResNet1D, masked_mse_loss, train_epoch  # noqa: E402
from nn_training_data import (  # noqa: E402
    DEFAULT_N_VALUES,
    GROUP_TYPE_KOR2EN,
    N_SLOTS,
    SLOT_LAYOUT,
    build_recipe_records,
    expand_and_tensorize,
    load_b_feedback_table,
    normalize_category,
)


SEED: int = 42
EPOCHS_PER_FOLD: int = 40  # 시간 절약, 5-Fold 정식학습보다 짧게
BATCH_SIZE: int = 64
LR: float = 1e-3
WD: float = 1e-4

# 비교 시 사용할 슬롯-카테고리 매핑 (역)
SLOT_TO_CATEGORY: dict[int, str] = {}
for cat, (start, size) in SLOT_LAYOUT.items():
    for s in range(start, start + size):
        SLOT_TO_CATEGORY[s] = cat


# ---------------------------------------------------------------------------
# 보조 함수
# ---------------------------------------------------------------------------

def set_seed(seed: int = SEED) -> None:
    """학습 재현성 위한 시드 고정."""
    torch.manual_seed(seed)
    np.random.seed(seed)
    random.seed(seed)


def pick_device() -> torch.device:
    """mps > cpu."""
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def cell_sample_mask(
    records: list,
    n_values: tuple[int, ...],
    hold_group_type: str,
    hold_category: str,
) -> tuple[np.ndarray, np.ndarray]:
    """
    각 (record, N) 샘플별로 hold-out 셀에 속하는 유효 슬롯 마스크 생성.

    Args:
        records: build_recipe_records 결과.
        n_values: N 확장 값 (샘플 수 결정).
        hold_group_type: 'dry_heat'|'moist_heat'|'no_heat'.
        hold_category: '주재료'|'부재료'|'양념류'|'수분류'|'유지류'.

    Returns:
        sample_has_cell: (M,) bool — 샘플이 hold-out 셀 슬롯을 하나라도 가지는가
        slot_in_cell: (M, N_SLOTS) bool — 각 슬롯이 hold-out 셀인지
    """
    cat_start, cat_size = SLOT_LAYOUT[hold_category]
    cat_slots = list(range(cat_start, cat_start + cat_size))

    m = len(records) * len(n_values)
    sample_has_cell = np.zeros(m, dtype=bool)
    slot_in_cell = np.zeros((m, N_SLOTS), dtype=bool)

    idx = 0
    for rec in records:
        rec_match = rec.group_type == hold_group_type
        valid_cell_slots = (
            [s for s in cat_slots if rec.mask[s]] if rec_match else []
        )
        for _ in n_values:
            if valid_cell_slots:
                sample_has_cell[idx] = True
                for s in valid_cell_slots:
                    slot_in_cell[idx, s] = True
            idx += 1
    return sample_has_cell, slot_in_cell


def make_loader(
    indices: np.ndarray,
    x: torch.Tensor,
    y: torch.Tensor,
    logn: torch.Tensor,
    mask: torch.Tensor,
    batch_size: int,
    shuffle: bool,
) -> DataLoader:
    """주어진 인덱스로 DataLoader 생성."""
    idx_t = torch.from_numpy(indices)
    ds = TensorDataset(x[idx_t], y[idx_t], logn[idx_t], mask[idx_t])
    return DataLoader(ds, batch_size=batch_size, shuffle=shuffle)


def category_mean_lookup(
    b_table: dict[tuple[str, str], float],
    category: str,
) -> float:
    """
    lookup baseline: 동일 카테고리의 정의된 모든 b의 평균.
    NN과 동일한 정보 제약(hold-out 셀의 직접 b는 모름) 하에서의 외삽.

    Args:
        b_table: load_b_feedback_table() 결과 (단, hold-out 셀 제외 상태로 전달).
        category: 한국어 카테고리.

    Returns:
        평균 b. 그 카테고리 행이 없으면 전체 평균 fallback.
    """
    matched = [b for (g, c), b in b_table.items() if c == category]
    if matched:
        return float(np.mean(matched))
    # 카테고리 행도 없으면 전체 평균
    return float(np.mean(list(b_table.values())))


# ---------------------------------------------------------------------------
# 단일 셀 LOCO 실행
# ---------------------------------------------------------------------------

def run_one_cell(
    hold_group_type: str,
    hold_category: str,
    df_b: pd.DataFrame,
    b_table_full: dict[tuple[str, str], float],
    n_values: tuple[int, ...],
    device: torch.device,
    epochs: int,
) -> dict[str, float]:
    """단일 (group_type, category) 셀 hold-out 실험.

    Args:
        hold_group_type: 영어 group_type.
        hold_category: 한국어 카테고리.
        df_b: df_B.csv DataFrame.
        b_table_full: 전체 b_feedback 표 (변경하지 않음).
        n_values: N 확장 값.
        device: 학습 디바이스.
        epochs: 학습 에폭.

    Returns:
        결과 사전.
    """
    # 1. b_table에서 해당 셀만 제외한 사본 — NN 학습 데이터 생성용
    b_table_loco = {k: v for k, v in b_table_full.items() if k != (hold_group_type, hold_category)}

    # 2. records (b 부여) 생성. hold-out 셀 슬롯은 fallback(B_FALLBACK)으로 채워지지만,
    #    어차피 학습/평가에서 그 셀 슬롯의 b는 b_table_full로부터 따로 계산하므로 무방.
    #    여기서는 NN 학습 데이터를 만들 때 정답 Y에 hold-out 셀의 진짜 b를 안 쓰면 됨.
    #    구현 단순화: records 자체는 hold-out과 무관하게 b_table_full로 만들고,
    #    학습 시 그 셀 슬롯만 mask로 가린다.
    records = build_recipe_records(df_b, b_table_full)
    x, y, logn, mask, _ = expand_and_tensorize(records, n_values=n_values)

    # 3. hold-out 슬롯 정보
    _, slot_in_cell = cell_sample_mask(records, n_values, hold_group_type, hold_category)
    slot_in_cell_t = torch.from_numpy(slot_in_cell.astype(np.float32))

    # 4. 학습용 mask = 기존 mask × (1 - slot_in_cell) → hold-out 셀 슬롯 학습 신호 제거
    train_mask = mask * (1.0 - slot_in_cell_t)

    # 5. 평가용 mask = 기존 mask × slot_in_cell → hold-out 셀 슬롯만 평가
    eval_mask = mask * slot_in_cell_t

    # 평가 슬롯이 0이면 그 셀은 데이터에 없음 — skip
    n_eval_slots = int(eval_mask.sum().item())
    if n_eval_slots == 0:
        return {
            "group_type": hold_group_type,
            "category": hold_category,
            "n_eval_slots": 0,
            "nn_mape": float("nan"),
            "lookup_mape": float("nan"),
            "delta": float("nan"),
            "b_true": float("nan"),
            "b_lookup": float("nan"),
            "note": "no_data",
        }

    # 6. NN 학습 — train_mask 사용 (Y는 그대로, mask만 변경)
    set_seed(SEED)
    model = ResNet1D().to(device)
    opt = optim.Adam(model.parameters(), lr=LR, weight_decay=WD)

    n_total = x.shape[0]
    indices = np.arange(n_total)
    loader = make_loader(indices, x, y, logn, train_mask, BATCH_SIZE, shuffle=True)

    for _ in range(1, epochs + 1):
        train_epoch(model, loader, opt, device)

    # 7. 평가 — eval_mask로 한정
    model.eval()
    eval_loader = make_loader(indices, x, y, logn, eval_mask, BATCH_SIZE, shuffle=False)
    nn_mape, lookup_mape = _eval_two_models(
        model, eval_loader, b_table_full, b_table_loco, device,
        hold_group_type, hold_category,
    )

    b_true = b_table_full[(hold_group_type, hold_category)]
    b_lookup = category_mean_lookup(b_table_loco, hold_category)

    return {
        "group_type": hold_group_type,
        "category": hold_category,
        "n_eval_slots": n_eval_slots,
        "nn_mape": nn_mape,
        "lookup_mape": lookup_mape,
        "delta": lookup_mape - nn_mape,
        "b_true": float(b_true),
        "b_lookup": float(b_lookup),
        "note": "",
    }


@torch.no_grad()
def _eval_two_models(
    model: ResNet1D,
    loader: DataLoader,
    b_table_full: dict[tuple[str, str], float],
    b_table_loco: dict[tuple[str, str], float],
    device: torch.device,
    hold_group_type: str,
    hold_category: str,
) -> tuple[float, float]:
    """hold-out 슬롯에 대한 NN MAPE와 lookup(category_mean) MAPE 동시 계산.

    Returns:
        (nn_mape, lookup_mape) 모두 % 단위.
    """
    nn_ape_sum, lk_ape_sum, n_valid = 0.0, 0.0, 0
    b_lookup_val = category_mean_lookup(b_table_loco, hold_category)
    log_b_lookup = float(b_lookup_val)

    for x_b, y_b, logn_b, mask_b in loader:
        x_b = x_b.to(device); y_b = y_b.to(device)
        logn_b = logn_b.to(device); mask_b = mask_b.to(device)

        pred = model(x_b, logn_b)                                # log(Y_NN)
        # lookup 예측: log_base + b_lookup * log_n
        log_base = x_b[:, 0, :]                                  # (B, 30)
        pred_lookup = log_base + log_b_lookup * logn_b.unsqueeze(-1)

        true_g = torch.exp(y_b) * mask_b
        pred_g_nn = torch.exp(pred) * mask_b
        pred_g_lk = torch.exp(pred_lookup) * mask_b

        denom = true_g.clamp_min(1e-8)
        nn_ape = (torch.abs(pred_g_nn - true_g) / denom) * mask_b
        lk_ape = (torch.abs(pred_g_lk - true_g) / denom) * mask_b

        nn_ape_sum += float(nn_ape.sum().item())
        lk_ape_sum += float(lk_ape.sum().item())
        n_valid += int(mask_b.sum().item())

    n_valid = max(n_valid, 1)
    return nn_ape_sum / n_valid * 100.0, lk_ape_sum / n_valid * 100.0


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------

def main() -> None:
    """13개 정의 셀에 대해 LOCO 실행."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=EPOCHS_PER_FOLD)
    parser.add_argument(
        "--out",
        type=str,
        default=str(_ENGINE_DIR / "loco_results_20260520.csv"),
        help="결과 CSV 저장 경로",
    )
    args = parser.parse_args()

    device = pick_device()
    print(f"[setup] device={device} epochs_per_cell={args.epochs}")

    df_b_path = _ENGINE_DIR.parent.parent / "df_B.csv"
    df_b = pd.read_csv(df_b_path)
    b_table_full = load_b_feedback_table()
    print(f"[data] df_B rows={len(df_b)}, b_table cells={len(b_table_full)}")

    # 13개 정의 셀 중 df_B에 실제 데이터가 있는 셀만 후보
    cells_in_data = set()
    for _, row in df_b.iterrows():
        gt_kor = str(row["group_type"])
        gt_en = GROUP_TYPE_KOR2EN.get(gt_kor)
        cat = normalize_category(row.get("derived_category"), row.get("role"))
        if gt_en and cat:
            cells_in_data.add((gt_en, cat))

    candidates = [k for k in b_table_full.keys() if k in cells_in_data]
    print(f"[loco] {len(candidates)}개 셀 후보 (b 정의 ∩ 데이터 존재):")
    for g, c in sorted(candidates):
        print(f"  - ({g}, {c})")

    results: list[dict[str, float]] = []
    for i, (gt, cat) in enumerate(sorted(candidates), 1):
        print(f"\n[loco {i}/{len(candidates)}] hold-out ({gt}, {cat})")
        res = run_one_cell(
            gt, cat, df_b, b_table_full, DEFAULT_N_VALUES, device, args.epochs
        )
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

    # 요약 통계
    valid = df_res[df_res["n_eval_slots"] > 0]
    nn_wins = int((valid["delta"] > 0).sum())
    lk_wins = int((valid["delta"] < 0).sum())
    mean_delta = float(valid["delta"].mean()) if len(valid) else float("nan")
    print(f"[loco] NN 우위 셀: {nn_wins}/{len(valid)}, lookup 우위: {lk_wins}/{len(valid)}")
    print(f"[loco] 평균 Δ (LOOKUP - NN) = {mean_delta:+.2f}%p")
    if mean_delta > 1.0:
        print("[verdict] NN < LOOKUP 외삽 우위 — 시나리오 (a) 논문 주장 가능")
    elif mean_delta < -1.0:
        print("[verdict] NN > LOOKUP — 시나리오 (c) lookup 회귀 검토 필요")
    else:
        print("[verdict] NN ≈ LOOKUP — 시나리오 (b) NN을 smooth 보간기로 위상 재정의")


if __name__ == "__main__":
    main()
