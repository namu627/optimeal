# -*- coding: utf-8 -*-
"""주재료 축(B6 Phase 1) 검증 — 적재된 recipe_ingredient_map 을 어울림/다양성 축으로 쓴다.

배경: `main_by_idx` 통로는 진작 있었지만 `recipe_ingredient_map` 이 0행이라 항이
계속 자동 비활성이었다. 2026-08-10 적재(12,503행)로 실데이터가 생겨 활성화한다.
CSP 후보 960종 중 **577종(60.1%)** 만 주재료를 얻는다 — 나머지는 중립.

여기서 지켜야 할 성질 네 가지:
  (1) **대표 주재료가 결정론적이다** — 한 메뉴에 주재료가 여럿일 때(151종) 양이 가장
      많은 재료를 고른다. ORDER BY 없이 "첫 행"을 쓰면 실행계획이 대표값을 바꾼다.
  (2) **항이 실제로 일한다** — 끄면 같은 주재료가 cap 을 넘어 반복된다(음성 대조).
  (3) **리포트가 제약이 쓴 값을 그대로 읽는다** — side-channel 주입분이 표에서
      사라지면 안 된다(2026-08-11 나트륨 표 불일치와 같은 계열).
  (4) **미상 메뉴는 중립** — 주재료를 못 얻은 메뉴가 감점을 받지 않는다.

실행:  pytest module_3/tests/test_main_ingredient_axis.py -v
"""
import os
import sys
from collections import Counter

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

pytest.importorskip("ortools", reason="ortools 미설치")
from ortools.sat.python import cp_model  # noqa: E402

import csp_hard_constraints as hc                      # noqa: E402
import csp_warm_start as ws                            # noqa: E402
import soft_constraints_diversity as scd               # noqa: E402
from csp_solver import (DEFAULT_COMPOSITION, MealPlanRequest, MenuItem,  # noqa: E402
                        _count_bounds, build_and_solve)

MEALS = ("아침", "점심", "저녁")
COMPOSITION = {c: _count_bounds(v) for c, v in DEFAULT_COMPOSITION.items()}


# --------------------------------------------------------------------------- #
# (A) 대표 주재료 조회 — DB 없이 가짜 커넥션으로 SQL 계약만 본다                 #
# --------------------------------------------------------------------------- #
class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self._rows


class _FakeConn:
    """execute 로 들어온 SQL 을 붙잡아 두고 정해진 행을 돌려주는 최소 스텁."""

    def __init__(self, rows, sink):
        self._rows = rows
        self._sink = sink

    def execute(self, stmt, params=None):
        self._sink.append(str(stmt))
        return _FakeResult(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self, rows):
        self.rows = rows
        self.sql: list = []

    def connect(self):
        return _FakeConn(self.rows, self.sql)


def _menus(n=3):
    return [MenuItem(100 + i, f"메뉴{i}", "부찬", 50.0) for i in range(n)]


def test_load_main_ingredients_maps_menu_id_to_index():
    """menu_id 로 조회한 결과가 메뉴 '인덱스' 키로 돌아온다(주입 규약)."""
    menus = _menus(3)
    eng = _FakeEngine([{"menu_id": 101, "main": "두부"}, {"menu_id": 102, "main": "연어"}])
    got = scd.load_main_ingredients(eng, menus)
    assert got == {1: "두부", 2: "연어"}      # 100→0, 101→1, 102→2
    assert 0 not in got                        # 조회 안 된 메뉴는 키 자체가 없다


def test_load_main_ingredients_orders_by_amount_deterministically():
    """대표 선정이 양 기준으로 정렬돼야 한다 — 재현성(NFR-05) 요건.

    ORDER BY 가 빠지면 어느 주재료가 대표가 될지 실행계획에 좌우된다. 오늘 우연히
    같은 값이 나오더라도 보장이 아니므로, SQL 수준에서 고정됐는지 본다.
    """
    eng = _FakeEngine([])
    scd.load_main_ingredients(eng, _menus(1))
    sql = " ".join(eng.sql[0].split())
    assert "DISTINCT ON (nr.nutrition_id)" in sql
    assert "ORDER BY nr.nutrition_id, rim.per_serving_grams DESC NULLS LAST" in sql
    assert "i.ingredient_name" in sql          # 동량일 때의 결정론적 tie-break


