# -*- coding: utf-8 -*-
"""MealPlanResult.plan_ids(솔버가 고른 menu_id 병행 필드) 검증 테스트.

plan 은 {day: {meal: [menu_name, ...]}} 로 이름만 담아, 같은 recipe_name 에 nutrition_id 가
다른 동명 메뉴를 구분하지 못한다. plan_ids 는 같은 chosen 에서 만든 {day: {meal: [menu_id, ...]}}
이므로 모양·순서가 plan 과 같고, 솔버가 실제로 고른 행을 가리켜야 한다.

DB/네트워크 없이 mock 메뉴(테스트 더블)로 확인한다.
실행: pytest module_3/tests/test_plan_ids.py -v
"""
import os
import sys

# module_3/src 를 import 경로에 추가
SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

from csp_solver import MenuItem, MealPlanRequest, build_and_solve  # noqa: E402


def mk(menu_id, name, category, cost=200.0, kcal=150.0):
    """간단한 MenuItem 픽스처 생성기."""
    return MenuItem(menu_id=menu_id, name=name, category=category,
                    calories=kcal, cost_won=cost)


def _solve_multi():
    """2일 × 2끼 × (주식1·국1) — 모양 비교용 다중 슬롯 식단."""
    menus = [mk(10 + i, f"밥{i}", "주식", cost=100.0 + i) for i in range(6)]
    menus += [mk(50 + i, f"국{i}", "국", cost=100.0 + i) for i in range(6)]
    req = MealPlanRequest(days=2, meals=("아침", "점심"),
                          composition={"주식": 1, "국": 1}, solver_time_limit=5.0)
    return menus, build_and_solve(menus, req)


def test_plan_ids_same_shape_as_plan():
    """(a) plan_ids 는 plan 과 같은 일·끼니 키, 같은 길이의 목록을 가진다."""
    _, res = _solve_multi()
    assert res.status in ("OPTIMAL", "FEASIBLE")
    assert res.plan_ids.keys() == res.plan.keys()
    for day, meals in res.plan.items():
        assert res.plan_ids[day].keys() == meals.keys()
        for meal, names in meals.items():
            assert len(res.plan_ids[day][meal]) == len(names)


def test_plan_ids_map_to_plan_names_in_order():
    """(b) 각 자리의 id 를 이름으로 바꾸면 plan 과 순서까지 일치한다."""
    menus, res = _solve_multi()
    name_of = {m.menu_id: m.name for m in menus}
    for day, meals in res.plan.items():
        for meal, names in meals.items():
            assert [name_of[i] for i in res.plan_ids[day][meal]] == names


def test_plan_ids_pick_solver_chosen_row_among_same_names():
    """(c) 동명·다른 id 후보에서 plan_ids 는 솔버가 실제로 고른 행(여기선 더 싼 행)의 id 다.

    후보 순서를 뒤집어도 같은 행을 가리켜야 한다 — 이름으로 첫/마지막 행을 고르는 방식이면
    둘 중 한 경우는 틀린다(동명 메뉴 버그의 원인).
    """
    for pool in ([mk(101, "가지볶음", "주식", cost=900.0), mk(202, "가지볶음", "주식", cost=100.0)],
                 [mk(202, "가지볶음", "주식", cost=100.0), mk(101, "가지볶음", "주식", cost=900.0)]):
        req = MealPlanRequest(days=1, meals=("점심",), composition={"주식": 1},
                              solver_time_limit=5.0)
        res = build_and_solve(pool, req)
        assert res.status in ("OPTIMAL", "FEASIBLE")
        assert res.plan[1]["점심"] == ["가지볶음"]          # 이름만으로는 구분 불가
        assert res.plan_ids[1]["점심"] == [202]             # 싼 행(솔버 선택)의 id
        assert res.total_cost == 100                       # 솔버 원가와 같은 행


def test_plan_ids_empty_when_no_solution():
    """해가 없으면 plan 과 마찬가지로 빈 dict."""
    menus = [mk(1, "밥", "주식")]
    req = MealPlanRequest(days=1, meals=("점심",), composition={"주식": 2},
                          solver_time_limit=5.0)
    res = build_and_solve(menus, req)
    assert not res.plan
    assert res.plan_ids == {}
