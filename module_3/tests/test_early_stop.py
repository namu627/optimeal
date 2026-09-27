# -*- coding: utf-8 -*-
"""조기 종료(relative_gap_limit·stall_seconds)·solver_params·stop_reason 검증 테스트.

API 경로(module_4 generate)만 켜는 opt-in 기능이라, 여기서는
  (a) 기본값이 기존 동작 그대로인지(끄면 아무것도 안 바뀜),
  (b) 켜도 반환 해가 가능해(OPTIMAL/FEASIBLE)인지,
  (c) 정체 감시자·종료 사유 판정이 규칙대로인지
를 확인한다. DB/네트워크 없이 mock 메뉴로 돈다.
실행: pytest module_3/tests/test_early_stop.py -v
"""
import os
import sys

import pytest
from ortools.sat.python import cp_model

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

import csp_solver as cs  # noqa: E402
from csp_solver import MenuItem, MealPlanRequest, build_and_solve  # noqa: E402


def mk(menu_id, name, category, cost=200.0, kcal=150.0):
    return MenuItem(menu_id=menu_id, name=name, category=category,
                    calories=kcal, cost_won=cost)


def _menus():
    menus = [mk(10 + i, f"밥{i}", "주식", cost=100.0 + i) for i in range(6)]
    menus += [mk(50 + i, f"국{i}", "국", cost=100.0 + i) for i in range(6)]
    return menus


def _req(**kw):
    return MealPlanRequest(days=2, meals=("아침", "점심"),
                           composition={"주식": 1, "국": 1}, solver_time_limit=5.0, **kw)


# ── (a) 기본값: 조기 종료 OFF ──────────────────────────────────────────
def test_defaults_are_off():
    req = MealPlanRequest()
    assert req.relative_gap_limit == 0.0
    assert req.stall_seconds is None
    assert req.stall_min_improvement == 0.0
    assert req.solver_params is None


def test_default_solve_reports_optimal_reason():
    res = build_and_solve(_menus(), _req())
    assert res.status == "OPTIMAL"
    assert res.stop_reason == "optimal"


# ── (b) 켜도 가능해를 반환 ─────────────────────────────────────────────
def test_early_stop_options_still_return_feasible_plan():
    res = build_and_solve(_menus(), _req(
        relative_gap_limit=0.05, stall_seconds=1.0,
        solver_params={"max_presolve_iterations": 1}))
    assert res.status in ("OPTIMAL", "FEASIBLE")
    assert res.stop_reason in ("optimal", "gap", "stall")
    for meals in res.plan.values():
        for names in meals.values():
            assert len(names) == 2          # 주식1·국1 구성 유지


def test_unknown_solver_param_fails_fast():
    """오타 난 파라미터 이름이 조용히 무시되면 튜닝이 안 된 줄 모른다 → 즉시 오류."""
    with pytest.raises((AttributeError, ValueError)):
        build_and_solve(_menus(), _req(solver_params={"no_such_param": 1}))


# ── (c) 정체 감시자 ────────────────────────────────────────────────────
class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_stall_watch_never_stops_before_first_solution():
    clock = _Clock()
    w = cs._StallWatch(solver=None, stall_seconds=5, clock=clock)
    clock.t = 100.0
    assert not w.should_stop(clock())


def test_stall_watch_resets_on_improvement_only():
    clock = _Clock()
    w = cs._StallWatch(solver=None, stall_seconds=5, clock=clock)
    w.record(10)                    # t=0 첫 해
    clock.t = 4.0
    w.record(10)                    # 같은 값 → 개선 아님(시계 유지)
    clock.t = 5.0
    assert w.should_stop(clock())
    w.record(11)                    # t=5 개선 → 리셋
    clock.t = 9.0
    assert not w.should_stop(clock())
    clock.t = 10.0
    assert w.should_stop(clock())


def test_stall_watch_min_improvement_treats_creeping_gain_as_stall():
    """찔끔 개선(창 안에서 +0.5%)은 min_improvement=1% 면 정체로 본다. 0 이면 정체 아님."""
    for tol, expected in ((0.01, True), (0.0, False)):
        clock = _Clock()
        w = cs._StallWatch(solver=None, stall_seconds=5, min_improvement=tol, clock=clock)
        w.record(100.0)
        clock.t = 3.0
        w.record(100.5)
        clock.t = 5.0
        assert w.should_stop(clock()) is expected


# ── (c) 종료 사유 판정 ─────────────────────────────────────────────────
class _FakeSolver:
    def __init__(self, obj, bound):
        self._o, self._b = obj, bound

    def ObjectiveValue(self):
        return self._o

    def BestObjectiveBound(self):
        return self._b


class _FakeWatch:
    def __init__(self, stopped):
        self.stopped = stopped


@pytest.mark.parametrize("status,obj,bound,watch,gap,expected", [
    (cp_model.OPTIMAL, 100, 100, None, 0.0, "optimal"),
    # CP-SAT 는 gap 도달도 OPTIMAL 로 보고한다 — 상한과 다르면 gap
    (cp_model.OPTIMAL, 100, 104, None, 0.05, "gap"),
    (cp_model.FEASIBLE, 100, 150, _FakeWatch(True), 0.05, "stall"),
    (cp_model.FEASIBLE, 100, 150, _FakeWatch(False), 0.05, "time_limit"),
    (cp_model.FEASIBLE, 100, 150, None, 0.0, "time_limit"),
    (cp_model.INFEASIBLE, 0, 0, None, 0.0, "infeasible"),
    (cp_model.UNKNOWN, 0, 0, None, 0.0, "unknown"),
])
def test_stop_reason(status, obj, bound, watch, gap, expected):
    assert cs._stop_reason(status, watch, gap, _FakeSolver(obj, bound)) == expected