def test_load_main_ingredients_no_menu_ids_returns_empty():
    """menu_id 가 없는 메뉴만 있으면 조회 없이 빈 dict(우아한 저하)."""
    menus = [MenuItem(None, "이름만있는메뉴", "부찬", 50.0)]
    eng = _FakeEngine([{"menu_id": 1, "main": "두부"}])
    assert scd.load_main_ingredients(eng, menus) == {}
    assert eng.sql == []                       # 쿼리 자체를 날리지 않는다


# --------------------------------------------------------------------------- #
# (B) 목적함수 — 주입값이 실제로 감점을 만드는가                                 #
# --------------------------------------------------------------------------- #
def _slot_model(menus, *, days, n_meals=1, main_by_idx=None, per_slot=1):
    """슬롯마다 per_slot 개를 고르는 최소 모델(다른 항의 간섭 없이 주재료만 본다)."""
    model = cp_model.CpModel()
    Mr, D, S = range(len(menus)), range(days), range(n_meals)
    x = {(m, d, s): model.NewBoolVar(f"x_{m}_{d}_{s}") for m in Mr for d in D for s in S}
    for d in D:
        for s in S:
            model.Add(sum(x[m, d, s] for m in Mr) == per_slot)
    soft = scd.add_diversity_soft_objective(
        model, x, menus, days=days, n_meals=n_meals, main_by_idx=main_by_idx)
    model.Maximize(soft.score)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 5.0
    solver.parameters.random_seed = 42
    solver.Solve(model)
    return solver, x, soft


def test_uncovered_menus_are_neutral():
    """주재료 미상 메뉴는 감점을 받지 않는다 — 커버리지 60%의 전제."""
    menus = [MenuItem(1, "미상A", "부찬", 50.0), MenuItem(2, "미상B", "부찬", 50.0)]
    solver, _x, soft = _slot_model(menus, days=7, main_by_idx={})   # 빈 주입
    assert soft.active_terms["main"] is False
    assert soft.main_excess_vars == {}


def test_partial_coverage_penalizes_only_labeled_menus():
    """일부만 주재료가 있으면 그 라벨만 감점 대상이 된다."""
    menus = [MenuItem(i + 1, f"메뉴{i}", "부찬", 50.0) for i in range(2)]
    solver, _x, soft = _slot_model(menus, days=7, main_by_idx={0: "두부"})
    assert set(soft.main_excess_vars) == {"두부"}
    # 미상 메뉴(1번)를 골라 감점을 피하는 쪽이 최적 — 커버리지 비대칭의 실체.
    assert solver.Value(soft.main_excess_vars["두부"]) == 0


def test_cap_scales_with_horizon():
    """cap 은 주당 기준이라 지평이 길어지면 함께 늘어난다(2회/주 → 31일 9회)."""
    assert scd.scale_targets(2.0, 7) == 2
    assert scd.scale_targets(2.0, 14) == 4
    assert scd.scale_targets(2.0, 31) == 9


def test_same_main_over_cap_is_penalized():
    """대안이 없으면 초과분이 실제 감점으로 잡힌다."""
    menus = [MenuItem(1, "두부조림", "부찬", 50.0), MenuItem(2, "두부부침", "부찬", 50.0)]
    solver, _x, soft = _slot_model(
        menus, days=7, main_by_idx={0: "두부", 1: "두부"})
    assert soft.active_terms["main"] is True
    # 7일 × 1끼 = 7접시, cap 2 → 초과 5
    assert solver.Value(soft.main_excess_vars["두부"]) == 5


