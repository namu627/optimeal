"""
test_feedback_update.py
=======================
feedback_update.py 단위 테스트.

pytest module_2/tests/test_feedback_update.py -v
"""

import math
import sys
from pathlib import Path

import pytest

# sys.path 설정 — conftest.py 대신 명시적으로 처리
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "engine"))

from feedback_update import (
    B_DOMAIN_HIGH,
    B_DOMAIN_LOW,
    FeedbackResult,
    apply_feedback,
    clip_b_output,
    clip_delta_g_iqr,
    compute_b_feedback,
    compute_rubric_avg,
    get_overall_rating,
    moving_average_update,
    parse_delta_g,
    validate_feedback_json,
)


# ---------------------------------------------------------------------------
# 픽스처
# ---------------------------------------------------------------------------

@pytest.fixture
def valid_feedback():
    """유효한 피드백 JSON 예시."""
    return {
        "feedback_meta": {
            "recipe_id": 201,
            "feedback_round": 1,
            "nutritionist_name": "홍길동",
            "nutritionist_id": "N001",
            "feedback_date": "2026-05-04",
        },
        "rubric_scores": {
            "taste_seasoning": 4,
            "cooking_feasibility": 5,
            "field_applicability": 4,
        },
        "ingredient_adjustments": {"고추장": "-100g", "물": "+500g"},
        "detailed_comments": "전반적으로 간이 약간 진함",
    }


@pytest.fixture
def b_old():
    return 0.6163


@pytest.fixture
def se_b():
    """B_simple MixedLM SE(b) — scaling_coefficients.csv id=1 기준."""
    return 0.1814


@pytest.fixture
def a_old():
    """양념류 B_simple a — scaling_coefficients.csv id=2 기준."""
    return 6.3028


# ---------------------------------------------------------------------------
# 테스트: validate_feedback_json
# ---------------------------------------------------------------------------

class TestValidateFeedbackJson:
    def test_valid_json_반환_빈_리스트(self, valid_feedback):
        errors = validate_feedback_json(valid_feedback)
        assert errors == []

    def test_필수_키_누락(self):
        errors = validate_feedback_json({})
        assert any("최상위 키 누락" in e for e in errors)

    def test_feedback_round_범위_초과(self, valid_feedback):
        valid_feedback["feedback_meta"]["feedback_round"] = 6
        errors = validate_feedback_json(valid_feedback)
        assert any("feedback_round" in e for e in errors)

    def test_루브릭_점수_범위_초과(self, valid_feedback):
        valid_feedback["rubric_scores"]["taste_seasoning"] = 0
        errors = validate_feedback_json(valid_feedback)
        assert any("taste_seasoning" in e for e in errors)

    def test_ingredient_adjustment_형식_오류(self, valid_feedback):
        valid_feedback["ingredient_adjustments"]["소금"] = "약간"
        errors = validate_feedback_json(valid_feedback)
        assert any("소금" in e for e in errors)

    def test_ml_단위_경고(self, valid_feedback):
        valid_feedback["ingredient_adjustments"]["물"] = "+200ml"
        errors = validate_feedback_json(valid_feedback)
        assert any("ml" in e for e in errors)

    def test_날짜_형식_오류(self, valid_feedback):
        valid_feedback["feedback_meta"]["feedback_date"] = "20260504"
        errors = validate_feedback_json(valid_feedback)
        assert any("feedback_date" in e for e in errors)


# ---------------------------------------------------------------------------
# 테스트: parse_delta_g
# ---------------------------------------------------------------------------

class TestParseDeltaG:
    def test_양수(self):
        assert parse_delta_g("+100g") == 100.0

    def test_음수(self):
        assert parse_delta_g("-50.5g") == -50.5

    def test_변경없음(self):
        assert parse_delta_g("0g") == 0.0

    def test_ml_단위(self):
        """ml 단위는 1:1로 파싱 (변환은 unit_conversion 별도)."""
        assert parse_delta_g("+200ml") == 200.0

    def test_잘못된_형식(self):
        with pytest.raises(ValueError):
            parse_delta_g("약간")


