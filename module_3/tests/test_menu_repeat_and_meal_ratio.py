# -*- coding: utf-8 -*-
"""메뉴 중복 회피(Q-1) · 끼니별 에너지 배분(Q-3) 검증 테스트.

배경: 2026-08-10 실데이터 식단 생성에서 (Q-1) 같은 메뉴가 점심·저녁·날짜 간 반복되고
      (Q-3) 아침/점심/저녁이 동일 기준이어서 아침에 부적절한 메뉴가 배치됐다.
      두 제약을 `csp_hard_constraints` 에 추가했고 그 동작을 여기서 고정한다.

DB 없이 mock 메뉴로 검증한다(solver 로직 검증용 픽스처, 학습 데이터 아님).
"""
import os
import sys

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

import csp_hard_constraints as hc          # noqa: E402
from csp_solver import MealPlanRequest, MenuItem, build_and_solve  # noqa: E402

MEALS = ("아침", "점심", "저녁")


def pool(n_per_cat=12):
    """카테고리별 후보를 넉넉히 만든다.

    픽스처 설계 주의 (둘 다 위반하면 제약이 아니라 픽스처 때문에 INFEASIBLE 이 된다):
      1. **열량 폭**을 실제 DB 수준으로 넓게 (주식 150~400 · 국 20~120 · 반찬 20~250).
         좁으면 한 끼 최소 열량이 아침 밴드 상한(600×1.15=690)을 넘는다.
      2. **반찬은 끼니당 2개**가 필요하므로 후보를 2배로 준다. 중복 금지 창이 걸리면
         창 안에서 필요한 서로 다른 반찬 수 = 2 × 끼니수 × 창일수 이다.

    Args:
        n_per_cat: 주식·국 후보 수. 반찬은 이 값의 2배로 생성된다.
    """
    spec = (("주식", 150.0, 400.0, 1), ("국", 20.0, 120.0, 1), ("반찬", 20.0, 250.0, 2))
    out, mid = [], 1
    for cat, lo, hi, mult in spec:
        count = n_per_cat * mult
        step = (hi - lo) / max(1, count - 1)
        for i in range(count):
            out.append(MenuItem(mid, f"{cat}{i}", cat, round(lo + i * step, 1),
                                cost_won=200.0))
            mid += 1
    return out


def picks_by_day(res):
    """{day: [메뉴명, ...]} — 하루 전체 끼니 합친 목록."""
    return {d: [n for picks in meals.values() for n in picks]
            for d, meals in res.plan.items()}


# ---------------------------------------------------------------------------
# Q-1 메뉴 중복 회피
# ---------------------------------------------------------------------------
def test_no_repeat_within_window():
    """창(3일) 내 동일 메뉴가 두 번 등장하지 않는다 — 같은 날 중복 포함."""
    cfg = hc.HardConstraintConfig(target_kcal_per_day=2000.0, budget_limit_per_person=None)
    res = build_and_solve(pool(12), MealPlanRequest(days=3, meals=MEALS, hard=cfg,
                                                   solver_time_limit=40.0))
    assert res.plan, f"해가 있어야 한다: {res.status}"
    day_lists = picks_by_day(res)

    for day, names in day_lists.items():
        assert len(names) == len(set(names)), f"{day}일 내 중복: {names}"

    all_names = [n for names in day_lists.values() for n in names]
    assert len(all_names) == len(set(all_names)), "3일 창 내 재등장 발생"

    rp = res.hard_breakdown["menu_repeat"]
    assert rp["window_days"] == 3
    assert rp["violations"] == [], rp["violations"]


def test_repeat_allowed_outside_window():
    """창 밖(4일 간격 이상)에서는 재등장이 허용된다 — 과도한 금지가 아니다."""
    cfg = hc.HardConstraintConfig(target_kcal_per_day=2000.0, budget_limit_per_person=None,
                                  menu_repeat_window_days=2)
    # 후보를 빡빡하게 주면 재사용이 불가피해진다(카테고리당 4종 × 끼니 슬롯 요구).
    res = build_and_solve(pool(n_per_cat=6), MealPlanRequest(days=5, meals=MEALS, hard=cfg,
                                                            solver_time_limit=40.0))
    assert res.plan, f"창이 2일이면 해가 존재해야 한다: {res.status}"
    assert res.hard_breakdown["menu_repeat"]["violations"] == []
    # 5일 × 12접시 = 60 슬롯인데 후보는 18종 → 재사용이 실제로 일어났음을 확인
    assert res.hard_breakdown["menu_repeat"]["max_same_menu_count"] > 1


