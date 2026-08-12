# -*- coding: utf-8 -*-
"""나트륨 1일 상한(H-2e 영양소 상한) 검증 테스트.

배경: 2026-08-10 검수용 식단표에서 일 나트륨이 1,665~4,352mg(권고 2,000mg)으로
      7일 중 5일 초과했다. 상한이 Hard 제약으로 없어 솔버가 낮출 이유가 없었고,
      특히 **노인(저염 대상) 프로파일에도 제약이 없다는 모순**이 있었다.
      H-2e 를 `csp_hard_constraints` 에 추가했고 그 동작을 여기서 고정한다.

DB 없이 mock 메뉴로 검증한다(solver 로직 검증용 픽스처, 학습 데이터 아님).
"""
import os
import sys

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

import alternative_menu as am              # noqa: E402
import csp_hard_constraints as hc          # noqa: E402
from csp_solver import MealPlanRequest, MenuItem, build_and_solve  # noqa: E402

MEALS = ("아침", "점심", "저녁")


def pool(n_per_cat=12, high_na=900.0, low_na=50.0):
    """카테고리별 후보. 인덱스 짝수는 고나트륨, 홀수는 저나트륨으로 둔다.

    열량 폭·부찬 2배 규칙은 test_menu_repeat_and_meal_ratio.pool 과 동일 이유
    (좁으면 제약이 아니라 픽스처 때문에 INFEASIBLE 이 된다).

    Returns:
        (menus, sodium_by_idx) — sodium 은 side-channel 로 주입한다(MenuItem 필드 아님).
    """
    spec = (("주식", 150.0, 400.0, 1), ("국", 20.0, 120.0, 1),
            ("주찬", 60.0, 250.0, 1), ("부찬", 20.0, 150.0, 2),
            ("김치", 5.0, 30.0, 1))
    out, sodium, mid = [], {}, 1
    for cat, lo, hi, mult in spec:
        count = n_per_cat * mult
        step = (hi - lo) / max(1, count - 1)
        for i in range(count):
            sodium[len(out)] = high_na if i % 2 == 0 else low_na
            out.append(MenuItem(mid, f"{cat}{i}", cat, round(lo + i * step, 1),
                                cost_won=200.0))
            mid += 1
    return out, sodium


def solve(days=2, cap=None, sodium=None, menus=None, time_limit=30.0):
    """상한 유무만 다르게 두고 푼다."""
    cfg = hc.HardConstraintConfig(
        target_kcal_per_day=2000.0,
        budget_limit_per_person=None,
        nutrient_max_per_day=({"sodium": cap} if cap is not None else {}),
    )
    return build_and_solve(menus, MealPlanRequest(
        days=days, meals=MEALS, hard=cfg, solver_time_limit=time_limit,
        hard_nutrient_by_idx=({"sodium": sodium} if sodium is not None else None)))


def day_sodium(res, menus, sodium):
    """해에서 일별 나트륨 합계를 재계산한다(리포트와 독립 경로로 교차 확인)."""
    by_name = {m.name: i for i, m in enumerate(menus)}
    out = {}
    for day, meals in res.plan.items():
        out[day] = sum(sodium[by_name[n]] for picks in meals.values() for n in picks)
    return out


# ---------------------------------------------------------------------------
# H-2e 상한이 실제로 걸리는가
# ---------------------------------------------------------------------------
def test_without_cap_sodium_can_exceed():
    """음성 대조 — 상한이 없으면 나트륨이 2,000mg 를 넘는 해가 나온다.

    이게 실패하면 아래 양성 테스트가 '원래 안 넘는 픽스처' 위에서 통과하는 셈이라
    제약을 검증하지 못한다.
    """
    menus, sodium = pool()
    res = solve(cap=None, sodium=None, menus=menus)
    assert res.status in ("OPTIMAL", "FEASIBLE")
    assert max(day_sodium(res, menus, sodium).values()) > 2000.0


def test_cap_is_enforced():
    """상한을 켜면 모든 날의 총 나트륨이 상한 이내다."""
    menus, sodium = pool()
    res = solve(cap=2000.0, sodium=sodium, menus=menus)
    assert res.status in ("OPTIMAL", "FEASIBLE")
    assert max(day_sodium(res, menus, sodium).values()) <= 2000.0


def test_report_matches_solution():
    """리포트(_nutrient_max_report)가 해와 같은 수를 본다 — 절단·이중조회 오보 방지."""
    menus, sodium = pool()
    res = solve(cap=2000.0, sodium=sodium, menus=menus)
    info = res.hard_breakdown["nutrient_max"]["sodium"]
    actual = day_sodium(res, menus, sodium)
    assert info["limit"] == 2000.0
    assert info["all_ok"] is True
    for row in info["per_day"]:
        assert row["amount"] == round(actual[row["day"]], 1)
        assert row["ok"] is True


def test_infeasible_cap_reports_no_solution():
    """물리적으로 불가능한 상한(하루 최소 나트륨 미만)은 해 없음으로 정직하게 끝난다.

    조용히 완화하거나 상한을 무시하고 푸는 일이 없어야 한다.
    """
    menus, sodium = pool(high_na=900.0, low_na=800.0)   # 12접시 최소 9,600mg
    res = solve(cap=1000.0, sodium=sodium, menus=menus)
    assert res.status == "INFEASIBLE"


