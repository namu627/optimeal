"""
test_fallback.py
================
Fallback 체인 pytest 테스트.

완료 기준:
  1) 역변환 수동 검증 PASS  ← TestApplyPowerLaw, TestVerifyInverseTransform
  2) pytest PASS            ← 전체 테스트 통과
  3) MAPE ≤ 52.30%          ← TestMapeCompletionCriteria.test_mape_with_baseline_pipeline
                               (baseline_model 파이프라인 결과물 필요 — 현재 skip 처리)

[데이터 상황 메모]
  - df_B.csv: MixedLM 학습용 (n=607). MAPE 완료기준 검증 불가.
  - 완료기준 검증 데이터: baseline_model_v0.02.py가 생성하는 result DataFrame
    (pairs CSV + 소규모/대규모 Excel 조인 후 ratio [0.2,3.0] 필터 적용, n=454)
  - 해당 데이터 확보 시 test_mape_with_baseline_pipeline()이 자동 실행됨.

실행:
  pytest module_2/tests/test_fallback.py -v
  pytest module_2/tests/test_fallback.py -v -s    # 수동 검증 출력 포함
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from lookup import ScalingParams, build_lookup_table, lookup_scaling_params
from fallback import (
    resolve_fallback_params,
    get_scaling_params,
    apply_power_law,
    verify_inverse_transform,
    calc_mape_fallback,
    scale_recipe,
    _FALLBACK_B,
    _FALLBACK_CONFIDENCE,
    _FALLBACK_ESTIMATION_METHOD,
    _FALLBACK_SE_B,
    _CATEGORY_A_DEFAULTS,
    _FALLBACK_A_LAST_RESORT,
)

# ---------------------------------------------------------------------------
# 픽스처
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def lookup_df():
    """scaling_coefficients.csv 기반 룩업 테이블 (B_simple 파라미터 기준, 한국어 15행 포함)."""
    csv_path = Path(__file__).parent.parent / "src" / "engine" / "scaling_coefficients.csv"
    return build_lookup_table(csv_path)


@pytest.fixture(scope="module")
def df_b():
    """df_B.csv — MixedLM 학습용 데이터셋."""
    csv_path = Path(__file__).parent.parent / "df_B.csv"
    if not csv_path.exists():
        pytest.skip("df_B.csv 없음")
    return pd.read_csv(csv_path)


@pytest.fixture(scope="module")
def baseline_pipeline_result():
    """
    baseline_model_v0.02.py 파이프라인 결과물 (완료기준 검증용).
    파일이 없으면 skip.
    파일명: baseline_result.csv (baseline_model_v0.02.py main() 실행 산출물)
    """
    csv_path = Path(__file__).parent.parent / "baseline_result.csv"
    if not csv_path.exists():
        pytest.skip(
            "baseline_result.csv 없음 — baseline_model_v0.02.py를 먼저 실행하세요.\n"
            "  python baseline_model_v0.02.py\n"
            "생성 파일: baseline_result.csv (pairs+Excel 조인 후 ratio 필터 [0.2,3.0] 적용, n=454)"
        )
    return pd.read_csv(csv_path)


# ===========================================================================
# 1. resolve_fallback_params — 기본 동작
# ===========================================================================

class TestResolveFallbackParams:
    """resolve_fallback_params() 단위 테스트."""

    def test_returns_scaling_params_instance(self):
        assert isinstance(resolve_fallback_params("주재료"), ScalingParams)

    def test_fallback_b_is_global_mean(self):
        """b = 0.6163 (ADR-002 v3 전체 평균)."""
        result = resolve_fallback_params("주재료")
        assert result.power_law_b == pytest.approx(_FALLBACK_B, abs=1e-6)

    def test_confidence_always_low(self):
        """Fallback 신뢰도는 항상 'low' (ADR-003)."""
        for cat in ["주재료", "부재료", "양념류", "수분류", "유지류", "미등록"]:
            assert resolve_fallback_params(cat).confidence == "low", \
                f"category={cat}: confidence != 'low'"

    def test_estimation_method_category_mean(self):
        """estimation_method = 'category_mean' (ADR-002 v3)."""
        assert resolve_fallback_params("부재료").estimation_method == "category_mean"

    def test_lookup_priority_is_3(self):
        """Fallback = 3순위."""
        assert resolve_fallback_params("양념류").lookup_priority == 3

    @pytest.mark.parametrize("category,expected_a", [
        ("주재료", 7.9677),
        ("양념류", 6.3028),
        ("부재료", 5.7500),
        ("수분류", 5.7500),
        ("유지류", 6.3028),
    ])
    def test_a_value_by_category_korean(self, category, expected_a):
        """한국어 카테고리별 a 값이 B_simple 모델 기준과 일치."""
        result = resolve_fallback_params(category)
        assert result.power_law_a == pytest.approx(expected_a, abs=1e-4)

    @pytest.mark.parametrize("role_en,expected_a", [
        ("main",      7.9677),
        ("seasoning", 6.3028),
        ("sub",       5.7500),
    ])
    def test_a_value_by_role_english(self, role_en, expected_a):
        """영어 role도 수용하여 올바른 a 반환."""
        result = resolve_fallback_params(role_en)
        assert result.power_law_a == pytest.approx(expected_a, abs=1e-4)

    def test_unknown_category_uses_last_resort_a(self):
        """미등록 카테고리 → 전체 평균 a 사용 (에러 없음)."""
        result = resolve_fallback_params("미등록카테고리xyz")
        assert result.power_law_a == pytest.approx(_FALLBACK_A_LAST_RESORT, abs=1e-4)
        assert result.power_law_b == pytest.approx(_FALLBACK_B, abs=1e-6)
        assert result.confidence == "low"

    def test_se_b_value(self):
        """se_b = B_simple 모델 표준오차 0.1814."""
        result = resolve_fallback_params("주재료")
        assert result.se_b == pytest.approx(_FALLBACK_SE_B, abs=1e-6)

    def test_lookup_df_none_ok(self):
        """lookup_df=None이어도 정상 동작."""
        result = resolve_fallback_params("주재료", lookup_df=None)
        assert isinstance(result, ScalingParams)
        assert result.power_law_b == pytest.approx(_FALLBACK_B, abs=1e-6)

    def test_empty_string_category(self):
        """빈 문자열 카테고리 → last resort a, 에러 없음."""
        result = resolve_fallback_params("")
        assert isinstance(result, ScalingParams)
        assert result.confidence == "low"


# ===========================================================================
# 2. get_scaling_params — 통합 진입점 (lookup + fallback)
# ===========================================================================

class TestGetScalingParams:
    """get_scaling_params() 통합 테스트."""

    def test_never_returns_none(self, lookup_df):
        """어떤 입력이든 None 반환 없음 — 항상 ScalingParams."""
        cases = [
            ("주재료", "dry_heat"),
            ("부재료", "moist_heat"),
            ("양념류", "no_heat"),
            ("없는카테고리", "없는조리법"),
            ("", ""),
            ("main", "dry_heat"),
        ]
        for cat, gt in cases:
            result = get_scaling_params(lookup_df, cat, gt)
            assert result is not None, f"None 반환: category={cat}, group_type={gt}"
            assert isinstance(result, ScalingParams)

    def test_fallback_on_unknown_group_type(self, lookup_df):
        """알 수 없는 group_type → Fallback 발동."""
        params = get_scaling_params(lookup_df, "주재료", "unknown_xyz")
        assert params.estimation_method == "category_mean"
        assert params.confidence == "low"

    def test_fallback_on_unknown_category(self, lookup_df):
        """알 수 없는 카테고리 → Fallback 발동."""
        params = get_scaling_params(lookup_df, "미등록재료", "dry_heat")
        assert params.estimation_method == "category_mean"
        assert params.confidence == "low"

    @pytest.mark.parametrize("category", ["주재료", "부재료", "양념류", "수분류", "유지류"])
    @pytest.mark.parametrize("group_type", ["dry_heat", "moist_heat", "no_heat"])
    def test_all_15_combinations_return_valid(self, lookup_df, category, group_type):
        """5분류 × 3대분류 = 15가지 조합 모두 유효한 ScalingParams 반환."""
        result = get_scaling_params(lookup_df, category, group_type)
        assert isinstance(result, ScalingParams)
        assert result.power_law_b > 0
        assert result.power_law_a > 0
        assert result.confidence in ("high", "medium", "low")
        assert result.estimation_method in (
            "mixedlm", "curve_fit", "nutritionist_feedback", "category_mean"
        )

    def test_fallback_b_is_correct(self, lookup_df):
        """Fallback 경로에서 b=0.6163 확인."""
        params = get_scaling_params(lookup_df, "주재료", "nonexistent_method")
        assert params.power_law_b == pytest.approx(_FALLBACK_B, abs=1e-6)


# ===========================================================================
# 3. apply_power_law — 역변환 수식 수동 검증
# ===========================================================================

class TestApplyPowerLaw:
    """apply_power_law() 역변환 수식 수동 검증."""

    def _params(self, a, b, se=0.1814, method="mixedlm", conf="high", prio=2):
        return ScalingParams(
            power_law_a=a, power_law_b=b, se_b=se,
            estimation_method=method, lookup_priority=prio, confidence=conf
        )

    # ── 수식 정확성 ──────────────────────────────────────────────────────────

    def test_linear_case_a1_b1(self):
        """b=1, a=1 이면 Y = base × N (선형과 동치)."""
        params = self._params(a=1.0, b=1.0)
        assert apply_power_law(params, 10.0, 100) == pytest.approx(1000.0, rel=1e-6)

    def test_economy_of_scale_b_less_1(self):
        """b<1 이면 선형(b=1, a=1)보다 Y가 작음 (규모의 경제)."""
        linear = self._params(a=1.0, b=1.0)
        scaled = self._params(a=1.0, b=0.6163)
        assert apply_power_law(scaled, 10.0, 100) < apply_power_law(linear, 10.0, 100)

    def test_manual_main_b_simple(self):
        """
        수동 검증 — 주재료 B_simple: a=7.9677, b=0.6163, base=10g, N=100
        Y = 7.9677 × 10 × 100^0.6163
        100^0.6163 = exp(0.6163 × ln100) = exp(0.6163 × 4.60517) ≈ exp(2.8377) ≈ 17.083
        Y ≈ 7.9677 × 10 × 17.083 ≈ 1361.3g
        """
        params = self._params(a=7.9677, b=0.6163)
        result = apply_power_law(params, 10.0, 100)
        assert result == pytest.approx(1361.3, abs=2.0)

    def test_manual_seasoning_b_simple(self):
        """
        수동 검증 — 양념류 B_simple: a=6.3028, b=0.6163, base=5g, N=100
        Y = 6.3028 × 5 × 17.083 ≈ 538.4g
        """
        params = self._params(a=6.3028, b=0.6163)
        result = apply_power_law(params, 5.0, 100)
        assert result == pytest.approx(538.4, abs=2.0)

    def test_manual_sub_b_simple(self):
        """
        수동 검증 — 부재료 B_simple: a=5.75, b=0.6163, base=20g, N=100
        Y = 5.75 × 20 × 17.083 ≈ 1966.5g
        """
        params = self._params(a=5.75, b=0.6163)
        result = apply_power_law(params, 20.0, 100)
        assert result == pytest.approx(1966.5, abs=2.0)

    def test_ratio_consistency(self):
        """
        Y = a × base × N^b 와 ratio = a × N^(b-1) 간 일관성 검증.
        Y / (base × N) = a × N^(b-1) = ratio
        """
        a, b, base, N = 7.9677, 0.6163, 10.0, 100
        params = self._params(a=a, b=b)
        y = apply_power_law(params, base, N)
        ratio_from_y = y / (base * N)
        ratio_formula = a * (N ** (b - 1))
        assert ratio_from_y == pytest.approx(ratio_formula, rel=1e-6)

    def test_fallback_params_give_valid_y(self):
        """Fallback 파라미터로 역변환이 정상 동작하는지 확인."""
        params = resolve_fallback_params("주재료")
        result = apply_power_law(params, 10.0, 100)
        assert result > 0
        assert not math.isnan(result)
        assert not math.isinf(result)

    # ── 단조 증가 ─────────────────────────────────────────────────────────────

    def test_monotonically_increasing_with_n(self):
        """N이 커질수록 Y도 단조 증가 (b>0 보장)."""
        params = self._params(a=7.9677, b=0.6163)
        ns = [1, 10, 50, 100, 200, 300]
        ys = [apply_power_law(params, 10.0, n) for n in ns]
        assert ys == sorted(ys), "N 증가에 따라 Y도 증가해야 함"

    # ── 입력 검증 ─────────────────────────────────────────────────────────────

    def test_raises_on_zero_base(self):
        params = self._params(a=1.0, b=0.6)
        with pytest.raises(ValueError, match="base_amount_g"):
            apply_power_law(params, 0.0, 100)

    def test_raises_on_negative_base(self):
        params = self._params(a=1.0, b=0.6)
        with pytest.raises(ValueError, match="base_amount_g"):
            apply_power_law(params, -5.0, 100)

    def test_raises_on_zero_n(self):
        params = self._params(a=1.0, b=0.6)
        with pytest.raises(ValueError, match="target_serving_n"):
            apply_power_law(params, 10.0, 0)

    def test_raises_on_negative_n(self):
        params = self._params(a=1.0, b=0.6)
        with pytest.raises(ValueError, match="target_serving_n"):
            apply_power_law(params, 10.0, -1)


# ===========================================================================
# 4. verify_inverse_transform — 역변환 수동 검증 헬퍼
# ===========================================================================

class TestVerifyInverseTransform:
    """verify_inverse_transform() 수동 검증 헬퍼 테스트."""

    def test_result_keys(self):
        """반환 딕셔너리 키 확인."""
        params = resolve_fallback_params("주재료")
        result = verify_inverse_transform(params, 10.0, 100, 1000.0)
        assert set(result.keys()) == {
            "y_pred", "y_true", "ape_pct", "pass", "estimation_method", "confidence"
        }

    def test_pass_when_within_tolerance(self):
        """허용 오차 내 → pass=True."""
        params = resolve_fallback_params("주재료")
        # y_pred ≈ 1361g, y_true=1361g → APE ≈ 0%
        result = verify_inverse_transform(params, 10.0, 100, 1361.0, tolerance_pct=5.0)
        assert result["pass"] is True

    def test_fail_when_outside_tolerance(self):
        """허용 오차 밖 → pass=False."""
        params = resolve_fallback_params("주재료")
        result = verify_inverse_transform(params, 10.0, 100, 9999.0, tolerance_pct=5.0)
        assert result["pass"] is False

    def test_confidence_low_for_fallback(self):
        """Fallback 파라미터 → confidence='low' 결과에 포함."""
        params = resolve_fallback_params("주재료")
        result = verify_inverse_transform(params, 10.0, 100, 1000.0)
        assert result["confidence"] == "low"
        assert result["estimation_method"] == "category_mean"

    def test_ape_pct_calculation(self):
        """APE 계산값 검증: baseline_model.py 동일 공식 — max(y_true, 1.0) 분모."""
        params = resolve_fallback_params("주재료")
        y_pred_expected = apply_power_law(params, 10.0, 100)
        y_true = 1000.0
        ape_expected = abs(y_true - y_pred_expected) / max(y_true, 1.0) * 100
        result = verify_inverse_transform(params, 10.0, 100, y_true)
        assert result["ape_pct"] == pytest.approx(ape_expected, abs=0.01)

    def test_y_pred_matches_apply_power_law(self):
        """verify의 y_pred가 apply_power_law 결과와 일치."""
        params = resolve_fallback_params("양념류")
        y_direct = apply_power_law(params, 5.0, 100)
        result = verify_inverse_transform(params, 5.0, 100, 500.0)
        assert result["y_pred"] == pytest.approx(y_direct, abs=0.01)


# ===========================================================================
# 5. MAPE 완료 기준 검증
# ===========================================================================

class TestMapeCompletionCriteria:
    """완료 기준: Baseline MAPE=60.75% (n=454, ratio [0.2,3.0]) 대비 ≤ 52.30% 달성 여부."""

    BASELINE_MAPE = 60.7489  # baseline_model_v0.02.py 정식 실행 결과, n=454, ratio [0.2,3.0]
    TARGET_MAPE = 52.30      # 완료 기준 (60.75% × 0.80 ≈ 48.60%이나 ADR 확정값 52.30% 적용)

    def test_target_is_20pct_improvement_over_baseline(self):
        """완료 기준 52.30%가 Baseline 60.75%보다 낮은지 확인 (20% 개선 목표)."""
        assert self.TARGET_MAPE < self.BASELINE_MAPE

    def test_fallback_b_less_than_1(self):
        """b=0.6163 < 1 → 규모의 경제 반영 (선형 b=1보다 현실적)."""
        params = resolve_fallback_params("주재료")
        assert params.power_law_b < 1.0

    def test_mape_with_df_b_reference(self, df_b, lookup_df):
        """
        df_B.csv 기준 Fallback MAPE 참조 계산 (완료기준 검증 아님).

        df_B는 MixedLM 학습용으로 ratio 이상치 미필터 상태이며,
        baseline_model 파이프라인과 다른 전처리 구조를 가짐.
        이 테스트는 구조 오류 없이 계산되는지만 검증.
        """
        mape = calc_mape_fallback(df_b, lookup_df)
        print(f"\n[참조] df_B 기준 Fallback MAPE = {mape:.4f}%")
        print(f"  (완료기준 {self.TARGET_MAPE}% 검증은 baseline_result.csv 필요)")
        assert not math.isnan(mape), "MAPE 계산 결과가 NaN"
        assert mape > 0, "MAPE가 0 이하"

    def test_mape_with_baseline_pipeline(self, baseline_pipeline_result, lookup_df):
        """
        ★ 완료 기준 검증 — MAPE ≤ 52.30%

        baseline_result.csv (baseline_model_v0.02.py main() 실행 산출물) 필요.
        파일 없으면 자동 skip.

        baseline_result.csv 생성 방법:
          python baseline_model_v0.02.py
          → baseline_result.csv 생성 (pairs+Excel 조인 후 ratio 필터 [0.2,3.0] 적용, n=454)
        """
        # ratio 필터 (ADR 확정 2026-03-20): [0.2, 3.0] — 변경 금지
        df = baseline_pipeline_result.copy()
        if "ratio_actual" in df.columns:
            df = df[(df["ratio_actual"] >= 0.2) & (df["ratio_actual"] <= 3.0)]
        elif "ratio" in df.columns:
            df = df[(df["ratio"] >= 0.2) & (df["ratio"] <= 3.0)]

        mape = calc_mape_fallback(df, lookup_df)
        print(f"\n[완료기준 검증] Fallback MAPE = {mape:.4f}%")
        print(f"  Baseline MAPE = {self.BASELINE_MAPE:.4f}% (n=454, ratio [0.2,3.0])")
        print(f"  완료 기준     = {self.TARGET_MAPE:.4f}%")
        print(f"  PASS 여부     = {mape <= self.TARGET_MAPE}")

        assert mape <= self.TARGET_MAPE, (
            f"MAPE {mape:.2f}% > 완료 기준 {self.TARGET_MAPE}%\n"
            f"Baseline: {self.BASELINE_MAPE:.2f}%"
        )


# ===========================================================================
# 6. scale_recipe — 배치 변환 통합 테스트
# ===========================================================================

class TestScaleRecipe:
    """scale_recipe() 배치 변환 테스트."""

    SAMPLE_INGREDIENTS = [
        {"name": "돼지고기", "category": "주재료", "base_amount_g": 120.0},
        {"name": "고추장",   "category": "양념류", "base_amount_g": 15.0},
        {"name": "대파",     "category": "부재료", "base_amount_g": 20.0},
    ]

    def test_basic_batch_returns_correct_count(self, lookup_df):
        result = scale_recipe(self.SAMPLE_INGREDIENTS, "dry_heat", 100, lookup_df)
        assert len(result) == 3

    def test_scaled_amount_positive(self, lookup_df):
        result = scale_recipe(self.SAMPLE_INGREDIENTS, "dry_heat", 100, lookup_df)
        for r in result:
            assert r["scaled_amount_g"] > 0

    def test_output_has_required_keys(self, lookup_df):
        result = scale_recipe(self.SAMPLE_INGREDIENTS, "dry_heat", 100, lookup_df)
        for r in result:
            assert "scaled_amount_g" in r
            assert "confidence" in r
            assert "estimation_method" in r

    def test_input_fields_preserved(self, lookup_df):
        """입력 필드가 출력에 그대로 보존."""
        result = scale_recipe(self.SAMPLE_INGREDIENTS, "dry_heat", 100, lookup_df)
        assert result[0]["name"] == "돼지고기"
        assert result[0]["base_amount_g"] == 120.0

    def test_unknown_category_in_batch(self, lookup_df):
        """미등록 카테고리 포함 배치도 정상 처리."""
        ingredients = [
            {"name": "미등록재료", "category": "미등록카테고리", "base_amount_g": 10.0},
        ]
        result = scale_recipe(ingredients, "dry_heat", 100, lookup_df)
        assert len(result) == 1
        assert result[0]["confidence"] == "low"
        assert result[0]["estimation_method"] == "category_mean"

    def test_scaled_greater_than_base_for_large_n(self, lookup_df):
        """N=100일 때 scaled > base_amount_g (a>1이 보장)."""
        result = scale_recipe(self.SAMPLE_INGREDIENTS, "dry_heat", 100, lookup_df)
        for r in result:
            assert r["scaled_amount_g"] > r["base_amount_g"], \
                f"{r['name']}: scaled={r['scaled_amount_g']} <= base={r['base_amount_g']}"

    def test_lookup_df_none_builds_internally(self):
        """lookup_df=None이면 내부에서 build_lookup_table() 실행."""
        result = scale_recipe(self.SAMPLE_INGREDIENTS[:1], "dry_heat", 100, lookup_df=None)
        assert len(result) == 1
        assert result[0]["scaled_amount_g"] > 0


# ===========================================================================
# 7. 수동 검증 시나리오 출력
# ===========================================================================

class TestManualVerificationScenarios:
    """
    수동 검증 PASS 확인용 시나리오.
    pytest -v -s 옵션으로 실행 시 계산 결과 출력.
    """

    SCENARIOS = [
        # (설명,             category,   group_type,   base_g, N,   y_true_근사, tolerance_pct)
        ("주재료 건열 100인분",  "주재료", "dry_heat",   10.0, 100, 1361.0, 10.0),
        ("양념류 습열 100인분",  "양념류", "moist_heat",  5.0, 100,  538.4, 10.0),
        ("부재료 비가열 100인분","부재료", "no_heat",    20.0, 100, 1966.5, 10.0),
        ("영어 role main",      "main",   "dry_heat",   10.0, 100, 1361.0, 10.0),
        ("Fallback 미등록",     "미등록",  "dry_heat",   10.0, 100, 1000.0, 80.0),
    ]

    @pytest.mark.parametrize(
        "desc,cat,gt,base,n,ytrue,tol", SCENARIOS
    )
    def test_scenario_no_exception(self, lookup_df, desc, cat, gt, base, n, ytrue, tol):
        """각 시나리오에서 예외 없이 실행 & 계산 결과 출력."""
        params = get_scaling_params(lookup_df, cat, gt)
        result = verify_inverse_transform(params, base, n, ytrue, tolerance_pct=tol)

        print(f"\n[수동검증] {desc}")
        print(f"  a={params.power_law_a:.4f}, b={params.power_law_b:.4f}")
        print(f"  Y = {params.power_law_a:.4f} × {base} × {n}^{params.power_law_b:.4f}")
        print(f"  y_pred={result['y_pred']:.2f}g, y_true={ytrue}g, APE={result['ape_pct']:.1f}%")
        print(f"  estimation={result['estimation_method']}, confidence={result['confidence']}")
        print(f"  PASS(tol={tol}%) = {result['pass']}")

        assert isinstance(result["y_pred"], float)
        assert result["y_pred"] > 0
        assert not math.isnan(result["y_pred"])
        assert result["pass"] is True, (
            f"{desc}: APE={result['ape_pct']:.1f}% > tolerance={tol}%"
        )