def test_repeat_window_can_be_disabled():
    """0 이면 제약이 붙지 않는다(기존 호출부 호환)."""
    cfg = hc.HardConstraintConfig(target_kcal_per_day=2000.0, budget_limit_per_person=None,
                                  menu_repeat_window_days=0)
    res = build_and_solve(pool(), MealPlanRequest(days=2, meals=MEALS, hard=cfg,
                                                 solver_time_limit=30.0))
    assert res.plan
    assert res.hard_breakdown["active_terms"]["menu_repeat_window"] is False
    assert res.hard_breakdown["menu_repeat"]["window_days"] == 0


# ---------------------------------------------------------------------------
# Q-3 끼니별 에너지 배분
# ---------------------------------------------------------------------------
def test_meal_energy_ratio_enforced():
    """아침30·점심40·저녁30% 밴드(±15%)를 실제로 지킨다."""
    cfg = hc.HardConstraintConfig(target_kcal_per_day=2000.0, budget_limit_per_person=None)
    res = build_and_solve(pool(16), MealPlanRequest(days=2, meals=MEALS, hard=cfg,
                                                   solver_time_limit=40.0))
    assert res.plan, f"해가 있어야 한다: {res.status}"
    assert res.hard_breakdown["active_terms"]["meal_energy_ratio"] is True

    by_name = {m.name: m for m in pool(16)}
    for day, meals in res.plan.items():
        for si, mname in enumerate(MEALS):
            kcal = sum(by_name[n].calories for n in meals[mname])
            target = 2000.0 * cfg.meal_energy_ratios[si]
            lo, hi = target * 0.85, target * 1.15
            assert lo <= kcal <= hi, f"{day}일 {mname}: {kcal} ∉ ({lo:.0f},{hi:.0f})"

    report = res.hard_breakdown["meal_kcal"]
    assert len(report) == 2 * 3
    assert all(i["ok"] for i in report)


def test_meal_ratio_skipped_when_meal_count_mismatch():
    """끼니 수와 비율 개수가 다르면 자동 skip — 1끼 호출부가 깨지지 않는다."""
    cfg = hc.HardConstraintConfig(target_kcal_per_day=700.0, budget_limit_per_person=None)
    res = build_and_solve(pool(), MealPlanRequest(days=2, meals=("점심",), hard=cfg,
                                                 solver_time_limit=30.0))
    assert res.plan, f"1끼 구성에서도 해가 나와야 한다: {res.status}"
    assert res.hard_breakdown["active_terms"]["meal_energy_ratio"] is False
    assert res.hard_breakdown["meal_kcal"] == []


def test_meal_ratio_can_be_disabled():
    """enable_meal_ratio=False 면 끼니 밴드가 걸리지 않는다."""
    cfg = hc.HardConstraintConfig(target_kcal_per_day=2000.0, budget_limit_per_person=None,
                                  enable_meal_ratio=False)
    res = build_and_solve(pool(), MealPlanRequest(days=2, meals=MEALS, hard=cfg,
                                                 solver_time_limit=30.0))
    assert res.plan
    assert res.hard_breakdown["active_terms"]["meal_energy_ratio"] is False


def test_soft_only_run_unaffected():
    """hard=None(Soft 전용) 경로는 두 제약과 무관하게 동작한다 — 하위호환."""
    res = build_and_solve(pool(), MealPlanRequest(days=2, meals=MEALS, hard=None,
                                                 solver_time_limit=30.0))
    assert res.plan
    assert res.hard_breakdown is None


@pytest.mark.parametrize("days", [1, 2, 4])
def test_window_larger_than_horizon(days):
    """창이 지평보다 길면 전 기간을 하나의 창으로 본다(경계 처리)."""
    cfg = hc.HardConstraintConfig(target_kcal_per_day=2000.0, budget_limit_per_person=None,
                                  menu_repeat_window_days=10)
    res = build_and_solve(pool(6 * days), MealPlanRequest(days=days, meals=MEALS, hard=cfg,
                                                        solver_time_limit=40.0))
    assert res.plan, f"days={days} 에서 해가 있어야 한다: {res.status}"
    assert res.hard_breakdown["menu_repeat"]["window_days"] == min(10, days)
    names = [n for meals in res.plan.values() for picks in meals.values() for n in picks]
    assert len(names) == len(set(names)), "전 기간 단일 창인데 재등장 발생"
