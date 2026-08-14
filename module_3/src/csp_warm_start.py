# -*- coding: utf-8 -*-
"""OptiMeal 모듈3 · 롤링 웜스타트 — 전체 모델에 넣을 초기해(hint) 구성

31일 SLA(NFR-02 <60초) 미달을 해소하기 위한 탐색 보조 모듈.

왜 필요한가 (2026-08-13 계측):
  · 31일 풀이는 **첫 가능해를 찾는 데** 전 시간을 쓴다. 목적함수를 통째로 제거해도
    12.6~65.5초로 오히려 더 느릴 때가 있었다 → 병목은 최적화가 아니라 가능해 탐색.
  · 원인은 나트륨 일 상한(H-2e)이다. 하루 18접시를 메뉴 중앙값(197mg)으로 채우면
    3,548mg 으로 상한 2,000mg 의 1.8배다. 즉 558개 슬롯 전부를 저나트륨 꼬리에서
    골라야 하는 전역 결합 문제라 CP-SAT 가 무작정 헤맨다.
  · 솔버 파라미터(symmetry_level·linearization_level·probing_level·workers·
    feasibility_jump)는 전부 무효였다 — 3회 반복 측정에서 차이가 노이즈 범위.

무엇을 하는가:
  하루(3끼)짜리 부분 문제를 **순차로** 풀어 31일치 배치를 만들고, 이를 전체 모델의
  `AddHint` 로 넣는다. 전체 모델 자체는 그대로 풀리므로 **가능영역·최적성의 의미가
  바뀌지 않는다** — 힌트는 탐색 출발점일 뿐이고, 틀린 힌트는 솔버가 그냥 버린다.

왜 하루 단위로 쪼개도 되는가:
  Hard 제약이 대부분 **일 단위**다(H-2a 에너지·H-2a' 끼니배분·H-2e 상한·예산 day·
  H-4b·H-4c·끼니 구성). 지평을 가로지르는 것은 메뉴 중복 창뿐이라, 직전 (창-1)일에
  쓴 메뉴를 후보에서 빼는 것으로 정확히 재현된다. 창 단위 비율 제약(H-2b·H-2c)은
  하루 창으로 더 **빡빡하게** 걸리므로 만들어진 해는 전체 모델에서도 유효하다.

비용을 줄이는 방법:
  하루 부분 문제의 풀이 시간은 후보 수에 좌우된다(960종 0.45초 / 250종 0.05초).
  힌트는 출발점이라 전 후보가 필요 없으므로 **카테고리 비례 표본**으로 푼다.
  표본으로 실패하면 그 하루만 전 후보로 재시도한다.

기준 문서: NFR-02, FR-11. 규약은 `csp_hard_constraints`·`soft_constraints` 와 동일
(순수 함수, 모델 주입, 키워드 인자, 우아한 저하).
"""
from __future__ import annotations

import random
import time
from collections import defaultdict

from ortools.sat.python import cp_model

try:
    from . import csp_hard_constraints as hc
    from . import soft_constraints as sc
    from . import soft_constraints_diversity as scd
except ImportError:  # 스크립트 직접 실행 지원 — csp_solver 와 동일 규약
    import csp_hard_constraints as hc
    import soft_constraints as sc
    import soft_constraints_diversity as scd


# 하루 부분 문제에 쓸 후보 표본 크기(카테고리 비례). 계측상 250종이 최적 구간.
DEFAULT_POOL_SIZE = 250
# 하루 부분 문제 1건의 시간 상한(초). 정상 케이스는 0.05~0.5초에 끝난다.
DEFAULT_PER_DAY_TIME = 2.0
# 힌트 구성에 쓸 수 있는 전체 예산 비율(solver_time_limit 대비). 초과 시 만든 데까지만
# 부분 힌트로 넘긴다 — 힌트 때문에 SLA 를 되레 넘기는 일이 없게 한다.
DEFAULT_BUDGET_RATIO = 0.35
# 하루 단위 유도 가중치. 지평 총량 규칙(잡곡밥·나물 min / 튀김·가공식품 max)을
# 하루 목적으로 근사해, 만들어진 힌트가 전체 목적함수에서도 감점 0에 닿게 한다.
_STEER = (("잡곡밥", 3), ("나물", 3), ("튀김", -3), ("가공식품", -3))
_COMMERCIAL_PENALTY = 5


