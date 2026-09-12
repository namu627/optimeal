# -*- coding: utf-8 -*-
"""soft_constraints_diversity.py 검증 테스트 (DB·네트워크 불필요, mock 메뉴).

실행:  pytest module_3/tests/test_soft_constraints_diversity.py -v

케이스 구성:
  분류기(2) · 조리법 다양성(2) · 색감(2) · 주재료/맛 중복(3) · 제철(1) ·
  나트륨당(2) · 우아한 저하/통합(2) · 리포팅(1)  = 15
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

pytest.importorskip("ortools", reason="ortools 미설치")
from ortools.sat.python import cp_model  # noqa: E402

from soft_constraints_diversity import (  # noqa: E402
    add_diversity_soft_objective,
    classify_commercial,
    classify_cooking_methods,
    evaluate_diversity_breakdown,
)


# --------------------------------------------------------------------------- #
# mock MenuItem — pmy MenuItem 의 필요한 필드만 모사                            #
# --------------------------------------------------------------------------- #
@dataclass
class M:
    menu_id: int
    name: str
    category: str = "반찬"
    colors: set = field(default_factory=set)
    season_score: float = 0.0
    sodium: float = 0.0
    sugar: float = 0.0
    main_ingredient: str | None = None
    taste: str | None = None
    cooking_method: str | None = None


def _build_and_solve(menus, *, days, n_meals, per_slot=1, weights=None, extra=0):
    """각 (d,s) 슬롯에 정확히 per_slot 개 메뉴를 고르는 최소 모델로 목적함수를 검증."""
    model = cp_model.CpModel()
    Mr, D, S = range(len(menus)), range(days), range(n_meals)
    x = {(m, d, s): model.NewBoolVar(f"x_{m}_{d}_{s}") for m in Mr for d in D for s in S}
    for d in D:
        for s in S:
            model.Add(sum(x[m, d, s] for m in Mr) == per_slot)
    soft = add_diversity_soft_objective(
        model, x, menus, days=days, n_meals=n_meals, weights=weights)
    model.Maximize(soft.score + extra)   # extra: 외부 Soft 항 합성 모사
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 5.0
    status = solver.Solve(model)
    return status, solver, x, soft


# =========================== 분류기 ======================================== #
def test_classify_by_name_keywords():
    menus = [M(1, "돈까스"), M(2, "시금치나물"), M(3, "제육볶음"),
             M(4, "고등어구이"), M(5, "감자조림"), M(6, "미역국")]
    got = classify_cooking_methods(menus)
    assert got[0] == "튀김"
    assert got[1] == "무침"
    assert got[2] == "볶음"
    assert got[3] == "구이"
    assert got[4] == "조림"
    assert got[5] == "끓이기"   # DB method_name 어휘에 맞춤(국·탕류 → '끓이기')


def test_classify_authoritative_overrides_name():
    # 로더가 cooking_method 를 채우면 이름보다 우선
    menus = [M(1, "돈까스", cooking_method="튀김"), M(2, "미상메뉴명xyz")]
    got = classify_cooking_methods(menus)
    assert got[0] == "튀김"
    assert 1 not in got   # 미상 → 분류 제외(우아한 저하)


def test_classify_db_override_merges_with_keyword():
    # DB 주입값(override)이 있는 메뉴는 그 값, 없는 메뉴는 이름 키워드로 병합
    menus = [M(1, "제육볶음"), M(2, "고등어구이"), M(3, "미상메뉴xyz")]
    got = classify_cooking_methods(menus, override_by_idx={0: "끓이기"})  # 0번만 DB 정답 주입
    assert got[0] == "끓이기"   # 주입값 우선(이름은 '볶음'이지만 무시)
    assert got[1] == "구이"     # 미주입 → 이름 키워드 폴백
    assert 2 not in got         # 미상 유지


# =========================== 조리법 다양성 ================================= #
def test_cooking_diversity_reaches_target_when_possible():
    # 4개 조리법 풀 → 하루 4슬롯이면 3종 이상 달성 가능 → 부족량 0
    menus = [M(1, "제육볶음"), M(2, "고등어구이"), M(3, "감자조림"), M(4, "시금치나물")]
    status, solver, x, soft = _build_and_solve(menus, days=1, n_meals=4)
    assert status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    assert solver.Value(soft.cook_short_vars[0]) == 0


def test_cooking_diversity_penalized_when_monotone():
    # 전부 볶음 → 종수 1 → 부족량 = target-1 = 2
    menus = [M(1, "제육볶음"), M(2, "김치볶음"), M(3, "어묵볶음")]
    status, solver, x, soft = _build_and_solve(menus, days=1, n_meals=3)
    assert solver.Value(soft.cook_short_vars[0]) == 2


# =========================== 색감 다양성 =================================== #
def test_color_term_inactive_when_no_color_data():
    menus = [M(1, "제육볶음"), M(2, "고등어구이"), M(3, "감자조림")]
    status, solver, x, soft = _build_and_solve(menus, days=1, n_meals=3)
    assert soft.active_terms["color"] is False
    assert soft.color_short_vars == {}


def test_color_diversity_rewarded_when_present():
    menus = [M(1, "제육볶음", colors={"red"}), M(2, "고등어구이", colors={"white"}),
             M(3, "시금치나물", colors={"green"}), M(4, "당근채", colors={"orange"})]
    status, solver, x, soft = _build_and_solve(menus, days=1, n_meals=4)
    assert soft.active_terms["color"] is True
    assert solver.Value(soft.color_short_vars[0]) == 0   # 3색↑ 달성


# =========================== 주재료/맛 중복 =============================== #
def test_main_ingredient_inactive_when_no_data():
    menus = [M(1, "제육볶음"), M(2, "고등어구이")]
    status, solver, x, soft = _build_and_solve(menus, days=7, n_meals=1)
    assert soft.active_terms["main"] is False
    assert soft.main_excess_vars == {}


def test_main_ingredient_repeat_penalized():
    # 돼지 메뉴만 있어 매일 돼지 → 주당 cap(2) 초과 → excess 발생
    menus = [M(1, "제육볶음", main_ingredient="돼지고기"),
             M(2, "돈까스", main_ingredient="돼지고기")]
    status, solver, x, soft = _build_and_solve(menus, days=7, n_meals=1)
    assert soft.active_terms["main"] is True
    # 7일 배치인데 cap=2 → 최소 초과 5
    assert solver.Value(soft.main_excess_vars["돼지고기"]) >= 5


def test_taste_inactive_when_no_schema():
    menus = [M(1, "제육볶음"), M(2, "고등어구이")]
    status, solver, x, soft = _build_and_solve(menus, days=3, n_meals=1)
    assert soft.active_terms["taste"] is False


# =========================== 제철 ========================================= #
def test_season_prefers_higher_score():
    # 동일 슬롯에서 제철점수 높은 메뉴 선호
    menus = [M(1, "봄나물무침", season_score=0.9), M(2, "겨울나물무침", season_score=0.1)]
    status, solver, x, soft = _build_and_solve(menus, days=1, n_meals=1)
    assert soft.active_terms["season"] is True
    chosen = [m for m in range(len(menus)) if solver.Value(x[m, 0, 0])]
    assert chosen == [0]


# =========================== 나트륨/당 ==================================== #
def test_sodium_prefers_lower():
    menus = [M(1, "저염국", sodium=100.0), M(2, "고염국", sodium=900.0)]
    status, solver, x, soft = _build_and_solve(menus, days=1, n_meals=1)
    assert soft.active_terms["sodium"] is True
    chosen = [m for m in range(len(menus)) if solver.Value(x[m, 0, 0])]
    assert chosen == [0]


def test_sugar_prefers_lower():
    menus = [M(1, "저당조림", sugar=1.0), M(2, "고당조림", sugar=30.0)]
    status, solver, x, soft = _build_and_solve(menus, days=1, n_meals=1)
    assert soft.active_terms["sugar"] is True
    chosen = [m for m in range(len(menus)) if solver.Value(x[m, 0, 0])]
    assert chosen == [0]


# =========================== 우아한 저하 / 통합 ============================ #
def test_graceful_degradation_all_empty():
    # 모든 부가필드 없음 → 크래시 없이 풀리고 score 유효(조리법만 활성 가능)
    menus = [M(1, "제육볶음"), M(2, "고등어구이"), M(3, "감자조림")]
    status, solver, x, soft = _build_and_solve(menus, days=1, n_meals=3)
    assert status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    assert soft.active_terms["color"] is False
    assert soft.active_terms["main"] is False
    assert soft.active_terms["season"] is False


def test_integration_with_external_soft_term():
    # ksm 항을 모사한 외부 정수-선형 항과 합성해도 정상 최대화
    menus = [M(1, "봄나물무침", season_score=0.8, colors={"green"}),
             M(2, "고등어구이", colors={"white"})]
    model = cp_model.CpModel()
    x = {(m, 0, 0): model.NewBoolVar(f"x_{m}") for m in range(len(menus))}
    model.Add(sum(x[m, 0, 0] for m in range(len(menus))) == 1)
    soft = add_diversity_soft_objective(model, x, menus, days=1, n_meals=1)
    external = 3 * x[1, 0, 0]                       # 외부 Soft 가점
    model.Maximize(soft.score + external)
    solver = cp_model.CpSolver()
    assert solver.Solve(model) in (cp_model.OPTIMAL, cp_model.FEASIBLE)


# =========================== 완제품 자제 ================================== #
def test_classify_commercial_by_name():
    menus = [M(1, "비엔나소시지볶음"), M(2, "어묵조림"), M(3, "시금치나물"), M(4, "고등어구이")]
    got = classify_commercial(menus)
    assert got == {0, 1}                # 소시지·어묵 → 완제품, 나물·구이 → 아님


def test_classify_commercial_sidechannel_by_menu_id():
    # 메뉴명에 키워드가 없어도 menu_id 주입(ADR-004 COMMERCIAL)으로 완제품 확정
    menus = [M(10, "특제모둠"), M(11, "봄나물무침")]
    got = classify_commercial(menus, commercial_menu_ids={10})
    assert got == {0}


def test_commercial_penalty_prefers_non_commercial():
    # 같은 슬롯에서 완제품 아닌 메뉴를 선호
    menus = [M(1, "어묵볶음"), M(2, "감자볶음")]     # 1번만 완제품
    status, solver, x, soft = _build_and_solve(menus, days=1, n_meals=1)
    assert soft.active_terms["commercial"] is True
    chosen = [m for m in range(len(menus)) if solver.Value(x[m, 0, 0])]
    assert chosen == [1]


def test_commercial_inactive_when_none():
    menus = [M(1, "감자볶음"), M(2, "시금치나물")]   # 완제품 키워드 없음
    status, solver, x, soft = _build_and_solve(menus, days=1, n_meals=1)
    assert soft.active_terms["commercial"] is False


# =========================== side-channel 주입 (ksm 규약) ================= #
def test_sidechannel_sodium_injection_overrides_attr():
    # MenuItem 에 sodium 이 없어도(0) side-channel 주입값으로 채점 → 낮은 쪽 선호
    menus = [M(1, "국A"), M(2, "국B")]           # sodium 속성 기본 0
    model = cp_model.CpModel()
    xx = {(m, 0, 0): model.NewBoolVar(f"x{m}") for m in range(len(menus))}
    model.Add(sum(xx[m, 0, 0] for m in range(len(menus))) == 1)
    soft = add_diversity_soft_objective(
        model, xx, menus, days=1, n_meals=1,
        sodium_by_idx={0: 120.0, 1: 950.0})       # side-channel 주입
    model.Maximize(soft.score)
    solver = cp_model.CpSolver()
    solver.Solve(model)
    assert soft.active_terms["sodium"] is True
    chosen = [m for m in range(len(menus)) if solver.Value(xx[m, 0, 0])]
    assert chosen == [0]


def test_sidechannel_main_ingredient_injection():
    # main 속성 없이 main_by_idx 주입만으로 주재료 중복 항 활성화
    menus = [M(1, "메뉴A"), M(2, "메뉴B")]
    model = cp_model.CpModel()
    xx = {(m, d, 0): model.NewBoolVar(f"x{m}_{d}") for m in range(2) for d in range(7)}
    for d in range(7):
        model.Add(sum(xx[m, d, 0] for m in range(2)) == 1)
    soft = add_diversity_soft_objective(
        model, xx, menus, days=7, n_meals=1,
        main_by_idx={0: "돼지고기", 1: "돼지고기"})   # 둘 다 같은 주재료
    model.Maximize(soft.score)
    solver = cp_model.CpSolver()
    solver.Solve(model)
    assert soft.active_terms["main"] is True
    assert solver.Value(soft.main_excess_vars["돼지고기"]) >= 5


# =========================== 리포팅 ======================================= #
def test_breakdown_report_keys():
    menus = [M(1, "봄나물무침", season_score=0.7, colors={"green"}, sodium=200.0, sugar=3.0)]
    model = cp_model.CpModel()
    x = {(0, 0, 0): model.NewBoolVar("x")}
    model.Add(x[0, 0, 0] == 1)
    soft = add_diversity_soft_objective(model, x, menus, days=1, n_meals=1)
    model.Maximize(soft.score)
    solver = cp_model.CpSolver()
    solver.Solve(model)
    rep = evaluate_diversity_breakdown(solver, x, menus, soft, days=1, n_meals=1)
    for key in ("active_terms", "cooking_diversity", "color_diversity",
                "total_season_score", "total_sodium_mg", "total_sugar_g"):
        assert key in rep
    assert rep["total_sodium_mg"] == 200.0