# ---------------------------------------------------------------------------
# 테스트: clip_delta_g_iqr (1단계 클리핑)
# ---------------------------------------------------------------------------

class TestClipDeltaGIqr:
    def test_이력_부족_클리핑_없음(self):
        """이력 3개 미만 → 클리핑 생략."""
        clipped, was_clipped = clip_delta_g_iqr(500.0, [])
        assert clipped == 500.0
        assert was_clipped is False

    def test_이력_2개_클리핑_없음(self):
        clipped, was_clipped = clip_delta_g_iqr(500.0, [10.0, 20.0])
        assert clipped == 500.0
        assert was_clipped is False

    def test_이상치_클리핑(self):
        """이력 내 IQR 기반 상한 초과 → 클리핑."""
        history = [-20.0, -10.0, 0.0, 10.0, 20.0]
        clipped, was_clipped = clip_delta_g_iqr(500.0, history)
        assert was_clipped is True
        assert clipped < 500.0

    def test_정상값_클리핑_없음(self):
        history = [-20.0, -10.0, 0.0, 10.0, 20.0]
        clipped, was_clipped = clip_delta_g_iqr(5.0, history)
        assert was_clipped is False
        assert clipped == 5.0

    def test_클리핑_후_범위_내(self):
        """클리핑 후 값이 [Q1-1.5IQR, Q3+1.5IQR] 이내."""
        import numpy as np
        history = list(range(-50, 51))  # -50 ~ 50
        clipped, _ = clip_delta_g_iqr(1000.0, history)
        arr = np.array(history)
        q1, q3 = np.percentile(arr, 25), np.percentile(arr, 75)
        iqr = q3 - q1
        assert clipped <= q3 + 1.5 * iqr + 1e-6


# ---------------------------------------------------------------------------
# 테스트: compute_b_feedback
# ---------------------------------------------------------------------------

class TestComputeBFeedback:
    def test_기본_역산(self, a_old):
        """
        actual=200, Δ=-50, base=2g, N=100, a=6.3028
        ratio_adj = (200-50)/(2×100) = 0.75
        b = log(0.75/6.3028)/log(100)+1
        """
        b = compute_b_feedback(200.0, -50.0, 2.0, 100, a_old)
        expected_ratio = (200.0 - 50.0) / (2.0 * 100)
        expected_b = math.log(expected_ratio / a_old) / math.log(100) + 1
        assert abs(b - expected_b) < 1e-10

    def test_delta_g_0_수정없음(self, a_old):
        """Δ_g=0이면 ratio_adj = actual / (base×N)."""
        actual = 6.3028 * 2.0 * (100 ** 0.6163)  # Y = a × base × N^b → ratio=a × N^(b-1)
        b = compute_b_feedback(actual, 0.0, 2.0, 100, a_old)
        # b_feedback ≈ 0.6163 (원래 b로 생성된 Y면 역산 시 동일 b 복원)
        assert abs(b - 0.6163) < 0.01

    def test_N_1_예외(self, a_old):
        """N=1이면 log(N)=0 → b 역산 불가 → ValueError."""
        with pytest.raises(ValueError, match="target_serving_n"):
            compute_b_feedback(10.0, 0.0, 1.0, 1, a_old)

    def test_base_0_예외(self, a_old):
        with pytest.raises(ValueError, match="base_amount_g"):
            compute_b_feedback(10.0, 0.0, 0.0, 100, a_old)

    def test_조정후_투입량_음수_예외(self, a_old):
        """actual+Δ_g ≤ 0이면 ValueError."""
        with pytest.raises(ValueError):
            compute_b_feedback(10.0, -20.0, 1.0, 100, a_old)


# ---------------------------------------------------------------------------
# 테스트: moving_average_update
# ---------------------------------------------------------------------------

