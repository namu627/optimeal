# -*- coding: utf-8 -*-
"""롤링 웜스타트(초기해 hint) 검증 — B7 31일 SLA 튜닝.

배경(2026-08-13 계측): 31일 풀이의 병목은 **첫 가능해 찾기**였다. 목적함수를 통째로
제거해도 12.6~65.5초였고, 솔버 파라미터(symmetry·linearization·probing·workers·
feasibility_jump)는 전부 노이즈 범위였다. 원인은 나트륨 일 상한이 558개 슬롯을 전역
결합시키는 것. → 하루씩 순차 구성한 초기해를 넣어 24.8~60초초과 → 10.0~10.2초로 고정.

여기서 지켜야 할 성질은 두 가지다.
  (1) **힌트는 해의 의미를 바꾸지 않는다** — 켜든 끄든 같은 상태·같은 목적값.
  (2) **못 만들면 조용히 물러난다** — 부분 문제가 안 풀려도 전체 풀이는 그대로 된다.
"""
import os
import sys

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

import csp_hard_constraints as hc            # noqa: E402
import csp_warm_start as ws                  # noqa: E402
from csp_solver import (DEFAULT_COMPOSITION, MealPlanRequest, MenuItem,  # noqa: E402
                        _count_bounds, build_and_solve)

MEALS = ("아침", "점심", "저녁")
COMPOSITION = {c: _count_bounds(v) for c, v in DEFAULT_COMPOSITION.items()}


def pool(per_cat=12, sub_side=36):
    """반상 구성을 여러 날 채울 수 있는 후보 풀.

    개수 산정(중복 창 3일을 켜는 테스트 기준): 창 안의 슬롯 수만큼 서로 다른 메뉴가
    필요하다 — 3일 × 3끼 = 주식·국·주찬·김치 각 9종, 부찬은 2~3개씩이라 최대 27종.
    풀이 이보다 작으면 코드가 아니라 픽스처 때문에 INFEASIBLE 이 난다.
    """
    spec = (("주식", "잡곡밥", 300.0, per_cat), ("국", "배추된장국", 90.0, per_cat),
            ("주찬", "고등어구이", 150.0, per_cat), ("부찬", "시금치나물", 40.0, sub_side),
            ("김치", "배추김치", 12.0, per_cat))
    out, mid = [], 1
    for cat, base, kcal, count in spec:
        for i in range(count):
            out.append(MenuItem(mid, f"{base}{i}", cat, kcal + i))
            mid += 1
    return out


def sodium_of(menus):
    """나트륨은 MenuItem 필드가 아니라 side-channel 로 넘어간다(H-2e 규약).

    절반은 저나트륨(50), 절반은 고나트륨(200) — 상한이 실제로 선택을 가르게 한다.
    """
    return {i: (50.0 if i % 2 == 0 else 200.0) for i in range(len(menus))}


def config(**kw):
    """관심사 외 제약은 꺼서 격리한다.

    끼니 배분·주식자격·궁합은 메뉴명에 의존하므로 mock 이름에서 켜면 전멸하고,
    중복 창은 풀 크기를 좌우하므로 그것을 보는 테스트에서만 켠다.
    """
    base = dict(target_kcal_per_day=1800.0, kcal_tolerance=0.35,
                enable_meal_ratio=False, budget_limit_per_person=None,
                enable_staple_main=False, enable_menu_pairing=False,
                menu_repeat_window_days=0)
    base.update(kw)
    return hc.HardConstraintConfig(**base)


def make_hint(menus, cfg, days=5, **kw):
    return ws.build_rolling_hint(menus, days=days, n_meals=len(MEALS),
                                 composition=COMPOSITION, config=cfg, **kw)


# ---------------------------------------------------------------------------
# 힌트 구성 — 무엇을 만들어 내는가
# ---------------------------------------------------------------------------
def test_hint_covers_every_day_and_meal():
    """모든 (일, 끼니)에 구성 개수만큼의 접시가 배정된다."""
    hint = make_hint(pool(), config())
    for d in range(5):
        for s in range(len(MEALS)):
            picked = [m for (m, dd, ss) in hint if dd == d and ss == s]
            assert len(picked) in (6, 7), f"{d}일 {s}끼니 접시 {len(picked)}개"