def _pool_by_category(menus: list) -> dict:
    """카테고리 → 메뉴 인덱스 목록."""
    by_cat: dict = defaultdict(list)
    for idx, menu in enumerate(menus):
        by_cat[getattr(menu, "category", None)].append(idx)
    return by_cat


def _sample_pool(by_cat: dict, total: int, *, seed: int, size: int, exclude: set) -> list:
    """카테고리 비례 표본을 뽑는다(각 카테고리 최소 10종, 배제분 제외).

    비례로만 뽑으면 김치(45종)처럼 작은 카테고리가 0개가 되어 구성이 성립하지 않는다.
    """
    rng = random.Random(seed)
    pool: list = []
    for idx_list in by_cat.values():
        avail = [i for i in idx_list if i not in exclude]
        if not avail:
            continue
        want = max(10, round(len(idx_list) * size / total)) if total else len(avail)
        pool += rng.sample(avail, min(want, len(avail)))
    return pool


def _solve_one_day(menus, pool, *, config, nutrient_by_idx, composition,
                   n_meals, time_limit, seed, food_types, commercial,
                   main_by_idx=None, main_budget=None):
    """하루(n_meals 끼)치 부분 문제를 푼다. 실패하면 None.

    main_budget: {주재료: 남은 허용 횟수}. 주면 하루 안에서 그 예산을 넘지 않게 막는다.
        하루에 부찬만 최대 9접시라 **배제만으로는 부족하다** — 첫날 하루가 지평 cap 을
        통째로 써 버릴 수 있다(cap 3 인데 9회 사용을 실측).
    """
    model = cp_model.CpModel()
    meals = range(n_meals)
    y = {(m, s): model.NewBoolVar(f"y_{m}_{s}") for m in pool for s in meals}
    sub = [menus[m] for m in pool]
    remap = {m: i for i, m in enumerate(pool)}
    x1 = {(remap[m], 0, s): y[m, s] for m in pool for s in meals}
    for s in meals:
        for cat, (lo, hi) in composition.items():
            picked = sum(y[m, s] for m in pool if getattr(menus[m], "category", None) == cat)
            if lo is not None:
                model.Add(picked >= lo)
            if hi is not None:
                model.Add(picked <= hi)
    if main_budget is not None and main_by_idx:
        by_label: dict = defaultdict(list)
        for m in pool:
            label = main_by_idx.get(m)
            if label:
                by_label[label] += [y[m, s] for s in meals]
        for label, vars_ in by_label.items():
            model.Add(sum(vars_) <= max(0, main_budget.get(label, 0)))
    hc.add_hard_constraints(
        model, x1, sub, days=1, n_meals=n_meals, config=config,
        nutrient_by_idx=_remap_nutrients(nutrient_by_idx, remap))
    obj = []
    for food_type, sign in _STEER:
        obj += [sign * y[m, s] for m in pool
                if food_type in food_types.get(m, ()) for s in meals]
    obj += [-_COMMERCIAL_PENALTY * y[m, s] for m in pool if m in commercial for s in meals]
    if obj:
        model.Maximize(sum(obj))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.random_seed = seed
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    return [(m, s) for m in pool for s in meals if solver.Value(y[m, s])]


def _remap_nutrients(nutrient_by_idx: dict | None, remap: dict) -> dict | None:
    """side-channel 영양소 dict 의 키를 부분 문제 인덱스로 옮긴다."""
    if not nutrient_by_idx:
        return None
    return {name: {remap[m]: values.get(m) for m in remap if m in values}
            for name, values in nutrient_by_idx.items()}