def test_negative_control_without_axis_the_same_main_repeats():
    """★음성 대조 — 항을 끄면 같은 주재료가 cap 을 넘어 실제로 반복된다.

    이걸 확인하지 않으면 "원래 안 나오는 픽스처 위에서 통과하는" 가짜 테스트가 된다.
    두부 메뉴에만 제철 가점을 줘서 **끄면 두부로 쏠릴 이유**를 만든다 — 그래야
    "안 쏠렸다"가 항의 성과인지 우연인지 갈린다.

    라벨 5종 × cap 2 = 10 ≥ 7접시라 ON 은 초과 없이 성립할 수 있어야 한다.
    (라벨 수가 모자라면 초과가 불가피해져 항의 잘못이 아닌 실패가 난다.)
    """
    # 제철 가점은 **작게** 준다(0.05×season_scale 20 = 1점/접시). 크게 주면 가점이
    # 주재료 감점(6점/초과)을 눌러, 항이 정상인데도 쏠림이 남는다.
    names = [("두부조림", "두부", 0.05), ("두부부침", "두부", 0.05), ("가지무침", "가지", 0.0),
             ("감자조림", "감자", 0.0), ("무생채", "무", 0.0), ("오이무침", "오이", 0.0)]
    menus = [MenuItem(i + 1, n, "부찬", 50.0, season_score=s)
             for i, (n, _lab, s) in enumerate(names)]
    labels = {i: lab for i, (_n, lab, _s) in enumerate(names)}

    def _counts(main_by_idx):
        solver, x, _soft = _slot_model(menus, days=7, main_by_idx=main_by_idx)
        c = Counter()
        for m in range(len(menus)):
            for d in range(7):
                if solver.Value(x[m, d, 0]):
                    c[labels[m]] += 1
        return c

    off = _counts(None)          # 감점이 없으니 제철 가점을 좇아 두부만 7번
    on = _counts(labels)         # cap 2 를 넘지 않는 선에서 최선
    assert off["두부"] == 7, f"픽스처가 쏠림을 못 만들었다: {off}"
    assert max(on.values()) <= 2, f"ON 인데 초과: {on}"
    assert sum(on.values()) == 7
    assert max(off.values()) > max(on.values()), f"OFF={off} ON={on} — 항이 무의미"


# --------------------------------------------------------------------------- #
# (C) 리포트 정합 — 제약이 쓴 라벨을 그대로 보여주는가                            #
# --------------------------------------------------------------------------- #
def test_breakdown_reports_injected_labels():
    """side-channel 로만 준 주재료가 리포트 표에 나온다.

    이전 구현은 MenuItem.main_ingredient 를 다시 읽어, 주입 경로로 제약이 걸려도
    표는 빈 채였다.
    """
    menus = [MenuItem(1, "두부조림", "부찬", 50.0), MenuItem(2, "가지무침", "부찬", 50.0)]
    solver, x, soft = _slot_model(menus, days=2, main_by_idx={0: "두부", 1: "가지"})
    rep = scd.evaluate_diversity_breakdown(solver, x, menus, soft, days=2, n_meals=1)
    counts = rep["main_ingredient_counts"]
    assert counts, "주입 라벨이 리포트에서 사라졌다"
    assert set(counts) <= {"두부", "가지"}
    assert sum(counts.values()) == 2               # 2일 × 1끼 = 2접시
    assert rep["main_ingredient_coverage"] == 2    # 라벨을 얻은 메뉴 수


def test_breakdown_falls_back_to_menu_attribute():
    """주입 없이 MenuItem 속성만 있는 구 호출부도 계속 동작한다."""
    menus = [MenuItem(1, "메뉴A", "부찬", 50.0)]
    menus[0].main_ingredient = "돼지고기"          # 속성 경로(구 규약)
    solver, x, soft = _slot_model(menus, days=1, main_by_idx=None)
    rep = scd.evaluate_diversity_breakdown(solver, x, menus, soft, days=1, n_meals=1)
    assert rep["main_ingredient_counts"] == {"돼지고기": 1}


# --------------------------------------------------------------------------- #
# (D) 웜스타트 원장 — 힌트도 같은 cap 을 지키는가                                 #
# --------------------------------------------------------------------------- #
def _pool(per_cat=12, sub_side=36):
    """반상 구성을 여러 날 채울 수 있는 후보 풀(test_warm_start 와 같은 규격)."""
    spec = (("주식", "잡곡밥", 300.0, per_cat), ("국", "배추된장국", 90.0, per_cat),
            ("주찬", "고등어구이", 150.0, per_cat), ("부찬", "시금치나물", 40.0, sub_side),
            ("김치", "배추김치", 12.0, per_cat))
    out, mid = [], 1
    for cat, base, kcal, count in spec:
        for i in range(count):
            out.append(MenuItem(mid, f"{base}{i}", cat, kcal + i))
            mid += 1
    return out