def test_hint_respects_composition_per_meal():
    """끼니마다 주식1·국1·주찬1·부찬2~3·김치1을 지킨다."""
    menus = pool()
    hint = make_hint(menus, config())
    for d in range(5):
        for s in range(len(MEALS)):
            got = [menus[m].category for (m, dd, ss) in hint if dd == d and ss == s]
            for cat, (lo, hi) in COMPOSITION.items():
                assert lo <= got.count(cat) <= (hi if hi is not None else 99)


def test_hint_respects_repeat_window():
    """창(3일) 안에서 같은 메뉴가 두 번 나오지 않는다 — 지평을 가로지르는 유일한 제약."""
    menus = pool()
    hint = make_hint(menus, config(menu_repeat_window_days=3), days=6)
    days_of = {}
    for (m, d, _s) in hint:
        days_of.setdefault(m, []).append(d)
    for m, ds in days_of.items():
        ds.sort()
        # 같은 날 중복(간격 0)도 위반이다 — 창이 하루의 모든 끼니를 포함한다.
        for a, b in zip(ds, ds[1:]):
            assert b - a >= 3, f"{menus[m].name}: {a + 1}일과 {b + 1}일에 중복"


def test_hint_respects_daily_kcal_band():
    """하루 총 열량이 Hard 밴드 안에 든다(부분 문제가 같은 config 를 쓰는지)."""
    menus = pool()
    cfg = config()
    lo = cfg.target_kcal_per_day * (1 - cfg.kcal_tolerance)
    hi = cfg.target_kcal_per_day * (1 + cfg.kcal_tolerance)
    hint = make_hint(menus, cfg)
    for d in range(5):
        kcal = sum(menus[m].calories for (m, dd, _s) in hint if dd == d)
        assert lo <= kcal <= hi, f"{d+1}일 {kcal}kcal (밴드 {lo}~{hi})"


def test_hint_respects_nutrient_max():
    """H-2e 상한을 부분 문제도 지킨다 — side-channel 인덱스 재매핑이 맞는지 확인.

    상한 근거: 하루 18~21접시. 전부 저나트륨(50)이면 900~1,050, 전부 고나트륨(200)이면
    3,600~4,200. 1,300으로 걸면 대부분을 저나트륨에서 골라야 성립한다(= 실제로 구속).
    """
    menus = pool()
    sodium = sodium_of(menus)
    cap = 1300.0
    hint = make_hint(menus, config(nutrient_max_per_day={"sodium": cap}),
                     nutrient_by_idx={"sodium": sodium})
    assert hint, "상한이 성립 가능한데 힌트를 못 만들었다"
    for d in range(5):
        total = sum(sodium[m] for (m, dd, _s) in hint if dd == d)
        assert total <= cap, f"{d + 1}일 나트륨 {total} > {cap}"


def test_hint_nutrient_max_is_binding():
    """음성 대조 — 상한을 빼면 실제로 그 값을 넘는 하루가 생긴다.

    이게 없으면 '원래 안 넘는 픽스처'에서 통과하는 가짜 테스트가 된다.
    """
    menus = pool()
    sodium = sodium_of(menus)
    hint = make_hint(menus, config())            # 상한 없음
    worst = max(sum(sodium[m] for (m, dd, _s) in hint if dd == d) for d in range(5))
    assert worst > 1300.0, f"상한 없이도 최대 {worst} — 픽스처가 구속력이 없다"


# ---------------------------------------------------------------------------
# 우아한 저하 — 못 만들 때
# ---------------------------------------------------------------------------
def test_hint_empty_when_day_infeasible():
    """하루가 안 풀리면 빈 힌트를 돌려준다(예외 없이)."""
    assert make_hint(pool(), config(target_kcal_per_day=99999.0,
                                    kcal_tolerance=0.01)) == {}


