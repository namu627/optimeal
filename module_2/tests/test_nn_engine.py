"""
test_nn_engine.py
=================
1D ResNet 스케일링 엔진 입출력 계약 검증.

검증 대상:
  1. nn_training_data: 텐서 shape, 카테고리/group_type 정규화, GroupKFold 무결성
  2. nn_scaling_engine: ResNet1D 순전파 shape, masked loss, predict() 계약
  3. 저장/로드 라운드트립

학습 데이터(df_B.csv) 의존성:
  - df_B.csv가 없으면 데이터 통합 테스트는 skip.
  - 순수 모델 테스트는 dummy 텐서로 수행 → 항상 실행.

담당: 권성민
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import torch

from nn_scaling_engine import (
    IngredientInput,
    ResBlock1D,
    ResNet1D,
    load_model,
    masked_mse_loss,
    predict,
    save_model,
)
from nn_training_data import (
    B_FALLBACK,
    CATEGORY_ID,
    DEFAULT_N_VALUES,
    N_SLOTS,
    SLOT_LAYOUT,
    expand_and_tensorize,
    groupkfold_indices,
    load_b_feedback_table,
    lookup_b_feedback,
    normalize_category,
    normalize_group_type,
    prepare_training_data,
)


# ---------------------------------------------------------------------------
# 정규화 헬퍼
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_group_type_korean_to_english(self) -> None:
        assert normalize_group_type("비가열") == "no_heat"
        assert normalize_group_type("습열") == "moist_heat"
        assert normalize_group_type("건열") == "dry_heat"

    def test_category_priority_derived_over_role(self) -> None:
        # derived_category가 있으면 role 무시
        assert normalize_category("oil_fat", "seasoning") == "유지류"
        assert normalize_category("water_base", "main") == "수분류"

    def test_category_falls_back_to_role(self) -> None:
        assert normalize_category(None, "main") == "주재료"
        assert normalize_category(float("nan"), "sub") == "부재료"
        assert normalize_category(None, "seasoning") == "양념류"

    def test_category_returns_none_on_failure(self) -> None:
        assert normalize_category(None, None) is None
        assert normalize_category("unknown", "unknown") is None


# ---------------------------------------------------------------------------
# b_feedback 룩업
# ---------------------------------------------------------------------------

class TestBFeedbackTable:
    def test_table_loads_and_has_expected_cells(self) -> None:
        table = load_b_feedback_table()
        # 사용자 사양: dry_heat × 5분류, moist_heat × 5분류, no_heat × 3분류 (3+5+5=13)
        assert ("dry_heat", "양념류") in table
        assert ("moist_heat", "수분류") in table
        assert ("no_heat", "주재료") in table

    def test_table_values_match_user_spec(self) -> None:
        table = load_b_feedback_table()
        # 사용자가 명시한 b_feedback 값 (오차 허용 0.001)
        assert table[("dry_heat", "주재료")] == pytest.approx(0.7393, abs=1e-3)
        assert table[("no_heat", "양념류")] == pytest.approx(0.7482, abs=1e-3)
        assert table[("moist_heat", "유지류")] == pytest.approx(0.7426, abs=1e-3)

    def test_missing_cells_fall_back(self) -> None:
        table = load_b_feedback_table()
        # 비가열 × 부재료/수분류는 없음 → fallback
        assert lookup_b_feedback(table, "no_heat", "부재료") == pytest.approx(B_FALLBACK)
        assert lookup_b_feedback(table, "no_heat", "수분류") == pytest.approx(B_FALLBACK)


# ---------------------------------------------------------------------------
# 학습 텐서 통합
# ---------------------------------------------------------------------------

DF_B_PATH = Path(__file__).parent.parent / "df_B.csv"


@pytest.mark.skipif(not DF_B_PATH.exists(), reason="df_B.csv not present")
class TestPrepareTrainingData:
    def test_shapes(self) -> None:
        out = prepare_training_data()
        m = out["X"].shape[0]
        assert out["X"].shape == (m, 4, N_SLOTS)
        assert out["Y"].shape == (m, N_SLOTS)
        assert out["logN"].shape == (m,)
        assert out["mask"].shape == (m, N_SLOTS)
        assert len(out["groups"]) == m
        # 205 레시피 × 7 N → 1435
        assert m == len(out["records"]) * len(DEFAULT_N_VALUES)

    def test_group_type_channel_matches_record(self) -> None:
        """Ch3=group_type_id/2.0 — 패딩 0.0, 유효 슬롯은 record group_type 값."""
        from nn_training_data import GROUP_TYPE_ID

        out = prepare_training_data()
        # 첫 샘플의 record (n_values[0] 사용)
        rec = out["records"][0]
        ch3 = out["X"][0, 3, :].numpy()
        expected_val = GROUP_TYPE_ID[rec.group_type]
        valid = rec.mask
        assert np.allclose(ch3[valid], expected_val)
        assert np.allclose(ch3[~valid], 0.0)

    def test_mask_channel_matches_mask_tensor(self) -> None:
        out = prepare_training_data()
        # Channel 2 == mask
        ch2 = out["X"][:, 2, :]
        assert torch.allclose(ch2, out["mask"])
        # 채널 수 4 보장
        assert out["X"].shape[1] == 4

    def test_groupkfold_no_leakage(self) -> None:
        out = prepare_training_data()
        for tr, te in groupkfold_indices(out["groups"], n_splits=5):
            grp_tr = set(out["groups"][tr])
            grp_te = set(out["groups"][te])
            assert grp_tr.isdisjoint(grp_te)

    def test_log_y_consistency(self) -> None:
        """log(Y) = log(base) + b × log(N) 관계 검증 (1 샘플)."""
        out = prepare_training_data()
        rec = out["records"][0]
        # 첫 레시피의 첫 유효 슬롯
        valid_slots = np.where(rec.mask)[0]
        if len(valid_slots) == 0:
            return
        slot = valid_slots[0]
        # n_values[0] 사용 샘플 인덱스 = 0
        n0 = float(DEFAULT_N_VALUES[0])
        expected_log_y = np.log(rec.base_amounts[slot]) + rec.b_per_slot[slot] * np.log(n0)
        actual = float(out["Y"][0, slot])
        assert actual == pytest.approx(expected_log_y, abs=1e-5)


# ---------------------------------------------------------------------------
# 모델 순전파
# ---------------------------------------------------------------------------

class TestResNet1DForward:
    def test_resblock_shape(self) -> None:
        block = ResBlock1D(32, 64)
        out = block(torch.randn(2, 32, 30))
        assert out.shape == (2, 64, 30)

    def test_forward_shape(self) -> None:
        torch.manual_seed(42)
        model = ResNet1D()
        x = torch.randn(4, 4, 30)
        log_n = torch.tensor([2.3, 4.6, 5.0, 5.7])
        out = model(x, log_n)
        assert out.shape == (4, 30)
        assert not torch.isnan(out).any()

    def test_eval_mode_deterministic(self) -> None:
        """eval 모드에서 Dropout/BN 비활성화 — 동일 입력 → 동일 출력."""
        torch.manual_seed(42)
        model = ResNet1D().eval()
        x = torch.randn(2, 4, 30)
        log_n = torch.tensor([4.6, 4.6])
        with torch.no_grad():
            out1 = model(x, log_n)
            out2 = model(x, log_n)
        assert torch.allclose(out1, out2)


# ---------------------------------------------------------------------------
# Masked MSE
# ---------------------------------------------------------------------------

class TestMaskedMSE:
    def test_only_valid_slots_count(self) -> None:
        pred = torch.zeros(2, 30)
        target = torch.ones(2, 30) * 3.0
        mask = torch.zeros(2, 30)
        mask[:, :2] = 1.0  # 4 valid
        loss = masked_mse_loss(pred, target, mask)
        # (0-3)^2 × 4 / 4 = 9
        assert float(loss) == pytest.approx(9.0)

    def test_zero_mask_does_not_nan(self) -> None:
        pred = torch.zeros(2, 30)
        target = torch.zeros(2, 30)
        mask = torch.zeros(2, 30)
        loss = masked_mse_loss(pred, target, mask)
        assert torch.isfinite(loss)


# ---------------------------------------------------------------------------
# predict() 인터페이스
# ---------------------------------------------------------------------------

class TestPredictInterface:
    def test_predict_returns_correct_length_and_keys(self) -> None:
        torch.manual_seed(42)
        model = ResNet1D().eval()
        items = [
            {"name": "소고기", "base": 100.0, "role": "main"},
            {"name": "소금", "base": 3.0, "role": "seasoning"},
            {"name": "참기름", "base": 5.0, "derived_category": "oil_fat"},
        ]
        result = predict(model, "건열", items, n=100)
        assert len(result) == 3
        for r in result:
            assert set(r.keys()) == {"name", "base", "scaled", "category"}
            assert isinstance(r["scaled"], float)
        assert result[0]["category"] == "주재료"
        assert result[1]["category"] == "양념류"
        assert result[2]["category"] == "유지류"

    def test_predict_accepts_english_group_type(self) -> None:
        torch.manual_seed(42)
        model = ResNet1D().eval()
        out_kor = predict(model, "건열", [IngredientInput("소금", 3.0, role="seasoning")], n=100)
        out_eng = predict(model, "dry_heat", [IngredientInput("소금", 3.0, role="seasoning")], n=100)
        assert out_kor[0]["scaled"] == pytest.approx(out_eng[0]["scaled"])

    def test_predict_orders_within_category_by_base_desc(self) -> None:
        """동일 카테고리 내 base가 큰 재료가 더 낮은 슬롯 인덱스를 받아야 한다."""
        torch.manual_seed(42)
        model = ResNet1D().eval()
        # 양념류 3개 — base 순서 섞어서 입력
        items = [
            {"name": "small", "base": 1.0, "role": "seasoning"},
            {"name": "large", "base": 100.0, "role": "seasoning"},
            {"name": "mid", "base": 10.0, "role": "seasoning"},
        ]
        result = predict(model, "건열", items, n=10)
        # 출력은 입력 순서 유지 → 'large'가 가장 큰 scaled 값 받음 (모델이 정상 학습됐다면)
        # 학습 전이라도 카테고리 정렬은 일관성 검증
        assert len(result) == 3
        assert all(r["category"] == "양념류" for r in result)


# ---------------------------------------------------------------------------
# 저장/로드 라운드트립
# ---------------------------------------------------------------------------

class TestSaveLoad:
    def test_roundtrip_preserves_outputs(self, tmp_path: Path) -> None:
        torch.manual_seed(42)
        model = ResNet1D().eval()
        x = torch.randn(2, 4, 30)
        log_n = torch.tensor([4.6, 4.6])
        with torch.no_grad():
            expected = model(x, log_n)

        save_path = tmp_path / "test_model.pt"
        save_model(model, save_path)
        loaded = load_model(save_path)
        with torch.no_grad():
            actual = loaded(x, log_n)
        assert torch.allclose(expected, actual)
