# -*- coding: utf-8 -*-
"""module_3 Soft 제약(제공빈도·기호도·식단가) 검증 테스트.

DB/네트워크 없이 mock 메뉴(테스트 더블)로 CP-SAT 목적함수 동작을 확인한다.
합성 '학습 데이터'가 아니라 solver 로직 검증용 픽스처이다.

실행: pytest module_3/tests/test_soft_constraints.py -v
"""
import os
import sys

import pytest

# module_3/src 를 import 경로에 추가
SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

import soft_constraints as sc          # noqa: E402
from csp_solver import MenuItem, MealPlanRequest, build_and_solve  # noqa: E402


def mk(menu_id, name, category, cost=200.0, kcal=150.0):
    """간단한 MenuItem 픽스처 생성기."""
    return MenuItem(menu_id=menu_id, name=name, category=category,
                    calories=kcal, cost_won=cost)


# ---------------------------------------------------------------------------
# 분류기 단위 테스트
# ---------------------------------------------------------------------------
def test_classify_food_types_keywords():
    menus = [
        mk(1, "잡곡밥", "주식"),
        mk(2, "흰쌀밥", "주식"),
        mk(3, "시금치나물", "반찬"),
        mk(4, "오징어튀김", "반찬"),
        mk(5, "감자햄볶음", "반찬"),
        mk(6, "제육볶음", "반찬"),
    ]
    ft = sc.classify_food_types(menus)
    assert ft.get(0) == {"잡곡밥"}
    assert 1 not in ft                       # 흰쌀밥: 유형 없음
    assert ft.get(2) == {"나물"}
    assert ft.get(3) == {"튀김"}
    assert ft.get(4) == {"가공식품"}          # '햄' 키워드
    assert 5 not in ft                       # 제육볶음: 유형 없음


def test_classify_japgok_only_in_juisik():
    """잡곡밥 키워드라도 주식 카테고리가 아니면 잡곡밥 유형 아님(오분류 방지)."""
    menus = [mk(1, "잡곡전", "반찬")]
    ft = sc.classify_food_types(menus)
    assert 0 not in ft


def test_classify_commercial_signal():
    """키워드가 없어도 COMMERCIAL 조달형태면 가공식품으로 확정."""
    menus = [mk(9, "수제너비아니", "반찬")]
    ft = sc.classify_food_types(menus, commercial_menu_ids={9})
    assert ft.get(0) == {"가공식품"}


def test_scale_targets():
    assert sc.scale_targets(3.0, 7) == 3
    assert sc.scale_targets(2.0, 7) == 2
    assert sc.scale_targets(3.0, 31) == 13   # round(13.28)
    assert sc.scale_targets(2.0, 31) == 9    # round(8.857)


# ---------------------------------------------------------------------------
# [검증 루프 1] 항별 동작 — 식단가 / 기호도 / 제공빈도
# ---------------------------------------------------------------------------
def test_cost_minimized():
    """유형·기호 동일 시 저렴한 메뉴가 선택된다."""
    menus = [mk(1, "값싼밥", "주식", cost=100.0),
             mk(2, "비싼밥", "주식", cost=900.0)]
    req = MealPlanRequest(days=1, meals=("점심",), composition={"주식": 1},
                          solver_time_limit=5.0)
    res = build_and_solve(menus, req)
    assert res.status in ("OPTIMAL", "FEASIBLE")
    assert res.plan[1]["점심"] == ["값싼밥"]


def test_preference_reward():
    """기호도(선호) 점수가 높은 메뉴가 선택된다 (원가 동일)."""
    menus = [mk(1, "선호밥", "주식", cost=300.0),
             mk(2, "보통밥", "주식", cost=300.0)]
    req = MealPlanRequest(days=1, meals=("점심",), composition={"주식": 1},
                          pref_scores={0: 5.0}, solver_time_limit=5.0)
    res = build_and_solve(menus, req)
    assert res.plan[1]["점심"] == ["선호밥"]
    assert res.soft_breakdown["preference_score"] == 5


def test_frequency_min_multigrain():
    """잡곡밥 주3회 이상 soft 목표가 충족된다."""
    menus = [mk(1, "잡곡밥", "주식", cost=300.0),
             mk(2, "흰쌀밥", "주식", cost=300.0)]
    req = MealPlanRequest(days=7, meals=("점심",), composition={"주식": 1},
                          solver_time_limit=5.0)
    res = build_and_solve(menus, req)
    info = res.soft_breakdown["frequency"]["잡곡밥"]
    assert info["count"] >= 3 and info["satisfied"]


def test_frequency_max_fried():
    """튀김 주2회 이하 soft 목표가 충족된다 (중립 대체재 존재)."""
    menus = [mk(1, "오징어튀김", "반찬", cost=300.0),
             mk(2, "두부조림", "반찬", cost=300.0)]
    req = MealPlanRequest(days=7, meals=("점심",), composition={"반찬": 1},
                          solver_time_limit=5.0)
    res = build_and_solve(menus, req)
    info = res.soft_breakdown["frequency"]["튀김"]
    assert info["count"] <= 2 and info["satisfied"]