def _config(**kw):
    base = dict(target_kcal_per_day=1800.0, kcal_tolerance=0.35,
                enable_meal_ratio=False, budget_limit_per_person=None,
                enable_staple_main=False, enable_menu_pairing=False,
                menu_repeat_window_days=0)
    base.update(kw)
    return hc.HardConstraintConfig(**base)


def test_hint_ledger_respects_main_cap():
    """힌트가 지평 cap 을 넘겨 같은 주재료를 반복하지 않는다.

    cap 은 **지평 전체** 규칙이라 하루 목적(_STEER)으로는 표현할 수 없다. 원장으로
    순차 배제하지 않으면 힌트가 감점 0에 못 닿아 솔버가 다시 개선 탐색을 한다.
    """
    menus = _pool()
    # 부찬 36종 중 앞 12종을 같은 주재료로 묶는다 — 원장이 없으면 자연히 초과한다.
    sub_side = [i for i, m in enumerate(menus) if m.category == "부찬"]
    main_by_idx = {i: "두부" for i in sub_side[:12]}
    hint = ws.build_rolling_hint(
        menus, days=5, n_meals=len(MEALS), composition=COMPOSITION,
        config=_config(), main_by_idx=main_by_idx, main_cap=3)
    used = sum(1 for (m, _d, _s) in hint if main_by_idx.get(m) == "두부")
    assert used <= 3, f"힌트가 cap 3 을 넘겨 두부를 {used}회 썼다"


def test_hint_ledger_off_without_cap():
    """main_cap 없이 부르면 원장은 꺼지고 8/13 동작 그대로다(회귀 방지)."""
    menus = _pool()
    base = ws.build_rolling_hint(menus, days=3, n_meals=len(MEALS),
                                 composition=COMPOSITION, config=_config())
    with_axis = ws.build_rolling_hint(menus, days=3, n_meals=len(MEALS),
                                      composition=COMPOSITION, config=_config(),
                                      main_by_idx={0: "두부"}, main_cap=None)
    assert base == with_axis


def test_hint_ledger_does_not_break_composition():
    """원장으로 후보를 빼도 끼니 구성은 그대로 성립한다(과배제 방지)."""
    menus = _pool()
    main_by_idx = {i: f"재료{i % 4}" for i in range(len(menus))}
    hint = ws.build_rolling_hint(
        menus, days=3, n_meals=len(MEALS), composition=COMPOSITION,
        config=_config(), main_by_idx=main_by_idx, main_cap=2)
    # 원장이 너무 조여 하루도 못 만들면 빈 힌트가 되는데, 그건 저하일 뿐 오류는 아니다.
    # 다만 만들어진 날은 구성을 지켜야 한다.
    made_days = {d for (_m, d, _s) in hint}
    for d in made_days:
        for s in range(len(MEALS)):
            got = [menus[m].category for (m, dd, ss) in hint if dd == d and ss == s]
            for cat, (lo, hi) in COMPOSITION.items():
                assert lo <= got.count(cat) <= (hi if hi is not None else 99)


# --------------------------------------------------------------------------- #
# (E) 골조 배선 — MealPlanRequest 로 끝까지 흐르는가                             #
# --------------------------------------------------------------------------- #
def test_request_main_by_idx_reaches_objective():
    """MealPlanRequest.main_by_idx 가 목적함수까지 전달된다."""
    menus = _pool()
    main_by_idx = {i: "두부" for i, m in enumerate(menus) if m.category == "부찬"}
    req = MealPlanRequest(days=2, meals=MEALS, hard=_config(),
                          solver_time_limit=15.0, main_by_idx=main_by_idx)
    res = build_and_solve(menus, req)
    assert res.status in ("OPTIMAL", "FEASIBLE")
    assert res.diversity_breakdown["active_terms"]["main"] is True
    assert res.diversity_breakdown["main_ingredient_coverage"] == len(main_by_idx)


def test_request_without_main_by_idx_is_inactive():
    """주입이 없으면 항이 비활성 — 8/13 이전 동작과 동일(우아한 저하)."""
    menus = _pool()
    req = MealPlanRequest(days=2, meals=MEALS, hard=_config(), solver_time_limit=15.0)
    res = build_and_solve(menus, req)
    assert res.status in ("OPTIMAL", "FEASIBLE")
    assert res.diversity_breakdown["active_terms"]["main"] is False