class TestMovingAverageUpdate:
    def test_n_old_0_첫번째_피드백(self):
        """n_old=0이면 b_new_raw = b_feedback."""
        b_new = moving_average_update(0.6163, 0, 0.50)
        assert b_new == 0.50

    def test_이동평균_공식(self):
        """(b_old × n_old + b_feedback) / (n_old + 1)."""
        b_old, n_old, b_feedback = 0.6163, 1, 0.50
        expected = (b_old * n_old + b_feedback) / (n_old + 1)
        assert abs(moving_average_update(b_old, n_old, b_feedback) - expected) < 1e-10

    def test_n_old_음수_예외(self):
        with pytest.raises(ValueError):
            moving_average_update(0.6163, -1, 0.50)

    def test_수렴_누적_피드백(self):
        """피드백이 누적될수록 b가 b_feedback 방향으로 수렴."""
        b = 0.6163
        for i in range(20):
            b = moving_average_update(b, i, 0.50)
        assert b < 0.6163  # b_feedback=0.50이 낮으므로 b가 하강


# ---------------------------------------------------------------------------
# 테스트: clip_b_output (2단계 클리핑)
# ---------------------------------------------------------------------------

class TestClipBOutput:
    def test_범위_내_클리핑_없음(self, b_old, se_b):
        epsilon = 2 * se_b
        b_in_range = b_old + epsilon * 0.5  # 허용 범위 내
        b_new, was_clipped = clip_b_output(b_in_range, b_old, se_b)
        assert was_clipped is False
        assert abs(b_new - b_in_range) < 1e-9

    def test_상한_초과_클리핑(self, b_old, se_b):
        epsilon = 2 * se_b
        b_exceed = b_old + epsilon * 2  # 상한 초과
        b_new, was_clipped = clip_b_output(b_exceed, b_old, se_b)
        assert was_clipped is True
        assert b_new <= min(B_DOMAIN_HIGH, b_old + epsilon) + 1e-9

    def test_하한_미만_클리핑(self, b_old, se_b):
        epsilon = 2 * se_b
        b_below = b_old - epsilon * 2  # 하한 미만
        b_new, was_clipped = clip_b_output(b_below, b_old, se_b)
        assert was_clipped is True
        assert b_new >= max(B_DOMAIN_LOW, b_old - epsilon) - 1e-9

    def test_도메인_절대_하한_적용(self, se_b):
        """b_old가 낮아도 B_DOMAIN_LOW=0.1 이하로 클리핑 불가."""
        b_old_low = 0.15
        b_new, _ = clip_b_output(-10.0, b_old_low, se_b)
        assert b_new >= B_DOMAIN_LOW

    def test_도메인_절대_상한_적용(self, se_b):
        """B_DOMAIN_HIGH=2.0 이상으로 클리핑 불가."""
        b_old_high = 1.8
        b_new, _ = clip_b_output(100.0, b_old_high, se_b)
        assert b_new <= B_DOMAIN_HIGH

    def test_se_b_0_예외(self, b_old):
        with pytest.raises(ValueError, match="se_b"):
            clip_b_output(0.70, b_old, 0.0)


# ---------------------------------------------------------------------------
# 테스트: apply_feedback (통합)
# ---------------------------------------------------------------------------