# ---------------------------------------------------------------------------
# [검증 루프 2] 엣지 케이스
# ---------------------------------------------------------------------------
def test_no_candidate_stays_feasible():
    """유형 후보가 없으면 soft 미충족이지만 infeasible 되지 않는다."""
    menus = [mk(1, "흰쌀밥", "주식", cost=300.0)]
    req = MealPlanRequest(days=7, meals=("점심",), composition={"주식": 1},
                          solver_time_limit=5.0)
    res = build_and_solve(menus, req)
    assert res.status in ("OPTIMAL", "FEASIBLE")
    info = res.soft_breakdown["frequency"]["잡곡밥"]
    assert info["count"] == 0 and not info["satisfied"]


def test_weight_zero_disables_frequency():
    """w_freq=0 이면 제공빈도가 목적함수에 영향을 주지 않는다."""
    menus = [mk(1, "오징어튀김", "반찬", cost=100.0),
             mk(2, "두부조림", "반찬", cost=900.0)]
    w = sc.SoftWeights(w_freq=0, w_pref=5, w_cost=1)
    req = MealPlanRequest(days=7, meals=("점심",), composition={"반찬": 1},
                          soft_weights=w, solver_time_limit=5.0)
    res = build_and_solve(menus, req)
    # 빈도 무시 → 원가만 작동 → 저렴한 튀김을 계속 선택(2회 초과 허용)
    assert res.soft_breakdown["frequency"]["튀김"]["count"] > 2


def test_31_day_horizon_runs():
    """31일 지평에서도 정상 풀이되고 스케일 목표가 적용된다."""
    menus = _full_menu_pool()
    req = MealPlanRequest(days=31, solver_time_limit=8.0)
    res = build_and_solve(menus, req)
    assert res.status in ("OPTIMAL", "FEASIBLE")
    assert res.soft_breakdown["frequency"]["잡곡밥"]["target"] == 13


def test_pref_none_neutral():
    """pref_scores=None 이어도 오류 없이 중립 처리된다."""
    menus = [mk(1, "밥", "주식", cost=300.0)]
    req = MealPlanRequest(days=1, meals=("점심",), composition={"주식": 1},
                          solver_time_limit=5.0)
    res = build_and_solve(menus, req)
    assert res.soft_breakdown["preference_score"] == 0


# ---------------------------------------------------------------------------
# [검증 루프 3] 통합 — 전체 구성 + 조합 가능성
# ---------------------------------------------------------------------------
def _full_menu_pool():
    """반상 구성(주식1·국1·주찬1·부찬2이상·김치1)을 채울 수 있는 후보 풀."""
    return [
        mk(1, "잡곡밥", "주식", cost=320.0),
        mk(2, "흰쌀밥", "주식", cost=280.0),
        mk(3, "미역국", "국", cost=210.0),
        mk(4, "된장국", "국", cost=190.0),
        mk(7, "오징어튀김", "주찬", cost=520.0),
        mk(8, "감자햄볶음", "주찬", cost=430.0),
        mk(9, "두부조림", "주찬", cost=300.0),
        mk(10, "제육볶음", "주찬", cost=460.0),
        mk(5, "시금치나물", "부찬", cost=150.0),
        mk(6, "콩나물무침", "부찬", cost=140.0),
        mk(11, "도라지생채", "부찬", cost=170.0),
        mk(12, "브로콜리무침", "부찬", cost=160.0),
        mk(13, "배추김치", "김치", cost=90.0),
        mk(14, "깍두기", "김치", cost=95.0),
    ]


def test_full_pipeline_composition_and_targets():
    """반상 구성(주식1·국1·주찬1·부찬2이상·김치1)과 제공빈도 목표를 함께 만족한다."""
    menus = _full_menu_pool()
    req = MealPlanRequest(days=7, solver_time_limit=10.0)
    res = build_and_solve(menus, req)
    assert res.status in ("OPTIMAL", "FEASIBLE")
    # 끼니 슬롯 구성 검증
    for day, meals in res.plan.items():
        for _, picks in meals.items():
            cats = [next(m.category for m in menus if m.name == p) for p in picks]
            assert cats.count("주식") == 1
            assert cats.count("국") == 1
            assert cats.count("주찬") == 1
            assert cats.count("부찬") >= 2
            assert cats.count("김치") == 1
    # 제공빈도: 4개 규칙 모두 충족되어야 한다(대체재 충분)
    for ftype, info in res.soft_breakdown["frequency"].items():
        assert info["satisfied"], f"{ftype} 미충족: {info}"


def test_objective_is_number_and_cost_reported():
    menus = _full_menu_pool()
    req = MealPlanRequest(days=7, solver_time_limit=10.0)
    res = build_and_solve(menus, req)
    assert isinstance(res.objective, (int, float))
    assert res.soft_breakdown["total_cost_won"] == res.total_cost


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