def build_rolling_hint(menus: list, *, days: int, n_meals: int, composition: dict,
                       config, nutrient_by_idx: dict | None = None,
                       main_by_idx: dict | None = None, main_cap: int | None = None,
                       pool_size: int = DEFAULT_POOL_SIZE,
                       per_day_time: float = DEFAULT_PER_DAY_TIME,
                       time_budget: float | None = None) -> dict:
    """하루씩 순차 구성한 배치를 {(m, d, s): 1} 로 돌려준다.

    Args:
        menus: MenuItem 리스트(골조와 동일).
        days/n_meals: 지평·끼니 수.
        composition: {카테고리: (lo, hi)} — 골조가 정규화한 끼니 구성.
        config: hc.HardConstraintConfig (전체 모델과 **같은 것**을 넘길 것).
        nutrient_by_idx: {영양소명: {메뉴인덱스: 양}} (H-2d·H-2e 용).
        main_by_idx: {메뉴인덱스: 대표 주재료명} — 주재료 원장용(B6 Phase 1).
        main_cap: 지평 전체에서 같은 주재료를 허용하는 횟수. 둘 다 주면 원장을 켠다.
        pool_size: 하루 부분 문제 후보 표본 크기.
        per_day_time: 하루 부분 문제 1건의 시간 상한(초).
        time_budget: 힌트 구성 전체 시간 상한(초). 넘으면 만든 데까지 부분 힌트 반환.

    Returns:
        {(메뉴인덱스, 일, 끼니): 1}. 한 건도 못 만들면 빈 dict(→ 호출부는 힌트 생략).
        **부분 힌트도 유효하다** — CP-SAT 힌트는 해를 강제하지 않는다.

    주재료 원장(2026-08-14): 주재료 cap 은 **지평 전체** 규칙이라 하루 목적(_STEER)으로는
    표현할 수 없다. 대신 이미 cap 에 닿은 주재료의 메뉴를 다음 날 후보에서 빼는 방식으로
    순차 근사한다 — 중복 창(recent) 배제와 같은 구조다. 이걸 안 하면 힌트가 감점 0에
    못 닿아 솔버가 힌트 지점에서 다시 개선 탐색을 해야 한다(31일 15.4s → 24.7s 로 확인).
    """
    if not menus or days <= 0 or n_meals <= 0:
        return {}
    by_cat = _pool_by_category(menus)
    food_types = sc.classify_food_types(menus)
    commercial = scd.classify_commercial(menus)
    window = max(1, int(getattr(config, "menu_repeat_window_days", 0) or 1))
    ledger_on = bool(main_by_idx) and main_cap is not None and main_cap > 0
    used_main: dict = defaultdict(int)
    started = time.monotonic()
    history: list[set] = []
    chosen: dict = {}
    for day in range(days):
        if time_budget is not None and time.monotonic() - started > time_budget:
            break
        recent: set = set().union(*history[-(window - 1):]) if window > 1 and history else set()
        budget = None
        if ledger_on:
            # 남은 예산을 하루 부분 문제에 그대로 넘긴다. cap 에 닿은 주재료는 예산 0이
            # 되어 자연히 배제된다(별도 exclude 불필요).
            budget = {label: main_cap - used_main.get(label, 0)
                      for label in set(main_by_idx.values())}
        kwargs = dict(config=config, nutrient_by_idx=nutrient_by_idx,
                      composition=composition, n_meals=n_meals,
                      time_limit=per_day_time, seed=day,
                      food_types=food_types, commercial=commercial,
                      main_by_idx=main_by_idx, main_budget=budget)
        picks = _solve_one_day(
            menus, _sample_pool(by_cat, len(menus), seed=day, size=pool_size,
                                exclude=recent), **kwargs)
        if picks is None:  # 표본이 좁아 실패 → 그 하루만 전 후보로 재시도
            picks = _solve_one_day(
                menus, _sample_pool(by_cat, len(menus), seed=day, size=len(menus),
                                    exclude=recent), **kwargs)
        if picks is None and budget is not None:
            # 예산이 하루를 못 풀게 막는 경우 — 힌트는 어디까지나 출발점이므로
            # 원장을 포기하고라도 그날을 만든다(감점 있는 힌트 > 힌트 없음).
            kwargs["main_budget"] = None
            picks = _solve_one_day(
                menus, _sample_pool(by_cat, len(menus), seed=day, size=len(menus),
                                    exclude=recent), **kwargs)
        if picks is None:  # 이 하루가 안 풀리면 이후도 못 잇는다 → 여기까지만
            break
        for m, s in picks:
            chosen[m, day, s] = 1
            if ledger_on and main_by_idx.get(m):
                used_main[main_by_idx[m]] += 1
        history.append({m for m, _ in picks})
    return chosen


def apply_hint(model, x: dict, hint: dict, *, days: int, n_meals: int) -> int:
    """힌트를 모델에 주입한다. 구성된 날짜의 변수만 완전 지정(부분 힌트).

    Returns:
        힌트를 준 변수 개수(0이면 미적용).
    """
    if not hint:
        return 0
    hinted_days = {d for _, d, _ in hint}
    count = 0
    for (m, d, s), var in x.items():
        if d in hinted_days:
            model.AddHint(var, hint.get((m, d, s), 0))
            count += 1
    return count