class TestApplyFeedback:
    def test_반환_타입(self, b_old, se_b, a_old):
        result = apply_feedback(
            actual_amount_g=200.0,
            delta_g_original=-50.0,
            base_amount_g=2.0,
            target_serving_n=100,
            b_old=b_old,
            n_old=0,
            a_old=a_old,
            se_b=se_b,
        )
        assert isinstance(result, FeedbackResult)

    def test_b_new_도메인_범위_내(self, b_old, se_b, a_old):
        result = apply_feedback(
            actual_amount_g=200.0,
            delta_g_original=-50.0,
            base_amount_g=2.0,
            target_serving_n=100,
            b_old=b_old,
            n_old=0,
            a_old=a_old,
            se_b=se_b,
        )
        assert B_DOMAIN_LOW <= result.b_new <= B_DOMAIN_HIGH

    def test_이력_없으면_1단계_클리핑_없음(self, b_old, se_b, a_old):
        result = apply_feedback(
            actual_amount_g=200.0,
            delta_g_original=-50.0,
            base_amount_g=2.0,
            target_serving_n=100,
            b_old=b_old,
            n_old=0,
            a_old=a_old,
            se_b=se_b,
            delta_g_history=[],
        )
        assert result.delta_g_clipped is None

    def test_delta_g_0_b_거의_불변(self, b_old, se_b, a_old):
        """
        Δ_g=0이면 actual → b_feedback ≈ b_old → b_new ≈ b_old.
        역변환 Y = a×base×N^b에서 도출된 actual을 사용하면 b_feedback=b_old.
        """
        import numpy as np
        actual = a_old * 2.0 * (100 ** b_old)  # 역변환 Y
        result = apply_feedback(
            actual_amount_g=actual,
            delta_g_original=0.0,
            base_amount_g=2.0,
            target_serving_n=100,
            b_old=b_old,
            n_old=0,
            a_old=a_old,
            se_b=se_b,
        )
        # n_old=0이면 b_new = b_feedback = b_old (이동평균 = b_old)
        assert abs(result.b_new - b_old) < 0.01

    def test_이력_이상치_1단계_클리핑_발생(self, b_old, se_b, a_old):
        """이상치 Δ_g가 IQR 클리핑되면 is_clipped=True."""
        history = list(range(-10, 11))  # -10 ~ 10
        result = apply_feedback(
            actual_amount_g=200.0,
            delta_g_original=10000.0,  # 극단적 이상치
            base_amount_g=2.0,
            target_serving_n=100,
            b_old=b_old,
            n_old=0,
            a_old=a_old,
            se_b=se_b,
            delta_g_history=history,
        )
        assert result.is_clipped is True
        assert result.delta_g_clipped is not None

    def test_n_new_증가(self, b_old, se_b, a_old):
        result = apply_feedback(
            actual_amount_g=200.0,
            delta_g_original=-10.0,
            base_amount_g=2.0,
            target_serving_n=100,
            b_old=b_old,
            n_old=3,
            a_old=a_old,
            se_b=se_b,
        )
        assert result.n_new == 4

    def test_감사_추적_b_new_raw_저장(self, b_old, se_b, a_old):
        """b_new_raw는 2단계 클리핑 전 값이어야 함."""
        result = apply_feedback(
            actual_amount_g=200.0,
            delta_g_original=-50.0,
            base_amount_g=2.0,
            target_serving_n=100,
            b_old=b_old,
            n_old=0,
            a_old=a_old,
            se_b=se_b,
        )
        # b_new_raw = moving_average_update(b_old, 0, b_feedback) = b_feedback
        b_feedback_direct = compute_b_feedback(
            actual_amount_g=200.0,
            delta_g=-50.0,
            base_amount_g=2.0,
            target_serving_n=100,
            a_old=a_old,
        )
        expected_raw = moving_average_update(b_old, 0, b_feedback_direct)
        assert abs(result.b_new_raw - expected_raw) < 1e-9


# ---------------------------------------------------------------------------
# 테스트: compute_rubric_avg / get_overall_rating
# ---------------------------------------------------------------------------

class TestRubric:
    def test_평균_계산(self):
        assert compute_rubric_avg(4, 5, 4) == 4.33

    def test_만점(self):
        assert compute_rubric_avg(5, 5, 5) == 5.0

    def test_최저점(self):
        assert compute_rubric_avg(1, 1, 1) == 1.0

    def test_점수_범위_초과_예외(self):
        with pytest.raises(ValueError):
            compute_rubric_avg(0, 5, 5)

    def test_overall_rating_상용가능(self):
        assert get_overall_rating(4.0) == "상용가능"
        assert get_overall_rating(5.0) == "상용가능"

    def test_overall_rating_수정필요(self):
        assert get_overall_rating(3.0) == "수정필요"
        assert get_overall_rating(2.5) == "수정필요"

    def test_overall_rating_부적합(self):
        assert get_overall_rating(2.0) == "부적합"
        assert get_overall_rating(1.0) == "부적합"

    def test_경계값_4점(self):
        """4.00 정확히 경계 → '상용가능'."""
        assert get_overall_rating(4.00) == "상용가능"

    def test_경계값_2점50(self):
        """2.50 정확히 경계 → '수정필요'."""
        assert get_overall_rating(2.50) == "수정필요"
