"""
hybrid_engine.py
================
NN_v2 + lookup category_mean 하이브리드 추론기 (ADR-007 채택).

규칙:
  - (group_type, ingredient_category) 셀이 b_feedback 표에 정의되어 있으면 → NN_v2 추론
  - 정의되어 있지 않으면 → lookup category_mean fallback (같은 카테고리 다른 셀들의 b 평균)

각 재료별 추론 결과에 다음 메타 정보를 함께 반환하여 발표 시 계산 과정을 드러낸다:
  - method: 'nn_v2' | 'lookup_category_mean'
  - b_value: 사용된 b
  - formula_str: 식 표현 'Y = base × N^b'
  - reason: 분기 사유 ("학습 셀" 또는 "미커버 셀")

담당: 권성민 (Opus 4.7 보조)
기준: ADR-007 (docs/decisions_summary_v5_8.md §ADR-007)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch

_ROOT = Path(__file__).parent.parent
_ENGINE_DIR = _ROOT / "module_2" / "src" / "engine"
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))

from nn_scaling_engine_v2 import DeepSetsContext, load_model_v2  # noqa: E402
from nn_training_data import (  # noqa: E402
    GROUP_TYPE_KOR2EN,
    SLOT_LAYOUT,
    load_b_feedback_table,
    normalize_category,
    normalize_group_type,
)
from nn_training_data_v2 import (  # noqa: E402
    CAT_TO_OH_IDX,
    CATEGORY_ORDER,
    GT_TO_OH_IDX,
    GROUP_TYPE_ORDER,
    N_FEATURES,
)

N_SLOTS: int = 30
DEFAULT_MODEL_PATH: Path = _ENGINE_DIR / "nn_model_v2.pt"


@dataclass
class IngredientItem:
    """레시피 한 재료.

    Attributes:
        name: 재료명 (표시용).
        base: 1인분 투입량 (g).
        role: 'main' | 'sub' | 'seasoning' (한국어 normalize용).
        derived_category: 'oil_fat' | 'water_base' | None.
    """
    name: str
    base: float
    role: str | None = None
    derived_category: str | None = None


@dataclass
class IngredientResult:
    """추론 결과 — 발표 표시용 메타 포함.

    Attributes:
        name: 재료명.
        base: 1인분 투입량 (g).
        scaled: N인분 예측량 (g).
        category: 정규화 한국어 카테고리.
        method: 'nn_v2' or 'lookup_category_mean'.
        b_value: 사용 b.
        formula_str: 표시용 식.
        reason: 분기 사유.
    """
    name: str
    base: float
    scaled: float
    category: str
    method: str
    b_value: float
    formula_str: str
    reason: str


def category_mean_b(
    b_table: dict[tuple[str, str], float], category: str
) -> float:
    """동일 카테고리의 정의된 셀 b 평균. 없으면 전체 평균.

    Args:
        b_table: load_b_feedback_table 결과.
        category: 한국어 카테고리.

    Returns:
        평균 b.
    """
    matched = [b for (_, c), b in b_table.items() if c == category]
    if matched:
        return float(np.mean(matched))
    return float(np.mean(list(b_table.values())))


def _assign_slots(items: list[IngredientItem], categories: list[str | None]) -> list[int | None]:
    """카테고리별 base 내림차순 → 슬롯 인덱스 할당.

    Args:
        items: 재료 리스트.
        categories: 각 재료의 한국어 카테고리.

    Returns:
        슬롯 인덱스 리스트 (매핑 실패 시 None).
    """
    out: list[int | None] = [None] * len(items)
    for cat, (start, size) in SLOT_LAYOUT.items():
        idx_in_cat = [i for i, c in enumerate(categories) if c == cat]
        idx_in_cat.sort(key=lambda i: items[i].base, reverse=True)
        for off, i in enumerate(idx_in_cat[:size]):
            out[i] = start + off
    return out


def _build_v2_tensor(
    items: list[IngredientItem],
    categories: list[str | None],
    slot_idx: list[int | None],
    group_type_en: str,
    n: int,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """v2 입력 텐서 (1, 30, 9) + mask + logN 생성."""
    x = np.zeros((N_SLOTS, N_FEATURES), dtype=np.float32)
    mask = np.zeros(N_SLOTS, dtype=np.float32)
    gt_idx = GT_TO_OH_IDX[group_type_en]
    for i, sl in enumerate(slot_idx):
        if sl is None or categories[i] is None:
            continue
        x[sl, 0] = float(np.log(items[i].base))
        x[sl, 1 + CAT_TO_OH_IDX[categories[i]]] = 1.0
        x[sl, 1 + len(CATEGORY_ORDER) + gt_idx] = 1.0
        mask[sl] = 1.0
    x_t = torch.from_numpy(x).unsqueeze(0)             # (1, 30, 9)
    mask_t = torch.from_numpy(mask).unsqueeze(0)       # (1, 30)
    logn_t = torch.tensor([np.log(float(n))], dtype=torch.float32)
    return x_t, mask_t, logn_t


class HybridScaler:
    """NN_v2 + lookup category_mean 하이브리드 추론기."""

    def __init__(
        self,
        model_path: str | Path = DEFAULT_MODEL_PATH,
        device: str = "cpu",
    ) -> None:
        """학습된 v2 모델과 b_feedback 표 로드.

        Args:
            model_path: nn_model_v2.pt 경로.
            device: 추론 디바이스. 'cpu'가 안정적.
        """
        self.device = torch.device(device)
        self.model: DeepSetsContext = load_model_v2(model_path, device=self.device)
        self.b_table = load_b_feedback_table()
        # 룩업 평균 캐시
        self._cache_lookup_b: dict[str, float] = {}

    def lookup_b(self, category: str) -> float:
        """카테고리별 lookup b (캐시)."""
        if category not in self._cache_lookup_b:
            self._cache_lookup_b[category] = category_mean_b(self.b_table, category)
        return self._cache_lookup_b[category]

    def predict(
        self,
        group_type: str,
        items: list[IngredientItem],
        n: int,
    ) -> list[IngredientResult]:
        """레시피 1인분 + N인분 목표 → 재료별 예측 + 분기 메타.

        Args:
            group_type: '비가열'|'습열'|'건열' 또는 영어.
            items: 재료 리스트.
            n: 목표 인원수.

        Returns:
            IngredientResult 리스트 (입력 순서 유지).
        """
        gt_en = (
            normalize_group_type(group_type)
            if group_type in GROUP_TYPE_KOR2EN
            else group_type
        )

        # 카테고리 정규화
        categories: list[str | None] = [
            normalize_category(it.derived_category, it.role) for it in items
        ]
        slot_idx = _assign_slots(items, categories)

        # NN 예측 텐서 한 번에 (slot 결과 캐싱)
        x_t, mask_t, logn_t = _build_v2_tensor(items, categories, slot_idx, gt_en, n)
        with torch.no_grad():
            log_y = self.model(
                x_t.to(self.device), mask_t.to(self.device), logn_t.to(self.device)
            )
        nn_pred_g = torch.exp(log_y[0]).cpu().numpy()  # (30,)

        results: list[IngredientResult] = []
        for i, it in enumerate(items):
            cat = categories[i]
            sl = slot_idx[i]
            if cat is None:
                # 카테고리 자체 분류 실패 — 매우 드묾 (엑셀 role 결손 등)
                results.append(IngredientResult(
                    name=it.name, base=it.base, scaled=float("nan"),
                    category="미분류", method="skip",
                    b_value=float("nan"), formula_str="",
                    reason="카테고리 분류 실패 (role/derived_category 결손)",
                ))
                continue

            cell_defined = (gt_en, cat) in self.b_table

            # NN_v2 우선 적용 조건: 셀이 학습 데이터에 있고, 슬롯 배정 성공
            # 슬롯 배정 실패(카테고리별 슬롯 사이즈 초과)는 lookup으로 회피
            use_nn = cell_defined and (sl is not None)

            if use_nn:
                method = "nn_v2"
                if n > 1 and nn_pred_g[sl] > 0:
                    b_eff = float(np.log(nn_pred_g[sl] / it.base) / np.log(n))
                else:
                    b_eff = float("nan")
                b_value = b_eff
                scaled = float(nn_pred_g[sl])
                reason = f"학습 셀 ({gt_en} × {cat})"
                formula = (
                    f"Y = base × N^b_NN = {it.base:.2f} × {n}^{b_eff:.4f}"
                )
            else:
                method = "lookup_category_mean"
                b_value = self.lookup_b(cat)
                scaled = float(it.base * (n ** b_value))
                if not cell_defined:
                    reason = f"미커버 셀 ({gt_en} × {cat}) → 카테고리 평균 fallback"
                else:
                    # 학습 셀이지만 같은 카테고리 재료 다수로 슬롯 초과
                    cap = SLOT_LAYOUT[cat][1]
                    reason = (
                        f"학습 셀이지만 {cat} 슬롯 한계({cap}) 초과 → lookup 대체"
                    )
                formula = (
                    f"Y = base × N^b_lookup = {it.base:.2f} × {n}^{b_value:.4f}"
                )

            results.append(IngredientResult(
                name=it.name, base=it.base, scaled=scaled,
                category=cat, method=method,
                b_value=b_value, formula_str=formula, reason=reason,
            ))

        return results