def test_hint_empty_for_degenerate_input():
    """후보가 없거나 지평이 0이면 빈 힌트."""
    assert make_hint([], config()) == {}
    assert make_hint(pool(), config(), days=0) == {}


def test_time_budget_stops_construction():
    """예산을 넘기면 만든 날짜까지만 넘긴다 — 힌트 때문에 SLA 를 되레 넘기지 않도록."""
    assert make_hint(pool(), config(), days=8, time_budget=0.0) == {}
    full = make_hint(pool(), config(), days=8)
    assert len({d for (_m, d, _s) in full}) == 8, "예산이 넉넉하면 전 기간을 만든다"


def test_apply_hint_ignores_empty():
    """빈 힌트는 모델을 건드리지 않는다."""
    from ortools.sat.python import cp_model
    model = cp_model.CpModel()
    x = {(0, 0, 0): model.NewBoolVar("x")}
    assert ws.apply_hint(model, x, {}, days=1, n_meals=1) == 0


def test_apply_hint_only_touches_built_days():
    """부분 힌트는 구성된 날짜의 변수에만 값을 준다 — 나머지는 솔버 자유."""
    from ortools.sat.python import cp_model
    model = cp_model.CpModel()
    x = {(m, d, 0): model.NewBoolVar(f"x{m}{d}") for m in range(2) for d in range(3)}
    n = ws.apply_hint(model, x, {(0, 1, 0): 1}, days=3, n_meals=1)
    assert n == 2, "1일차 변수 2개만 힌트 대상"


# ---------------------------------------------------------------------------
# 통합 — 해의 의미가 바뀌지 않는가 (핵심 성질)
# ---------------------------------------------------------------------------
def solve(menus, *, warm, days=5, cfg=None):
    return build_and_solve(menus, MealPlanRequest(
        days=days, meals=MEALS, hard=cfg or config(),
        solver_time_limit=30.0, warm_start=warm))


def test_warm_start_preserves_status_and_objective():
    """켜든 끄든 같은 상태·같은 목적값이어야 한다(힌트는 탐색 보조일 뿐)."""
    menus = pool()
    on, off = solve(menus, warm=True), solve(menus, warm=False)
    assert on.status == off.status == "OPTIMAL"
    assert on.objective == off.objective


def test_warm_start_plan_satisfies_hard():
    """웜스타트로 얻은 식단도 Hard 밴드를 지킨다(힌트가 해를 오염시키지 않음)."""
    res = solve(pool(), warm=True)
    assert all(d["kcal_ok"] for d in res.hard_breakdown["per_day"])


def test_warm_start_does_not_mask_infeasible():
    """가능해가 없으면 웜스타트가 있어도 INFEASIBLE 을 정직하게 돌려준다."""
    cfg = config(target_kcal_per_day=99999.0, kcal_tolerance=0.01)
    assert solve(pool(), warm=True, cfg=cfg).status == "INFEASIBLE"


def test_warm_start_skipped_without_hard_config():
    """hard=None 이면 힌트를 만들지 않는다 — 모델에 없는 제약을 지키느라 시간 낭비 금지."""
    res = build_and_solve(pool(), MealPlanRequest(
        days=3, meals=MEALS, hard=None, solver_time_limit=30.0, warm_start=True))
    assert res.warm_start_seconds == 0.0


def test_wall_time_includes_warm_start():
    """보고 시간에 초기해 구성 시간이 포함된다 — SLA 를 과소 보고하면 안 된다."""
    res = solve(pool(), warm=True)
    assert res.warm_start_seconds > 0.0
    assert res.wall_time >= res.warm_start_seconds


@pytest.mark.parametrize("warm", [True, False])
def test_default_request_enables_warm_start(warm):
    """기본값은 ON — 운영 경로가 별도 설정 없이 혜택을 받는다."""
    assert MealPlanRequest().warm_start is True
    assert MealPlanRequest(warm_start=warm).warm_start is warm
