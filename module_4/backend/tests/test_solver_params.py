"""/api/menu/generate CP-SAT 파라미터 — 여러 끼(2식 이상)만 가벼운 presolve.

7일 3식은 presolve(Probe·FindBig*LinearOverlap)에 ~11.5초를 써서 첫 해가 늦었다(2026-10-01).
점심만(1식)은 기존 파라미터 그대로여야 한다(7일 점심 정체 종료 시점이 늦어지지 않게).
DB·모듈3 없이 도는 순수 단위 테스트.
"""
from __future__ import annotations

from module_4.backend.src.routers import menu


def test_single_meal_keeps_base_params():
    assert menu._solver_params(("점심",)) == menu.SOLVER_PARAMS


def test_multi_meal_adds_light_presolve():
    for meals in (("점심", "저녁"), ("아침", "점심", "저녁")):
        p = menu._solver_params(meals)
        assert p["max_presolve_iterations"] == 1
        assert p["cp_model_probing_level"] == 0
        assert p["find_big_linear_overlap"] is False


def test_params_are_a_fresh_dict_per_request():
    p = menu._solver_params(("아침", "점심", "저녁"))
    p["cp_model_probing_level"] = 2
    assert menu._solver_params(("아침", "점심", "저녁"))["cp_model_probing_level"] == 0
    assert "cp_model_probing_level" not in menu.SOLVER_PARAMS


def test_long_multi_meal_turns_presolve_off():
    """7일 초과·2식 이상만 presolve 를 끈다(31일 3식: presolve ~19초가 80초 한도를 넘김, 2026-10-02)."""
    for meals in (("점심", "저녁"), ("아침", "점심", "저녁")):
        assert menu._solver_params(meals, 31)["cp_model_presolve"] is False
        assert "cp_model_presolve" not in menu._solver_params(meals, 7)
    assert menu._solver_params(("점심",), 31) == menu.SOLVER_PARAMS


def test_long_multi_meal_gets_larger_hint_share():
    """힌트 몫: 7일 초과·2식 이상만 0.85, 나머지(7일 이하·점심만)는 기존 0.5."""
    assert menu._hint_budget_ratio(31, ("아침", "점심", "저녁")) == menu.HINT_BUDGET_RATIO_LONG_MULTI
    assert menu._hint_budget_ratio(31, ("점심", "저녁")) == menu.HINT_BUDGET_RATIO_LONG_MULTI
    assert menu._hint_budget_ratio(31, ("점심",)) == menu.HINT_BUDGET_RATIO
    assert menu._hint_budget_ratio(7, ("아침", "점심", "저녁")) == menu.HINT_BUDGET_RATIO