# ---------------------------------------------------------------------------
# 결측 처리 — 상한에서 "모름"을 0으로 뭉개면 제약이 무력화된다
# ---------------------------------------------------------------------------
def test_missing_value_menu_is_excluded_not_treated_as_zero():
    """나트륨 값이 없는 메뉴는 편성에서 배제된다(기본 정책 exclude).

    값을 지우는 대상은 **고나트륨 메뉴**(각 카테고리 local index 0)로 고른다.
    미상을 0으로 뭉개면 이들이 '나트륨 0'이 되어 상한 아래서 오히려 매력적인 후보가
    되므로, 배제가 실제로 작동해야만 이 테스트가 통과한다.

    픽스처 주의: 저나트륨 메뉴를 많이 지우면 2일치 최소 나트륨이 상한을 넘어
    제약이 아니라 픽스처 때문에 INFEASIBLE 이 된다(카테고리별 필요 개수 × 최소 나트륨으로 검산).
    """
    menus, sodium = pool()
    missing = {0, 12, 24}                              # 주식0·국0·주찬0 = 각 900mg
    partial = {i: v for i, v in sodium.items() if i not in missing}
    res = solve(cap=2000.0, sodium=partial, menus=menus)
    assert res.status in ("OPTIMAL", "FEASIBLE")
    placed = {n for meals in res.plan.values() for picks in meals.values() for n in picks}
    assert not (placed & {menus[i].name for i in missing})


def test_missing_policy_zero_allows_menu():
    """정책을 'zero' 로 바꾸면 값 없는 메뉴도 편성 가능(데이터 완전성 확인된 경우만)."""
    menus, sodium = pool()
    cfg = hc.HardConstraintConfig(
        target_kcal_per_day=2000.0, budget_limit_per_person=None,
        nutrient_max_per_day={"sodium": 2000.0}, nutrient_max_missing="zero")
    res = build_and_solve(menus, MealPlanRequest(
        days=2, meals=MEALS, hard=cfg, solver_time_limit=30.0,
        hard_nutrient_by_idx={"sodium": {}}))          # 전량 결측
    assert res.status in ("OPTIMAL", "FEASIBLE")
    assert res.hard_breakdown["nutrient_max"]["sodium"]["max_day"] == 0.0


# ---------------------------------------------------------------------------
# 기본값은 OFF — 기존 호출부·픽스처 동작 불변
# ---------------------------------------------------------------------------
def test_default_config_does_not_activate():
    """기본 설정에서는 H-2e 가 비활성이라 기존 식단 생성이 그대로 동작한다."""
    menus, _ = pool()
    res = solve(cap=None, sodium=None, menus=menus)
    assert res.status in ("OPTIMAL", "FEASIBLE")
    assert res.hard_breakdown["active_terms"]["nutrient_max"] is False
    assert res.hard_breakdown["nutrient_max"] == {}


# ---------------------------------------------------------------------------
# 대체식(알레르기 트랙)도 상한을 지켜야 한다
# ---------------------------------------------------------------------------
def _alt_menus():
    """대체식용 소형 픽스처 — 원본은 저나트륨, 대체 후보는 고/저 두 종."""
    return [
        MenuItem(1, "밥", "주식", 300.0),
        MenuItem(2, "계란국", "국", 100.0, allergens={"난류"}),
        MenuItem(3, "고염국", "국", 100.0),
        MenuItem(4, "저염국", "국", 100.0),
    ]


def test_alternative_respects_sodium_cap():
    """교체 후 그날 나트륨이 상한을 넘는 후보는 고르지 않는다."""
    menus = _alt_menus()
    sodium_by_id = {1: 300.0, 2: 200.0, 3: 1900.0, 4: 100.0}
    cfg = hc.HardConstraintConfig(enable_energy=False, budget_limit_per_person=None,
                                  nutrient_max_per_day={"sodium": 1000.0})
    alts = am.derive_alternative_menus(
        {1: {"점심": ["밥", "계란국"]}}, menus,
        [am.AllergyGroup(label="난류", allergens={"난류"})],
        hard_config=cfg, sodium_by_id=sodium_by_id)
    subs = alts[0].substitutions
    assert [s.alternative for s in subs] == ["저염국"]   # 고염국(1900)은 상한 초과로 탈락


def test_alternative_unresolved_when_sodium_unknown():
    """나트륨을 모르는 후보뿐이면 교체하지 않고 unresolved 로 남긴다(임의 통과 금지)."""
    menus = _alt_menus()
    cfg = hc.HardConstraintConfig(enable_energy=False, budget_limit_per_person=None,
                                  nutrient_max_per_day={"sodium": 1000.0})
    alts = am.derive_alternative_menus(
        {1: {"점심": ["밥", "계란국"]}}, menus,
        [am.AllergyGroup(label="난류", allergens={"난류"})],
        hard_config=cfg, sodium_by_id=None)             # 나트륨 정보 없음
    assert alts[0].substitutions == []
    assert len(alts[0].unresolved) == 1
